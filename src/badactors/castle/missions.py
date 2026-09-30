"""Missions: the pot grows with real work, and nobody sees the books.

A mission is worked physically. Its station is a fixture in a room, and each turn
a player spends using it during the mission phase is one unit of work, recorded
by the world like any other act and witnessed by whoever is in the room. When the
bell goes, the referee reads those entries back, rolls whatever the kind of
mission leaves to chance, and writes every worker's exact contribution to the
ledger. The household hears the exact change to the pot, and only a rough tally of
who did what: each worker's share is off by a seeded amount, so a claim about
the tally can be a mistake rather than a lie.

The hardest worker is given a shield in secret while the Steward has one left.

Sabotage arrives in phase 3. It hooks in at :func:`sabotaged`: a covert fumble
will subtract from one worker's yield there, and everything downstream — the
contribution entries, the pot, the tally — already accounts for it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from badactors.castle.contracts import (
    HeldBy,
    LedgerEntry,
    Offstage,
    RngDraw,
    WitnessRecord,
)
from badactors.castle.game import (
    GameState,
    Tally,
    name,
    shield_ids,
)
from badactors.castle.ledger import append, move_object, placement_payload
from badactors.castle.rng import draw
from badactors.castle.rules import MissionKind
from badactors.castle.steward import announce, note

CIPHER_ODDS = 0.5
CIPHER_PRIZE = 5
CARRY_LOAD = 2
TEAM_SIZE = 3
"""Workers at the clock case in the same tick for each of them to earn double."""


def open_mission(game: GameState) -> None:
    """Draw today's mission and brief the household."""
    world = game.world
    options = game.config.missions
    roll = draw(world.seed, world.rng_cursor, f"mission:day{world.day}")
    mission = options[int(roll.value * len(options))]
    entry = announce(
        world,
        type="mission_briefing",
        words=f"Today's mission is {mission.title}. {mission.briefing} The bell "
        "ends it.",
        payload={
            "kind": mission.kind,
            "room": mission.room_id,
            "station": mission.station,
        },
        rolls=[roll],
        cursor=world.rng_cursor + 1,
    )
    game.mission = mission
    game.mission_seq = entry.seq


def work_done(game: GameState) -> dict[str, list[LedgerEntry]]:
    """Each worker's turns at today's station, in seat order."""
    mission = game.mission
    if mission is None or game.mission_seq is None:
        return {}
    turns: dict[str, list[LedgerEntry]] = {}
    for entry in game.world.ledger[game.mission_seq :]:
        if (
            entry.type == "use"
            and entry.actor_id is not None
            and entry.room_id == mission.room_id
            and entry.payload.get("thing_id") == mission.station
        ):
            turns.setdefault(entry.actor_id, []).append(entry)
    return {pid: turns[pid] for pid in game.world.seats if pid in turns}


def sabotaged(game: GameState, player_id: str, turns: Sequence[LedgerEntry]) -> int:
    """Coins this worker covertly kept out of the pot. Always zero until the
    sabotage tool exists; it is the one place that tool will change."""
    return 0


