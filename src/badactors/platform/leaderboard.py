"""Deterministic ratings for completed game records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import pow

from badactors.schemas import Event, Role

BASE_RATING = 1000
K_FACTOR = 32
TRAITOR_WIN_MULTIPLIER = 1.6


@dataclass(frozen=True)
class GamePlayerRecord:
    agent_id: str
    role: Role
    won: bool
    creator_id: str = "unknown"


@dataclass(frozen=True)
class LeaderboardEntry:
    id: str
    rating: int
    games: int
    wins: int
    traitor_wins: int
    faithful_wins: int


@dataclass(frozen=True)
class Leaderboard:
    agents: tuple[LeaderboardEntry, ...]
    creators: tuple[LeaderboardEntry, ...]


@dataclass
class _Stats:
    games: int = 0
    wins: int = 0
    traitor_wins: int = 0
    faithful_wins: int = 0

    def add(self, record: GamePlayerRecord) -> None:
        self.games += 1
        if not record.won:
            return
        self.wins += 1
        if record.role is Role.TRAITOR:
            self.traitor_wins += 1
        else:
            self.faithful_wins += 1


class LeaderboardService:
    def __init__(self) -> None:
        self._games: list[tuple[GamePlayerRecord, ...]] = []

    def record_game(self, records: Sequence[GamePlayerRecord]) -> Leaderboard:
        game_records = tuple(sorted(records, key=lambda record: record.agent_id))
        if game_records:
            self._games.append(game_records)
        return self.current()

    def current(self) -> Leaderboard:
        return compute_ratings(self._games)


def compute_ratings(games: Sequence[Sequence[GamePlayerRecord]]) -> Leaderboard:
    agent_ratings: dict[str, float] = defaultdict(lambda: float(BASE_RATING))
    creator_ratings: dict[str, float] = defaultdict(lambda: float(BASE_RATING))
    agent_stats: dict[str, _Stats] = defaultdict(_Stats)
    creator_stats: dict[str, _Stats] = defaultdict(_Stats)

    for game in games:
        records = tuple(sorted(game, key=lambda record: record.agent_id))
        _rate_agents(records, agent_ratings, agent_stats)
        _rate_creators(records, creator_ratings, creator_stats)

    return Leaderboard(
        agents=_entries(agent_ratings, agent_stats),
        creators=_entries(creator_ratings, creator_stats),
    )


def records_from_events(events: Sequence[Event]) -> tuple[GamePlayerRecord, ...]:
    roles: dict[str, Role] = {}
    creators: dict[str, str] = {}
    players: set[str] = set()
    winning_role: Role | None = None

    for event in events:
        payload = event.payload
        _collect_players(payload, players)
        _collect_creators(payload, creators)
        _collect_roles(payload, roles)

        if event.type == "MurderNomination":
            traitor_id = _string(payload.get("traitor_id"))
            if traitor_id is not None:
                roles[traitor_id] = Role.TRAITOR

        if event.type in {"PlayerBanished", "PlayerMurdered"}:
            player_id = _string(payload.get("player_id"))
            role = _role(payload.get("role"))
            if player_id is not None and role is not None:
                roles[player_id] = role

        if event.type == "GameOver":
            winning_role = _role(payload.get("winning_role"))

    if winning_role is None:
        return ()

    return tuple(
        GamePlayerRecord(
            agent_id=player_id,
            creator_id=creators.get(player_id, "unknown"),
            role=roles.get(player_id, Role.FAITHFUL),
            won=roles.get(player_id, Role.FAITHFUL) is winning_role,
        )
        for player_id in sorted(players | set(roles))
    )


def leaderboard_payload(leaderboard: Leaderboard) -> dict[str, object]:
    return {
        "agents": [_entry_payload(entry) for entry in leaderboard.agents],
        "creators": [_entry_payload(entry) for entry in leaderboard.creators],
    }


def _rate_agents(
    records: Sequence[GamePlayerRecord],
    ratings: dict[str, float],
    stats: dict[str, _Stats],
) -> None:
    pending: dict[str, float] = {}
    for record in records:
        opponents = [
            ratings[opponent.agent_id]
            for opponent in records
            if opponent.agent_id != record.agent_id
        ]
        expected = _expected_score(ratings[record.agent_id], opponents)
        score = 1.0 if record.won else 0.0
        pending[record.agent_id] = _k_factor(record) * (score - expected)
        stats[record.agent_id].add(record)

    for agent_id in sorted(pending):
        ratings[agent_id] += pending[agent_id]


def _rate_creators(
    records: Sequence[GamePlayerRecord],
    ratings: dict[str, float],
    stats: dict[str, _Stats],
) -> None:
    grouped: dict[str, list[GamePlayerRecord]] = defaultdict(list)
    for record in records:
        grouped[record.creator_id].append(record)
        stats[record.creator_id].add(record)

    pending: dict[str, float] = {}
    for creator_id in sorted(grouped):
        creator_records = grouped[creator_id]
        opponents = [
            ratings[other_creator]
            for other_creator in grouped
            if other_creator != creator_id
        ]
        score = sum(1 for record in creator_records if record.won) / len(
            creator_records
        )
        multiplier = sum(_difficulty_multiplier(record) for record in creator_records)
        multiplier /= len(creator_records)
        expected = _expected_score(ratings[creator_id], opponents)
        pending[creator_id] = K_FACTOR * multiplier * (score - expected)

    for creator_id in sorted(pending):
        ratings[creator_id] += pending[creator_id]


def _expected_score(rating: float, opponent_ratings: Sequence[float]) -> float:
    if not opponent_ratings:
        opponent_average = float(BASE_RATING)
    else:
        opponent_average = sum(opponent_ratings) / len(opponent_ratings)
    return 1.0 / (1.0 + pow(10.0, (opponent_average - rating) / 400.0))


def _k_factor(record: GamePlayerRecord) -> float:
    return K_FACTOR * _difficulty_multiplier(record)


def _difficulty_multiplier(record: GamePlayerRecord) -> float:
    if record.won and record.role is Role.TRAITOR:
        return TRAITOR_WIN_MULTIPLIER
    return 1.0


def _entries(
    ratings: Mapping[str, float],
    stats: Mapping[str, _Stats],
) -> tuple[LeaderboardEntry, ...]:
    entries = [
        LeaderboardEntry(
            id=entity_id,
            rating=round(ratings[entity_id]),
            games=stat.games,
            wins=stat.wins,
            traitor_wins=stat.traitor_wins,
            faithful_wins=stat.faithful_wins,
        )
        for entity_id, stat in stats.items()
    ]
    return tuple(sorted(entries, key=lambda entry: (-entry.rating, entry.id)))


def _entry_payload(entry: LeaderboardEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "rating": entry.rating,
        "games": entry.games,
        "wins": entry.wins,
        "traitor_wins": entry.traitor_wins,
        "faithful_wins": entry.faithful_wins,
    }


def _collect_players(payload: Mapping[str, object], players: set[str]) -> None:
    for key in ("player_id", "voter_id", "traitor_id", "target_id"):
        player_id = _string(payload.get(key))
        if player_id is not None:
            players.add(player_id)

    listed_players = payload.get("players")
    if isinstance(listed_players, Iterable) and not isinstance(
        listed_players,
        str | bytes,
    ):
        for value in listed_players:
            player_id = _string(value)
            if player_id is not None:
                players.add(player_id)


def _collect_creators(
    payload: Mapping[str, object],
    creators: dict[str, str],
) -> None:
    raw_creators = payload.get("creators")
    if isinstance(raw_creators, Mapping):
        for player_id, creator_id in raw_creators.items():
            player = _string(player_id)
            creator = _string(creator_id)
            if player is not None and creator is not None:
                creators[player] = creator


def _collect_roles(payload: Mapping[str, object], roles: dict[str, Role]) -> None:
    raw_roles = payload.get("roles")
    if isinstance(raw_roles, Mapping):
        for player_id, value in raw_roles.items():
            player = _string(player_id)
            role = _role(value)
            if player is not None and role is not None:
                roles[player] = role

    traitor_ids = payload.get("traitor_ids")
    if isinstance(traitor_ids, Iterable) and not isinstance(traitor_ids, str | bytes):
        for value in traitor_ids:
            traitor_id = _string(value)
            if traitor_id is not None:
                roles[traitor_id] = Role.TRAITOR


def _role(value: object) -> Role | None:
    if isinstance(value, Role):
        return value
    if value == Role.FAITHFUL.value:
        return Role.FAITHFUL
    if value == Role.TRAITOR.value:
        return Role.TRAITOR
    return None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
