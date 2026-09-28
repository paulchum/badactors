"""The authored castle must be simulable, and must create pressure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from badactors.castle.mapfile import (
    CastSpecError,
    MapError,
    build_world,
    load_cast,
    load_map,
    load_world,
)

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "configs/castle/map.json"
CAST_PATH = ROOT / "configs/castle/cast.example.json"


def _raw_map() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(MAP_PATH.read_text())
    return loaded


def _raw_cast() -> list[dict[str, Any]]:
    loaded: dict[str, Any] = json.loads(CAST_PATH.read_text())
    players: list[dict[str, Any]] = loaded["players"]
    return players


def _write(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "map.json"
    path.write_text(json.dumps(payload))
    return path


def test_shipped_castle_loads() -> None:
    castle = load_map(MAP_PATH)
    assert len(castle.rooms) == 10
    assert {room.lighting for room in castle.rooms} == {"bright", "dim", "dark"}
    assert castle.gathering_room == "great_hall"
    assert castle.conclave_room == "crypt"


def test_shipped_world_seats_the_cast() -> None:
    world = load_world(MAP_PATH, CAST_PATH)
    assert len(world.players) == len(world.seats) == 8
    assert set(world.placements) == {thing.id for thing in world.map.things}
    assert world.tick == 0 and world.phase == "breakfast"


def test_travel_time_is_per_passage() -> None:
    castle = load_map(MAP_PATH)
    assert castle.travel_ticks("great_hall", "dining_room") == 1
    assert castle.travel_ticks("chapel", "crypt") == 3
    assert castle.travel_ticks("great_hall", "crypt") is None


def test_passages_are_two_way() -> None:
    castle = load_map(MAP_PATH)
    for room in castle.rooms:
        for neighbor in castle.neighbors(room.id):
            assert room.id in castle.neighbors(neighbor)


# ------------------------------------------------------------- structural lints


def test_unknown_room_in_passage_is_rejected(tmp_path: Path) -> None:
    raw = _raw_map()
    raw["passages"][0]["rooms"] = ["great_hall", "solarium"]
    with pytest.raises(MapError, match="unknown room"):
        load_map(_write(tmp_path, raw))


def test_duplicate_passage_is_rejected(tmp_path: Path) -> None:
    raw = _raw_map()
    raw["passages"].append({"rooms": ["dining_room", "great_hall"], "travel_ticks": 2})
    with pytest.raises(MapError, match="declared twice"):
        load_map(_write(tmp_path, raw))


def test_unreachable_wing_is_rejected(tmp_path: Path) -> None:
    raw = _raw_map()
    raw["passages"] = [
        passage for passage in raw["passages"] if "armoury" not in passage["rooms"]
    ]
    raw["passages"].append({"rooms": ["armoury", "armoury"], "travel_ticks": 1})
    with pytest.raises(MapError):
        load_map(_write(tmp_path, raw))


def test_single_lighting_castle_is_rejected(tmp_path: Path) -> None:
    raw = _raw_map()
    for room in raw["rooms"]:
        room["lighting"] = "bright"
    with pytest.raises(MapError, match="bright, dim and dark"):
        load_map(_write(tmp_path, raw))


def test_object_without_placement_is_rejected(tmp_path: Path) -> None:
    raw = _raw_map()
    del raw["placements"]["candle_stub"]
    with pytest.raises(MapError, match="exactly one placement"):
        load_map(_write(tmp_path, raw))


def test_object_in_non_container_is_rejected(tmp_path: Path) -> None:
    raw = _raw_map()
    raw["placements"]["letter_opener"] = {
        "where": "container",
        "thing_id": "candle_stub",
    }
    with pytest.raises(MapError, match="non-container"):
        load_map(_write(tmp_path, raw))


def test_missing_gathering_room_is_rejected(tmp_path: Path) -> None:
    raw = _raw_map()
    raw["gathering_room"] = "ballroom"
    with pytest.raises(MapError, match="gathering_room"):
        load_map(_write(tmp_path, raw))


# --------------------------------------------------------------- authoring lints


def test_important_object_needs_two_claimants(tmp_path: Path) -> None:
    raw = _raw_map()
    for thing in raw["things"]:
        if thing["id"] == "brass_key":
            thing["claimants"] = ["knox"]
    castle = load_map(_write(tmp_path, raw))
    with pytest.raises(MapError, match="at least 2"):
        build_world(castle, _raw_cast(), world_id="w", seed=1)


def test_claimant_must_be_in_the_cast(tmp_path: Path) -> None:
    raw = _raw_map()
    for thing in raw["things"]:
        if thing["id"] == "brass_key":
            thing["claimants"] = ["knox", "nobody"]
    castle = load_map(_write(tmp_path, raw))
    with pytest.raises(MapError, match="unknown claimants"):
        build_world(castle, _raw_cast(), world_id="w", seed=1)


def test_useful_fact_must_sit_somewhere_private(tmp_path: Path) -> None:
    raw = _raw_map()
    for fact in raw["facts"]:
        fact["useful"] = fact["room_id"] == "great_hall"
    with pytest.raises(MapError, match="useful fact"):
        load_map(_write(tmp_path, raw))


def test_castle_without_a_private_room_is_rejected(tmp_path: Path) -> None:
    raw = _raw_map()
    for room in raw["rooms"]:
        room["private"] = False
    with pytest.raises(MapError, match="private room"):
        load_map(_write(tmp_path, raw))


def test_gathering_room_must_hold_the_cast(tmp_path: Path) -> None:
    raw = _raw_map()
    for room in raw["rooms"]:
        if room["id"] == "great_hall":
            room["capacity"] = 3
    castle = load_map(_write(tmp_path, raw))
    with pytest.raises(MapError, match="the cast is 8"):
        build_world(castle, _raw_cast(), world_id="w", seed=1)


# ----------------------------------------------------------------------- casting


def test_duplicate_cast_id_is_rejected() -> None:
    cast = _raw_cast()
    cast[1]["id"] = cast[0]["id"]
    with pytest.raises(CastSpecError, match="used twice"):
        build_world(load_map(MAP_PATH), cast, world_id="w", seed=1)


def test_cast_member_in_unknown_room_is_rejected() -> None:
    cast = _raw_cast()
    cast[0]["start_room"] = "dungeon"
    with pytest.raises(CastSpecError, match="unknown room"):
        build_world(load_map(MAP_PATH), cast, world_id="w", seed=1)


def test_empty_cast_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cast.json"
    path.write_text(json.dumps({"players": []}))
    with pytest.raises(CastSpecError, match="non-empty"):
        load_cast(path)
