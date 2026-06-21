"""Tests for the FastAPI event stream backend."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from badactors.server.app import EventSink, create_app


def _cast_payload(game_id: str) -> dict[str, object]:
    return {
        "game_id": game_id,
        "rng_seed": 13,
        "traitor_count": 2,
        "memory_budget": 100,
        "token_budget": 200,
        "agents": {
            "mock-alpha": {"id": "p1"},
            "mock-bravo": {"id": "p2"},
            "mock-charlie": {"id": "p3"},
            "mock-delta": {"id": "p4"},
            "mock-echo": {"id": "p5"},
            "mock-foxtrot": {"id": "p6"},
        },
    }


def _client() -> TestClient:
    return TestClient(create_app(EventSink()))


def _sse_events(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            loaded = json.loads(line.removeprefix("data: "))
            if isinstance(loaded, dict):
                events.append(loaded)
    return events


def _create_game(client: TestClient, game_id: str) -> list[dict[str, object]]:
    response = client.post("/games", json=_cast_payload(game_id))
    assert response.status_code == 200
    payload = response.json()
    events = payload["events"]
    assert isinstance(events, list)
    return events


def test_stream_delivers_ordered_events() -> None:
    client = _client()
    created_events = _create_game(client, "stream-game")

    with client.stream("GET", "/games/stream-game/stream") as response:
        assert response.status_code == 200
        streamed_events = _sse_events(response.read().decode())

    assert [event["id"] for event in streamed_events] == sorted(
        event["id"] for event in streamed_events
    )
    assert [event["id"] for event in streamed_events] == [
        event["id"] for event in created_events
    ]
    assert streamed_events[-1]["type"] == "GameOver"


def test_replay_reproduces_stream() -> None:
    client = _client()
    _create_game(client, "replay-game")

    with client.stream("GET", "/games/replay-game/stream") as response:
        original_stream = _sse_events(response.read().decode())

    replay_response = client.post("/games/replay-game/replay")
    assert replay_response.status_code == 200
    replay_events = replay_response.json()["events"]

    with client.stream("GET", "/games/replay-game/stream") as response:
        replayed_stream = _sse_events(response.read().decode())

    assert replay_events == original_stream
    assert replayed_stream == original_stream


def test_record_endpoint_returns_log() -> None:
    client = _client()
    created_events = _create_game(client, "record-game")

    response = client.get("/games/record-game")

    assert response.status_code == 200
    payload = response.json()
    assert payload["game_id"] == "record-game"
    assert payload["events"] == created_events
