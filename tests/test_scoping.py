"""Tests for scoped context leakage protections."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from badactors.engine.state import step
from badactors.providers.base import Budget, Message
from badactors.providers.mock import MockProvider
from badactors.schemas import (
    AgentTurnOutput,
    EndgameChoice,
    Event,
    GameState,
    NoAction,
    Phase,
    Player,
    Role,
    Visibility,
    Vote,
)
from badactors.scoping import LeakageError, assert_no_leak, build_context


def _player(player_id: str, role: Role, *, alive: bool = True) -> Player:
    return Player(
        id=player_id,
        model=f"model-{player_id}",
        role=role,
        alive=alive,
        memory_budget=100,
        token_budget=200,
    )


def _event(
    event_id: int,
    *,
    phase: Phase,
    type: str,
    payload: dict[str, object],
    visibility: Visibility = Visibility.PUBLIC,
) -> Event:
    return Event(
        id=event_id,
        game_id="game-1",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        phase=phase,
        type=type,
        payload=payload,
        visibility=visibility,
    )


def _state(
    *,
    phase: Phase = Phase.ROUND_TABLE,
    players: list[Player] | None = None,
    public_history: list[Event] | None = None,
    conclave_history: list[Event] | None = None,
    diaries: dict[str, list[str]] | None = None,
) -> GameState:
    game_players = players or [
        _player("p1", Role.FAITHFUL),
        _player("p2", Role.TRAITOR),
        _player("p3", Role.FAITHFUL),
        _player("p4", Role.FAITHFUL),
    ]
    return GameState(
        game_id="game-1",
        players=game_players,
        phase=phase,
        round=1,
        traitor_ids={
            player.id for player in game_players if player.role is Role.TRAITOR
        },
        public_history=public_history or [],
        conclave_history=conclave_history or [],
        diaries=diaries
        or {player.id: [f"{player.id} diary"] for player in game_players},
        rng_seed=13,
    )


def _turn(action: object, *, diary: str = "private") -> AgentTurnOutput:
    return AgentTurnOutput(public_speech="public", diary=diary, action=action)


@st.composite
def _game_states(draw: st.DrawFn) -> GameState:
    player_count = draw(st.integers(min_value=3, max_value=6))
    traitor_indices = draw(
        st.sets(
            st.integers(min_value=0, max_value=player_count - 1),
            min_size=1,
            max_size=max(1, player_count // 2),
        )
    )
    alive_flags = draw(
        st.lists(st.booleans(), min_size=player_count, max_size=player_count)
    )
    phase = draw(st.sampled_from(list(Phase)))
    players = [
        _player(
            f"p{index}",
            Role.TRAITOR if index in traitor_indices else Role.FAITHFUL,
            alive=alive_flags[index],
        )
        for index in range(player_count)
    ]
    public_history = [
        _event(
            index + 1,
            phase=phase,
            type="PlayerBanished",
            payload={"player_id": player.id, "role": player.role.value},
        )
        for index, player in enumerate(players)
        if not player.alive
    ]
    public_history.append(
        _event(
            len(public_history) + 1,
            phase=phase,
            type="PublicSpeech",
            payload={"player_id": players[0].id, "speech": "Public claim only."},
        )
    )
    conclave_history = [
        _event(
            len(public_history) + 1,
            phase=Phase.CONCLAVE,
            type="MurderNomination",
            payload={
                "traitor_id": players[min(traitor_indices)].id,
                "target_id": players[0].id,
            },
            visibility=Visibility.TRAITORS,
        )
    ]
    diaries = {player.id: [f"{player.id} private note"] for player in players}
    return _state(
        phase=phase,
        players=players,
        public_history=public_history,
        conclave_history=conclave_history,
        diaries=diaries,
    )


@settings(max_examples=30)
@given(_game_states())
def test_property_no_leak(state: GameState) -> None:
    for player in state.players:
        assert_no_leak(build_context(state, player.id), state)


@pytest.mark.asyncio
async def test_full_game_no_leak() -> None:
    provider = MockProvider(
        {
            "model-p1": [
                _turn(NoAction(), diary="p1 round"),
                _turn(Vote(target_id="p4"), diary="p1 vote p4"),
                _turn(EndgameChoice(choice="banish"), diary="p1 wants banish"),
                _turn(Vote(target_id="p2"), diary="p1 vote p2"),
            ],
            "model-p2": [
                _turn(NoAction(), diary="p2 round"),
                _turn(Vote(target_id="p4"), diary="p2 hides"),
                _turn(EndgameChoice(choice="end"), diary="p2 wants end"),
                _turn(Vote(target_id="p1"), diary="p2 deflects"),
            ],
            "model-p3": [
                _turn(NoAction(), diary="p3 round"),
                _turn(Vote(target_id="p4"), diary="p3 vote p4"),
                _turn(EndgameChoice(choice="end"), diary="p3 trusts end"),
                _turn(Vote(target_id="p2"), diary="p3 vote p2"),
            ],
            "model-p4": [
                _turn(NoAction(), diary="p4 round"),
                _turn(Vote(target_id="p2"), diary="p4 suspects p2"),
            ],
        }
    )
    state = _state(phase=Phase.ROUND_TABLE)

    while state.phase is not Phase.GAME_OVER:
        outputs: dict[str, AgentTurnOutput] = {}
        for player in state.players:
            ctx = build_context(state, player.id)
            assert_no_leak(ctx, state)
            if player.alive and state.phase in {
                Phase.ROUND_TABLE,
                Phase.BANISHMENT_VOTE,
                Phase.ENDGAME_VOTE,
            }:
                result = await provider.complete(
                    [Message(role="system", content=ctx.current_phase.value)],
                    AgentTurnOutput,
                    player.model,
                    Budget(max_tokens=player.token_budget, thinking=False),
                )
                assert isinstance(result.parsed, AgentTurnOutput)
                outputs[player.id] = result.parsed
        state, _ = step(state, outputs)

    for player in state.players:
        assert_no_leak(build_context(state, player.id), state)


def test_revealed_role_allowed() -> None:
    state = _state(
        players=[
            _player("p1", Role.FAITHFUL),
            _player("p2", Role.TRAITOR, alive=False),
            _player("p3", Role.FAITHFUL),
        ],
        public_history=[
            _event(
                1,
                phase=Phase.BANISHMENT_VOTE,
                type="PlayerBanished",
                payload={"player_id": "p2", "role": "TRAITOR"},
            )
        ],
    )

    assert_no_leak(build_context(state, "p1"), state)


def test_planted_leak_is_caught() -> None:
    state = _state()
    ctx = build_context(state, "p1")
    leaked_ctx = ctx.model_copy(
        update={
            "public_history": [
                *ctx.public_history,
                _event(
                    99,
                    phase=Phase.ROUND_TABLE,
                    type="PlantedLeak",
                    payload={"player_id": "p2", "role": "TRAITOR"},
                ),
            ]
        }
    )

    with pytest.raises(LeakageError):
        assert_no_leak(leaked_ctx, state)
