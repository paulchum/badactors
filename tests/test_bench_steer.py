"""The steer/resist fit, on made-up data with known answers and on a bot dry
run whose susceptibilities are known."""

from __future__ import annotations

import random

import pytest

from badactors.bench.dryrun import play_schedule, profiles
from badactors.bench.leaderboard import Finished
from badactors.bench.matrix import ALL, matrix
from badactors.bench.steer import bootstrap, fit, sigmoid, spearman, tally
from badactors.castle.rules import GameConfig
from badactors.castle.schedule import Scenario, mixed_schedule
from castle_scenes import CAST, CASTLE

STEER = {"a": -0.8, "b": 0.0, "c": 0.9}
RESIST = {"x": -0.5, "y": 0.8, "z": 2.0}


def expected_cells(n: int) -> dict[tuple[str, str], tuple[int, int]]:
    """Every pair met ``n`` times, with exactly the expected successes."""
    return {
        (a, t): (round(n * sigmoid(s - r)), n)
        for a, s in STEER.items()
        for t, r in RESIST.items()
    }


def test_the_fit_recovers_known_steer_and_resistance() -> None:
    fitted = fit(expected_cells(400))
    assert sorted(STEER, key=fitted.steer.__getitem__) == ["a", "b", "c"]
    assert sorted(RESIST, key=fitted.resist.__getitem__) == ["x", "y", "z"]
    for a, s in STEER.items():
        for t, r in RESIST.items():
            assert fitted.p(a, t) == pytest.approx(sigmoid(s - r), abs=0.02)
    assert fit(expected_cells(400)) == fitted


def test_an_unmet_pair_is_predicted_from_what_each_side_did_elsewhere() -> None:
    cells = expected_cells(300)
    del cells[("c", "z")]
    del cells[("a", "x")]
    fitted = fit(cells)
    assert fitted.p("c", "z") == pytest.approx(sigmoid(0.9 - 2.0), abs=0.06)
    assert fitted.p("a", "x") == pytest.approx(sigmoid(-0.8 + 0.5), abs=0.06)


def test_a_technique_that_never_works_stays_finite() -> None:
    fitted = fit({("a", "x"): (0, 40), ("b", "y"): (0, 30)})
    assert all(abs(v) < 20 for v in [*fitted.steer.values(), *fitted.resist.values()])
    assert fitted.p("a", "x") < 0.1


def test_bootstrap_intervals_bracket_the_fit_and_cover_every_pair() -> None:
    rng = random.Random(4)
    games = []
    for _ in range(120):
        game = []
        for _ in range(5):
            a, t = rng.choice(list(STEER)), rng.choice(list(RESIST))
            game.append((a, t, rng.random() < sigmoid(STEER[a] - RESIST[t])))
        games.append(game)
    result = bootstrap(
        games, attackers=list(STEER), targets=list(RESIST), resamples=60, seed=2
    )
    assert result.resamples == 60
    assert set(result.pairs) == {(a, t) for a in STEER for t in RESIST}
    for band in [*result.steer.values(), *result.resist.values()]:
        assert band.low <= band.estimate <= band.high
    for band in result.pairs.values():
        assert 0.0 <= band.low <= band.high <= 1.0
    again = bootstrap(
        games, attackers=list(STEER), targets=list(RESIST), resamples=60, seed=2
    )
    assert again.pairs == result.pairs
    assert result.observed == tally([x for game in games for x in game])


def test_spearman() -> None:
    assert spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 2], [5, 5, 9]) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        spearman([1], [1])


@pytest.fixture(scope="module")
def dry_run() -> tuple[dict[str, float], list[Finished]]:
    scenario = Scenario(castle=CASTLE, cast=CAST.first(8), config=GameConfig.shipped())
    levels = profiles(4, seed=0)
    schedule = mixed_schedule(scenario, list(levels), seeds=list(range(16)))
    return levels, play_schedule(schedule.games, scenario, levels)


def test_the_fit_recovers_the_bots_known_susceptibility_ordering(
    dry_run: tuple[dict[str, float], list[Finished]],
) -> None:
    """Reference bots fall for a manipulation by a seeded roll against a
    susceptibility the run dealt them, so the ordering is known: the fit over
    every technique must put the targets in that order."""
    levels, games = dry_run
    found = matrix(games, reference=True)
    pooled = found.fits(resamples=20)[ALL]
    entrants = found.entrants
    known = [levels[e.removeprefix("bot:heuristic/")] for e in entrants]
    estimated = [pooled.fit.susceptibility(e) for e in entrants]
    assert spearman(known, estimated) == pytest.approx(1.0)
    # Every attacker is the same policy, so none steers much better than another.
    steer = list(pooled.fit.steer.values())
    assert max(steer) - min(steer) < 1.5


def test_the_model_matrix_never_holds_a_bot_attempt(
    dry_run: tuple[dict[str, float], list[Finished]],
) -> None:
    _, games = dry_run
    models = matrix(games)
    assert models.attempts == [] and models.entrants == []
    assert len(models.refused) == len(games)
    bots = matrix(games, reference=True)
    shown = bots.as_json(resamples=10)
    assert shown["reference_bots"] and shown["label"].startswith("Reference bots")
    total = shown["techniques"][ALL]
    assert total["attempts"] == len(bots.attempts) > 0
    assert sum(cell["attempts"] for cell in total["cells"]) == total["attempts"]
    assert len(total["cells"]) == len(bots.entrants) ** 2
    assert all(cell["predicted"] is not None for cell in total["cells"])
    usage = shown["usage"]
    assert sum(row["attempts"] for row in usage.values()) == len(bots.attempts)
    for row in usage.values():
        assert row["traitor_games"] > 0
        assert 0 <= row["share_of_traitor_games_tried"] <= 1
    for target, by in shown["susceptibility"].items():
        assert by[ALL]["attempts"] == sum(
            rate["attempts"] for key, rate in by.items() if key != ALL
        ), target
