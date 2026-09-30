"""Data contracts for the Castle world.

Three rules shape every model here.

Truth and perception are separate. A ``LedgerEntry`` holds what actually
happened; each ``WitnessRecord`` on it holds what one player wrote down, already
degraded by the light. A prompt is built from witness records, never from the
entry's own ``actor``, so a vague or mistaken record cannot leak the truth.

Placement is not a property of an object. ``WorldState.placements`` is a cache of
the current custody, and it must always equal a replay of the ledger.

Randomness is a cursor, not an object. ``seed`` plus ``rng_cursor`` reproduces
every roll, and each roll is recorded on the entry that consumed it.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Lighting = Literal["bright", "dim", "dark"]
Confidence = Literal["clear", "unsure", "glimpse"]

Sense = Literal["own", "saw", "heard"]
"""How a record's holder perceived an event: their own act, sight, or a sound
through a wall."""

Channel = Literal["direct", "speech", "whisper", "note", "book", "log", "steward"]
"""What content arrived through. ``direct`` is the holder's own senses, with
nothing communicated; every other channel carries quoted words. ``steward`` is the
official channel, and only the host writes to it."""

QUOTE_LIMIT = 400
"""The most words one message may carry. Filibuster will be the only exception."""

CastlePhase = Literal[
    "breakfast",
    "mission",
    "free_time",
    "round_table",
    "dinner",
    "night",
]

DAY_ORDER: tuple[CastlePhase, ...] = (
    "breakfast",
    "mission",
    "free_time",
    "round_table",
    "dinner",
    "night",
)

GATHERINGS: frozenset[CastlePhase] = frozenset({"breakfast", "round_table", "dinner"})


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Record(Contract):
    """Something already written down. Frozen, so copies of a world can share it.

    The ledger only ever grows, so a world and the copy it was forked from hold
    the same entry objects rather than a deep copy of the whole past each tick.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --------------------------------------------------------------------------- map


class Room(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=240)
    lighting: Lighting
    capacity: int = Field(ge=1, le=24)
    private: bool = False


class Passage(Contract):
    """An undirected connection. Travel time is a property of the edge."""

    rooms: list[str] = Field(min_length=2, max_length=2)
    travel_ticks: int = Field(ge=1, le=6)
    description: str = Field(default="", max_length=120)

    def other(self, room_id: str) -> str | None:
        first, second = self.rooms
        if room_id == first:
            return second
        if room_id == second:
            return first
        return None


class Fact(Contract):
    """An objective statement that can be discovered in a room."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    text: str = Field(min_length=1, max_length=240)
    room_id: str
    useful: bool = False


class Thing(Contract):
    """An object. Where it is lives in the ledger, not here."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    kind: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    properties: dict[str, str] = Field(default_factory=dict)
    container: bool = False
    important: bool = False
    claimants: list[str] = Field(default_factory=list)


class InRoom(Contract):
    where: Literal["room"] = "room"
    room_id: str
    hidden: bool = False


class HeldBy(Contract):
    where: Literal["held"] = "held"
    player_id: str


class InContainer(Contract):
    where: Literal["container"] = "container"
    thing_id: str


class Offstage(Contract):
    """In the Steward's keeping: out of the castle and out of play."""

    where: Literal["offstage"] = "offstage"


type Placement = Annotated[
    InRoom | HeldBy | InContainer | Offstage,
    Field(discriminator="where"),
]


def quickest_route(passages: Sequence[Passage], origin: str, target: str) -> list[str]:
    """The rooms after ``origin`` on the quickest way to ``target``.

    Quickest by travel ticks, then by fewest rooms, then alphabetically, so the
    same map always gives the same route. Empty when already there or when
    ``target`` cannot be reached.
    """
    frontier: list[tuple[int, int, tuple[str, ...], str]] = [(0, 0, (), origin)]
    settled: set[str] = set()
    while frontier:
        ticks, hops, path, room_id = heapq.heappop(frontier)
        if room_id in settled:
            continue
        settled.add(room_id)
        if room_id == target:
            return list(path)
        for passage in passages:
            neighbor = passage.other(room_id)
            if neighbor is not None and neighbor not in settled:
                heapq.heappush(
                    frontier,
                    (
                        ticks + passage.travel_ticks,
                        hops + 1,
                        (*path, neighbor),
                        neighbor,
                    ),
                )
    return []


