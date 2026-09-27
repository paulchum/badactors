"""Pure world transitions: physical constraints, local consequences and memory."""

from __future__ import annotations

from collections.abc import Mapping

from badactors.simulation.models import (
    CommunityProject,
    LifeTurn,
    Objective,
    Place,
    Resident,
    ResidentSpec,
    WorldCreate,
    WorldEvent,
    WorldState,
)
from badactors.simulation.progression import FOOD_CARRY_LIMIT, update_objectives
from badactors.simulation.progression import remember as remember


def create_world(config: WorldCreate) -> WorldState:
    specs = config.residents or default_residents()
    if not 3 <= len(specs) <= 24:
        raise ValueError("A world needs between 3 and 24 residents.")
    if len({spec.id for spec in specs}) != len(specs):
        raise ValueError("Resident IDs must be unique.")
    maxima = {"places_visited": 5, "connections": len(specs) - 1, "community_work": 36}
    if any(
        objective.target > maxima.get(objective.kind, 10000)
        for spec in specs
        for objective in spec.objectives
    ):
        raise ValueError("An objective exceeds what this world can support.")
    project = CommunityProject()
    residents = {spec.id: Resident(**spec.model_dump()) for spec in specs}
    for resident in residents.values():
        for objective in resident.objectives:
            objective.progress = 0
            objective.completed_tick = None
        update_objectives(resident, 0)
    return WorldState(
        id=config.id,
        name=config.name,
        seed=config.seed,
        provider=config.provider,
        tick_seconds=config.tick_seconds,
        max_model_requests=config.max_model_requests,
        max_tokens=config.max_tokens,
        residents=residents,
        places={place.id: place for place in _places()},
        projects={project.id: project},
    )


def default_residents() -> list[ResidentSpec]:
    rows = [
        (
            "mara",
            "Mara",
            "A patient engineer who values useful work.",
            "Build a reserve of 30 coins and help a neighbor.",
        ),
        (
            "sol",
            "Sol",
            "A generous gardener who notices scarcity.",
            "Keep a food reserve and feed someone who needs it.",
        ),
        (
            "iris",
            "Iris",
            "An outgoing organizer with a long memory.",
            "Build relationships with three different neighbors.",
        ),
        (
            "knox",
            "Knox",
            "A competitive craftsperson who wants independence.",
            "Earn 40 coins without running out of food.",
        ),
        (
            "june",
            "June",
            "A curious observer who asks careful questions.",
            "Explore every place and get to know the residents.",
        ),
        (
            "ash",
            "Ash",
            "A restless explorer who repays favors.",
            "Travel widely and share food with a new friend.",
        ),
    ]
    objectives = [
        [
            Objective(kind="coins", target=30),
            Objective(kind="community_work", target=2),
        ],
        [Objective(kind="food_given", target=4)],
        [Objective(kind="connections", target=3)],
        [Objective(kind="coins", target=40)],
        [
            Objective(kind="places_visited", target=5),
            Objective(kind="connections", target=3),
        ],
        [
            Objective(kind="places_visited", target=5),
            Objective(kind="food_given", target=2),
        ],
    ]
    return [
        ResidentSpec(id=i, name=n, persona=p, goal=g, objectives=goals)
        for (i, n, p, g), goals in zip(rows, objectives, strict=True)
    ]


def _places() -> list[Place]:
    return [
        Place(
            id="square",
            name="The Square",
            description="Paths cross here.",
            neighbors=["garden", "workshop", "hearth", "lodge"],
        ),
        Place(
            id="garden",
            name="The Garden",
            description="Harvest replenishing food.",
            neighbors=["square", "hearth"],
            food=12,
            capacity=12,
        ),
        Place(
            id="workshop",
            name="The Workshop",
            description="Work earns three coins.",
            neighbors=["square", "lodge"],
        ),
        Place(
            id="hearth",
            name="The Hearth",
            description="Meet, share, or trade food: buy for 2 coins, sell for 1.",
            neighbors=["square", "garden"],
            food=8,
            capacity=60,
            coins=24,
        ),
        Place(
            id="lodge",
            name="The Lodge",
            description="Rest restores extra energy.",
            neighbors=["square", "workshop"],
        ),
    ]


def advance_world(
    state: WorldState, turns: Mapping[str, LifeTurn]
) -> tuple[WorldState, list[WorldEvent]]:
    """Resolve a simultaneous decision snapshot in rotating, deterministic order."""
    if set(turns) != set(state.residents):
        raise ValueError("Exactly one decision per resident is required.")
    updated = state.model_copy(deep=True)
    updated.tick += 1
    order = sorted(updated.residents)
    offset = (state.seed + state.tick) % len(order)
    order = order[offset:] + order[:offset]
    events: list[WorldEvent] = []
    for resident in updated.residents.values():
        resident.hunger = min(100, resident.hunger + 4)
        resident.energy = max(0, resident.energy - (8 if resident.hunger >= 90 else 2))
    for resident_id in order:
        resident = updated.residents[resident_id]
        turn = turns[resident_id]
        origin = resident.location
        observers = {
            other.id for other in updated.residents.values() if other.location == origin
        }
        text, succeeded = _act(updated, resident, turn)
        resident.last_action = text
        # A failed attempt is observable only to its author.
        witnesses = observers if succeeded else {resident.id}
        witnesses.add(resident.id)
        if succeeded and resident.location != origin:
            witnesses.update(
                other.id
                for other in updated.residents.values()
                if other.location == resident.location
            )
        updated.event_sequence += 1
        event = WorldEvent(
            sequence=updated.event_sequence,
            tick=updated.tick,
            type=turn.action.kind if succeeded else "blocked",
            actor_id=resident.id,
            location=resident.location,
            text=text,
            witnesses=sorted(witnesses),
        )
        events.append(event)
        for witness in witnesses:
            remember(updated.residents[witness], updated.tick, text)
        if turn.reflection:
            remember(resident, updated.tick, turn.reflection, "reflection")
    garden = updated.places["garden"]
    irrigation = updated.projects.get("garden-irrigation")
    garden.food = min(
        garden.capacity, garden.food + 2 + (irrigation.level if irrigation else 0)
    )
    for resident in updated.residents.values():
        update_objectives(resident, updated.tick)
    return updated, events


