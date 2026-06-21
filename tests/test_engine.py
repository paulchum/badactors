"""Tests for deterministic engine transitions."""

from __future__ import annotations

import ast
from pathlib import Path

from badactors.engine.rules import vote_tally
from badactors.engine.state import step
from badactors.schemas import (
    AgentTurnOutput,
    EndgameChoice,
    GameState,
    MurderNomination,
    NoAction,
    Phase,
    Player,
    Role,
    Vote,
)


def _player(player_id: str, role: Role) -> Player:
    return Player(
        id=player_id,
        model=f"model-{player_id}",
        role=role,
        memory_budget=100,
        token_budget=200,
    )


def _state(
    *,
    phase: Phase = Phase.BANISHMENT_VOTE,
    players: list[Player] | None = None,
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
        public_history=[],
        conclave_history=[],
        diaries={player.id: [] for player in game_players},
        rng_seed=13,
    )


def _turn(action: object, *, diary: str = "private") -> AgentTurnOutput:
    return AgentTurnOutput(
        public_speech="public",
        diary=diary,
        action=action,
    )


def _alive_ids(state: GameState) -> set[str]:
    return {player.id for player in state.players if player.alive}


def test_vote_tally() -> None:
    outputs = {
        "p1": _turn(Vote(target_id="p2")),
        "p2": _turn(Vote(target_id="p1")),
        "p3": _turn(Vote(target_id="p2")),
    }

    assert vote_tally(outputs, ["p1", "p2", "p3"], ["p1", "p2", "p3"]) == {
        "p1": 1,
        "p2": 2,
    }


def test_tie_break_deterministic() -> None:
    state = _state()
    outputs = {
        "p1": _turn(Vote(target_id="p2")),
        "p2": _turn(Vote(target_id="p1")),
    }

    first_state, first_events = step(state, outputs)
    second_state, second_events = step(state, outputs)

    assert first_state == second_state
    assert first_events == second_events
    assert [event.type for event in first_events] == [
        "DiaryEntry",
        "DiaryEntry",
        "VoteCast",
        "VoteCast",
        "VoteTallied",
        "PlayerBanished",
    ]


def test_banishment_reveals_role() -> None:
    state = _state()
    next_state, emitted = step(
        state,
        {
            "p1": _turn(Vote(target_id="p2")),
            "p2": _turn(Vote(target_id="p2")),
            "p3": _turn(Vote(target_id="p2")),
        },
    )

    assert next_state.phase is Phase.BANISHMENT_REVEAL
    assert "p2" not in _alive_ids(next_state)
    reveal = next(event for event in emitted if event.type == "PlayerBanished")
    assert reveal.payload == {"player_id": "p2", "role": "TRAITOR"}


def test_murder_removes_player() -> None:
    state = _state(phase=Phase.CONCLAVE)
    next_state, emitted = step(
        state,
        {"p2": _turn(MurderNomination(target_id="p3"))},
    )

    assert next_state.phase is Phase.BREAKFAST_REVEAL
    assert "p3" not in _alive_ids(next_state)
    assert [event.type for event in emitted] == [
        "MurderNomination",
        "DiaryEntry",
        "PlayerMurdered",
    ]
    assert emitted[-1].payload == {"player_id": "p3", "role": "FAITHFUL"}


def test_faithful_win() -> None:
    banished_state, _ = step(
        _state(),
        {"p1": _turn(Vote(target_id="p2")), "p3": _turn(Vote(target_id="p2"))},
    )

    final_state, emitted = step(banished_state, {})

    assert final_state.phase is Phase.GAME_OVER
    assert emitted[-1].type == "GameOver"
    assert emitted[-1].payload["winning_role"] == "FAITHFUL"


def test_traitor_win_endgame_unanimous() -> None:
    state = _state(
        phase=Phase.ENDGAME_VOTE,
        players=[
            _player("p1", Role.FAITHFUL),
            _player("p2", Role.TRAITOR),
            _player("p3", Role.FAITHFUL),
        ],
    )
    outputs = {
        "p1": _turn(EndgameChoice(choice="end")),
        "p2": _turn(EndgameChoice(choice="end")),
        "p3": _turn(EndgameChoice(choice="end")),
    }

    next_state, emitted = step(state, outputs)

    assert next_state.phase is Phase.GAME_OVER
    assert emitted[-1].payload == {
        "winning_role": "TRAITOR",
        "reason": "endgame_unanimous",
    }


def test_endgame_nonunanimous_proceeds_to_banish() -> None:
    state = _state(
        phase=Phase.ENDGAME_VOTE,
        players=[
            _player("p1", Role.FAITHFUL),
            _player("p2", Role.TRAITOR),
            _player("p3", Role.FAITHFUL),
        ],
    )
    outputs = {
        "p1": _turn(EndgameChoice(choice="end")),
        "p2": _turn(EndgameChoice(choice="banish")),
        "p3": _turn(EndgameChoice(choice="end")),
    }

    next_state, emitted = step(state, outputs)

    assert next_state.phase is Phase.BANISHMENT_VOTE
    assert [event.type for event in emitted].count("EndgameChoice") == 3


def test_replay_determinism() -> None:
    state = _state(phase=Phase.ROUND_TABLE)
    outputs = {
        "p1": _turn(NoAction(), diary="p1 thought"),
        "p2": _turn(NoAction(), diary="p2 thought"),
    }

    assert step(state, outputs) == step(state, outputs)


def test_full_scripted_game_reaches_terminal() -> None:
    state = _state(phase=Phase.ROUND_TABLE)
    state, _ = step(
        state,
        {
            "p1": _turn(NoAction()),
            "p2": _turn(NoAction()),
            "p3": _turn(NoAction()),
            "p4": _turn(NoAction()),
        },
    )
    state, _ = step(
        state,
        {
            "p1": _turn(Vote(target_id="p4")),
            "p2": _turn(Vote(target_id="p4")),
            "p3": _turn(Vote(target_id="p4")),
            "p4": _turn(Vote(target_id="p2")),
        },
    )
    state, _ = step(state, {})
    assert state.phase is Phase.ENDGAME_VOTE

    state, _ = step(
        state,
        {
            "p1": _turn(EndgameChoice(choice="banish")),
            "p2": _turn(EndgameChoice(choice="end")),
            "p3": _turn(EndgameChoice(choice="end")),
        },
    )
    state, _ = step(
        state,
        {
            "p1": _turn(Vote(target_id="p2")),
            "p2": _turn(Vote(target_id="p1")),
            "p3": _turn(Vote(target_id="p2")),
        },
    )
    state, _ = step(state, {})

    assert state.phase is Phase.GAME_OVER
    assert state.public_history[-1].payload["winning_role"] == "FAITHFUL"


def test_engine_has_no_model_imports() -> None:
    engine_dir = Path(__file__).parents[1] / "src" / "badactors" / "engine"
    forbidden = {"badactors.providers", "badactors.agents"}
    for path in engine_dir.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not any(node.module.startswith(module) for module in forbidden)
