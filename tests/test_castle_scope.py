"""A scoped observation must be earned, item by item.

Each planted leak below is a mistake a plausible refactor could make. The guard
against it is structural — every line names the ledger entry it came from, and that
entry must carry a record for this viewer — so these tests fail when the leak is
real rather than when a string happens to look suspicious.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from badactors.castle import scope as scope_module
from badactors.castle.contracts import (
    AtRoom,
    CustodyLink,
    Drop,
    InRoom,
    Inspect,
    LedgerEntry,
    Move,
    ObservationItem,
    Plan,
    Remember,
    ScopedObservation,
    Search,
    Take,
    Talk,
    ThingView,
    Wait,
    Whisper,
    WitnessRecord,
    WorldState,
)
from badactors.castle.director import RoutineDecider, run_phase
from badactors.castle.ledger import custody_chain, replay_notebook
from badactors.castle.mapfile import build_world, load_cast, load_map, load_world
from badactors.castle.perceive import room_of
from badactors.castle.scope import (
    LeakageError,
    assert_no_leak,
    build_observation,
    item_for,
)
from badactors.castle.world import start, submit_plan, tick
from castle_tools import act, last, put, scene

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "configs/castle/map.json"
CAST_PATH = ROOT / "configs/castle/cast.json"


def _world(seed: int = 13) -> WorldState:
    return start(load_world(MAP_PATH, CAST_PATH, seed=seed))


def _played(phases: int = 3, seed: int = 13) -> WorldState:
    world = _world(seed)
    decider = RoutineDecider(seed=seed)
    for _ in range(phases):
        world = run_phase(world, decider, check_leaks=False)
    return world


def _rewrite(
    world: WorldState, entry: LedgerEntry, player_id: str, **changes: object
) -> WitnessRecord:
    """Plant a world bug: swap in a copy of ``entry`` whose record for
    ``player_id`` says something else. Records are frozen, so a bug has to
    write a new one, just as this does, and nothing validates it on the way."""
    record = entry.record_for(player_id)
    assert record is not None
    changed = record.model_copy(update=changes)
    world.ledger[entry.seq - 1] = entry.model_copy(
        update={
            "witnesses": [
                changed if witness is record else witness for witness in entry.witnesses
            ]
        }
    )
    return changed


def _someone_indoors(world: WorldState) -> str:
    return next(
        player_id
        for player_id in world.seats
        if world.players[player_id].room_id is not None
    )


# ------------------------------------------------------------------ honest build


def test_an_honest_observation_passes() -> None:
    world = _played()
    for player_id in world.seats:
        assert_no_leak(build_observation(world, player_id), world)


def test_every_item_matches_the_record_it_cites() -> None:
    world = _played()
    observation = build_observation(world, "mara")
    by_seq = {entry.seq: entry for entry in world.ledger}
    assert observation.recent
    for item in observation.recent:
        record = by_seq[item.ledger_seq].record_for("mara")
        assert record is not None
        assert item.text == record.text
        assert item.sense == record.sense
        assert item.confidence == record.confidence


def test_a_viewer_in_a_passage_is_told_only_that() -> None:
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="chambers")]))
    world = tick(world)
    observation = build_observation(world, "mara")
    assert observation.room is None
    assert observation.transit is not None
    assert observation.transit.destination == "chambers"
    assert observation.present == []
    assert observation.visible_objects == []
    assert_no_leak(observation, world)


# --------------------------------------------------------- leak 1: foreign room


def test_planted_leak_object_from_another_room() -> None:
    world = _played()
    observation = build_observation(world, "mara")
    leaked = observation.model_copy(
        update={
            "visible_objects": [
                *observation.visible_objects,
                ThingView(id="guest_book", kind="record", name="guest book"),
            ]
        }
    )
    with pytest.raises(LeakageError, match="not visible from where"):
        assert_no_leak(leaked, world)


def test_planted_leak_room_the_viewer_is_not_in() -> None:
    world = _played()
    observation = build_observation(world, _someone_indoors(world))
    assert observation.room is not None
    elsewhere = next(room for room in world.map.rooms if room.id != observation.room.id)
    leaked = observation.model_copy(
        update={
            "room": observation.room.model_copy(
                update={"id": elsewhere.id, "name": elsewhere.name}
            )
        }
    )
    with pytest.raises(LeakageError, match="a room the viewer is not in"):
        assert_no_leak(leaked, world)


def test_planted_leak_fact_from_an_unsearched_private_room() -> None:
    world = _world()
    world.players["mara"].position = AtRoom(room_id="library")
    observation = build_observation(world, "mara")
    assert observation.known_facts == []
    secret = next(fact for fact in world.map.facts if fact.room_id == "library")
    leaked = observation.model_copy(update={"known_facts": [secret.text]})
    with pytest.raises(LeakageError, match="has not found here"):
        assert_no_leak(leaked, world)


# ---------------------------------------------------- leak 2: unwitnessed action


def test_planted_leak_unwitnessed_action() -> None:
    world = _played()
    observation = build_observation(world, "mara")
    unseen = next(
        entry
        for entry in world.ledger
        if entry.record_for("mara") is None and entry.witnesses
    )
    other = unseen.witnesses[0]
    leaked = observation.model_copy(
        update={
            "recent": [
                *observation.recent,
                ObservationItem(
                    ledger_seq=unseen.seq,
                    tick=unseen.tick,
                    day=unseen.day,
                    phase=unseen.phase,
                    kind=unseen.type,
                    sense=other.sense,
                    confidence=other.confidence,
                    channel=other.channel,
                    text=other.text,
                    quote=other.quote,
                    author=other.author,
                ),
            ]
        }
    )
    with pytest.raises(LeakageError, match="never witnessed"):
        assert_no_leak(leaked, world)


def test_planted_leak_invented_ledger_reference() -> None:
    world = _played()
    observation = build_observation(world, "mara")
    leaked = observation.model_copy(
        update={
            "recent": [
                *observation.recent,
                ObservationItem(
                    ledger_seq=world.ledger_seq + 500,
                    tick=world.tick,
                    day=world.day,
                    phase=world.phase,
                    kind="talk",
                    sense="saw",
                    confidence="clear",
                    channel="direct",
                    text="Something that never happened.",
                ),
            ]
        }
    )
    with pytest.raises(LeakageError, match="does not exist"):
        assert_no_leak(leaked, world)


def test_planted_leak_rewritten_record_text() -> None:
    """The ledger's own truth is not what the witness kept; only the record is."""
    world = _played()
    observation = build_observation(world, "mara")
    assert observation.recent
    by_seq = {entry.seq: entry for entry in world.ledger}
    target = next(
        item for item in observation.recent if by_seq[item.ledger_seq].text != item.text
    )
    truth = by_seq[target.ledger_seq].text
    leaked = observation.model_copy(
        update={
            "recent": [
                item.model_copy(update={"text": truth}) if item is target else item
                for item in observation.recent
            ]
        }
    )
    with pytest.raises(LeakageError, match="does not match the record"):
        assert_no_leak(leaked, world)


