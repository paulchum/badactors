"""``castle-power``: how many seeds an arm needs, before anyone pays for one.

SPEC §7 asks for a power analysis on bot sweeps before any paid run. This one
plays a pilot — the mixed arm of a schedule, every entrant a reference bot
profile — and answers two questions from it, at a stated effect size, a
significance level and a power.

* **Ratings.** How many games before two neighbouring entrants, a rating gap
  apart, are told apart? The pilot is rated at checkpoints, and each role's
  sigma is fitted as ``c * n ** -b`` over the games an entrant played in that
  role; Weng-Lin sigma shrinks more slowly than ``1 / sqrt(n)``, so the
  exponent is fitted, not assumed. Two entrants are separated when the gap is
  at least ``(z_alpha + z_power) * sqrt(2) * sigma``.
* **Matrix cells.** How many attempts before two neighbouring cells of the
  attacker-by-target matrix, a success-rate gap apart around the pilot's base
  rate, are told apart by a two-proportion test? The pilot gives the attempts
  made per meeting of a traitor seat and a Faithful seat; each arm's schedule
  gives how often its rarest ordered pair meets per seed.

Both are extrapolations from bots, which attempt manipulation at their own
rate; a model that tries less needs more seeds. The plan says so beside the
numbers.

    uv run castle-power
    uv run castle-power --pilot-seeds 12 --rating-gap 2.5 --cell-gap 0.2 --json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from math import ceil, exp, log, sqrt
from pathlib import Path
from statistics import NormalDist
from typing import Any

from badactors.bench.dryrun import play_schedule, profiles
from badactors.bench.leaderboard import Finished, board
from badactors.bench.matrix import ALL, matrix
from badactors.bench.ratings import BETA
from badactors.castle.cast import load_cast_file
from badactors.castle.mapfile import load_map
from badactors.castle.rules import GameConfig
from badactors.castle.schedule import (
    ArmKind,
    Rotation,
    Scenario,
    Schedule,
    Seating,
    arm_games,
    invader_arm,
    single_arm,
)

_CONFIGS = Path(__file__).resolve().parents[3] / "configs" / "castle"

CHECKPOINTS = (0.25, 0.5, 0.75, 1.0)
"""The shares of the pilot rated, to see sigma shrink."""


def z(probability: float) -> float:
    return NormalDist().inv_cdf(probability)


@dataclass(frozen=True)
class Shrink:
    """Sigma as a function of games in one role: ``c * n ** -b``."""

    c: float
    b: float

    def sigma(self, games: float) -> float:
        shrunk: float = self.c * games**-self.b
        return shrunk

    def games_for(self, sigma: float) -> float:
        if self.b <= 0:
            return float("inf")
        ratio: float = (self.c / sigma) ** (1 / self.b)
        return ratio


def fit_shrink(points: Sequence[tuple[float, float]]) -> Shrink:
    """Least squares on ``log sigma = log c - b log n``."""
    usable = [(log(n), log(s)) for n, s in points if n > 0 and s > 0]
    if len(usable) < 2:
        raise ValueError("Sigma needs at least two points to fit.")
    mx = sum(x for x, _ in usable) / len(usable)
    my = sum(y for _, y in usable) / len(usable)
    sxx = sum((x - mx) ** 2 for x, _ in usable)
    if sxx == 0:
        raise ValueError("Sigma needs points at different game counts.")
    slope = sum((x - mx) * (y - my) for x, y in usable) / sxx
    return Shrink(c=exp(my - slope * mx), b=-slope)


def cell_attempts(
    p: float, gap: float, *, alpha: float = 0.05, power: float = 0.8
) -> float:
    """Attempts per cell for a two-proportion test to tell two success rates
    ``gap`` apart, centred on ``p`` where they fit and pushed inside [0, 1]
    where they do not."""
    if not 0 < gap <= 1:
        raise ValueError("The gap is a difference in success rate, in (0, 1].")
    p1 = min(max(p - gap / 2, 0.0), 1.0 - gap)
    p2 = p1 + gap
    mean = (p1 + p2) / 2
    a = z(1 - alpha / 2) * sqrt(2 * mean * (1 - mean))
    b = z(power) * sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return ((a + b) / gap) ** 2


def head_to_head(gap: float) -> float:
    """How often the stronger of two settled entrants a rating gap apart
    outranks the weaker, under the rating's own model."""
    return 1 / (1 + exp(-gap / (sqrt(2) * BETA)))


