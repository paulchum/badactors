"""Build what one player may be told, and prove nothing else got in.

:func:`build_observation` reads the authoritative state and emits only material
the viewer has a claim to. :func:`assert_no_leak` then checks the result
*structurally*: every line of memory names the ledger entry it came from, and that
entry must carry a witness record for this viewer whose text matches exactly. A
leak is therefore not a suspicious-looking string but a line with no provenance —
which is why a new kind of leak cannot slip past a checker that was written before
it existed.

Nine classes of leak have their own guard, because each is a mistake a plausible
refactor could make:

* **foreign room** — an object or memory from somewhere the viewer has not been;
* **unwitnessed action** — an entry with no record for this viewer;
* **heard identity** — a through-the-wall record naming who did it, or carrying
  the words said;
* **another player's plan** — plan steps that are not the viewer's own;
* **uninspected custody chain** — an object's history without an inspection;
* **a look taken for a window** — what was hidden or stowed in a room after the
  viewer searched it, or what happened to an object after they inspected it.
  This guard reads the viewer's own search and inspection records, with their
  ticks, never the indexes the builder keeps;
* **laundered channel** — words shown through a channel, or under an author, other
  than the ones they arrived by, and above all a player's words in the Steward's
  channel, which only the host may use;
* **presence** — who else is in the room beyond what the light shows: a name in
  dim or dark, a garment in the dark, a build or distinctive detail anywhere but
  bright light;
* **private field** — anything from a ledger entry's ``private`` intentions, which
  only the audience sees. No builder reads ``private``, and every other source a
  player may be shown is checked against the ledger, including the entry text a
  custody link quotes.

The toolkits add four more:

* **a declared intent** — what a manipulation was meant to achieve, which no
  observation may show, its author's included: not in a plan step, not in any
  record of the entry that carries it;
* **a finding seen by someone else** — what a search, a reading or a comparison
  turned up belongs to the one who made it; anyone watching saw only the act;
* **a covert act seen by its victim** — a pocket picked or a coat slipped into
  leaves its victim no record unless they caught the hand;
* **a notebook** — every noted item must cite a record its viewer noted, or one
  they made a note to keep, and a secret may only be the viewer's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from badactors.castle.contracts import (
    AtRoom,
    InRoom,
    Intent,
    InTransit,
    LedgerEntry,
    ObservationItem,
    PlayerState,
    Presence,
    RoomView,
    ScopedObservation,
    ThingView,
    ToolUse,
    TransitView,
    WitnessRecord,
    WorldState,
    without_intent,
)
from badactors.castle.ledger import (
    custody_chain,
    entry_at,
    notebook_seqs,
    placement_before,
    recent_records,
    traced_links,
)
from badactors.castle.papers import WRITING_TOOLS
from badactors.castle.perceive import (
    carried_things,
    occupants,
    presence,
    room_of,
    visible_things,
    worn_garment,
)
from badactors.castle.world import valid_actions

FINDINGS: frozenset[str] = frozenset({"search", "inspect", "read", "search_person"})
"""Entry types whose result belongs to the player who made them."""

COVERT: frozenset[str] = frozenset({"plant", "steal"})
"""Covert acts that went unnoticed: only their actor has a record."""

CAUGHT: frozenset[str] = frozenset({"plant_caught", "pickpocket"})
"""Covert acts that their target may know of, if they caught the hand."""


class LeakageError(AssertionError):
    """A scoped observation contains something its viewer has no claim to."""


def build_observation(
    state: WorldState,
    viewer_id: str,
    *,
    recent_limit: int | None = None,
) -> ScopedObservation:
    viewer = state.players[viewer_id]
    if recent_limit is None:
        recent_limit = state.balance.recent_limit
    position = viewer.position
    room_view: RoomView | None = None
    transit_view: TransitView | None = None
    present: list[Presence] = []
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
        present = presence(state, viewer_id, room.id)
        visible = [
            _thing_view(state, viewer_id, thing_id)
            for thing_id in visible_things(state, viewer_id, room.id)
        ]
        searched = set(state.searched.get(viewer_id, {}))
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

    records = recent_records(state.ledger, viewer_id, recent_limit)
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
        wearing=_wearing(state, viewer_id),
        secret=viewer.secret,
        known_facts=facts,
        recent=[item_for(entry, record) for entry, record in records],
        evidence=notebook_items(state, viewer_id),
        plan_remaining=[without_intent(step) for step in viewer.plan.steps],
        valid_actions=valid_actions(state, viewer_id),
    )


def notebook_items(state: WorldState, viewer_id: str) -> list[ObservationItem]:
    """The viewer's most recently noted or kept records, however long ago they
    were made."""
    items: list[ObservationItem] = []
    for seq in notebook_seqs(state, viewer_id)[-state.balance.notebook_limit :]:
        entry = state.ledger[seq - 1]
        record = entry.record_for(viewer_id)
        if record is not None:
            items.append(item_for(entry, record))
    return items


def assert_no_leak(observation: ScopedObservation, state: WorldState) -> None:
    """Raise :class:`LeakageError` unless every part of ``observation`` is earned."""
    viewer_id = observation.viewer_id
    if viewer_id not in state.players:
        raise LeakageError("Observation names a viewer who is not in this world.")
    viewer = state.players[viewer_id]

    _assert_position(observation, viewer)
    for item in observation.recent:
        _assert_tool_audience(assert_item(item, viewer_id, state), item, viewer_id)
    _assert_notebook(observation, state, viewer_id)
    if observation.secret != viewer.secret:
        raise LeakageError(
            f"Observation shows {viewer_id!r} a secret that is not their own: a "
            "notebook holds only what its viewer found."
        )
    looks = _looks(state, viewer_id)
    _assert_objects(observation, state, looks)
    _assert_plan(observation, state, viewer_id)
    _assert_facts(observation, state, looks)
    _assert_searched(observation, looks)
    _assert_presence(observation, state, viewer)
    _assert_wearing(observation, state, viewer_id, looks)


@dataclass(frozen=True)
class _Looks:
    """What a viewer has looked into for themselves, and when: the room each of
    their searches was in and the object each inspection examined, latest
    first. Read from the viewer's own records, never from the indexes the
    builder keeps, so a builder that shows a search or an inspection as a live
    window cannot vouch for itself."""

    searches: dict[str, int]
    inspections: dict[str, int]


def _looks(state: WorldState, viewer_id: str) -> _Looks:
    """Every search and inspection is filed in its user's notebook, so the
    notebook narrows the scan; the viewer's own record of each is what counts."""
    searches: dict[str, int] = {}
    inspections: dict[str, int] = {}
    for seq in state.notebook.get(viewer_id, []):
        entry = entry_at(state.ledger, seq)
        record = entry.record_for(viewer_id) if entry is not None else None
        if entry is None or record is None or record.sense != "own":
            continue
        tool = entry.payload.get("tool")
        if entry.type == "search" and tool == "search" and record.from_room:
            searches[record.from_room] = entry.seq
        elif entry.type == "inspect" and tool == "inspect":
            inspections[entry.payload["thing_id"]] = entry.seq
    return _Looks(searches=searches, inspections=inspections)


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


