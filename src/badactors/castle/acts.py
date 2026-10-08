"""Small pieces every act is built from: what is within reach, what a player
remembers of their own doing, and the private record of a step that failed.

The world's steps and the tools both use these, which is why they live apart
from either.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from badactors.castle.contracts import (
    GARMENT,
    HeldBy,
    InRoom,
    PlayerState,
    Thing,
    WitnessRecord,
    WorldState,
    WornBy,
)
from badactors.castle.ledger import (
    append,
    append_perceived,
    move_object,
    placement_payload,
)
from badactors.castle.perceive import perceive, visible_things, worn_garment


def thing(world: WorldState, thing_id: str) -> Thing:
    for item in world.map.things:
        if item.id == thing_id:
            return item
    raise KeyError(thing_id)


def find_thing(world: WorldState, thing_id: str) -> Thing | None:
    for item in world.map.things:
        if item.id == thing_id:
            return item
    return None


def on_person(world: WorldState, player_id: str, thing_id: str) -> str | None:
    """Whether a player holds a thing or wears it, or neither."""
    placement = world.placements.get(thing_id)
    if isinstance(placement, HeldBy) and placement.player_id == player_id:
        return "held"
    if isinstance(placement, WornBy) and placement.player_id == player_id:
        return "worn"
    return None


def reach(world: WorldState, player_id: str, room_id: str) -> list[str]:
    """Everything this player could lay a hand on: what they hold or wear, and
    what they can see in the room."""
    out = set(visible_things(world, player_id, room_id))
    out.update(
        thing_id
        for thing_id in world.placements
        if on_person(world, player_id, thing_id) is not None
    )
    return sorted(out)


def wearable(world: WorldState, player_id: str) -> list[str]:
    """Garments this player could put on: in hand, or lying within reach."""
    room_id = world.players[player_id].room_id
    near = set(visible_things(world, player_id, room_id)) if room_id else set()
    out: list[str] = []
    for item in world.map.things:
        if item.kind != GARMENT:
            continue
        placement = world.placements.get(item.id)
        held = isinstance(placement, HeldBy) and placement.player_id == player_id
        if held or (isinstance(placement, InRoom) and item.id in near):
            out.append(item.id)
    return sorted(out)


def change_garment(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    thing_id: str,
    *,
    own_text: str | None = None,
    payload: Mapping[str, str] | None = None,
    noted: bool = False,
) -> bool:
    """Change outer garments: one tick, seen by the room in whatever light it
    has. The garment taken off stays in hand, to be carried, dropped or hidden,
    and both garments' custody goes on the ledger. Changing sheds any manner a
    player had taken on with it. Returns whether the change happened."""
    if thing_id not in wearable(world, player.id):
        blocked(world, player, room_id, "there is no such garment within reach")
        return False
    garment = thing(world, thing_id)
    old = worn_garment(world, player.id)
    world.disguises.pop(player.id, None)
    # Witnesses see the change as the person they could make out beforehand.
    perception = perceive(
        world,
        kind="wear",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} changed into a {garment.name}.",
        own_text=own_text or f"I changed into a {garment.name}.",
    )
    if noted:
        perception.note(player.id)
    if old is not None:
        held = HeldBy(player_id=player.id)
        append(
            world,
            type="take_off",
            text=f"{player.name} took off a {old.name}.",
            actor_id=player.id,
            room_id=room_id,
            payload=placement_payload(old.id, held),
            witnesses=[own_record(player.id, room_id, f"I took off the {old.name}.")],
        )
        move_object(world, old.id, held)
    worn = WornBy(player_id=player.id)
    append_perceived(
        world,
        perception,
        type="wear",
        text=f"{player.name} put on a {garment.name}.",
        actor_id=player.id,
        room_id=room_id,
        payload={**placement_payload(thing_id, worn), **(payload or {})},
    )
    move_object(world, thing_id, worn)
    return True


def own_record(
    player_id: str,
    room_id: str | None,
    text: str,
    *,
    noted: bool = False,
) -> WitnessRecord:
    return WitnessRecord(
        player_id=player_id,
        sense="own",
        confidence="clear",
        channel="direct",
        text=text,
        recorded_actor=player_id,
        from_room=room_id,
        noted=noted,
    )


def blocked(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    reason: str,
    *,
    witnessed: bool = False,
    describe: Callable[[str], str] | None = None,
    tool: str | None = None,
) -> None:
    """A failed step is private to the actor: nobody else sees an intention.

    The exception is a failure with a visible body attached to it — turning back
    at a full door — where ``witnessed`` lets the room record what it saw.
    ``tool`` names the tool that was refused, for the audience.
    """
    perception = perceive(
        world,
        kind="blocked",
        actor_id=player.id,
        room_id=room_id,
        describe=describe or (lambda seen: f"{seen} hesitated."),
        own_text=f"I could not: {reason}.",
        silent=True,
        private=not witnessed,
    )
    payload = {"reason": reason}
    if tool is not None:
        payload["refused"] = tool
    append_perceived(
        world,
        perception,
        type="blocked",
        text=f"{player.name} could not act: {reason}.",
        actor_id=player.id,
        room_id=room_id,
        payload=payload,
    )


__all__ = [
    "blocked",
    "change_garment",
    "find_thing",
    "on_person",
    "own_record",
    "reach",
    "thing",
    "wearable",
]
