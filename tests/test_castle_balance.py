"""Every number lives in the shipped balance.json, validated when it loads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from badactors.castle.balance import (
    DEFAULT_PATH,
    Balance,
    ToolBalance,
    default_balance,
    load_balance,
)
from badactors.castle.contracts import PLAN_TOOLS
from badactors.castle.kit import FAITHFUL_TOOLS, SPECS, TRAITOR_TOOLS
from badactors.castle.rules import GameConfig, rulebook


def _raw() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(DEFAULT_PATH.read_text())
    return loaded


def _write(tmp_path: Path, raw: dict[str, Any]) -> Path:
    path = tmp_path / "balance.json"
    path.write_text(json.dumps(raw))
    return path


def test_the_shipped_balance_loads() -> None:
    balance = load_balance(DEFAULT_PATH)
    assert balance == default_balance()
    assert balance.traitors == 2
    assert 0 < balance.tools.forge.trace < 1


def test_every_tool_has_its_numbers_and_its_spec() -> None:
    assert set(ToolBalance.model_fields) == set(SPECS)
    assert set(SPECS) == set(FAITHFUL_TOOLS) | set(TRAITOR_TOOLS)
    plan = {name for name, spec in SPECS.items() if spec.access == "plan"}
    assert plan == set(PLAN_TOOLS)
    for name, spec in SPECS.items():
        assert spec.kit == ("household" if name in FAITHFUL_TOOLS else "traitor")
        for part in (spec.rule, spec.preconditions, spec.cost, spec.success):
            assert part, (name, "every spec says what its numbers mean")
        if spec.manipulation:
            assert spec.intent, f"{name} is a manipulation tool and must declare intent"


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("tools", "forge", "trace"), 1.5, "less than or equal to 1"),
        (("tools", "plant", "success"), -0.1, "greater than or equal to 0"),
        (("tools", "poison", "cooldown_days"), "two", "valid integer"),
        (("dim_recognition",), 2, "less than or equal to 1"),
    ],
)
def test_a_number_out_of_range_fails_to_load(
    tmp_path: Path, path: tuple[str, ...], value: object, match: str
) -> None:
    raw = _raw()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError, match=match):
        load_balance(_write(tmp_path, raw))


def test_a_missing_number_fails_to_load(tmp_path: Path) -> None:
    for path in (("max_days",), ("tools", "hook"), ("tools", "filibuster", "chars")):
        raw = _raw()
        target = raw
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
        with pytest.raises(ValidationError, match="Field required"):
            load_balance(_write(tmp_path, raw))


def test_an_unknown_number_fails_to_load(tmp_path: Path) -> None:
    raw = _raw()
    raw["tools"]["forge"]["quality"] = 0.9
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_balance(_write(tmp_path, raw))
    raw = _raw()
    raw["tools"]["teleport"] = {"success": 1.0, "trace": 0.0}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_balance(_write(tmp_path, raw))


def test_a_game_config_is_the_file_with_overrides_merged_in() -> None:
    shipped = GameConfig.shipped()
    assert shipped.balance() == default_balance()
    tuned = GameConfig.shipped(traitors=3, tools={"poison": {"per_game": 0}})
    assert tuned.traitors == 3
    assert tuned.tools.poison.per_game == 0
    # Everything not named keeps the file's value, nested sections included.
    assert tuned.tools.poison.cooldown_days == shipped.tools.poison.cooldown_days
    assert tuned.tools.forge == shipped.tools.forge
    assert tuned.max_days == shipped.max_days


def test_a_stored_config_keeps_its_own_numbers() -> None:
    """A game record replays under the rules it was played under, whatever the
    file says now."""
    tuned = GameConfig.shipped(tools={"forge": {"trace": 0.9}})
    again = GameConfig.model_validate_json(tuned.model_dump_json())
    assert again == tuned
    assert again.tools.forge.trace == 0.9


def test_the_world_holds_the_same_numbers_as_the_game() -> None:
    from badactors.castle.play import start_game
    from castle_scenes import CAST, CASTLE

    config = GameConfig.shipped(tools={"steal": {"success": 0.25}})
    game = start_game(CASTLE, CAST.first(8), seed=3, config=config)
    assert game.world.balance == config.balance()
    assert game.world.balance.tools.steal.success == 0.25


def test_the_rulebook_states_each_tools_limits_from_the_numbers() -> None:
    config = GameConfig.shipped(tools={"hook": {"per_day": 3}})
    rules = rulebook(config, players=10, traitors=2, castle_name="Greywater")
    assert "hook: whisper something engineered" in rules
    assert "(3 a day)" in rules
    chars = config.tools.filibuster.chars
    assert f"up to {chars} characters" in rules
    for name in SPECS:
        assert f"  - {name}:" in rules


def test_balance_is_strict_about_types() -> None:
    with pytest.raises(ValidationError):
        Balance.model_validate({**_raw(), "traitors": "2"})
