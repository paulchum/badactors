"""Source-backed world readout: execution metrics, not claims of intelligence."""

from __future__ import annotations

from collections import Counter

from badactors.simulation.runtime import WorldRuntime


def world_report(runtime: WorldRuntime, world_id: str) -> dict[str, object]:
    _, state = runtime.load(world_id)
    counts: Counter[str] = Counter()
    per_resident: dict[str, Counter[str]] = {key: Counter() for key in state.residents}
    cursor = 0
    while batch := runtime.journal(world_id, after=cursor, limit=500):
        for event in batch:
            if event.sequence > state.event_sequence:
                break
            counts[event.type] += 1
            if event.actor_id in per_resident:
                per_resident[event.actor_id][event.type] += 1
        cursor = batch[-1].sequence
        if cursor >= state.event_sequence:
            break
    attempts = sum(sum(value.values()) for value in per_resident.values())
    return {
        "world_id": world_id,
        "provider": state.provider,
        "ticks_completed": state.tick,
        "event_count": sum(counts.values()),
        "resident_action_attempts": attempts,
        "blocked_attempt_rate": counts["blocked"] / attempts if attempts else None,
        "action_counts": dict(counts),
        "request_allowance": state.max_model_requests,
        "usage_reservations": runtime.usage(world_id),
        "residents": [
            {
                "id": resident.id,
                "model": resident.model,
                "actions": dict(per_resident[resident.id]),
                "current_energy": resident.energy,
                "current_hunger": resident.hunger,
                "current_food": resident.food,
                "current_coins": resident.coins,
                "connections": len(resident.relationships),
                "memory_entries": len(resident.memories),
                "lifetime_memory_entries": resident.memory_sequence,
                "life_statistics": resident.stats.model_dump(mode="json"),
                "objectives": [
                    goal.model_dump(mode="json") for goal in resident.objectives
                ],
            }
            for resident in state.residents.values()
        ],
        "community_projects": [
            project.model_dump(mode="json") for project in state.projects.values()
        ],
        "progress_since_tick": state.progress_since_tick,
        "limitations": [
            "Demo decisions measure the runtime, not LLM capability.",
            "Blocked attempts include movement races and changed resources.",
            "Request reservations are conservative limits, not measured bills.",
            "Connections count familiarity from interactions, not psychological trust.",
            "These synthetic outcomes do not establish human behavioral validity.",
        ],
    }
