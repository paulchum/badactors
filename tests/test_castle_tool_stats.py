"""Across many seeds, every roll a tool draws comes up at the rate its number in
the balance file says, within sampling error.

Each sample is one use in a fresh scene with its own seed, so the samples are
independent. The tolerance is four standard errors of a proportion, which a
correct roll misses about once in fifteen thousand runs and a wrong number (off
by a tenth or more) fails reliably. Daily limits are lifted so one scene can be
reused where the tool allows.
"""

from __future__ import annotations

from collections.abc import Callable
from math import sqrt

import pytest

from badactors.castle.contracts import (
    CompareHands,
    Disguise,
    Forge,
    InspectSeal,
    Intent,
    Plant,
    Read,
    Sabotage,
    Steal,
    Talk,
    Tamper,
    WorldState,
    WornBy,
    Write,
)
from badactors.castle.decisions import Poison
from badactors.castle.night import poison_act, poison_turn, seat_dinner
from badactors.castle.rules import GameConfig
from badactors.castle.world import advance_phase
from castle_scenes import CAST, CASTLE
from castle_tools import act, give, last, paper, put, scene

SAMPLES = 600


def _within(hits: int, n: int, p: float) -> None:
    rate = hits / n
    tolerance = 4 * sqrt(p * (1 - p) / n)
    assert abs(rate - p) <= tolerance, f"{rate:.3f} is not {p} ± {tolerance:.3f}"


def _rate(sample: Callable[[int], bool]) -> int:
    return sum(sample(seed) for seed in range(SAMPLES))


def _forge(seed: int) -> bool:
    world = scene(seed)
    sheet = paper(world)
    give(world, "knox", sheet)
    world = act(world, "knox", Forge(thing_id=sheet, hand="mara", text="x — Mara"))
    return last(world, "forge").payload["tell"] == "true"


def _tamper(seed: int) -> bool:
    world = scene(seed)
    put(world, "knox", "study")
    world = act(
        world, "knox", Tamper(thing_id="camera_log", edit="erase", subject="mara")
    )
    return last(world, "tamper").payload["flagged"] == "true"


def _sabotage(seed: int) -> bool:
    world = advance_phase(scene(seed))
    world = act(world, "knox", Sabotage(thing_id="clock_case"))
    return last(world, "sabotage").payload["fumble"] == "true"


def _plant(seed: int) -> bool:
    world = scene(seed)
    give(world, "knox", "steward_seal")
    world = act(world, "knox", Plant(thing_id="steward_seal", to="mara"))
    return last(world, "plant").type == "plant"


def _steal(seed: int) -> bool:
    world = scene(seed)
    world = act(world, "knox", Steal(to="mara"))
    return last(world, "steal").payload.get("caught") != "true"


def _secret(seed: int) -> bool:
    world = scene(seed)
    put(world, "knox", "library")
    world = act(world, "knox", Read(thing_id="household_letters", subject="sol"))
    return last(world, "read").payload["found"] == "true"


def _compare(seed: int) -> bool:
    world = scene(seed)
    sheet = paper(world)
    give(world, "mara", sheet)
    world = act(world, "mara", Write(thing_id=sheet, text="An honest note."))
    put(world, "sol", "library")
    give(world, "sol", sheet)
    world = act(world, "sol", CompareHands(thing_id=sheet))
    compared = last(world, "compare_hands").record_for("sol")
    assert compared is not None
    return "inconclusive" not in compared.text


def _seal(seed: int) -> bool:
    from badactors.castle.contracts import Counterfeit

    world = scene(seed)
    sheet = paper(world)
    give(world, "knox", sheet)
    world = act(
        world,
        "knox",
        Counterfeit(
            thing_id=sheet,
            text="By order of the Steward.",
            intent=Intent(target="sol", effect="vote", want="mara", window="next_vote"),
        ),
    )
    give(world, "sol", sheet)
    world = act(world, "sol", InspectSeal(thing_id=sheet))
    return last(world, "inspect_seal").payload["detected"] == "true"


def _fooled(seed: int) -> bool:
    world = scene(seed)
    world.balance = world.balance.model_copy(update={"dim_recognition": 1.0})
    put(world, "knox", "gallery")
    put(world, "sol", "gallery")
    coat = next(
        t.id
        for t in world.map.things
        if world.placements.get(t.id) == WornBy(player_id="knox")
    )
    world = act(
        world, "knox", Disguise(thing_id=coat, as_player="mara"), Talk(text="Hm.")
    )
    record = world.ledger[-1].record_for("sol")
    assert record is not None
    return record.recorded_actor == "mara"


ROLLS: dict[str, tuple[Callable[[int], bool], Callable[[WorldState], float]]] = {
    "forge trace": (_forge, lambda w: w.balance.tools.forge.trace),
    "tamper trace": (_tamper, lambda w: w.balance.tools.tamper.trace),
    "sabotage trace": (_sabotage, lambda w: w.balance.tools.sabotage.trace),
    "plant success": (_plant, lambda w: w.balance.tools.plant.success),
    "steal success": (_steal, lambda w: w.balance.tools.steal.success),
    "letters give up a secret": (_secret, lambda w: w.balance.tools.read.secret),
    "compare_hands success": (
        _compare,
        lambda w: w.balance.tools.compare_hands.success,
    ),
    "inspect_seal success": (_seal, lambda w: w.balance.tools.inspect_seal.success),
    "disguise fools a witness": (_fooled, lambda w: w.balance.tools.disguise.success),
}


@pytest.mark.parametrize("name", sorted(ROLLS))
def test_each_roll_comes_up_at_its_configured_rate(name: str) -> None:
    sample, chance = ROLLS[name]
    p = chance(scene())
    assert 0 < p < 1, f"{name} is a certainty and is never rolled"
    _within(_rate(sample), SAMPLES, p)


def test_poison_residue_and_noticing_come_up_at_their_rates() -> None:
    from badactors.castle.game import living_faithful, living_traitors
    from badactors.castle.play import start_game

    residue = seen = noticing = 0
    for seed in range(SAMPLES // 2):
        game = start_game(CASTLE, CAST.first(8), seed=seed, config=GameConfig.shipped())
        world = game.world
        world.phase = "dinner"
        for pid in world.seats:
            put(world, pid, world.map.gathering_room)
        seat_dinner(game)
        traitor = living_traitors(game)[0]
        left, right = living_faithful(game)[:2]
        game.seating = [
            left,
            traitor,
            right,
            *[p for p in game.seating if p not in (left, traitor, right)],
        ]
        turn = poison_turn(game)
        assert turn is not None
        poison_act(game, traitor, turn[1], Poison(target=left))
        entry = next(e for e in reversed(game.world.ledger) if e.type == "poison")
        residue += entry.payload["residue"] == "true"
        noticing += 1
        seen += entry.payload["seen_by"] == right
    numbers = GameConfig.shipped().tools.poison
    _within(residue, SAMPLES // 2, numbers.trace)
    _within(seen, noticing, numbers.seen)


def test_a_wrong_rate_would_fail() -> None:
    """Guards the tolerance: a number off by a tenth is caught."""
    hits = _rate(_forge)
    p = scene().balance.tools.forge.trace
    with pytest.raises(AssertionError):
        _within(hits, SAMPLES, p + 0.1)
