"""Explicit, additive upgrades for persisted world snapshots."""

from __future__ import annotations

from copy import deepcopy

from badactors.simulation.engine import default_residents
from badactors.simulation.models import CommunityProject, WorldState


def decode_world(payload: dict[str, object]) -> WorldState:
    version = payload.get("schema_version", 1)
    if type(version) is not int:
        raise ValueError("World schema version must be an integer.")
    if version == 2:
        return WorldState.model_validate(payload)
    if version != 1:
        raise ValueError(f"Unsupported world schema version: {version}")
    updated = deepcopy(payload)
    updated["schema_version"] = 2
    updated["progress_since_tick"] = payload.get("tick", 0)
    games = payload.get("arena_games", [])
    if not isinstance(games, list):
        raise ValueError("Legacy arena history must be a list.")
    updated["arena_runs"] = len(games)
    project = CommunityProject()
    updated["projects"] = {project.id: project.model_dump(mode="json")}
    # Old worlds had an empty hearth. Add the market with explicit initial stock.
    places = updated.get("places")
    if isinstance(places, dict) and isinstance(places.get("hearth"), dict):
        places["hearth"].update(
            food=8,
            capacity=60,
            coins=24,
            description="Meet, share, or trade food: buy for 2 coins, sell for 1.",
        )
    residents = updated.get("residents")
    if isinstance(residents, dict):
        defaults = {spec.id: spec for spec in default_residents()}
        for resident in residents.values():
            if not isinstance(resident, dict):
                raise ValueError("Legacy resident must be an object.")
            resident_id = resident.get("id")
            if not isinstance(resident_id, str):
                raise ValueError("Legacy resident ID must be a string.")
            known = defaults.get(resident_id)
            if known and all(
                resident.get(field) == getattr(known, field)
                for field in ("name", "persona", "goal")
            ):
                resident["objectives"] = [
                    objective.model_dump(mode="json") for objective in known.objectives
                ]
            memories = resident.get("memories", [])
            if not isinstance(memories, list) or any(
                not isinstance(m, dict) for m in memories
            ):
                raise ValueError("Legacy memories must be a list of objects.")
            for index, memory in enumerate(memories, start=1):
                memory["sequence"] = index
            resident["memory_sequence"] = len(memories)
            resident["experiences"] = [
                memory
                for memory in memories
                if memory["kind"] in {"arena", "milestone"}
            ][-12:]
    return WorldState.model_validate(updated)
