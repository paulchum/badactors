"""Turn one thing that happened into the records each player keeps of it.

Degradation happens here, at write time, and never later. Bright light records
who it was. A witness in a dim room gets a seeded roll: on a success it records
the name but still labels the memory ``unsure``, because the person genuinely
isn't certain; on a failure it records only the outer garment — "someone in a
grey coat" — never the build or a distinctive detail, and every garment is shared
by several of the household, so a dim record never names one person. A dark room
records a silhouette, "someone". That string is the only thing a prompt will ever
see; the actor's real name stays on the ledger entry beside it. Confidence is
honest — it tracks how the record was made, not whether it happens to be right.

Disguise works through both rolls. Wear someone else's kind of garment and a dim
witness whose roll fails writes down that garment, not yours. Take on their manner
as well, with the disguise tool, and a dim witness whose roll succeeds — who
thinks they know who it is — may be fooled by a second roll into writing down the
person you are passing as. Bright light always names you.

Hearing is deliberately thin. An adjacent room learns an event's kind and the room
it came from, never who did it or what was said, because a leaked identity through
a wall would defeat disguise and most of the evidence game with it.

A sleeper records nothing at all. They still take up room, and anyone awake
beside them sees them lying there, but what happens around them passes them by.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from badactors.castle.contracts import (
    GARMENT,
    AtRoom,
    CastleMap,
    Channel,
    Confidence,
    InContainer,
    InRoom,
    Lighting,
    Placement,
    Presence,
    RngDraw,
    Thing,
    WitnessRecord,
    WorldState,
    WornBy,
)
from badactors.castle.rng import draw

_AUDIBLE: dict[str, str] = {
    "move_depart": "footsteps leaving",
    "move_arrive": "footsteps arriving",
    "take": "something being lifted",
    "drop": "something set down",
    "stow": "a lid closing",
    "wear": "cloth rustling",
    "use": "something being handled",
    "search": "furniture being moved",
    "talk": "voices",
}

_CONFIDENCE_BY_LIGHT: dict[Lighting, Confidence] = {
    "bright": "clear",
    "dim": "unsure",
    "dark": "glimpse",
}


@dataclass
class Perception:
    """Witness records for one event, plus the rolls that shaped them."""

    records: list[WitnessRecord] = field(default_factory=list)
    rolls: list[RngDraw] = field(default_factory=list)
    cursor: int = 0

    def note(self, *player_ids: str) -> None:
        """File these holders' records in their notebooks."""
        wanted = set(player_ids)
        self.records = [
            record.model_copy(update={"noted": True})
            if record.player_id in wanted
            else record
            for record in self.records
        ]

    def put(self, record: WitnessRecord) -> None:
        """Give one holder this record in place of whatever they had."""
        kept = [held for held in self.records if held.player_id != record.player_id]
        self.records = [*kept, record]


def occupants(state: WorldState, room_id: str) -> list[str]:
    """Living players standing in ``room_id``. Players in transit are nowhere."""
    return [
        player_id
        for player_id in state.seats
        if (player := state.players[player_id]).alive
        and isinstance(player.position, AtRoom)
        and player.position.room_id == room_id
    ]


def awake_in(state: WorldState, room_id: str) -> list[str]:
    """Occupants of ``room_id`` who are awake, and so can see and hear."""
    return [
        player_id
        for player_id in occupants(state, room_id)
        if not state.players[player_id].asleep
    ]


