"""The game on top of the world: roles, the pot, the dead, and who won.

A :class:`GameState` wraps the world and adds what only the referee knows — who
the traitors are, what tonight's kill is — beside what everyone has been told:
the fates the Steward announced, the tallies and the votes. Those public lists
grow only when an announcement is made, and each cites that announcement's
ledger entry, so an observation can be built from them without ever running ahead
of what the household has heard.

Game transitions change the state they are given. The runner in ``play`` works
on its own :func:`fork_game` copy, so a caller's state is never touched, and the
world beneath keeps its own copy-per-transition discipline.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import Field

from badactors.castle.cast import check_size
from badactors.castle.contracts import (
    SHIELD,
    AtRoom,
    CastleMap,
    CastlePhase,
    Contract,
    HeldBy,
    InContainer,
    InRoom,
    Offstage,
    Placement,
    Plan,
    Thing,
    WitnessRecord,
    WorldState,
)
from badactors.castle.ledger import append
from badactors.castle.mapfile import MapError, build_world
from badactors.castle.perceive import room_of
from badactors.castle.rng import draw
from badactors.castle.rules import GameConfig, MissionKind, MissionSpec, Role
from badactors.castle.steward import announce, note, tell
from badactors.castle.world import fork, start

SHIELD_KIND = SHIELD

NUMBERS = {
    0: "none",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


class GameSetupError(MapError):
    """The castle or config cannot host a game as written."""


# ------------------------------------------------------------------ public record


class Fate(Contract):
    """A death or banishment, as the Steward announced it."""

    player_id: str
    day: int = Field(ge=1)
    how: Literal["night", "banished"]
    role: Role
    seq: int = Field(ge=1)


class Tally(Contract):
    """A mission's result as announced: the exact pot change, and each worker's
    share only roughly."""

    day: int = Field(ge=1)
    kind: MissionKind
    pot_change: int = Field(ge=0)
    pot: int = Field(ge=0)
    reported: dict[str, int]
    shield_won: bool
    seq: int = Field(ge=1)


class VoteRound(Contract):
    day: int = Field(ge=1)
    round: int = Field(ge=1)
    votes: dict[str, str]
    banished: str | None
    seq: int = Field(ge=1)


class Offer(Contract):
    day: int = Field(ge=1)
    recruiter: str
    target: str
    seq: int = Field(ge=1)
    answer: Literal["accepted", "refused", "lapsed"] | None = None


# ------------------------------------------------------------------ the set pieces


TableStage = Literal[
    "accuse", "defend", "question", "answer", "vote", "endgame", "done"
]


class Asked(Contract):
    """Something put to a player at the table that they must answer: a
    question, a cross-examination, or a demand for an alibi over one part of a
    day."""

    asker: str
    target: str
    seq: int = Field(ge=1)
    kind: Literal["question", "cross", "alibi"] = "question"
    day: int | None = Field(default=None, ge=1)
    phase: CastlePhase | None = None


class Given(Contract):
    """An alibi given at the table, which its companion is asked to vouch for."""

    claimant: str
    room: str
    companion: str | None
    day: int = Field(ge=1)
    phase: CastlePhase
    seq: int = Field(ge=1)


class TableState(Contract):
    """The round table in progress. ``cursor`` walks the current stage's queue."""

    day: int = Field(ge=1)
    final: bool
    present: list[str]
    order: list[str]
    stage: TableStage = "accuse"
    cursor: int = Field(default=0, ge=0)
    round: int = Field(default=1, ge=1)
    accused: list[str] = Field(default_factory=list)
    questions: list[Asked] = Field(default_factory=list)
    alibis: list[Given] = Field(default_factory=list)
    votes: dict[str, str] = Field(default_factory=dict)
    endgame_votes: dict[str, str] = Field(default_factory=dict)
    floor_lost: int = Field(default=0, ge=0)
    """Speaking turns at the end of this stage that a filibuster has used up."""
    seqs: list[int] = Field(default_factory=list)
    """Every entry the table produced today, in order."""