def close_mission(game: GameState) -> None:
    """Ring the bell: measure the work, grow the pot, publish a rough tally, and
    hand the best worker a shield in secret."""
    mission = game.mission
    if mission is None:
        return
    world = game.world
    turns = work_done(game)
    crowd = Counter(entry.tick for entries in turns.values() for entry in entries)
    cursor = world.rng_cursor
    contributions: dict[str, int] = {}
    for pid, entries in turns.items():
        rolls = []
        earned = 0
        for entry in entries:
            if mission.kind == "cipher":
                roll = draw(
                    world.seed,
                    cursor,
                    f"cipher:{pid}:t{entry.tick}",
                    threshold=CIPHER_ODDS,
                )
                cursor += 1
                rolls.append(roll)
                earned += CIPHER_PRIZE if roll.success else 0
            else:
                earned += _flat_yield(mission.kind, crowd[entry.tick])
        kept_back = sabotaged(game, pid, entries)
        contributions[pid] = max(0, earned - kept_back)
        note(
            world,
            type="contribution",
            text=f"{name(game, pid)} worked {len(entries)} turn(s) and earned "
            f"{contributions[pid]} coins.",
            actor_id=pid,
            room_id=mission.room_id,
            payload={
                "mission": mission.kind,
                "turns": str(len(entries)),
                "earned": str(earned),
                "sabotage": str(kept_back),
                "coins": str(contributions[pid]),
            },
            rolls=rolls,
            cursor=cursor,
        )
    change = sum(contributions.values())
    game.pot += change

    reported, noise, cursor = _rough_tally(game, contributions)
    spare = _spare_shield(game) if max(contributions.values(), default=0) > 0 else None
    clerk = ", ".join(
        f"{name(game, pid)} about {coins}" for pid, coins in reported.items()
    )
    if change:
        words = (
            f"The bell. The mission added {change} coins; the pot now holds "
            f"{game.pot}. My clerk's rough tally of the work: {clerk}."
        )
    else:
        words = f"The bell. Nobody worked the mission; the pot stays at {game.pot}."
    if spare:
        words += " A shield has been won. Its holder knows who they are."
    entry = announce(
        world,
        type="mission_result",
        words=words,
        payload={
            "mission": mission.kind,
            "pot_change": str(change),
            "pot": str(game.pot),
            "reported": ",".join(f"{pid}={coins}" for pid, coins in reported.items()),
            "workers": str(len(contributions)),
        },
        rolls=noise,
        cursor=cursor,
    )
    game.tallies.append(
        Tally(
            day=world.day,
            kind=mission.kind,
            pot_change=change,
            pot=game.pot,
            reported=reported,
            shield_won=spare is not None,
            seq=entry.seq,
        )
    )
    if spare:
        _award_shield(game, spare, contributions)


def _flat_yield(kind: MissionKind, workers_this_tick: int) -> int:
    if kind == "carry":
        return CARRY_LOAD
    return 2 if workers_this_tick >= TEAM_SIZE else 1


def _rough_tally(
    game: GameState, contributions: dict[str, int]
) -> tuple[dict[str, int], list[RngDraw], int]:
    """Each worker's share, off by up to ``tally_noise`` coins either way."""
    world = game.world
    spread = game.config.tally_noise
    cursor = world.rng_cursor
    reported: dict[str, int] = {}
    rolls: list[RngDraw] = []
    for pid, coins in contributions.items():
        roll = draw(world.seed, cursor, f"tally:{pid}:day{world.day}")
        cursor += 1
        rolls.append(roll)
        offset = int(roll.value * (2 * spread + 1)) - spread
        reported[pid] = max(0, coins + offset)
    return reported, rolls, cursor


def _spare_shield(game: GameState) -> str | None:
    """The first shield still in the Steward's keeping and never used."""
    for shield_id in shield_ids(game):
        if (
            isinstance(game.world.placements.get(shield_id), Offstage)
            and shield_id not in game.shields_spent
        ):
            return shield_id
    return None


def _award_shield(game: GameState, shield: str, contributions: dict[str, int]) -> None:
    """Give the best worker the shield, in secret. Ties are settled by lot."""
    world = game.world
    best = max(contributions.values())
    leaders = [pid for pid, coins in contributions.items() if coins == best]
    rolls: list[RngDraw] = []
    cursor = world.rng_cursor
    if len(leaders) > 1:
        roll = draw(world.seed, cursor, f"shield:day{world.day}")
        cursor += 1
        rolls.append(roll)
        winner = leaders[int(roll.value * len(leaders))]
    else:
        winner = leaders[0]
    placement = HeldBy(player_id=winner)
    words = (
        "You worked hardest today, so this is yours: a shield token. Keep it on "
        "you. It will turn aside one murder, and then it is spent. The others know "
        "only that a shield was won."
    )
    append(
        world,
        type="awarded",
        text=f"The Steward slipped a shield token to {name(game, winner)}.",
        payload=placement_payload(shield, placement),
        rolls=rolls,
        cursor=cursor,
        witnesses=[
            WitnessRecord(
                player_id=winner,
                sense="own",
                confidence="clear",
                channel="steward",
                text="The Steward told you privately:",
                quote=words,
                from_room=world.players[winner].room_id,
            )
        ],
    )
    move_object(world, shield, placement)


__all__ = [
    "CARRY_LOAD",
    "CIPHER_ODDS",
    "CIPHER_PRIZE",
    "TEAM_SIZE",
    "close_mission",
    "open_mission",
    "sabotaged",
    "work_done",
]
