"""Tests for cross-game memory and compute ladder."""

from __future__ import annotations

from badactors.platform.ladder import GameOutcome, MemoryStore, budget_for_outcome
from badactors.providers.base import Budget
from badactors.schemas import GameState, Phase, Player, Role
from badactors.scoping import assert_no_leak, build_context


def _player(player_id: str, role: Role) -> Player:
    return Player(
        id=player_id,
        model="mock-fixed-tier",
        role=role,
        memory_budget=100,
        token_budget=200,
    )


def _state() -> GameState:
    players = [
        _player("p1", Role.FAITHFUL),
        _player("p2", Role.TRAITOR),
        _player("p3", Role.FAITHFUL),
    ]
    return GameState(
        game_id="next-game",
        players=players,
        phase=Phase.ROUND_TABLE,
        round=1,
        traitor_ids={"p2"},
        public_history=[],
        conclave_history=[],
        diaries={player.id: [] for player in players},
        rng_seed=13,
    )


def _outcome(
    agent_id: str,
    *,
    won: bool,
    banished: bool = False,
    role: Role = Role.FAITHFUL,
    diary_entries: tuple[str, ...] = ("I noticed p2 overplayed the vote.",),
) -> GameOutcome:
    return GameOutcome(
        agent_id=agent_id,
        role=role,
        won=won,
        banished=banished,
        diary_entries=diary_entries,
        outcome_summary="Faithful win with a clean final vote.",
    )


def test_winner_memory_persists_and_injects() -> None:
    store = MemoryStore()
    store.record_outcome(_outcome("p1", won=True))

    injected = store.inject_memory(_state(), "p1")
    p1_context = build_context(injected, "p1")
    p3_context = build_context(injected, "p3")

    rendered_memory = "\n".join(p1_context.your_diary_history)
    assert "Prior game outcome for p1" in rendered_memory
    assert "I noticed p2 overplayed the vote." in rendered_memory
    assert p3_context.your_diary_history == []


def test_memory_injection_keeps_leakage_invariant() -> None:
    store = MemoryStore()
    store.record_outcome(
        _outcome(
            "p1",
            won=True,
            diary_entries=(
                "My own prior diary says I suspected the late pile-on.",
                "My own outcome note never names another hidden role.",
            ),
        )
    )

    injected = store.inject_memory(_state(), "p1")

    for player in injected.players:
        assert_no_leak(build_context(injected, player.id), injected)


def test_banished_memory_wiped_or_throttled() -> None:
    store = MemoryStore()
    store.record_outcome(
        _outcome(
            "p1",
            won=True,
            diary_entries=("Winner note that should be replaced.",),
        )
    )
    store.record_outcome(_outcome("p1", won=False, banished=True))

    record = store.memory_for("p1")
    injected = store.inject_memory(_state(), "p1")

    assert record.throttled is True
    assert record.entries == ()
    assert build_context(injected, "p1").your_diary_history == []


def test_budget_reflects_outcome() -> None:
    base = Budget(max_tokens=200, thinking=False)

    winner_budget = budget_for_outcome(_outcome("p1", won=True), base)
    banished_budget = budget_for_outcome(
        _outcome("p1", won=False, banished=True),
        base,
    )
    loser_budget = budget_for_outcome(_outcome("p1", won=False), base)

    assert winner_budget.max_tokens > base.max_tokens
    assert winner_budget.thinking is True
    assert banished_budget.max_tokens < base.max_tokens
    assert banished_budget.thinking is False
    assert loser_budget.max_tokens < base.max_tokens
    assert loser_budget.max_tokens > banished_budget.max_tokens