class NightState(Contract):
    """Tonight's kill, from the dinner table to the dawn."""

    day: int = Field(ge=1)
    asked_poison: list[str] = Field(default_factory=list)
    poisoner: str | None = None
    poisoned: str | None = None
    asked_recruit: list[str] = Field(default_factory=list)
    offer: Offer | None = None
    conclave: list[str] = Field(default_factory=list)
    conclave_open: bool = False
    asked_nominate: list[str] = Field(default_factory=list)
    nominations: dict[str, str] = Field(default_factory=dict)
    target: str | None = None
    blocked: bool = False


# ------------------------------------------------------------------------- state


class GameState(Contract):
    schema_version: Literal[1] = 1
    world: WorldState
    config: GameConfig
    roles: dict[str, Role]
    starting_traitors: list[str]
    traitor_since: dict[str, int]
    """The ledger entry that made each traitor one."""
    pot: int = Field(default=0, ge=0)
    fates: list[Fate] = Field(default_factory=list)
    tallies: list[Tally] = Field(default_factory=list)
    votes: list[VoteRound] = Field(default_factory=list)
    offers: list[Offer] = Field(default_factory=list)
    mission: MissionSpec | None = None
    mission_seq: int | None = None
    seating: list[str] = Field(default_factory=list)
    seating_seq: int | None = None
    table: TableState | None = None
    night: NightState | None = None
    shields_spent: list[str] = Field(default_factory=list)
    forfeits: dict[str, int] = Field(default_factory=dict)
    """Round-table speaking turns each player has yet to give up for a
    filibuster."""
    status: Literal["playing", "over"] = "playing"
    winner: Role | None = None
    payouts: dict[str, int] = Field(default_factory=dict)


def fork_game(game: GameState) -> GameState:
    """A copy the runner may change freely; the world is forked, the rest copied."""
    memo: dict[int, object] = {
        id(game.world): fork(game.world),
        id(game.config): game.config,
    }
    return copy.deepcopy(game, memo)


# ----------------------------------------------------------------------- queries


def alive(game: GameState, player_id: str) -> bool:
    return game.world.players[player_id].alive


def living(game: GameState) -> list[str]:
    """Who is actually alive, in seat order. Not what anyone has been told."""
    return [pid for pid in game.world.seats if alive(game, pid)]


def living_traitors(game: GameState) -> list[str]:
    return [pid for pid in living(game) if game.roles[pid] == "traitor"]


def living_faithful(game: GameState) -> list[str]:
    return [pid for pid in living(game) if game.roles[pid] == "faithful"]


def publicly_living(game: GameState) -> list[str]:
    """Who the household has been told is alive: everyone without an announced
    fate. A murder is not public until breakfast."""
    gone = {fate.player_id for fate in game.fates}
    return [pid for pid in game.world.seats if pid not in gone]


def was_traitor_at(game: GameState, player_id: str, seq: int) -> bool:
    since = game.traitor_since.get(player_id)
    return since is not None and since <= seq


def present_at(game: GameState, room_id: str) -> list[str]:
    """Living, waking players standing in ``room_id``, in seat order."""
    world = game.world
    return [
        pid
        for pid in world.seats
        if (player := world.players[pid]).alive
        and not player.asleep
        and isinstance(player.position, AtRoom)
        and player.position.room_id == room_id
    ]


def shield_ids(game: GameState) -> list[str]:
    return [thing.id for thing in game.world.map.things if thing.kind == SHIELD_KIND]


def shield_held_by(game: GameState, player_id: str) -> str | None:
    """The first unspent shield this player is carrying, if any."""
    for shield_id in shield_ids(game):
        placement = game.world.placements.get(shield_id)
        if isinstance(placement, HeldBy) and placement.player_id == player_id:
            return shield_id
    return None


def name(game: GameState, player_id: str) -> str:
    return game.world.players[player_id].name


