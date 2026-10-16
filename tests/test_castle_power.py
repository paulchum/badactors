"""castle-power: seeds per arm from a reference-bot pilot."""

from __future__ import annotations

import json

import pytest

from badactors.castle.power import (
    Pilot,
    Plan,
    arm_load,
    arm_loads,
    cell_attempts,
    fit_shrink,
    head_to_head,
    main,
    plan,
    run_pilot,
)
from badactors.castle.rules import GameConfig
from badactors.castle.schedule import Scenario, arm_games
from castle_scenes import CAST, CASTLE

SCENARIO = Scenario(castle=CASTLE, cast=CAST.first(8), config=GameConfig.shipped())


def test_sigma_shrinking_is_fitted_with_its_exponent() -> None:
    points = [(n, 8.0 * n**-0.4) for n in (2.0, 5.0, 11.0, 30.0)]
    shrink = fit_shrink(points)
    assert shrink.c == pytest.approx(8.0)
    assert shrink.b == pytest.approx(0.4)
    assert shrink.sigma(shrink.games_for(0.5)) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        fit_shrink([(3.0, 1.0), (3.0, 0.9)])


def test_a_cell_needs_the_textbook_two_proportion_sample() -> None:
    # 0.20 against 0.35 at alpha 0.05 and power 0.8: about 138 a group.
    assert cell_attempts(0.275, 0.15) == pytest.approx(137.9, abs=0.5)
    # A rate at the floor is tested against a real gap, not half of one.
    assert cell_attempts(0.0, 0.15) == pytest.approx(cell_attempts(0.075, 0.15))
    assert cell_attempts(0.3, 0.3) < cell_attempts(0.3, 0.15)
    with pytest.raises(ValueError):
        cell_attempts(0.3, 0.0)


def test_head_to_head() -> None:
    assert head_to_head(0.0) == pytest.approx(0.5)
    assert 0.5 < head_to_head(2.0) < head_to_head(4.0) < 1.0


def test_an_arms_load_is_what_one_seed_gives_the_rarest_entrant_and_pair() -> None:
    entrants = [f"p{i}" for i in range(8)]
    load = arm_load(arm_games(SCENARIO, "mixed", entrants, seeds=[0, 1]))
    assert (load.games, load.traitor_games, load.faithful_games) == (4, 1, 3)
    assert 0 < load.meetings <= 1
    latin = arm_load(
        arm_games(SCENARIO, "mixed", entrants, seeds=[0, 1], rotation="latin")
    )
    assert (latin.games, latin.traitor_games, latin.faithful_games) == (8, 2, 6)
    assert 1 <= latin.meetings <= 2
    loads = {found.arm: found for found in arm_loads(SCENARIO, entrants)}
    assert set(loads) == {"mixed", "names_visible", "invader", "single"}
    assert loads["single"].games == 1 and loads["single"].meetings == 2 * 6
    assert loads["invader"].games == 2 * 4


@pytest.fixture(scope="module")
def pilot_plan() -> tuple[Pilot, Plan]:
    pilot = run_pilot(SCENARIO, seeds=2, entrants=4)
    loads = arm_loads(SCENARIO, [f"p{i}" for i in range(4)])
    return pilot, plan(pilot, loads)


def test_the_plan_reads_the_pilot_and_gives_every_arm_seeds(
    pilot_plan: tuple[Pilot, Plan],
) -> None:
    pilot, found = pilot_plan
    assert len(pilot.games) == 2 * 4
    assert all(f.record.reference_bots for f in pilot.games)
    assert set(pilot.shrink) == {"deception", "detection"}
    assert all(shrink.b > 0 for shrink in pilot.shrink.values())
    by_arm = {need.arm: need for need in found.needs}
    assert set(by_arm["mixed"].rating_seeds) == {"deception", "detection"}
    assert all(n > 0 for n in by_arm["mixed"].rating_seeds.values())
    for arm in ("names_visible", "invader", "single"):
        assert by_arm[arm].rating_seeds == {}
    assert all(need.cell_seeds["all"] > 0 for need in found.needs)
    wider = plan(pilot, [need.load for need in found.needs], rating_gap=4.0)
    assert (
        wider.needs[0].rating_seeds["deception"]
        < by_arm["mixed"].rating_seeds["deception"]
    )
    text = found.summary()
    assert "SEEDS PER ARM" in text and "reference-bot pilot" in text
    json.dumps(found.as_json())


def test_castle_power_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--pilot-seeds", "2", "--entrants", "4", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["pilot"]["reference_bots"] is True
    assert {arm["arm"] for arm in data["arms"]} == {
        "mixed",
        "names_visible",
        "invader",
        "single",
    }
