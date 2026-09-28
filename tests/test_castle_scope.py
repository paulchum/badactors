"""A scoped observation must be earned, item by item.

Each planted leak below is a mistake a plausible refactor could make. The guard
against it is structural — every line names the ledger entry it came from, and that
entry must carry a record for this viewer — so these tests fail when the leak is
real rather than when a string happens to look suspicious.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from badactors.castle.contracts import (
    AtRoom,
    CustodyLink,
    Move,
    ObservationItem,
    Plan,
    ScopedObservation,
    Take,
    ThingView,
    ToolStep,
    Wait,
    WorldState,
)
from badactors.castle.director import RoutineDecider, run_phase
from badactors.castle.ledger import custody_chain
from badactors.castle.mapfile import load_world
from badactors.castle.scope import (
    LeakageError,
    assert_no_leak,
    build_observation,
)
from badactors.castle.world import start, submit_plan, tick

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "configs/castle/map.json"
CAST_PATH = ROOT / "configs/castle/cast.example.json"


def _world(seed: int = 13) -> WorldState:
    return start(load_world(MAP_PATH, CAST_PATH, seed=seed))


def _played(phases: int = 3, seed: int = 13) -> WorldState:
    world = _world(seed)
    decider = RoutineDecider(seed=seed)
    for _ in range(phases):
        world = run_phase(world, decider, check_leaks=False)
    return world


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
        assert item.channel == record.channel
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
                    channel=other.channel,
                    confidence=other.confidence,
                    text=other.text,
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
                    channel="saw",
                    confidence="clear",
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
            if record.channel != "heard":
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
            if record.channel != "heard":
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
    record.text = f"{actor.name}: {record.text}"

    observation = build_observation(world, viewer_id)
    with pytest.raises(LeakageError, match="the actor's identity"):
        assert_no_leak(observation, world)


def test_planted_leak_heard_record_naming_an_actor() -> None:
    world = _played(phases=4)
    viewer_id, item = _heard_pair(world)
    entry = next(e for e in world.ledger if e.seq == item.ledger_seq)
    record = entry.record_for(viewer_id)
    assert record is not None
    record.recorded_actor = entry.actor_id

    observation = build_observation(world, viewer_id)
    with pytest.raises(LeakageError, match="names an actor"):
        assert_no_leak(observation, world)


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
        Plan(steps=[ToolStep(tool="inspect", thing_id="silver_decanter")]),
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


def test_planted_leak_invented_custody_link() -> None:
    world = _with_decanter(_world())
    world = submit_plan(
        world,
        "mara",
        Plan(steps=[ToolStep(tool="inspect", thing_id="silver_decanter")]),
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
    world = submit_plan(world, "mara", Plan(steps=[ToolStep(tool="search")]))
    world = tick(world)
    assert build_observation(world, "mara").searched_here is True
    assert build_observation(world, "sol").searched_here is False
