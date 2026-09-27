"""BLUFF: an open deception/detection benchmark over Bad Actors games.

Ranks frontier models on two independent axes — how good they are at lying (Deception)
and at catching liars (Detection) — using log-derived, reproducible metrics and
uncertainty-aware OpenSkill-style ratings.
"""

from __future__ import annotations

from badactors.bench.aggregate import (
    StandingRow,
    leaderboard_payload,
    standings,
)
from badactors.bench.order import (
    deception_score,
    detection_score,
    rounds_survived_undetected,
    vote_accuracy,
    winner_of,
)
from badactors.bench.rate import (
    AgentRating,
    FinishedGame,
    RatingTable,
    ranks_from_scores,
    run_bench,
    update_ratings,
)
from badactors.bench.ratings import Rating, default_rating, ordinal, rate

__all__ = [
    "AgentRating",
    "FinishedGame",
    "Rating",
    "RatingTable",
    "StandingRow",
    "deception_score",
    "default_rating",
    "detection_score",
    "leaderboard_payload",
    "ordinal",
    "ranks_from_scores",
    "rate",
    "rounds_survived_undetected",
    "run_bench",
    "standings",
    "update_ratings",
    "vote_accuracy",
    "winner_of",
]