class PhaseBudget(Contract):
    breakfast: int = Field(default=2, ge=1, le=24)
    mission: int = Field(default=4, ge=1, le=24)
    free_time: int = Field(default=6, ge=1, le=24)
    round_table: int = Field(default=3, ge=1, le=24)
    dinner: int = Field(default=2, ge=1, le=24)
    night: int = Field(default=5, ge=1, le=24)

    def ticks(self, phase: CastlePhase) -> int:
        return int(getattr(self, phase))


class CastleMap(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,39}$")
    name: str = Field(min_length=1, max_length=80)
    rooms: list[Room] = Field(min_length=8, max_length=12)
    passages: list[Passage] = Field(min_length=8)
    things: list[Thing] = Field(default_factory=list)
    placements: dict[str, Placement] = Field(default_factory=dict)
    facts: list[Fact] = Field(default_factory=list)
    gathering_room: str
    conclave_room: str
    chambers_room: str
    phase_ticks: PhaseBudget = Field(default_factory=PhaseBudget)
    max_replans_per_phase: int = Field(default=2, ge=0, le=8)

    def room(self, room_id: str) -> Room:
        for room in self.rooms:
            if room.id == room_id:
                return room
        raise KeyError(room_id)

    def neighbors(self, room_id: str) -> list[str]:
        found = [
            other
            for passage in self.passages
            if (other := passage.other(room_id)) is not None
        ]
        return sorted(found)

    def travel_ticks(self, origin: str, destination: str) -> int | None:
        for passage in self.passages:
            if passage.other(origin) == destination:
                return passage.travel_ticks
        return None

    def route(self, origin: str, target: str) -> list[str]:
        """The rooms after ``origin`` on the quickest way to ``target``."""
        return quickest_route(self.passages, origin, target)


# ------------------------------------------------------------------------ plans


class Move(Contract):
    kind: Literal["move"] = "move"
    room_id: str


class Take(Contract):
    kind: Literal["take"] = "take"
    thing_id: str


class Drop(Contract):
    """Put down something held: in plain sight, hidden, or into a container."""

    kind: Literal["drop"] = "drop"
    thing_id: str
    hidden: bool = False
    into: str | None = None

    @model_validator(mode="after")
    def one_destination(self) -> Self:
        if self.into is not None and self.hidden:
            raise ValueError("Stow into a container or hide in the room, not both.")
        return self


class Use(Contract):
    kind: Literal["use"] = "use"
    thing_id: str


class ToolStep(Contract):
    kind: Literal["tool"] = "tool"
    tool: Literal["search", "inspect"]
    thing_id: str | None = None


class Talk(Contract):
    kind: Literal["talk"] = "talk"
    text: str = Field(min_length=1, max_length=QUOTE_LIMIT)
    to: str | None = None


class Whisper(Contract):
    """Words for one person's ear. Nobody else in the room catches them."""

    kind: Literal["whisper"] = "whisper"
    to: str
    text: str = Field(min_length=1, max_length=QUOTE_LIMIT)


class Wait(Contract):
    kind: Literal["wait"] = "wait"
    reason: str = Field(default="", max_length=200)


type Step = Annotated[
    Move | Take | Drop | Use | ToolStep | Talk | Whisper | Wait,
    Field(discriminator="kind"),
]

MAX_PLAN_STEPS = 5


class Plan(Contract):
    goal: str = Field(default="", max_length=200)
    steps: list[Step] = Field(default_factory=list, max_length=MAX_PLAN_STEPS)


# ----------------------------------------------------------------------- ledger


class RngDraw(Record):
    """One consumed roll, replayable from ``seed`` and ``cursor``."""

    cursor: int
    purpose: str = Field(min_length=1, max_length=80)
    value: float = Field(ge=0.0, lt=1.0)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    success: bool | None = None