def assert_item(
    item: ObservationItem,
    viewer_id: str,
    state: WorldState,
) -> LedgerEntry:
    """Check one line of memory against the record it cites, and return the
    entry it came from."""
    entry = entry_at(state.ledger, item.ledger_seq)
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
    if item.sense != record.sense or item.confidence != record.confidence:
        raise LeakageError(
            f"Observation item for entry {entry.seq} misstates how it was perceived."
        )
    if (item.channel, item.quote, item.author) != (
        record.channel,
        record.quote,
        record.author,
    ):
        raise LeakageError(
            f"Observation item for entry {entry.seq} misstates what was said, by "
            "whom, or through which channel."
        )
    if (item.tick, item.day, item.phase) != (entry.tick, entry.day, entry.phase):
        raise LeakageError(f"Observation item for entry {entry.seq} moves it in time.")
    if item.kind != entry.type:
        raise LeakageError(
            f"Observation item for entry {entry.seq} misstates what kind of event "
            "it was."
        )
    if item.actor != record.recorded_actor:
        raise LeakageError(
            f"Observation item for entry {entry.seq} names an actor this viewer did "
            "not record."
        )
    target = entry.payload.get("target") if record.sense != "heard" else None
    if item.target != target:
        raise LeakageError(
            f"Observation item for entry {entry.seq} misstates whom the act was "
            "aimed at."
        )
    if item.from_room != (record.from_room if record.sense == "heard" else None):
        raise LeakageError(
            f"Observation item for entry {entry.seq} misstates where it came from."
        )
    _assert_official(entry, record)
    _assert_hearing(entry, record, item, state)
    return entry


