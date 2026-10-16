"""``castle-live``: no budget, no start; a mock game end to end; no key, no play.

Nothing here touches the network: real-model runs read model metadata from a
cache file written by the test, and stop before any call.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from badactors.castle.live import main
from badactors.castle.play import is_model, load_record, replay
from castle_scenes import CAST, MODELS

BUDGET = ["--max-requests", "2000", "--max-tokens", "20000000", "--max-dollars", "50"]


def test_it_will_not_start_without_every_limit() -> None:
    for given in ([], BUDGET[:2], BUDGET[:4], BUDGET[2:]):
        with pytest.raises(SystemExit) as stopped:
            main(["--mock", *given])
        assert "will not start without" in str(stopped.value.code)


def test_a_mock_game_runs_the_harness_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save = tmp_path / "game.json"
    main(["--mock", "--seed", "7", "--max-days", "2", *BUDGET, "--save", str(save)])
    out = capsys.readouterr().out
    assert out.startswith("Preflight")
    assert "Outcome: finished" in out
    stored = load_record(save)
    assert stored.outcome == "finished" and stored.rated
    assert set(stored.agents.values()) == {f"model:{m}" for m in CAST.models().values()}
    assert all(is_model(agent) for agent in stored.agents.values())
    again = replay(stored)
    assert len(again.world.ledger) == len(stored.ledger)
    harness = save.with_suffix(".harness.jsonl").read_text().splitlines()
    events = {json.loads(line)["event"] for line in harness}
    assert {"mode", "call", "prefetch", "spent"} <= events


def test_a_dry_run_estimates_and_spends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save = tmp_path / "game.json"
    main(["--mock", "--dry-run", *BUDGET, "--save", str(save)])
    assert "requests" in capsys.readouterr().out
    assert not save.exists()


def _cache(tmp_path: Path, models: list[str], price: str = "0.000002") -> Path:
    cache = tmp_path / "models.json"
    listing = {
        "data": [
            {
                "id": model,
                "supported_parameters": ["tools", "structured_outputs"],
                "pricing": {"prompt": price, "completion": "0.00001"},
            }
            for model in models
        ]
    }
    cache.write_text(json.dumps({"fetched_at": time.time(), "listing": listing}))
    return cache


def test_a_real_run_needs_the_key_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cache = _cache(tmp_path, MODELS)
    with pytest.raises(SystemExit) as stopped:
        main(["--max-days", "2", *BUDGET, "--models-cache", str(cache)])
    assert "OPENROUTER_API_KEY" in str(stopped.value.code)


def test_an_estimate_over_budget_stops_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-a-key")
    cache = _cache(tmp_path, MODELS, price="0.001")
    with pytest.raises(SystemExit) as stopped:
        main(["--max-days", "2", *BUDGET, "--models-cache", str(cache)])
    assert "does not fit the budget" in str(stopped.value.code)


def test_a_cast_model_openrouter_does_not_list_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-a-key")
    cache = _cache(tmp_path, MODELS[1:])
    with pytest.raises(SystemExit) as stopped:
        main([*BUDGET, "--models-cache", str(cache)])
    assert MODELS[0] in str(stopped.value.code)
