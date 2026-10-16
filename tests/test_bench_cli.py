"""castle-bench: a dry run with bots, saved records, and a planned run."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from badactors.bench.cli import main
from badactors.castle.bots import HeuristicBot
from badactors.castle.decisions import Decision, ExitAnswer
from badactors.castle.interview import ExitObservation
from badactors.castle.observe import GameObservation
from badactors.castle.play import record, run_game, save_record, start_game
from badactors.castle.rules import GameConfig
from badactors.castle.schedule import Scenario, mixed_schedule
from badactors.castle.verify import load_bundle, verify
from castle_scenes import CAST, CASTLE

SCENARIO = Scenario(castle=CASTLE, cast=CAST.first(8), config=GameConfig.shipped())
MODELS = ["lab-a/alpha-1", "lab-b/beta-2", "lab-c/gamma-3", "lab-d/delta-4"]


@dataclass(frozen=True)
class Posing:
    model: str
    seed: int

    @property
    def agent(self) -> str:
        return f"model:{self.model}"

    def __call__(self, observation: GameObservation) -> Decision:
        return HeuristicBot(seed=self.seed)(observation)

    def interview(self, departing: ExitObservation) -> ExitAnswer:
        return HeuristicBot(seed=self.seed).interview(departing)


def test_a_dry_run_writes_both_boards_apart_and_a_bundle_per_game(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    main(
        [
            "--bots",
            "--seeds",
            "2",
            "--profiles",
            "4",
            "--resamples",
            "5",
            "--no-leak-check",
            "--out",
            str(out),
        ]
    )
    printed = capsys.readouterr().out
    assert "REFERENCE BOTS (never mixed with model results)" in printed
    board = json.loads((out / "leaderboard.json").read_text())
    assert board["models"]["games"] == 0 and board["models"]["rows"] == []
    assert {item["reason"] for item in board["models"]["refused"]} == {
        "played by reference bots"
    }
    bots = board["reference_bots"]
    assert bots["reference_bots"] and bots["games"] == 16
    assert all(row["label"].startswith("Reference bot") for row in bots["rows"])
    assert {row["entrant"] for row in bots["rows"]} == {
        f"bot:heuristic/p{i}" for i in range(4)
    }
    found = json.loads((out / "matrix.json").read_text())
    assert found["models"]["entrants"] == []
    assert found["reference_bots"]["label"].startswith("Reference bots")
    replays = sorted((out / "replays" / "bots").iterdir())
    assert len(replays) == 16 and not (out / "replays" / "models").exists()
    assert verify(load_bundle(replays[0])) == []
    profiles = json.loads((out / "profiles.json").read_text())
    assert set(profiles["susceptibility"]) == {f"bot:heuristic/p{i}" for i in range(4)}


def test_saved_records_are_replayed_rated_and_bundled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schedule = mixed_schedule(SCENARIO, MODELS, seeds=[0], rotation="compact")
    games = tmp_path / "games"
    for seating in schedule.games:
        cast = seating.cast(SCENARIO.cast)
        game = start_game(
            CASTLE,
            cast,
            seed=seating.seed,
            config=SCENARIO.config,
            world_id=seating.world_id,
        )
        played = run_game(
            game,
            {
                seat: Posing(model, seating.seed)
                for seat, model in seating.seats.items()
            },
            check_leaks=False,
        )
        path = games / f"{seating.id}.json"
        save_record(record(CASTLE, cast, played, rated=True, arm=seating.arm), path)
        calls = [
            {"event": "call", "ref": {"seat": seat}, "dollars": 0.02}
            for seat in seating.seats
        ]
        lines = [*calls, {"event": "spent", "dollars": 0.16}]
        path.with_suffix(".harness.jsonl").write_text(
            "\n".join(json.dumps(line) for line in lines)
        )
    cast = SCENARIO.cast
    bot_game = start_game(CASTLE, cast, seed=9, config=SCENARIO.config)
    bot = HeuristicBot(seed=9)
    played = run_game(bot_game, dict.fromkeys(bot_game.world.seats, bot))
    save_record(record(CASTLE, cast, played), games / "bot-game.json")
    seating = schedule.games[0]
    single = seating.model_copy(update={"id": "single-x-s0-r0", "arm": "single"})
    alone = start_game(
        CASTLE,
        seating.cast(cast),
        seed=seating.seed,
        config=SCENARIO.config,
        world_id=single.world_id,
    )
    played_alone = run_game(
        alone,
        {seat: Posing(model, seating.seed) for seat, model in seating.seats.items()},
        check_leaks=False,
    )
    save_record(
        record(CASTLE, seating.cast(cast), played_alone, arm="single"),
        games / "single.json",
    )
    forged = record(CASTLE, cast, played)
    ledger = list(forged.ledger)
    ledger[5] = ledger[5].model_copy(update={"tick": ledger[5].tick + 3})
    save_record(forged.model_copy(update={"ledger": ledger}), games / "forged.json")

    out = tmp_path / "out"
    main(
        [
            "--records",
            str(games),
            "--resamples",
            "5",
            "--out",
            str(out),
        ]
    )
    board = json.loads((out / "leaderboard.json").read_text())
    models = board["models"]
    assert models["games"] == len(schedule.games)
    assert {row["entrant"] for row in models["rows"]} == set(MODELS)
    refused = {item["game"]: item["reason"] for item in models["refused"]}
    assert refused["bot-game"] == "played by reference bots"
    assert refused[single.world_id].startswith("single-model arm")
    assert refused["forged"] == "its ledger is not the one its replay writes"
    assert models["cost_per_game"]["median"] == pytest.approx(0.16)
    assert all(row["cost"]["per_game"] for row in models["rows"])
    assert board["reference_bots"]["games"] == 1
    assert {p.name for p in (out / "replays" / "models").iterdir()} == {
        f"{game.world_id}.json.gz" for game in [*schedule.games, single]
    }
    assert [p.name for p in (out / "replays" / "bots").iterdir()] == [
        "bot-game.json.gz"
    ]
    assert "MODELS: 4 games rated" in capsys.readouterr().out


def test_a_planned_run_prints_the_power_estimate_before_its_schedule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    cast = json.loads(Path("configs/castle/cast.json").read_text())
    open_weight = [cast["players"][6]["model"]]
    main(
        [
            "--plan",
            "--seeds",
            "2",
            "--pilot-seeds",
            "1",
            "--open-weight",
            *open_weight,
            "--out",
            str(out),
        ]
    )
    printed = capsys.readouterr().out
    assert printed.index("POWER") < printed.index("Schedule: mixed")
    written = json.loads((out / "schedule.json").read_text())
    assert written["tiers"][open_weight[0]] == "open"
    with pytest.raises(SystemExit, match="Not in the cast"):
        main(["--plan", "--pilot-seeds", "1", "--open-weight", "nobody/none"])
