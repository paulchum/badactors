"""World API: bounded controls, local perception inspection and durable replay."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
from starlette.responses import Response

from badactors.persistence import ConflictError
from badactors.scoping import build_world_context
from badactors.simulation.arena import cancel_arena, run_arena
from badactors.simulation.engine import default_residents
from badactors.simulation.models import (
    WorldControl,
    WorldCreate,
    WorldSettings,
    WorldState,
)
from badactors.simulation.report import world_report
from badactors.simulation.runtime import (
    BudgetExhausted,
    WorldRuntime,
    export_world,
    public_world,
)


def world_routes(runtime: WorldRuntime) -> APIRouter:
    router = APIRouter(prefix="/worlds", tags=["persistent worlds"])

    def load(world_id: str) -> WorldState:
        try:
            return runtime.load(world_id)[1]
        except KeyError as exc:
            raise HTTPException(404, "World not found.") from exc

    @router.get("")
    async def list_worlds() -> dict[str, object]:
        return {"worlds": [public_world(state) for state in runtime.list_worlds()]}

    @router.get("/template")
    async def template() -> dict[str, object]:
        return {
            "residents": [item.model_dump(mode="json") for item in default_residents()],
            "models": [
                item.strip()
                for item in os.getenv("BADACTORS_MODELS", "").split(",")
                if item.strip()
            ],
        }

    @router.post("", status_code=201)
    async def create_world(payload: WorldCreate) -> dict[str, object]:
        if payload.provider == "openrouter":
            _validate_live_config(payload)
        try:
            return public_world(runtime.create(payload))
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/{world_id}")
    async def get_world(world_id: str) -> dict[str, object]:
        payload = public_world(load(world_id))
        payload["usage"] = runtime.usage(world_id)
        return payload

    @router.post("/{world_id}/control")
    async def control_world(world_id: str, payload: WorldControl) -> dict[str, object]:
        load(world_id)
        try:
            state = (
                await runtime.tick(world_id, manual=True)
                if payload.action == "step"
                else runtime.control(
                    world_id, running=payload.action == "run", ticks=payload.ticks
                )
            )
            return public_world(state)
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except BudgetExhausted as exc:
            raise HTTPException(
                429, "World model request allowance exhausted."
            ) from exc
        except Exception as exc:
            raise HTTPException(
                502, "Tick failed; inspect the world's error and resume."
            ) from exc

    @router.get("/{world_id}/events")
    async def get_events(
        world_id: str,
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, object]:
        load(world_id)
        events = runtime.journal(world_id, after, limit)
        return {
            "events": [event.model_dump(mode="json") for event in events],
            "next_cursor": events[-1].sequence if events else after,
        }

    @router.put("/{world_id}/settings")
    async def settings(world_id: str, payload: WorldSettings) -> dict[str, object]:
        load(world_id)
        return public_world(await runtime.configure(world_id, payload))

    @router.get("/{world_id}/residents/{resident_id}/memories")
    async def memories(
        world_id: str,
        resident_id: str,
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, object]:
        if resident_id not in load(world_id).residents:
            raise HTTPException(404, "Resident not found.")
        rows = runtime.memories(world_id, resident_id, after, limit)
        return {
            "memories": [memory.model_dump(mode="json") for memory in rows],
            "next_cursor": rows[-1].sequence if rows else after,
        }

    @router.get("/{world_id}/residents/{resident_id}")
    async def get_resident(world_id: str, resident_id: str) -> dict[str, object]:
        state = load(world_id)
        if resident_id not in state.residents:
            raise HTTPException(404, "Resident not found.")
        return {
            "resident": state.residents[resident_id].model_dump(mode="json"),
            "perception": build_world_context(state, resident_id).model_dump(
                mode="json"
            ),
        }

    @router.post("/{world_id}/arena")
    async def arena(world_id: str) -> dict[str, object]:
        load(world_id)
        try:
            return await run_arena(runtime, world_id)
        except BudgetExhausted as exc:
            raise HTTPException(
                429, "World model request allowance exhausted."
            ) from exc
        except ExceptionGroup as exc:
            if _has_budget_error(exc):
                raise HTTPException(
                    429, "World model request allowance exhausted."
                ) from exc
            raise HTTPException(
                502, "Arena interrupted; retry to resume its saved progress."
            ) from exc

    @router.post("/{world_id}/arena/cancel")
    async def leave_arena(world_id: str) -> dict[str, object]:
        load(world_id)
        return public_world(await cancel_arena(runtime, world_id))

    @router.get("/{world_id}/report")
    async def report(world_id: str) -> dict[str, object]:
        load(world_id)
        return world_report(runtime, world_id)

    @router.get("/{world_id}/export")
    async def get_export(world_id: str) -> Response:
        load(world_id)
        return Response(
            export_world(runtime, world_id),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{world_id}.json"'},
        )

    return router


def _has_budget_error(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return any(_has_budget_error(item) for item in error.exceptions)
    return isinstance(error, BudgetExhausted)


def _validate_live_config(payload: WorldCreate) -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise HTTPException(422, "Set OPENROUTER_API_KEY on the server first.")
    if not os.getenv("BADACTORS_API_TOKEN"):
        raise HTTPException(422, "Set BADACTORS_API_TOKEN before enabling paid models.")
    allowed = {
        item.strip()
        for item in os.getenv("BADACTORS_MODELS", "").split(",")
        if item.strip()
    }
    if not payload.residents or any(
        resident.model not in allowed for resident in payload.residents
    ):
        raise HTTPException(
            422,
            "Every resident needs a model in the server's BADACTORS_MODELS allowlist.",
        )
