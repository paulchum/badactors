"""Load and lint an authored castle.

Two kinds of check run here. Structural lints reject a map that cannot be
simulated: unknown rooms, one-way passages, an unreachable wing. Authoring lints
reject a map that can be simulated but won't produce pressure, which is the point
of the exercise — an object with a single plausible claimant accuses its owner by
itself, and a castle whose useful facts all sit in public rooms gives nobody a
reason to go somewhere private.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, deque
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from badactors.castle.balance import Balance, default_balance
from badactors.castle.contracts import (
    GARMENT,
    AtRoom,
    CastleMap,
    HeldBy,
    InContainer,
    InRoom,
    Placement,
    PlayerState,
    Thing,
    WorldState,
    WornBy,
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
    _lint_stores(castle)


def build_world(
    castle: CastleMap,
    cast: Sequence[Mapping[str, object]],
    *,
    world_id: str,
    seed: int,
    balance: Balance | None = None,
) -> WorldState:
    """Seat an authored cast in an authored castle, with paper in the paper
    store and a glass in the glass store for every place at dinner."""
    numbers = balance if balance is not None else default_balance()
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
                "build": member.get("build"),
                "starting_garment": member.get("garment"),
                "detail": member.get("detail"),
                "secret": member.get("secret"),
                "position": AtRoom(room_id=start).model_dump(mode="json"),
            }
        )
        if player.id in players:
            raise CastSpecError(f"Cast member id {player.id!r} is used twice.")
        players[player.id] = player
        seats.append(player.id)

    _lint_claimants(castle, set(players))
    _lint_capacity(castle, players)
    lint_shared_garments(players.values())
    stocked, stock = _stock(castle, places=len(seats), paper=numbers.paper)
    dressed, worn = _dress(stocked, players, seed)
    return WorldState(
        id=world_id,
        seed=seed,
        map=dressed,
        seats=seats,
        players=players,
        placements={**castle.placements, **stock, **worn},
        balance=numbers,
    )


def lint_shared_garments(players: Iterable[PlayerState]) -> None:
    """Every garment must be worn by several people when the game starts.

    A dim witness whose roll fails records only a garment, so a garment one
    person wears would name them. Two wearers is the floor; three whenever the
    cast is big enough for every garment to have three. Deaths thin this out as
    the game goes on, and that is allowed.
    """
    seated = list(players)
    counts = Counter(player.starting_garment for player in seated)
    least = 3 if 3 * len(counts) <= len(seated) else 2
    for name, count in sorted(counts.items()):
        if count < least:
            raise CastSpecError(
                f"Only {count} of the cast start in a {name}; every garment needs "
                f"at least {least} wearers, so a dim witness who sees it cannot "
                "tell who it was."
            )


def _dress(
    castle: CastleMap,
    players: Mapping[str, PlayerState],
    seed: int,
) -> tuple[CastleMap, dict[str, Placement]]:
    """Put a real garment on every back.

    Each garment is an object with its own custody. Ids are numbered in an order
    drawn from the seed, so an id found on a coat left in the cellar says
    nothing about who first wore it; only its custody chain does.
    """
    by_name: dict[str, list[str]] = {}
    for pid, player in players.items():
        by_name.setdefault(player.starting_garment, []).append(pid)
    taken = {thing.id for thing in castle.things}
    things = list(castle.things)
    worn: dict[str, Placement] = {}
    for name in sorted(by_name):
        slug = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
        wearers = sorted(
            by_name[name],
            key=lambda pid: hashlib.sha256(f"{seed}:{pid}".encode()).hexdigest(),
        )
        for number, pid in enumerate(wearers, start=1):
            thing_id = f"{slug}_{number}"
            if thing_id in taken:
                raise MapError(f"Garment id {thing_id!r} clashes with an object.")
            things.append(Thing(id=thing_id, kind=GARMENT, name=name))
            worn[thing_id] = WornBy(player_id=pid)
    return castle.model_copy(update={"things": things}), worn


ORDINALS = (
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
)


def _stock(
    castle: CastleMap, *, places: int, paper: int
) -> tuple[CastleMap, dict[str, Placement]]:
    """Mint the castle's consumables into their stores: sheets of notepaper, and
    one dinner glass for each place at the table. The glass at a place is the one
    its diner drinks from, so the seating plan says whose glass is whose."""
    things = list(castle.things)
    placements: dict[str, Placement] = {}
    taken = {thing.id for thing in castle.things}
    minted: list[tuple[str, Thing]] = []
    if castle.paper_store is not None:
        minted += [
            (
                castle.paper_store,
                Thing(id=f"paper_{n}", kind="paper", name="sheet of notepaper"),
            )
            for n in range(1, paper + 1)
        ]
    if castle.glass_store is not None:
        minted += [
            (
                castle.glass_store,
                Thing(
                    id=f"glass_{n}",
                    kind="glass",
                    name=f"glass from the {_ordinal(n)} place",
                ),
            )
            for n in range(1, places + 1)
        ]
    for store, item in minted:
        if item.id in taken:
            raise MapError(f"Minted object id {item.id!r} clashes with an object.")
        things.append(item)
        placements[item.id] = InContainer(thing_id=store)
    return castle.model_copy(update={"things": things}), placements


def _ordinal(n: int) -> str:
    return ORDINALS[n - 1] if n <= len(ORDINALS) else f"{n}th"


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
    for thing_id in sorted(castle.placements):
        _lint_reaches_a_room(castle.placements, thing_id)


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
    where = (
        f"held by {placement.player_id!r}"
        if isinstance(placement, HeldBy)
        else placement.where
    )
    raise MapError(
        f"Object {thing_id!r} starts {where}; "
        "authored maps place objects in rooms or containers."
    )


def _lint_reaches_a_room(placements: Mapping[str, Placement], thing_id: str) -> None:
    """Containers may nest, but every chain must end in a room."""
    seen: set[str] = set()
    current = thing_id
    while True:
        if current in seen:
            raise MapError(f"Object {thing_id!r} sits in a loop of containers.")
        seen.add(current)
        placement = placements[current]
        if not isinstance(placement, InContainer):
            return
        current = placement.thing_id


def _lint_stores(castle: CastleMap) -> None:
    things = {thing.id: thing for thing in castle.things}
    for label, store in (
        ("paper_store", castle.paper_store),
        ("glass_store", castle.glass_store),
    ):
        if store is None:
            continue
        if store not in things or not things[store].container:
            raise MapError(f"{label} {store!r} must be a container in this castle.")
    for room in castle.rooms:
        if room.camera and room.lighting == "dark":
            raise MapError(f"A camera in the dark {room.id!r} would record nothing.")
    if any(room.camera for room in castle.rooms) and not any(
        thing.kind == "camera_log" for thing in castle.things
    ):
        raise MapError("Rooms have cameras, but there is no camera log to read.")


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
