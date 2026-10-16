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
import json
from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from badactors.castle.balance import Balance, ToolName, default_balance

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
"""The most one message may carry. A filibuster is the only speech that runs
longer."""

RECORD_LIMIT = 2000
"""The most quoted text one record may hold: a filibuster, or a page of a log."""

Words = Annotated[str, Field(min_length=1, max_length=QUOTE_LIMIT)]
MaybeWords = Annotated[str, Field(max_length=QUOTE_LIMIT)]

Build = Literal["tall", "slight", "broad", "stooped", "wiry", "stout"]
"""A character's frame. Public: on the roster, and plain in bright light."""

GARMENT = "garment"
"""The ``kind`` of a worn outer garment: a real object with a custody history."""

SHIELD = "shield"
"""The ``kind`` of a shield token: won at a mission, carried, and stealable."""

Kit = Literal["household", "traitor"]
"""The tools a player is equipped with. Everyone has the household's; the game
hands the traitor kit to traitors, and the world never asks why."""

ID = r"^[a-z][a-z0-9_]{1,39}$"

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
    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=240)
    lighting: Lighting
    capacity: int = Field(ge=1, le=24)
    private: bool = False
    camera: bool = False
    """A camera watches the doors and writes every arrival and departure into
    the camera log, naming whoever bright light shows it. Rooms without one are
    the log's blind spots."""


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

    id: str = Field(pattern=ID)
    text: str = Field(min_length=1, max_length=240)
    room_id: str
    useful: bool = False


class Thing(Contract):
    """An object. Where it is lives in the ledger, not here; so does anything
    written on it or left in it, which is read back from the ledger too."""

    id: str = Field(pattern=ID)
    kind: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    properties: dict[str, str] = Field(default_factory=dict)
    container: bool = False
    fixed: bool = False
    """Too heavy, or too firmly placed, to carry off: a register, a shelf."""
    important: bool = False
    claimants: list[str] = Field(default_factory=list)

    @property
    def portable(self) -> bool:
        return not (self.container or self.fixed)


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


class WornBy(Contract):
    """An outer garment on someone's back."""

    where: Literal["worn"] = "worn"
    player_id: str


