"""Set-ups shared by the toolkit tests.

A tool scene is a started world with the shipped castle and cast, one shield
token in the Steward's keeping, and the traitor kit handed to whoever the test
names. Everyone stands in the Great Hall with no plan, so nothing moves unless a
test moves it. Setting a scene may place people and hand them objects directly:
a hand-over is written to the ledger like any custody change, so the custody
invariant holds from the first entry.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from badactors.castle.balance import Balance
from badactors.castle.contracts import (
    AtRoom,
    HeldBy,
    LedgerEntry,
    Plan,
    ScopedObservation,
    Step,
    WitnessRecord,
    WorldState,
)
from badactors.castle.game import with_shields
from badactors.castle.ledger import append, move_object, placement_payload
from badactors.castle.mapfile import build_world
from badactors.castle.scope import assert_no_leak, build_observation
from badactors.castle.world import start, submit_plan, tick
from castle_scenes import CAST, CASTLE


def scene(
    seed: int = 13,
    *,
    players: int = 10,
    traitors: Iterable[str] = ("knox",),
    **tools: dict[str, Any],
) -> WorldState:
    """A quiet world to use tools in. ``tools`` overrides tool numbers, such as
    ``forge={"trace": 1.0}``."""
    castle = with_shields(CASTLE, 1)
    world = start(
        build_world(
            castle,
            CAST.first(players).characters(),
            world_id="tools",
            seed=seed,
        )
    )
    for pid in traitors:
        world.kits[pid] = "traitor"
    for player in world.players.values():
        player.plan = Plan()
    if tools:
        world.balance = tuned(world.balance, **tools)
    return world


def tuned(balance: Balance, **tools: dict[str, Any]) -> Balance:
    numbers = balance.tools.model_copy(
        update={
            name: balance.tools.of(name).model_copy(update=changes)  # type: ignore[arg-type]
            for name, changes in tools.items()
        }
    )
    return balance.model_copy(update={"tools": numbers})


def put(world: WorldState, player_id: str, room_id: str) -> None:
    """Stand someone in a room, for setting a scene."""
    world.players[player_id].position = AtRoom(room_id=room_id)


def give(world: WorldState, player_id: str, thing_id: str) -> None:
    """Hand someone an object, on the ledger like any custody change."""
    placement = HeldBy(player_id=player_id)
    append(
        world,
        type="placed",
        text=f"The scene puts the {thing_id} in {player_id}'s hands.",
        payload=placement_payload(thing_id, placement),
    )
    move_object(world, thing_id, placement)


def act(world: WorldState, player_id: str, *steps: Step) -> WorldState:
    """One player carries out these steps, a tick each."""
    world = submit_plan(world, player_id, Plan(goal="test", steps=list(steps)))
    for _ in steps:
        world = tick(world)
    return world


def uses(world: WorldState, tool: str) -> list[LedgerEntry]:
    return [entry for entry in world.ledger if entry.payload.get("tool") == tool]


def last(world: WorldState, tool: str) -> LedgerEntry:
    found = uses(world, tool)
    assert found, f"{tool} was never used"
    return found[-1]


def refused(world: WorldState) -> LedgerEntry | None:
    """The newest entry, if it is a refusal."""
    entry = world.ledger[-1]
    return entry if entry.type == "blocked" else None


def own(entry: LedgerEntry, player_id: str) -> WitnessRecord:
    record = entry.record_for(player_id)
    assert record is not None, f"{player_id} holds no record of entry {entry.seq}"
    return record


def seen(world: WorldState, player_id: str) -> ScopedObservation:
    """A player's observation, leak-checked as every one in a game is."""
    observation = build_observation(world, player_id)
    assert_no_leak(observation, world)
    return observation


def paper(world: WorldState) -> str:
    """The first sheet of notepaper still in the paper store."""
    store = world.map.paper_store
    return next(
        thing.id
        for thing in world.map.things
        if thing.kind == "paper"
        and getattr(world.placements[thing.id], "thing_id", None) == store
    )


__all__ = [
    "act",
    "give",
    "last",
    "own",
    "paper",
    "put",
    "refused",
    "scene",
    "seen",
    "tuned",
    "uses",
]
