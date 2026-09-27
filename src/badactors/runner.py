"""Command runner for local simulation execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import BaseModel

from badactors.agents.agent import ModelAgent
from badactors.engine.rules import living_player_ids, living_traitor_ids
from badactors.engine.state import step
from badactors.host.host import Host
from badactors.persistence import SQLiteEventStore
from badactors.providers.base import Budget, Message, ModelProvider
from badactors.providers.mock import MockProvider
from badactors.schemas import (
    Action,
    AgentTurnOutput,
    EndgameChoice,
    Event,
    GameState,
    MurderNomination,
    NoAction,
    Phase,
    Player,
    Role,
    ScopedContext,
    Visibility,
    Vote,
)
from badactors.scoping import assert_no_leak, build_context


@dataclass(frozen=True)
class CastMember:
    id: str
    model: str
    memory_budget: int
    token_budget: int


@dataclass(frozen=True)
class Cast:
    game_id: str
    rng_seed: int
    traitor_count: int
    members: tuple[CastMember, ...]


type ContextObserver = Callable[[ScopedContext, GameState], None]
type StateTransform = Callable[[GameState], GameState]
type TurnObserver = Callable[[GameState, Mapping[str, AgentTurnOutput]], None]


@dataclass(frozen=True)
class GameRunResult:
    transcript: str
    final_state: GameState
    events: tuple[Event, ...]


async def run_game(
    cast: Cast | Mapping[str, object],
    provider: ModelProvider,
    *,
    event_store: SQLiteEventStore | None = None,
    context_observer: ContextObserver | None = None,
    max_steps: int = 100,
    resume: bool = False,
) -> str:
    result = await run_game_record(
        cast,
        provider,
        event_store=event_store,
        context_observer=context_observer,
        max_steps=max_steps,
        resume=resume,
    )
    return result.transcript


async def run_game_record(
    cast: Cast | Mapping[str, object],
    provider: ModelProvider,
    *,
    event_store: SQLiteEventStore | None = None,
    context_observer: ContextObserver | None = None,
    state_transform: StateTransform | None = None,
    budget_overrides: Mapping[str, Budget] | None = None,
    turn_observer: TurnObserver | None = None,
    max_steps: int = 100,
    resume: bool = False,
) -> GameRunResult:
    cast_spec = cast if isinstance(cast, Cast) else cast_from_mapping(cast)
    store = event_store if event_store is not None else SQLiteEventStore()
    try:
        async with store.lease(f"game:{cast_spec.game_id}") as owner:
            return await _run_persisted_game(
                cast_spec,
                provider,
                store=store,
                owner=owner,
                resume=resume,
                context_observer=context_observer,
                state_transform=state_transform,
                budget_overrides=budget_overrides,
                turn_observer=turn_observer,
                max_steps=max_steps,
            )
    finally:
        if event_store is None:
            store.close()


async def _run_persisted_game(
    cast: Cast,
    provider: ModelProvider,
    *,
    store: SQLiteEventStore,
    owner: str,
    resume: bool,
    context_observer: ContextObserver | None,
    state_transform: StateTransform | None,
    budget_overrides: Mapping[str, Budget] | None,
    turn_observer: TurnObserver | None,
    max_steps: int,
) -> GameRunResult:
    cast_payload: dict[str, object] = json.loads(json.dumps(asdict(cast)))
    saved = store.document("game", cast.game_id)
    if saved is not None:
        if not resume:
            raise ValueError("Game already exists; pass resume=True to continue.")
        revision, body = saved
        if body["cast"] != cast_payload:
            raise ValueError("Resume cast must match the original cast and seed.")
        state = GameState.model_validate(body["state"])
        transcript = (
            list(body["transcript"]) if isinstance(body["transcript"], list) else []
        )
    else:
        if store.load(cast.game_id):
            raise ValueError("Legacy event-only game has no resumable state.")
        state = initialize_state(cast)
        if state_transform is not None:
            state = state_transform(state)
        transcript = [f"Game {state.game_id} started."]
        revision = store.checkpoint_game(
            state, transcript, [], expected_revision=0, cast=cast_payload, owner=owner
        )
    host = Host()
    agents = _agents_for_players(state.players, provider, budget_overrides)
    for _step_index in range(max_steps):
        if state.phase is Phase.GAME_OVER:
            return GameRunResult(
                "\n".join(transcript), state, tuple(store.load(state.game_id))
            )
        _observe_contexts(state, context_observer)
        outputs = await _persisted_outputs(state, agents, store, revision, owner)
        if outputs and turn_observer is not None:
            turn_observer(state, outputs)
        next_transcript = [*transcript, *_turn_lines(state.phase, outputs)]
        next_state, emitted = step(state, outputs)
        narration_events = [
            event for event in emitted if event.visibility is not Visibility.AUDIENCE
        ]
        if narration_events:
            next_transcript.append(f"HOST: {await host.narrate(narration_events)}")
        revision = store.checkpoint_game(
            next_state,
            next_transcript,
            emitted,
            expected_revision=revision,
            cast=cast_payload,
            owner=owner,
        )
        state, transcript = next_state, next_transcript
        if state.phase is Phase.GAME_OVER:
            return GameRunResult(
                "\n".join(transcript), state, tuple(store.load(state.game_id))
            )
    raise RuntimeError(
        "Game did not reach GAME_OVER before max_steps; checkpoint saved."
    )


async def _persisted_outputs(
    state: GameState,
    agents: Mapping[str, ModelAgent],
    store: SQLiteEventStore,
    revision: int,
    owner: str,
) -> dict[str, AgentTurnOutput]:
    async def decide(player_id: str) -> AgentTurnOutput:
        key = f"{state.game_id}:{revision}:{player_id}"
        saved = store.document("game_turn", key)
        if saved:
            return AgentTurnOutput.model_validate(saved[1]["output"])
        output = await agents[player_id].act(build_context(state, player_id))
        with store.transaction() as connection:
            store.require_lease(connection, f"game:{state.game_id}", owner)
            store.put_document(
                connection,
                "game_turn",
                key,
                {"output": output.model_dump(mode="json")},
                0,
            )
        return output

    actor_ids = _actor_ids(state)
    # TaskGroup cancels and joins siblings before releasing the writer lease.
    async with asyncio.TaskGroup() as group:
        tasks = {
            player_id: group.create_task(decide(player_id)) for player_id in actor_ids
        }
    return {player_id: task.result() for player_id, task in tasks.items()}


def initialize_state(cast: Cast) -> GameState:
    rng = random.Random(cast.rng_seed)
    member_ids = [member.id for member in cast.members]
    if len(set(member_ids)) != len(member_ids):
        raise ValueError("Player IDs must be unique.")
    if any(
        member.memory_budget < 0 or member.token_budget < 1 for member in cast.members
    ):
        raise ValueError(
            "Memory must be nonnegative and token budgets must be positive."
        )
    if cast.traitor_count < 1 or cast.traitor_count >= len(member_ids):
        raise ValueError("traitor_count must be between 1 and player count - 1.")

    traitor_ids = set(rng.sample(member_ids, cast.traitor_count))
    players = [
        Player(
            id=member.id,
            model=member.model,
            role=Role.TRAITOR if member.id in traitor_ids else Role.FAITHFUL,
            memory_budget=member.memory_budget,
            token_budget=member.token_budget,
        )
        for member in cast.members
    ]
    return GameState(
        game_id=cast.game_id,
        players=players,
        phase=Phase.LOBBY,
        round=1,
        traitor_ids=traitor_ids,
        public_history=[],
        conclave_history=[],
        diaries={player.id: [] for player in players},
        rng_seed=cast.rng_seed,
    )


def load_cast(path: str | Path) -> Cast:
    return cast_from_mapping(_load_json_yaml_subset(path))


def cast_from_mapping(raw: Mapping[str, object]) -> Cast:
    agents = raw.get("agents")
    if not isinstance(agents, Mapping):
        raise ValueError("Cast file must contain an agents mapping.")

    default_memory = _int_value(raw, "memory_budget")
    default_tokens = _int_value(raw, "token_budget")
    members: list[CastMember] = []
    for index, (model, value) in enumerate(agents.items(), start=1):
        if not isinstance(model, str):
            raise ValueError("Agent model keys must be strings.")
        if not isinstance(value, Mapping):
            raise ValueError("Each agent entry must be a mapping.")
        player_id = value.get("id")
        members.append(
            CastMember(
                id=player_id if isinstance(player_id, str) else f"p{index}",
                model=model,
                memory_budget=_int_value(value, "memory_budget", default_memory),
                token_budget=_int_value(value, "token_budget", default_tokens),
            )
        )

    game_id = raw.get("game_id")
    return Cast(
        game_id=game_id if isinstance(game_id, str) else "game-1",
        rng_seed=_int_value(raw, "rng_seed"),
        traitor_count=_int_value(raw, "traitor_count"),
        members=tuple(members),
    )


def default_mock_provider() -> MockProvider:
    return MockProvider(responder=_default_mock_response)


def main() -> None:
    parser = argparse.ArgumentParser(prog="run-game")
    parser.add_argument("--cast", required=True, help="Path to a cast YAML file.")
    parser.add_argument("--db", default="data/badactors.sqlite3")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    store = SQLiteEventStore(args.db)
    try:
        print(
            asyncio.run(
                run_game(
                    load_cast(args.cast),
                    default_mock_provider(),
                    event_store=store,
                    resume=args.resume,
                )
            )
        )
    finally:
        store.close()


def _agents_for_players(
    players: Sequence[Player],
    provider: ModelProvider,
    budget_overrides: Mapping[str, Budget] | None = None,
) -> dict[str, ModelAgent]:
    return {
        player.id: ModelAgent(
            model=player.model,
            provider=provider,
            budget=_budget_for_player(player, budget_overrides),
        )
        for player in players
    }


def _budget_for_player(
    player: Player,
    budget_overrides: Mapping[str, Budget] | None,
) -> Budget:
    if budget_overrides is not None:
        budget = budget_overrides.get(player.id)
        if budget is not None:
            return budget
    return Budget(max_tokens=player.token_budget, thinking=False)


def _observe_contexts(
    state: GameState,
    context_observer: ContextObserver | None,
) -> None:
    for player in state.players:
        ctx = build_context(state, player.id)
        assert_no_leak(ctx, state)
        if context_observer is not None:
            context_observer(ctx, state)


async def _collect_outputs(
    state: GameState,
    agents: Mapping[str, ModelAgent],
) -> dict[str, AgentTurnOutput]:
    actor_ids = _actor_ids(state)
    turns = await asyncio.gather(
        *(
            agents[player_id].act(build_context(state, player_id))
            for player_id in actor_ids
        )
    )
    return dict(zip(actor_ids, turns, strict=True))


def _actor_ids(state: GameState) -> list[str]:
    if state.phase in {
        Phase.ROUND_TABLE,
        Phase.BANISHMENT_VOTE,
        Phase.ENDGAME_VOTE,
    }:
        return living_player_ids(state)
    if state.phase is Phase.CONCLAVE:
        return living_traitor_ids(state)
    return []


def _turn_lines(
    phase: Phase,
    outputs: Mapping[str, AgentTurnOutput],
) -> list[str]:
    lines = [f"TURN {phase.value}"]
    for player_id, output in sorted(outputs.items()):
        lines.append(
            f"{player_id} PUBLIC: {output.public_speech} | "
            f"DIARY: {output.diary} | ACTION: {output.action.kind}"
        )
    return lines


def _default_mock_response(
    messages: Sequence[Message],
    schema: type[BaseModel],
    model: str,
    budget: Budget,
    phase: str | None,
) -> AgentTurnOutput:
    del schema, model, budget, phase
    prompt = "\n".join(message.content for message in messages)
    you_id = _you_id_from_prompt(prompt)
    current_phase = Phase(_field_value(prompt, "Current phase").rstrip("."))
    alive = _csv_field(prompt, "Players alive")
    fellow_traitors = _csv_field(prompt, "Fellow Traitors")

    action: Action
    target: str | None = None
    if current_phase is Phase.BANISHMENT_VOTE:
        target = _first_other(alive, {you_id})
        action = Vote(target_id=target) if target is not None else NoAction()
    elif current_phase is Phase.CONCLAVE:
        excluded = {you_id, *fellow_traitors}
        target = _first_other(alive, excluded)
        action = (
            MurderNomination(target_id=target) if target is not None else NoAction()
        )
    elif current_phase is Phase.ENDGAME_VOTE:
        action = EndgameChoice(choice="end")
    else:
        action = NoAction()

    suspicion_target = target or _first_other(alive, {you_id})
    trust_target = _first_other(alive, {you_id, suspicion_target or ""})
    return AgentTurnOutput(
        public_speech=f"{you_id} contributes during {current_phase.value}.",
        diary=f"{you_id} privately tracks {current_phase.value}.",
        action=action,
        suspicions=[{"player_id": suspicion_target, "level": 6}]
        if suspicion_target is not None
        else [],
        trust=[{"player_id": trust_target, "level": 5}]
        if trust_target is not None
        else [],
    )


def _first_other(values: Sequence[str], excluded: set[str]) -> str | None:
    for value in values:
        if value not in excluded:
            return value
    return None


def _field_value(prompt: str, label: str) -> str:
    prefix = f"{label}: "
    for line in prompt.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    raise ValueError(f"Prompt did not include {label!r}.")


def _you_id_from_prompt(prompt: str) -> str:
    prefix = "You are player "
    for line in prompt.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).rstrip(".")
    raise ValueError("Prompt did not include player id.")


def _csv_field(prompt: str, label: str) -> list[str]:
    try:
        value = _field_value(prompt, label).rstrip(".")
    except ValueError:
        return []
    if value == "none":
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_json_yaml_subset(path: str | Path) -> Mapping[str, object]:
    lines = []
    for line in Path(path).read_text().splitlines():
        if line.strip().startswith("#"):
            continue
        lines.append(line)
    loaded = json.loads("\n".join(lines))
    if not isinstance(loaded, Mapping):
        raise ValueError("Cast file must contain a JSON object.")
    return loaded


def _int_value(
    mapping: Mapping[str, object],
    key: str,
    default: int | None = None,
) -> int:
    value = mapping.get(key, default)
    if isinstance(value, int):
        return value
    raise ValueError(f"{key} must be an integer.")
