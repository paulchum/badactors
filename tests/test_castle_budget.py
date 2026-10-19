"""Budgets: reserve the worst case, settle the actual, stop before overspending."""

from __future__ import annotations

import json
from typing import Any

import pytest

from badactors.castle.budget import (
    Budget,
    BudgetExhausted,
    Limits,
    ModelOutputs,
    OutputLimit,
    OutputTable,
    Spend,
    load_outputs,
    output_limits,
    preflight,
)
from badactors.castle.cast import load_cast_file
from badactors.castle.configs import CAST as SHIPPED_CAST
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
    flat = {model: OutputLimit(4_096, 4_096) for model in MODELS}
    estimate = preflight(CASTLE, CAST, priced, seed=4, config=config, outputs=flat)
    assert estimate.requests == len(ref.log)
    assert sum(estimate.by_kind.values()) == estimate.requests
    assert estimate.prompt_tokens > estimate.requests * 2_000
    assert 0 < estimate.dollars < estimate.worst_dollars
    assert estimate.fits(Limits(10**6, 10**9, 1_000.0))
    assert not estimate.fits(Limits(estimate.requests - 1, 10**9, 1_000.0))
    free_models = {model: ModelInfo(model, frozenset(), 0.0, 0.0) for model in MODELS}
    free = preflight(CASTLE, CAST, free_models, seed=4, config=config, outputs=flat)
    assert free.dollars == 0 and free.requests == estimate.requests


def test_preflight_prices_each_models_worst_case_at_its_own_reservation() -> None:
    config = GameConfig.shipped(max_days=2)
    priced = {
        model: ModelInfo(model, frozenset({"tools"}), 2e-6, 1e-5) for model in MODELS
    }
    small = {model: OutputLimit(1_000, 1_000) for model in MODELS}
    roomy = {model: OutputLimit(1_000, 9_000) for model in MODELS}
    low = preflight(CASTLE, CAST, priced, seed=4, config=config, outputs=small)
    high = preflight(CASTLE, CAST, priced, seed=4, config=config, outputs=roomy)
    assert high.dollars == low.dollars
    assert high.worst_dollars - low.worst_dollars == pytest.approx(
        high.requests * 8_000 * 1e-5
    )


def _info(model: str, *, reasons: bool = True, most: int | None = None) -> ModelInfo:
    supported = frozenset({"tools", "reasoning"} if reasons else {"tools"})
    return ModelInfo(model, supported, 1e-6, 1e-5, max_completion_tokens=most)


TABLE = OutputTable(
    thinking=16_384,
    models={
        "q/thinks-long": ModelOutputs(thinking=32_768),
        "x/runs-over": ModelOutputs(reserve=32_768),
    },
)
MODELS_SEEN = {
    "a/plain": _info("a/plain", reasons=False),
    "g/thinks": _info("g/thinks"),
    "q/thinks-long": _info("q/thinks-long"),
    "x/runs-over": _info("x/runs-over"),
    "s/short-endpoint": _info("s/short-endpoint", most=8_000),
}


@pytest.mark.parametrize("reasoning", ["default", "low", "medium", "high"])
def test_where_models_think_a_reasoning_model_gets_room(reasoning: str) -> None:
    limits = output_limits(MODELS_SEEN, base=4_096, reasoning=reasoning, table=TABLE)
    assert limits == {
        "a/plain": OutputLimit(4_096, 4_096),
        "g/thinks": OutputLimit(16_384, 16_384),
        "q/thinks-long": OutputLimit(32_768, 32_768),
        "x/runs-over": OutputLimit(16_384, 32_768),
        "s/short-endpoint": OutputLimit(8_000, 8_000),
    }


@pytest.mark.parametrize("reasoning", ["least", "off", "minimal"])
def test_without_thinking_every_model_gets_the_base_and_reserves_its_worst(
    reasoning: str,
) -> None:
    limits = output_limits(MODELS_SEEN, base=4_096, reasoning=reasoning, table=TABLE)
    assert {
        model: limit.max_tokens for model, limit in limits.items()
    } == dict.fromkeys(MODELS_SEEN, 4_096)
    assert limits["x/runs-over"].reserve == 32_768
    assert all(limit.reserve >= limit.max_tokens for limit in limits.values())


def test_a_base_above_the_table_is_kept() -> None:
    limits = output_limits(MODELS_SEEN, base=20_000, reasoning="default", table=TABLE)
    assert limits["g/thinks"].max_tokens == 20_000
    assert limits["s/short-endpoint"].max_tokens == 8_000


def test_the_shipped_table_names_cast_models_and_says_why() -> None:
    table = load_outputs()
    cast = set(load_cast_file(SHIPPED_CAST).models().values())
    assert table.models and set(table.models) <= cast
    assert all(entry.why for entry in table.models.values())
    assert table.thinking > 4_096


def test_a_table_with_unknown_keys_is_refused(tmp_path: Any) -> None:
    bad = tmp_path / "outputs.json"
    bad.write_text(json.dumps({"thinking": 8_000, "model": {}}))
    with pytest.raises(ValueError):
        load_outputs(bad)