def names(game: GameState, player_ids: Sequence[str]) -> str:
    """ "Mara", "Mara and Sol", "Mara, Sol and Iris"."""
    labels = [name(game, pid) for pid in player_ids]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


# ------------------------------------------------------------------------- setup


def new_game(
    castle: CastleMap,
    characters: Sequence[Mapping[str, object]],
    *,
    seed: int,
    config: GameConfig | None = None,
    world_id: str = "castle-game",
) -> GameState:
    """Seat a cast, mint the shields, draw the traitors and open day one."""
    rules = config if config is not None else GameConfig.shipped()
    check_size(len(characters))
    lint_game(castle, rules)
    world = start(
        build_world(
            with_shields(castle, rules.shields),
            characters,
            world_id=world_id,
            seed=seed,
            balance=rules.balance(),
        )
    )
    game = GameState(
        world=world,
        config=rules,
        roles={pid: "faithful" for pid in world.seats},
        starting_traitors=[],
        traitor_since={},
    )
    _draw_roles(game)
    _welcome(game)
    return game


def lint_game(castle: CastleMap, config: GameConfig) -> None:
    """The castle must host every mission and fit a night's round trip."""
    rooms = {room.id for room in castle.rooms}
    things = {thing.id: thing for thing in castle.things}
    for mission in config.missions:
        if mission.room_id not in rooms:
            raise GameSetupError(f"Mission {mission.kind!r} is set in an unknown room.")
        station = things.get(mission.station)
        placement = castle.placements.get(mission.station)
        if station is None or not station.container:
            raise GameSetupError(
                f"Mission {mission.kind!r} needs a station that cannot be carried off."
            )
        if not (
            isinstance(placement, InRoom)
            and placement.room_id == mission.room_id
            and not placement.hidden
        ):
            raise GameSetupError(
                f"Mission {mission.kind!r} needs its station in plain sight in the "
                f"{mission.room_id}."
            )
    retire, descend = night_legs(castle)
    if descend is None:
        raise GameSetupError(
            "The conclave room must open directly off the chambers, so the traitors "
            "can slip there and back while the house sleeps."
        )
    if retire + 2 * descend + 1 > castle.phase_ticks.night:
        raise GameSetupError(
            "The night is too short for the traitors to reach the conclave, meet, "
            "and be back in the chambers by dawn."
        )
    if castle.room(castle.conclave_room).capacity < 3:
        raise GameSetupError("The conclave room must hold three traitors.")


def night_legs(castle: CastleMap) -> tuple[int, int | None]:
    """Ticks from the gathering room to the chambers, and from the chambers down
    to the conclave room (``None`` if not directly connected)."""
    here = castle.gathering_room
    retire = 0
    for room_id in castle.route(here, castle.chambers_room):
        retire += castle.travel_ticks(here, room_id) or 0
        here = room_id
    return retire, castle.travel_ticks(castle.chambers_room, castle.conclave_room)


def night_schedule(castle: CastleMap) -> tuple[int, int]:
    """After which night tick the recruitment window opens, and after which the
    conclave meets: once everyone has reached the chambers, and once the traitors
    have walked down and waited a tick."""
    retire, descend = night_legs(castle)
    return retire, retire + (descend or 0) + 1


def with_shields(castle: CastleMap, count: int) -> CastleMap:
    """The castle with ``count`` shield tokens in the Steward's keeping."""
    shields = [
        Thing(
            id=f"shield_{index}",
            kind=SHIELD_KIND,
            name="shield token",
            properties={"turns_aside": "one murder"},
        )
        for index in range(1, count + 1)
    ]
    placements: dict[str, Placement] = dict(castle.placements)
    placements.update({shield.id: Offstage() for shield in shields})
    return castle.model_copy(
        update={"things": [*castle.things, *shields], "placements": placements}
    )


