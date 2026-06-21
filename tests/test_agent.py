"""Tests for model-backed agent turn generation."""

from __future__ import annotations

import pytest

from badactors.agents.agent import ModelAgent
from badactors.agents.prompts import build_messages
from badactors.providers.base import Budget
from badactors.providers.mock import MockProvider
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
    Visibility,
    Vote,
)
from badactors.scoping import build_context


def _player(player_id: str, role: Role) -> Player:
    return Player(
        id=player_id,
        model=f"model-{player_id}",
        role=role,
        memory_budget=100,
        token_budget=200,
    )


def _state(phase: Phase, *, viewer_role: Role = Role.TRAITOR) -> GameState:
    players = [
        _player("p1", viewer_role),
        _player("p2", Role.FAITHFUL),
        _player("p3", Role.TRAITOR),
    ]
    return GameState(
        game_id="game-1",
        players=players,
        phase=phase,
        round=1,
        traitor_ids={player.id for player in players if player.role is Role.TRAITOR},
        public_history=[],
        conclave_history=[
            Event(
                id=1,
                game_id="game-1",
                ts="2026-01-01T00:00:00Z",
                phase=Phase.CONCLAVE,
                type="MurderNomination",
                payload={"traitor_id": "p3", "target_id": "p2"},
                visibility=Visibility.TRAITORS,
            )
        ],
        diaries={player.id: [] for player in players},
        rng_seed=13,
    )


def _output(action: object, *, public_speech: str = "I will act.") -> dict[str, object]:
    return AgentTurnOutput(
        public_speech=public_speech,
        diary="private",
        action=action,
    ).model_dump(mode="json")


def _agent(provider: MockProvider) -> ModelAgent:
    return ModelAgent(
        model="model-p1",
        provider=provider,
        budget=Budget(max_tokens=200, thinking=False),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "action"),
    [
        (Phase.LOBBY, NoAction()),
        (Phase.ROUND_TABLE, NoAction()),
        (Phase.BANISHMENT_VOTE, Vote(target_id="p2")),
        (Phase.BANISHMENT_REVEAL, NoAction()),
        (Phase.CONCLAVE, MurderNomination(target_id="p2")),
        (Phase.BREAKFAST_REVEAL, NoAction()),
        (Phase.ENDGAME_VOTE, EndgameChoice(choice="end")),
        (Phase.GAME_OVER, NoAction()),
    ],
)
async def test_act_returns_valid_output_per_phase(
    phase: Phase,
    action: object,
) -> None:
    provider = MockProvider({"model-p1": [_output(action)]})
    ctx = build_context(_state(phase), "p1")

    output = await _agent(provider).act(ctx)

    assert isinstance(output, AgentTurnOutput)
    assert output.public_speech == "I will act."


@pytest.mark.asyncio
async def test_action_matches_phase() -> None:
    provider = MockProvider(
        {
            "model-p1": [
                _output(MurderNomination(target_id="p2")),
                _output(Vote(target_id="p2")),
            ]
        }
    )
    ctx = build_context(_state(Phase.BANISHMENT_VOTE), "p1")

    output = await _agent(provider).act(ctx)

    assert isinstance(output.action, Vote)
    assert output.action.target_id == "p2"


@pytest.mark.asyncio
async def test_refusal_retries_then_safe_default() -> None:
    provider = MockProvider(
        {
            "model-p1": [
                _output(NoAction(), public_speech="I refuse to play."),
                _output(NoAction(), public_speech="As an AI, I cannot comply."),
            ]
        }
    )
    agent = _agent(provider)
    ctx = build_context(_state(Phase.ROUND_TABLE), "p1")

    output = await agent.act(ctx)

    assert len(agent.refusals) == 2
    assert output.public_speech == ""
    assert output.diary == ""
    assert isinstance(output.action, NoAction)


def test_faithful_prompt_contains_no_secret_roles_or_conclave() -> None:
    ctx = build_context(_state(Phase.ROUND_TABLE, viewer_role=Role.FAITHFUL), "p1")

    rendered = "\n".join(message.content for message in build_messages(ctx))

    assert "Fellow Traitors" not in rendered
    assert "MurderNomination" not in rendered
    assert 'p3", "role": "TRAITOR' not in rendered