# ------------------------------------------------------- leak 3: heard identity


def _heard_pair(world: WorldState) -> tuple[str, ObservationItem]:
    for entry in world.ledger:
        for record in entry.witnesses:
            if record.sense != "heard":
                continue
            observation = build_observation(world, record.player_id)
            for item in observation.recent:
                if item.ledger_seq == entry.seq:
                    return record.player_id, item
    raise AssertionError("no overheard record in this world")


def test_hearing_never_carries_who_it_was() -> None:
    world = _played(phases=4)
    for entry in world.ledger:
        for record in entry.witnesses:
            if record.sense != "heard":
                continue
            assert record.recorded_actor is None
            if entry.actor_id is not None:
                actor = world.players[entry.actor_id]
                assert actor.name.casefold() not in record.text.casefold()
                assert entry.actor_id.casefold() not in record.text.casefold()


def test_planted_leak_heard_identity_in_the_text() -> None:
    world = _played(phases=4)
    viewer_id, item = _heard_pair(world)
    entry = next(e for e in world.ledger if e.seq == item.ledger_seq)
    assert entry.actor_id is not None
    actor = world.players[entry.actor_id]
    record = entry.record_for(viewer_id)
    assert record is not None
    _rewrite(world, entry, viewer_id, text=f"{actor.name}: {record.text}")

    observation = build_observation(world, viewer_id)
    with pytest.raises(LeakageError, match="the actor's identity"):
        assert_no_leak(observation, world)


