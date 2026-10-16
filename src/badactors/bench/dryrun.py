"""Playing a schedule with reference bots: the benchmark's dry run.

Every entrant of a dry run is a **reference bot profile**: the seeded heuristic
bot under a name of its own, with a fixed susceptibility to manipulation. The
levels are evenly spaced and dealt to the profiles by rolls drawn from the run's
seed, so they differ, are known, and the steer/resist fit can be checked
against them. The record says which profile played each seat
(``bot:heuristic/<name>``), so its games are rated on the reference board, never
the model one. The cast keeps the would-be models, as ``castle-game`` does: who
would play, not who did.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from badactors.bench.leaderboard import Finished
from badactors.castle.bots import HeuristicBot
from badactors.castle.decisions import Decision, ExitAnswer
from badactors.castle.interview import ExitObservation
from badactors.castle.observe import GameObservation
from badactors.castle.play import record, run_game, start_game
from badactors.castle.rng import draw
from badactors.castle.schedule import Scenario, Seating
from badactors.castle.verify import make_bundle, save_bundle

LOW, HIGH = 0.1, 0.8
"""The span of the profiles' susceptibilities, as the heuristic bot draws its
own."""


@dataclass(frozen=True)
class ReferenceBot:
    """The seeded heuristic bot under a profile name, with a fixed
    susceptibility."""

    name: str
    seed: int
    susceptibility: float

    @property
    def agent(self) -> str:
        return f"bot:heuristic/{self.name}"

    @property
    def bot(self) -> HeuristicBot:
        return HeuristicBot(seed=self.seed, susceptibility=self.susceptibility)

    def __call__(self, observation: GameObservation) -> Decision:
        return self.bot(observation)

    def interview(self, departing: ExitObservation) -> ExitAnswer:
        return self.bot.interview(departing)


def profiles(count: int, *, seed: int) -> dict[str, float]:
    """``count`` profile names and their susceptibilities: evenly spaced from
    ``LOW`` to ``HIGH``, dealt out by rolls drawn from ``seed``."""
    if count < 2:
        raise ValueError("A dry run needs at least two profiles.")
    levels = [LOW + (HIGH - LOW) * i / (count - 1) for i in range(count)]
    for index in range(count - 1, 0, -1):
        pick = int(draw(seed, index, "bench:profiles").value * (index + 1))
        levels[index], levels[pick] = levels[pick], levels[index]
    return {f"p{i}": round(level, 4) for i, level in enumerate(levels)}


def play_seating(
    seating: Seating,
    scenario: Scenario,
    susceptibility: Mapping[str, float],
    *,
    check_leaks: bool = False,
    replays: Path | None = None,
) -> Finished:
    """Play one scheduled game with each seat's reference bot, and write its
    verify bundle into ``replays`` if given."""
    game = start_game(
        scenario.castle,
        scenario.cast,
        seed=seating.seed,
        config=scenario.config,
        world_id=seating.world_id,
    )
    players = {
        seat: ReferenceBot(entrant, seating.seed, susceptibility[entrant])
        for seat, entrant in seating.seats.items()
    }
    played = run_game(
        game,
        players,
        check_leaks=check_leaks,
        models=sorted(set(scenario.cast.models().values())),
    )
    stored = record(scenario.castle, scenario.cast, played)
    if replays is not None:
        save_bundle(make_bundle(stored), replays / f"{seating.id}.json.gz")
    return Finished(id=seating.id, record=stored, game=played.game, arm=seating.arm)


def play_schedule(
    games: Iterable[Seating],
    scenario: Scenario,
    susceptibility: Mapping[str, float],
    *,
    check_leaks: bool = False,
    pool: bool = True,
    replays: Path | None = None,
) -> list[Finished]:
    """Play every game, in a process pool unless told not to."""
    play = partial(
        play_seating,
        scenario=scenario,
        susceptibility=dict(susceptibility),
        check_leaks=check_leaks,
        replays=replays,
    )
    listed: Sequence[Seating] = list(games)
    if not pool:
        return [play(seating) for seating in listed]
    with ProcessPoolExecutor() as executor:
        return list(executor.map(play, listed, chunksize=4))


__all__ = ["ReferenceBot", "play_schedule", "play_seating", "profiles"]
