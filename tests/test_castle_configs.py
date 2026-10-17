"""The castle ships inside the package, and a command can be pointed elsewhere."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import badactors.castle
from badactors.castle import configs
from badactors.castle.balance import default_balance
from badactors.castle.cli import main as castle_game
from badactors.castle.play import load_record

PACKAGE = Path(badactors.castle.__file__).parent


def test_the_shipped_files_live_in_the_package() -> None:
    for path in (configs.MAP, configs.CAST, configs.BALANCE, configs.SUITE):
        assert path.is_file()
        assert path.parent == PACKAGE / "configs"


def test_a_balance_file_replaces_the_shipped_numbers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = json.loads(configs.BALANCE.read_text())
    raw["max_days"] = 2
    raw["tools"]["poison"]["per_game"] = 0
    balance = tmp_path / "balance.json"
    balance.write_text(json.dumps(raw))
    record = tmp_path / "game.json"

    castle_game(["--seed", "7", "--balance", str(balance), "--save", str(record)])
    capsys.readouterr()

    config = load_record(record).config
    assert config.max_days == 2
    assert config.tools.poison.per_game == 0
    assert default_balance().tools.poison.per_game != 0


def test_a_flag_beats_the_balance_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = json.loads(configs.BALANCE.read_text())
    raw["max_days"] = 3
    balance = tmp_path / "balance.json"
    balance.write_text(json.dumps(raw))
    record = tmp_path / "game.json"

    flags = ["--balance", str(balance), "--max-days", "2", "--save", str(record)]
    castle_game(["--seed", "7", *flags])
    capsys.readouterr()

    assert load_record(record).config.max_days == 2


def test_a_broken_balance_file_is_refused(tmp_path: Path) -> None:
    raw = json.loads(configs.BALANCE.read_text())
    del raw["max_days"]
    balance = tmp_path / "balance.json"
    balance.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="max_days"):
        castle_game(["--seed", "7", "--balance", str(balance)])
