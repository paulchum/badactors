"""Pure game state transition logic."""

from __future__ import annotations

from collections.abc import Iterable

from badactors.engine import events
from badactors.engine.rules import (
    banish_player,
    check_win_condition,
    choose_from_tally,
    endgame_unanimous_to_end,
    living_player_ids,
    living_traitor_ids,
    murder_player,
    murder_tally,
    player_by_id,
    should_enter_endgame,
    vote_tally,
    winning_role_after_end,
)
from badactors.schemas import (
    AgentTurnOutput,
    EndgameChoice,
    Event,
    GameState,
    MurderNomination,
    Phase,
    Player,
    Visibility,
    Vote,
)


def step(
    state: GameState,
    outputs: dict[str, AgentTurnOutput],
) -> tuple[GameState, list[Event]]:
    allocator = events.EventIdAllocator(
        game_id=state.game_id,
        next_id=_next_event_id(state),
    )
    emitted: list[Event] = []
    players = list(state.players)
    next_phase = state.phase
    next_round = state.round

    if state.phase is Phase.LOBBY:
        next_phase = Phase.ROUND_TABLE
        emitted.append(
            events.phase_advanced(
                allocator,
                phase=state.phase,
                from_phase=state.phase,
                to_phase=next_phase,
                round=state.round,
            )
        )
    elif state.phase is Phase.ROUND_TABLE:
        _record_agent_outputs(allocator, state.phase, outputs, emitted)
        next_phase = Phase.BANISHMENT_VOTE
    elif state.phase is Phase.BANISHMENT_VOTE:
        _record_diaries(allocator, state.phase, outputs, emitted)
        players = _resolve_banishment(state, outputs, allocator, emitted)
        next_phase = Phase.BANISHMENT_REVEAL
    elif state.phase is Phase.BANISHMENT_REVEAL:
        transition_state = _state_with(
            state,
            players=players,
            phase=state.phase,
            round=state.round,
        )
        winner = check_win_condition(transition_state)
        if winner is not None:
            next_phase = Phase.GAME_OVER
            emitted.append(
                events.game_over(
                    allocator,
                    phase=state.phase,
                    winning_role=winner,
                    reason="no_living_traitors",
                )
            )
        elif should_enter_endgame(transition_state):
            next_phase = Phase.ENDGAME_VOTE
        else:
            next_phase = Phase.CONCLAVE
    elif state.phase is Phase.CONCLAVE:
        _record_conclave_outputs(allocator, state, outputs, emitted)
        players = _resolve_murder(state, outputs, allocator, emitted)
        next_phase = Phase.BREAKFAST_REVEAL
    elif state.phase is Phase.BREAKFAST_REVEAL:
        transition_state = _state_with(
            state,
            players=players,
            phase=state.phase,
            round=state.round,
        )
        winner = check_win_condition(transition_state)
        next_round = state.round + 1
        if winner is not None:
            next_phase = Phase.GAME_OVER
            emitted.append(
                events.game_over(
                    allocator,
                    phase=state.phase,
                    winning_role=winner,
                    reason="no_living_traitors",
                )
            )
        elif should_enter_endgame(transition_state):
            next_phase = Phase.ENDGAME_VOTE
        else:
            next_phase = Phase.ROUND_TABLE
    elif state.phase is Phase.ENDGAME_VOTE:
        _record_endgame_choices(allocator, state, outputs, emitted)
        if endgame_unanimous_to_end(outputs, living_player_ids(state)):
            next_phase = Phase.GAME_OVER
            emitted.append(
                events.game_over(
                    allocator,
                    phase=state.phase,
                    winning_role=winning_role_after_end(state),
                    reason="endgame_unanimous",
                )
            )
        else:
            next_phase = Phase.BANISHMENT_VOTE
    elif state.phase is Phase.GAME_OVER:
        next_phase = Phase.GAME_OVER

    return _state_with_events(
        state,
        players=players,
        phase=next_phase,
        round=next_round,
        emitted=emitted,
    ), emitted


def _resolve_banishment(
    state: GameState,
    outputs: dict[str, AgentTurnOutput],
    allocator: events.EventIdAllocator,
    emitted: list[Event],
) -> list[Player]:
    living_ids = living_player_ids(state)
    tally = vote_tally(outputs, living_ids, living_ids)
    for voter_id, output in sorted(outputs.items()):
        if voter_id not in living_ids or not isinstance(output.action, Vote):
            continue
        if output.action.target_id in living_ids:
            emitted.append(
                events.vote_cast(
                    allocator,
                    phase=state.phase,
                    voter_id=voter_id,
                    target_id=output.action.target_id,
                )
            )
    target_id = choose_from_tally(tally, state.rng_seed + state.round)
    emitted.append(
        events.vote_tallied(
            allocator,
            phase=state.phase,
            tally=tally,
            selected_id=target_id,
        )
    )
    if target_id is None:
        return list(state.players)

    target = player_by_id(state.players, target_id)
    emitted.append(
        events.player_banished(
            allocator,
            phase=state.phase,
            player_id=target.id,
            role=target.role,
        )
    )
    return banish_player(state.players, target_id)


