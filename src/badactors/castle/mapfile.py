"""Load and lint an authored castle.

Two kinds of check run here. Structural lints reject a map that cannot be
simulated: unknown rooms, one-way passages, an unreachable wing. Authoring lints
reject a map that can be simulated but won't produce pressure, which is the point
of the exercise — an object with a single plausible claimant accuses its owner by
itself, and a castle whose useful facts all sit in public rooms gives nobody a
reason to go somewhere private.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path

from badactors.castle.contracts import (
    AtRoom,
    CastleMap,
    InContainer,
    InRoom,
    Placement,
    PlayerState,
    WorldState,
)


class MapError(ValueError):
    """The authored castle or cast cannot be used as written."""


class CastSpecError(MapError):
    """The authored cast cannot be used as written."""


MIN_CLAIMANTS = 2


def load_map(path: str | Path) -> CastleMap:
    """Parse, validate and lint a castle map file."""
    castle = CastleMap.model_validate(_read_json(path))
    lint_map(castle)
    return castle


def load_cast(path: str | Path) -> list[Mapping[str, object]]:
    raw = _read_json(path)
    members = raw.get("players")
    if not isinstance(members, list) or not members:
        raise CastSpecError("A cast file needs a non-empty players list.")
    out: list[Mapping[str, object]] = []
    for member in members:
        if not isinstance(member, Mapping):
            raise CastSpecError("Every cast entry must be an object.")
        out.append(member)
    return out


def lint_map(castle: CastleMap) -> None:
    """Raise :class:`MapError` on the first problem, naming what to fix."""
    _lint_rooms(castle)
    _lint_passages(castle)
    _lint_connected(castle)
    _lint_things(castle)
    _lint_private_fact(castle)


def build_world(
    castle: CastleMap,
    cast: Sequence[Mapping[str, object]],
    *,
    world_id: str,
    seed: int,
) -> WorldState:
    """Seat an authored cast in an authored castle."""
    room_ids = {room.id for room in castle.rooms}
    players: dict[str, PlayerState] = {}
    seats: list[str] = []
    for index, member in enumerate(cast, start=1):
        start = member.get("start_room", castle.chambers_room)
        if not isinstance(start, str) or start not in room_ids:
            raise CastSpecError(f"Cast member {index} starts in an unknown room.")
        player = PlayerState.model_validate(
            {
                "id": member.get("id"),
                "name": member.get("name"),
                "persona": member.get("persona"),
                "appearance": member.get("appearance"),
                "position": AtRoom(room_id=start).model_dump(mode="json"),
            }
        )
        if player.id in players:
            raise CastSpecError(f"Cast member id {player.id!r} is used twice.")
        players[player.id] = player
        seats.append(player.id)

    _lint_claimants(castle, set(players))
    _lint_capacity(castle, players)
    return WorldState(
        id=world_id,
        seed=seed,
        map=castle,
        seats=seats,
        players=players,
        placements=dict(castle.placements),
    )


def load_world(
    map_path: str | Path,
    cast_path: str | Path,
    *,
    world_id: str = "castle-1",
    seed: int = 13,
) -> WorldState:
    return build_world(
        load_map(map_path),
        load_cast(cast_path),
        world_id=world_id,
        seed=seed,
    )


# ------------------------------------------------------------------------ lints


def _lint_rooms(castle: CastleMap) -> None:
    ids = [room.id for room in castle.rooms]
    duplicates = sorted({room_id for room_id in ids if ids.count(room_id) > 1})
    if duplicates:
        raise MapError(f"Duplicate room ids: {', '.join(duplicates)}.")
    known = set(ids)
    for label, room_id in (
        ("gathering_room", castle.gathering_room),
        ("conclave_room", castle.conclave_room),
        ("chambers_room", castle.chambers_room),
    ):
        if room_id not in known:
            raise MapError(f"{label} {room_id!r} is not a room in this castle.")
    if len({"bright", "dim", "dark"} & {room.lighting for room in castle.rooms}) < 3:
        raise MapError("A castle needs bright, dim and dark rooms to be worth playing.")


def _lint_passages(castle: CastleMap) -> None:
    known = {room.id for room in castle.rooms}
    seen: set[frozenset[str]] = set()
    for passage in castle.passages:
        first, second = passage.rooms
        if first == second:
            raise MapError(f"Passage {first!r} connects a room to itself.")
        for room_id in passage.rooms:
            if room_id not in known:
                raise MapError(f"Passage names unknown room {room_id!r}.")
        pair = frozenset(passage.rooms)
        if pair in seen:
            raise MapError(f"Passage {first!r}-{second!r} is declared twice.")
        seen.add(pair)


def _lint_connected(castle: CastleMap) -> None:
    start = castle.rooms[0].id
    reached = {start}
    queue = deque([start])
    while queue:
        for neighbor in castle.neighbors(queue.popleft()):
            if neighbor not in reached:
                reached.add(neighbor)
                queue.append(neighbor)
    missing = sorted({room.id for room in castle.rooms} - reached)
    if missing:
        raise MapError(f"Unreachable rooms: {', '.join(missing)}.")


def _lint_things(castle: CastleMap) -> None:
    ids = [thing.id for thing in castle.things]
    duplicates = sorted({thing_id for thing_id in ids if ids.count(thing_id) > 1})
    if duplicates:
        raise MapError(f"Duplicate object ids: {', '.join(duplicates)}.")
    known_rooms = {room.id for room in castle.rooms}
    containers = {thing.id for thing in castle.things if thing.container}
    if set(castle.placements) != set(ids):
        missing = sorted(set(ids) - set(castle.placements))
        extra = sorted(set(castle.placements) - set(ids))
        detail = f"missing {missing}" if missing else f"unknown {extra}"
        raise MapError(f"Every object needs exactly one placement; {detail}.")
    for thing_id, placement in castle.placements.items():
        _lint_placement(thing_id, placement, known_rooms, containers)


def _lint_placement(
    thing_id: str,
    placement: Placement,
    known_rooms: set[str],
    containers: set[str],
) -> None:
    if isinstance(placement, InRoom):
        if placement.room_id not in known_rooms:
            raise MapError(f"Object {thing_id!r} starts in an unknown room.")
        return
    if isinstance(placement, InContainer):
        if placement.thing_id not in containers:
            raise MapError(f"Object {thing_id!r} starts in a non-container.")
        return
    raise MapError(
        f"Object {thing_id!r} starts held by {placement.player_id!r}; "
        "authored maps place objects in rooms or containers."
    )


def _lint_private_fact(castle: CastleMap) -> None:
    known_rooms = {room.id for room in castle.rooms}
    private = {room.id for room in castle.rooms if room.private}
    ids = [fact.id for fact in castle.facts]
    duplicates = sorted({fact_id for fact_id in ids if ids.count(fact_id) > 1})
    if duplicates:
        raise MapError(f"Duplicate fact ids: {', '.join(duplicates)}.")
    for fact in castle.facts:
        if fact.room_id not in known_rooms:
            raise MapError(f"Fact {fact.id!r} sits in an unknown room.")
    if not private:
        raise MapError("A castle needs at least one private room to hide a fact in.")
    if not any(fact.useful and fact.room_id in private for fact in castle.facts):
        raise MapError(
            "At least one useful fact must sit in a private room, so that going "
            "somewhere private is worth the risk of being seen."
        )


def _lint_claimants(castle: CastleMap, player_ids: set[str]) -> None:
    for thing in castle.things:
        unknown = sorted(set(thing.claimants) - player_ids)
        if unknown:
            raise MapError(
                f"Object {thing.id!r} names unknown claimants: {', '.join(unknown)}."
            )
        if len(set(thing.claimants)) != len(thing.claimants):
            raise MapError(f"Object {thing.id!r} repeats a claimant.")
        if thing.important and len(thing.claimants) < MIN_CLAIMANTS:
            raise MapError(
                f"Important object {thing.id!r} has "
                f"{len(thing.claimants)} claimant(s); it needs at least "
                f"{MIN_CLAIMANTS} so that possessing it is not proof by itself."
            )


def _lint_capacity(castle: CastleMap, players: Mapping[str, PlayerState]) -> None:
    size = len(players)
    for label, room_id in (
        ("gathering_room", castle.gathering_room),
        ("chambers_room", castle.chambers_room),
    ):
        room = castle.room(room_id)
        if room.capacity < size:
            raise MapError(
                f"{label} {room.id!r} holds {room.capacity} but the cast is {size}."
            )
    counts: dict[str, int] = {}
    for player in players.values():
        start = player.room_id
        if start is not None:
            counts[start] = counts.get(start, 0) + 1
    for start, count in sorted(counts.items()):
        if count > castle.room(start).capacity:
            raise MapError(f"More cast members start in {start!r} than it holds.")


def _read_json(path: str | Path) -> Mapping[str, object]:
    loaded = json.loads(Path(path).read_text())
    if not isinstance(loaded, Mapping):
        raise MapError(f"{path} must contain a JSON object.")
    return loaded


__all__ = [
    "CastSpecError",
    "MapError",
    "build_world",
    "lint_map",
    "load_cast",
    "load_map",
    "load_world",
]