def _draw_roles(game: GameState) -> None:
    world = game.world
    count = game.config.traitors
    pool = list(world.seats)
    cursor = world.rng_cursor
    rolls = []
    chosen: set[str] = set()
    for index in range(count):
        roll = draw(world.seed, cursor, f"traitor:{index}")
        cursor += 1
        rolls.append(roll)
        chosen.add(pool.pop(int(roll.value * len(pool))))
    traitors = [pid for pid in world.seats if pid in chosen]
    drawn = note(
        world,
        type="roles_drawn",
        text=f"The Steward drew the traitors: {names(game, traitors)}.",
        payload={"traitors": ",".join(traitors)},
        rolls=rolls,
        cursor=cursor,
    )
    game.starting_traitors = traitors
    for pid in traitors:
        make_traitor(game, pid, drawn.seq)
    for pid in world.seats:
        tell_role(game, pid)


def make_traitor(game: GameState, player_id: str, seq: int) -> None:
    """Turn a player: their role, and the traitor kit in the world."""
    game.roles[player_id] = "traitor"
    game.traitor_since[player_id] = seq
    game.world.kits[player_id] = "traitor"


def tell_role(game: GameState, player_id: str, *, joined: bool = False) -> None:
    """Tell one player, and nobody else, what they are."""
    role = game.roles[player_id]
    if role == "traitor":
        others = [pid for pid in living_traitors(game) if pid != player_id]
        company = (
            f"The other traitors are {names(game, others)}."
            if others
            else "You are the only traitor."
        )
        opening = "You are one of the traitors now." if joined else "You are a traitor."
        words = (
            f"{opening} {company} Kill by night, deceive by day, and survive to "
            "the end."
        )
    else:
        words = (
            "You are one of the Faithful. Find the traitors and banish them before "
            "they finish you."
        )
    tell(
        game.world,
        [player_id],
        type="role_told",
        words=words,
        payload={"player": player_id, "role": role},
    )


def _welcome(game: GameState) -> None:
    world = game.world
    players = len(world.seats)
    traitors = len(game.starting_traitors)
    announce(
        world,
        type="welcome",
        words=(
            f"Welcome to {world.map.name}. There are {NUMBERS[players]} of you, and "
            f"{NUMBERS[traitors]} are traitors. Each night they will try to kill; "
            "each day you may banish one of your own. Believe nothing unless I "
            "announce it."
        ),
        payload={"players": str(players), "traitors": str(traitors)},
    )


# ------------------------------------------------------------------------- death


def remove(game: GameState, player_id: str, *, type: str, cause: str) -> None:
    """Take a player out of the game: murdered, poisoned or banished.

    What they carried stays with them, as it would on a body; nothing is handed
    back to the castle behind the ledger's back.
    """
    player = game.world.players[player_id]
    player.alive = False
    player.asleep = False
    player.plan = Plan()
    note(
        game.world,
        type=type,
        text=f"{player.name} is gone: {cause}.",
        room_id=player.room_id,
        payload={"player": player_id, "cause": cause, "role": game.roles[player_id]},
    )


# --------------------------------------------------------------- missing items


def missing_items(game: GameState) -> None:
    """Things that went missing are reported as each part of the day begins.

    A shield picked from a pocket is missed by its owner, who files a report
    only they hold, since it says they carried one; the Steward announces to
    everyone that a shield token was stolen, never whose. The Steward's own
    seal, gone from the desk in the study, is announced as missing once each
    time it leaves. Nothing is reported by anyone who is dead.
    """
    world = game.world
    ledger = world.ledger
    reported = {
        int(entry.payload["steal"])
        for entry in ledger
        if entry.type == "missing" and "steal" in entry.payload
    }
    for entry in list(ledger):
        if entry.type != "steal" or entry.seq in reported:
            continue
        owner = entry.payload["to"]
        if not alive(game, owner):
            continue
        player = world.players[owner]
        append(
            world,
            type="missing",
            text=f"{player.name} found their shield token gone.",
            room_id=player.room_id,
            payload={"steal": str(entry.seq), "owner": owner},
            witnesses=[
                WitnessRecord(
                    player_id=owner,
                    sense="own",
                    confidence="clear",
                    channel="direct",
                    text="My shield token is gone from my pocket. Someone took it.",
                    recorded_actor=owner,
                    from_room=player.room_id,
                    noted=True,
                )
            ],
        )
        announce(
            world,
            type="missing_notice",
            words="A shield token has been reported stolen from the one who held it.",
            payload={"kind": SHIELD_KIND, "steal": str(entry.seq)},
        )
    _seal_missing(game)


