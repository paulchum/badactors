"""Uncertainty-aware OpenSkill-style ratings (Weng-Lin Bradley-Terry full pairing).

A dependency-free, deterministic implementation of the Bradley-Terry full-pairing
update from Weng & Lin (2011) — the same model family OpenSkill exposes. Each player
carries a Gaussian skill ``(mu, sigma)``; a single match ranks players against one
another, shifts every rating toward its observed placement, and shrinks uncertainty.
Pure and deterministic: identical inputs yield identical outputs, so a benchmark run
is fully reproducible.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import exp, sqrt

MU: float = 25.0
SIGMA: float = MU / 3.0
BETA: float = SIGMA / 2.0
TAU: float = SIGMA / 100.0
KAPPA: float = 0.0001

_BETA_SQ = BETA * BETA


@dataclass(frozen=True)
class Rating:
    mu: float = MU
    sigma: float = SIGMA


def default_rating() -> Rating:
    return Rating(MU, SIGMA)


def ordinal(rating: Rating, *, z: float = 3.0) -> float:
    """Conservative skill estimate: ``mu - z * sigma``."""
    return rating.mu - z * rating.sigma


def rate(ratings: Sequence[Rating], ranks: Sequence[int]) -> list[Rating]:
    """Update one match of individual players.

    ``ranks`` uses ``1`` for the best placement; ties share a rank. Returns the new
    ratings in the same order as the input.
    """
    if len(ratings) != len(ranks):
        raise ValueError("ratings and ranks must have equal length")
    n = len(ratings)
    sigma_sq = [r.sigma * r.sigma + TAU * TAU for r in ratings]
    mu = [r.mu for r in ratings]
    updated: list[Rating] = []
    for i in range(n):
        omega = 0.0
        delta = 0.0
        for j in range(n):
            if i == j:
                continue
            c = sqrt(sigma_sq[i] + sigma_sq[j] + 2.0 * _BETA_SQ)
            p_ij = 1.0 / (1.0 + exp((mu[j] - mu[i]) / c))
            if ranks[i] < ranks[j]:
                s = 1.0
            elif ranks[i] == ranks[j]:
                s = 0.5
            else:
                s = 0.0
            omega += (sigma_sq[i] / c) * (s - p_ij)
            gamma = sqrt(sigma_sq[i]) / c
            delta += (gamma * sigma_sq[i] / (c * c)) * p_ij * (1.0 - p_ij)
        new_mu = mu[i] + omega
        new_sigma_sq = sigma_sq[i] * max(1.0 - delta, KAPPA)
        updated.append(Rating(new_mu, sqrt(new_sigma_sq)))
    return updated


def ranks_from_scores(scores: Sequence[float]) -> list[int]:
    """Convert "higher score is better" into ranks (1 = best; ties share a rank)."""
    return [1 + sum(1 for other in scores if other > score) for score in scores]
