"""Tests for the schema data contract."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from badactors.schemas import (
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


def _event() -> Event:
    return Event(
        id=1,
        game_id="game-1",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        phase=Phase.ROUND_TABLE,
        type="speech",
        payload={"player_id": "p1", "text": "hello"},
        visibility=Visibility.PUBLIC,
    )


def _player() -> Player:
    return Player(
        id="p1",
        model="mock",
        role=Role.FAITHFUL,
        memory_budget=1000,
        token_budget=2000,
    )


def test_roundtrip_all_models() -> None:
    event = _event()
    player = _player()
    models = [
        player,
        Vote(target_id="p2"),
        MurderNomination(target_id="p3"),
        EndgameChoice(choice="end"),
        NoAction(),
        AgentTurnOutput(
            public_speech="I trust p2.",
            diary="I suspect p3.",
            action=Vote(target_id="p3"),
            suspicions=[{"player_id": "p3", "level": 8}],
            trust=[{"player_id": "p2", "level": 6}],
        ),
        event,
        GameState(
            game_id="game-1",
            players=[player],
            phase=Phase.ROUND_TABLE,
            round=1,
            traitor_ids={"p3"},
            public_history=[event],
            conclave_history=[],
            diaries={"p1": ["I suspect p3."]},
            rng_seed=42,
        ),
        ScopedContext(
            you_id="p1",
            you_role=Role.FAITHFUL,
            fellow_traitors=None,
            players_alive=["p1", "p2", "p3"],
            public_history=[event],
            conclave_history=None,
            your_diary_history=["I suspect p3."],
            current_phase=Phase.ROUND_TABLE,
            rules="Rules text.",
            valid_actions=["vote", "no_action"],
        ),
    ]

    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model


def test_action_union_discriminates() -> None:
    output = AgentTurnOutput(
        public_speech="I choose p3.",
        diary="Conclave thought.",
        action={"kind": "murder_nomination", "target_id": "p3"},
    )

    assert isinstance(output.action, MurderNomination)
    assert output.action.target_id == "p3"

    endgame_output = AgentTurnOutput(
        public_speech="End the game.",
        diary="This should win.",
        action={"kind": "endgame_choice", "choice": "end"},
    )

    assert isinstance(endgame_output.action, EndgameChoice)
    assert endgame_output.action.choice == "end"

    with pytest.raises(ValidationError):
        AgentTurnOutput(
            public_speech="Invalid action.",
            diary="Invalid action.",
            action={"kind": "unknown"},
        )


def test_enum_literal_values() -> None:
    assert [role.value for role in Role] == ["FAITHFUL", "TRAITOR"]
    assert [phase.value for phase in Phase] == [
        "LOBBY",
        "ROUND_TABLE",
        "BANISHMENT_VOTE",
        "BANISHMENT_REVEAL",
        "CONCLAVE",
        "BREAKFAST_REVEAL",
        "ENDGAME_VOTE",
        "GAME_OVER",
    ]
    assert [visibility.value for visibility in Visibility] == [
        "PUBLIC",
        "TRAITORS",
        "AUDIENCE",
        "HOST",
    ]


def test_faithful_scoped_context_optionals_none() -> None:
    context = ScopedContext(
        you_id="p1",
        you_role=Role.FAITHFUL,
        fellow_traitors=None,
        players_alive=["p1", "p2"],
        public_history=[],
        conclave_history=None,
        your_diary_history=[],
        current_phase=Phase.LOBBY,
        rules="Rules text.",
        valid_actions=["no_action"],
    )

    assert context.fellow_traitors is None
    assert context.conclave_history is None