def test_planted_leak_heard_record_naming_an_actor() -> None:
    world = _played(phases=4)
    viewer_id, item = _heard_pair(world)
    entry = next(e for e in world.ledger if e.seq == item.ledger_seq)
    _rewrite(world, entry, viewer_id, recorded_actor=entry.actor_id)

    observation = build_observation(world, viewer_id)
    with pytest.raises(LeakageError, match="names an actor"):
        assert_no_leak(observation, world)


def test_a_name_inside_a_room_name_is_not_a_heard_identity() -> None:
    """Whole words only: a player called Al is not named by "the Great Hall"."""
    cast = [dict(member) for member in load_cast(CAST_PATH)]
    renamed = next(member for member in cast if member["id"] == "vale")
    renamed.update(id="al", name="Al")
    world = start(build_world(load_map(MAP_PATH), cast, world_id="w", seed=13))
    world = submit_plan(world, "sol", Plan(steps=[Move(room_id="dining_room")]))
    world = tick(world)
    assert world.players["sol"].room_id == "dining_room"

    world = submit_plan(world, "al", Plan(steps=[Move(room_id="gallery")]))
    world = tick(world)
    departure = next(
        entry
        for entry in world.ledger
        if entry.type == "move_depart" and entry.actor_id == "al"
    )
    heard = departure.record_for("sol")
    assert heard is not None and heard.sense == "heard"
    assert "Great Hall" in heard.text
    assert_no_leak(build_observation(world, "sol"), world)

    _rewrite(world, departure, "sol", text=f"Al, {heard.text}")
    with pytest.raises(LeakageError, match="the actor's identity"):
        assert_no_leak(build_observation(world, "sol"), world)


# --------------------------------------------------- leak 4: another player's plan


def test_planted_leak_another_players_plan() -> None:
    world = _world()
    world = submit_plan(world, "mara", Plan(goal="mine", steps=[Wait()]))
    world = submit_plan(
        world,
        "sol",
        Plan(goal="theirs", steps=[Move(room_id="dining_room"), Wait()]),
    )
    observation = build_observation(world, "mara")
    assert observation.plan_remaining == [Wait()]
    leaked = observation.model_copy(
        update={"plan_remaining": list(world.players["sol"].plan.steps)}
    )
    with pytest.raises(LeakageError, match="not the viewer's own"):
        assert_no_leak(leaked, world)


def test_planted_leak_extra_step_appended_to_the_viewers_plan() -> None:
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Wait()]))
    observation = build_observation(world, "mara")
    leaked = observation.model_copy(
        update={"plan_remaining": [*observation.plan_remaining, Move(room_id="crypt")]}
    )
    with pytest.raises(LeakageError, match="not the viewer's own"):
        assert_no_leak(leaked, world)


# ------------------------------------------------ leak 5: uninspected custody


def _with_decanter(world: WorldState) -> WorldState:
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="dining_room")]))
    world = tick(world)
    world = submit_plan(world, "mara", Plan(steps=[Take(thing_id="silver_decanter")]))
    return tick(world)


def test_inspection_reveals_a_slice_of_the_real_chain() -> None:
    world = _with_decanter(_world())
    before = build_observation(world, "mara")
    carried = next(view for view in before.carrying if view.id == "silver_decanter")
    assert carried.custody is None

    world = submit_plan(
        world,
        "mara",
        Plan(steps=[Inspect(thing_id="silver_decanter")]),
    )
    world = tick(world)
    after = build_observation(world, "mara")
    examined = next(view for view in after.carrying if view.id == "silver_decanter")
    assert examined.custody is not None
    truth = custody_chain(world.ledger, "silver_decanter")
    assert [link.ledger_seq for link in examined.custody] == [
        link.ledger_seq for link in truth
    ]
    assert_no_leak(after, world)