class WitnessRecord(Record):
    """What one player wrote down. Already degraded; never holds hidden truth.

    ``text`` is simulator narration and never contains anyone's words. Words that
    arrived through a channel sit in ``quote``, attributed to ``author`` as this
    holder recorded them, so they can be shown as attributed quotes and never
    mistaken for the narration around them.
    """

    player_id: str
    sense: Sense
    confidence: Confidence
    channel: Channel
    text: str = Field(min_length=1, max_length=400)
    quote: str | None = Field(default=None, min_length=1, max_length=QUOTE_LIMIT)
    author: str | None = None
    recorded_actor: str | None = None
    from_room: str | None = None

    @model_validator(mode="after")
    def words_travel_only_by_channel(self) -> Self:
        if self.sense == "heard" and (
            self.quote is not None
            or self.author is not None
            or self.recorded_actor is not None
        ):
            raise ValueError(
                "A wall carries the kind of event and the room, never words or a name."
            )
        if self.channel == "direct" and self.quote is not None:
            raise ValueError("A direct record carries no quoted words.")
        if self.channel != "direct" and self.sense != "heard" and self.quote is None:
            raise ValueError("Words that arrived through a channel must be kept.")
        if self.author is not None and self.quote is None:
            raise ValueError("Only quoted words have an author.")
        return self


class LedgerEntry(Record):
    """Ground truth for one thing that happened, plus every record of it.

    ``quote`` holds the exact words said or written, if any. ``payload`` is what
    happened, in machine-readable form; the fact-checker treats it as fact.
    ``private`` is what the actor meant — a plan's goal, a reason, a declared
    intent — which is not a fact about the world. Only the audience sees it: no
    scoping path reads it, and an actor's own memory of it, if any, is an ordinary
    witness record.
    """

    seq: int = Field(ge=1)
    tick: int = Field(ge=0)
    day: int = Field(ge=1)
    phase: CastlePhase
    type: str = Field(min_length=1, max_length=40)
    actor_id: str | None = None
    room_id: str | None = None
    text: str = Field(min_length=1, max_length=400)
    quote: str | None = Field(default=None, min_length=1, max_length=QUOTE_LIMIT)
    payload: dict[str, str] = Field(default_factory=dict)
    private: dict[str, str] = Field(default_factory=dict)
    witnesses: list[WitnessRecord] = Field(default_factory=list)
    rolls: list[RngDraw] = Field(default_factory=list)

    def record_for(self, player_id: str) -> WitnessRecord | None:
        for witness in self.witnesses:
            if witness.player_id == player_id:
                return witness
        return None


# ------------------------------------------------------------------------ world


class InTransit(Contract):
    where: Literal["transit"] = "transit"
    origin: str
    destination: str
    ticks_remaining: int = Field(ge=1)


class AtRoom(Contract):
    where: Literal["at"] = "at"
    room_id: str


type Position = Annotated[AtRoom | InTransit, Field(discriminator="where")]


