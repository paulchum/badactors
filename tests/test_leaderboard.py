"""Tests for deterministic leaderboard ratings."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from badactors.platform.leaderboard import GamePlayerRecord, compute_ratings
from badactors.schemas import Event, Phase, Role, Visibility
from badactors.server.app import EventSink, create_app


def test_ratings_deterministic_from_records() -> None:
    games = (
        (
            GamePlayerRecord("aria", Role.TRAITOR, True, "creator-a"),
            GamePlayerRecord("bex", Role.FAITHFUL, False, "creator-b"),
            GamePlayerRecord("cy", Role.FAITHFUL, False, "creator-c"),
        ),
        (
            GamePlayerRecord("aria", Role.FAITHFUL, False, "creator-a"),
            GamePlayerRecord("bex", Role.TRAITOR, False, "creator-b"),
            GamePlayerRecord("cy", Role.FAITHFUL, True, "creator-c"),
        ),
    )

    first = compute_ratings(games)
    second = compute_ratings(tuple(tuple(record for record in game) for game in games))

    assert second == first
    assert [entry.id for entry in first.agents] == [
        entry.id for entry in second.agents
    ]


def test_traitor_win_weighted_more() -> None:
    board = compute_ratings(
        (
            (
                GamePlayerRecord("traitor-winner", Role.TRAITOR, True, "ct"),
                GamePlayerRecord("traitor-opponent", Role.FAITHFUL, False, "cf"),
            ),
            (
                GamePlayerRecord("faithful-winner", Role.FAITHFUL, True, "cf"),
                GamePlayerRecord("faithful-opponent", Role.TRAITOR, False, "ct"),
            ),
        )
    )
    ratings = {entry.id: entry.rating for entry in board.agents}

    assert ratings["traitor-winner"] > ratings["faithful-winner"]


def test_ratings_update_on_completion() -> None:
    sink = EventSink()
    client = TestClient(create_app(sink))
    sink.append(
        _event(
            1,
            "RolesAssigned",
            {
                "roles": {"aria": "TRAITOR", "bex": "FAITHFUL"},
                "creators": {"aria": "creator-a", "bex": "creator-b"},
            },
            visibility=Visibility.AUDIENCE,
        )
    )

    assert client.get("/leaderboard").json() == {"agents": [], "creators": []}

    sink.append(
        _event(
            2,
            "GameOver",
            {"winning_role": "TRAITOR", "reason": "traitor parity"},
            phase=Phase.GAME_OVER,
        )
    )
    payload = client.get("/leaderboard").json()

    assert payload["agents"][0]["id"] == "aria"
    assert payload["agents"][0]["traitor_wins"] == 1
    assert payload["creators"][0]["id"] == "creator-a"
    assert payload["creators"][0]["wins"] == 1


def _event(
    event_id: int,
    event_type: str,
    payload: dict[str, object],
    *,
    visibility: Visibility = Visibility.PUBLIC,
    phase: Phase = Phase.LOBBY,
) -> Event:
    return Event(
        id=event_id,
        game_id="leaderboard-game",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        phase=phase,
        type=event_type,
        payload=payload,
        visibility=visibility,
    )
