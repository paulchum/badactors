"""``castle-live``: no budget, no start; a mock game end to end, its receipt, and
its provenance; no key, no play.

Nothing here touches the network: real-model runs read model metadata from a
cache file written by the test, and stop before any call.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from badactors.bench.leaderboard import Finished, board
from badactors.castle import configs, verify
from badactors.castle.budget import load_outputs
from badactors.castle.live import main
from badactors.castle.play import is_mock, is_model, load_record, replay
from badactors.castle.rules import GameConfig
from badactors.castle.schedule import (
    Scenario,
    load_schedule,
    mixed_schedule,
    save_schedule,
)
from castle_scenes import CAST, CASTLE, MODELS

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
    assert stored.outcome == "finished"
    again = replay(stored)
    assert len(again.world.ledger) == len(stored.ledger)
    harness = save.with_suffix(".harness.jsonl").read_text().splitlines()
    events = {json.loads(line)["event"] for line in harness}
    assert {"mode", "call", "exchange", "prefetch", "spent"} <= events


def test_a_mock_game_is_marked_mock_and_no_leaderboard_takes_it(
    tmp_path: Path,
) -> None:
    save = tmp_path / "game.json"
    main(["--mock", "--seed", "3", "--max-days", "2", *BUDGET, "--save", str(save)])
    stored = load_record(save)
    assert stored.outcome == "finished" and not stored.rated
    assert stored.agents == {seat: f"mock:{m}" for seat, m in CAST.models().items()}
    assert all(is_mock(d.agent) and not is_model(d.agent) for d in stored.decisions)
    assert stored.reference_bots
    finished = Finished("mock", stored, replay(stored))
    for reference in (False, True):
        (refused,) = board([finished], reference=reference).refused
        assert refused.reason.startswith("played through the mock provider")


def test_a_mock_game_writes_a_bundle_castle_verify_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save = tmp_path / "game.json"
    main(["--mock", "--seed", "7", "--max-days", "2", *BUDGET, "--save", str(save)])
    path = tmp_path / "game.bundle.json.gz"
    bundle = verify.load_bundle(path)
    assert f"head {bundle.head}" in capsys.readouterr().out
    assert bundle.record == load_record(save)
    assert verify.verify(bundle) == []
    calls = bundle.exchanges
    harness = save.with_suffix(".harness.jsonl").read_text().splitlines()
    asked = [json.loads(line) for line in harness]
    assert len(calls) == sum(1 for event in asked if event["event"] == "call")
    assert calls and {call.provider for call in calls} == {"mock:bot"}
    cast = CAST.models()
    assert all(call.model == cast[call.seat] for call in calls)
    assert all(call.response is not None and call.failure is None for call in calls)
    assert verify.make_bundle(bundle.record, asked).head == bundle.head

    verify.main([str(path), "--head", bundle.head])
    assert "verified" in capsys.readouterr().out
    assert "mock provider" in verify.summary(bundle)


def test_a_dry_run_estimates_and_spends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save = tmp_path / "game.json"
    main(["--mock", "--dry-run", *BUDGET, "--save", str(save)])
    assert "requests" in capsys.readouterr().out
    assert not save.exists()


def _cache(
    tmp_path: Path,
    models: list[str],
    price: str = "0.000002",
    supported: tuple[str, ...] = ("tools", "structured_outputs"),
) -> Path:
    cache = tmp_path / "models.json"
    listing = {
        "data": [
            {
                "id": model,
                "supported_parameters": list(supported),
                "pricing": {"prompt": price, "completion": "0.00001"},
                "top_provider": {"max_completion_tokens": 65_536},
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


@pytest.mark.parametrize(
    ("reasoning", "thinking"), [("default", True), ("high", True), ("least", False)]
)
def test_a_dry_run_prints_each_models_output_limit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    reasoning: str,
    thinking: bool,
) -> None:
    cache = _cache(tmp_path, MODELS, supported=("tools", "reasoning"))
    main(
        [
            "--dry-run",
            *BUDGET,
            "--models-cache",
            str(cache),
            "--reasoning",
            reasoning,
        ]
    )
    out = capsys.readouterr().out
    table = load_outputs()
    for model in MODELS:
        entry = table.models.get(model)
        room = (entry.thinking if entry else None) or table.thinking
        tokens = room if thinking else 4_096
        assert f"Output limit: {model} {tokens:,} tokens" in out
    grok = next(line for line in out.splitlines() if "x-ai/grok-4.7" in line)
    assert grok.endswith("reserving 32,768.")


def test_a_cast_model_openrouter_does_not_list_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-a-key")
    cache = _cache(tmp_path, MODELS[1:])
    with pytest.raises(SystemExit) as stopped:
        main([*BUDGET, "--models-cache", str(cache)])
    assert MODELS[0] in str(stopped.value.code)


@pytest.fixture(scope="module")
def schedule_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    scenario = Scenario(castle=CASTLE, cast=CAST.first(8), config=GameConfig.shipped())
    entrants = MODELS[:4]
    planned = mixed_schedule(
        scenario, entrants, seeds=[4], rotation="compact", names_visible=True
    )
    path = tmp_path_factory.mktemp("plan") / "schedule.json"
    save_schedule(planned, path)
    return path


def test_a_scheduled_game_takes_its_seed_seats_and_arm_from_the_schedule(
    tmp_path: Path, schedule_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seating = load_schedule(schedule_file).arm("mixed")[1]
    save = tmp_path / "game.json"
    main(
        [
            "--mock",
            "--schedule",
            str(schedule_file),
            "--game",
            seating.id,
            "--max-days",
            "2",
            *BUDGET,
            "--save",
            str(save),
        ]
    )
    assert f"Arm: mixed, {seating.world_id}." in capsys.readouterr().out
    stored = load_record(save)
    assert (stored.arm, stored.world_id, stored.seed) == (
        "mixed",
        seating.world_id,
        seating.seed,
    )
    assert stored.agents == {seat: f"mock:{m}" for seat, m in seating.seats.items()}
    assert sorted(replay(stored).starting_traitors) == sorted(seating.traitors)
    assert verify.verify(verify.load_bundle(tmp_path / "game.bundle.json.gz")) == []


def test_a_scheduled_game_refuses_what_the_schedule_cannot_back(
    tmp_path: Path, schedule_file: Path
) -> None:
    planned = load_schedule(schedule_file)
    mixed, shown = planned.arm("mixed")[0], planned.arm("names_visible")[0]
    given = ["--mock", "--schedule", str(schedule_file), *BUDGET]
    for extra, why in (
        (["--game", shown.id], "names-visible"),
        (["--game", "no-such-game"], "has no game"),
        (["--game", mixed.id, "--seed", str(mixed.seed + 1)], "is played on seed"),
        (["--game", mixed.id, "--players", "9"], "takes its seats"),
        (["--game", mixed.id, "--cast", str(_cast(tmp_path))], "another cast"),
    ):
        with pytest.raises(SystemExit) as stopped:
            main([*given, *extra, "--save", str(tmp_path / "x.json")])
        assert why in str(stopped.value.code), extra
    with pytest.raises(SystemExit) as stopped:
        main(["--mock", "--game", mixed.id, *BUDGET])
    assert "go together" in str(stopped.value.code)
    assert not (tmp_path / "x.json").exists()


def _cast(tmp_path: Path) -> Path:
    """The shipped cast with two characters swapped."""
    shipped = json.loads(configs.CAST.read_text())
    players = shipped["players"]
    players[0], players[1] = players[1], players[0]
    path = tmp_path / "swapped.json"
    path.write_text(json.dumps(shipped))
    return path