def test_planted_leak_custody_without_an_inspection() -> None:
    world = _with_decanter(_world())
    observation = build_observation(world, "mara")
    chain = custody_chain(world.ledger, "silver_decanter")
    assert chain
    leaked = observation.model_copy(
        update={
            "carrying": [
                view.model_copy(update={"custody": chain})
                if view.id == "silver_decanter"
                else view
                for view in observation.carrying
            ]
        }
    )
    with pytest.raises(LeakageError, match="without the"):
        assert_no_leak(leaked, world)


# ------------------------------------- a search or an inspection read as a window


def _hidden_after_a_search() -> WorldState:
    """June searches the Library. While she is out, Knox hides the almanac
    there and searches it himself. June comes back."""
    world = scene()
    put(world, "june", "library")
    world = act(world, "june", Search())
    put(world, "june", "gallery")
    put(world, "knox", "library")
    world = act(
        world,
        "knox",
        Take(thing_id="almanac"),
        Drop(thing_id="almanac", hidden=True),
        Search(),
    )
    put(world, "knox", "gallery")
    put(world, "june", "library")
    assert_no_leak(build_observation(world, "june"), world)
    return world


def _live_window(state: WorldState, viewer_id: str, room_id: str) -> list[str]:
    """The bug: a search that, once made, shows the room as it is now."""
    searched = room_id in state.searched.get(viewer_id, {})
    return sorted(
        thing_id
        for thing_id, placement in state.placements.items()
        if room_of(state, thing_id) == room_id
        and (searched or (isinstance(placement, InRoom) and not placement.hidden))
    )


def test_planted_leak_a_search_index_pointing_at_someone_elses_search() -> None:
    world = _hidden_after_a_search()
    world.searched["june"]["library"] = last(world, "search").seq
    observation = build_observation(world, "june")
    assert "almanac" in {view.id for view in observation.visible_objects}
    with pytest.raises(LeakageError, match="not visible from where"):
        assert_no_leak(observation, world)


def test_a_world_whose_search_is_a_live_window_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _hidden_after_a_search()
    monkeypatch.setattr(scope_module, "visible_things", _live_window)
    observation = build_observation(world, "june")
    assert "almanac" in {view.id for view in observation.visible_objects}
    with pytest.raises(LeakageError, match="not visible from where"):
        assert_no_leak(observation, world)


def _moved_after_an_inspection() -> WorldState:
    """Mara inspects the decanter. While she is out, Sol carries it round the
    room and inspects it himself. Mara comes back."""
    world = scene()
    put(world, "mara", "dining_room")
    world = act(world, "mara", Inspect(thing_id="silver_decanter"))
    put(world, "mara", "kitchen")
    put(world, "sol", "dining_room")
    world = act(
        world,
        "sol",
        Take(thing_id="silver_decanter"),
        Drop(thing_id="silver_decanter"),
        Inspect(thing_id="silver_decanter"),
    )
    put(world, "sol", "kitchen")
    put(world, "mara", "dining_room")
    assert_no_leak(build_observation(world, "mara"), world)
    return world


def _decanter_chain(observation: ScopedObservation) -> list[CustodyLink]:
    view = next(v for v in observation.visible_objects if v.id == "silver_decanter")
    assert view.custody is not None
    return view.custody


def test_planted_leak_an_inspection_index_pointing_at_someone_elses() -> None:
    world = _moved_after_an_inspection()
    world.inspected["mara"]["silver_decanter"] = last(world, "inspect").seq
    observation = build_observation(world, "mara")
    assert any("Sol" in link.text for link in _decanter_chain(observation))
    with pytest.raises(LeakageError, match="after this viewer inspected it"):
        assert_no_leak(observation, world)