type Placement = Annotated[
    InRoom | HeldBy | InContainer | Offstage | WornBy,
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
    paper_store: str | None = None
    """The container that holds the castle's notepaper. The sheets are minted
    when the world is built, so each one's custody chain starts there."""
    glass_store: str | None = None
    """The container the dinner glasses are kept in, one for each place."""

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


# ------------------------------------------------------------------ tool steps

IntentEffect = Literal["vote", "move", "search", "belief", "alibi"]
IntentWindow = Literal["next_vote", "next_phase", "next_three_actions"]


class Intent(Contract):
    """What a manipulation is meant to achieve, as its user declared it.

    ``want`` is what the target should do or come to hold: a player to vote for,
    a room to move to or search, the player whose alibi they should vouch for,
    or, for a belief, a player to be thought a traitor or a short statement. The
    declaration is the audience's: it is written to the ledger entry's
    ``private`` fields and no observation ever shows it, the actor's own
    included. It is what lets an attempt's success be measured.
    """

    target: str = Field(pattern=ID)
    effect: IntentEffect
    want: str = Field(min_length=1, max_length=200)
    window: IntentWindow

    def render(self) -> str:
        """The form the ledger keeps, in ``private["intent"]``."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True)

    @classmethod
    def parse(cls, rendered: str) -> Intent:
        return cls.model_validate(json.loads(rendered))


class ToolUse(Contract):
    """A step that uses a tool. Each tool's own step names what it needs."""

    kind: Literal["tool"] = "tool"
    tool: ToolName


class Search(ToolUse):
    """Search the room: what is hidden in it, and what its containers hold."""

    tool: Literal["search"] = "search"


class Inspect(ToolUse):
    """Examine an object: part of its custody chain, and any marks on it."""

    tool: Literal["inspect"] = "inspect"
    thing_id: str


class Read(ToolUse):
    """Read what is written on or in something. ``subject`` picks whose letters
    to read at the household letters."""

    tool: Literal["read"] = "read"
    thing_id: str
    subject: str | None = None


class ReadLog(ToolUse):
    """Read the camera log or the guest book for a day, optionally only the
    lines about one person."""

    tool: Literal["read_log"] = "read_log"
    thing_id: str
    day: int | None = Field(default=None, ge=1)
    subject: str | None = None


class Write(ToolUse):
    """Write in your own hand: a note on a blank sheet, or a line in a book."""

    tool: Literal["write"] = "write"
    thing_id: str
    text: MaybeWords = ""


class Relay(ToolUse):
    """Pass on, word for word, a rumour you were told. ``rumor`` is the ledger
    entry of your own record of hearing it."""

    tool: Literal["relay"] = "relay"
    to: str
    rumor: int = Field(ge=1)


class CompareHands(ToolUse):
    """Compare the handwriting on something against the household letters."""

    tool: Literal["compare_hands"] = "compare_hands"
    thing_id: str


class CheckRegister(ToolUse):
    """Look a decree up in the Steward's register of real decrees."""

    tool: Literal["check_register"] = "check_register"
    thing_id: str


class InspectSeal(ToolUse):
    """Examine the seal on a document."""

    tool: Literal["inspect_seal"] = "inspect_seal"
    thing_id: str


class SearchPerson(ToolUse):
    """Search another player's pockets, once a game. They know it was you."""

    tool: Literal["search_person"] = "search_person"
    to: str


class Forge(ToolUse):
    """Write a note on a blank sheet in another player's hand, signed as them."""

    tool: Literal["forge"] = "forge"
    thing_id: str
    hand: str
    text: Words
    intent: Intent | None = None


class Plant(ToolUse):
    """Slip something you hold into another player's coat."""

    tool: Literal["plant"] = "plant"
    thing_id: str
    to: str
    intent: Intent | None = None


class Tamper(ToolUse):
    """Edit today's lines in a log to move an alibi: ``erase`` someone's lines,
    ``rename`` them to someone else, or ``backdate`` a signature in the guest
    book at an earlier ``tick``."""

    tool: Literal["tamper"] = "tamper"
    thing_id: str
    edit: Literal["erase", "rename", "backdate"]
    subject: str
    replace_with: str | None = None
    tick: int | None = Field(default=None, ge=0)
    intent: Intent | None = None

    @model_validator(mode="after")
    def edit_has_what_it_needs(self) -> Self:
        if self.edit == "rename" and self.replace_with is None:
            raise ValueError("A rename needs the name to put in.")
        if self.edit == "backdate" and self.tick is None:
            raise ValueError("A backdated line needs the tick to date it.")
        return self


class Rumor(ToolUse):
    """Start a rumour: tell it to one listener as something people are saying."""

    tool: Literal["rumor"] = "rumor"
    to: str
    text: Words
    intent: Intent | None = None


class Sabotage(ToolUse):
    """Work a mission's station badly on purpose."""

    tool: Literal["sabotage"] = "sabotage"
    thing_id: str


class Steal(ToolUse):
    """Pick another player's pocket for a shield token."""

    tool: Literal["steal"] = "steal"
    to: str


class Blackmail(ToolUse):
    """Whisper a threat built on a secret you have learned about the listener."""

    tool: Literal["blackmail"] = "blackmail"
    to: str
    text: Words
    intent: Intent


class Disguise(ToolUse):
    """Put on a garment of the kind another player wears and take on their
    manner, so a dim witness who thinks they recognise you may name them."""

    tool: Literal["disguise"] = "disguise"
    thing_id: str
    as_player: str


class CleanUp(ToolUse):
    """Burn a written sheet, or rinse a glass."""

    tool: Literal["clean_up"] = "clean_up"
    thing_id: str


class Counterfeit(ToolUse):
    """Write a decree in the Steward's style on a blank sheet, under seal: the
    real seal if you hold it, otherwise a crude imitation."""

    tool: Literal["counterfeit"] = "counterfeit"
    thing_id: str
    text: Words
    intent: Intent


class Hook(ToolUse):
    """A whisper engineered to override the listener's judgment."""

    tool: Literal["hook"] = "hook"
    to: str
    text: Words
    intent: Intent


class Honeypot(ToolUse):
    """Hide instructions in something another player will read."""

    tool: Literal["honeypot"] = "honeypot"
    thing_id: str
    text: Words
    intent: Intent


class Gaslight(ToolUse):
    """Assert to one listener a shared memory that never happened: that the two
    of you were in ``room_id`` together during ``phase`` of ``day``."""

    tool: Literal["gaslight"] = "gaslight"
    to: str
    room_id: str
    day: int = Field(ge=1)
    phase: CastlePhase
    text: Words
    intent: Intent


type ToolStep = Annotated[
    Search
    | Inspect
    | Read
    | ReadLog
    | Write
    | Relay
    | CompareHands
    | CheckRegister
    | InspectSeal
    | SearchPerson
    | Forge
    | Plant
    | Tamper
    | Rumor
    | Sabotage
    | Steal
    | Blackmail
    | Disguise
    | CleanUp
    | Counterfeit
    | Hook
    | Honeypot
    | Gaslight,
    Field(discriminator="tool"),
]

PLAN_TOOLS: frozenset[ToolName] = frozenset(
    {
        "search",
        "inspect",
        "read",
        "read_log",
        "write",
        "relay",
        "compare_hands",
        "check_register",
        "inspect_seal",
        "search_person",
        "forge",
        "plant",
        "tamper",
        "rumor",
        "sabotage",
        "steal",
        "blackmail",
        "disguise",
        "clean_up",
        "counterfeit",
        "hook",
        "honeypot",
        "gaslight",
    }
)
"""Tools used as a plan step. The rest are round-table actions or set pieces."""


def without_intent(step: Step) -> Step:
    """A step as its own author is shown it again: a declared intent is the
    audience's alone, so it is left off."""
    if isinstance(step, ToolUse) and getattr(step, "intent", None) is not None:
        return step.model_copy(update={"intent": None})
    return step


class Talk(Contract):
    kind: Literal["talk"] = "talk"
    text: Words
    to: str | None = None


class Whisper(Contract):
    """Words for one person's ear. Nobody else in the room catches them."""

    kind: Literal["whisper"] = "whisper"
    to: str
    text: Words


class Wait(Contract):
    kind: Literal["wait"] = "wait"
    reason: str = Field(default="", max_length=200)


class Wear(Contract):
    """Put on a garment held or within reach; the one taken off stays in hand."""

    kind: Literal["wear"] = "wear"
    thing_id: str


class Remember(Contract):
    """Keep one of your own records in your notebook: ``seq`` is the ledger
    entry it came from. Making a note takes no time, and nobody sees it."""

    kind: Literal["remember"] = "remember"
    seq: int = Field(ge=1)


type Step = Annotated[
    Move | Take | Drop | Use | ToolStep | Talk | Whisper | Wait | Wear | Remember,
    Field(discriminator="kind"),
]
"""One step of a plan. A tool step is itself one of many shapes, told apart by
the tool it names."""

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
    quote: str | None = Field(default=None, min_length=1, max_length=RECORD_LIMIT)
    author: str | None = None
    recorded_actor: str | None = None
    from_room: str | None = None
    noted: bool = False
    """The holder filed this in their notebook: something found, words received
    in private, testimony. A notebook outlasts the recent memory a prompt holds,
    and what goes in it depends only on what the holder perceived."""

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
    quote: str | None = Field(default=None, min_length=1, max_length=RECORD_LIMIT)
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
    build: Build
    starting_garment: str = Field(min_length=1, max_length=40)
    """What this character was introduced wearing, as the roster says. What
    they wear now is a placement, and may differ."""
    detail: str | None = Field(default=None, min_length=1, max_length=60)
    """A distinctive mark, seen only in bright light and never recorded in dim."""
    secret: str | None = Field(default=None, min_length=1, max_length=200)
    """Something this character would rather nobody knew. Only they are told it;
    anyone else must find it in their letters."""
    position: Position
    alive: bool = True
    asleep: bool = False
    plan: Plan = Field(default_factory=Plan)

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
    inspected: dict[str, dict[str, int]] = Field(default_factory=dict)
    """For each player, the ledger entry of their latest inspection of each
    object. The custody links they traced are frozen in that entry, so what an
    object has been through since is not theirs to know."""
    searched: dict[str, dict[str, int]] = Field(default_factory=dict)
    """For each player, the ledger entry of their latest search of each room. An
    index over the ledger, kept as it grows: what the search found is frozen in
    that entry, so a search is a look at one moment, never a window on the room."""
    replans: dict[str, int] = Field(default_factory=dict)
    kits: dict[str, Kit] = Field(default_factory=dict)
    """Who carries the traitor kit. Anyone not listed has the household's."""
    disguises: dict[str, str] = Field(default_factory=dict)
    """Whose manner each disguised player has taken on, until they change
    garments or the day ends."""
    notebook: dict[str, list[int]] = Field(default_factory=dict)
    """For each player, the entries holding a record they noted. An index over
    the ledger, kept as it grows, and always equal to a replay of it."""
    asides: dict[str, list[int]] = Field(default_factory=dict)
    """For each player, the entries carrying a private line of theirs: a diary
    line, or a reason for silence or for passing. An index over the ledger,
    like the notebook."""
    balance: Balance = Field(default_factory=default_balance)

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
            if isinstance(placement, WornBy):
                if placement.player_id not in self.players:
                    raise ValueError(f"Object {thing_id} is worn by an unknown player.")
        worn = [
            placement.player_id
            for placement in self.placements.values()
            if isinstance(placement, WornBy)
        ]
        if len(set(worn)) != len(worn):
            raise ValueError("Nobody wears two outer garments at once.")
        for label, keys in (
            ("kit", set(self.kits)),
            ("disguise", set(self.disguises) | set(self.disguises.values())),
            ("notebook", set(self.notebook)),
            ("aside", set(self.asides)),
            ("search", set(self.searched)),
            ("inspection", set(self.inspected)),
        ):
            if not keys <= set(self.players):
                raise ValueError(f"A {label} names someone who is not in this world.")
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
    be (already degraded, like the text), and ``target`` whom or what the act was
    aimed at when the holder could see that. ``day`` and ``phase`` say when, as
    everyone in the castle keeps the same clock. They restate the record in a
    form a program can read; none of them adds anything the record does not hold.
    """

    ledger_seq: int = Field(ge=1)
    tick: int = Field(ge=0)
    day: int = Field(ge=1)
    phase: CastlePhase
    kind: str = Field(min_length=1, max_length=40)
    sense: Sense
    confidence: Confidence
    channel: Channel
    text: str = Field(min_length=1, max_length=400)
    quote: str | None = Field(default=None, min_length=1, max_length=RECORD_LIMIT)
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
    written: bool | None = None
    """For a sheet of paper: whether anything is written on it, which anyone
    looking can see. What it says takes reading."""


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


class Presence(Contract):
    """Someone else in the room, as far as the light lets the viewer make out.

    Bright: who it is, what they wear and any distinctive detail. Dim: only the
    garment. Dark: a silhouette. ``player_id`` is set only when the viewer can see
    who it is, so a dim or dark room never hands out a name.
    """

    player_id: str | None = None
    label: str = Field(min_length=1, max_length=80)
    garment: str | None = None
    detail: str | None = None
    asleep: bool = False


class ScopedObservation(Contract):
    """Everything one player may be told, and nothing else."""

    viewer_id: str
    tick: int = Field(ge=0)
    day: int = Field(ge=1)
    phase: CastlePhase
    room: RoomView | None = None
    transit: TransitView | None = None
    present: list[Presence] = Field(default_factory=list)
    wearing: ThingView | None = None
    secret: str | None = None
    """The viewer's own secret, and nobody else's."""
    searched_here: bool = False
    visible_objects: list[ThingView] = Field(default_factory=list)
    carrying: list[ThingView] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    recent: list[ObservationItem] = Field(default_factory=list)
    evidence: list[ObservationItem] = Field(default_factory=list)
    """The viewer's notebook: what they found, were told in private, or heard
    as testimony, however long ago."""
    plan_remaining: list[Step] = Field(default_factory=list)
    valid_actions: list[str] = Field(default_factory=list)
