"""Build what one player may be told, and prove nothing else got in.

:func:`build_observation` reads the authoritative state and emits only material
the viewer has a claim to. :func:`assert_no_leak` then checks the result
*structurally*: every line of memory names the ledger entry it came from, and that
entry must carry a witness record for this viewer whose text matches exactly. A
leak is therefore not a suspicious-looking string but a line with no provenance —
which is why a new kind of leak cannot slip past a checker that was written before
it existed.

Five classes of leak have their own guard, because each is a mistake a plausible
refactor could make:

* **foreign room** — an object or memory from somewhere the viewer has not been;
* **unwitnessed action** — an entry with no record for this viewer;
* **heard identity** — a through-the-wall record naming who did it;
* **another player's plan** — plan steps that are not the viewer's own;
* **uninspected custody chain** — an object's history without an inspection.
"""

from __future__ import annotations

from badactors.castle.contracts import (
    AtRoom,
    InTransit,
    LedgerEntry,
    ObservationItem,
    PlayerState,
    RoomView,
    ScopedObservation,
    ThingView,
    TransitView,
    WitnessRecord,
    WorldState,
)
from badactors.castle.ledger import custody_chain, witnessed_by
from badactors.castle.perceive import carried_things, occupants, visible_things
from badactors.castle.world import valid_actions

RECENT_LIMIT = 40
"""How many of a player's most recent records reach a prompt."""

CUSTODY_LIMIT = 4
"""How much of an inspected object's history an inspection reveals."""


class LeakageError(AssertionError):
    """A scoped observation contains something its viewer has no claim to."""


def build_observation(
    state: WorldState,
    viewer_id: str,
    *,
    recent_limit: int = RECENT_LIMIT,
) -> ScopedObservation:
    viewer = state.players[viewer_id]
    position = viewer.position
    room_view: RoomView | None = None
    transit_view: TransitView | None = None
    present: list[str] = []
    visible: list[ThingView] = []
    facts: list[str] = []
    searched_here = False

    if isinstance(position, AtRoom):
        room = state.map.room(position.room_id)
        room_view = RoomView(
            id=room.id,
            name=room.name,
            description=room.description,
            lighting=room.lighting,
            exits=state.map.neighbors(room.id),
        )
        present = [other for other in occupants(state, room.id) if other != viewer_id]
        visible = [
            _thing_view(state, viewer_id, thing_id)
            for thing_id in visible_things(state, viewer_id, room.id)
        ]
        searched = set(state.searched.get(viewer_id, []))
        searched_here = room.id in searched
        facts = [
            fact.text
            for fact in state.map.facts
            if fact.room_id == room.id and (not room.private or room.id in searched)
        ]
    elif isinstance(position, InTransit):
        transit_view = TransitView(
            origin=position.origin,
            destination=position.destination,
            ticks_remaining=position.ticks_remaining,
        )

    records = witnessed_by(state.ledger, viewer_id)[-recent_limit:]
    return ScopedObservation(
        viewer_id=viewer_id,
        tick=state.tick,
        day=state.day,
        phase=state.phase,
        room=room_view,
        transit=transit_view,
        present=present,
        searched_here=searched_here,
        visible_objects=visible,
        carrying=[
            _thing_view(state, viewer_id, thing_id)
            for thing_id in carried_things(state, viewer_id)
        ],
        known_facts=facts,
        recent=[_item(entry, record) for entry, record in records],
        plan_remaining=list(viewer.plan.steps),
        valid_actions=valid_actions(state, viewer_id),
    )


def assert_no_leak(observation: ScopedObservation, state: WorldState) -> None:
    """Raise :class:`LeakageError` unless every part of ``observation`` is earned."""
    viewer_id = observation.viewer_id
    if viewer_id not in state.players:
        raise LeakageError("Observation names a viewer who is not in this world.")
    viewer = state.players[viewer_id]
    by_seq = {entry.seq: entry for entry in state.ledger}

    _assert_position(observation, viewer)
    for item in observation.recent:
        _assert_item(item, by_seq.get(item.ledger_seq), viewer_id, state)
    _assert_objects(observation, state, viewer_id)
    _assert_plan(observation, state, viewer_id)
    _assert_facts(observation, state, viewer_id)
    _assert_searched(observation, state, viewer_id)


# ------------------------------------------------------------------- leak guards


def _assert_position(observation: ScopedObservation, viewer: PlayerState) -> None:
    position = viewer.position
    if isinstance(position, AtRoom):
        if observation.room is None or observation.room.id != position.room_id:
            raise LeakageError("Observation reports a room the viewer is not in.")
        if observation.transit is not None:
            raise LeakageError("A seated viewer cannot also be in transit.")
        return
    if observation.room is not None:
        raise LeakageError("A viewer in a passage cannot see into a room.")
    if observation.transit is None:
        raise LeakageError("A viewer in a passage must be told they are travelling.")