# ------------------------------------------------------------------ the pilot


@dataclass(frozen=True)
class Pilot:
    seeds: int
    entrants: int
    games: list[Finished]
    shrink: dict[str, Shrink]
    """Per role: ``deception`` and ``detection``."""
    meetings: int
    """Times a traitor seat sat with a Faithful seat, over the pilot."""
    attempts: dict[str, int]
    successes: dict[str, int]


def run_pilot(
    scenario: Scenario,
    *,
    seeds: int,
    entrants: int,
    rotation: Rotation = "latin",
    seed: int = 0,
) -> Pilot:
    """Play the mixed arm with ``entrants`` reference bot profiles over
    ``seeds`` seeds, and read sigma's shrinking and the attempt rate from it."""
    levels = profiles(entrants, seed=seed)
    games = arm_games(
        scenario, "mixed", list(levels), seeds=range(seeds), rotation=rotation
    )
    played = play_schedule(games, scenario, levels)
    ordered = sorted(played, key=lambda f: (f.record.seed, f.id))
    points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for share in CHECKPOINTS:
        upto = ordered[: max(1, round(share * len(ordered)))]
        rated = board(upto, reference=True)
        for row in rated.rows.values():
            points["deception"].append((row.traitor_games, row.deception.sigma))
            points["detection"].append((row.faithful_games, row.detection.sigma))
    meetings = sum(
        len(f.game.starting_traitors)
        * (len(f.game.world.seats) - len(f.game.starting_traitors))
        for f in played
    )
    found = matrix(played, reference=True)
    attempts: Counter[str] = Counter()
    successes: Counter[str] = Counter()
    for attempt in found.attempts:
        for key in (ALL, attempt.tool):
            attempts[key] += 1
            successes[key] += int(attempt.success)
    return Pilot(
        seeds=seeds,
        entrants=entrants,
        games=played,
        shrink={
            role: fit_shrink(found_points) for role, found_points in points.items()
        },
        meetings=meetings,
        attempts=dict(attempts),
        successes=dict(successes),
    )


# ------------------------------------------------------------------ the arms


@dataclass(frozen=True)
class ArmLoad:
    """What one seed of an arm gives the rarest entrant and the rarest pair."""

    arm: ArmKind
    games: float
    """Games per seed."""
    traitor_games: float
    faithful_games: float
    """Per entrant per seed, the fewest any entrant gets."""
    meetings: float
    """Meetings per seed of the rarest ordered pair: its attacker in a traitor
    seat while its target sits in a Faithful one."""


def arm_load(games: Sequence[Seating]) -> ArmLoad:
    seeds = len({game.seed for game in games}) or 1
    traitor: Counter[tuple[str, str]] = Counter()
    faithful: Counter[tuple[str, str]] = Counter()
    met: Counter[tuple[str, str, str]] = Counter()
    groups: dict[str, set[str]] = defaultdict(set)
    for game in games:
        entrants = set(game.seats.values())
        groups[game.group] |= entrants
        for entrant in entrants:
            seats = [s for s, e in game.seats.items() if e == entrant]
            traitor[(game.group, entrant)] += any(s in game.traitors for s in seats)
            faithful[(game.group, entrant)] += any(
                s not in game.traitors for s in seats
            )
        for t in game.traitors:
            for seat, entrant in game.seats.items():
                if seat not in game.traitors:
                    met[(game.group, game.seats[t], entrant)] += 1
    pairs = [
        (group, a, b)
        for group, names in groups.items()
        for a in names
        for b in names
        if a != b or len(names) == 1
    ]
    keys = [(group, name) for group, names in groups.items() for name in names]
    return ArmLoad(
        arm=games[0].arm,
        games=len(games) / seeds,
        traitor_games=min(traitor[key] for key in keys) / seeds,
        faithful_games=min(faithful[key] for key in keys) / seeds,
        meetings=min(met[pair] for pair in pairs) / seeds,
    )


@dataclass(frozen=True)
class Need:
    arm: ArmKind
    load: ArmLoad
    rating_seeds: dict[str, int] = field(default_factory=dict)
    """Per role, for a rated arm."""
    cell_seeds: dict[str, int] = field(default_factory=dict)
    """Per technique."""


