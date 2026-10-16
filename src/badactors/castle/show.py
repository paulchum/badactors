"""The audience feed: one game, laid out for everything the audience watches.

SPEC §8 makes the event log the single source for the viewer and for clips.
:func:`audience_feed` turns a stored :class:`~badactors.castle.play.GameRecord`
into one versioned JSON document that every audience surface reads. It is a
pure function of the record: the record is replayed, which is deterministic, so
the roles, the recruits and the fates come from the rules that played the game,
and the replay is held to the record's own ledger before anything is read.

Each ledger entry becomes one event: its public text and words, its private
fields (a diary line, a reason for silence or for passing, a plan's goal, a
declared intent, what an exit interview told and guessed), every witness record
with its confidence, and every roll with the chance it was rolled against. Beside
the events sit what a viewer needs to draw the game: the rooms and their light,
where every player stood at the end of every tick (walking, asleep and dead
included), each dinner's seating plan, and who played each seat.

Nothing here reaches a player. The feed is the audience's, and holds the truth.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field

from badactors.castle.contracts import (
    AtRoom,
    Build,
    CastlePhase,
    Channel,
    Confidence,
    Contract,
    Intent,
    LedgerEntry,
    Lighting,
    Sense,
    WorldState,
)
from badactors.castle.game import GameState
from badactors.castle.interview import ExitWhy, why_out
from badactors.castle.play import (
    GameRecord,
    Outcome,
    ReplayDiverged,
    is_model,
    replay,
    start_game,
)
from badactors.castle.rules import Role

FEED: Literal["badactors.castle.feed"] = "badactors.castle.feed"
FEED_VERSION: Literal[1] = 1

PlayedBy = Literal["model", "bot", "unknown"]


class FeedGame(Contract):
    world_id: str
    castle_id: str
    castle: str
    seed: int
    outcome: Outcome
    rated: bool
    winner: Role | None
    reference_bots: bool
    """No seat was played by a model. Every renderer says so."""
    days: int = Field(ge=1)
    ticks: int = Field(ge=0)
    pot: int = Field(ge=0)
    payouts: dict[str, int]
    starting_traitors: list[str]


class FeedSeat(Contract):
    """One seat, and who played it. ``model`` is set only when a model did: a
    seat a reference bot played names no model, so its play is never read as
    one's."""

    id: str
    seat: int = Field(ge=1)
    character: str
    agent: str
    played_by: PlayedBy
    model: str | None = None
    build: Build
    garment: str
    detail: str | None = None
    role: Role
    """The role the seat ended the game with."""
    traitor_from: int | None = None
    """The entry that made this seat a traitor: the draw, or a recruitment."""
    recruited_by: str | None = None
    fate: ExitWhy
    out_at: int | None = None
    """The entry that took them out of the game."""
    decisions: int = Field(ge=0)
    refused: int = Field(ge=0)
    failed: int = Field(ge=0)


class FeedRoom(Contract):
    id: str
    name: str
    lighting: Lighting
    capacity: int
    camera: bool
    private: bool


class FeedPassage(Contract):
    rooms: list[str]
    travel_ticks: int


class FeedThing(Contract):
    id: str
    kind: str
    name: str


class FeedWitness(Contract):
    """What one player wrote down of an event, as the light let them."""

    player: str
    sense: Sense
    confidence: Confidence
    channel: Channel
    text: str
    quote: str | None = None
    author: str | None = None
    recorded_actor: str | None = None
    from_room: str | None = None
    noted: bool = False


class FeedRoll(Contract):
    """One recorded roll. ``chance`` is what it had to come in under; a roll
    with none picked from a list."""

    cursor: int
    purpose: str
    value: float
    chance: float | None = None
    success: bool | None = None


class FeedEvent(Contract):
    """One ledger entry, as the audience may see all of it."""

    seq: int = Field(ge=1)
    tick: int = Field(ge=0)
    day: int = Field(ge=1)
    phase: CastlePhase
    type: str
    actor: str | None = None
    room: str | None = None
    light: Lighting | None = None
    text: str
    quote: str | None = None
    payload: dict[str, str] = Field(default_factory=dict)
    private: dict[str, str] = Field(default_factory=dict)
    """The actor's own private lines: ``diary``, ``reason``, ``goal``,
    ``thoughts``. Declared intents, told truths and guesses are parsed below."""
    intent: Intent | None = None
    told: list[str] | None = None
    guesses: dict[str, str] | None = None
    witnesses: list[FeedWitness] = Field(default_factory=list)
    rolls: list[FeedRoll] = Field(default_factory=list)