def _assert_item(
    item: ObservationItem,
    entry: LedgerEntry | None,
    viewer_id: str,
    state: WorldState,
) -> None:
    if entry is None:
        raise LeakageError(
            f"Observation item cites ledger entry {item.ledger_seq}, which does "
            "not exist."
        )
    record = entry.record_for(viewer_id)
    if record is None:
        raise LeakageError(
            f"Observation item cites entry {entry.seq} ({entry.type}), which this "
            "viewer never witnessed."
        )
    if item.text != record.text:
        raise LeakageError(
            f"Observation item for entry {entry.seq} does not match the record "
            "this viewer actually kept."
        )
    if item.channel != record.channel or item.confidence != record.confidence:
        raise LeakageError(
            f"Observation item for entry {entry.seq} misstates how it was perceived."
        )
    if item.tick != entry.tick:
        raise LeakageError(f"Observation item for entry {entry.seq} moves it in time.")
    _assert_hearing(entry, record, item, state)


def _assert_hearing(
    entry: LedgerEntry,
    record: WitnessRecord,
    item: ObservationItem,
    state: WorldState,
) -> None:
    if record.channel != "heard":
        return
    if record.recorded_actor is not None:
        raise LeakageError(
            f"Heard record for entry {entry.seq} names an actor; a wall carries "
            "the kind of event and the room, never who did it."
        )
    actor_id = entry.actor_id
    if actor_id is None:
        return
    actor = state.players.get(actor_id)
    names = {actor_id} | ({actor.name} if actor is not None else set())
    spoken = item.text.casefold()
    if any(name.casefold() in spoken for name in names):
        raise LeakageError(
            f"Heard record for entry {entry.seq} contains the actor's identity."
        )


def _assert_objects(
    observation: ScopedObservation,
    state: WorldState,
    viewer_id: str,
) -> None:
    room = observation.room
    allowed_here = (
        set(visible_things(state, viewer_id, room.id)) if room is not None else set()
    )
    held = set(carried_things(state, viewer_id))
    inspected = set(state.inspected.get(viewer_id, []))

    for view in observation.visible_objects:
        if view.id not in allowed_here:
            raise LeakageError(
                f"Observation shows object {view.id!r}, which is not visible from "
                "where this viewer stands."
            )
        _assert_custody(view, state, inspected)
    for view in observation.carrying:
        if view.id not in held:
            raise LeakageError(
                f"Observation says the viewer carries {view.id!r}, which they do not."
            )
        _assert_custody(view, state, inspected)


def _assert_custody(
    view: ThingView,
    state: WorldState,
    inspected: set[str],
) -> None:
    if view.custody is None:
        return
    if view.id not in inspected:
        raise LeakageError(
            f"Observation reveals the custody chain of {view.id!r} without the "
            "viewer having inspected it."
        )
    truth = {link.ledger_seq for link in custody_chain(state.ledger, view.id)}
    invented = sorted({link.ledger_seq for link in view.custody} - truth)
    if invented:
        raise LeakageError(
            f"Custody chain for {view.id!r} cites entries {invented} that never "
            "moved it."
        )


def _assert_plan(
    observation: ScopedObservation,
    state: WorldState,
    viewer_id: str,
) -> None:
    own = [step.model_dump(mode="json") for step in state.players[viewer_id].plan.steps]
    shown = [step.model_dump(mode="json") for step in observation.plan_remaining]
    if shown != own:
        raise LeakageError("Observation shows a plan that is not the viewer's own.")


def _assert_searched(
    observation: ScopedObservation,
    state: WorldState,
    viewer_id: str,
) -> None:
    room = observation.room
    here = room is not None and room.id in state.searched.get(viewer_id, [])
    if observation.searched_here != here:
        raise LeakageError(
            "Observation misstates whether this viewer has searched this room."
        )


def _assert_facts(
    observation: ScopedObservation,
    state: WorldState,
    viewer_id: str,
) -> None:
    room = observation.room
    if not observation.known_facts:
        return
    if room is None:
        raise LeakageError("A viewer in a passage cannot read facts in a room.")
    searched = set(state.searched.get(viewer_id, []))
    private = state.map.room(room.id).private
    allowed = {
        fact.text
        for fact in state.map.facts
        if fact.room_id == room.id and (not private or room.id in searched)
    }
    extra = sorted(set(observation.known_facts) - allowed)
    if extra:
        raise LeakageError(
            f"Observation states facts the viewer has not found here: {extra}."
        )


# ------------------------------------------------------------------------ views


def _thing_view(state: WorldState, viewer_id: str, thing_id: str) -> ThingView:
    thing = next(item for item in state.map.things if item.id == thing_id)
    inspected = thing_id in state.inspected.get(viewer_id, [])
    return ThingView(
        id=thing.id,
        kind=thing.kind,
        name=thing.name,
        properties=dict(thing.properties),
        custody=(
            custody_chain(state.ledger, thing_id, limit=CUSTODY_LIMIT)
            if inspected
            else None
        ),
    )


def _item(entry: LedgerEntry, record: WitnessRecord) -> ObservationItem:
    return ObservationItem(
        ledger_seq=entry.seq,
        tick=entry.tick,
        channel=record.channel,
        confidence=record.confidence,
        text=record.text,
        from_room=record.from_room if record.channel == "heard" else None,
    )


__all__ = [
    "CUSTODY_LIMIT",
    "RECENT_LIMIT",
    "LeakageError",
    "assert_no_leak",
    "build_observation",
]
