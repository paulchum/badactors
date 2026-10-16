"""Tests for the OpenSkill-style rating engine."""

from __future__ import annotations

from badactors.bench.ratings import default_rating, ordinal, ranks_from_scores, rate


def test_ranks_from_scores_handles_ties() -> None:
    assert ranks_from_scores([5, 3, 3, 1]) == [1, 2, 2, 4]


def test_rate_moves_winner_up_and_shrinks_sigma() -> None:
    base = default_rating()
    winner, loser = rate([base, base], [1, 2])
    assert winner.mu > base.mu > loser.mu
    assert winner.sigma < base.sigma
    assert loser.sigma < base.sigma
    assert ordinal(winner) > ordinal(loser)


def test_rate_is_deterministic_and_ties_stay_level() -> None:
    base = default_rating()
    first = rate([base, base, base], [1, 2, 2])
    assert first == rate([base, base, base], [1, 2, 2])
    assert first[1] == first[2]
