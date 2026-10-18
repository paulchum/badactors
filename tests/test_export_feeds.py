"""The feeds the spectator bundles: built from the engine, the same every time.

The feeds are not committed; ``npm run build`` exports them. A recorded digest
of seed 56's feed still makes any engine change that alters a feed fail here,
loudly, until the digest is updated on purpose.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from badactors.castle.show import load_feed, vocabulary

ROOT = Path(__file__).resolve().parent.parent
DIGESTS = Path(__file__).resolve().parent / "fixtures" / "feeds" / "digests.json"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "export_feeds", ROOT / "scripts" / "export_feeds.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exported(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, list[dict[str, Any]]]:
    """Seed 56 exported once, into a folder that held a stale feed and cards."""
    out = tmp_path_factory.mktemp("games")
    (out / "seed-41.json").write_text("{}")
    (out / "cards" / "seed-41").mkdir(parents=True)
    games = _script().export([56], out)
    return out, games


def test_an_export_is_the_same_every_time(
    exported: tuple[Path, list[dict[str, Any]]], tmp_path: Path
) -> None:
    out, _ = exported
    _script().export([56], tmp_path)
    for name in ("seed-56.json", "index.json", "show.json"):
        assert (tmp_path / name).read_bytes() == (out / name).read_bytes()


def test_seed_56s_feed_is_the_one_recorded(
    exported: tuple[Path, list[dict[str, Any]]],
) -> None:
    out, games = exported
    digest = hashlib.sha256((out / "seed-56.json").read_bytes()).hexdigest()
    recorded = json.loads(DIGESTS.read_text())["seed-56"]
    assert digest == recorded, (
        "The engine now writes a different feed for seed 56. If that is meant, "
        f"record the new digest in {DIGESTS.relative_to(ROOT)}: {digest}"
    )
    assert games[0]["twist"] is True


def test_the_index_lists_each_exported_seed_as_a_bot_game(
    exported: tuple[Path, list[dict[str, Any]]],
) -> None:
    out, _ = exported
    assert _script().SEEDS[0] == 56
    index = json.loads((out / "index.json").read_text())
    assert [game["seed"] for game in index["games"]] == [56]
    assert sorted(path.name for path in out.iterdir()) == [
        "cards",
        "index.json",
        "seed-56.json",
        "show.json",
    ]
    assert json.loads((out / "show.json").read_text()) == vocabulary()
    feed = load_feed(out / "seed-56.json")
    assert sorted(p.name for p in (out / "cards" / "seed-56").iterdir()) == sorted(
        f"{claim.id}.svg" for claim in feed.claims
    )
    assert index["games"][0]["claims"] == len(feed.claims)
    for game in index["games"]:
        assert game["reference_bots"] is True
        assert game["played_by"] == ["bot"]
        feed = load_feed(out / game["file"])
        assert feed.game.seed == game["seed"]
        assert feed.game.reference_bots