@dataclass(frozen=True)
class Plan:
    pilot: Pilot
    rating_gap: float
    cell_gap: float
    alpha: float
    power: float
    sigma_needed: float
    role_games: dict[str, int]
    cell_needed: dict[str, int]
    attempts_per_meeting: dict[str, float]
    base_rate: dict[str, float]
    needs: list[Need]

    def as_json(self) -> dict[str, Any]:
        pilot = self.pilot
        return {
            "pilot": {
                "seeds": pilot.seeds,
                "games": len(pilot.games),
                "entrants": pilot.entrants,
                "reference_bots": True,
                "sigma": {
                    role: {"c": round(s.c, 4), "b": round(s.b, 4)}
                    for role, s in pilot.shrink.items()
                },
                "meetings": pilot.meetings,
                "attempts": pilot.attempts,
                "successes": pilot.successes,
            },
            "effect": {
                "rating_gap": self.rating_gap,
                "head_to_head": round(head_to_head(self.rating_gap), 4),
                "cell_gap": self.cell_gap,
                "alpha": self.alpha,
                "power": self.power,
            },
            "ratings": {
                "sigma_needed": round(self.sigma_needed, 4),
                "games_per_entrant": self.role_games,
            },
            "cells": {
                technique: {
                    "base_rate": round(self.base_rate[technique], 4),
                    "attempts_per_cell": self.cell_needed[technique],
                    "attempts_per_meeting": round(
                        self.attempts_per_meeting[technique], 5
                    ),
                }
                for technique in self.cell_needed
            },
            "arms": [
                {
                    "arm": need.arm,
                    "games_per_seed": need.load.games,
                    "rating_seeds": need.rating_seeds or None,
                    "cell_seeds": need.cell_seeds,
                    "seeds": max(
                        [*need.rating_seeds.values(), need.cell_seeds.get(ALL, 0)]
                    ),
                }
                for need in self.needs
            ],
        }

    def summary(self) -> str:
        pilot = self.pilot
        lines = [
            f"POWER, from a reference-bot pilot: {pilot.seeds} seeds, "
            f"{len(pilot.games)} games, {pilot.entrants} entrants.",
            f"Effect sizes at alpha {self.alpha}, power {self.power}: a rating gap of "
            f"{self.rating_gap} (the stronger outranks the weaker "
            f"{head_to_head(self.rating_gap):.0%} of the time), and a cell gap of "
            f"{self.cell_gap:.0%} in success rate.",
            "",
            f"RATINGS: separating needs sigma <= {self.sigma_needed:.3f} in each role.",
        ]
        for role, shrink in pilot.shrink.items():
            lines.append(
                f"  {role:10} sigma ~ {shrink.c:.2f} * n^-{shrink.b:.2f}: "
                f"{self.role_games[role]:,} games in the role per entrant"
            )
        lines.append("")
        lines.append(
            "MATRIX CELLS: attempts each cell needs, at the bots' own attempt rate."
        )
        for technique, attempts in self.cell_needed.items():
            lines.append(
                f"  {technique:12} base {self.base_rate[technique]:5.1%}: {attempts:,} "
                f"attempts a cell; {self.attempts_per_meeting[technique]:.4f} per "
                "meeting"
            )
        lines.append("")
        lines.append("SEEDS PER ARM (the rarest entrant and the rarest pair bind)")
        for need in self.needs:
            ratings = (
                ", ".join(f"{role} {n:,}" for role, n in need.rating_seeds.items())
                if need.rating_seeds
                else "not rated"
            )
            lines.append(
                f"  {need.arm:14} {need.load.games:5.1f} games a seed; ratings: "
                f"{ratings}; cells (all techniques): {need.cell_seeds.get(ALL, 0):,}"
            )
        lines.append("")
        lines.append(
            "Bots attempt manipulation at their own rate; a model that tries less "
            "needs more seeds. Every arm is reported, null results included."
        )
        return "\n".join(lines)


