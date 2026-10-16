"""Who sits where, on which side, in every benchmark game (SPEC §7).

The benchmark plays fixed seeds, and every entrant — a model, or a reference
bot profile in a dry run — must play both traitor and Faithful in the same
scenario. A seed fixes the scenario: the castle, the characters, and which seats
the Steward draws as traitors, since the role draw reads only the seed and the
seats, never who plays them. So the schedule moves the entrants, not the roles.

For each seed, the seats are put in a fixed order, the drawn traitor seats first,
and the cast's entrants are laid along that order and turned one place per game:
a Latin square of entrants over seats. Every entrant sits in every seat once, so
each one plays the traitor seats as often as its share of the cast allows, and
every ordered pair of entrants meets as traitor and Faithful somewhere. The
compact rotation turns by the traitor count instead, so each entrant takes a
traitor seat once a seed in far fewer games; it can leave pairs uncovered on a
few seeds, which :func:`check` reports.

Before turning, the entrants are dealt into the seats by a shuffle drawn from the
seed, so the same two models do not always sit side by side, and when a cast has
more seats than entrants the extra seats go round from seed to seed.

The arms of the Corruption Test (SPEC §7) are kinds of schedule:

* ``single`` — every seat one model, one castle per model;
* ``mixed`` — the rated mixed cast;
* ``invader`` — one seat of model A among seats of model B, and the reverse;
* ``names_visible`` — the mixed cast with each character's model on the roster.
  Its games are flagged, labeled and never rated.

:func:`check` asserts coverage: every entrant in both roles on every seed, and
every ordered pair of distinct entrants in an arm met as attacker and target.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from badactors.castle.cast import Cast
from badactors.castle.contracts import CastleMap, Contract
from badactors.castle.play import start_game
from badactors.castle.rng import draw
from badactors.castle.rules import GameConfig

ArmKind = Literal["single", "mixed", "invader", "names_visible"]
Rotation = Literal["latin", "compact"]
Tier = Literal["frontier", "open"]


class ScheduleError(ValueError):
    """A schedule that does not give every entrant both roles and every pair a
    meeting, or that is not consistent with itself."""


class Seating(Contract):
    """One scheduled game: who plays each seat, and which seats the seed makes
    traitors."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")
    arm: ArmKind
    group: str = ""
    """Which castle of its arm this is: the model of a single-model castle, or
    ``a-in-b`` for an invader."""
    seed: int
    rotation: int = Field(ge=0)
    seats: dict[str, str]
    """Player id to entrant, in seat order."""
    traitors: list[str]
    """The seats the seed draws as traitors."""
    names_visible: bool = False

    @model_validator(mode="after")
    def consistent(self) -> Seating:
        if self.names_visible != (self.arm == "names_visible"):
            raise ValueError("Only the names-visible arm shows models on the roster.")
        if not set(self.traitors) <= set(self.seats):
            raise ValueError("A traitor seat must be one of the game's seats.")
        return self

    @property
    def rated(self) -> bool:
        """A names-visible game is labeled and never rated (SPEC §7)."""
        return not self.names_visible

    @property
    def world_id(self) -> str:
        return f"bench-{self.id}"

    def role_of(self, seat: str) -> Literal["traitor", "faithful"]:
        return "traitor" if seat in self.traitors else "faithful"

    def cast(self, base: Cast) -> Cast:
        """The base cast's characters, each played by the entrant this game
        seats there."""
        if [member.id for member in base.players] != list(self.seats):
            raise ScheduleError(f"Game {self.id} seats another cast.")
        return Cast(
            players=[
                member.model_copy(update={"model": self.seats[member.id]})
                for member in base.players
            ]
        )


class Schedule(Contract):
    """Every game of a benchmark run, in the order to play them."""

    players: int
    traitors: int
    rotation: Rotation
    games: list[Seating]
    tiers: dict[str, Tier] = Field(default_factory=dict)
    """Which entrants are frontier models and which smaller open-weight ones; a
    mixed arm must hold both when given."""

    def arm(self, kind: ArmKind) -> list[Seating]:
        return [game for game in self.games if game.arm == kind]

    def entrants(self) -> list[str]:
        return sorted(
            {entrant for game in self.games for entrant in game.seats.values()}
        )


# ------------------------------------------------------------------ building


def traitor_seats(
    castle: CastleMap, cast: Cast, *, seed: int, config: GameConfig
) -> list[str]:
    """The seats the Steward draws as traitors on this seed. It reads only the
    seed and the seats, so it holds whoever plays them."""
    return start_game(castle, cast, seed=seed, config=config).starting_traitors


def deal(entrants: Sequence[str], seats: int, *, seed: int) -> list[str]:
    """Lay ``entrants`` over ``seats`` places: round the list, starting where
    the seed says so the extra seats move from seed to seed, then shuffled by
    rolls drawn from the seed."""
    if not entrants:
        raise ScheduleError("A game needs at least one entrant.")
    if len(entrants) > seats:
        raise ScheduleError(
            f"{len(entrants)} entrants cannot all sit at {seats} seats."
        )
    start = seed % len(entrants)
    dealt = [entrants[(start + index) % len(entrants)] for index in range(seats)]
    for index in range(seats - 1, 0, -1):
        pick = int(draw(seed, index, "schedule:deal").value * (index + 1))
        dealt[index], dealt[pick] = dealt[pick], dealt[index]
    return dealt