def _assert_official(entry: LedgerEntry, record: WitnessRecord) -> None:
    """Only the host writes to the Steward's channel; a player never can."""
    if record.channel == "steward" and entry.actor_id is not None:
        raise LeakageError(
            f"Entry {entry.seq} puts {entry.actor_id!r}'s words in the Steward's "
            "channel, which only the host may use."
        )


def _assert_hearing(
    entry: LedgerEntry,
    record: WitnessRecord,
    item: ObservationItem,
    state: WorldState,
) -> None:
    if record.sense != "heard":
        return
    if record.recorded_actor is not None:
        raise LeakageError(
            f"Heard record for entry {entry.seq} names an actor; a wall carries "
            "the kind of event and the room, never who did it."
        )
    if record.quote is not None or record.author is not None:
        raise LeakageError(
            f"Heard record for entry {entry.seq} carries words; through a wall "
            "speech is only voices."
        )
    actor_id = entry.actor_id
    if actor_id is None:
        return
    actor = state.players.get(actor_id)
    names = {actor_id} | ({actor.name} if actor is not None else set())
    # Whole words only: a player called Al must not trip over "the Great Hall".
    if any(
        re.search(rf"\b{re.escape(name)}\b", item.text, re.IGNORECASE) for name in names
    ):
        raise LeakageError(
            f"Heard record for entry {entry.seq} contains the actor's identity."
        )


def _assert_objects(
    observation: ScopedObservation,
    state: WorldState,
    looks: _Looks,
) -> None:
    room = observation.room
    held = set(carried_things(state, observation.viewer_id))

    for view in observation.visible_objects:
        if room is None or not _in_view(state, looks, room.id, view.id):
            raise LeakageError(
                f"Observation shows object {view.id!r}, which is not visible from "
                "where this viewer stands."
            )
        _assert_custody(view, state, looks)
    for view in observation.carrying:
        if view.id not in held:
            raise LeakageError(
                f"Observation says the viewer carries {view.id!r}, which they do not."
            )
        _assert_custody(view, state, looks)


def _in_view(state: WorldState, looks: _Looks, room_id: str, thing_id: str) -> bool:
    """Whether the viewer can see this object here: it lies in plain sight, or
    their latest search of the room found it exactly where it is now."""
    placement = state.placements.get(thing_id)
    if isinstance(placement, InRoom) and not placement.hidden:
        return placement.room_id == room_id
    searched = looks.searches.get(room_id)
    return (
        searched is not None
        and room_of(state, thing_id) == room_id
        and placement_before(state.ledger, thing_id, searched) == placement
    )


def _assert_custody(view: ThingView, state: WorldState, looks: _Looks) -> None:
    thing = next(item for item in state.map.things if item.id == view.id)
    honest = _written(state, view.id) if thing.kind == "paper" else None
    if view.written != honest:
        raise LeakageError(f"Observation misstates whether {view.id!r} is written on.")
    if view.custody is None:
        return
    inspected = looks.inspections.get(view.id)
    if inspected is None:
        raise LeakageError(
            f"Observation reveals the custody chain of {view.id!r} without the "
            "viewer having inspected it."
        )
    shown = {link.ledger_seq for link in view.custody}
    later = sorted(seq for seq in shown if seq > inspected)
    if later:
        raise LeakageError(
            f"Custody chain for {view.id!r} shows entries {later}, written after "
            "this viewer inspected it: an inspection is a look, not a window."
        )
    truth = {
        link.ledger_seq: link
        for link in custody_chain(
            state.ledger,
            view.id,
            limit=state.balance.tools.inspect.links,
            before=inspected,
        )
    }
    invented = sorted(shown - set(truth))
    if invented:
        raise LeakageError(
            f"Custody chain for {view.id!r} cites entries {invented} that never "
            "moved it within the links this viewer's inspection traced."
        )
    # A custody link is the one place an entry's own text reaches a player, so
    # it must be that text exactly — nothing added, nothing from ``private``.
    rewritten = sorted(
        link.ledger_seq for link in view.custody if link != truth[link.ledger_seq]
    )
    if rewritten:
        raise LeakageError(
            f"Custody chain for {view.id!r} misquotes entries {rewritten}."
        )