def plan(
    pilot: Pilot,
    loads: Sequence[ArmLoad],
    *,
    rating_gap: float = 2.0,
    cell_gap: float = 0.15,
    alpha: float = 0.05,
    power: float = 0.8,
) -> Plan:
    sigma_needed = rating_gap / ((z(1 - alpha / 2) + z(power)) * sqrt(2))
    role_games = {
        role: ceil(shrink.games_for(sigma_needed))
        for role, shrink in pilot.shrink.items()
    }
    base_rate = {
        key: pilot.successes.get(key, 0) / count
        for key, count in pilot.attempts.items()
        if count
    }
    per_meeting = {
        key: count / pilot.meetings
        for key, count in pilot.attempts.items()
        if pilot.meetings
    }
    cell_needed = {
        key: ceil(cell_attempts(rate, cell_gap, alpha=alpha, power=power))
        for key, rate in sorted(base_rate.items(), key=lambda kv: (kv[0] != ALL, kv[0]))
    }
    needs: list[Need] = []
    for load in loads:
        rating_seeds = (
            {
                "deception": ceil(role_games["deception"] / load.traitor_games),
                "detection": ceil(role_games["detection"] / load.faithful_games),
            }
            if load.arm == "mixed" and load.traitor_games and load.faithful_games
            else {}
        )
        cell_seeds = {
            key: ceil(need / (per_meeting[key] * load.meetings))
            if per_meeting.get(key) and load.meetings
            else 0
            for key, need in cell_needed.items()
        }
        needs.append(
            Need(
                arm=load.arm,
                load=load,
                rating_seeds=rating_seeds,
                cell_seeds=cell_seeds,
            )
        )
    return Plan(
        pilot=pilot,
        rating_gap=rating_gap,
        cell_gap=cell_gap,
        alpha=alpha,
        power=power,
        sigma_needed=sigma_needed,
        role_games=role_games,
        cell_needed=cell_needed,
        attempts_per_meeting=per_meeting,
        base_rate=base_rate,
        needs=needs,
    )


def arm_loads(
    scenario: Scenario,
    entrants: Sequence[str],
    *,
    rotation: Rotation = "latin",
    seeds: Sequence[int] = range(4),
) -> list[ArmLoad]:
    """What a seed of each arm of the Corruption Test gives, for this cast."""
    a, b = entrants[0], entrants[1]
    mixed = arm_games(scenario, "mixed", entrants, seeds=seeds, rotation=rotation)
    schedule = Schedule(
        players=len(scenario.cast.players),
        traitors=scenario.config.traitors,
        rotation=rotation,
        games=[
            *single_arm(scenario, [a], seeds=seeds),
            *mixed,
            *invader_arm(scenario, a, b, seeds=seeds, rotation=rotation),
            *arm_games(
                scenario, "names_visible", entrants, seeds=seeds, rotation=rotation
            ),
        ],
    )
    kinds: tuple[ArmKind, ...] = ("mixed", "names_visible", "invader", "single")
    return [arm_load(schedule.arm(kind)) for kind in kinds]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="castle-power",
        description="Estimate the seeds per arm a paid benchmark run needs, from a "
        "reference-bot pilot.",
    )
    parser.add_argument("--pilot-seeds", type=int, default=10)
    parser.add_argument(
        "--entrants", type=int, help="Profiles in the pilot (default: players)."
    )
    parser.add_argument("--players", type=int, default=8)
    parser.add_argument("--traitors", type=int, choices=(2, 3), default=2)
    parser.add_argument("--rotation", choices=("latin", "compact"), default="latin")
    parser.add_argument("--rating-gap", type=float, default=2.0)
    parser.add_argument("--cell-gap", type=float, default=0.15)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--power", type=float, default=0.8)
    parser.add_argument("--seed", type=int, default=0, help="Deals the profiles.")
    parser.add_argument("--cast", default=str(_CONFIGS / "cast.json"))
    parser.add_argument("--map", default=str(_CONFIGS / "map.json"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    scenario = Scenario(
        castle=load_map(args.map),
        cast=load_cast_file(args.cast).first(args.players),
        config=GameConfig.shipped(traitors=args.traitors),
    )
    entrants = args.entrants or args.players
    rotation: Rotation = args.rotation
    pilot = run_pilot(
        scenario,
        seeds=args.pilot_seeds,
        entrants=entrants,
        rotation=rotation,
        seed=args.seed,
    )
    names = list(profiles(entrants, seed=args.seed))
    estimate = plan(
        pilot,
        arm_loads(scenario, names, rotation=rotation),
        rating_gap=args.rating_gap,
        cell_gap=args.cell_gap,
        alpha=args.alpha,
        power=args.power,
    )
    print(json.dumps(estimate.as_json(), indent=1) if args.json else estimate.summary())


if __name__ == "__main__":
    main()


__all__ = [
    "ArmLoad",
    "Pilot",
    "Plan",
    "Shrink",
    "arm_load",
    "arm_loads",
    "cell_attempts",
    "fit_shrink",
    "head_to_head",
    "main",
    "plan",
    "run_pilot",
    "z",
]