class Whereabouts(Contract):
    """Where one player was at the end of a tick: in a room, or on the way
    between two (``origin`` and ``destination``)."""

    room: str | None = None
    origin: str | None = None
    destination: str | None = None
    asleep: bool = False
    alive: bool = True


class Frame(Contract):
    tick: int = Field(ge=0)
    day: int = Field(ge=1)
    phase: CastlePhase
    seats: dict[str, Whereabouts]


class Seating(Contract):
    """One dinner's seats, round the table, in order."""

    day: int = Field(ge=1)
    seq: int = Field(ge=1)
    order: list[str]


class Feed(Contract):
    feed: Literal["badactors.castle.feed"] = FEED
    version: Literal[1] = FEED_VERSION
    game: FeedGame
    seats: list[FeedSeat]
    rooms: list[FeedRoom]
    passages: list[FeedPassage]
    things: list[FeedThing]
    events: list[FeedEvent]
    positions: list[Frame]
    """One frame for every tick of the game, from the first to the last."""
    seatings: list[Seating]


# ------------------------------------------------------------------ building


def audience_feed(stored: GameRecord) -> Feed:
    """The audience feed for one stored game. Same record, same feed."""
    game = replay(stored)
    if game.world.ledger != stored.ledger:
        raise ReplayDiverged("The record's ledger is not the one its game writes.")
    start = start_game(
        stored.castle,
        stored.cast,
        seed=stored.seed,
        config=stored.config,
        world_id=stored.world_id,
    ).world
    world = game.world
    ledger = stored.ledger
    return Feed(
        game=FeedGame(
            world_id=stored.world_id,
            castle_id=stored.castle.id,
            castle=stored.castle.name,
            seed=stored.seed,
            outcome=stored.outcome,
            rated=stored.rated,
            winner=stored.winner,
            reference_bots=stored.reference_bots,
            days=world.day,
            ticks=world.tick,
            pot=game.pot,
            payouts=dict(game.payouts),
            starting_traitors=list(game.starting_traitors),
        ),
        seats=_seats(stored, game),
        rooms=[
            FeedRoom(
                id=room.id,
                name=room.name,
                lighting=room.lighting,
                capacity=room.capacity,
                camera=room.camera,
                private=room.private,
            )
            for room in world.map.rooms
        ],
        passages=[
            FeedPassage(rooms=list(passage.rooms), travel_ticks=passage.travel_ticks)
            for passage in world.map.passages
        ],
        things=[
            FeedThing(id=thing.id, kind=thing.kind, name=thing.name)
            for thing in world.map.things
        ],
        events=[_event(world, entry) for entry in ledger],
        positions=positions(start, ledger),
        seatings=[
            Seating(
                day=entry.day,
                seq=entry.seq,
                order=[pid for pid in entry.payload["seating"].split(",") if pid],
            )
            for entry in ledger
            if entry.type == "seating"
        ],
    )


def played_by(agent: str) -> PlayedBy:
    if is_model(agent):
        return "model"
    if agent.startswith("bot:"):
        return "bot"
    return "unknown"


def _seats(stored: GameRecord, game: GameState) -> list[FeedSeat]:
    world = game.world
    recruiters = {
        offer.target: offer.recruiter
        for offer in game.offers
        if offer.answer == "accepted"
    }
    out_at = {
        entry.payload["player"]: entry.seq
        for entry in stored.ledger
        if entry.type in ("death", "banished")
    }
    failed = [entry.actor_id for entry in stored.ledger if entry.type == "turn_failed"]
    seats: list[FeedSeat] = []
    for index, pid in enumerate(world.seats, start=1):
        player = world.players[pid]
        agent = stored.agents[pid]
        asked = [d for d in stored.decisions if d.player_id == pid]
        seats.append(
            FeedSeat(
                id=pid,
                seat=index,
                character=player.name,
                agent=agent,
                played_by=played_by(agent),
                model=agent.removeprefix("model:") if is_model(agent) else None,
                build=player.build,
                garment=player.starting_garment,
                detail=player.detail,
                role=game.roles[pid],
                traitor_from=game.traitor_since.get(pid),
                recruited_by=recruiters.get(pid),
                fate=why_out(game, pid),
                out_at=out_at.get(pid),
                decisions=len(asked),
                refused=sum(1 for d in asked if d.refused),
                failed=failed.count(pid),
            )
        )
    return seats


_PARSED = frozenset({"intent", "told", "guesses"})