def _act(state: WorldState, actor: Resident, turn: LifeTurn) -> tuple[str, bool]:
    action = turn.action
    place = state.places[actor.location]
    target = state.residents.get(action.target_id or "")
    if action.kind not in {"rest", "eat"} and actor.energy < 10:
        return f"{actor.name} is too exhausted and needs to rest.", False
    if action.kind == "move" and action.destination in place.neighbors:
        actor.location = action.destination
        if actor.location not in actor.stats.visited:
            actor.stats.visited.append(actor.location)
        actor.energy = max(0, actor.energy - 4)
        return f"{actor.name} moved to {state.places[actor.location].name}.", True
    if (
        action.kind == "harvest"
        and actor.location == "garden"
        and place.food > 0
        and actor.food < FOOD_CARRY_LIMIT
    ):
        amount = min(2, place.food, FOOD_CARRY_LIMIT - actor.food)
        place.food -= amount
        actor.food += amount
        actor.stats.food_harvested += amount
        actor.energy = max(0, actor.energy - 8)
        return f"{actor.name} harvested {amount} food.", True
    if action.kind == "work" and actor.location == "workshop" and actor.energy >= 14:
        actor.coins += 3
        actor.stats.coins_earned += 3
        actor.stats.work_shifts += 1
        actor.energy -= 12
        return f"{actor.name} earned 3 coins at the workshop.", True
    if action.kind == "eat" and actor.food > 0:
        actor.food -= 1
        actor.hunger = max(0, actor.hunger - 34)
        return f"{actor.name} ate from their food supply.", True
    if action.kind == "rest":
        actor.energy = min(
            100, actor.energy + (26 if actor.location == "lodge" else 18)
        )
        return f"{actor.name} rested at {place.name}.", True
    if action.kind in {"buy", "sell"}:
        return _trade(state, actor, action.kind)
    if action.kind == "build":
        return _build(state, actor)
    if (
        target is not None
        and target.id != actor.id
        and target.location == actor.location
    ):
        if action.kind == "give" and actor.food > 0 and target.food < FOOD_CARRY_LIMIT:
            actor.food -= 1
            target.food += 1
            actor.stats.food_given += 1
            target.stats.food_received += 1
            _bond(target, actor.id, 2)
            _bond(actor, target.id, 1)
            return f"{actor.name} gave one food to {target.name}.", True
        if action.kind == "talk" and turn.speech.strip():
            actor.stats.conversations += 1
            target.stats.conversations += 1
            _bond(actor, target.id, 1)
            _bond(target, actor.id, 1)
            return f"{actor.name} to {target.name}: “{turn.speech.strip()}”", True
    return f"{actor.name}'s {action.kind} could not happen here.", False


def _trade(state: WorldState, actor: Resident, kind: str) -> tuple[str, bool]:
    market = state.places["hearth"]
    if actor.location != market.id:
        return f"{actor.name} needs to visit the hearth market to trade.", False
    if (
        kind == "buy"
        and market.food > 0
        and actor.coins >= 2
        and actor.food < FOOD_CARRY_LIMIT
    ):
        market.food -= 1
        market.coins += 2
        actor.food += 1
        actor.coins -= 2
        return f"{actor.name} bought one food for 2 coins at the market.", True
    if (
        kind == "sell"
        and market.food < market.capacity
        and actor.food > 0
        and market.coins >= 1
    ):
        market.food += 1
        market.coins -= 1
        actor.food -= 1
        actor.coins += 1
        actor.stats.coins_earned += 1
        return f"{actor.name} sold one food for 1 coin at the market.", True
    return f"{actor.name}'s trade lacked stock, coins, or carrying space.", False


def _build(state: WorldState, actor: Resident) -> tuple[str, bool]:
    project = state.projects.get("garden-irrigation")
    if (
        project is None
        or actor.location != project.location
        or project.level >= project.max_level
        or actor.coins < 2
    ):
        return f"{actor.name} could not contribute to garden irrigation here.", False
    actor.coins -= 2
    actor.energy -= 10
    actor.stats.community_work += 1
    state.places[project.location].coins += 2
    project.work += 1
    project.contributors[actor.id] = project.contributors.get(actor.id, 0) + 1
    result = f"{actor.name} spent 2 coins and contributed work to garden irrigation."
    if project.work >= project.work_required:
        project.level += 1
        project.work = 0
        state.places["garden"].capacity += 8
        result += f" Irrigation reached level {project.level}; more food can grow."
    return result, True


def _bond(resident: Resident, target_id: str, amount: int) -> None:
    resident.relationships[target_id] = min(
        100, resident.relationships.get(target_id, 0) + amount
    )
