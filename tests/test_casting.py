"""Tests for auditions and host casting."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import BaseModel

from badactors.host.host import Host
from badactors.platform.audition import (
    AuditionResult,
    AuditionScore,
    audition_submission,
)
from badactors.platform.submissions import Submission
from badactors.providers.base import Budget, Message
from badactors.providers.mock import MockProvider
from badactors.schemas import GameState, Phase, Role, Visibility
from badactors.scoping import assert_no_leak, build_context


def _submission(name: str) -> Submission:
    return Submission(
        creator=f"{name} creator",
        agent_name=name,
        system_prompt=f"{name} plays carefully and keeps secrets.",
        base_model="mock-fixed-tier",
    )


def _audition(name: str, coherence: int, play_quality: int) -> AuditionResult:
    return AuditionResult(
        submission=_submission(name),
        coherence=coherence,
        play_quality=play_quality,
        calls_used=1,
        tokens_used=128,
        notes="solid",
    )


def test_cast_returns_valid_cast_and_secret_traitors() -> None:
    casting = Host().cast(
        [
            _audition("Aria", 9, 8),
            _audition("Bex", 7, 8),
            _audition("Cy", 6, 6),
            _audition("Dee", 8, 7),
        ],
        game_id="season-1",
    )

    assert casting.game_id == "season-1"
    assert len(casting.players) == 4
    assert casting.traitor_ids
    assert all(player.id for player in casting.players)
    assert casting.events[0].type == "RolesAssigned"
    assert casting.events[0].visibility is Visibility.AUDIENCE
    assert casting.events[1].type == "CastingAnnouncement"
    assert casting.events[1].visibility is Visibility.PUBLIC
    assert "roles" not in casting.events[1].payload


def test_casting_keeps_leakage_invariant() -> None:
    casting = Host().cast(
        [
            _audition("Aria", 9, 8),
            _audition("Bex", 7, 8),
            _audition("Cy", 6, 6),
            _audition("Dee", 8, 7),
            _audition("Eli", 7, 7),
            _audition("Fin", 5, 8),
        ],
        game_id="season-2",
    )
    players = []
    for player in casting.players:
        players.append(
            player.model_copy(
                update={
                    "role": (
                        Role.TRAITOR
                        if player.id in casting.traitor_ids
                        else Role.FAITHFUL
                    )
                }
            )
        )
    state = GameState(
        game_id=casting.game_id,
        players=players,
        phase=Phase.LOBBY,
        round=1,
        traitor_ids=set(casting.traitor_ids),
        public_history=[casting.events[1]],
        conclave_history=[],
        diaries={player.id: [] for player in players},
        rng_seed=casting.rng_seed,
    )

    for player in state.players:
        assert_no_leak(build_context(state, player.id), state)


@pytest.mark.asyncio
async def test_audition_is_cost_bounded() -> None:
    calls: list[tuple[str, int]] = []

    def responder(
        messages: Sequence[Message],
        schema: type[BaseModel],
        model: str,
        budget: Budget,
        phase: str | None,
    ) -> dict[str, object]:
        del messages, schema, phase
        calls.append((model, budget.max_tokens))
        return AuditionScore(
            coherence=8,
            play_quality=7,
            notes="Focused and playable.",
        ).model_dump(mode="json")

    result = await audition_submission(
        _submission("Bounded"),
        MockProvider(responder=responder),
        max_calls=1,
        max_tokens=96,
    )

    assert calls == [("mock-fixed-tier", 96)]
    assert result.calls_used == 1
    assert result.tokens_used == 96
    assert result.coherence == 8
    assert result.play_quality == 7
