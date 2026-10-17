"""The bot cast's standings the site bundles: castle-bench's own JSON, a verify
bundle per game, and the seat rows the standings pages read, the same every
time and never a model's."""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "export_bench", ROOT / "scripts" / "export_bench.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    """Two seeds exported once, into a folder that held a stale file."""
    out = tmp_path_factory.mktemp("bench")
    (out / "stale.json").write_text("{}")
    return out, _script().export(2, out, min_games=3)


def test_an_export_is_the_same_every_time(
    exported: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    out, _ = exported
    _script().export(2, tmp_path, min_games=3)
    written = sorted(p.relative_to(out) for p in out.rglob("*") if p.is_file())
    assert written == sorted(
        p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file()
    )
    for name in written:
        assert (tmp_path / name).read_bytes() == (out / name).read_bytes()


def test_it_writes_castle_benchs_json_and_a_bundle_per_game(
    exported: tuple[Path, dict[str, Any]],
) -> None:
    out, site = exported
    assert not (out / "stale.json").exists()
    for name in ("leaderboard.json", "matrix.json", "profiles.json", "schedule.json"):
        assert (out / name).is_file()
    assert site["min_games"] == 3
    assert site["run"]["command"] == "castle-bench --bots --seeds 2"
    assert site["run"]["rotation"] == "compact"
    assert len(site["run"]["suite_digest"]) == 64
    for game in site["games"]:
        assert (out / game["bundle"]).is_file()
        assert game["ticks"][0] <= game["ticks"][1]


def test_every_seat_is_a_bot_and_matches_the_leaderboard(
    exported: tuple[Path, dict[str, Any]],
) -> None:
    out, site = exported
    board = json.loads((out / "leaderboard.json").read_text())
    assert board["models"]["rows"] == []
    assert site["reference_bots"] is True
    assert all(row["entrant"].startswith("bot:") for row in site["seats"])
    games = Counter(row["entrant"] for row in site["seats"])
    traitors = Counter(row["entrant"] for row in site["seats"] if row["traitor"])
    for row in board["reference_bots"]["rows"]:
        assert games[row["entrant"]] == row["games"]
        assert traitors[row["entrant"]] == row["traitor_games"]
        assert (
            sum(r["attempts"] for r in site["seats"] if r["entrant"] == row["entrant"])
            == row["manipulation"]["attempts"]
        )


def test_seat_counts_hold_together(exported: tuple[Path, dict[str, Any]]) -> None:
    _, site = exported
    ids = {game["id"] for game in site["games"]}
    for row in site["seats"]:
        assert row["game"] in ids
        assert row["lies_exposed"] <= row["lies"] <= row["checkable"] <= row["claims"]
        assert row["unforced_lies"] <= row["lies"]
        assert row["evidence_votes"] <= row["votes"]
        assert row["offers_refused"] <= row["offers"]
        assert row["steered"] <= row["attempts"]
        assert row["fell_for"] <= row["targeted"]
        for kind in ("lies", "unforced_lies", "steered", "fell_for", "planted"):
            count = row[kind]
            assert (kind in row["ticks"]) == (count > 0), (kind, row)
        if row["traitor"] and not row["recruited"]:
            assert row["unforced_lies"] == 0