def _seal_missing(game: GameState) -> None:
    world = game.world
    seal = next((thing for thing in world.map.things if thing.kind == "seal"), None)
    if seal is None:
        return
    home = world.map.placements.get(seal.id)
    if world.placements.get(seal.id) == home:
        return
    left = max(
        (
            entry.seq
            for entry in world.ledger
            if entry.payload.get("thing_id") == seal.id and entry.type == "take"
        ),
        default=0,
    )
    noticed = any(
        entry.seq > left
        for entry in world.ledger
        if entry.type == "missing_notice" and entry.payload.get("thing_id") == seal.id
    )
    if left and not noticed:
        kept = (
            room_of(world, home.thing_id)
            if isinstance(home, InContainer)
            else home.room_id
            if isinstance(home, InRoom)
            else None
        )
        room = world.map.room(kept or world.map.gathering_room)
        announce(
            world,
            type="missing_notice",
            words=f"My seal is missing from the {room.name}. Any decree under it "
            "that is not in my register is not mine.",
            payload={"thing_id": seal.id},
        )


# ----------------------------------------------------------------------- endings


def victor(game: GameState) -> Role | None:
    """A side that has already won: no traitors left, or nobody but traitors."""
    if not living_traitors(game):
        return "faithful"
    if not living_faithful(game):
        return "traitor"
    return None


def split(pot: int, winners: Sequence[str]) -> dict[str, int]:
    """Share the pot evenly; the odd coins go one each in seat order."""
    if not winners:
        return {}
    share, rest = divmod(pot, len(winners))
    return {
        pid: share + (1 if index < rest else 0) for index, pid in enumerate(winners)
    }


def finish(game: GameState, winner: Role, *, reason: str) -> None:
    """End the game: the winners still alive split the pot."""
    world = game.world
    winners = [pid for pid in living(game) if game.roles[pid] == winner]
    payouts = split(game.pot, winners)
    side = "Faithful" if winner == "faithful" else "traitors"
    note(
        world,
        type="payout",
        text=f"The {side} won; the pot of {game.pot} coins went to "
        f"{names(game, winners) or 'nobody'}.",
        payload={
            "winner": winner,
            "pot": str(game.pot),
            "shares": ",".join(f"{pid}={coins}" for pid, coins in payouts.items()),
        },
    )
    shares = ", ".join(f"{name(game, pid)} {coins}" for pid, coins in payouts.items())
    announce(
        world,
        type="game_over",
        words=(
            f"{reason} The game is over, and the {side} have won. The pot of "
            f"{game.pot} coins is split: {shares}."
        ),
        payload={"winner": winner},
    )
    game.status = "over"
    game.winner = winner
    game.payouts = payouts


__all__ = [
    "NUMBERS",
    "SHIELD_KIND",
    "Asked",
    "Fate",
    "GameSetupError",
    "GameState",
    "Given",
    "NightState",
    "Offer",
    "TableStage",
    "TableState",
    "Tally",
    "VoteRound",
    "alive",
    "finish",
    "fork_game",
    "lint_game",
    "living",
    "living_faithful",
    "living_traitors",
    "make_traitor",
    "missing_items",
    "name",
    "names",
    "new_game",
    "night_legs",
    "night_schedule",
    "present_at",
    "publicly_living",
    "remove",
    "shield_held_by",
    "shield_ids",
    "split",
    "tell_role",
    "victor",
    "was_traitor_at",
    "with_shields",
]
