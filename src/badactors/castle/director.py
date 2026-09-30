"""Run phases: ask for plans, execute ticks, re-prompt only on interruption.

This is the only layer that talks to a decider, and it exists to keep that
conversation cheap. A player is asked for a plan when a phase opens, and again
only when something happened that a five-step plan could not have anticipated —
somebody walked in, a step failed, an object changed hands in view, or the plan
ran out. ``max_replans_per_phase`` caps the rest, so the per-phase call count is a
property of the map rather than of how crowded a room happened to get: one opening
plan plus that many re-prompts, which at the default of two is SPEC's ceiling of
three calls per player per phase. Nobody is prompted on a phase's final tick,
since there would be no tick left to act on the answer.

Phase 1 has no model calls at all: a :class:`Decider` is a plain callable, and
:class:`RoutineDecider` below is deterministic. The harness phase swaps in a model
behind the same protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from badactors.castle.contracts import (
    GATHERINGS,
    Move,
    Plan,
    ScopedObservation,
    Step,
    Take,
    Talk,
    ToolStep,
    Wait,
    WorldState,
)
from badactors.castle.rng import draw
from badactors.castle.scope import assert_no_leak, build_observation
from badactors.castle.world import (
    advance_phase,
    note_replan,
    note_replan_capped,
    phase_complete,
    replans_left,
    submit_plan,
    tick,
    would_interrupt,
)


class Decider(Protocol):
    """Turns one player's scoped observation into its next plan."""

    def __call__(self, observation: ScopedObservation) -> Plan: ...


type TickObserver = Callable[[WorldState], None]
"""Called with the world after every tick and after every phase change."""

type Ask = Callable[[WorldState, str], WorldState]
"""Ask one player for a plan, and return the world with that plan submitted."""


@dataclass
class DirectorStats:
    """How many times each player was asked to decide, per phase."""

    calls: dict[str, int] = field(default_factory=dict)

    def record(self, player_id: str) -> None:
        self.calls[player_id] = self.calls.get(player_id, 0) + 1

    def most(self) -> int:
        return max(self.calls.values(), default=0)


def run_phase(
    state: WorldState,
    decider: Decider,
    *,
    stats: DirectorStats | None = None,
    check_leaks: bool = True,
    on_tick: TickObserver | None = None,
) -> WorldState:
    """Play the current phase to its tick budget and open the next one."""
    world = state
    tally = stats if stats is not None else DirectorStats()

    def ask(current: WorldState, player_id: str) -> WorldState:
        return _ask(current, player_id, decider, tally, check_leaks=check_leaks)

    if world.phase not in GATHERINGS:
        world = prompt_everyone(world, ask)
    while not phase_complete(world):
        before = world.ledger_seq
        world = tick(world)
        # No point asking for a plan there is no tick left to act on.
        if not phase_complete(world):
            world = prompt_interrupted(world, ask, before)
        if on_tick is not None:
            on_tick(world)
    world = advance_phase(world)
    if on_tick is not None:
        on_tick(world)
    return world


def run_day(
    state: WorldState,
    decider: Decider,
    *,
    stats: DirectorStats | None = None,
    check_leaks: bool = True,
    on_tick: TickObserver | None = None,
) -> WorldState:
    world = state
    for _ in range(6):
        world = run_phase(
            world,
            decider,
            stats=stats,
            check_leaks=check_leaks,
            on_tick=on_tick,
        )
    return world


def observe(
    state: WorldState,
    player_id: str,
    *,
    check_leaks: bool = True,
) -> ScopedObservation:
    observation = build_observation(state, player_id)
    if check_leaks:
        assert_no_leak(observation, state)
    return observation


def prompt_everyone(state: WorldState, ask: Ask) -> WorldState:
    """The opening prompt of a phase: every living, waking player, in seat order."""
    world = state
    for player_id in world.seats:
        player = world.players[player_id]
        if not player.alive or player.asleep:
            continue
        world = ask(world, player_id)
    return world


def prompt_interrupted(state: WorldState, ask: Ask, since_seq: int) -> WorldState:
    """Re-prompt whoever something has happened to since ``since_seq``, within
    the phase's decision budget, and record it when the budget runs out."""
    world = state
    for player_id in world.seats:
        if not would_interrupt(world, player_id, since_seq):
            continue
        # A gathering summons its own route; re-planning would let a player
        # quietly ignore the call, so only the plan's exhaustion is honoured.
        if world.phase in GATHERINGS and world.players[player_id].plan.steps:
            continue
        if replans_left(world, player_id) == 0:
            if not _already_capped(world, player_id):
                world = note_replan_capped(world, player_id)
            continue
        world = note_replan(world, player_id)
        world = ask(world, player_id)
    return world