def test_a_world_whose_inspection_is_a_live_window_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _moved_after_an_inspection()

    def live(ledger: list[LedgerEntry], seq: int) -> list[CustodyLink]:
        thing_id = ledger[seq - 1].payload["thing_id"]
        return custody_chain(ledger, thing_id, limit=world.balance.tools.inspect.links)

    monkeypatch.setattr(scope_module, "traced_links", live)
    observation = build_observation(world, "mara")
    assert any("Sol" in link.text for link in _decanter_chain(observation))
    with pytest.raises(LeakageError, match="after this viewer inspected it"):
        assert_no_leak(observation, world)


def test_planted_leak_a_chain_older_than_the_inspection_traced() -> None:
    world = scene()
    put(world, "mara", "dining_room")
    for _ in range(3):
        world = act(
            world,
            "mara",
            Take(thing_id="silver_decanter"),
            Drop(thing_id="silver_decanter"),
        )
    world = act(world, "mara", Inspect(thing_id="silver_decanter"))
    observation = build_observation(world, "mara")
    traced = _decanter_chain(observation)
    older = custody_chain(world.ledger, "silver_decanter")[: -len(traced)]
    assert older, "the decanter moved more often than an inspection traces"
    leaked = observation.model_copy(
        update={
            "visible_objects": [
                view.model_copy(update={"custody": [*older, *traced]})
                if view.id == "silver_decanter"
                else view
                for view in observation.visible_objects
            ]
        }
    )
    with pytest.raises(LeakageError, match="never moved it within"):
        assert_no_leak(leaked, world)


def test_planted_leak_invented_custody_link() -> None:
    world = _with_decanter(_world())
    world = submit_plan(
        world,
        "mara",
        Plan(steps=[Inspect(thing_id="silver_decanter")]),
    )
    world = tick(world)
    observation = build_observation(world, "mara")
    leaked = observation.model_copy(
        update={
            "carrying": [
                view.model_copy(
                    update={
                        "custody": [
                            *(view.custody or []),
                            CustodyLink(
                                ledger_seq=1,
                                tick=0,
                                text="It was in the Crypt all along.",
                            ),
                        ]
                    }
                )
                if view.id == "silver_decanter"
                else view
                for view in observation.carrying
            ]
        }
    )
    with pytest.raises(LeakageError, match="never moved it"):
        assert_no_leak(leaked, world)


def test_planted_leak_claiming_to_carry_something() -> None:
    world = _played()
    observation = build_observation(world, "mara")
    leaked = observation.model_copy(
        update={
            "carrying": [
                *observation.carrying,
                ThingView(id="ledger_page", kind="record", name="loose ledger page"),
            ]
        }
    )
    with pytest.raises(LeakageError, match="carries"):
        assert_no_leak(leaked, world)


# ---------------------------------------------------- leak 6: laundered channel


def _spoken(world: WorldState) -> tuple[str, ObservationItem]:
    """A viewer, and an item holding words someone else said in their sight."""
    world_items = (
        (player_id, item)
        for player_id in world.seats
        for item in build_observation(world, player_id).recent
        if item.channel == "speech" and item.sense == "saw"
    )
    return next(world_items)


def _replace_item(
    observation: ScopedObservation, seq: int, **changes: object
) -> ScopedObservation:
    return observation.model_copy(
        update={
            "recent": [
                item.model_copy(update=changes) if item.ledger_seq == seq else item
                for item in observation.recent
            ]
        }
    )


def test_planted_leak_speech_passed_off_as_the_steward() -> None:
    world = _played(phases=4)
    viewer_id, item = _spoken(world)
    observation = build_observation(world, viewer_id)
    leaked = _replace_item(observation, item.ledger_seq, channel="steward")
    with pytest.raises(LeakageError, match="through which channel"):
        assert_no_leak(leaked, world)


def test_planted_leak_words_given_to_another_author() -> None:
    world = _played(phases=4)
    viewer_id, item = _spoken(world)
    other = next(p for p in world.seats if p not in {viewer_id, item.author})
    observation = build_observation(world, viewer_id)
    leaked = _replace_item(observation, item.ledger_seq, author=other)
    with pytest.raises(LeakageError, match="by whom"):
        assert_no_leak(leaked, world)


