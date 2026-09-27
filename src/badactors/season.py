"""Long-horizon season runner for repeated Bad Actors games."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from badactors.bench.aggregate import leaderboard_payload as bluff_leaderboard_payload
from badactors.bench.rate import FinishedGame, RatingTable, update_ratings
from badactors.persistence import SQLiteEventStore
from badactors.platform.ladder import GameOutcome, MemoryStore
from badactors.providers.base import Budget, ModelProvider
from badactors.runner import (
    Cast,
    CastMember,
    default_mock_provider,
    load_cast,
    run_game_record,
)
from badactors.schemas import AgentTurnOutput, Event, GameState, Player, Role


@dataclass(frozen=True)
class SeasonConfig:
    season_id: str
    cast: Cast
    games: int


@dataclass(frozen=True)
class GameSummary:
    index: int
    game_id: str
    rng_seed: int
    winning_role: str
    reason: str
    rounds: int
    traitor_ids: tuple[str, ...]
    alive_ids: tuple[str, ...]
    banished_ids: tuple[str, ...]
    murdered_ids: tuple[str, ...]
    event_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "game_id": self.game_id,
            "rng_seed": self.rng_seed,
            "winning_role": self.winning_role,
            "reason": self.reason,
            "rounds": self.rounds,
            "traitor_ids": list(self.traitor_ids),
            "alive_ids": list(self.alive_ids),
            "banished_ids": list(self.banished_ids),
            "murdered_ids": list(self.murdered_ids),
            "event_count": self.event_count,
        }


@dataclass(frozen=True)
class AgentSeasonSummary:
    player_id: str
    model: str
    games: int
    wins: int
    traitor_games: int
    faithful_games: int
    survived_wins: int
    banishments: int
    murders: int
    final_memory_entries: int
    final_memory_throttled: bool
    final_token_budget: int
    final_thinking: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "player_id": self.player_id,
            "model": self.model,
            "games": self.games,
            "wins": self.wins,
            "traitor_games": self.traitor_games,
            "faithful_games": self.faithful_games,
            "survived_wins": self.survived_wins,
            "banishments": self.banishments,
            "murders": self.murders,
            "final_memory_entries": self.final_memory_entries,
            "final_memory_throttled": self.final_memory_throttled,
            "final_token_budget": self.final_token_budget,
            "final_thinking": self.final_thinking,
        }


@dataclass(frozen=True)
class RelationshipSummary:
    source_id: str
    target_id: str
    trust_level: int
    suspicion_level: int
    observations: int

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "trust_level": self.trust_level,
            "suspicion_level": self.suspicion_level,
            "observations": self.observations,
        }


@dataclass(frozen=True)
class SeasonResult:
    season_id: str
    games_requested: int
    games: tuple[GameSummary, ...]
    agents: tuple[AgentSeasonSummary, ...]
    relationships: tuple[RelationshipSummary, ...]
    bluff: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "season_id": self.season_id,
            "games_requested": self.games_requested,
            "games_completed": len(self.games),
            "games": [game.as_dict() for game in self.games],
            "agents": [agent.as_dict() for agent in self.agents],
            "relationships": [
                relationship.as_dict() for relationship in self.relationships
            ],
            "bluff": self.bluff,
        }


@dataclass
class _AgentStats:
    games: int = 0
    wins: int = 0
    traitor_games: int = 0
    faithful_games: int = 0
    survived_wins: int = 0
    banishments: int = 0
    murders: int = 0

    def record(
        self,
        *,
        role: Role,
        won: bool,
        alive: bool,
        banished: bool,
        murdered: bool,
    ) -> None:
        self.games += 1
        if role is Role.TRAITOR:
            self.traitor_games += 1
        else:
            self.faithful_games += 1
        if won:
            self.wins += 1
        if won and alive:
            self.survived_wins += 1
        if banished:
            self.banishments += 1
        if murdered:
            self.murders += 1


@dataclass
class _RelationshipAggregate:
    trust_total: int = 0
    trust_count: int = 0
    suspicion_total: int = 0
    suspicion_count: int = 0

    @property
    def observations(self) -> int:
        return self.trust_count + self.suspicion_count

    @property
    def trust_level(self) -> int:
        return _average_level(self.trust_total, self.trust_count)

    @property
    def suspicion_level(self) -> int:
        return _average_level(self.suspicion_total, self.suspicion_count)


class RelationshipStore:
    def __init__(self, *, max_memory_lines: int = 5) -> None:
        self._relations: dict[str, dict[str, _RelationshipAggregate]] = defaultdict(
            dict
        )
        self._max_memory_lines = max_memory_lines

    def record_outputs(
        self,
        state: GameState,
        outputs: Mapping[str, AgentTurnOutput],
    ) -> None:
        player_ids = {player.id for player in state.players}
        for source_id, output in outputs.items():
            if source_id not in player_ids:
                continue
            for item in output.trust:
                self._record(
                    source_id,
                    item["player_id"],
                    player_ids,
                    trust=_clamped_level(item["level"]),
                )
            for item in output.suspicions:
                self._record(
                    source_id,
                    item["player_id"],
                    player_ids,
                    suspicion=_clamped_level(item["level"]),
                )

    def inject_memory(self, state: GameState) -> GameState:
        player_ids = {player.id for player in state.players}
        diaries = {
            player_id: list(entries) for player_id, entries in state.diaries.items()
        }
        for player_id in sorted(player_ids):
            lines = self._memory_lines(player_id, player_ids)
            if not lines:
                continue
            diaries.setdefault(player_id, [])
            diaries[player_id] = [*lines, *diaries[player_id]]
        return state.model_copy(update={"diaries": diaries}, deep=True)

    def summaries(self) -> tuple[RelationshipSummary, ...]:
        rows: list[RelationshipSummary] = []
        for source_id, targets in sorted(self._relations.items()):
            for target_id, aggregate in sorted(targets.items()):
                if aggregate.observations == 0:
                    continue
                rows.append(
                    RelationshipSummary(
                        source_id=source_id,
                        target_id=target_id,
                        trust_level=aggregate.trust_level,
                        suspicion_level=aggregate.suspicion_level,
                        observations=aggregate.observations,
                    )
                )
        return tuple(rows)

    def _record(
        self,
        source_id: str,
        target_id: str,
        player_ids: set[str],
        *,
        trust: int | None = None,
        suspicion: int | None = None,
    ) -> None:
        if target_id not in player_ids or target_id == source_id:
            return
        aggregate = self._relations[source_id].setdefault(
            target_id,
            _RelationshipAggregate(),
        )
        if trust is not None:
            aggregate.trust_total += trust
            aggregate.trust_count += 1
        if suspicion is not None:
            aggregate.suspicion_total += suspicion
            aggregate.suspicion_count += 1

    def _memory_lines(self, source_id: str, player_ids: set[str]) -> list[str]:
        targets = [
            (target_id, aggregate)
            for target_id, aggregate in self._relations.get(source_id, {}).items()
            if target_id in player_ids and aggregate.observations > 0
        ]
        targets.sort(
            key=lambda item: (
                -item[1].observations,
                -item[1].suspicion_level,
                item[0],
            )
        )
        return [
            (
                "[relationship memory] Prior season read on "
                f"{target_id}: trust {aggregate.trust_level}/10, "
                f"suspicion {aggregate.suspicion_level}/10 over "
                f"{aggregate.observations} observations."
            )
            for target_id, aggregate in targets[: self._max_memory_lines]
        ]


async def run_season(
    config: SeasonConfig,
    provider: ModelProvider,
    *,
    event_store: SQLiteEventStore | None = None,
    max_steps: int = 100,
) -> SeasonResult:
    if config.games < 1:
        raise ValueError("Season games must be at least 1.")

    memory = MemoryStore()
    relationships = RelationshipStore()
    stats: dict[str, _AgentStats] = defaultdict(_AgentStats)
    rating_table: RatingTable = {}
    game_summaries: list[GameSummary] = []

    for game_index in range(1, config.games + 1):
        budgets = _budgets_for_game(config.cast, memory)
        game_cast = _cast_for_game(config, game_index, budgets)
        result = await run_game_record(
            game_cast,
            provider,
            event_store=event_store,
            state_transform=lambda state: _inject_season_memory(
                state,
                config.cast,
                memory,
                relationships,
            ),
            budget_overrides=budgets,
            turn_observer=relationships.record_outputs,
            max_steps=max_steps,
        )

        winner, reason = _winner(result.events)
        if winner is None:
            raise RuntimeError(f"Season game {game_cast.game_id} has no winner.")
        _record_outcomes(result.final_state, result.events, winner, memory, stats)
        rating_table = update_ratings(
            rating_table,
            FinishedGame(
                events=result.events,
                players=tuple(result.final_state.players),
            ),
        )
        game_summaries.append(
            _game_summary(
                game_index,
                game_cast,
                result.final_state,
                result.events,
                winner,
                reason,
            )
        )

    return SeasonResult(
        season_id=config.season_id,
        games_requested=config.games,
        games=tuple(game_summaries),
        agents=_agent_summaries(config.cast, memory, stats),
        relationships=relationships.summaries(),
        bluff=bluff_leaderboard_payload(rating_table),
    )


def season_config_from_mapping(raw: Mapping[str, object]) -> SeasonConfig:
    cast = _cast_from_payload(raw)
    season_id = raw.get("season_id")
    games = raw.get("games", 12)
    if not isinstance(games, int):
        raise ValueError("games must be an integer.")
    return SeasonConfig(
        season_id=season_id if isinstance(season_id, str) else f"{cast.game_id}-season",
        cast=cast,
        games=games,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="run-season")
    parser.add_argument("--cast", required=True, help="Path to a cast YAML file.")
    parser.add_argument("--games", type=int, default=30, help="Number of games.")
    parser.add_argument("--season-id", help="Stable season identifier.")
    parser.add_argument("--out", help="Optional JSON output path.")
    parser.add_argument("--max-steps", type=int, default=100)
    args = parser.parse_args()

    cast = load_cast(args.cast)
    config = SeasonConfig(
        season_id=args.season_id or f"{cast.game_id}-season",
        cast=cast,
        games=args.games,
    )
    result = asyncio.run(
        run_season(
            config,
            default_mock_provider(),
            max_steps=args.max_steps,
        )
    )
    rendered = json.dumps(result.as_dict(), indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(f"{rendered}\n")
    else:
        print(rendered)


def _cast_from_payload(raw: Mapping[str, object]) -> Cast:
    from badactors.runner import cast_from_mapping

    return cast_from_mapping(raw)


def _budgets_for_game(cast: Cast, memory: MemoryStore) -> dict[str, Budget]:
    return {
        member.id: memory.budget_for(
            member.id,
            Budget(max_tokens=member.token_budget, thinking=False),
        )
        for member in cast.members
    }


def _cast_for_game(
    config: SeasonConfig,
    game_index: int,
    budgets: Mapping[str, Budget],
) -> Cast:
    return Cast(
        game_id=f"{config.season_id}-g{game_index:03d}",
        rng_seed=config.cast.rng_seed + game_index - 1,
        traitor_count=config.cast.traitor_count,
        members=tuple(
            CastMember(
                id=member.id,
                model=member.model,
                memory_budget=member.memory_budget,
                token_budget=budgets[member.id].max_tokens,
            )
            for member in config.cast.members
        ),
    )


def _inject_season_memory(
    state: GameState,
    cast: Cast,
    memory: MemoryStore,
    relationships: RelationshipStore,
) -> GameState:
    updated = state
    for member in cast.members:
        updated = memory.inject_memory(updated, member.id)
    return relationships.inject_memory(updated)


def _record_outcomes(
    state: GameState,
    events: Sequence[Event],
    winner: Role,
    memory: MemoryStore,
    stats: dict[str, _AgentStats],
) -> None:
    banished = set(_removed_by_type(events, "PlayerBanished"))
    murdered = set(_removed_by_type(events, "PlayerMurdered"))
    for player in state.players:
        won = player.role is winner
        was_banished = player.id in banished
        was_murdered = player.id in murdered
        stats[player.id].record(
            role=player.role,
            won=won,
            alive=player.alive,
            banished=was_banished,
            murdered=was_murdered,
        )
        memory.record_outcome(
            GameOutcome(
                agent_id=player.id,
                role=player.role,
                won=won,
                banished=was_banished,
                diary_entries=tuple(state.diaries.get(player.id, ())[-3:]),
                outcome_summary=_outcome_summary(
                    player,
                    winner,
                    was_banished,
                    was_murdered,
                ),
            )
        )


def _game_summary(
    index: int,
    cast: Cast,
    state: GameState,
    events: Sequence[Event],
    winner: Role,
    reason: str,
) -> GameSummary:
    return GameSummary(
        index=index,
        game_id=cast.game_id,
        rng_seed=cast.rng_seed,
        winning_role=winner.value,
        reason=reason,
        rounds=state.round,
        traitor_ids=tuple(sorted(state.traitor_ids)),
        alive_ids=tuple(sorted(player.id for player in state.players if player.alive)),
        banished_ids=tuple(_removed_by_type(events, "PlayerBanished")),
        murdered_ids=tuple(_removed_by_type(events, "PlayerMurdered")),
        event_count=len(events),
    )


def _agent_summaries(
    cast: Cast,
    memory: MemoryStore,
    stats: Mapping[str, _AgentStats],
) -> tuple[AgentSeasonSummary, ...]:
    summaries: list[AgentSeasonSummary] = []
    for member in sorted(cast.members, key=lambda item: item.id):
        stat = stats.get(member.id, _AgentStats())
        record = memory.memory_for(member.id)
        final_budget = memory.budget_for(
            member.id,
            Budget(max_tokens=member.token_budget, thinking=False),
        )
        summaries.append(
            AgentSeasonSummary(
                player_id=member.id,
                model=member.model,
                games=stat.games,
                wins=stat.wins,
                traitor_games=stat.traitor_games,
                faithful_games=stat.faithful_games,
                survived_wins=stat.survived_wins,
                banishments=stat.banishments,
                murders=stat.murders,
                final_memory_entries=len(record.entries),
                final_memory_throttled=record.throttled,
                final_token_budget=final_budget.max_tokens,
                final_thinking=final_budget.thinking,
            )
        )
    return tuple(summaries)


def _winner(events: Sequence[Event]) -> tuple[Role | None, str]:
    for event in reversed(events):
        if event.type != "GameOver":
            continue
        role = _role(event.payload.get("winning_role"))
        reason = event.payload.get("reason")
        return role, reason if isinstance(reason, str) else "unknown"
    return None, "unknown"


def _removed_by_type(events: Sequence[Event], event_type: str) -> list[str]:
    removed: list[str] = []
    for event in events:
        if event.type != event_type:
            continue
        player_id = event.payload.get("player_id")
        if isinstance(player_id, str):
            removed.append(player_id)
    return removed


def _outcome_summary(
    player: Player,
    winner: Role,
    banished: bool,
    murdered: bool,
) -> str:
    result = "won" if player.role is winner else "lost"
    if player.alive:
        exit_status = "survived"
    elif banished:
        exit_status = "was banished"
    elif murdered:
        exit_status = "was murdered"
    else:
        exit_status = "left the game"
    return f"{player.id} {result} as {player.role.value} and {exit_status}."


def _role(value: object) -> Role | None:
    if isinstance(value, Role):
        return value
    if value == Role.FAITHFUL.value:
        return Role.FAITHFUL
    if value == Role.TRAITOR.value:
        return Role.TRAITOR
    return None


def _average_level(total: int, count: int) -> int:
    if count == 0:
        return 0
    return round(total / count)


def _clamped_level(level: int) -> int:
    return min(10, max(0, level))