def _assert_plan(
    observation: ScopedObservation,
    state: WorldState,
    viewer_id: str,
) -> None:
    for step in observation.plan_remaining:
        if isinstance(step, ToolUse) and getattr(step, "intent", None) is not None:
            raise LeakageError(
                "Observation shows a plan step with its declared intent: a declared "
                "intent is the audience's alone."
            )
    own = [
        without_intent(step).model_dump(mode="json")
        for step in state.players[viewer_id].plan.steps
    ]
    shown = [step.model_dump(mode="json") for step in observation.plan_remaining]
    if shown != own:
        raise LeakageError("Observation shows a plan that is not the viewer's own.")


def _assert_notebook(
    observation: ScopedObservation, state: WorldState, viewer_id: str
) -> None:
    noted = set(state.notebook.get(viewer_id, []))
    # A note to remember is the viewer's own act, so their own record of it,
    # filed, is what lets the record it names into the notebook.
    kept: set[int] = set()
    for seq in noted:
        note = state.ledger[seq - 1]
        own = note.record_for(viewer_id)
        if (
            note.type == "remember"
            and note.actor_id == viewer_id
            and own is not None
            and own.sense == "own"
        ):
            kept.add(int(note.payload["seq"]))
    for item in observation.evidence:
        entry = assert_item(item, viewer_id, state)
        record = entry.record_for(viewer_id)
        filed = record is not None and record.noted and entry.seq in noted
        if not filed and entry.seq not in kept:
            raise LeakageError(
                f"Notebook item for entry {entry.seq} cites a record {viewer_id!r} "
                "never noted or kept: a notebook holds only what its viewer found."
            )
        _assert_tool_audience(entry, item, viewer_id)


def _assert_tool_audience(
    entry: LedgerEntry, item: ObservationItem, viewer_id: str
) -> None:
    """The leak classes the toolkits add, checked for one line of memory."""
    rendered = entry.private.get("intent")
    if rendered is not None:
        shown = f"{item.text}\n{item.quote or ''}"
        intent = Intent.parse(rendered)
        free = intent.effect == "belief" and len(intent.want) >= 12
        if rendered in shown or (
            free and intent.want in item.text and intent.want not in (entry.quote or "")
        ):
            raise LeakageError(
                f"Entry {entry.seq} shows {viewer_id!r} what its actor meant it to do: "
                "a declared intent is the audience's alone."
            )
    if entry.actor_id == viewer_id:
        return
    if entry.type in COVERT:
        raise LeakageError(
            f"Entry {entry.seq} ({entry.type}) reached {viewer_id!r}: a covert act "
            "seen by its victim, who never caught the hand."
        )
    if entry.type in CAUGHT and not (
        entry.payload.get("caught") == "true" and entry.payload.get("to") == viewer_id
    ):
        raise LeakageError(
            f"Entry {entry.seq} ({entry.type}) reached {viewer_id!r}: a covert act "
            "seen by someone who never caught the hand."
        )
    if entry.type in FINDINGS:
        record = entry.record_for(viewer_id)
        own = entry.record_for(entry.actor_id or "")
        if record is not None and (
            record.quote is not None
            or record.channel != "direct"
            or (own is not None and own.text in record.text)
        ):
            raise LeakageError(
                f"Entry {entry.seq} ({entry.type}) shows {viewer_id!r} what "
                f"{entry.actor_id!r} found: a finding seen by someone else."
            )


