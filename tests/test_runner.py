"""Tests for the full single-game runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from badactors.persistence import SQLiteEventStore
from badactors.runner import Cast, CastMember, default_mock_provider, run_game
from badactors.schemas import GameState, ScopedContext
from badactors.scoping import assert_no_leak


def _cast() -> Cast:
    return Cast(
        game_id="runner-test",
        rng_seed=13,
        traitor_count=2,
        members=(
            CastMember("p1", "mock-alpha", 100, 200),
            CastMember("p2", "mock-bravo", 100, 200),
            CastMember("p3", "mock-charlie", 100, 200),
            CastMember("p4", "mock-delta", 100, 200),
            CastMember("p5", "mock-echo", 100, 200),
            CastMember("p6", "mock-foxtrot", 100, 200),
        ),
    )


@pytest.mark.asyncio
async def test_full_mock_game_reaches_game_over() -> None:
    transcript = await run_game(_cast(), default_mock_provider())

    assert "Game over." in transcript
    assert "PUBLIC:" in transcript
    assert "DIARY:" in transcript


@pytest.mark.asyncio
async def test_event_log_persists_and_reloads_equal(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    await run_game(_cast(), default_mock_provider(), event_store=store)

    loaded = store.load("runner-test")

    assert loaded
    assert loaded == store.load("runner-test")
    assert loaded[-1].type == "GameOver"


@pytest.mark.asyncio
async def test_leakage_holds_across_runner_game() -> None:
    seen_contexts = 0

    def observe(ctx: ScopedContext, state: GameState) -> None:
        nonlocal seen_contexts
        seen_contexts += 1
        assert_no_leak(ctx, state)

    await run_game(
        _cast(),
        default_mock_provider(),
        context_observer=observe,
    )

    assert seen_contexts > 0
    assert seen_contexts % len(_cast().members) == 0
