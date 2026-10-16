"""The append-only record, and custody derived from it.

``WorldState.placements`` is a cache. :func:`replay_placements` rebuilds the same
mapping by folding the custody events in the ledger, and a property test holds the
two equal at every tick. That is what lets the rest of the system treat custody as
a ledger query — an inspection returns a slice of :func:`custody_chain`, so what a
player is told about an object's history and what actually happened cannot drift
apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence

from badactors.castle.contracts import (
    CustodyLink,
    HeldBy,
    InContainer,
    InRoom,
    LedgerEntry,
    Offstage,
    Placement,
    RngDraw,
    WitnessRecord,
    WorldState,
    WornBy,
)
from badactors.castle.perceive import Perception

CUSTODY_TYPES: frozenset[str] = frozenset(
    {
        "placed",
        "take",
        "drop",
        "stow",
        "awarded",
        "spent",
        "take_off",
        "wear",
        "plant",
        "steal",
        "destroyed",
    }
)
"""Entry types that move an object. Nothing else may change a placement.

``awarded`` and ``spent`` are the Steward's: an object handed out of its keeping
into a player's hands, and one taken back out of play once used up. ``take_off``
and ``wear`` are the two halves of changing an outer garment. ``plant`` and
``steal`` move something covertly into or out of a pocket, and their text names
nobody, so the chain shows a gap rather than a culprit. ``destroyed`` is a sheet
burned to ash, out of play.
"""


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
    never handed out twice. Every record marked ``noted`` files the entry in its
    holder's notebook.
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
    for record in entry.witnesses:
        if record.noted:
            state.notebook.setdefault(record.player_id, []).append(entry.seq)
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
    rolls: Sequence[RngDraw] = (),
) -> LedgerEntry:
    """Append an entry carrying the records and rolls a perception produced.
    ``rolls`` are any drawn before it, such as a tool's own, and come first."""
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
        rolls=[*rolls, *perception.rolls],
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
        placements[thing_id] = placement_of(entry)
    return placements


def custody_chain(
    ledger: Sequence[LedgerEntry],
    thing_id: str,
    *,
    limit: int | None = None,
    before: int | None = None,
) -> list[CustodyLink]:
    """Every recorded move of one object, oldest first.

    ``limit`` keeps only the most recent links, which is how a partial chain is
    served without inventing a second source of truth. It reads backwards and
    stops once it has them, so a partial chain costs nothing like a full one.
    ``before`` keeps only the moves recorded ahead of that entry: the chain as
    it stood then.
    """
    links: list[CustodyLink] = []
    for entry in _backwards(ledger, before):
        if limit is not None and len(links) >= limit:
            break
        if entry.type in CUSTODY_TYPES and entry.payload.get("thing_id") == thing_id:
            links.append(
                CustodyLink(ledger_seq=entry.seq, tick=entry.tick, text=entry.text)
            )
    links.reverse()
    return links


def placement_before(
    ledger: Sequence[LedgerEntry], thing_id: str, seq: int
) -> Placement | None:
    """Where an object was when entry ``seq`` was written, from the ledger alone."""
    for entry in _backwards(ledger, seq):
        if entry.type in CUSTODY_TYPES and entry.payload.get("thing_id") == thing_id:
            return placement_of(entry)
    return None


def _backwards(
    ledger: Sequence[LedgerEntry], before: int | None
) -> Iterator[LedgerEntry]:
    """The entries ahead of ``before`` (all of them when ``None``), newest first."""
    end = len(ledger) if before is None else min(len(ledger), before - 1)
    for index in range(end - 1, -1, -1):
        yield ledger[index]


def traced_links(ledger: Sequence[LedgerEntry], seq: int) -> list[CustodyLink]:
    """The custody links an inspection traced, as its entry froze them."""
    packed = ledger[seq - 1].payload.get("links", "")
    links: list[CustodyLink] = []
    for link in packed.split(","):
        if link:
            moved = ledger[int(link) - 1]
            links.append(
                CustodyLink(ledger_seq=moved.seq, tick=moved.tick, text=moved.text)
            )
    return links