def rotations(
    dealt: Sequence[str],
    order: Sequence[str],
    *,
    traitors: int,
    rotation: Rotation = "latin",
) -> list[tuple[int, dict[str, str]]]:
    """Every distinct seating of one seed, as (rotation, seat to entrant).

    ``order`` is the seats, traitor seats first. Rotation ``r`` puts entrant
    ``dealt[(k + r * step) % n]`` in the ``k``-th seat of that order: a step of
    one for the Latin square, of the traitor count for the compact set. Two
    rotations that seat everyone alike are one game.
    """
    n = len(order)
    step = 1 if rotation == "latin" else traitors
    count = n if rotation == "latin" else -(-n // traitors)
    seen: set[tuple[str, ...]] = set()
    out: list[tuple[int, dict[str, str]]] = []
    for r in range(count):
        placed = {order[k]: dealt[(k + r * step) % n] for k in range(n)}
        key = tuple(placed[seat] for seat in sorted(placed))
        if key in seen:
            continue
        seen.add(key)
        out.append((r, placed))
    return out


@dataclass(frozen=True)
class Scenario:
    """What every game of a schedule shares, apart from its seed."""

    castle: CastleMap
    cast: Cast
    config: GameConfig

    def order(self, seed: int) -> tuple[list[str], list[str]]:
        """This seed's traitor seats, and every seat with those first."""
        seats = [member.id for member in self.cast.players]
        traitors = traitor_seats(self.castle, self.cast, seed=seed, config=self.config)
        return traitors, [*traitors, *(seat for seat in seats if seat not in traitors)]


def arm_games(
    scenario: Scenario,
    kind: ArmKind,
    entrants: Sequence[str],
    *,
    seeds: Iterable[int],
    rotation: Rotation = "latin",
    group: str = "",
) -> list[Seating]:
    """One arm's games over ``seeds``: the entrants dealt and turned on each."""
    out: list[Seating] = []
    tag = f"-{group}" if group else ""
    for seed in seeds:
        traitors, order = scenario.order(seed)
        dealt = deal(entrants, len(order), seed=seed)
        seat_order = [member.id for member in scenario.cast.players]
        for r, placed in rotations(
            dealt, order, traitors=len(traitors), rotation=rotation
        ):
            out.append(
                Seating(
                    id=f"{kind.replace('_', '-')}{tag}-s{seed}-r{r}",
                    arm=kind,
                    group=group,
                    seed=seed,
                    rotation=r,
                    seats={seat: placed[seat] for seat in seat_order},
                    traitors=traitors,
                    names_visible=kind == "names_visible",
                )
            )
    return out


def _slug(entrant: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in entrant.casefold())
    return "-".join(part for part in cleaned.split("-") if part)[:30]


def single_arm(
    scenario: Scenario, models: Sequence[str], *, seeds: Sequence[int]
) -> list[Seating]:
    """Every seat one model: one castle per model per seed."""
    return [
        game
        for model in models
        for game in arm_games(
            scenario, "single", [model], seeds=seeds, group=_slug(model)
        )
    ]


def invader_arm(
    scenario: Scenario,
    a: str,
    b: str,
    *,
    seeds: Sequence[int],
    rotation: Rotation = "latin",
) -> list[Seating]:
    """One seat of ``a`` among seats of ``b``, and the reverse; the invader
    goes round the seats, so it plays both sides."""
    n = len(scenario.cast.players)
    return [
        *arm_games(
            scenario,
            "invader",
            [a, *[b] * (n - 1)],
            seeds=seeds,
            rotation=rotation,
            group=f"{_slug(a)}-in-{_slug(b)}",
        ),
        *arm_games(
            scenario,
            "invader",
            [b, *[a] * (n - 1)],
            seeds=seeds,
            rotation=rotation,
            group=f"{_slug(b)}-in-{_slug(a)}",
        ),
    ]


def mixed_schedule(
    scenario: Scenario,
    models: Sequence[str],
    *,
    seeds: Sequence[int],
    rotation: Rotation = "latin",
    tiers: Mapping[str, Tier] | None = None,
    names_visible: bool = False,
) -> Schedule:
    """The rated mixed cast over ``seeds``, and its names-visible twin if
    asked for."""
    games = arm_games(scenario, "mixed", models, seeds=seeds, rotation=rotation)
    if names_visible:
        games += arm_games(
            scenario, "names_visible", models, seeds=seeds, rotation=rotation
        )
    return Schedule(
        players=len(scenario.cast.players),
        traitors=scenario.config.traitors,
        rotation=rotation,
        games=games,
        tiers=dict(tiers or {}),
    )


def corruption_schedule(
    scenario: Scenario,
    mixed: Sequence[str],
    a: str,
    b: str,
    *,
    seeds: Sequence[int],
    rotation: Rotation = "latin",
    tiers: Mapping[str, Tier] | None = None,
) -> Schedule:
    """The four arms of the Corruption Test over the same seeds: ``a`` and
    ``b`` alone, each invading the other, and the mixed cast with names hidden
    and shown. The pilot is two model families (SPEC §7)."""
    games = [
        *single_arm(scenario, [a, b], seeds=seeds),
        *arm_games(scenario, "mixed", mixed, seeds=seeds, rotation=rotation),
        *invader_arm(scenario, a, b, seeds=seeds, rotation=rotation),
        *arm_games(scenario, "names_visible", mixed, seeds=seeds, rotation=rotation),
    ]
    return Schedule(
        players=len(scenario.cast.players),
        traitors=scenario.config.traitors,
        rotation=rotation,
        games=games,
        tiers=dict(tiers or {}),
    )


# ------------------------------------------------------------------ coverage


@dataclass(frozen=True)
class ArmCoverage:
    arm: ArmKind
    games: int
    seeds: int
    entrants: int
    pairs: int
    """Ordered pairs of distinct entrants met as traitor and Faithful."""
    pairs_wanted: int


def check(schedule: Schedule) -> list[ArmCoverage]:
    """Assert the schedule covers what the benchmark needs, or raise
    :class:`ScheduleError` naming every gap.

    * every game seats the same players, with the configured traitor count;
    * every entrant of an arm plays a traitor seat and a Faithful seat on every
      seed it plays;
    * every ordered pair of distinct entrants of an arm (the castles of a
      single-model arm apart) is met somewhere as attacker and target: the
      first in a traitor seat while the second sits in a Faithful one;
    * a mixed arm holds both frontier and open-weight entrants, when the
      schedule says which are which.
    """
    problems: list[str] = []
    seat_sets = {tuple(game.seats) for game in schedule.games}
    if len(seat_sets) > 1:
        problems.append("Games seat different casts.")
    ids = [game.id for game in schedule.games]
    if len(set(ids)) != len(ids):
        problems.append("Two games share an id.")
    for game in schedule.games:
        if len(game.seats) != schedule.players:
            problems.append(
                f"{game.id} seats {len(game.seats)}, not {schedule.players}."
            )
        if len(game.traitors) != schedule.traitors:
            problems.append(
                f"{game.id} has {len(game.traitors)} traitor seats, not "
                f"{schedule.traitors}."
            )
    out: list[ArmCoverage] = []
    by_arm: dict[ArmKind, list[Seating]] = defaultdict(list)
    for game in schedule.games:
        by_arm[game.arm].append(game)
    for kind, games in by_arm.items():
        roles: dict[tuple[str, int, str], set[str]] = defaultdict(set)
        met: dict[str, set[tuple[str, str]]] = defaultdict(set)
        entrants: dict[str, set[str]] = defaultdict(set)
        for game in games:
            for seat, entrant in game.seats.items():
                roles[(game.group, game.seed, entrant)].add(game.role_of(seat))
                entrants[game.group].add(entrant)
            for t in game.traitors:
                for seat, entrant in game.seats.items():
                    if seat not in game.traitors:
                        met[game.group].add((game.seats[t], entrant))
        for (group, seed, entrant), sides in sorted(roles.items()):
            if sides != {"traitor", "faithful"}:
                where = f"{kind}{f' {group}' if group else ''}"
                problems.append(
                    f"{entrant} plays only {''.join(sides)} on seed {seed} ({where})."
                )
        wanted = covered = 0
        for group, names in entrants.items():
            pairs = {(a, b) for a in names for b in names if a != b}
            wanted += len(pairs)
            covered += len(pairs & met[group])
            for a, b in sorted(pairs - met[group]):
                problems.append(f"{a} never attacks {b} in the {kind} arm.")
        if kind == "mixed" and schedule.tiers:
            held = {
                schedule.tiers.get(name)
                for names in entrants.values()
                for name in names
            }
            if not {"frontier", "open"} <= held:
                problems.append(
                    "The mixed arm needs frontier and open-weight models both."
                )
        out.append(
            ArmCoverage(
                arm=kind,
                games=len(games),
                seeds=len({game.seed for game in games}),
                entrants=len(set().union(*entrants.values())),
                pairs=covered,
                pairs_wanted=wanted,
            )
        )
    if problems:
        raise ScheduleError("\n".join(problems))
    return out


def save_schedule(schedule: Schedule, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(schedule.model_dump_json(indent=1))


def load_schedule(path: str | Path) -> Schedule:
    return Schedule.model_validate_json(Path(path).read_text())


__all__ = [
    "ArmCoverage",
    "ArmKind",
    "Rotation",
    "Scenario",
    "Schedule",
    "ScheduleError",
    "Seating",
    "Tier",
    "arm_games",
    "check",
    "corruption_schedule",
    "deal",
    "invader_arm",
    "load_schedule",
    "mixed_schedule",
    "rotations",
    "save_schedule",
    "single_arm",
    "traitor_seats",
]
