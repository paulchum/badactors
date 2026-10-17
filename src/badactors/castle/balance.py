"""Every number the game is balanced by, loaded from one validated file.

``balance.json``, shipped in :mod:`badactors.castle.configs`, holds them all: the
size of the game, the odds of recognising someone in dim light, what a mission
pays, and for every tool its chance of working, its chance of leaving a trace,
and what it costs. Nothing in the code carries a balance number of its own, so a
balance pass edits one file and a stored game carries the exact numbers it was
played under.

The file is validated strictly when it loads: every key must be present, no key
may be unknown, and every probability must lie between nought and one. A number
the file forgets is an error, never a silent default.

This module depends on nothing else in the castle but where the file is, because
the world holds its numbers too: :class:`Balance` sits in the world state as well
as in the game's config. The world's copy is made from the config when a game
starts, and once play begins every tool, the game's own included, reads its
numbers from the world, so there is one source of them at a time.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from badactors.castle.configs import BALANCE

DEFAULT_PATH = BALANCE

Probability = Annotated[float, Field(ge=0.0, le=1.0)]
Count = Annotated[int, Field(ge=0, le=1000)]


class Numbers(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# ------------------------------------------------------------------------ tools

ToolName = Literal[
    # The Faithful toolkit, open to every player.
    "search",
    "inspect",
    "read",
    "read_log",
    "write",
    "relay",
    "compare_hands",
    "check_register",
    "inspect_seal",
    "search_person",
    "demand_alibi",
    "cross_examine",
    "report",
    # The traitor toolkit.
    "forge",
    "plant",
    "tamper",
    "poison",
    "rumor",
    "sabotage",
    "steal",
    "recruit",
    "blackmail",
    "disguise",
    "clean_up",
    # Manipulation tools: aimed at the other players' models.
    "counterfeit",
    "hook",
    "honeypot",
    "gaslight",
    "filibuster",
]


class ToolNumbers(Numbers):
    """What every tool has. What ``success`` and ``trace`` mean for a given tool
    is written beside its spec in ``kit``; a certainty (0 or 1) is never rolled."""

    success: Probability
    trace: Probability
    per_day: Count | None = None
    """Uses a player may make in one day. Absent means no limit."""
    per_game: Count | None = None
    """Uses a player may make in the whole game. Absent means no limit."""


class InspectNumbers(ToolNumbers):
    links: Annotated[int, Field(ge=1, le=50)]
    """How many of an object's most recent custody links an inspection shows."""


class ReadNumbers(ToolNumbers):
    secret: Probability
    """Chance a resident's letters give their secret away to a reader."""


class ReadLogNumbers(ToolNumbers):
    lines: Annotated[int, Field(ge=1, le=200)]
    """The most lines one reading of a log takes in."""


class PoisonNumbers(ToolNumbers):
    seen: Probability
    """Chance that each diner beside the poisoner, other than the victim, notices
    the reach toward the victim's glass."""
    cooldown_days: Annotated[int, Field(ge=0, le=30)]
    """Days that must pass after a poisoning before the traitors may pour again."""


class SabotageNumbers(ToolNumbers):
    coins: Annotated[int, Field(ge=0, le=100)]
    """Coins one sabotaged turn costs the pot."""


class FilibusterNumbers(ToolNumbers):
    chars: Annotated[int, Field(ge=1, le=2000)]
    """The longest a filibuster speech may run, beyond the ordinary message cap."""
    floor: Annotated[int, Field(ge=1, le=20)]
    """Speaking turns of the current stage one filibuster uses up."""
    forfeit: Annotated[int, Field(ge=0, le=20)]
    """The filibusterer's own later speaking turns it costs."""


