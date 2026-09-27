"""Durable tick orchestration with fenced leases and bounded model requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx

from badactors.persistence import ConflictError, SQLiteEventStore
from badactors.providers.base import ModelProvider
from badactors.providers.openrouter import OpenRouterProvider
from badactors.scoping import build_world_context
from badactors.simulation.agents import demo_turn, model_turn
from badactors.simulation.engine import advance_world, create_world
from badactors.simulation.migrations import decode_world
from badactors.simulation.models import (
    LifeTurn,
    Memory,
    WorldCreate,
    WorldEvent,
    WorldSettings,
    WorldState,
)


class BudgetExhausted(RuntimeError):
    """The persisted model request allowance has been exhausted."""


class WorldRuntime:
    def __init__(self, store: SQLiteEventStore, provider: ModelProvider | None = None):
        self.store = store
        self.provider = provider

    def create(self, config: WorldCreate) -> WorldState:
        state = create_world(config)
        self.store.save_document("world", state.id, state.model_dump(mode="json"))
        return state

    def load(self, world_id: str) -> tuple[int, WorldState]:
        document = self.store.document("world", world_id)
        if document is None:
            raise KeyError(world_id)
        return document[0], decode_world(document[1])

    def list_worlds(self) -> list[WorldState]:
        return [decode_world(body) for _, _, body in self.store.documents("world")]

    def control(self, world_id: str, *, running: bool, ticks: int = 24) -> WorldState:
        if not 1 <= ticks <= 1000:
            raise ValueError("Run length must be between 1 and 1000 ticks.")
        revision, state = self.load(world_id)
        if running and state.active_arena_id:
            raise ConflictError(
                "Resume or cancel the active arena before running life."
            )
        state.status = "running" if running else "paused"
        state.run_until_tick = state.tick + ticks if running else state.tick
        state.next_tick_at = 0.0
        state.error = None
        state.consecutive_failures = 0
        self.store.save_document(
            "world", world_id, state.model_dump(mode="json"), expected_revision=revision
        )
        return state

    async def configure(self, world_id: str, settings: WorldSettings) -> WorldState:
        async with self.lease(world_id) as owner:
            revision, state = self.load(world_id)
            if state.status == "running":
                raise ConflictError("Pause the world before changing its settings.")
            used = int(str(self.usage(world_id)["reserved_requests"]))
            if settings.max_model_requests < used:
                raise ValueError(
                    "The lifetime allowance cannot be below usage already reserved."
                )
            state.max_model_requests = settings.max_model_requests
            if settings.max_tokens is not None:
                if state.active_arena_id and settings.max_tokens != state.max_tokens:
                    raise ConflictError(
                        "Finish or cancel the arena before changing output tokens."
                    )
                state.max_tokens = settings.max_tokens
            state.tick_seconds = settings.tick_seconds
            state.error = None
            state.consecutive_failures = 0
            state.status = "paused"
            self.commit(state, [], revision, owner)
            return state

    def memories(
        self, world_id: str, resident_id: str, after: int = 0, limit: int = 100
    ) -> list[Memory]:
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT body FROM world_memories WHERE world_id = ? "
                "AND resident_id = ? AND sequence > ? ORDER BY sequence LIMIT ?",
                (world_id, resident_id, after, min(500, max(1, limit))),
            ).fetchall()
        return [Memory.model_validate_json(row[0]) for row in rows]

    def journal(
        self, world_id: str, after: int = 0, limit: int = 100
    ) -> list[WorldEvent]:
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT body FROM world_events WHERE world_id = ? AND sequence > ? "
                "ORDER BY sequence LIMIT ?",
                (world_id, after, min(500, max(1, limit))),
            ).fetchall()
        return [WorldEvent.model_validate_json(row[0]) for row in rows]

    def usage(self, world_id: str) -> dict[str, object]:
        doc = self.store.document("world_usage", world_id)
        return doc[1] if doc else {"reserved_requests": 0, "reserved_output_tokens": 0}

    async def tick(self, world_id: str, *, manual: bool = False) -> WorldState:
        async with self.lease(world_id) as owner:
            revision, state = self.load(world_id)
            if state.active_arena_id:
                raise ConflictError(
                    "Resume or cancel the active arena before advancing life."
                )
            if not manual and (
                state.status != "running" or state.next_tick_at > time.time()
            ):
                return state
            if manual and state.status == "running":
                raise ConflictError("Pause the world before advancing it manually.")
            try:
                turns = await self._decisions(state, revision, owner)
                updated, events = advance_world(state, turns)
                updated.next_tick_at = time.time() + state.tick_seconds
                updated.error = None
                updated.consecutive_failures = 0
                if manual or updated.tick >= updated.run_until_tick:
                    updated.status = "paused"
                self.commit(updated, events, revision, owner)
                return updated
            except (ConflictError, asyncio.CancelledError):
                raise
            except Exception as exc:
                state.consecutive_failures += 1
                retry = (
                    not manual and _retryable(exc) and state.consecutive_failures < 3
                )
                state.status = "running" if retry else "error"
                if retry:
                    state.next_tick_at = time.time() + 2**state.consecutive_failures
                state.error = (
                    (
                        "Model request allowance exhausted. "
                        "Raise this world's lifetime allowance in settings to continue."
                    )
                    if isinstance(exc, BudgetExhausted)
                    else "Provider unavailable; a bounded retry is scheduled."
                    if retry
                    else (
                        f"Tick paused after {type(exc).__name__}. "
                        "Check provider configuration and resume."
                    )
                )
                try:
                    self.commit(state, [], revision, owner)
                except ConflictError:
                    pass
                raise

    async def _decisions(
        self, state: WorldState, revision: int, owner: str
    ) -> dict[str, LifeTurn]:
        turns: dict[str, LifeTurn] = {}
        for resident in sorted(state.residents.values(), key=lambda item: item.id):
            current_revision, _ = self.load(state.id)
            if current_revision != revision:
                raise ConflictError("World control changed while deciding.")
            context = build_world_context(state, resident.id)
            fingerprint = hashlib.sha256(
                json.dumps(context.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest()
            key = f"{state.id}:{state.tick}:{resident.id}"
            saved = self.store.document("world_turn", key)
            if saved and saved[1].get("context_hash") == fingerprint:
                turns[resident.id] = LifeTurn.model_validate(saved[1]["turn"])
                continue
            if state.provider == "demo":
                turn = demo_turn(context, state.seed)
            else:
                # OpenRouter performs at most two HTTP attempts per completion.
                # Reserve both before network IO, including lost-response retries.
                self.reserve_requests(state, owner, expected_revision=revision)
                provider = self.provider or OpenRouterProvider(timeout=45.0)
                turn = await asyncio.wait_for(
                    model_turn(context, provider, max_tokens=state.max_tokens),
                    timeout=100,
                )
            with self.store.transaction() as connection:
                self.store.require_lease(connection, f"world:{state.id}", owner)
                self.store.put_document(
                    connection,
                    "world_turn",
                    key,
                    {
                        "turn": turn.model_dump(mode="json"),
                        "tick": state.tick,
                        "provider": state.provider,
                        "model": resident.model,
                        "context_hash": fingerprint,
                    },
                    saved[0] if saved else 0,
                )
            turns[resident.id] = turn
        return turns

    def reserve_requests(
        self, state: WorldState, owner: str, *, expected_revision: int | None = None
    ) -> None:
        with self.store.transaction() as connection:
            self.store.require_lease(connection, f"world:{state.id}", owner)
            current = self.store.document("world", state.id)
            if expected_revision is not None and (
                current is None or current[0] != expected_revision
            ):
                raise ConflictError("World control changed before the model request.")
            usage = self.store.document("world_usage", state.id)
            revision, body = (
                usage
                if usage
                else (0, {"reserved_requests": 0, "reserved_output_tokens": 0})
            )
            requests = int(str(body["reserved_requests"])) + 2
            output_tokens = (
                int(str(body["reserved_output_tokens"])) + 2 * state.max_tokens
            )
            if requests > state.max_model_requests:
                raise BudgetExhausted()
            self.store.put_document(
                connection,
                "world_usage",
                state.id,
                {
                    "reserved_requests": requests,
                    "reserved_output_tokens": output_tokens,
                },
                revision,
            )

    def commit(
        self, state: WorldState, events: list[WorldEvent], revision: int, owner: str
    ) -> None:
        WorldState.model_validate(state.model_dump(mode="json"))
        with self.store.transaction() as connection:
            self.store.require_lease(connection, f"world:{state.id}", owner)
            previous = self.store.document("world", state.id)
            prior_state = decode_world(previous[1]) if previous else None
            self.store.put_document(
                connection, "world", state.id, state.model_dump(mode="json"), revision
            )
            for event in events:
                connection.execute(
                    "INSERT INTO world_events(world_id, sequence, tick, body) "
                    "VALUES (?, ?, ?, ?)",
                    (state.id, event.sequence, event.tick, event.model_dump_json()),
                )
            for resident in state.residents.values():
                prior = (
                    prior_state.residents[resident.id].memories
                    if prior_state and resident.id in prior_state.residents
                    else []
                )
                memories = {
                    memory.sequence: memory for memory in [*prior, *resident.memories]
                }
                for memory in memories.values():
                    if memory.sequence:
                        connection.execute(
                            "INSERT OR IGNORE INTO world_memories "
                            "(world_id, resident_id, sequence, tick, body) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                state.id,
                                resident.id,
                                memory.sequence,
                                memory.tick,
                                memory.model_dump_json(),
                            ),
                        )
            connection.execute(
                "DELETE FROM documents WHERE namespace = 'world_turn' "
                "AND substr(key, 1, ?) = ? AND json_extract(body, '$.tick') < ?",
                (len(state.id) + 1, f"{state.id}:", state.tick),
            )

    @asynccontextmanager
    async def lease(self, world_id: str) -> AsyncIterator[str]:
        owner = uuid.uuid4().hex
        resource = f"world:{world_id}"
        self.store.claim(resource, owner)
        heartbeat = asyncio.create_task(self._heartbeat(resource, owner))
        try:
            yield owner
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, ConflictError):
                await heartbeat
            self.store.release(resource, owner)

    async def _heartbeat(self, resource: str, owner: str) -> None:
        while True:
            await asyncio.sleep(20)
            # A lost lease is never silently reacquired by a stale worker.
            with self.store.transaction() as connection:
                self.store.require_lease(connection, resource, owner)
                connection.execute(
                    "UPDATE leases SET expires = ? WHERE resource = ? AND owner = ?",
                    (time.time() + 120, resource, owner),
                )

    async def serve(self, *, concurrency: int = 4) -> None:
        if not 1 <= concurrency <= 24:
            raise ValueError("Worker concurrency must be between 1 and 24 worlds.")
        active: dict[str, asyncio.Task[WorldState]] = {}
        try:
            while True:
                for world_id, task in list(active.items()):
                    if task.done():
                        with suppress(Exception):
                            task.result()
                        del active[world_id]
                for state in self.list_worlds():
                    if len(active) >= concurrency:
                        break
                    if (
                        state.id not in active
                        and state.status == "running"
                        and not state.active_arena_id
                        and state.next_tick_at <= time.time()
                    ):
                        active[state.id] = asyncio.create_task(self.tick(state.id))
                await asyncio.sleep(0.1)
        finally:
            for task in active.values():
                task.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)


def public_world(state: WorldState) -> dict[str, object]:
    """Spectator projection; private diaries are a distinct opt-in endpoint."""
    payload = state.model_dump(mode="json")
    payload["residents"] = [
        resident.model_dump(
            mode="json", exclude={"memories", "relationships", "experiences"}
        )
        for resident in state.residents.values()
    ]
    return payload


def export_world(runtime: WorldRuntime, world_id: str) -> str:
    """Owner export includes private memories; never supplied to agent prompts."""
    _, state = runtime.load(world_id)
    return json.dumps(
        {"world": state.model_dump(mode="json"), "usage": runtime.usage(world_id)},
        indent=2,
    )


def _retryable(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, httpx.TransportError)):
        return True
    return isinstance(error, httpx.HTTPStatusError) and (
        error.response.status_code == 429 or error.response.status_code >= 500
    )