def _resolve_murder(
    state: GameState,
    outputs: dict[str, AgentTurnOutput],
    allocator: events.EventIdAllocator,
    emitted: list[Event],
) -> list[Player]:
    traitor_ids = living_traitor_ids(state)
    valid_targets = [
        player_id
        for player_id in living_player_ids(state)
        if player_id not in traitor_ids
    ]
    tally = murder_tally(outputs, traitor_ids, valid_targets)
    target_id = choose_from_tally(tally, state.rng_seed + state.round)
    if target_id is None:
        emitted.append(events.no_murder(allocator, phase=state.phase))
        return list(state.players)

    target = player_by_id(state.players, target_id)
    emitted.append(
        events.player_murdered(
            allocator,
            phase=state.phase,
            player_id=target.id,
            role=target.role,
        )
    )
    return murder_player(state.players, target_id)


def _record_agent_outputs(
    allocator: events.EventIdAllocator,
    phase: Phase,
    outputs: dict[str, AgentTurnOutput],
    emitted: list[Event],
) -> None:
    for player_id, output in sorted(outputs.items()):
        emitted.append(
            events.public_speech(
                allocator,
                phase=phase,
                player_id=player_id,
                speech=output.public_speech,
            )
        )
        emitted.append(
            events.diary_entry(
                allocator,
                phase=phase,
                player_id=player_id,
                diary=output.diary,
            )
        )


def _record_diaries(
    allocator: events.EventIdAllocator,
    phase: Phase,
    outputs: dict[str, AgentTurnOutput],
    emitted: list[Event],
) -> None:
    for player_id, output in sorted(outputs.items()):
        emitted.append(
            events.diary_entry(
                allocator,
                phase=phase,
                player_id=player_id,
                diary=output.diary,
            )
        )


def _record_conclave_outputs(
    allocator: events.EventIdAllocator,
    state: GameState,
    outputs: dict[str, AgentTurnOutput],
    emitted: list[Event],
) -> None:
    traitor_ids = set(living_traitor_ids(state))
    valid_targets = set(living_player_ids(state)) - traitor_ids
    for player_id, output in sorted(outputs.items()):
        if player_id not in traitor_ids:
            continue
        action = output.action
        if isinstance(action, MurderNomination) and action.target_id in valid_targets:
            emitted.append(
                events.murder_nomination(
                    allocator,
                    phase=state.phase,
                    traitor_id=player_id,
                    target_id=action.target_id,
                )
            )
        emitted.append(
            events.diary_entry(
                allocator,
                phase=state.phase,
                player_id=player_id,
                diary=output.diary,
            )
        )


def _record_endgame_choices(
    allocator: events.EventIdAllocator,
    state: GameState,
    outputs: dict[str, AgentTurnOutput],
    emitted: list[Event],
) -> None:
    living_ids = set(living_player_ids(state))
    for player_id, output in sorted(outputs.items()):
        if player_id not in living_ids:
            continue
        action = output.action
        if isinstance(action, EndgameChoice):
            emitted.append(
                events.endgame_choice(
                    allocator,
                    phase=state.phase,
                    player_id=player_id,
                    choice=action.choice,
                )
            )
        emitted.append(
            events.diary_entry(
                allocator,
                phase=state.phase,
                player_id=player_id,
                diary=output.diary,
            )
        )


def _state_with_events(
    state: GameState,
    *,
    players: list[Player],
    phase: Phase,
    round: int,
    emitted: Iterable[Event],
) -> GameState:
    public_history = list(state.public_history)
    conclave_history = list(state.conclave_history)
    diaries = {player_id: list(entries) for player_id, entries in state.diaries.items()}

    for event in emitted:
        if event.visibility is Visibility.PUBLIC:
            public_history.append(event)
        elif event.visibility is Visibility.TRAITORS:
            conclave_history.append(event)
        elif event.visibility is Visibility.AUDIENCE and event.type == "DiaryEntry":
            player_id = event.payload["player_id"]
            diary = event.payload["diary"]
            if isinstance(player_id, str) and isinstance(diary, str):
                diaries.setdefault(player_id, []).append(diary)

    return _state_with(
        state,
        players=players,
        phase=phase,
        round=round,
        public_history=public_history,
        conclave_history=conclave_history,
        diaries=diaries,
    )


def _state_with(
    state: GameState,
    *,
    players: list[Player],
    phase: Phase,
    round: int,
    public_history: list[Event] | None = None,
    conclave_history: list[Event] | None = None,
    diaries: dict[str, list[str]] | None = None,
) -> GameState:
    return GameState(
        game_id=state.game_id,
        players=players,
        phase=phase,
        round=round,
        traitor_ids=set(state.traitor_ids),
        public_history=list(
            state.public_history if public_history is None else public_history
        ),
        conclave_history=list(
            state.conclave_history if conclave_history is None else conclave_history
        ),
        diaries={
            player_id: list(entries)
            for player_id, entries in (
                state.diaries if diaries is None else diaries
            ).items()
        },
        rng_seed=state.rng_seed,
    )


def _next_event_id(state: GameState) -> int:
    stored_ids = [event.id for event in state.public_history + state.conclave_history]
    stored_count = (
        len(state.public_history)
        + len(state.conclave_history)
        + sum(len(entries) for entries in state.diaries.values())
    )
    highest_known_id = max(stored_ids, default=0)
    return max(highest_known_id, stored_count) + 1
