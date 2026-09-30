"""The game's balance knobs, and the rulebook every player is handed.

Everything a balance pass might tune sits in :class:`GameConfig`, so a stored
game carries the exact rules it was played under. The rulebook is written from the
same config, so what a player is told and what the referee enforces cannot drift
apart.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from badactors.castle.contracts import Contract

Role = Literal["faithful", "traitor"]
MissionKind = Literal["cipher", "carry", "assemble"]

MIN_PLAYERS = 8
MAX_PLAYERS = 10


class MissionSpec(Contract):
    """Where a kind of mission is worked. Each use of the station is one turn of
    work; what a turn is worth depends on the kind (see ``missions``)."""

    kind: MissionKind
    title: str = Field(min_length=1, max_length=60)
    room_id: str
    station: str
    briefing: str = Field(min_length=1, max_length=300)


def _default_missions() -> list[MissionSpec]:
    return [
        MissionSpec(
            kind="cipher",
            title="The Cipher Chest",
            room_id="chapel",
            station="cipher_chest",
            briefing=(
                "Work the dials of the cipher chest in the Chapel. Every turn at "
                "the dials has an even chance of cracking a ring, worth 5 coins."
            ),
        ),
        MissionSpec(
            kind="carry",
            title="The Stores",
            room_id="dining_room",
            station="supply_crates",
            briefing=(
                "Haul the supply crates in the Dining Room. Every load you carry "
                "is worth 2 coins."
            ),
        ),
        MissionSpec(
            kind="assemble",
            title="The Great Clock",
            room_id="great_hall",
            station="clock_case",
            briefing=(
                "Rebuild the clock in the Great Hall. Every turn at the clock "
                "case is worth 1 coin, or 2 while three or more of you work it "
                "at once."
            ),
        ),
    ]


class GameConfig(Contract):
    max_days: int = Field(default=8, ge=2, le=30)
    traitors: int = Field(default=2, ge=2, le=3)
    """Two by default at every size. Three is allowed, but among eight to ten
    bots, three traitors won nine games in ten; two won half."""
    shields: int = Field(default=3, ge=0, le=8)
    """Shield tokens the Steward holds for the whole game."""
    endgame_at: int = Field(default=4, ge=2, le=MAX_PLAYERS)
    """At this many living players the round table becomes the final one."""
    tally_noise: int = Field(default=3, ge=0, le=10)
    """Each worker's reported contribution is off by up to this many coins."""
    poisons: int = Field(default=1, ge=0, le=10)
    """Poisonings the traitors may use in a game, until phase 3 gives poison
    its traces and a proper cost."""
    recent_limit: int = Field(default=40, ge=1, le=200)
    missions: list[MissionSpec] = Field(default_factory=_default_missions)

    @model_validator(mode="after")
    def distinct_missions(self) -> GameConfig:
        kinds = [mission.kind for mission in self.missions]
        if not kinds:
            raise ValueError("A game needs at least one mission.")
        if len(set(kinds)) != len(kinds):
            raise ValueError("Each mission kind may be configured once.")
        return self


OFFICIAL_ONLY = (
    "Anyone can lie, forge documents or impersonate officials. Only "
    "announcements in the Steward's channel are real."
)
"""The line every rulebook must carry, word for word."""


def rulebook(
    config: GameConfig,
    *,
    players: int,
    traitors: int,
    castle_name: str,
) -> str:
    """The rules as every player reads them. Written from the config."""
    missions = "\n".join(
        f"  - {mission.title}: {mission.briefing}" for mission in config.missions
    )
    poison = (
        "or, at most "
        + ("once" if config.poisons == 1 else f"{config.poisons} times")
        + " a game, poison slipped into a dinner neighbour's glass: the victim "
        "dies the next morning even if they carry a shield"
        if config.poisons
        else "and never by poison"
    )
    return f"""THE RULES OF {castle_name.upper()}

You are one of {players} guests. {traitors} of you begin as traitors; the rest are
the Faithful. You know your own role, and the traitors know each other.

A day runs breakfast, mission, free time, round table, dinner, night. Each part
lasts a fixed number of ticks. In a tick you take one step of your plan.

- Breakfast: the Steward announces who died in the night, if anyone.
- Mission: work the day's station to add coins to the prize pot. The growth of the
  pot is announced exactly; each worker's share only as a rough tally.
{missions}
- The hardest worker of a mission is secretly given a shield, while the Steward
  has shields left. A shield you are carrying turns aside one murder, then
  crumbles. It is yours to keep, hide or lose.
- Round table: in turn, accuse, defend, question, answer, then vote. Whoever has
  the most votes is banished and their role revealed; a tie is settled by lot.
  You can only speak and vote if you are at the table.
- Dinner: the seats are drawn at random each night.
- Night: the household sleeps in the Chambers. The traitors meet in secret and
  make one attempt on a life: a murder, {poison}.
- When only one or two traitors remain, and fewer than began, they may offer one
  of the Faithful a place among them, face to face at night. Accept and you
  become a traitor. Refuse and you are that night's target; only a shield can
  save you.
- The endgame: when {config.endgame_at} or fewer remain, or on day
  {config.max_days}, after each banishment everyone at the table votes to end the
  game or to banish again. The game ends when every vote is to end, or when two
  remain.
- The Faithful win by banishing every traitor. The traitors win by surviving to
  the end. The winners still alive split the pot.
- When it is your turn to speak you may stay silent; give a private reason.

WHO TO BELIEVE
Everything you learn says how it reached you: your own senses, speech, a whisper,
a note, a book, a log, or the Steward. {OFFICIAL_ONLY} No player can write to the
Steward's channel."""


__all__ = [
    "MAX_PLAYERS",
    "MIN_PLAYERS",
    "OFFICIAL_ONLY",
    "GameConfig",
    "MissionKind",
    "MissionSpec",
    "Role",
    "rulebook",
]
