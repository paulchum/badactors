"""The feeds the spectator bundles are the ones the engine writes today."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from badactors.castle.show import load_feed

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "frontend" / "public" / "games"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "export_feeds", ROOT / "scripts" / "export_feeds.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_index_lists_every_curated_seed_as_a_bot_game() -> None:
    script = _script()
    index = json.loads((BUNDLED / "index.json").read_text())
    assert [game["seed"] for game in index["games"]] == list(script.SEEDS)
    for game in index["games"]:
        assert game["reference_bots"] is True
        assert game["played_by"] == ["bot"]
        feed = load_feed(BUNDLED / game["file"])
        assert feed.game.seed == game["seed"]
        assert feed.game.reference_bots


def test_a_bundled_feed_is_what_the_engine_writes_now(tmp_path: Path) -> None:
    """If this fails, the engine changed what a game or its feed holds: run
    ``uv run python scripts/export_feeds.py`` and commit the new feeds."""
    script = _script()
    games = script.export([56], tmp_path)
    assert games[0]["twist"] is True
    fresh = (tmp_path / "seed-56.json").read_text()
    assert fresh == (BUNDLED / "seed-56.json").read_text()
    assert [path.name for path in sorted(tmp_path.iterdir())] == [
        "index.json",
        "seed-56.json",
    ]