def perceive(
    state: WorldState,
    *,
    kind: str,
    actor_id: str,
    room_id: str,
    describe: Callable[[str], str],
    own_text: str | None = None,
    channel: Channel = "direct",
    quote: str | None = None,
    speaker_count: int = 1,
    silent: bool = False,
    private: bool = False,
) -> Perception:
    """Build every record of one event.

    ``describe`` renders the actor as a witness recorded them: it receives the
    recorded name, a physical description, or "someone". It must never render
    ``quote``: words travel in their own field, attributed to whoever the witness
    took the speaker to be, and a wall passes on only the channel.
    """
    cursor = state.rng_cursor
    perception = Perception(cursor=cursor)
    actor = state.players[actor_id]
    perception.records.append(
        WitnessRecord(
            player_id=actor_id,
            sense="own",
            confidence="clear",
            channel=channel,
            text=own_text if own_text is not None else describe(actor.name),
            quote=quote,
            author=actor_id if quote is not None else None,
            recorded_actor=actor_id,
            from_room=room_id,
        )
    )
    if private:
        return perception

    lighting = state.map.room(room_id).lighting
    for witness_id in awake_in(state, room_id):
        if witness_id == actor_id:
            continue
        identity, rolls = _identify(
            state,
            cursor=perception.cursor,
            witness_id=witness_id,
            actor_id=actor_id,
            lighting=lighting,
        )
        perception.rolls.extend(rolls)
        perception.cursor += len(rolls)
        label = (
            state.players[identity].name
            if identity is not None
            else unnamed(state, actor_id, lighting)
        )
        perception.records.append(
            WitnessRecord(
                player_id=witness_id,
                sense="saw",
                confidence=_CONFIDENCE_BY_LIGHT[lighting],
                channel=channel,
                text=describe(label),
                quote=quote,
                author=identity if quote is not None else None,
                recorded_actor=identity,
                from_room=room_id,
            )
        )

    if not silent:
        perception.records.extend(
            _overheard(
                state,
                kind,
                room_id,
                channel=channel,
                speaker_count=speaker_count,
            )
        )
    return perception


def _identify(
    state: WorldState,
    *,
    cursor: int,
    witness_id: str,
    actor_id: str,
    lighting: Lighting,
) -> tuple[str | None, list[RngDraw]]:
    """Who this witness writes down, and the rolls that decided it."""
    if lighting == "bright":
        return actor_id, []
    if lighting == "dark":
        return None, []
    roll = draw(
        state.seed,
        cursor,
        f"recognize:{witness_id}:{actor_id}",
        threshold=state.balance.dim_recognition,
    )
    if not roll.success:
        return None, [roll]
    passing_as = state.disguises.get(actor_id)
    if (
        passing_as is None
        or passing_as == witness_id
        or not state.players[passing_as].alive
    ):
        return actor_id, [roll]
    fooled = draw(
        state.seed,
        cursor + 1,
        f"disguise:{witness_id}:{actor_id}",
        threshold=state.balance.tools.disguise.success,
    )
    return (passing_as if fooled.success else actor_id), [roll, fooled]


def seen_as(state: WorldState, player_id: str, lighting: Lighting) -> str:
    """How someone looks to a standing glance in this light, with no roll:
    their name in bright light, otherwise as :func:`unnamed` has it."""
    if lighting == "bright":
        return state.players[player_id].name
    return unnamed(state, player_id, lighting)


def unnamed(state: WorldState, player_id: str, lighting: Lighting) -> str:
    """How someone not recognised is written down: their garment in dim light, a
    silhouette in the dark. Never their build or a distinctive detail."""
    if lighting == "dark":
        return "someone"
    garment = worn_garment(state, player_id)
    if garment is None:
        return "someone in shirtsleeves"
    return f"someone in a {garment.name}"


def worn_garment(state: WorldState, player_id: str) -> Thing | None:
    for thing in state.map.things:
        placement = state.placements.get(thing.id)
        if isinstance(placement, WornBy) and placement.player_id == player_id:
            return thing
    return None


def garments(state: WorldState) -> list[Thing]:
    return [thing for thing in state.map.things if thing.kind == GARMENT]


def presence(state: WorldState, viewer_id: str, room_id: str) -> list[Presence]:
    """Everyone else in the room, as far as the light lets ``viewer_id`` see.

    No rolls: a standing look at the room never names anyone in dim light, even
    someone a roll recognised a moment ago. Unnamed figures are listed in label
    order, so not even seat order hints at who is who.
    """
    if state.players[viewer_id].asleep:
        return []
    lighting = state.map.room(room_id).lighting
    others = [pid for pid in occupants(state, room_id) if pid != viewer_id]
    seen: list[Presence] = []
    for pid in others:
        player = state.players[pid]
        garment = worn_garment(state, pid)
        if lighting == "bright":
            seen.append(
                Presence(
                    player_id=pid,
                    label=player.name,
                    garment=garment.name if garment is not None else None,
                    detail=player.detail,
                    asleep=player.asleep,
                )
            )
        else:
            seen.append(
                Presence(
                    label=unnamed(state, pid, lighting),
                    garment=(
                        garment.name
                        if lighting == "dim" and garment is not None
                        else None
                    ),
                    asleep=player.asleep,
                )
            )
    if lighting != "bright":
        seen.sort(key=lambda figure: (figure.label, figure.asleep))
    return seen