def _event(world: WorldState, entry: LedgerEntry) -> FeedEvent:
    private = entry.private
    intent = private.get("intent")
    told = private.get("told")
    guesses = private.get("guesses")
    light = world.map.room(entry.room_id).lighting if entry.room_id else None
    return FeedEvent(
        seq=entry.seq,
        tick=entry.tick,
        day=entry.day,
        phase=entry.phase,
        type=entry.type,
        actor=entry.actor_id,
        room=entry.room_id,
        light=light,
        text=entry.text,
        quote=entry.quote,
        payload=dict(entry.payload),
        private={key: value for key, value in private.items() if key not in _PARSED},
        intent=Intent.parse(intent) if intent is not None else None,
        told=json.loads(told) if told is not None else None,
        guesses=json.loads(guesses) if guesses is not None else None,
        witnesses=[
            FeedWitness(
                player=record.player_id,
                sense=record.sense,
                confidence=record.confidence,
                channel=record.channel,
                text=record.text,
                quote=record.quote,
                author=record.author,
                recorded_actor=record.recorded_actor,
                from_room=record.from_room,
                noted=record.noted,
            )
            for record in entry.witnesses
        ],
        rolls=[
            FeedRoll(
                cursor=roll.cursor,
                purpose=roll.purpose,
                value=roll.value,
                chance=roll.threshold,
                success=roll.success,
            )
            for roll in entry.rolls
        ],
    )


# ----------------------------------------------------------------- positions


def positions(start: WorldState, ledger: Sequence[LedgerEntry]) -> list[Frame]:
    """Where everyone was at the end of every tick, from the ledger alone.

    A player moves only by setting out and arriving, or by turning back at a
    full door, and each is an entry; so is falling asleep, waking, dying and
    being banished. Folding them from where the cast began gives the whole
    game's movement, which a test holds to the world's own state at every
    tick."""
    here = {pid: _standing(start, pid) for pid in start.seats}
    frames: list[Frame] = []
    day, phase = start.day, start.phase
    last = ledger[-1].tick if ledger else start.tick
    index = 0
    for tick in range(start.tick, last + 1):
        while index < len(ledger) and ledger[index].tick <= tick:
            entry = ledger[index]
            index += 1
            day, phase = entry.day, entry.phase
            _fold(here, entry)
        frames.append(Frame(tick=tick, day=day, phase=phase, seats=dict(here)))
    return frames


def _standing(world: WorldState, player_id: str) -> Whereabouts:
    player = world.players[player_id]
    position = player.position
    if isinstance(position, AtRoom):
        return Whereabouts(
            room=position.room_id, asleep=player.asleep, alive=player.alive
        )
    return Whereabouts(
        origin=position.origin,
        destination=position.destination,
        asleep=player.asleep,
        alive=player.alive,
    )


def _fold(here: dict[str, Whereabouts], entry: LedgerEntry) -> None:
    """Apply one entry to everyone's whereabouts. Each change is a new value,
    so the frames already taken keep what they held."""
    actor = entry.actor_id
    kind = entry.type
    if kind in ("death", "banished"):
        pid = entry.payload["player"]
        here[pid] = here[pid].model_copy(update={"alive": False, "asleep": False})
    elif actor is None or actor not in here:
        return
    elif kind == "move_depart":
        here[actor] = here[actor].model_copy(
            update={
                "room": None,
                "origin": entry.room_id,
                "destination": entry.payload["destination"],
            }
        )
    elif kind == "move_arrive" or (kind == "blocked" and here[actor].room is None):
        # A player still on the way who is blocked has turned back at a full
        # door: the entry is written in the room they came back to.
        here[actor] = here[actor].model_copy(
            update={"room": entry.room_id, "origin": None, "destination": None}
        )
    elif kind == "sleep":
        here[actor] = here[actor].model_copy(update={"asleep": True})
    elif kind == "wake":
        here[actor] = here[actor].model_copy(update={"asleep": False})


# ----------------------------------------------------------------------- files


def save_feed(feed: Feed, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(feed.model_dump_json())


def load_feed(path: str | Path) -> Feed:
    return Feed.model_validate_json(Path(path).read_text())


__all__ = [
    "FEED",
    "FEED_VERSION",
    "Feed",
    "FeedEvent",
    "FeedGame",
    "FeedPassage",
    "FeedRoll",
    "FeedRoom",
    "FeedSeat",
    "FeedThing",
    "FeedWitness",
    "Frame",
    "PlayedBy",
    "Seating",
    "Whereabouts",
    "audience_feed",
    "load_feed",
    "played_by",
    "positions",
    "save_feed",
]
