"""Budgets: reserve the worst case, settle the actual, stop before overspending."""

from __future__ import annotations

import pytest

from badactors.castle.budget import Budget, BudgetExhausted, Limits, Spend, preflight
from badactors.castle.provider import ModelInfo, Usage
from badactors.castle.rules import GameConfig
from castle_scenes import CAST, CASTLE, MODELS, referee

INFO = ModelInfo("m", frozenset(), prompt_price=1e-6, completion_price=1e-5)


def test_limits_must_all_be_positive() -> None:
    with pytest.raises(ValueError):
        Limits(0, 10, 1.0)
    with pytest.raises(ValueError):
        Limits(10, 10, 0.0)


def test_a_call_reserves_its_worst_case_and_settles_what_it_cost() -> None:
    budget = Budget(Limits(10, 100_000, 1.0))
    held = budget.reserve(INFO, prompt=1_000, max_tokens=500)
    assert held.worst == Spend(1, 1_500, pytest.approx(0.006))
    assert budget.held.dollars == pytest.approx(0.006)
    cost = budget.settle(held, INFO, Usage(900, 100, cost=0.002))
    assert cost == Spend(1, 1_000, 0.002)
    assert budget.held == Spend(0, 0, 0.0)
    assert budget.spent == Spend(1, 1_000, 0.002)


def test_a_reply_without_a_cost_is_priced_from_its_tokens() -> None:
    budget = Budget(Limits(10, 100_000, 1.0))
    held = budget.reserve(INFO, prompt=1_000, max_tokens=500)
    assert budget.settle(held, INFO, Usage(1_000, 100)).dollars == pytest.approx(0.002)


def test_an_unknown_outcome_is_charged_the_worst_case() -> None:
    budget = Budget(Limits(10, 100_000, 1.0))
    held = budget.reserve(INFO, prompt=1_000, max_tokens=500)
    assert budget.settle(held, INFO, None) == held.worst


def test_a_call_that_would_not_fit_is_never_made() -> None:
    budget = Budget(Limits(2, 100_000, 1.0))
    budget.reserve(INFO, prompt=10, max_tokens=10)
    budget.reserve(INFO, prompt=10, max_tokens=10)
    with pytest.raises(BudgetExhausted, match="request limit"):
        budget.reserve(INFO, prompt=10, max_tokens=10)
    dollars = Budget(Limits(100, 100_000, 0.01))
    dollars.reserve(INFO, prompt=1_000, max_tokens=500)
    with pytest.raises(BudgetExhausted, match="could cost"):
        dollars.reserve(INFO, prompt=1_000, max_tokens=500)
    tokens = Budget(Limits(100, 1_000, 1.0))
    with pytest.raises(BudgetExhausted, match="tokens"):
        tokens.reserve(INFO, prompt=900, max_tokens=500)


def test_a_released_call_costs_only_the_request() -> None:
    budget = Budget(Limits(10, 100_000, 1.0))
    budget.release(budget.reserve(INFO, prompt=1_000, max_tokens=500))
    assert budget.spent == Spend(1, 0, 0.0) and budget.held == Spend(0, 0, 0.0)


def test_preflight_rehearses_the_game_and_prices_every_question() -> None:
    config = GameConfig.shipped(max_days=2)
    ref = referee(seed=4, max_days=2)
    ref.run()
    priced = {
        model: ModelInfo(model, frozenset({"tools"}), 2e-6, 1e-5) for model in MODELS
    }
    estimate = preflight(CASTLE, CAST, priced, seed=4, config=config, max_tokens=4_096)
    assert estimate.requests == len(ref.log)
    assert sum(estimate.by_kind.values()) == estimate.requests
    assert estimate.prompt_tokens > estimate.requests * 2_000
    assert 0 < estimate.dollars < estimate.worst_dollars
    assert estimate.fits(Limits(10**6, 10**9, 1_000.0))
    assert not estimate.fits(Limits(estimate.requests - 1, 10**9, 1_000.0))
    free_models = {model: ModelInfo(model, frozenset(), 0.0, 0.0) for model in MODELS}
    free = preflight(CASTLE, CAST, free_models, seed=4, config=config, max_tokens=4_096)
    assert free.dollars == 0 and free.requests == estimate.requests
