"""Who can steer whom: a fit of manipulation success, in pure Python.

For one technique, an attempt by attacker ``a`` on target ``t`` works with
probability ``sigmoid(v_t * (steer_a - resist_t))``: the attacker's skill at
steering against the target's resistance, scaled by how sharply the target's
resistance tells (``v_t``, kept positive as ``exp(u_t)``). It is a two-parameter
item-response model with targets as the items. Weak Gaussian priors on steer,
resistance and ``u`` make the fit unique and keep thin cells from running off
to infinity; the fit is their maximum a posteriori, found by preconditioned
gradient ascent with backtracking, so it is deterministic.

Confidence intervals come from a bootstrap over games, since attempts within
one game are not independent: the games are drawn again with replacement by a
seeded generator, the model refitted from the full fit, and the 2.5th and
97.5th percentiles kept. The fit predicts every pair, including pairs that
never met, with intervals from the same bootstrap.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import exp, log

type Cell = tuple[str, str]
"""(attacker, target)."""

STEER_PRIOR = 1.0
RESIST_PRIOR = 2.0
SLOPE_PRIOR = 0.5
"""Standard deviations of the priors on steer, resistance and log-slope."""


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + exp(-z))
    e = exp(z)
    return e / (1.0 + e)


@dataclass(frozen=True)
class Fit:
    """Fitted steer per attacker, and resistance and slope per target."""

    steer: dict[str, float]
    resist: dict[str, float]
    slope: dict[str, float]
    log_posterior: float
    iterations: int

    def p(self, attacker: str, target: str) -> float:
        """The predicted chance an attempt by ``attacker`` on ``target`` works."""
        return sigmoid(
            self.slope.get(target, 1.0)
            * (self.steer.get(attacker, 0.0) - self.resist.get(target, 0.0))
        )

    def susceptibility(self, target: str) -> float:
        """The chance an attempt works on ``target``, against the average
        attacker."""
        mean = sum(self.steer.values()) / len(self.steer) if self.steer else 0.0
        return sigmoid(
            self.slope.get(target, 1.0) * (mean - self.resist.get(target, 0.0))
        )


def tally(attempts: Sequence[tuple[str, str, bool]]) -> dict[Cell, tuple[int, int]]:
    """(successes, attempts) per (attacker, target)."""
    out: dict[Cell, list[int]] = defaultdict(lambda: [0, 0])
    for attacker, target, success in attempts:
        cell = out[(attacker, target)]
        cell[0] += int(success)
        cell[1] += 1
    return {key: (hits, of) for key, (hits, of) in out.items()}


def fit(
    cells: Mapping[Cell, tuple[int, int]],
    *,
    attackers: Sequence[str] | None = None,
    targets: Sequence[str] | None = None,
    start: Fit | None = None,
    max_iterations: int = 400,
    tolerance: float = 1e-7,
) -> Fit:
    """The maximum a posteriori fit to (successes, attempts) per cell."""
    a_names = sorted(attackers if attackers is not None else {a for a, _ in cells})
    t_names = sorted(targets if targets is not None else {t for _, t in cells})
    s = {a: (start.steer.get(a, 0.0) if start else 0.0) for a in a_names}
    r = {t: (start.resist.get(t, 0.0) if start else 0.0) for t in t_names}
    u = {
        t: (log(start.slope[t]) if start and t in start.slope else 0.0) for t in t_names
    }
    data = [(a, t, k, n) for (a, t), (k, n) in sorted(cells.items()) if n > 0]

    def objective(
        s: Mapping[str, float], r: Mapping[str, float], u: Mapping[str, float]
    ) -> float:
        total = 0.0
        for a, t, k, n in data:
            z = exp(u[t]) * (s[a] - r[t])
            total += k * _log_sigmoid(z) + (n - k) * _log_sigmoid(-z)
        total -= sum(x * x for x in s.values()) / (2 * STEER_PRIOR**2)
        total -= sum(x * x for x in r.values()) / (2 * RESIST_PRIOR**2)
        total -= sum(x * x for x in u.values()) / (2 * SLOPE_PRIOR**2)
        return total

    current = objective(s, r, u)
    iterations = 0
    for iterations in range(1, max_iterations + 1):  # noqa: B007
        gs = {a: -s[a] / STEER_PRIOR**2 for a in s}
        gr = {t: -r[t] / RESIST_PRIOR**2 for t in r}
        gu = {t: -u[t] / SLOPE_PRIOR**2 for t in u}
        hs = dict.fromkeys(s, 1 / STEER_PRIOR**2)
        hr = dict.fromkeys(r, 1 / RESIST_PRIOR**2)
        hu = dict.fromkeys(u, 1 / SLOPE_PRIOR**2)
        for a, t, k, n in data:
            v = exp(u[t])
            gap = s[a] - r[t]
            p = sigmoid(v * gap)
            residual = k - n * p
            curvature = n * p * (1 - p)
            gs[a] += residual * v
            gr[t] -= residual * v
            gu[t] += residual * v * gap
            hs[a] += curvature * v * v
            hr[t] += curvature * v * v
            hu[t] += curvature * (v * gap) ** 2 + abs(residual * v * gap)
        step = 1.0
        while True:
            ns = {a: s[a] + step * gs[a] / hs[a] for a in s}
            nr = {t: r[t] + step * gr[t] / hr[t] for t in r}
            nu = {t: u[t] + step * gu[t] / hu[t] for t in u}
            proposed = objective(ns, nr, nu)
            if proposed >= current or step < 1e-6:
                break
            step /= 2
        gained = proposed - current
        if gained < 0:
            break
        s, r, u, current = ns, nr, nu, proposed
        if gained < tolerance:
            break
    return Fit(
        steer=s,
        resist=r,
        slope={t: exp(value) for t, value in u.items()},
        log_posterior=current,
        iterations=iterations,
    )


def _log_sigmoid(z: float) -> float:
    return -log(1.0 + exp(-z)) if z >= 0 else z - log(1.0 + exp(z))


@dataclass(frozen=True)
class Band:
    estimate: float
    low: float
    high: float

    def as_json(self) -> dict[str, float]:
        return {
            "estimate": round(self.estimate, 4),
            "low": round(self.low, 4),
            "high": round(self.high, 4),
        }


@dataclass(frozen=True)
class Bootstrapped:
    """A fit and its bootstrap intervals."""

    fit: Fit
    steer: dict[str, Band]
    resist: dict[str, Band]
    susceptibility: dict[str, Band]
    pairs: dict[Cell, Band]
    """The predicted chance of success for every attacker and target, met or
    not."""
    observed: dict[Cell, tuple[int, int]]
    resamples: int


def bootstrap(
    games: Sequence[Sequence[tuple[str, str, bool]]],
    *,
    attackers: Sequence[str],
    targets: Sequence[str],
    resamples: int = 200,
    seed: int = 0,
) -> Bootstrapped:
    """Fit all the games' attempts, then refit ``resamples`` times on games
    drawn with replacement. ``games`` holds each game's (attacker, target,
    success) attempts."""
    every = [attempt for game in games for attempt in game]
    observed = tally(every)
    full = fit(observed, attackers=attackers, targets=targets)
    rng = random.Random(seed)
    draws: dict[str, dict[str, list[float]]] = {
        "steer": defaultdict(list),
        "resist": defaultdict(list),
        "susceptibility": defaultdict(list),
    }
    pair_draws: dict[Cell, list[float]] = defaultdict(list)
    for _ in range(resamples if games else 0):
        picked = [games[rng.randrange(len(games))] for _ in games]
        again = fit(
            tally([attempt for game in picked for attempt in game]),
            attackers=attackers,
            targets=targets,
            start=full,
        )
        for a in attackers:
            draws["steer"][a].append(again.steer[a])
        for t in targets:
            draws["resist"][t].append(again.resist[t])
            draws["susceptibility"][t].append(again.susceptibility(t))
        for a in attackers:
            for t in targets:
                pair_draws[(a, t)].append(again.p(a, t))

    def band(estimate: float, values: list[float]) -> Band:
        if not values:
            return Band(estimate, estimate, estimate)
        ordered = sorted(values)
        return Band(estimate, _quantile(ordered, 0.025), _quantile(ordered, 0.975))

    return Bootstrapped(
        fit=full,
        steer={a: band(full.steer[a], draws["steer"][a]) for a in attackers},
        resist={t: band(full.resist[t], draws["resist"][t]) for t in targets},
        susceptibility={
            t: band(full.susceptibility(t), draws["susceptibility"][t]) for t in targets
        },
        pairs={
            (a, t): band(full.p(a, t), pair_draws[(a, t)])
            for a in attackers
            for t in targets
        },
        observed=observed,
        resamples=resamples if games else 0,
    )


def _quantile(ordered: Sequence[float], q: float) -> float:
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation of two equal-length sequences, ties at their mean
    rank."""
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("Two sequences of the same length, at least two long.")
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / (vx * vy) ** 0.5 if vx and vy else 0.0


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


__all__ = [
    "Band",
    "Bootstrapped",
    "Cell",
    "Fit",
    "bootstrap",
    "fit",
    "sigmoid",
    "spearman",
    "tally",
]
