"""Tick mechanics, the phase clock, and the invariants that must always hold."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from badactors.castle.contracts import (
    DAY_ORDER,
    AtRoom,
    Drop,
    InRoom,
    InTransit,
    Move,
    Plan,
    Take,
    ToolStep,
    Wait,
    WorldState,
)
from badactors.castle.director import (
    DirectorStats,
    RoutineDecider,
    run_day,
    run_phase,
)
from badactors.castle.ledger import custody_chain, replay_placements
from badactors.castle.mapfile import load_world
from badactors.castle.perceive import occupants, visible_things
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
        if record.player_id == "june" and record.channel != "own"
    ]
    assert [record.channel for record in others] == ["heard"]
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


@st.composite
def _played_worlds(draw_from: st.DrawFn) -> WorldState:
    seed = draw_from(st.integers(min_value=0, max_value=4096))
    phases = draw_from(st.integers(min_value=1, max_value=7))
    world = _world(seed)
    decider = RoutineDecider(seed=seed)
    for _ in range(phases):
        world = run_phase(world, decider, check_leaks=False)
    return world


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_played_worlds())
def test_property_custody_conservation(world: WorldState) -> None:
    """One placement per object at all times, and the cache is the ledger's fold."""
    assert set(world.placements) == {thing.id for thing in world.map.things}
    assert replay_placements(world.ledger) == world.placements
    holders = [
        placement.player_id
        for placement in world.placements.values()
        if placement.where == "held"
    ]
    assert all(holder in world.players for holder in holders)


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_played_worlds())
def test_property_perception_soundness(world: WorldState) -> None:
    """Every line a player could be shown traces to an entry they witnessed."""
    for player_id in world.seats:
        assert_no_leak(build_observation(world, player_id), world)


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=4096),
    phases=st.integers(min_value=1, max_value=7),
)
def test_property_determinism(seed: int, phases: int) -> None:
    """The same seed and the same decisions give a byte-identical ledger."""

    def play() -> WorldState:
        world = _world(seed)
        decider = RoutineDecider(seed=seed)
        for _ in range(phases):
            world = run_phase(world, decider, check_leaks=False)
        return world

    first, second = play(), play()
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_played_worlds())
def test_property_every_roll_is_replayable(world: WorldState) -> None:
    for entry in world.ledger:
        for roll in entry.rolls:
            again = draw(
                world.seed,
                roll.cursor,
                roll.purpose,
                threshold=roll.threshold,
            )
            assert again == roll


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_played_worlds())
def test_property_ledger_is_append_only_and_gapless(world: WorldState) -> None:
    assert [entry.seq for entry in world.ledger] == list(
        range(1, len(world.ledger) + 1)
    )
    assert all(
        later.tick >= earlier.tick
        for earlier, later in zip(world.ledger, world.ledger[1:], strict=False)
    )


def test_a_full_day_runs_and_stays_consistent() -> None:
    world = _world()
    world = run_day(world, RoutineDecider(seed=world.seed))
    assert world.day == 2
    assert world.tick == sum(world.map.phase_ticks.ticks(phase) for phase in DAY_ORDER)
    assert replay_placements(world.ledger) == world.placements
    for player_id in world.seats:
        assert_no_leak(build_observation(world, player_id), world)
