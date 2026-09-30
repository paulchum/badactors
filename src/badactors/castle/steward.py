"""The Steward's voice: the one official channel.

Every entry written here has no actor, because the host is not a player, and
every record it leaves is in the ``steward`` channel. Nothing a player decides is
ever routed through these helpers, which is what makes the channel trustworthy:
the leak checker rejects any Steward record on an entry a player acted in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from badactors.castle.contracts import LedgerEntry, RngDraw, WitnessRecord, WorldState
from badactors.castle.ledger import append

ANNOUNCED = "The Steward announced:"
TOLD = "The Steward told you privately:"


def announce(
    world: WorldState,
    *,
    type: str,
    words: str,
    payload: Mapping[str, str] | None = None,
    rolls: Sequence[RngDraw] = (),
    cursor: int | None = None,
) -> LedgerEntry:
    """Say something to every living player, wherever they are."""
    recipients = [pid for pid in world.seats if world.players[pid].alive]
    return _official(
        world,
        recipients,
        type=type,
        words=words,
        heading=ANNOUNCED,
        truth="The Steward announced:",
        payload=payload,
        rolls=rolls,
        cursor=cursor,
    )


def tell(
    world: WorldState,
    recipients: Iterable[str],
    *,
    type: str,
    words: str,
    payload: Mapping[str, str] | None = None,
    rolls: Sequence[RngDraw] = (),
    cursor: int | None = None,
) -> LedgerEntry:
    """Say something to these players only."""
    chosen = [pid for pid in recipients if world.players[pid].alive]
    names = ", ".join(world.players[pid].name for pid in chosen) or "nobody"
    return _official(
        world,
        chosen,
        type=type,
        words=words,
        heading=TOLD,
        truth=f"The Steward told {names} privately:",
        payload=payload,
        rolls=rolls,
        cursor=cursor,
    )


def note(
    world: WorldState,
    *,
    type: str,
    text: str,
    actor_id: str | None = None,
    room_id: str | None = None,
    payload: Mapping[str, str] | None = None,
    rolls: Sequence[RngDraw] = (),
    cursor: int | None = None,
) -> LedgerEntry:
    """Ground truth that nobody perceives: the referee writing down what
    happened, such as a death in the night before anyone has found the body."""
    return append(
        world,
        type=type,
        text=text,
        actor_id=actor_id,
        room_id=room_id,
        payload=payload,
        rolls=rolls,
        cursor=cursor,
    )


def _official(
    world: WorldState,
    recipients: Sequence[str],
    *,
    type: str,
    words: str,
    heading: str,
    truth: str,
    payload: Mapping[str, str] | None,
    rolls: Sequence[RngDraw],
    cursor: int | None,
) -> LedgerEntry:
    return append(
        world,
        type=type,
        text=truth,
        quote=words,
        payload=payload,
        rolls=rolls,
        cursor=cursor,
        witnesses=[
            WitnessRecord(
                player_id=player_id,
                sense="own",
                confidence="clear",
                channel="steward",
                text=heading,
                quote=words,
                from_room=world.players[player_id].room_id,
            )
            for player_id in recipients
        ],
    )


__all__ = ["ANNOUNCED", "TOLD", "announce", "note", "tell"]