def test_planted_leak_a_player_writing_in_the_stewards_channel() -> None:
    """A world bug, not a builder bug: the record itself claims official status."""
    world = _played(phases=4)
    viewer_id, item = _spoken(world)
    entry = next(e for e in world.ledger if e.seq == item.ledger_seq)
    assert entry.actor_id is not None
    _rewrite(world, entry, viewer_id, channel="steward")
    with pytest.raises(LeakageError, match="only the host may use"):
        assert_no_leak(build_observation(world, viewer_id), world)


def test_planted_leak_words_carried_through_a_wall() -> None:
    world = _world()
    world = submit_plan(world, "sol", Plan(steps=[Move(room_id="dining_room")]))
    world = tick(world)
    world = submit_plan(world, "mara", Plan(steps=[Talk(text="Meet me tonight.")]))
    world = tick(world)
    record = world.ledger[-1].record_for("sol")
    assert record is not None and record.sense == "heard"
    _rewrite(world, world.ledger[-1], "sol", quote="Meet me tonight.")
    with pytest.raises(LeakageError, match="carries words"):
        assert_no_leak(build_observation(world, "sol"), world)


# ------------------------------------------------------- leak 7: private field


def test_planted_leak_another_players_intent_in_memory() -> None:
    world = submit_plan(
        _world(), "knox", Plan(goal="frame Mara for the key", steps=[Wait()])
    )
    intent = world.ledger[-1].private["goal"]
    observation = build_observation(world, "mara")
    item = observation.recent[-1]
    leaked = _replace_item(
        observation, item.ledger_seq, text=f"Knox means to {intent}."
    )
    with pytest.raises(LeakageError, match="does not match the record"):
        assert_no_leak(leaked, world)


def test_planted_leak_intent_quoted_in_a_custody_link() -> None:
    world = _with_decanter(_world())
    world = submit_plan(
        world,
        "knox",
        Plan(goal="make Mara look like the poisoner", steps=[Wait()]),
    )
    intent = world.ledger[-1].private["goal"]
    world = submit_plan(
        world,
        "mara",
        Plan(steps=[Inspect(thing_id="silver_decanter")]),
    )
    world = tick(world)
    observation = build_observation(world, "mara")

    def with_intent(view: ThingView) -> ThingView:
        chain = list(view.custody or [])
        last = chain[-1]
        chain[-1] = last.model_copy(update={"text": f"{last.text} Knox: {intent}."})
        return view.model_copy(update={"custody": chain})

    leaked = observation.model_copy(
        update={
            "carrying": [
                with_intent(view) if view.id == "silver_decanter" else view
                for view in observation.carrying
            ]
        }
    )
    with pytest.raises(LeakageError, match="misquotes"):
        assert_no_leak(leaked, world)


# ------------------------------------------------------------------- viewer sanity


def test_unknown_viewer_is_rejected() -> None:
    world = _world()
    observation = ScopedObservation(
        viewer_id="ghost",
        tick=0,
        day=1,
        phase="breakfast",
    )
    with pytest.raises(LeakageError, match="not in this world"):
        assert_no_leak(observation, world)


def test_planted_leak_claiming_a_room_was_already_searched() -> None:
    world = _world()
    world.players["mara"].position = AtRoom(room_id="library")
    observation = build_observation(world, "mara")
    assert observation.searched_here is False
    leaked = observation.model_copy(update={"searched_here": True})
    with pytest.raises(LeakageError, match="whether this viewer has searched"):
        assert_no_leak(leaked, world)


def test_a_search_is_remembered_only_by_the_searcher() -> None:
    world = _world()
    world.players["mara"].position = AtRoom(room_id="library")
    world.players["sol"].position = AtRoom(room_id="library")
    world = submit_plan(world, "mara", Plan(steps=[Search()]))
    world = tick(world)
    assert build_observation(world, "mara").searched_here is True
    assert build_observation(world, "sol").searched_here is False


