"""Independent lifecycle acceptance: society, archives, migration and recovery."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from badactors.persistence import ConflictError, SQLiteEventStore
from badactors.providers.base import Budget, Message, ProviderResult
from badactors.runner import GameRunResult, run_game_record
from badactors.scoping import build_world_context
from badactors.server.app import EventSink, create_app
from badactors.simulation.agents import demo_turn
from badactors.simulation.arena import cancel_arena, run_arena
from badactors.simulation.engine import advance_world, create_world, default_residents
from badactors.simulation.migrations import decode_world
from badactors.simulation.models import (
    LifeAction,
    LifeTurn,
    WorldCreate,
    WorldSettings,
    WorldState,
)
from badactors.simulation.progression import update_objectives
from badactors.simulation.runtime import BudgetExhausted, WorldRuntime


class CharacterProvider:
    def __init__(self, fail_at: int = -1) -> None:
        self.calls = 0
        self.fail_at = fail_at
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def complete(
        self,
        messages: Sequence[Message],
        schema: type[BaseModel],
        model: str,
        budget: Budget,
    ) -> ProviderResult:
        self.calls += 1
        self.started.set()
        if self.gate:
            await self.gate.wait()
        if self.calls == self.fail_at:
            raise RuntimeError("injected provider interruption")
        you = json.loads(messages[-1].content)["you"]["id"]
        return ProviderResult(
            LifeTurn(
                action=LifeAction(kind="rest"), reflection=f"PRIVATE_{you}_{self.calls}"
            ),
            None,
            {},
        )


class TransientProvider(CharacterProvider):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures_left = failures

    async def complete(
        self,
        messages: Sequence[Message],
        schema: type[BaseModel],
        model: str,
        budget: Budget,
    ) -> ProviderResult:
        if self.failures_left:
            self.failures_left -= 1
            self.calls += 1
            raise httpx.ConnectError("injected transient connection failure")
        return await super().complete(messages, schema, model, budget)


def rest_turns(state: WorldState) -> dict[str, LifeTurn]:
    return {key: LifeTurn(action=LifeAction(kind="rest")) for key in state.residents}


def resources(state: WorldState) -> tuple[int, int]:
    return (
        sum(r.food for r in state.residents.values())
        + sum(p.food for p in state.places.values()),
        sum(r.coins for r in state.residents.values())
        + sum(p.coins for p in state.places.values()),
    )


def economy_and_progress() -> None:
    state = create_world(WorldCreate(id="economy"))
    actor = state.residents["mara"]
    actor.location = "hearth"
    actor.coins = 30
    update_objectives(actor, state.tick)
    before = resources(state)
    turns = rest_turns(state)
    turns["mara"] = LifeTurn(action=LifeAction(kind="buy"))
    after, events = advance_world(state, turns)
    assert resources(after) == before
    assert after.residents["mara"].food == 3 and after.residents["mara"].coins == 28
    assert next(e for e in events if e.actor_id == "mara").type == "buy"
    assert after.residents["mara"].objectives[0].completed_tick == 0
    assert after.residents["mara"].objectives[0].progress == 30
    turns = rest_turns(after)
    turns["mara"] = LifeTurn(action=LifeAction(kind="sell"))
    sold, _ = advance_world(after, turns)
    assert resources(sold) == before
    assert sold.residents["mara"].coins == 29
    sold.residents["mara"].food = 12
    turns["mara"] = LifeTurn(action=LifeAction(kind="buy"))
    full, blocked = advance_world(sold, turns)
    assert full.residents["mara"].food == 12
    assert next(e for e in blocked if e.actor_id == "mara").type == "blocked"

    state = create_world(WorldCreate(id="project"))
    state.residents["mara"].location = "garden"
    state.residents["mara"].coins = 24
    while state.projects["garden-irrigation"].level == 0:
        assert state.tick < 100, "Irrigation never completed"
        turns = rest_turns(state)
        if state.residents["mara"].energy >= 28:
            turns["mara"] = LifeTurn(action=LifeAction(kind="build"))
        state, _ = advance_world(state, turns)
    project = state.projects["garden-irrigation"]
    assert project.level == 1 and project.contributors == {"mara": 12}
    assert state.places["garden"].capacity == 20 and state.places["garden"].coins == 24
    assert state.residents["mara"].coins == 0
    assert not build_world_context(state, "sol").local_projects
    assert build_world_context(state, "mara").local_projects[0].level == 1
    print(
        "PASS market conservation, carrying limits, sticky milestones "
        "and lasting local construction"
    )


def long_horizon() -> None:
    for seed in [13, 29, 101]:
        state = create_world(WorldCreate(id=f"long-{seed}", seed=seed))
        counts: Counter[str] = Counter()
        for _ in range(1000):
            state, events = advance_world(
                state,
                {
                    key: demo_turn(build_world_context(state, key), state.seed)
                    for key in state.residents
                },
            )
            WorldState.model_validate(state.model_dump(mode="json"))
            counts.update(event.type for event in events)
        assert state.tick == 1000 and sum(counts.values()) == 6000
        assert counts["buy"] and counts["sell"] and counts["build"]
        assert all(
            r.food <= 12 and len(r.memories) <= 40 for r in state.residents.values()
        )
        assert all(
            goal.completed_tick is not None
            for r in state.residents.values()
            for goal in r.objectives
        )
        assert all(r.memory_sequence > 40 for r in state.residents.values())
        assert state.projects["garden-irrigation"].level >= 1
        serialized = WorldState.model_validate_json(
            json.dumps(state.model_dump(mode="json"), sort_keys=True)
        )
        assert all(
            demo_turn(build_world_context(state, key), seed)
            == demo_turn(build_world_context(serialized, key), seed)
            for key in state.residents
        ), "Serialization order changed a resident's decision"
    print(
        "PASS 18,000 autonomous demo actions across three seeds: "
        "bounded needs, memories and achieved goals"
    )


def migration() -> None:
    state = create_world(WorldCreate(id="legacy"))
    old = state.model_dump(mode="json")
    old["schema_version"] = 1
    for field in ["arena_runs", "active_arena_id", "projects", "progress_since_tick"]:
        old.pop(field)
    for resident in old["residents"].values():
        for field in ["objectives", "stats", "memory_sequence", "experiences"]:
            resident.pop(field)
    for place in old["places"].values():
        place.pop("coins")
    old["places"]["hearth"].update(food=0, capacity=0)
    migrated = decode_world(old)
    assert migrated.schema_version == 2 and migrated.places["hearth"].food == 8
    assert migrated.residents["mara"].coins == state.residents["mara"].coins
    assert migrated.projects["garden-irrigation"].level == 0
    assert old["schema_version"] == 1  # Migration leaves its input untouched.
    for invalid in [3, True, "1"]:
        try:
            decode_world({**old, "schema_version": invalid})
            raise AssertionError("Invalid schema version accepted")
        except ValueError:
            pass
    broken = migrated.model_dump(mode="json")
    broken["places"]["square"]["neighbors"] = []
    try:
        decode_world(broken)
        raise AssertionError("Broken graph accepted")
    except ValidationError:
        pass
    print(
        "PASS explicit v1 migration, preserved state, unsupported versions "
        "and malformed graph rejection"
    )


async def archives_and_cache(directory: Path) -> None:
    path = directory / "history.sqlite3"
    store = SQLiteEventStore(path)
    provider = CharacterProvider()
    runtime = WorldRuntime(store, provider)
    runtime.create(
        WorldCreate(id="history", provider="openrouter", max_model_requests=2000)
    )
    for _ in range(80):
        await runtime.tick("history", manual=True)
    store.close()
    store = SQLiteEventStore(path)
    runtime = WorldRuntime(store, provider)
    for resident in runtime.load("history")[1].residents.values():
        archive = []
        cursor = 0
        while batch := runtime.memories("history", resident.id, cursor, 100):
            archive.extend(batch)
            cursor = batch[-1].sequence
        assert len(archive) == resident.memory_sequence > len(resident.memories)
        assert [m.sequence for m in archive] == list(
            range(1, resident.memory_sequence + 1)
        )
        private = [m.text for m in archive if m.kind == "reflection"]
        assert len(private) == 80 and all(
            f"PRIVATE_{resident.id}_" in text for text in private
        )
    assert not store.documents("world_turn"), "Committed decision cache was not pruned"

    defaults = default_residents()
    residents = []
    for index in range(24):
        resident = defaults[index % len(defaults)].model_copy(deep=True)
        resident.id = f"resident-{index:02}"
        resident.name = f"Resident {index}"
        resident.model = "test-model"
        residents.append(resident)
    runtime.create(WorldCreate(id="dense", provider="openrouter", residents=residents))
    for _ in range(10):
        await runtime.tick("dense", manual=True)
    for resident in runtime.load("dense")[1].residents.values():
        archive = runtime.memories("dense", resident.id, limit=500)
        assert len(archive) == resident.memory_sequence == 250
        assert len(resident.memories) == 40

    stale = CharacterProvider(fail_at=2)
    runtime = WorldRuntime(store, stale)
    runtime.create(WorldCreate(id="changed", provider="openrouter"))
    try:
        await runtime.tick("changed", manual=True)
    except RuntimeError:
        pass
    revision, state = runtime.load("changed")
    state.residents["ash"].goal = "A different private objective after interruption"
    store.save_document(
        "world", "changed", state.model_dump(mode="json"), expected_revision=revision
    )
    await runtime.tick("changed", manual=True)
    assert stale.calls == 8, "A cached decision was reused against changed perception"
    assert any(m.text == "PRIVATE_ash_3" for m in runtime.memories("changed", "ash"))
    store.close()
    print(
        "PASS durable private archives beyond prompt memory, cache pruning "
        "and stale-perception rejection"
    )


async def memory_transaction(directory: Path) -> None:
    store = SQLiteEventStore(directory / "atomic-history.sqlite3")
    provider = CharacterProvider()
    runtime = WorldRuntime(store, provider)
    runtime.create(WorldCreate(id="atomic", provider="openrouter"))
    with store.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER reject_memory BEFORE INSERT ON world_memories "
            "WHEN NEW.resident_id = 'mara' BEGIN "
            "SELECT RAISE(ABORT, 'injected archive failure'); END"
        )
    try:
        await runtime.tick("atomic", manual=True)
        raise AssertionError("Injected archive failure did not abort the tick")
    except sqlite3.IntegrityError:
        pass
    assert runtime.load("atomic")[1].tick == 0
    assert not runtime.journal("atomic")
    assert all(
        not runtime.memories("atomic", key)
        for key in runtime.load("atomic")[1].residents
    )
    assert provider.calls == 6
    with store.transaction() as connection:
        connection.execute("DROP TRIGGER reject_memory")
    await runtime.tick("atomic", manual=True)
    assert provider.calls == 6, "Retry should reuse all completed decisions"
    assert runtime.load("atomic")[1].tick == 1 and len(runtime.journal("atomic")) == 6
    store.close()
    print("PASS archive-write failure rolls back state, journal and memories together")


async def allowance_and_scheduling(directory: Path) -> None:
    store = SQLiteEventStore(directory / "budget.sqlite3")
    provider = CharacterProvider()
    runtime = WorldRuntime(store, provider)
    runtime.create(
        WorldCreate(id="allowance", provider="openrouter", max_model_requests=2)
    )
    try:
        await runtime.tick("allowance", manual=True)
        raise AssertionError("Allowance was bypassed")
    except BudgetExhausted:
        pass
    assert provider.calls == 1
    await runtime.configure(
        "allowance", WorldSettings(max_model_requests=12, tick_seconds=1.0)
    )
    await runtime.tick("allowance", manual=True)
    assert provider.calls == 6 and runtime.usage("allowance")["reserved_requests"] == 12
    try:
        await runtime.configure(
            "allowance", WorldSettings(max_model_requests=10, tick_seconds=1.0)
        )
        raise AssertionError("Usage was reset by lowering the allowance")
    except ValueError:
        pass

    blocked = CharacterProvider()
    blocked.gate = asyncio.Event()
    runtime = WorldRuntime(store, blocked)
    runtime.create(WorldCreate(id="a-slow", provider="openrouter"))
    runtime.create(WorldCreate(id="b-fast"))
    runtime.control("a-slow", running=True, ticks=1)
    runtime.control("b-fast", running=True, ticks=1)
    worker = asyncio.create_task(runtime.serve(concurrency=2))
    try:
        await asyncio.wait_for(blocked.started.wait(), timeout=2)
        for _ in range(40):
            if runtime.load("b-fast")[1].tick == 1:
                break
            await asyncio.sleep(0.05)
        assert runtime.load("b-fast")[1].tick == 1
        assert runtime.load("a-slow")[1].tick == 0
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    store.claim("world:a-slow", "after-shutdown")
    store.release("world:a-slow", "after-shutdown")
    store.close()
    print(
        "PASS same-world allowance extension, preserved reservations "
        "and independent-world scheduling"
    )


async def interrupted_game(*args: Any, **kwargs: Any) -> GameRunResult:
    return await run_game_record(*args, **kwargs, max_steps=2)


async def bounded_retry(directory: Path) -> None:
    store = SQLiteEventStore(directory / "retry.sqlite3")
    provider = TransientProvider(2)
    runtime = WorldRuntime(store, provider)
    runtime.create(WorldCreate(id="retry", provider="openrouter"))
    runtime.control("retry", running=True, ticks=1)
    for now, expected_next in [(1000.0, 1002.0), (1002.0, 1006.0)]:
        with patch("badactors.simulation.runtime.time.time", return_value=now):
            try:
                await runtime.tick("retry")
                raise AssertionError("Connection failure was not propagated")
            except httpx.ConnectError:
                pass
            state = runtime.load("retry")[1]
            assert state.status == "running" and state.next_tick_at == expected_next
    with patch("badactors.simulation.runtime.time.time", return_value=1005.0):
        await runtime.tick("retry")
    assert provider.calls == 2, "Retry ran before its saved deadline"
    with patch("badactors.simulation.runtime.time.time", return_value=1006.0):
        await runtime.tick("retry")
    state = runtime.load("retry")[1]
    assert (
        state.tick == 1 and state.status == "paused" and state.consecutive_failures == 0
    )
    assert provider.calls == 8 and runtime.usage("retry")["reserved_requests"] == 16

    failing = TransientProvider(10)
    runtime = WorldRuntime(store, failing)
    runtime.create(WorldCreate(id="stop", provider="openrouter"))
    runtime.control("stop", running=True, ticks=1)
    for now in [2000.0, 2002.0, 2006.0]:
        with patch("badactors.simulation.runtime.time.time", return_value=now):
            try:
                await runtime.tick("stop")
            except httpx.ConnectError:
                pass
    state = runtime.load("stop")[1]
    assert state.status == "error" and state.consecutive_failures == 3
    with patch("badactors.simulation.runtime.time.time", return_value=2010.0):
        await runtime.tick("stop")
    assert failing.calls == 3 and runtime.usage("stop")["reserved_requests"] == 6
    store.close()
    print(
        "PASS persisted retry deadlines, no early calls, recovery "
        "and three-failure stop"
    )


async def arena_lifecycle(directory: Path) -> None:
    path = directory / "arena.sqlite3"
    store = SQLiteEventStore(path)
    runtime = WorldRuntime(store)
    runtime.create(WorldCreate(id="resumable"))
    with patch(
        "badactors.simulation.arena.run_game_record", side_effect=interrupted_game
    ):
        try:
            await run_arena(runtime, "resumable")
            raise AssertionError("Injected interruption did not happen")
        except RuntimeError:
            pass
    state = runtime.load("resumable")[1]
    game_id = state.active_arena_id
    assert game_id and store.document("game", game_id)
    try:
        await runtime.tick("resumable", manual=True)
        raise AssertionError("Residents advanced during an unfinished challenge")
    except ConflictError:
        pass
    store.close()
    store = SQLiteEventStore(path)
    runtime = WorldRuntime(store)
    result = await run_arena(runtime, "resumable")
    assert result["game_id"] == game_id
    state = runtime.load("resumable")[1]
    assert state.active_arena_id is None and state.arena_games == [game_id]
    assert all(r.coins in {0, 5} for r in state.residents.values())
    assert all(r.stats.arena_wins == (r.coins // 5) for r in state.residents.values())

    runtime.create(WorldCreate(id="cancel"))
    with patch(
        "badactors.simulation.arena.run_game_record", side_effect=interrupted_game
    ):
        try:
            await run_arena(runtime, "cancel")
        except RuntimeError:
            pass
    cancelled_id = runtime.load("cancel")[1].active_arena_id
    cancelled = await cancel_arena(runtime, "cancel")
    assert not cancelled.arena_games and all(
        r.coins == 0 for r in cancelled.residents.values()
    )
    assert store.document("game", str(cancelled_id))
    await runtime.tick("cancel", manual=True)
    next_game = await run_arena(runtime, "cancel")
    assert next_game["game_id"] != cancelled_id and str(next_game["game_id"]).endswith(
        "0002"
    )
    store.close()
    print(
        "PASS active-arena freeze, process reopen/resume, single rewards "
        "and cancellation without ID reuse"
    )


def owner_api(directory: Path) -> None:
    sink = EventSink(directory / "api.sqlite3")
    with patch.dict(os.environ, {"BADACTORS_WORKER": "0", "BADACTORS_API_TOKEN": ""}):
        with TestClient(
            create_app(sink),
            base_url="http://outside.example",
            client=("127.0.0.1", 50000),
        ) as client:
            assert client.get("/worlds").status_code == 403
        with TestClient(
            create_app(sink), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
        ) as client:
            assert client.get("/worlds").status_code == 200
    with patch.dict(
        os.environ, {"BADACTORS_WORKER": "0", "BADACTORS_API_TOKEN": "owner"}
    ):
        with TestClient(create_app(sink)) as client:
            headers = {"Authorization": "Bearer owner"}
            assert (
                client.post(
                    "/worlds", json={"id": "owner"}, headers=headers
                ).status_code
                == 201
            )
            assert (
                client.put(
                    "/worlds/owner/settings",
                    json={"max_model_requests": 1200, "tick_seconds": 2.0},
                    headers=headers,
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/worlds/owner/control", json={"action": "step"}, headers=headers
                ).status_code
                == 200
            )
            route = "/worlds/owner/residents/mara/memories"
            assert client.get(route).status_code == 401
            history = client.get(route, headers=headers).json()
            assert history["memories"] and history["next_cursor"] >= 1
            assert (
                client.get(
                    "/worlds/owner/residents/unknown/memories", headers=headers
                ).status_code
                == 404
            )
            public = client.get("/worlds/owner", headers=headers).json()
            assert "experiences" not in public["residents"][0]
            assert (
                client.post("/worlds/owner/arena/cancel", headers=headers).status_code
                == 409
            )
    sink.close()
    print(
        "PASS owner-only memory archive, settings validation and arena cancellation API"
    )


async def main() -> None:
    economy_and_progress()
    long_horizon()
    migration()
    with tempfile.TemporaryDirectory(prefix="badactors-lifecycle-") as temporary:
        directory = Path(temporary)
        await archives_and_cache(directory)
        await memory_transaction(directory)
        await allowance_and_scheduling(directory)
        await bounded_retry(directory)
        await arena_lifecycle(directory)
        owner_api(directory)
    print("All world lifecycle acceptance scenarios passed.")


if __name__ == "__main__":
    asyncio.run(main())