def _already_capped(state: WorldState, player_id: str) -> bool:
    for entry in reversed(state.ledger):
        if entry.type == "phase_opened":
            return False
        if entry.type == "replan_capped" and entry.actor_id == player_id:
            return True
    return False


def _ask(
    state: WorldState,
    player_id: str,
    decider: Decider,
    stats: DirectorStats,
    *,
    check_leaks: bool,
) -> WorldState:
    observation = observe(state, player_id, check_leaks=check_leaks)
    stats.record(player_id)
    return submit_plan(state, player_id, decider(observation))


# ------------------------------------------------------------- deterministic bot


@dataclass
class RoutineDecider:
    """A seeded stand-in for a model. Pure: same seed, same plans, forever.

    It is not meant to play well. It talks to whoever is present, picks up and
    examines what it finds, turns over dark rooms and otherwise wanders — enough
    traffic to exercise perception, custody and the interrupt path without a single
    model call. At a gathering it stays put, which is what a household member would
    do and what makes an early departure worth noticing.
    """

    seed: int = 13

    def __call__(self, observation: ScopedObservation) -> Plan:
        if observation.room is None:
            return Plan(goal="keep walking", steps=[Wait(reason="in the passage")])
        salt = _salt(self.seed, observation)
        steps: list[Step] = []
        goal = "look around"

        loose = [
            view
            for view in observation.visible_objects
            if f"take:{view.id}" in observation.valid_actions
        ]
        if loose:
            target = loose[salt % len(loose)]
            goal = f"get hold of the {target.name}"
            steps.append(ToolStep(tool="inspect", thing_id=target.id))
            steps.append(Take(thing_id=target.id))
        elif _worth_searching(observation):
            goal = f"turn over the {observation.room.name}"
            steps.append(ToolStep(tool="search"))

        if observation.present:
            other = observation.present[salt % len(observation.present)]
            if not steps:
                goal = f"sound out {other}"
            steps.append(Talk(text=_line(observation, other), to=other))

        if _at_gathering(observation):
            if not steps:
                steps.append(Wait(reason="staying for the gathering"))
            return Plan(goal=goal, steps=steps[:5])

        exits = observation.room.exits
        if exits and len(steps) < 4:
            destination = exits[salt % len(exits)]
            if not steps:
                goal = f"cross to {destination}"
            steps.append(Move(room_id=destination))
        if not steps:
            steps.append(Wait(reason="watching the room"))
        return Plan(goal=goal, steps=steps[:5])


def _at_gathering(observation: ScopedObservation) -> bool:
    return observation.phase in GATHERINGS and observation.room is not None


def _worth_searching(observation: ScopedObservation) -> bool:
    room = observation.room
    if room is None or room.lighting == "bright" or observation.searched_here:
        return False
    return "tool:search" in observation.valid_actions


def _salt(seed: int, observation: ScopedObservation) -> int:
    roll = draw(
        seed,
        observation.tick * 1000 + len(observation.recent),
        f"routine:{observation.viewer_id}",
    )
    return int(roll.value * 1_000_003)


def _line(observation: ScopedObservation, other: str) -> str:
    """Say something a witness could later check, or contradict."""
    room = observation.room
    if room is None:
        return "Keep walking."
    heard = [item for item in observation.recent if item.sense == "heard"]
    # Ask about something half-seen: not something heard, which has its own
    # line, and not something said, since quoting a quote nests speech inside
    # speech and reads as noise in the timeline.
    unsure = [
        item
        for item in observation.recent
        if item.sense == "saw" and item.confidence != "clear" and item.quote is None
    ]
    carried = observation.carrying
    salt = _salt(observation.tick + 7, observation)
    options: list[str] = [
        f"I came straight here to the {room.name}. Ask anyone.",
        f"Nobody has told me where they were before the {room.name}.",
    ]
    if heard:
        noise = heard[-1].text.rstrip(".")
        options.append(f"I heard {noise}. Were you near it?")
    if unsure:
        seen = unsure[-1].text.rstrip(".")
        options.append(f"I thought I saw something: {seen}. Was that you?")
    if carried:
        options.append(f"I am holding the {carried[0].name}, and I say so plainly.")
    if observation.known_facts:
        options.append(f"Something here is worth knowing: {observation.known_facts[0]}")
    return options[salt % len(options)]


__all__ = [
    "Ask",
    "Decider",
    "DirectorStats",
    "RoutineDecider",
    "TickObserver",
    "observe",
    "prompt_everyone",
    "prompt_interrupted",
    "run_day",
    "run_phase",
]