# ------------------------------------------------------- keeping a record


def _overheard_sol() -> tuple[WorldState, LedgerEntry]:
    """Mara hears Sol say something in the Great Hall: a record she holds but
    did not note."""
    world = scene()
    world = act(world, "sol", Talk(text="I was in the cellar all afternoon."))
    said = world.ledger[-1]
    record = said.record_for("mara")
    assert record is not None and not record.noted
    return world, said


def test_a_note_keeps_a_record_in_the_notebook_and_takes_no_time() -> None:
    world, said = _overheard_sol()
    world = submit_plan(
        world,
        "mara",
        Plan(steps=[Remember(seq=said.seq), Move(room_id="dining_room")]),
    )
    world = tick(world)
    assert world.players["mara"].room_id == "dining_room", "the note took no time"
    note = next(e for e in world.ledger if e.type == "remember")
    assert [record.player_id for record in note.witnesses] == ["mara"]
    observation = build_observation(world, "mara")
    assert said.seq in {item.ledger_seq for item in observation.evidence}
    assert note.seq not in {item.ledger_seq for item in observation.evidence}
    assert_no_leak(observation, world)
    assert replay_notebook(world.ledger) == world.notebook


def test_a_note_of_a_record_never_held_is_refused_privately() -> None:
    world = scene()
    put(world, "sol", "cellar")
    world = act(world, "sol", Wait(reason="alone"))
    unheard = world.ledger[-1]
    assert unheard.type == "wait" and unheard.record_for("mara") is None
    world = act(world, "mara", Remember(seq=unheard.seq), Wait())
    refused = next(
        e for e in world.ledger if e.type == "blocked" and e.actor_id == "mara"
    )
    assert [record.player_id for record in refused.witnesses] == ["mara"]
    assert not [e for e in world.ledger if e.type == "remember"]
    assert unheard.seq not in {
        item.ledger_seq for item in build_observation(world, "mara").evidence
    }


def test_a_kept_record_stays_within_the_notebook_limit() -> None:
    world, said = _overheard_sol()
    world.balance = world.balance.model_copy(update={"notebook_limit": 2})
    world = act(world, "mara", Remember(seq=said.seq), Wait())
    for line in ("One.", "Two.", "Three."):
        world = act(world, "sol", Whisper(to="mara", text=line))
    shown = [item.ledger_seq for item in build_observation(world, "mara").evidence]
    assert said.seq not in shown, "three noted whispers pushed it out"
    world = act(world, "mara", Remember(seq=said.seq), Wait())
    shown = [item.ledger_seq for item in build_observation(world, "mara").evidence]
    assert len(shown) == 2 and shown[-1] == said.seq
    assert_no_leak(build_observation(world, "mara"), world)


def test_planted_leak_a_notebook_item_neither_noted_nor_kept() -> None:
    world, said = _overheard_sol()
    observation = build_observation(world, "mara")
    record = said.record_for("mara")
    assert record is not None
    leaked = observation.model_copy(
        update={"evidence": [*observation.evidence, item_for(said, record)]}
    )
    with pytest.raises(LeakageError, match="never noted or kept"):
        assert_no_leak(leaked, world)


def test_planted_leak_someone_elses_note_does_not_fill_your_notebook() -> None:
    world, said = _overheard_sol()
    world = act(world, "sol", Remember(seq=said.seq), Wait())
    note = next(e for e in world.ledger if e.type == "remember")
    own = note.record_for("sol")
    assert own is not None
    planted = own.model_copy(update={"player_id": "mara"})
    world.ledger[note.seq - 1] = note.model_copy(
        update={"witnesses": [*note.witnesses, planted]}
    )
    world.notebook.setdefault("mara", []).append(note.seq)
    observation = build_observation(world, "mara")
    record = said.record_for("mara")
    assert record is not None
    leaked = observation.model_copy(
        update={"evidence": [*observation.evidence, item_for(said, record)]}
    )
    with pytest.raises(LeakageError, match="never noted or kept"):
        assert_no_leak(leaked, world)
