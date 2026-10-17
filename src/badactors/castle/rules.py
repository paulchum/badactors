"""The rules a game is played under, and the rulebook every player is handed.

:class:`GameConfig` is every number in the shipped ``balance.json`` plus the
missions on offer, so a stored game carries the exact rules it was played under.
Built without arguments it is the shipped balance; any field given overrides
the file, nested sections merging key by key, so a test can change one tool's
odds without restating the rest. The rulebook is written from the same config,
so what a player is told and what the referee enforces cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from badactors.castle.balance import Balance, ToolName, default_balance_data
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


class GameConfig(Balance):
    """Every number from the balance file, and the missions on offer."""

    missions: list[MissionSpec] = Field(default_factory=_default_missions)

    @classmethod
    def shipped(cls, **overrides: Any) -> GameConfig:
        """The shipped balance, with any fields given overriding it."""
        return cls.model_validate(overrides)

    @model_validator(mode="before")
    @classmethod
    def balance_from_the_file(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            return _merged(default_balance_data(), data)
        return data

    @model_validator(mode="after")
    def distinct_missions(self) -> GameConfig:
        kinds = [mission.kind for mission in self.missions]
        if not kinds:
            raise ValueError("A game needs at least one mission.")
        if len(set(kinds)) != len(kinds):
            raise ValueError("Each mission kind may be configured once.")
        return self

    def balance(self) -> Balance:
        """The numbers alone, as the world holds them."""
        return Balance.model_validate(self.model_dump(exclude={"missions"}))


def _merged(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _merged(out[key], value)
        else:
            out[key] = value
    return out


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
    dose = config.tools.poison
    poison = (
        "or poison slipped into a dinner neighbour's glass: the victim dies the "
        "next morning even if they carry a shield. Each traitor may poison "
        + ("once" if dose.per_game == 1 else f"{dose.per_game} times")
        + " a game, and never within "
        + f"{dose.cooldown_days} days of the last poisoning"
        if dose.per_game
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

{toolkit(config)}

WHO TO BELIEVE
Everything you learn says how it reached you: your own senses, speech, a whisper,
a note, a book, a log, or the Steward. {OFFICIAL_ONLY} No player can write to the
Steward's channel."""


def toolkit(config: GameConfig) -> str:
    """The tools, as the rulebook lists them: what each does and what it costs,
    from the same specs and numbers the referee enforces."""
    from badactors.castle.kit import FAITHFUL_TOOLS, SPECS, TRAITOR_TOOLS

    def line(name: ToolName) -> str:
        numbers = config.tools.of(name)
        limits = []
        if numbers.per_day is not None:
            limits.append(f"{numbers.per_day} a day")
        if numbers.per_game is not None:
            limits.append(f"{numbers.per_game} a game")
        limit = f" ({', '.join(limits)})" if limits else ""
        rule = SPECS[name].rule.format(**numbers.model_dump())
        return f"  - {name}: {rule}{limit}."

    household = "\n".join(line(name) for name in FAITHFUL_TOOLS)
    traitor = "\n".join(line(name) for name in TRAITOR_TOOLS)
    return f"""TOOLS
Everyone carries the household's tools. Most are a step in your plan; demand an
alibi, cross-examine and report are your turn at the round table. Nobody can
read, write or compare hands in the dark.
{household}
The traitors also carry these. Poison is chosen at dinner and recruitment at
night; filibuster is a turn at the round table; the rest are plan steps.
{traitor}"""


__all__ = [
    "MAX_PLAYERS",
    "MIN_PLAYERS",
    "OFFICIAL_ONLY",
    "GameConfig",
    "MissionKind",
    "MissionSpec",
    "Role",
    "rulebook",
    "toolkit",
]
