"""Tick mechanics, the phase clock, and the invariants that must always hold."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    precondition,
    rule,
    run_state_machine_as_test,
)

from badactors.castle.contracts import (
    DAY_ORDER,
    MAX_PLAN_STEPS,
    AtRoom,
    CastleMap,
    Drop,
    HeldBy,
    InContainer,
    InRoom,
    InTransit,
    LedgerEntry,
    Move,
    Placement,
    Plan,
    ScopedObservation,
    Step,
    Take,
    Talk,
    ToolStep,
    Use,
    Wait,
    WorldState,
)
from badactors.castle.director import (
    Decider,
    DirectorStats,
    RoutineDecider,
    TickObserver,
    run_day,
    run_phase,
)
from badactors.castle.ledger import custody_chain, replay_placements
from badactors.castle.mapfile import (
    build_world,
    lint_map,
    load_cast,
    load_map,
    load_world,
)
from badactors.castle.perceive import (
    carried_things,
    occupants,
    room_of,
    visible_things,
)
from badactors.castle.rng import draw
from badactors.castle.scope import assert_no_leak, build_observation
from badactors.castle.world import (
    advance_phase,
    interrupted,
    phase_complete,
    replans_left,
    start,
    submit_plan,
    tick,
    valid_actions,
    would_interrupt,
)

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "configs/castle/map.json"
CAST_PATH = ROOT / "configs/castle/cast.example.json"


def _world(seed: int = 13) -> WorldState:
    return start(load_world(MAP_PATH, CAST_PATH, seed=seed))


def _entries(state: WorldState, type: str) -> list[object]:
    return [entry for entry in state.ledger if entry.type == type]


# -------------------------------------------------------------------- the clock


def test_start_records_authored_custody() -> None:
    world = _world()
    placed = [entry for entry in world.ledger if entry.type == "placed"]
    assert len(placed) == len(world.map.things)
    assert replay_placements(world.ledger) == world.placements


def test_phase_runs_to_its_tick_budget() -> None:
    world = _world()
    budget = world.map.phase_ticks.ticks("breakfast")
    for _ in range(budget):
        assert not phase_complete(world)
        world = tick(world)
    assert phase_complete(world)


def test_day_cycles_through_every_phase_in_order() -> None:
    world = _world()
    seen = [world.phase]
    for _ in range(6):
        world = advance_phase(world)
        seen.append(world.phase)
    assert seen == [
        "breakfast",
        "mission",
        "free_time",
        "round_table",
        "dinner",
        "night",
        "breakfast",
    ]
    assert world.day == 2


def test_phase_boundary_resets_the_replan_budget() -> None:
    world = _world()
    world.replans["mara"] = 2
    world = advance_phase(world)
    assert world.replans == {}


# ------------------------------------------------------------------- travel time


def test_move_commits_only_on_arrival() -> None:
    """A two-tick passage leaves one tick where the traveller is in neither room."""
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="chambers")]))
    world = tick(world)
    position = world.players["mara"].position
    assert isinstance(position, InTransit)
    assert position.ticks_remaining == 1
    assert "mara" not in occupants(world, "chambers")
    assert "mara" not in occupants(world, "great_hall")

    world = tick(world)
    assert world.players["mara"].position == AtRoom(room_id="chambers")


def test_a_one_tick_archway_is_crossed_at_once() -> None:
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="dining_room")]))
    world = tick(world)
    assert world.players["mara"].position == AtRoom(room_id="dining_room")


def test_departure_and_arrival_are_separate_records() -> None:
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="dining_room")]))
    world = tick(world)
    departures = _entries(world, "move_depart")
    arrivals = _entries(world, "move_arrive")
    assert len(departures) == 1
    assert len(arrivals) == 1
    arrival = world.ledger[-1]
    assert arrival.payload["origin"] == "great_hall"
    assert arrival.record_for("mara") is not None


def test_move_without_a_passage_is_blocked_privately() -> None:
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="crypt")]))
    world = tick(world)
    blocked = [entry for entry in world.ledger if entry.type == "blocked"]
    assert len(blocked) == 1
    assert [record.player_id for record in blocked[0].witnesses] == ["mara"]
    assert world.players["mara"].position == AtRoom(room_id="great_hall")


def test_a_full_room_turns_the_last_arrival_back() -> None:
    """Capacity binds at the door, and the room they return to sees it."""
    world = _world()
    # The kitchen holds four. Send five, and the last one in seat order is late.
    movers = ("mara", "sol", "iris", "knox", "june")
    capacity = world.map.room("kitchen").capacity
    assert len(movers) == capacity + 1
    for player_id in movers:
        world = submit_plan(
            world,
            player_id,
            Plan(steps=[Move(room_id="dining_room"), Move(room_id="kitchen")]),
        )
    for _ in range(2):
        world = tick(world)
    assert len(occupants(world, "kitchen")) == capacity
    turned_back = [
        entry
        for entry in world.ledger
        if entry.type == "blocked" and "was full" in entry.payload["reason"]
    ]
    assert len(turned_back) == 1
    retreat = turned_back[0]
    assert retreat.actor_id == "june"
    assert world.players["june"].position == AtRoom(room_id="dining_room")
    assert retreat.record_for("june") is not None


# ----------------------------------------------------------------- plans and cuts


def test_cut_plan_is_a_ledger_event_naming_the_dropped_steps() -> None:
    world = _world()
    world = submit_plan(
        world,
        "mara",
        Plan(goal="wander", steps=[Wait(), Wait(), Wait(), Wait(), Wait()]),
    )
    world = tick(world)
    world = advance_phase(world)
    cuts = [entry for entry in world.ledger if entry.type == "plan_cut"]
    mine = [entry for entry in cuts if entry.actor_id == "mara"]
    assert len(mine) == 1
    assert mine[0].payload["dropped"] == "4"
    assert mine[0].payload["kinds"] == "wait,wait,wait,wait"
    assert world.players["mara"].plan.steps == []


def test_a_cut_leaves_the_player_where_they_physically_were() -> None:
    world = _world()
    world = submit_plan(
        world,
        "mara",
        Plan(steps=[Move(room_id="chapel"), Wait(), Wait()]),
    )
    world = tick(world)
    world = advance_phase(world)
    position = world.players["mara"].position
    assert isinstance(position, InTransit)
    assert position.destination == "chapel"


def test_a_move_underway_finishes_across_a_phase_boundary() -> None:
    """Somebody standing in a passage has to come out somewhere."""
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="chapel")]))
    world = tick(world)
    world = advance_phase(world)
    assert world.phase == "mission"
    assert isinstance(world.players["mara"].position, InTransit)
    world = tick(world)
    assert world.players["mara"].position == AtRoom(room_id="chapel")


def test_plan_length_is_capped_at_five_steps() -> None:
    with pytest.raises(ValueError, match="at most 5"):
        Plan(steps=[Wait() for _ in range(6)])


# -------------------------------------------------------------------- gatherings


def test_a_gathering_summons_everyone_and_routes_them() -> None:
    world = _world()
    world = advance_phase(world)  # mission
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="chapel")]))
    for _ in range(3):
        world = tick(world)
    assert world.players["mara"].room_id == "chapel"

    world = advance_phase(world)  # free time
    world = advance_phase(world)  # round table: a gathering
    summons = [entry for entry in world.ledger if entry.type == "summoned"]
    assert len(summons) == 2
    assert world.players["mara"].plan.steps == [Move(room_id="great_hall")]
    assert world.players["sol"].plan.steps == []


def test_lateness_is_observed_with_a_direction() -> None:
    world = _world()
    world = advance_phase(world)
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="gallery")]))
    for _ in range(world.map.phase_ticks.ticks("mission")):
        world = tick(world)
    world = advance_phase(world)
    world = advance_phase(world)  # round table summons Mara back
    before = world.ledger_seq
    for _ in range(world.map.phase_ticks.ticks("round_table")):
        world = tick(world)
    arrivals = [
        entry
        for entry in world.ledger
        if entry.seq > before
        and entry.type == "move_arrive"
        and entry.actor_id == "mara"
    ]
    assert len(arrivals) == 1
    late = arrivals[0]
    assert late.tick > 0
    assert late.payload["origin"] == "gallery"
    watcher = late.record_for("sol")
    assert watcher is not None
    assert "Gallery" in watcher.text


# ------------------------------------------------------------------- objects


def test_taking_then_dropping_moves_custody_through_the_ledger() -> None:
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="dining_room")]))
    world = tick(world)
    world = submit_plan(world, "mara", Plan(steps=[Take(thing_id="silver_decanter")]))
    world = tick(world)
    assert world.placements["silver_decanter"].where == "held"

    world = submit_plan(world, "mara", Plan(steps=[Drop(thing_id="silver_decanter")]))
    world = tick(world)
    placement = world.placements["silver_decanter"]
    assert isinstance(placement, InRoom) and placement.room_id == "dining_room"
    chain = custody_chain(world.ledger, "silver_decanter")
    assert [link.ledger_seq for link in chain] == sorted(
        link.ledger_seq for link in chain
    )
    assert len(chain) == 3
    assert replay_placements(world.ledger) == world.placements


def test_a_container_cannot_be_carried() -> None:
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="gallery")]))
    world = tick(world)
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="armoury")]))
    for _ in range(2):
        world = tick(world)
    assert world.players["mara"].room_id == "armoury"
    world = submit_plan(world, "mara", Plan(steps=[Take(thing_id="cabinet")]))
    world = tick(world)
    assert any(
        entry.type == "blocked" and "cannot be carried" in entry.payload["reason"]
        for entry in world.ledger
    )


def test_hidden_objects_need_a_search() -> None:
    world = _world()
    world = submit_plan(world, "june", Plan(steps=[Move(room_id="gallery")]))
    world = tick(world)
    world = submit_plan(world, "june", Plan(steps=[Move(room_id="library")]))
    world = tick(world)
    assert world.players["june"].room_id == "library"
    assert "brass_key" not in visible_things(world, "june", "library")
    assert "take:brass_key" not in valid_actions(world, "june")

    world = submit_plan(world, "june", Plan(steps=[ToolStep(tool="search")]))
    world = tick(world)
    assert "brass_key" in visible_things(world, "june", "library")
    assert "brass_key" not in visible_things(world, "mara", "library")


# ----------------------------------------------------------------------- light


def test_each_light_level_records_what_the_spec_allows() -> None:
    """Bright names the actor; dim names them or describes them; dark gives only
    a silhouette, never the description that would identify them anyway."""
    world = _world()
    for _ in range(9):
        world = run_phase(world, RoutineDecider(seed=world.seed))
    counted = {"bright": 0, "dim": 0, "dark": 0}
    for entry in world.ledger:
        if entry.actor_id is None or entry.room_id is None:
            continue
        actor = world.players[entry.actor_id]
        lighting = world.map.room(entry.room_id).lighting
        for record in entry.witnesses:
            if record.sense != "saw":
                continue
            counted[lighting] += 1
            if lighting == "bright":
                assert record.recorded_actor == entry.actor_id
            elif lighting == "dark":
                assert record.recorded_actor is None
                assert record.text.startswith("someone")
                assert actor.appearance not in record.text
            elif record.recorded_actor is None:
                assert record.text.startswith(actor.appearance)
    assert all(counted.values()), counted


# -------------------------------------------------------------------- containers


def _walk(world: WorldState, player_id: str, *rooms: str) -> WorldState:
    """Walk one player through adjacent rooms, ticking until each arrival."""
    for room_id in rooms:
        world = submit_plan(world, player_id, Plan(steps=[Move(room_id=room_id)]))
        for _ in range(8):
            world = tick(world)
            if world.players[player_id].room_id == room_id:
                break
        assert world.players[player_id].room_id == room_id
    return world


_OPEN_THE_CABINET = Plan(
    steps=[
        ToolStep(tool="search"),
        Take(thing_id="letter_opener"),
        Drop(thing_id="letter_opener", into="cabinet"),
    ]
)


def test_a_search_shows_what_a_container_holds_to_the_searcher_only() -> None:
    world = _walk(_world(), "mara", "gallery", "armoury")
    world = _walk(world, "knox", "gallery", "armoury")
    assert "letter_opener" not in visible_things(world, "mara", "armoury")

    world = submit_plan(world, "mara", Plan(steps=[ToolStep(tool="search")]))
    world = tick(world)
    assert "letter_opener" in visible_things(world, "mara", "armoury")
    assert "letter_opener" not in visible_things(world, "knox", "armoury")
    found = world.ledger[-1].record_for("mara")
    assert found is not None and "letter opener" in found.text


def test_taking_from_a_container_names_it_in_the_custody_chain() -> None:
    world = _walk(_world(), "mara", "gallery", "armoury")
    world = submit_plan(world, "mara", Plan(steps=_OPEN_THE_CABINET.steps[:2]))
    world = tick(world)
    world = tick(world)
    assert world.placements["letter_opener"] == HeldBy(player_id="mara")
    chain = custody_chain(world.ledger, "letter_opener")
    assert chain[-1].text == "Mara took the letter opener from the armoury cabinet."
    assert replay_placements(world.ledger) == world.placements


def test_stowing_puts_a_held_object_into_a_visible_container() -> None:
    world = _walk(_world(), "mara", "gallery", "armoury")
    world = submit_plan(world, "mara", Plan(steps=_OPEN_THE_CABINET.steps[:2]))
    world = tick(world)
    world = tick(world)
    assert "drop:letter_opener:into:cabinet" in valid_actions(world, "mara")

    world = submit_plan(world, "mara", Plan(steps=_OPEN_THE_CABINET.steps[2:]))
    world = tick(world)
    assert world.placements["letter_opener"] == InContainer(thing_id="cabinet")
    stowed = [entry for entry in world.ledger if entry.type == "stow"]
    assert [entry.text for entry in stowed] == [
        "Mara put the letter opener in the armoury cabinet."
    ]
    assert replay_placements(world.ledger) == world.placements


def test_stowing_needs_a_container_in_the_same_room() -> None:
    world = _walk(_world(), "mara", "dining_room")
    world = submit_plan(
        world,
        "mara",
        Plan(
            steps=[
                Take(thing_id="silver_decanter"),
                Drop(thing_id="silver_decanter", into="cabinet"),
                Drop(thing_id="silver_decanter", into="candle_stub"),
            ]
        ),
    )
    for _ in range(3):
        world = tick(world)
    assert world.placements["silver_decanter"] == HeldBy(player_id="mara")
    reasons = [
        entry.payload["reason"] for entry in world.ledger if entry.type == "blocked"
    ]
    assert reasons == ["there is nothing here to put it in"] * 2


def test_stowing_needs_the_object_in_hand() -> None:
    world = _walk(_world(), "mara", "gallery", "armoury")
    world = submit_plan(
        world, "mara", Plan(steps=[Drop(thing_id="silver_decanter", into="cabinet")])
    )
    world = tick(world)
    assert world.ledger[-1].type == "blocked"
    assert world.ledger[-1].payload["reason"] == "that is not in hand"


def test_a_drop_cannot_both_stow_and_hide() -> None:
    with pytest.raises(ValueError, match="not both"):
        Drop(thing_id="letter_opener", into="cabinet", hidden=True)


def test_a_stow_is_heard_next_door_without_a_name() -> None:
    world = _walk(_world(), "knox", "gallery")
    world = _walk(world, "mara", "gallery", "armoury")
    world = submit_plan(world, "mara", _OPEN_THE_CABINET)
    for _ in range(3):
        world = tick(world)
    stow = next(entry for entry in world.ledger if entry.type == "stow")
    heard = stow.record_for("knox")
    assert heard is not None
    assert heard.sense == "heard"
    assert heard.text == "a lid closing in the Armoury"
    assert heard.recorded_actor is None


# --------------------------------------------------------------------- interrupts


def test_an_exhausted_plan_interrupts() -> None:
    world = _world()
    assert interrupted(world, "mara", 0)
    world = submit_plan(world, "mara", Plan(steps=[Wait(), Wait()]))
    assert not interrupted(world, "mara", world.ledger_seq)


def test_overheard_noise_does_not_interrupt() -> None:
    """A listener next door learns something happened, not that a plan is stale."""
    world = _world()
    world.players["sol"].position = AtRoom(room_id="kitchen")
    world.players["june"].position = AtRoom(room_id="dining_room")
    world = submit_plan(world, "june", Plan(steps=[Wait(), Wait(), Wait()]))
    world = submit_plan(world, "sol", Plan(steps=[ToolStep(tool="search"), Wait()]))
    before = world.ledger_seq
    world = tick(world)

    others = [
        record
        for entry in world.ledger
        if entry.seq > before
        for record in entry.witnesses
        if record.player_id == "june" and record.sense != "own"
    ]
    assert [record.sense for record in others] == ["heard"]
    assert "Kitchen" in others[0].text
    assert others[0].recorded_actor is None
    assert not would_interrupt(world, "june", before)


def test_a_blocked_step_interrupts_its_own_actor() -> None:
    world = _world()
    world = submit_plan(
        world,
        "mara",
        Plan(steps=[Move(room_id="crypt"), Wait()]),
    )
    before = world.ledger_seq
    world = tick(world)
    assert interrupted(world, "mara", before)


def test_the_replan_budget_caps_re_prompts() -> None:
    world = _world()
    world = submit_plan(world, "mara", Plan(steps=[Move(room_id="crypt"), Wait()]))
    before = world.ledger_seq
    world = tick(world)
    assert would_interrupt(world, "mara", before)

    assert replans_left(world, "mara") == 2
    assert interrupted(world, "mara", before)
    world.replans["mara"] = 2
    assert replans_left(world, "mara") == 0
    assert would_interrupt(world, "mara", before)
    assert not interrupted(world, "mara", before)


def test_a_capped_replan_is_recorded_rather_than_looking_like_a_choice() -> None:
    world = _world()
    world.map.max_replans_per_phase = 0
    world = advance_phase(world)  # mission: not a gathering
    world = run_phase(world, RoutineDecider(seed=world.seed))
    capped = [entry for entry in world.ledger if entry.type == "replan_capped"]
    assert capped, "a spent decision budget must leave a trace"
    assert all(
        [record.player_id for record in entry.witnesses] == [entry.actor_id]
        for entry in capped
    )


def test_director_stays_within_three_decisions_per_player_per_phase() -> None:
    world = _world()
    for _ in range(3):
        stats = DirectorStats()
        world = run_phase(world, RoutineDecider(seed=world.seed), stats=stats)
        assert stats.most() <= 3, stats.calls


# ------------------------------------------------------------------- properties
#
# Two harnesses, both drawn by Hypothesis rather than run on RoutineDecider, which
# never drops, hides or stows anything.
#
# Through the director, plans are drawn from each viewer's legal actions plus
# deliberately illegal steps. That tests what the full loop does — interrupts,
# re-prompts, phase cuts — for perception soundness, checked in ``on_tick`` after
# every tick, and for determinism, by replaying the recorded decisions.
#
# Custody is tested by ``CastleMachine`` below, which drives world transitions
# directly so that every way an object can change hands is actually reached.

_LINES = (
    "Where were you before the gathering?",
    "I saw nothing, and I was in the Gallery.",
    "Knox had the key. I watched him take it.",
    "Somebody went down to the cellar.",
)

_Log = list[tuple[str, int, Plan]]


@st.composite
def _drop(draw_from: st.DrawFn, thing_ids: list[str], boxes: list[str]) -> Drop:
    thing_id = draw_from(st.sampled_from(thing_ids))
    choice = draw_from(st.sampled_from(("show", "hide", "stow")))
    if choice == "stow" and boxes:
        return Drop(thing_id=thing_id, into=draw_from(st.sampled_from(boxes)))
    return Drop(thing_id=thing_id, hidden=choice == "hide")


def _legal(action: str, present: list[str]) -> st.SearchStrategy[Step]:
    """The step a valid-action string names, with free parts drawn."""
    kind, _, rest = action.partition(":")
    if kind == "move":
        return st.just(Move(room_id=rest))
    if kind == "take":
        return st.just(Take(thing_id=rest))
    if kind == "use":
        return st.just(Use(thing_id=rest))
    if kind == "drop":
        thing_id, _, box = rest.partition(":into:")
        if box:
            return st.just(Drop(thing_id=thing_id, into=box))
        return st.builds(Drop, thing_id=st.just(thing_id), hidden=st.booleans())
    if kind == "tool" and rest == "search":
        return st.just(ToolStep(tool="search"))
    if kind == "tool":
        return st.just(ToolStep(tool="inspect", thing_id=rest.removeprefix("inspect:")))
    if kind == "talk":
        targets = st.sampled_from([None, *present])
        return st.builds(Talk, text=st.sampled_from(_LINES), to=targets)
    return st.builds(Wait, reason=st.sampled_from(("", "watching the door")))


def _illegal(world: WorldState) -> st.SearchStrategy[Step]:
    """Steps that are usually impossible from wherever the player stands."""
    rooms = sorted(room.id for room in world.map.rooms)
    things = sorted(thing.id for thing in world.map.things)
    boxes = [thing.id for thing in world.map.things if thing.container]
    return st.one_of(
        st.builds(Move, room_id=st.sampled_from(rooms)),
        st.builds(Take, thing_id=st.sampled_from(things)),
        _drop(things, [*boxes, "candle_stub"]),
        st.builds(ToolStep, tool=st.just("inspect"), thing_id=st.sampled_from(things)),
        st.builds(
            Talk, text=st.just("Are you there?"), to=st.sampled_from(world.seats)
        ),
    )


def _kind(action: str) -> str:
    verb, _, rest = action.partition(":")
    if verb == "tool":
        return "search" if rest == "search" else "inspect"
    return verb


@st.composite
def _step(
    draw_from: st.DrawFn,
    observation: ScopedObservation,
    illegal: st.SearchStrategy[Step],
) -> Step:
    """Pick a kind of step first, then an action of that kind.

    Sampling actions directly would drown the one ``drop`` a player can make
    under four exits and six things to use; choosing the kind first gives
    letting go of something the same chance as walking away.
    """
    kinds: dict[str, list[str]] = {}
    for action in observation.valid_actions:
        kinds.setdefault(_kind(action), []).append(action)
    kind = draw_from(st.sampled_from([*sorted(kinds), "illegal"]))
    if kind == "illegal":
        return draw_from(illegal)
    action = draw_from(st.sampled_from(kinds[kind]))
    return draw_from(_legal(action, observation.present))


@dataclass
class _Drawn:
    """A decider whose every plan is a Hypothesis draw, logged for replay."""

    data: st.DataObject
    world: WorldState
    log: _Log

    def __call__(self, observation: ScopedObservation) -> Plan:
        label = f"{observation.viewer_id}@t{observation.tick}"
        steps = self.data.draw(
            st.lists(
                _step(observation, _illegal(self.world)),
                max_size=MAX_PLAN_STEPS,
            ),
            label=label,
        )
        # Phases are short and a queued drop is usually cut before it runs, so a
        # player holding something may open the plan by letting go of it.
        letting_go = [
            action for action in observation.valid_actions if _kind(action) == "drop"
        ]
        if letting_go and self.data.draw(st.booleans(), label=f"{label} let go"):
            action = self.data.draw(st.sampled_from(letting_go), label=f"{label} how")
            first = self.data.draw(_legal(action, observation.present))
            steps = [first, *steps][:MAX_PLAN_STEPS]
        plan = Plan(goal="drawn", steps=steps)
        self.log.append((observation.viewer_id, observation.tick, plan))
        return plan


@dataclass
class _Replay:
    """Plays back a logged run, failing at the first ask that differs."""

    log: _Log
    cursor: int = 0

    def __call__(self, observation: ScopedObservation) -> Plan:
        viewer_id, at_tick, plan = self.log[self.cursor]
        assert (observation.viewer_id, observation.tick) == (viewer_id, at_tick), (
            f"ask {self.cursor} went to {observation.viewer_id}@t{observation.tick}, "
            f"expected {viewer_id}@t{at_tick}"
        )
        self.cursor += 1
        return plan


def _drawn_layout(
    data: st.DataObject,
    castle: CastleMap,
    busy: list[str],
) -> CastleMap:
    """The shipped castle with every object moved to a drawn starting place.

    The invariants must hold for any valid layout, and a scattered one puts
    portable objects where players actually are, so custody changes hands every
    way the world allows. Furniture stands in the open in a room somebody is
    in, since a stow needs a holder and a visible container in the same room.
    The variant is linted like any authored map before it is used.
    """
    rooms = sorted(room.id for room in castle.rooms)
    boxes = [thing.id for thing in castle.things if thing.container]
    placements: dict[str, Placement] = {}
    for thing in castle.things:
        if thing.container:
            room_id = data.draw(st.sampled_from(busy), label=f"room:{thing.id}")
            placements[thing.id] = InRoom(room_id=room_id)
        elif boxes and data.draw(st.booleans(), label=f"boxed:{thing.id}"):
            box = data.draw(st.sampled_from(boxes), label=f"box:{thing.id}")
            placements[thing.id] = InContainer(thing_id=box)
        else:
            placements[thing.id] = InRoom(
                room_id=data.draw(st.sampled_from(rooms), label=f"room:{thing.id}"),
                hidden=data.draw(st.booleans(), label=f"hidden:{thing.id}"),
            )
    layout = castle.model_copy(update={"placements": placements})
    lint_map(layout)
    return layout


def _setup(data: st.DataObject) -> tuple[WorldState, int]:
    """A drawn world, and how many phases to play in it.

    The seed, the layout, every player's starting room and the phase the run
    opens in are all drawn, so runs begin scattered through dark rooms and
    beside the cabinet rather than always in the Great Hall at breakfast.
    """
    castle = load_map(MAP_PATH)
    cast = [dict(member) for member in load_cast(CAST_PATH)]
    free = {room.id: room.capacity for room in castle.rooms}
    for member in cast:
        rooms = sorted(room_id for room_id, left in free.items() if left)
        room_id = data.draw(st.sampled_from(rooms), label=f"start:{member['id']}")
        free[room_id] -= 1
        member["start_room"] = room_id
    busy = sorted(
        {str(member["start_room"]) for member in cast} | {castle.gathering_room}
    )
    castle = _drawn_layout(data, castle, busy)
    seed = data.draw(st.integers(min_value=0, max_value=4096), label="seed")
    world = start(build_world(castle, cast, world_id="castle-drawn", seed=seed))
    for _ in range(data.draw(st.integers(min_value=0, max_value=5), label="skip")):
        world = advance_phase(world)
    return world, data.draw(st.integers(min_value=1, max_value=3), label="phases")


def _play(
    world: WorldState,
    phases: int,
    decider: Decider,
    on_tick: TickObserver | None = None,
) -> WorldState:
    for _ in range(phases):
        world = run_phase(world, decider, on_tick=on_tick)
    return world


def _assert_custody(world: WorldState) -> None:
    things = {thing.id for thing in world.map.things}
    assert set(world.placements) == things
    for thing_id, placement in world.placements.items():
        if isinstance(placement, HeldBy):
            assert placement.player_id in world.players
        else:
            assert room_of(world, thing_id) in {room.id for room in world.map.rooms}
    assert replay_placements(world.ledger) == world.placements


_PROPERTY = settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@_PROPERTY
@given(st.data())
def test_property_perception_soundness(data: st.DataObject) -> None:
    """Through the director, at every tick, every line any player could be shown
    traces to a ledger entry that player witnessed."""
    world, phases = _setup(data)

    def sound(state: WorldState) -> None:
        for player_id in state.seats:
            assert_no_leak(build_observation(state, player_id), state)

    _play(world, phases, _Drawn(data, world, []), on_tick=sound)


@_PROPERTY
@given(st.data())
def test_property_determinism(data: st.DataObject) -> None:
    """Through the director, the same seed and the same decisions give a
    byte-identical world, interrupts and re-prompts included."""
    world, phases = _setup(data)
    log: _Log = []
    first = _play(world, phases, _Drawn(data, world, log))
    replay = _Replay(log)
    second = _play(world, phases, replay)
    assert replay.cursor == len(log)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# ------------------------------------------------------------ the world machine
#
# Custody is a property of world transitions, so it is tested by driving them
# directly. Rules have preconditions — a stow only fires when a holder stands
# beside a visible container — which reaches every custody path reliably, where
# random plans through the director reach the deeper ones only by luck. Every
# invariant is checked after every transition: each plan, each tick, each phase.

_Op = tuple[str, str, Plan] | tuple[str]
_COVERED: Counter[str] = Counter()


class CastleMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.initial: WorldState | None = None
        self.world: WorldState | None = None
        self.ops: list[_Op] = []
        self.seen: list[LedgerEntry] = []

    @initialize(data=st.data())
    def open(self, data: st.DataObject) -> None:
        world, _ = _setup(data)
        self.initial = world
        self.world = world
        self._check()

    # ------------------------------------------------------------ transitions

    def _now(self) -> WorldState:
        assert self.world is not None
        return self.world

    def _plan(self, player_id: str, *steps: Step) -> None:
        plan = Plan(goal="machine", steps=list(steps))
        self.ops.append(("plan", player_id, plan))
        self.world = submit_plan(self._now(), player_id, plan)
        self._check()

    def _tick(self) -> None:
        self.ops.append(("tick",))
        self.world = tick(self._now())
        self._check()
        if phase_complete(self._now()):
            self._advance()

    def _advance(self) -> None:
        self.ops.append(("advance",))
        self.world = advance_phase(self._now())
        self._check()

    def _act(self, player_id: str, *steps: Step) -> None:
        """Give one player a plan and tick until it is spent or cut."""
        self._plan(player_id, *steps)
        for _ in range(12):
            player = self._now().players[player_id]
            if not player.plan.steps and not isinstance(player.position, InTransit):
                return
            self._tick()

    # -------------------------------------------------------------- invariants

    def _check(self) -> None:
        world = self._now()
        _assert_custody(world)
        for player_id in world.seats:
            assert_no_leak(build_observation(world, player_id), world)
        assert world.ledger[: len(self.seen)] == self.seen, "ledger was rewritten"
        assert [entry.seq for entry in world.ledger] == list(
            range(1, len(world.ledger) + 1)
        )
        for entry in world.ledger[len(self.seen) :]:
            for roll in entry.rolls:
                again = draw(
                    world.seed, roll.cursor, roll.purpose, threshold=roll.threshold
                )
                assert again == roll, f"roll {roll.cursor} does not replay"
        self.seen = list(world.ledger)

    def teardown(self) -> None:
        """Replaying the same transitions from the same start gives the same world."""
        if self.initial is None or self.world is None:
            return
        _COVERED.update(_custody_kinds(self.world))
        world = self.initial
        for op in self.ops:
            if op[0] == "plan" and len(op) == 3:
                world = submit_plan(world, op[1], op[2])
            elif op[0] == "tick":
                world = tick(world)
            else:
                world = advance_phase(world)
        assert world.model_dump(mode="json") == self.world.model_dump(mode="json")

    # ------------------------------------------------------------------- rules

    def _seated(self) -> list[str]:
        world = self._now()
        return [pid for pid in world.seats if world.players[pid].room_id is not None]

    def _holders(self) -> list[str]:
        world = self._now()
        return [pid for pid in self._seated() if carried_things(world, pid)]

    def _stowers(self) -> list[tuple[str, str]]:
        world = self._now()
        boxes = {thing.id for thing in world.map.things if thing.container}
        pairs: list[tuple[str, str]] = []
        for pid in self._holders():
            room_id = world.players[pid].room_id
            assert room_id is not None
            for box in visible_things(world, pid, room_id):
                if box in boxes:
                    pairs.append((pid, box))
        return pairs

    def _takers(self) -> list[tuple[str, str]]:
        world = self._now()
        portable = {thing.id for thing in world.map.things if not thing.container}
        pairs: list[tuple[str, str]] = []
        for pid in self._seated():
            room_id = world.players[pid].room_id
            assert room_id is not None
            pairs.extend(
                (pid, thing_id)
                for thing_id in visible_things(world, pid, room_id)
                if thing_id in portable
            )
        return pairs

    @precondition(lambda self: self.world is not None and bool(self._seated()))
    @rule(data=st.data())
    def walk(self, data: st.DataObject) -> None:
        pid = data.draw(st.sampled_from(self._seated()), label="who")
        room_id = self._now().players[pid].room_id
        assert room_id is not None
        exits = self._now().map.neighbors(room_id)
        self._act(pid, Move(room_id=data.draw(st.sampled_from(exits), label="to")))

    @precondition(lambda self: self.world is not None and bool(self._seated()))
    @rule(data=st.data())
    def search(self, data: st.DataObject) -> None:
        self._act(data.draw(st.sampled_from(self._seated())), ToolStep(tool="search"))

    @precondition(lambda self: self.world is not None and bool(self._takers()))
    @rule(data=st.data())
    def take(self, data: st.DataObject) -> None:
        pid, thing_id = data.draw(st.sampled_from(self._takers()), label="take")
        self._act(
            pid, ToolStep(tool="inspect", thing_id=thing_id), Take(thing_id=thing_id)
        )

    @precondition(lambda self: self.world is not None and bool(self._holders()))
    @rule(data=st.data())
    def drop(self, data: st.DataObject) -> None:
        pid = data.draw(st.sampled_from(self._holders()), label="who")
        thing_id = data.draw(st.sampled_from(carried_things(self._now(), pid)))
        self._act(pid, Drop(thing_id=thing_id, hidden=data.draw(st.booleans())))

    @precondition(lambda self: self.world is not None and bool(self._stowers()))
    @rule(data=st.data())
    def stow(self, data: st.DataObject) -> None:
        pid, box = data.draw(st.sampled_from(self._stowers()), label="stow")
        thing_id = data.draw(st.sampled_from(carried_things(self._now(), pid)))
        self._act(pid, Drop(thing_id=thing_id, into=box))

    @precondition(lambda self: self.world is not None and bool(self._seated()))
    @rule(data=st.data())
    def talk(self, data: st.DataObject) -> None:
        pid = data.draw(st.sampled_from(self._seated()), label="who")
        self._act(pid, Talk(text=data.draw(st.sampled_from(_LINES))))

    @precondition(lambda self: self.world is not None)
    @rule(data=st.data())
    def blunder(self, data: st.DataObject) -> None:
        """Anyone attempts something that is usually impossible."""
        world = self._now()
        pid = data.draw(st.sampled_from(world.seats), label="who")
        self._act(pid, data.draw(_illegal(world), label="blunder"))

    @precondition(lambda self: self.world is not None)
    @rule()
    def wait_a_tick(self) -> None:
        self._tick()

    @precondition(lambda self: self.world is not None)
    @rule()
    def end_the_phase(self) -> None:
        self._advance()


def _custody_kinds(world: WorldState) -> set[str]:
    kinds: set[str] = set()
    for entry in world.ledger:
        if entry.type == "take":
            kinds.add("take-from-container" if " from the " in entry.text else "take")
        elif entry.type == "drop":
            kinds.add("drop-hidden" if entry.payload["hidden"] == "true" else "drop")
        elif entry.type in {"stow", "search", "inspect", "blocked"}:
            kinds.add(entry.type)
    return kinds


TestCastleMachine = CastleMachine.TestCase
TestCastleMachine.settings = settings(
    max_examples=25,
    stateful_step_count=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def test_the_machine_reaches_every_custody_path() -> None:
    """Guards the machine above: it must move objects every way the world allows,
    or the custody invariant it checks proves little. Derandomized, so it is
    deterministic rather than lucky."""
    _COVERED.clear()
    run_state_machine_as_test(
        CastleMachine,
        settings=settings(
            max_examples=25,
            stateful_step_count=30,
            deadline=None,
            derandomize=True,
            database=None,
            suppress_health_check=[HealthCheck.too_slow],
        ),
    )
    wanted = {
        "take",
        "take-from-container",
        "drop",
        "drop-hidden",
        "stow",
        "search",
        "inspect",
        "blocked",
    }
    assert wanted <= set(_COVERED), sorted(wanted - set(_COVERED))


def test_a_full_day_runs_and_stays_consistent() -> None:
    world = _world()
    world = run_day(world, RoutineDecider(seed=world.seed))
    assert world.day == 2
    assert world.tick == sum(world.map.phase_ticks.ticks(phase) for phase in DAY_ORDER)
    assert replay_placements(world.ledger) == world.placements
    for player_id in world.seats:
        assert_no_leak(build_observation(world, player_id), world)
