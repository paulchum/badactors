"""Cross-game memory and compute budget ladder."""

from __future__ import annotations

from dataclasses import dataclass

from badactors.providers.base import Budget
from badactors.schemas import GameState, Role


@dataclass(frozen=True)
class GameOutcome:
    agent_id: str
    role: Role
    won: bool
    banished: bool
    diary_entries: tuple[str, ...]
    outcome_summary: str


@dataclass(frozen=True)
class MemoryRecord:
    entries: tuple[str, ...]
    throttled: bool = False


class MemoryStore:
    def __init__(self, *, max_entries: int = 6) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._outcomes: dict[str, GameOutcome] = {}
        self._max_entries = max_entries

    def record_outcome(self, outcome: GameOutcome) -> None:
        self._outcomes[outcome.agent_id] = outcome
        if outcome.won and not outcome.banished:
            self._records[outcome.agent_id] = MemoryRecord(
                entries=self._winner_entries(outcome),
                throttled=False,
            )
            return

        self._records[outcome.agent_id] = MemoryRecord(entries=(), throttled=True)

    def memory_for(self, agent_id: str) -> MemoryRecord:
        return self._records.get(agent_id, MemoryRecord(entries=()))

    def inject_memory(self, state: GameState, agent_id: str) -> GameState:
        record = self.memory_for(agent_id)
        if not record.entries:
            return state.model_copy(deep=True)

        diaries = {
            player_id: list(entries) for player_id, entries in state.diaries.items()
        }
        diaries.setdefault(agent_id, [])
        diaries[agent_id] = [
            *_format_memory_entries(record.entries),
            *diaries[agent_id],
        ]
        return state.model_copy(update={"diaries": diaries}, deep=True)

    def budget_for(self, agent_id: str, base: Budget) -> Budget:
        outcome = self._outcomes.get(agent_id)
        if outcome is None:
            return base
        return budget_for_outcome(outcome, base)

    def _winner_entries(self, outcome: GameOutcome) -> tuple[str, ...]:
        raw_entries = (
            f"Prior game outcome for {outcome.agent_id}: {outcome.outcome_summary}",
            *(
                f"Prior own diary for {outcome.agent_id}: {entry}"
                for entry in outcome.diary_entries
            ),
        )
        return tuple(raw_entries[-self._max_entries :])


def budget_for_outcome(outcome: GameOutcome, base: Budget) -> Budget:
    if outcome.won and not outcome.banished:
        return Budget(max_tokens=base.max_tokens + 256, thinking=True)
    if outcome.banished:
        return Budget(max_tokens=max(64, base.max_tokens // 2), thinking=False)
    return Budget(max_tokens=max(128, (base.max_tokens * 3) // 4), thinking=False)


def _format_memory_entries(entries: tuple[str, ...]) -> list[str]:
    return [f"[cross-game memory] {entry}" for entry in entries]
