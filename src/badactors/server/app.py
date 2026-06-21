"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException, Request
from starlette.responses import StreamingResponse

from badactors.persistence import SQLiteEventStore
from badactors.runner import cast_from_mapping, default_mock_provider, run_game
from badactors.schemas import Event

GameBody = Annotated[dict[str, object] | None, Body()]


class EventSink(SQLiteEventStore):
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_tables()
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = {}

    def append(self, event: Event) -> None:
        super().append(event)
        self.emit(event)

    def emit(self, event: Event) -> None:
        for queue in list(self._subscribers.get(event.game_id, [])):
            queue.put_nowait(event)

    async def subscribe(self, game_id: str) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.setdefault(game_id, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers = self._subscribers.get(game_id, [])
            if queue in subscribers:
                subscribers.remove(queue)


def create_app(event_sink: EventSink | None = None) -> FastAPI:
    sink = event_sink if event_sink is not None else EventSink()
    api = FastAPI()
    api.state.event_sink = sink

    @api.post("/games")
    async def create_game(
        payload: GameBody = None,
    ) -> dict[str, object]:
        cast_payload = payload if payload is not None else _default_cast_payload()
        cast = cast_from_mapping(cast_payload)
        if sink.load(cast.game_id):
            raise HTTPException(status_code=409, detail="Game already exists.")

        transcript = await run_game(
            cast,
            default_mock_provider(),
            event_store=sink,
        )
        return {
            "game_id": cast.game_id,
            "transcript": transcript,
            "events": [_event_payload(event) for event in sink.load(cast.game_id)],
        }

    @api.get("/games/{game_id}/stream")
    async def stream_game(game_id: str, request: Request) -> StreamingResponse:
        if not sink.load(game_id):
            raise HTTPException(status_code=404, detail="Game not found.")
        return StreamingResponse(
            _stream_events(sink, game_id, request),
            media_type="text/event-stream",
        )

    @api.get("/games/{game_id}")
    async def get_game(game_id: str) -> dict[str, object]:
        events = sink.load(game_id)
        if not events:
            raise HTTPException(status_code=404, detail="Game not found.")
        return {
            "game_id": game_id,
            "events": [_event_payload(event) for event in events],
        }

    @api.post("/games/{game_id}/replay")
    async def replay_game(game_id: str) -> dict[str, object]:
        events = sink.load(game_id)
        if not events:
            raise HTTPException(status_code=404, detail="Game not found.")
        for event in events:
            sink.emit(event)
        return {
            "game_id": game_id,
            "events": [_event_payload(event) for event in events],
        }

    return api


async def _stream_events(
    sink: EventSink,
    game_id: str,
    request: Request,
) -> AsyncIterator[str]:
    sent_ids: set[int] = set()
    stored_events = sink.load(game_id)
    for event in stored_events:
        if await request.is_disconnected():
            return
        sent_ids.add(event.id)
        yield _sse(event)

    if _has_game_over(stored_events):
        return

    async for event in sink.subscribe(game_id):
        if await request.is_disconnected():
            return
        if event.id in sent_ids:
            continue
        sent_ids.add(event.id)
        yield _sse(event)
        if event.type == "GameOver":
            return


def _sse(event: Event) -> str:
    return f"id: {event.id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


def _event_payload(event: Event) -> dict[str, object]:
    return dict(event.model_dump(mode="json"))


def _has_game_over(events: list[Event]) -> bool:
    return any(event.type == "GameOver" for event in events)


def _default_cast_payload() -> Mapping[str, object]:
    game_id = f"game-{uuid.uuid4().hex[:12]}"
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


app = create_app()