def _overheard(
    state: WorldState,
    kind: str,
    room_id: str,
    *,
    channel: Channel,
    speaker_count: int,
) -> list[WitnessRecord]:
    phrase = _AUDIBLE.get(kind)
    if phrase is None:
        return []
    if kind == "talk":
        phrase = f"{_count_word(speaker_count)} in conversation"
    source = state.map.room(room_id).name
    records: list[WitnessRecord] = []
    for neighbor in state.map.neighbors(room_id):
        for listener_id in awake_in(state, neighbor):
            records.append(
                WitnessRecord(
                    player_id=listener_id,
                    sense="heard",
                    confidence="glimpse",
                    channel=channel,
                    text=f"{phrase} in the {source}",
                    recorded_actor=None,
                    from_room=room_id,
                )
            )
    return records


def _count_word(speakers: int) -> str:
    if speakers <= 1:
        return "a voice"
    if speakers == 2:
        return "two voices"
    return "several voices"


def visible_things(state: WorldState, viewer_id: str, room_id: str) -> list[str]:
    """Objects on show in ``room_id``, plus what this viewer's latest search of it
    found that is still where they found it.

    A search turns up the objects hidden in a room and the contents of the
    containers standing in it, as they were at that moment. It is a look, not a
    window: anything hidden or stowed there afterwards stays out of sight until
    the next search, and something found and since moved is simply not where it
    was. Someone who has not searched sees neither, so a stowed object is as
    private as a hidden one.
    """
    finds = search_finds(state, viewer_id, room_id)
    out: list[str] = []
    for thing in state.map.things:
        placement = state.placements.get(thing.id)
        if isinstance(placement, InRoom) and not placement.hidden:
            if placement.room_id == room_id:
                out.append(thing.id)
        elif thing.id in finds and finds[thing.id] == spot(placement):
            out.append(thing.id)
    return sorted(out)


def spot(placement: Placement | None) -> str | None:
    """Where a search turns something up: hidden loose in a room, or inside a
    container. ``None`` for anything in plain sight or on a person."""
    if isinstance(placement, InRoom) and placement.hidden:
        return f"hidden:{placement.room_id}"
    if isinstance(placement, InContainer):
        return f"in:{placement.thing_id}"
    return None


def search_finds(state: WorldState, viewer_id: str, room_id: str) -> dict[str, str]:
    """What this viewer's latest search of ``room_id`` turned up, and the spot
    each was found in, as the search's entry froze it."""
    seq = state.searched.get(viewer_id, {}).get(room_id)
    if seq is None:
        return {}
    return unpack_finds(state.ledger[seq - 1].payload.get("finds", ""))


def pack_finds(finds: dict[str, str]) -> str:
    """A search's finds as its entry's payload keeps them."""
    return ",".join(f"{thing_id}={where}" for thing_id, where in sorted(finds.items()))


def unpack_finds(packed: str) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in packed.split(",") if pair)


def room_of(state: WorldState, thing_id: str) -> str | None:
    """The room an object is ultimately in, through any containers.

    ``None`` when it is held, or when its containers never reach a room.
    """
    seen: set[str] = set()
    current = thing_id
    while current not in seen:
        seen.add(current)
        placement = state.placements.get(current)
        if isinstance(placement, InRoom):
            return placement.room_id
        if not isinstance(placement, InContainer):
            return None
        current = placement.thing_id
    return None


def carried_things(state: WorldState, holder_id: str) -> list[str]:
    return sorted(
        thing_id
        for thing_id, placement in state.placements.items()
        if placement.where == "held" and placement.player_id == holder_id
    )


def audible_kinds() -> frozenset[str]:
    return frozenset(_AUDIBLE)


def describe_room(castle: CastleMap, room_id: str) -> str:
    room = castle.room(room_id)
    return f"the {room.name}"


__all__ = [
    "Perception",
    "audible_kinds",
    "awake_in",
    "carried_things",
    "describe_room",
    "garments",
    "occupants",
    "pack_finds",
    "perceive",
    "presence",
    "room_of",
    "search_finds",
    "seen_as",
    "spot",
    "unnamed",
    "unpack_finds",
    "visible_things",
    "worn_garment",
]