class PlayerState(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    name: str = Field(min_length=1, max_length=60)
    persona: str = Field(min_length=1, max_length=500)
    appearance: str = Field(min_length=1, max_length=80)
    position: Position
    alive: bool = True
    asleep: bool = False
    plan: Plan = Field(default_factory=Plan)
    disguised_as: str | None = None

    @property
    def room_id(self) -> str | None:
        position = self.position
        return position.room_id if isinstance(position, AtRoom) else None


class WorldState(Contract):
    schema_version: Literal[1] = 1
    id: str
    seed: int = Field(ge=0, le=2**31 - 1)
    rng_cursor: int = Field(default=0, ge=0)
    tick: int = Field(default=0, ge=0)
    day: int = Field(default=1, ge=1)
    phase: CastlePhase = "breakfast"
    phase_tick: int = Field(default=0, ge=0)
    map: CastleMap
    seats: list[str] = Field(min_length=2, max_length=24)
    players: dict[str, PlayerState]
    placements: dict[str, Placement] = Field(default_factory=dict)
    ledger: list[LedgerEntry] = Field(default_factory=list)
    ledger_seq: int = Field(default=0, ge=0)
    inspected: dict[str, list[str]] = Field(default_factory=dict)
    searched: dict[str, list[str]] = Field(default_factory=dict)
    replans: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        room_ids = {room.id for room in self.map.rooms}
        if set(self.seats) != set(self.players):
            raise ValueError("Seats and players must name the same people.")
        if len(set(self.seats)) != len(self.seats):
            raise ValueError("Seat order must not repeat a player.")
        for key, player in self.players.items():
            if key != player.id:
                raise ValueError("Player identity is inconsistent.")
            for room_id in _position_rooms(player.position):
                if room_id not in room_ids:
                    raise ValueError(f"Player {key} stands in an unknown room.")
        thing_ids = {thing.id for thing in self.map.things}
        if set(self.placements) != thing_ids:
            raise ValueError("Every object needs exactly one placement.")
        for thing_id, placement in self.placements.items():
            if isinstance(placement, InRoom) and placement.room_id not in room_ids:
                raise ValueError(f"Object {thing_id} sits in an unknown room.")
            if isinstance(placement, HeldBy):
                if placement.player_id not in self.players:
                    raise ValueError(f"Object {thing_id} is held by an unknown player.")
            if isinstance(placement, InContainer):
                if placement.thing_id == thing_id:
                    raise ValueError(f"Object {thing_id} cannot contain itself.")
                if placement.thing_id not in thing_ids:
                    raise ValueError(f"Object {thing_id} sits in an unknown container.")
        if [entry.seq for entry in self.ledger] != list(range(1, len(self.ledger) + 1)):
            raise ValueError("Ledger sequence numbers must be a gapless run from 1.")
        if self.ledger_seq < len(self.ledger):
            raise ValueError("Ledger sequence counter is behind the ledger.")
        return self


def _position_rooms(position: Position) -> tuple[str, ...]:
    if isinstance(position, AtRoom):
        return (position.room_id,)
    return (position.origin, position.destination)


# ----------------------------------------------------------------- observations


class ObservationItem(Contract):
    """One line of a player's memory, tagged with the entry it came from.

    ``kind`` is the entry's type, ``actor`` whoever the holder took the actor to
    be (already degraded, like the text), and ``target`` whom the act was aimed
    at when the holder could see that. They restate the record in a form a
    program can read; none of them adds anything the record does not hold.
    """

    ledger_seq: int = Field(ge=1)
    tick: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=40)
    sense: Sense
    confidence: Confidence
    channel: Channel
    text: str = Field(min_length=1, max_length=400)
    quote: str | None = Field(default=None, min_length=1, max_length=QUOTE_LIMIT)
    author: str | None = None
    actor: str | None = None
    target: str | None = None
    from_room: str | None = None


class CustodyLink(Contract):
    ledger_seq: int = Field(ge=1)
    tick: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=400)


class ThingView(Contract):
    id: str
    kind: str
    name: str
    properties: dict[str, str] = Field(default_factory=dict)
    custody: list[CustodyLink] | None = None


class RoomView(Contract):
    id: str
    name: str
    description: str
    lighting: Lighting
    exits: list[str] = Field(default_factory=list)


class TransitView(Contract):
    origin: str
    destination: str
    ticks_remaining: int = Field(ge=1)


class ScopedObservation(Contract):
    """Everything one player may be told, and nothing else."""

    viewer_id: str
    tick: int = Field(ge=0)
    day: int = Field(ge=1)
    phase: CastlePhase
    room: RoomView | None = None
    transit: TransitView | None = None
    present: list[str] = Field(default_factory=list)
    searched_here: bool = False
    visible_objects: list[ThingView] = Field(default_factory=list)
    carrying: list[ThingView] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    recent: list[ObservationItem] = Field(default_factory=list)
    plan_remaining: list[Step] = Field(default_factory=list)
    valid_actions: list[str] = Field(default_factory=list)
