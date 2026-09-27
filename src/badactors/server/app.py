"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import hmac
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Body, FastAPI, HTTPException, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from badactors.persistence import ConflictError, SQLiteEventStore
from badactors.platform.leaderboard import (
    LeaderboardService,
    leaderboard_payload,
    records_from_events,
)
from badactors.runner import cast_from_mapping, default_mock_provider, run_game
from badactors.schemas import Event
from badactors.season import run_season, season_config_from_mapping
from badactors.server.worlds import world_routes
from badactors.simulation.runtime import WorldRuntime

GameBody = Annotated[dict[str, object] | None, Body()]


class EventSink(SQLiteEventStore):
    def __init__(self, path: str | Path = ":memory:") -> None:
        super().__init__(path)
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = {}
        self.leaderboard = LeaderboardService()
        self._completed_games: set[str] = set()
        self.seasons: dict[str, dict[str, object]] = {
            key: body for key, _, body in self.documents("season_result")
        }
        self.refresh_leaderboard()

    def refresh_leaderboard(self) -> None:
        """Rebuild in committed event order, including games from other workers."""
        self.leaderboard = LeaderboardService()
        self._completed_games.clear()
        for game_id in self.game_ids():
            events = self.load(game_id)
            if _has_game_over(events):
                self.leaderboard.record_game(records_from_events(events))
                self._completed_games.add(game_id)

    def events_committed(self, events: Sequence[Event]) -> None:
        for event in events:
            if event.type == "GameOver" and event.game_id not in self._completed_games:
                self.leaderboard.record_game(
                    records_from_events(self.load(event.game_id))
                )
                self._completed_games.add(event.game_id)
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
    sink = (
        event_sink
        if event_sink is not None
        else EventSink(os.getenv("BADACTORS_DB", "data/badactors.sqlite3"))
    )
    runtime = WorldRuntime(sink)

    @asynccontextmanager
    async def lifespan(_api: FastAPI) -> AsyncIterator[None]:
        worker = (
            asyncio.create_task(runtime.serve())
            if os.getenv("BADACTORS_WORKER", "1") == "1"
            else None
        )
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            if event_sink is None:
                sink.close()

    api = FastAPI(title="Bad Actors", version="0.2.0", lifespan=lifespan)
    api.include_router(world_routes(runtime))

    @api.middleware("http")
    async def owner_access(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        token = os.getenv("BADACTORS_API_TOKEN")
        if request.url.path == "/health":
            return await call_next(request)
        if token:
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(supplied, token):
                return JSONResponse(
                    {"detail": "Owner token required."}, status_code=401
                )
        elif request.client is None or request.client.host not in {
            "127.0.0.1",
            "::1",
            "testclient",
        }:
            return JSONResponse(
                {"detail": "Configure BADACTORS_API_TOKEN for remote access."},
                status_code=403,
            )
        elif request.url.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "testserver",
        }:
            return JSONResponse(
                {"detail": "A loopback hostname is required without an owner token."},
                status_code=403,
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).hostname != request.url.hostname:
                return JSONResponse(
                    {"detail": "Cross-origin writes are disabled."}, status_code=403
                )
        return await call_next(request)

    @api.exception_handler(ConflictError)
    async def conflict(_request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @api.exception_handler(ValueError)
    async def invalid(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @api.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "storage": "sqlite",
            "world_worker": os.getenv("BADACTORS_WORKER", "1") == "1",
        }

    api.state.event_sink = sink

    @api.post("/games")
    async def create_game(
        payload: GameBody = None,
    ) -> dict[str, object]:
        cast_payload = payload if payload is not None else _default_cast_payload()
        cast = cast_from_mapping(cast_payload)
        if len(cast.members) > 24 or any(
            member.token_budget > 4096 for member in cast.members
        ):
            raise HTTPException(
                422, "Limit games to 24 players and 4096 tokens per turn."
            )
        if sink.document("game", cast.game_id) or sink.load(cast.game_id):
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

    @api.get("/leaderboard")
    async def get_leaderboard() -> dict[str, object]:
        sink.refresh_leaderboard()
        return leaderboard_payload(sink.leaderboard.current())

    @api.post("/seasons")
    async def create_season(
        payload: GameBody = None,
    ) -> dict[str, object]:
        season_payload = payload if payload is not None else _default_season_payload()
        config = season_config_from_mapping(season_payload)
        if not 1 <= config.games <= 100 or len(config.cast.members) > 24:
            raise HTTPException(422, "Limit seasons to 100 games and 24 players.")
        if sink.document("season_result", config.season_id):
            raise HTTPException(status_code=409, detail="Season already exists.")

        result = await run_season(
            config,
            default_mock_provider(),
            event_store=sink,
        )
        body = result.as_dict()
        sink.save_document("season_result", result.season_id, body)
        sink.seasons[result.season_id] = body
        return body

    @api.get("/seasons")
    async def list_seasons() -> dict[str, object]:
        return {
            "seasons": [
                {
                    "season_id": body["season_id"],
                    "games_completed": body["games_completed"],
                }
                for _, _, body in sink.documents("season_result")
            ]
        }

    @api.get("/seasons/{season_id}")
    async def get_season(season_id: str) -> dict[str, object]:
        document = sink.document("season_result", season_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Season not found.")
        return document[1]

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
    try:
        cursor = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        cursor = 0
    while True:
        if await request.is_disconnected():
            return
        stored_events = sink.load(game_id)
        for event in stored_events:
            if event.id <= cursor:
                continue
            cursor = event.id
            yield _sse(event)
        if _has_game_over(stored_events):
            return
        yield ": keepalive\n\n"
        await asyncio.sleep(0.5)


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


def _default_season_payload() -> Mapping[str, object]:
    payload = dict(_default_cast_payload())
    payload["season_id"] = f"season-{uuid.uuid4().hex[:12]}"
    payload["games"] = 12
    return payload


app = create_app()
