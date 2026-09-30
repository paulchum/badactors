"""Set-ups shared by the Castle game tests.

A scene is a real game played by the seeded bot, run to the point a test cares
about. Where a test needs control, a script answers first and the bot plays every
decision the script leaves alone; scripts read the true game state, which is
the one liberty tests take that a player never can.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from badactors.castle.bots import HeuristicBot
from badactors.castle.cast import load_cast_file
from badactors.castle.contracts import CastlePhase, LedgerEntry, WitnessRecord
from badactors.castle.decisions import Decision, Pass, Recruit, Vote
from badactors.castle.game import GameState, fork_game, shield_held_by
from badactors.castle.mapfile import load_map
from badactors.castle.observe import GameObservation
from badactors.castle.play import Referee, start_game
from badactors.castle.rules import GameConfig

ROOT = Path(__file__).resolve().parent.parent
CASTLE = load_map(ROOT / "configs/castle/map.json")
CAST = load_cast_file(ROOT / "configs/castle/cast.json")
MODELS = sorted(set(CAST.models().values()))

type Script = Callable[[GameObservation, GameState], Decision | None]


class Pause(Exception):
    """Stops a game at the decision a test wants to look at."""

    def __init__(self, observation: GameObservation) -> None:
        super().__init__(observation.request.kind)
        self.observation = observation


def referee(
    *,
    seed: int = 13,
    players: int = 10,
    script: Script | None = None,
    **config: Any,
) -> Referee:
    """A referee for a fresh game, every seat played by the bot unless the
    script answers first."""
    cast = CAST.first(players)
    game = start_game(CASTLE, cast, seed=seed, config=GameConfig(**config))
    ref = Referee(fork_game(game), {}, models=tuple(MODELS))
    bot = HeuristicBot(seed=seed)

    def player(observation: GameObservation) -> Decision:
        if script is not None:
            decision = script(observation, ref.game)
            if decision is not None:
                return decision
        return bot(observation)

    ref.players = {pid: player for pid in game.world.seats}
    return ref


def run_until(ref: Referee, day: int, phase: CastlePhase) -> GameState:
    """Play whole phases until ``phase`` of ``day`` has just opened."""
    while ref.game.status == "playing" and (
        ref.game.world.day,
        ref.game.world.phase,
    ) != (day, phase):
        ref.play_phase()
    return ref.game


def pause_at(
    when: Callable[[GameObservation, GameState], bool],
    *,
    script: Script | None = None,
    **options: Any,
) -> tuple[GameState, GameObservation]:
    """Play until ``when`` holds for a decision about to be asked, and return the
    game at that moment with the (already leak-checked) observation."""

    def stop(observation: GameObservation, game: GameState) -> Decision | None:
        if when(observation, game):
            raise Pause(observation)
        return script(observation, game) if script is not None else None

    ref = referee(script=stop, **options)
    try:
        ref.run()
    except Pause as paused:
        return ref.game, paused.observation
    raise AssertionError("The game ended before the moment the test waited for.")


def recruiting(answer: Decision) -> Script:
    """Day one's table votes out the first traitor; that night the other offers
    a place to one of the Faithful without a shield, who answers ``answer``."""

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        kind = observation.request.kind
        options = observation.request.options
        if kind == "vote" and game.world.day == 1:
            first = game.starting_traitors[0]
            return Vote(target=first if first in options else options[0])
        if kind == "poison":
            return Pass()
        if kind == "recruit":
            unshielded = [pid for pid in options if shield_held_by(game, pid) is None]
            return Recruit(target=(unshielded or options)[0], text="Join us.")
        if kind == "recruit_answer":
            return answer
        return None

    return script


def of_type(game: GameState, *types: str) -> list[LedgerEntry]:
    return [entry for entry in game.world.ledger if entry.type in types]


def plant_record(
    game: GameState, entry: LedgerEntry, viewer: str, **changes: Any
) -> LedgerEntry:
    """Simulate a referee bug: give ``viewer`` a record of ``entry`` they should
    never have had. The record copies an existing one (or states the entry's own
    text), so it looks as legitimate as the bug that would write it."""
    if entry.witnesses:
        template = entry.witnesses[-1]
    else:
        template = WitnessRecord(
            player_id=viewer,
            sense="saw",
            confidence="clear",
            channel="direct",
            text=entry.text,
        )
    record = template.model_copy(update={"player_id": viewer, **changes})
    planted = entry.model_copy(update={"witnesses": [*entry.witnesses, record]})
    game.world.ledger[entry.seq - 1] = planted
    return planted


def rewrite_record(
    game: GameState, entry: LedgerEntry, viewer: str, **changes: Any
) -> LedgerEntry:
    """Simulate a referee bug that wrote ``viewer`` the wrong record of
    ``entry``."""
    rewritten = entry.model_copy(
        update={
            "witnesses": [
                record.model_copy(update=changes)
                if record.player_id == viewer
                else record
                for record in entry.witnesses
            ]
        }
    )
    game.world.ledger[entry.seq - 1] = rewritten
    return rewritten


__all__ = [
    "CAST",
    "CASTLE",
    "MODELS",
    "Pause",
    "Script",
    "of_type",
    "pause_at",
    "plant_record",
    "recruiting",
    "referee",
    "rewrite_record",
    "run_until",
]
