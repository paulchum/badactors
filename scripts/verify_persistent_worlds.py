"""Executable acceptance scenarios without changing the repository's test suite."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from badactors.persistence import ConflictError, SQLiteEventStore
from badactors.providers.base import Budget, Message, ProviderResult
from badactors.providers.openrouter import OpenRouterProvider
from badactors.runner import default_mock_provider, load_cast, run_game_record
from badactors.scoping import build_world_context
from badactors.server.app import EventSink, create_app
from badactors.simulation.agents import demo_turn
from badactors.simulation.arena import run_arena
from badactors.simulation.engine import advance_world, create_world
from badactors.simulation.models import (
    LifeAction,
    LifeTurn,
    Memory,
    WorldCreate,
    WorldEvent,
)
from badactors.simulation.report import world_report
from badactors.simulation.runtime import BudgetExhausted, WorldRuntime


class CountingProvider:
    def __init__(self, fail_at: int = -1) -> None:
        self.calls = 0
        self.fail_at = fail_at

    async def complete(
        self,
        messages: Sequence[Message],
        schema: type[BaseModel],
        model: str,
        budget: Budget,
    ) -> ProviderResult:
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("injected network failure")
        return ProviderResult(
            LifeTurn(
                action=LifeAction(kind="rest"),
                reflection="Only my own private thought.",
            ),
            None,
            {},
        )


def pure_rules_and_scope() -> None:
    state = create_world(WorldCreate(id="pure"))
    state.residents["sol"].memories.append(Memory(tick=0, text="SECRET_SOL"))
    state.residents["sol"].goal = "SECRET_GOAL"
    state.residents["sol"].relationships["mara"] = 73
    state.residents["sol"].location = "garden"
    before = state.model_dump_json()
    context = build_world_context(state, "mara")
    assert "SECRET" not in context.model_dump_json()
    assert "sol" not in {item["id"] for item in context.nearby}
    context.you.memories.append(Memory(tick=0, text="MUTATION"))
    context.place.food = 999
    assert state.model_dump_json() == before
    turns = {
        key: demo_turn(build_world_context(state, key), state.seed)
        for key in state.residents
    }
    first, events = advance_world(state, turns)
    second, second_events = advance_world(state, turns)
    assert first == second and events == second_events
    assert state.model_dump_json() == before
    bad_turns = {
        key: LifeTurn(action=LifeAction(kind="move", destination="garden"))
        for key in state.residents
    }
    state.residents["mara"].location = "workshop"
    blocked, _ = advance_world(state, bad_turns)
    assert blocked.residents["mara"].location == "workshop"
    try:
        WorldCreate.model_validate({"id": "bad", "unknown": "injected"})
        raise AssertionError("Unknown config accepted")
    except ValidationError:
        pass
    specs = WorldCreate(
        id="duplicate", residents=[first.residents["mara"].model_copy()] * 3
    )
    try:
        create_world(specs)
        raise AssertionError("Duplicate resident IDs accepted")
    except ValueError:
        pass
    print(
        "PASS pure deterministic transitions, physical constraints, strict "
        "input, local/private scoping"
    )


async def durability(directory: Path) -> None:
    path = directory / "durable.sqlite3"
    store = SQLiteEventStore(path)
    runtime = WorldRuntime(store)
    runtime.create(WorldCreate(id="durable"))
    for _ in range(40):
        await runtime.tick("durable", manual=True)
    revision, state = runtime.load("durable")
    assert state.tick == 40 and state.event_sequence == 240
    assert any(resident.coins > 0 for resident in state.residents.values())
    assert all(len(resident.memories) <= 40 for resident in state.residents.values())
    saved = state.model_dump_json()
    store.close()
    reopened = SQLiteEventStore(path)
    runtime = WorldRuntime(reopened)
    assert runtime.load("durable")[1].model_dump_json() == saved
    assert len(runtime.journal("durable", limit=500)) == 240
    report = world_report(runtime, "durable")
    assert report["resident_action_attempts"] == 240
    assert report["event_count"] == 240
    async with runtime.lease("durable") as owner:
        other = SQLiteEventStore(path)
        try:
            other.claim("world:durable", "other-worker")
            raise AssertionError("Concurrent worker acquired lease")
        except ConflictError:
            pass
        changed = state.model_copy(deep=True)
        changed.tick += 1
        duplicate = WorldEvent(
            sequence=1,
            tick=41,
            type="rest",
            actor_id="mara",
            location="square",
            text="rollback sentinel",
            witnesses=["mara"],
        )
        try:
            runtime.commit(changed, [duplicate], revision, owner)
            raise AssertionError("Duplicate event accepted")
        except Exception as exc:
            assert "UNIQUE" in str(exc)
        assert runtime.load("durable")[1].model_dump_json() == saved
        try:
            runtime.commit(changed, [], revision - 1, owner)
            raise AssertionError("Stale revision accepted")
        except ConflictError:
            pass
        other.close()
    reopened.backup(directory / "backup.sqlite3")
    backup = SQLiteEventStore(directory / "backup.sqlite3")
    assert backup.document("world", "durable") == reopened.document("world", "durable")
    backup.close()
    await runtime.tick("durable", manual=True)
    assert runtime.load("durable")[1].tick == 41
    reopened.close()
    print(
        "PASS restart continuity, bounded memory, fenced concurrent writers, "
        "atomic rollback, stale writes, backup"
    )


async def recovery_and_limits(directory: Path) -> None:
    store = SQLiteEventStore(directory / "recovery.sqlite3")
    provider = CountingProvider(fail_at=2)
    runtime = WorldRuntime(store, provider)
    runtime.create(WorldCreate(id="recovery", provider="openrouter"))
    try:
        await runtime.tick("recovery", manual=True)
        raise AssertionError("Injected failure did not propagate")
    except RuntimeError:
        pass
    assert runtime.load("recovery")[1].tick == 0
    assert runtime.load("recovery")[1].status == "error"
    store.close()
    store = SQLiteEventStore(directory / "recovery.sqlite3")
    runtime = WorldRuntime(store, provider)
    await runtime.tick("recovery", manual=True)
    assert provider.calls == 7  # One cached success, one failed call, five remaining.
    assert runtime.load("recovery")[1].tick == 1
    assert runtime.usage("recovery")["reserved_requests"] == 14
    runtime.create(
        WorldCreate(id="capped", provider="openrouter", max_model_requests=1)
    )
    before = provider.calls
    try:
        await runtime.tick("capped", manual=True)
        raise AssertionError("Exceeded persisted request cap")
    except BudgetExhausted:
        pass
    assert provider.calls == before
    assert runtime.load("capped")[1].tick == 0
    store.close()
    print(
        "PASS interrupted model recovery reuses completed decisions and "
        "enforces pre-call durable budgets"
    )


async def real_adapter_transport(directory: Path) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-key"
        assert body["provider"]["require_parameters"] is True
        schema = body["response_format"]["json_schema"]["schema"]
        assert set(schema["required"]) == {"action", "speech", "reflection"}
        assert set(schema["$defs"]["LifeAction"]["required"]) == {
            "kind",
            "destination",
            "target_id",
        }
        context = json.loads(body["messages"][-1]["content"])
        viewer = context["you"]["id"]
        for resident_id in ("ash", "iris", "june", "knox", "mara", "sol"):
            if resident_id != viewer:
                assert f"PRIVATE_{resident_id}" not in request.content.decode()
        requests.append(body)
        content = (
            "malformed"
            if len(requests) == 1
            else json.dumps(
                {
                    "action": {"kind": "rest", "destination": None, "target_id": None},
                    "speech": "",
                    "reflection": "My private reflection",
                }
            )
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    # The first malformed retry has a corrective message after the user context.
    def transport_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if len(body["messages"]) > 2:
            requests.append(body)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "action": {
                                            "kind": "rest",
                                            "destination": None,
                                            "target_id": None,
                                        },
                                        "speech": "",
                                        "reflection": "My private reflection",
                                    }
                                )
                            }
                        }
                    ]
                },
            )
        return handler(request)

    store = SQLiteEventStore(directory / "adapter.sqlite3")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport_handler)
    ) as client:
        provider = OpenRouterProvider(api_key="test-key", client=client)
        runtime = WorldRuntime(store, provider)
        runtime.create(WorldCreate(id="adapter", provider="openrouter"))
        revision, state = runtime.load("adapter")
        for resident in state.residents.values():
            resident.memories.append(Memory(tick=0, text=f"PRIVATE_{resident.id}"))
        store.save_document(
            "world", state.id, state.model_dump(mode="json"), expected_revision=revision
        )
        await runtime.tick("adapter", manual=True)
        assert len(requests) == 7
        assert runtime.usage("adapter")["reserved_requests"] == 12
    store.close()
    print(
        "PASS OpenRouter HTTP contract, strict schemas, malformed retry, "
        "prompt isolation"
    )


async def game_and_arena(directory: Path) -> None:
    cast = load_cast("configs/cast.example.yaml")
    baseline = await run_game_record(cast, default_mock_provider())
    store = SQLiteEventStore(directory / "games.sqlite3")
    try:
        await run_game_record(
            cast, default_mock_provider(), event_store=store, max_steps=2
        )
        raise AssertionError("Short run unexpectedly completed")
    except RuntimeError:
        pass
    assert store.document("game", cast.game_id) is not None
    prefix = store.load(cast.game_id)
    store.close()
    store = SQLiteEventStore(directory / "games.sqlite3")
    resumed = await run_game_record(
        cast, default_mock_provider(), event_store=store, resume=True
    )
    assert resumed == baseline
    assert list(resumed.events[: len(prefix)]) == prefix
    replay = await run_game_record(
        cast, default_mock_provider(), event_store=store, resume=True
    )
    assert replay == resumed
    runtime = WorldRuntime(store)
    runtime.create(WorldCreate(id="arena"))
    await runtime.tick("arena", manual=True)
    result = await run_arena(runtime, "arena")
    _, world = runtime.load("arena")
    assert result["game_id"] in world.arena_games
    assert all(
        any(memory.kind == "arena" for memory in resident.memories)
        for resident in world.residents.values()
    )
    assert any(resident.coins >= 5 for resident in world.residents.values())
    snapshot = store.document("game", str(result["game_id"]))
    assert snapshot and snapshot[1]["state"]
    store.close()
    print(
        "PASS full-game restart equality, idempotent resume, arena integration "
        "and resident outcome continuity"
    )


def api_restart(directory: Path) -> None:
    path = directory / "api.sqlite3"
    sink = EventSink(path)
    with patch.dict(os.environ, {"BADACTORS_WORKER": "0", "BADACTORS_API_TOKEN": ""}):
        with TestClient(create_app(sink)) as client:
            assert client.post("/worlds", json={"id": "api"}).status_code == 201
            assert client.post("/worlds", json={"id": "api"}).status_code == 409
            assert (
                client.post("/worlds/api/control", json={"action": "step"}).status_code
                == 200
            )
            assert (
                client.post("/worlds", json={"id": "oops", "unknown": 42}).status_code
                == 422
            )
            assert (
                client.post(
                    "/worlds",
                    json={"id": "hostile"},
                    headers={"Origin": "https://hostile.example"},
                ).status_code
                == 403
            )
            assert client.get("/worlds/template").status_code == 200
            with patch(
                "badactors.server.worlds.run_arena",
                side_effect=ExceptionGroup("actors", [BudgetExhausted("limit")]),
            ):
                assert client.post("/worlds/api/arena").status_code == 429
            assert client.get("/worlds/api/residents/mara").json()["resident"][
                "memories"
            ]
            assert "memories" not in client.get("/worlds/api").json()["residents"][0]
            season = client.post(
                "/seasons",
                json={
                    "season_id": "durable-season",
                    "games": 2,
                    "game_id": "seed",
                    "rng_seed": 13,
                    "traitor_count": 1,
                    "memory_budget": 100,
                    "token_budget": 200,
                    "agents": {f"mock-{n}": {"id": f"p{n}"} for n in range(6)},
                },
            )
            assert season.status_code == 200, season.text
            other = SQLiteEventStore(path)
            asyncio.run(
                run_game_record(
                    load_cast("configs/cast.example.yaml"),
                    default_mock_provider(),
                    event_store=other,
                )
            )
            other.close()
            leaderboard = client.get("/leaderboard").json()
        sink.close()
        sink = EventSink(path)
        with TestClient(create_app(sink)) as client:
            assert client.get("/worlds/api").json()["tick"] == 1
            assert client.get("/seasons/durable-season").json() == season.json()
            assert client.get("/leaderboard").json() == leaderboard
            assert (
                client.get("/worlds/api/events?after=2&limit=2").json()["next_cursor"]
                == 4
            )
            with patch.dict(os.environ, {"BADACTORS_API_TOKEN": "acceptance-secret"}):
                assert (
                    client.post(
                        "/worlds/api/control", json={"action": "step"}
                    ).status_code
                    == 401
                )
                assert client.get("/worlds/api/export").status_code == 401
                assert (
                    client.get(
                        "/worlds/api",
                        headers={"Authorization": "Bearer acceptance-secret"},
                    ).status_code
                    == 200
                )
        sink.close()
    print(
        "PASS API validation, owner auth, origin checks, cursor replay, "
        "season/leaderboard persistence"
    )


async def worker_process_restart(directory: Path) -> None:
    path = directory / "worker.sqlite3"
    store = SQLiteEventStore(path)
    runtime = WorldRuntime(store)
    runtime.create(WorldCreate(id="worker", tick_seconds=1.0))
    runtime.control("worker", running=True, ticks=4)
    command = [sys.executable, "-m", "badactors.simulation.worker", "--db", str(path)]
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    try:
        for _ in range(100):
            if runtime.load("worker")[1].tick >= 1:
                break
            await asyncio.sleep(0.1)
        assert runtime.load("worker")[1].tick >= 1
        process.terminate()
        process.wait(timeout=5)
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        for _ in range(100):
            if runtime.load("worker")[1].status == "paused":
                break
            await asyncio.sleep(0.1)
        state = runtime.load("worker")[1]
        assert state.tick == 4 and state.status == "paused", state
        events = runtime.journal("worker", limit=500)
        assert len(events) == 24 and len({event.sequence for event in events}) == 24
    finally:
        process.terminate()
        process.wait(timeout=5)
        store.close()
    print(
        "PASS separate worker process restart resumes scheduled work and stops "
        "at its persisted tick limit"
    )


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="badactors-acceptance-") as directory:
        path = Path(directory)
        pure_rules_and_scope()
        await durability(path)
        await recovery_and_limits(path)
        await real_adapter_transport(path)
        await game_and_arena(path)
        # TestClient owns its own event loop; run it outside the simulation loop.
        await asyncio.to_thread(api_restart, path)
        await worker_process_restart(path)
    print("All persistent-world acceptance scenarios passed.")


if __name__ == "__main__":
    asyncio.run(main())
