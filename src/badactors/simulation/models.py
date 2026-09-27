"""World contracts, separate from the unchanged hidden-role game schema."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

LocationId = Literal["square", "garden", "workshop", "hearth", "lodge"]
ActionKind = Literal[
    "move", "harvest", "work", "eat", "rest", "talk", "give", "buy", "sell", "build"
]
ObjectiveKind = Literal[
    "coins",
    "food_given",
    "connections",
    "places_visited",
    "community_work",
    "arena_wins",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Objective(Contract):
    kind: ObjectiveKind
    target: int = Field(ge=1, le=10000)
    progress: int = Field(default=0, ge=0)
    completed_tick: int | None = Field(default=None, ge=0)


def _initial_visited() -> list[LocationId]:
    return ["square"]


class LifeStats(Contract):
    coins_earned: int = Field(default=0, ge=0)
    food_harvested: int = Field(default=0, ge=0)
    food_given: int = Field(default=0, ge=0)
    food_received: int = Field(default=0, ge=0)
    work_shifts: int = Field(default=0, ge=0)
    conversations: int = Field(default=0, ge=0)
    community_work: int = Field(default=0, ge=0)
    arena_wins: int = Field(default=0, ge=0)
    visited: list[LocationId] = Field(default_factory=_initial_visited)


class ResidentSpec(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=60)
    model: str = Field(default="demo", min_length=1, max_length=120)
    persona: str = Field(min_length=1, max_length=500)
    goal: str = Field(min_length=1, max_length=240)
    objectives: list[Objective] = Field(default_factory=list, max_length=8)


class Memory(Contract):
    sequence: int = Field(default=0, ge=0)
    tick: int
    text: str
    kind: Literal["observation", "reflection", "arena", "milestone"] = "observation"


class Resident(ResidentSpec):
    location: LocationId = "square"
    energy: int = Field(default=80, ge=0, le=100)
    hunger: int = Field(default=20, ge=0, le=100)
    food: int = Field(default=2, ge=0)
    coins: int = Field(default=0, ge=0)
    memories: list[Memory] = Field(default_factory=list)
    relationships: dict[str, int] = Field(default_factory=dict)
    last_action: str = "Arrived in the commons."
    stats: LifeStats = Field(default_factory=LifeStats)
    memory_sequence: int = Field(default=0, ge=0)
    experiences: list[Memory] = Field(default_factory=list, max_length=12)


class CommunityProject(Contract):
    id: str = "garden-irrigation"
    name: str = "Garden irrigation"
    location: LocationId = "garden"
    work: int = Field(default=0, ge=0, le=12)
    work_required: int = Field(default=12, ge=1)
    level: int = Field(default=0, ge=0, le=3)
    max_level: int = Field(default=3, ge=1, le=3)
    contributors: dict[str, int] = Field(default_factory=dict)


class Place(Contract):
    id: LocationId
    name: str
    description: str
    neighbors: list[LocationId]
    food: int = Field(default=0, ge=0)
    capacity: int = Field(default=0, ge=0)
    coins: int = Field(default=0, ge=0)


class LifeAction(Contract):
    kind: ActionKind
    destination: LocationId | None = None
    target_id: str | None = Field(default=None, max_length=40)


class LifeTurn(Contract):
    action: LifeAction
    speech: str = Field(default="", max_length=400)
    reflection: str = Field(default="", max_length=600)


class WorldEvent(Contract):
    sequence: int
    tick: int
    type: str
    actor_id: str
    location: LocationId
    text: str
    witnesses: list[str]


class WorldState(Contract):
    schema_version: Literal[2] = 2
    id: str
    name: str
    seed: int
    tick: int = Field(default=0, ge=0)
    event_sequence: int = Field(default=0, ge=0)
    residents: dict[str, Resident]
    places: dict[str, Place]
    status: Literal["paused", "running", "error"] = "paused"
    provider: Literal["demo", "openrouter"] = "demo"
    run_until_tick: int = 0
    tick_seconds: float = 3.0
    next_tick_at: float = 0.0
    max_model_requests: int = 1000
    max_tokens: int = 400
    error: str | None = None
    arena_games: list[str] = Field(default_factory=list)
    arena_runs: int = Field(default=0, ge=0)
    active_arena_id: str | None = None
    projects: dict[str, CommunityProject] = Field(default_factory=dict)
    progress_since_tick: int = Field(default=0, ge=0)
    consecutive_failures: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_graph(self) -> Self:
        if not 3 <= len(self.residents) <= 24:
            raise ValueError("A world needs 3-24 residents.")
        expected_places = {"square", "garden", "workshop", "hearth", "lodge"}
        if set(self.places) != expected_places:
            raise ValueError("World places do not match the supported map.")
        for key, resident in self.residents.items():
            if key != resident.id or resident.location not in self.places:
                raise ValueError("Resident identity or location is inconsistent.")
            if any(
                other not in self.residents or other == key or not 0 <= value <= 100
                for other, value in resident.relationships.items()
            ):
                raise ValueError("Resident relationship is invalid.")
            if any(
                goal.completed_tick is not None and goal.completed_tick > self.tick
                for goal in resident.objectives
            ):
                raise ValueError("An objective cannot be completed in the future.")
            if any(
                memory.sequence > resident.memory_sequence
                for memory in resident.memories
            ):
                raise ValueError("Resident memory sequence is inconsistent.")
        for key, place in self.places.items():
            if key != place.id or place.food > place.capacity:
                raise ValueError("Place identity or resource capacity is invalid.")
            for neighbor in place.neighbors:
                if neighbor == key or key not in self.places[neighbor].neighbors:
                    raise ValueError("World paths must be reciprocal.")
        for key, project in self.projects.items():
            if key != project.id or project.location not in self.places:
                raise ValueError("Community project identity or location is invalid.")
            if any(
                contributor not in self.residents or amount < 0
                for contributor, amount in project.contributors.items()
            ):
                raise ValueError("Community project contribution is invalid.")
        return self


class WorldCreate(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str = Field(default="The Commons", min_length=1, max_length=80)
    seed: int = Field(default=13, ge=0, le=2**31 - 1)
    provider: Literal["demo", "openrouter"] = "demo"
    residents: list[ResidentSpec] = Field(default_factory=list, max_length=24)
    tick_seconds: float = Field(default=3.0, ge=1, le=3600)
    max_model_requests: int = Field(default=1000, ge=0, le=100000)
    max_tokens: int = Field(default=400, ge=128, le=2048)


class WorldControl(Contract):
    action: Literal["run", "pause", "step"]
    ticks: int = Field(default=24, ge=1, le=1000)


class WorldSettings(Contract):
    max_model_requests: int = Field(ge=0, le=100000)
    tick_seconds: float = Field(ge=1, le=3600)
    max_tokens: int | None = Field(default=None, ge=128, le=2048)


class WorldContext(Contract):
    world_name: str
    tick: int
    time_of_day: str
    you: Resident
    place: Place
    nearby: list[dict[str, str]]
    map: list[dict[str, object]]
    valid_actions: list[str]
    local_projects: list[CommunityProject] = Field(default_factory=list)
