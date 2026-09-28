"""The append-only record, and custody derived from it.

``WorldState.placements`` is a cache. :func:`replay_placements` rebuilds the same
mapping by folding the custody events in the ledger, and a property test holds the
two equal at every tick. That is what lets the rest of the system treat custody as
a ledger query — an inspection returns a slice of :func:`custody_chain`, so what a
player is told about an object's history and what actually happened cannot drift
apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from badactors.castle.contracts import (
    CustodyLink,
    HeldBy,
    InContainer,
    InRoom,
    LedgerEntry,
    Placement,
    RngDraw,
    WitnessRecord,
    WorldState,
)
from badactors.castle.perceive import Perception

CUSTODY_TYPES: frozenset[str] = frozenset({"placed", "take", "drop", "stow"})
"""Entry types that move an object. Nothing else may change a placement."""


def append(
    state: WorldState,
    *,
    type: str,
    text: str,
    actor_id: str | None = None,
    room_id: str | None = None,
    quote: str | None = None,
    payload: Mapping[str, str] | None = None,
    private: Mapping[str, str] | None = None,
    witnesses: Sequence[WitnessRecord] = (),
    rolls: Sequence[RngDraw] = (),
    cursor: int | None = None,
) -> LedgerEntry:
    """Append one entry in place and return it.

    ``cursor`` advances the world's roll cursor; pass the value a
    :class:`~badactors.castle.perceive.Perception` reached so consumed rolls are
    never handed out twice.
    """
    state.ledger_seq += 1
    entry = LedgerEntry(
        seq=state.ledger_seq,
        tick=state.tick,
        day=state.day,
        phase=state.phase,
        type=type,
        actor_id=actor_id,
        room_id=room_id,
        text=text,
        quote=quote,
        payload=dict(payload or {}),
        private=dict(private or {}),
        witnesses=list(witnesses),
        rolls=list(rolls),
    )
    state.ledger.append(entry)
    if cursor is not None:
        state.rng_cursor = cursor
    return entry


def append_perceived(
    state: WorldState,
    perception: Perception,
    *,
    type: str,
    text: str,
    actor_id: str,
    room_id: str,
    quote: str | None = None,
    payload: Mapping[str, str] | None = None,
    private: Mapping[str, str] | None = None,
) -> LedgerEntry:
    """Append an entry carrying the records and rolls a perception produced."""
    return append(
        state,
        type=type,
        text=text,
        actor_id=actor_id,
        room_id=room_id,
        quote=quote,
        payload=payload,
        private=private,
        witnesses=perception.records,
        rolls=perception.rolls,
        cursor=perception.cursor,
    )


def move_object(
    state: WorldState,
    thing_id: str,
    placement: Placement,
) -> None:
    """Update the custody cache. Only call this beside a custody ledger entry."""
    state.placements[thing_id] = placement


def replay_placements(ledger: Iterable[LedgerEntry]) -> dict[str, Placement]:
    """Rebuild current custody from the ledger alone."""
    placements: dict[str, Placement] = {}
    for entry in ledger:
        if entry.type not in CUSTODY_TYPES:
            continue
        thing_id = entry.payload.get("thing_id")
        if thing_id is None:
            continue
        placements[thing_id] = _placement_from_payload(entry)
    return placements


def custody_chain(
    ledger: Sequence[LedgerEntry],
    thing_id: str,
    *,
    limit: int | None = None,
) -> list[CustodyLink]:
    """Every recorded move of one object, oldest first.

    ``limit`` keeps only the most recent links, which is how a partial chain is
    served without inventing a second source of truth.
    """
    links = [
        CustodyLink(ledger_seq=entry.seq, tick=entry.tick, text=entry.text)
        for entry in ledger
        if entry.type in CUSTODY_TYPES and entry.payload.get("thing_id") == thing_id
    ]
    return links if limit is None else links[-limit:]


def witnessed_by(
    ledger: Sequence[LedgerEntry],
    player_id: str,
    *,
    since_seq: int = 0,
) -> list[tuple[LedgerEntry, WitnessRecord]]:
    """Entries this player has a record of, with that record."""
    out: list[tuple[LedgerEntry, WitnessRecord]] = []
    for entry in ledger:
        if entry.seq <= since_seq:
            continue
        record = entry.record_for(player_id)
        if record is not None:
            out.append((entry, record))
    return out


def _placement_from_payload(entry: LedgerEntry) -> Placement:
    where = entry.payload.get("where")
    if where == "held":
        return HeldBy(player_id=entry.payload["holder_id"])
    if where == "container":
        return InContainer(thing_id=entry.payload["container_id"])
    if where == "room":
        return InRoom(
            room_id=entry.payload["room"],
            hidden=entry.payload.get("hidden") == "true",
        )
    raise ValueError(f"Custody entry {entry.seq} has no usable placement.")


def placement_payload(thing_id: str, placement: Placement) -> dict[str, str]:
    """Encode a placement so :func:`replay_placements` can rebuild it exactly."""
    payload = {"thing_id": thing_id, "where": placement.where}
    if isinstance(placement, InRoom):
        payload["room"] = placement.room_id
        payload["hidden"] = "true" if placement.hidden else "false"
    elif isinstance(placement, HeldBy):
        payload["holder_id"] = placement.player_id
    else:
        payload["container_id"] = placement.thing_id
    return payload


def seed_placements(state: WorldState) -> None:
    """Record the authored starting custody so a replay has a first link."""
    for thing_id in sorted(state.placements):
        placement = state.placements[thing_id]
        thing = next(item for item in state.map.things if item.id == thing_id)
        append(
            state,
            type="placed",
            text=f"{thing.name} starts {_where_phrase(state, placement)}.",
            room_id=placement.room_id if isinstance(placement, InRoom) else None,
            payload=placement_payload(thing_id, placement),
        )


def _where_phrase(state: WorldState, placement: Placement) -> str:
    if isinstance(placement, InRoom):
        room = state.map.room(placement.room_id)
        hidden = " out of sight" if placement.hidden else ""
        return f"in the {room.name}{hidden}"
    if isinstance(placement, HeldBy):
        return f"in {state.players[placement.player_id].name}'s hands"
    container = next(item for item in state.map.things if item.id == placement.thing_id)
    return f"inside the {container.name}"


__all__ = [
    "CUSTODY_TYPES",
    "append",
    "append_perceived",
    "custody_chain",
    "move_object",
    "placement_payload",
    "replay_placements",
    "seed_placements",
    "witnessed_by",
]