def witnessed_by(
    ledger: Sequence[LedgerEntry],
    player_id: str,
    *,
    since_seq: int = 0,
) -> list[tuple[LedgerEntry, WitnessRecord]]:
    """Entries this player has a record of, with that record, oldest first."""
    out: list[tuple[LedgerEntry, WitnessRecord]] = []
    for entry in ledger[since_seq:]:
        record = entry.record_for(player_id)
        if record is not None:
            out.append((entry, record))
    return out


def recent_records(
    ledger: Sequence[LedgerEntry],
    player_id: str,
    limit: int,
) -> list[tuple[LedgerEntry, WitnessRecord]]:
    """This player's ``limit`` most recent records, oldest first.

    The same as the tail of :func:`witnessed_by`, read backwards so that a long
    game costs a prompt only the stretch of ledger its memory reaches back over.
    """
    out: list[tuple[LedgerEntry, WitnessRecord]] = []
    for entry in reversed(ledger):
        if len(out) >= limit:
            break
        record = entry.record_for(player_id)
        if record is not None:
            out.append((entry, record))
    out.reverse()
    return out


def replay_notebook(ledger: Iterable[LedgerEntry]) -> dict[str, list[int]]:
    """Rebuild every player's notebook from the ledger alone."""
    notebook: dict[str, list[int]] = {}
    for entry in ledger:
        for record in entry.witnesses:
            if record.noted:
                notebook.setdefault(record.player_id, []).append(entry.seq)
    return notebook


def replay_searches(ledger: Iterable[LedgerEntry]) -> dict[str, dict[str, int]]:
    """Rebuild every player's latest search of each room from the ledger alone."""
    searched: dict[str, dict[str, int]] = {}
    for entry in ledger:
        if (
            entry.type == "search"
            and entry.payload.get("tool") == "search"
            and entry.actor_id is not None
            and entry.room_id is not None
        ):
            searched.setdefault(entry.actor_id, {})[entry.room_id] = entry.seq
    return searched


def replay_inspections(ledger: Iterable[LedgerEntry]) -> dict[str, dict[str, int]]:
    """Rebuild every player's latest inspection of each object from the ledger
    alone."""
    inspected: dict[str, dict[str, int]] = {}
    for entry in ledger:
        if (
            entry.type == "inspect"
            and entry.payload.get("tool") == "inspect"
            and entry.actor_id is not None
        ):
            inspected.setdefault(entry.actor_id, {})[entry.payload["thing_id"]] = (
                entry.seq
            )
    return inspected


def entry_at(ledger: Sequence[LedgerEntry], seq: int) -> LedgerEntry | None:
    """The entry with this sequence number. Sequence numbers are a gapless run
    from 1, so this is an index, not a search."""
    if 1 <= seq <= len(ledger):
        entry = ledger[seq - 1]
        if entry.seq == seq:
            return entry
    return None


def placement_of(entry: LedgerEntry) -> Placement:
    """Where a custody entry left its object."""
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
    if where == "offstage":
        return Offstage()
    if where == "worn":
        return WornBy(player_id=entry.payload["wearer_id"])
    raise ValueError(f"Custody entry {entry.seq} has no usable placement.")


def placement_payload(thing_id: str, placement: Placement) -> dict[str, str]:
    """Encode a placement so :func:`replay_placements` can rebuild it exactly."""
    payload = {"thing_id": thing_id, "where": placement.where}
    if isinstance(placement, InRoom):
        payload["room"] = placement.room_id
        payload["hidden"] = "true" if placement.hidden else "false"
    elif isinstance(placement, HeldBy):
        payload["holder_id"] = placement.player_id
    elif isinstance(placement, InContainer):
        payload["container_id"] = placement.thing_id
    elif isinstance(placement, WornBy):
        payload["wearer_id"] = placement.player_id
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
    if isinstance(placement, Offstage):
        return "in the Steward's keeping"
    if isinstance(placement, WornBy):
        return f"worn by {state.players[placement.player_id].name}"
    container = next(item for item in state.map.things if item.id == placement.thing_id)
    return f"inside the {container.name}"


__all__ = [
    "CUSTODY_TYPES",
    "append",
    "append_perceived",
    "custody_chain",
    "entry_at",
    "move_object",
    "placement_before",
    "placement_of",
    "placement_payload",
    "recent_records",
    "replay_inspections",
    "replay_notebook",
    "replay_placements",
    "replay_searches",
    "seed_placements",
    "traced_links",
    "witnessed_by",
]
