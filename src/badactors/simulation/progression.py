"""Pure personal history and objective progress, independent of short memory."""

from __future__ import annotations

from badactors.simulation.models import Memory, Resident

MEMORY_LIMIT = 40
FOOD_CARRY_LIMIT = 12


def remember(
    resident: Resident, tick: int, text: str, kind: str = "observation"
) -> None:
    resident.memory_sequence += 1
    memory = Memory.model_validate(
        {"sequence": resident.memory_sequence, "tick": tick, "text": text, "kind": kind}
    )
    resident.memories = [*resident.memories, memory][-MEMORY_LIMIT:]
    if kind in {"arena", "milestone"}:
        resident.experiences = [*resident.experiences, memory][-12:]


def update_objectives(resident: Resident, tick: int) -> None:
    measures = {
        "coins": resident.coins,
        "food_given": resident.stats.food_given,
        "connections": len(resident.relationships),
        "places_visited": len(set(resident.stats.visited)),
        "community_work": resident.stats.community_work,
        "arena_wins": resident.stats.arena_wins,
    }
    for objective in resident.objectives:
        objective.progress = max(objective.progress, measures[objective.kind])
        if objective.completed_tick is None and objective.progress >= objective.target:
            objective.completed_tick = tick
            remember(
                resident,
                tick,
                f"I reached my {objective.kind.replace('_', ' ')} milestone "
                f"of {objective.target}. This achievement stays in my life record.",
                "milestone",
            )