def _assert_presence(
    observation: ScopedObservation,
    state: WorldState,
    viewer: PlayerState,
) -> None:
    """Who else is here, as far as the light shows and no further.

    Checked against the true occupants directly, not against the builder: in
    bright light each is named with what they wear; in dim light nobody is named
    and only garments show; in the dark there are only silhouettes.
    """
    room = observation.room
    if room is None or viewer.asleep:
        if observation.present:
            raise LeakageError(
                "A viewer in a passage, or asleep, sees nobody around them."
            )
        return
    others = [pid for pid in occupants(state, room.id) if pid != viewer.id]
    truth = []
    for pid in others:
        player = state.players[pid]
        garment = worn_garment(state, pid)
        truth.append((pid, player, garment.name if garment is not None else None))
    lighting = state.map.room(room.id).lighting
    shown = observation.present
    if lighting == "bright":
        expected = sorted(
            (pid, player.name, worn, player.detail, player.asleep)
            for pid, player, worn in truth
        )
        actual = sorted(
            (p.player_id or "", p.label, p.garment, p.detail, p.asleep) for p in shown
        )
        if actual != expected:
            raise LeakageError("Observation misstates who is in this bright room.")
        return
    if any(p.player_id is not None or p.detail is not None for p in shown):
        raise LeakageError(
            "Observation names someone, or shows a distinctive detail, in a room "
            "too dim to see it."
        )
    if lighting == "dim":
        dim = sorted(
            (
                f"someone in a {worn}" if worn else "someone in shirtsleeves",
                worn,
                player.asleep,
            )
            for _, player, worn in truth
        )
        if sorted((p.label, p.garment, p.asleep) for p in shown) != dim:
            raise LeakageError(
                "Observation shows more than garments in a dim room, or the wrong ones."
            )
        return
    dark = sorted(("someone", None, player.asleep) for _, player, _worn in truth)
    if sorted((p.label, p.garment, p.asleep) for p in shown) != dark:
        raise LeakageError("Observation shows more than silhouettes in the dark.")


def _assert_wearing(
    observation: ScopedObservation,
    state: WorldState,
    viewer_id: str,
    looks: _Looks,
) -> None:
    garment = worn_garment(state, viewer_id)
    shown = observation.wearing
    if (shown.id if shown is not None else None) != (
        garment.id if garment is not None else None
    ):
        raise LeakageError("Observation misstates what the viewer is wearing.")
    if shown is not None:
        _assert_custody(shown, state, looks)


def _wearing(state: WorldState, viewer_id: str) -> ThingView | None:
    garment = worn_garment(state, viewer_id)
    return _thing_view(state, viewer_id, garment.id) if garment is not None else None


def _assert_searched(observation: ScopedObservation, looks: _Looks) -> None:
    room = observation.room
    here = room is not None and room.id in looks.searches
    if observation.searched_here != here:
        raise LeakageError(
            "Observation misstates whether this viewer has searched this room."
        )


def _assert_facts(
    observation: ScopedObservation,
    state: WorldState,
    looks: _Looks,
) -> None:
    room = observation.room
    if not observation.known_facts:
        return
    if room is None:
        raise LeakageError("A viewer in a passage cannot read facts in a room.")
    private = state.map.room(room.id).private
    allowed = {
        fact.text
        for fact in state.map.facts
        if fact.room_id == room.id and (not private or room.id in looks.searches)
    }
    extra = sorted(set(observation.known_facts) - allowed)
    if extra:
        raise LeakageError(
            f"Observation states facts the viewer has not found here: {extra}."
        )


# ------------------------------------------------------------------------ views


def _thing_view(state: WorldState, viewer_id: str, thing_id: str) -> ThingView:
    thing = next(item for item in state.map.things if item.id == thing_id)
    inspection = state.inspected.get(viewer_id, {}).get(thing_id)
    return ThingView(
        id=thing.id,
        kind=thing.kind,
        name=thing.name,
        properties=dict(thing.properties),
        custody=(
            traced_links(state.ledger, inspection) if inspection is not None else None
        ),
        written=_written(state, thing.id) if thing.kind == "paper" else None,
    )


def _written(state: WorldState, thing_id: str) -> bool:
    """Whether a sheet has writing on it: a backward scan to the first entry
    that wrote on it, since a sheet is written at most once."""
    for entry in reversed(state.ledger):
        if (
            entry.type == "write"
            and entry.payload.get("thing_id") == thing_id
            and entry.payload.get("tool") in WRITING_TOOLS
        ):
            return True
    return False


def item_for(entry: LedgerEntry, record: WitnessRecord) -> ObservationItem:
    """The line of memory one record becomes."""
    return ObservationItem(
        ledger_seq=entry.seq,
        tick=entry.tick,
        day=entry.day,
        phase=entry.phase,
        kind=entry.type,
        sense=record.sense,
        confidence=record.confidence,
        channel=record.channel,
        text=record.text,
        quote=record.quote,
        author=record.author,
        actor=record.recorded_actor,
        target=entry.payload.get("target") if record.sense != "heard" else None,
        from_room=record.from_room if record.sense == "heard" else None,
    )


__all__ = [
    "CAUGHT",
    "COVERT",
    "FINDINGS",
    "LeakageError",
    "assert_item",
    "assert_no_leak",
    "build_observation",
    "item_for",
    "notebook_items",
]