class ToolBalance(Numbers):
    search: ToolNumbers
    inspect: InspectNumbers
    read: ReadNumbers
    read_log: ReadLogNumbers
    write: ToolNumbers
    relay: ToolNumbers
    compare_hands: ToolNumbers
    check_register: ToolNumbers
    inspect_seal: ToolNumbers
    search_person: ToolNumbers
    demand_alibi: ToolNumbers
    cross_examine: ToolNumbers
    report: ToolNumbers
    forge: ToolNumbers
    plant: ToolNumbers
    tamper: ToolNumbers
    poison: PoisonNumbers
    rumor: ToolNumbers
    sabotage: SabotageNumbers
    steal: ToolNumbers
    recruit: ToolNumbers
    blackmail: ToolNumbers
    disguise: ToolNumbers
    clean_up: ToolNumbers
    counterfeit: ToolNumbers
    hook: ToolNumbers
    honeypot: ToolNumbers
    gaslight: ToolNumbers
    filibuster: FilibusterNumbers

    def of(self, tool: ToolName) -> ToolNumbers:
        numbers = getattr(self, tool)
        assert isinstance(numbers, ToolNumbers)
        return numbers


# --------------------------------------------------------------------- the rest


class MissionYields(Numbers):
    cipher_odds: Probability
    """Chance one turn at the cipher dials cracks a ring."""
    cipher_prize: Count
    """Coins a cracked ring adds to the pot."""
    carry_load: Count
    """Coins one load of the supply crates adds."""
    assemble_solo: Count
    """Coins one turn at the clock case adds."""
    assemble_team: Count
    """Coins one turn at the clock case adds while a team works it together."""
    team_size: Annotated[int, Field(ge=1, le=24)]
    """Workers at the clock case in the same tick for each of them to earn the
    team rate."""


class Balance(Numbers):
    """Every number of the game, as ``balance.json`` states it."""

    max_days: Annotated[int, Field(ge=2, le=30)]
    traitors: Annotated[int, Field(ge=2, le=3)]
    """Two by default at every size; three is allowed."""
    shields: Annotated[int, Field(ge=0, le=8)]
    """Shield tokens the Steward holds for the whole game."""
    endgame_at: Annotated[int, Field(ge=2, le=24)]
    """At this many living players the round table becomes the final one."""
    tally_noise: Annotated[int, Field(ge=0, le=10)]
    """Each worker's reported contribution is off by up to this many coins."""
    recent_limit: Annotated[int, Field(ge=1, le=200)]
    """How many of a player's most recent records reach a prompt."""
    notebook_limit: Annotated[int, Field(ge=1, le=200)]
    """How many of a player's noted records (findings, words received in private,
    testimony) reach a prompt, however long ago they were made."""
    diary_limit: Annotated[int, Field(ge=0, le=100)]
    """How many of a player's own earlier private lines — diary lines, and their
    reasons for silence or for letting a chance pass — reach their prompt."""
    dim_recognition: Probability
    """Chance a witness in a dim room pins the identity rather than a garment."""
    paper: Annotated[int, Field(ge=0, le=60)]
    """Sheets of notepaper in the castle's paper store."""
    yields: MissionYields
    tools: ToolBalance


def load_balance(path: str | Path) -> Balance:
    """Read and validate a balance file. Every key is required."""
    raw = json.loads(Path(path).read_text())
    return Balance.model_validate(raw)


@lru_cache(maxsize=1)
def default_balance() -> Balance:
    """The shipped balance, read once."""
    return load_balance(DEFAULT_PATH)


def default_balance_data() -> dict[str, object]:
    """The shipped balance as plain data, for merging overrides into."""
    return default_balance().model_dump(mode="python", exclude_none=True)


__all__ = [
    "DEFAULT_PATH",
    "Balance",
    "FilibusterNumbers",
    "InspectNumbers",
    "MissionYields",
    "PoisonNumbers",
    "Probability",
    "ReadLogNumbers",
    "ReadNumbers",
    "SabotageNumbers",
    "ToolBalance",
    "ToolName",
    "ToolNumbers",
    "default_balance",
    "default_balance_data",
    "load_balance",
]
