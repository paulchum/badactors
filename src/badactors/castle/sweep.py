"""``castle-sweep``: play many seeded mock games and report how the tools fared.

Every seat is the seeded heuristic bot, so there are no model calls. The sweep is
the 200-seed one the tests run: eight, nine and ten players in turn, and every
fifth game with three traitors. Every observation passes the leak checker before
a bot sees it, unless ``--no-leak-check`` is given.

    uv run castle-sweep
    uv run castle-sweep --seeds 50

The report reads the finished ledgers, as the audience can:

* **usage** — how often each tool was used, per game;
* **traces** — for every roll a tool draws, how often it came up: a forgery that
  betrays its writer, residue left in a glass, a flag left on an edited log;
  beside each, the chance the game was configured with;
* **discovery** — how often someone found the trace: a comparison that named a
  forger, a register check that caught a decree, residue found in a glass;
* **manipulation** — for every attempt that declared an intent, how often it
  worked, was reported by its target, or backfired on the attacker;
* **wins** — by side, with two traitors and with three;
* **balance** — the targets of SPEC §9 that a bot sweep can check, each beside
  the numbers it reads: the side split per traitor count, the share of traitor
  wins that involved a manipulation that worked, and how often a failed attempt
  exposed its attacker, against the quiet baseline. The two it cannot check
  (evidence-based banishments need the claims of §5; cost needs models) say so.

``--json`` prints the same report as JSON.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Any

from badactors.castle.bots import HeuristicBot
from badactors.castle.cast import Cast, load_cast_file
from badactors.castle.contracts import LedgerEntry
from badactors.castle.game import GameState
from badactors.castle.kit import FAITHFUL_TOOLS, SPECS, TRAITOR_TOOLS
from badactors.castle.manipulation import Outcome, outcomes
from badactors.castle.mapfile import load_map
from badactors.castle.play import Played, run_game, start_game
from badactors.castle.rules import GameConfig

_CONFIGS = Path(__file__).resolve().parents[3] / "configs" / "castle"


@dataclass
class Swept:
    seed: int
    cast: Cast
    played: Played

    @property
    def game(self) -> GameState:
        return self.played.game


def config_for(seed: int) -> GameConfig:
    """Every fifth game of a sweep has three traitors."""
    return GameConfig.shipped(traitors=3) if seed % 5 == 4 else GameConfig.shipped()


def play_seed(
    seed: int,
    *,
    check_leaks: bool = True,
    cast_path: str | Path = _CONFIGS / "cast.json",
    map_path: str | Path = _CONFIGS / "map.json",
) -> Swept:
    """One sweep game: the seed picks the cast size and the traitor count."""
    full = load_cast_file(cast_path)
    cast = full.first(8 + seed % 3)
    castle = load_map(map_path)
    game = start_game(castle, cast, seed=seed, config=config_for(seed))
    bot = HeuristicBot(seed=seed)
    played = run_game(
        game,
        {pid: bot for pid in game.world.seats},
        check_leaks=check_leaks,
        models=sorted(set(full.models().values())),
    )
    return Swept(seed=seed, cast=cast, played=played)


def _checked(seed: int) -> Swept:
    return play_seed(seed)


def _unchecked(seed: int) -> Swept:
    return play_seed(seed, check_leaks=False)


def sweep(seeds: Iterable[int], *, check_leaks: bool = True) -> list[Swept]:
    """Play every seed, in a process pool."""
    with ProcessPoolExecutor() as pool:
        return list(
            pool.map(_checked if check_leaks else _unchecked, list(seeds), chunksize=5)
        )


# ----------------------------------------------------------------- the numbers


@dataclass(frozen=True)
class Rate:
    hits: int
    of: int

    def __str__(self) -> str:
        if not self.of:
            return "   —"
        return f"{100 * self.hits / self.of:3.0f}% ({self.hits}/{self.of})"

    @property
    def share(self) -> float | None:
        return self.hits / self.of if self.of else None

    def as_json(self) -> dict[str, Any]:
        share = self.share
        return {
            "hits": self.hits,
            "of": self.of,
            "rate": None if share is None else round(share, 4),
        }


def usage(games: Sequence[GameState]) -> Counter[str]:
    """Uses of each tool, counted by the one entry each use writes."""
    counts: Counter[str] = Counter()
    for game in games:
        counts.update(
            entry.payload["tool"]
            for entry in game.world.ledger
            if "tool" in entry.payload and entry.actor_id is not None
        )
    return counts


def rolls(games: Sequence[GameState]) -> dict[str, Rate]:
    """How often each kind of tool roll came up, by its purpose: ``forge:trace``,
    ``steal:success``, ``disguise`` for a dim witness fooled, and so on."""
    hits: Counter[str] = Counter()
    seen: Counter[str] = Counter()
    for game in games:
        for entry in game.world.ledger:
            for roll in entry.rolls:
                key = _roll_key(roll.purpose)
                if key is None:
                    continue
                seen[key] += 1
                hits[key] += 1 if roll.success else 0
    return {key: Rate(hits[key], seen[key]) for key in sorted(seen)}


def _roll_key(purpose: str) -> str | None:
    head = purpose.split(":")
    if head[0] == "disguise":
        return "disguise:fooled"
    if head[0] in SPECS and len(head) > 1:
        return f"{head[0]}:{head[1]}"
    return None


def configured(games: Sequence[GameState]) -> dict[str, tuple[float, float]]:
    """The chance each kind of roll was configured with, from each game's own
    config, as (lowest, highest) across the games."""
    out: dict[str, tuple[float, float]] = {}
    for game in games:
        tools = game.config.tools
        for entry in game.world.ledger:
            for roll in entry.rolls:
                key = _roll_key(roll.purpose)
                if key is None:
                    continue
                tool, what = key.split(":")
                number = (
                    tools.disguise.success
                    if tool == "disguise"
                    else float(getattr(tools.of(tool), what))  # type: ignore[arg-type]
                )
                low, high = out.get(key, (number, number))
                out[key] = (min(low, number), max(high, number))
    return dict(sorted(out.items()))


def on_chance(rate: Rate, chance: tuple[float, float]) -> bool | None:
    """Whether a roll came up at its configured chance, within four standard
    errors; ``None`` with too few draws, or a chance that varied."""
    low, high = chance
    if rate.of < 30 or low != high:
        return None
    return abs(rate.hits / rate.of - low) <= 4 * sqrt(low * (1 - low) / rate.of)


def discovery(games: Sequence[GameState]) -> dict[str, Rate]:
    """How often a tool's trace was found by someone other than its user."""
    found: Counter[str] = Counter()
    of: Counter[str] = Counter()
    for game in games:
        ledger = game.world.ledger
        for entry in ledger:
            tool = entry.payload.get("tool")
            if tool is None or entry.actor_id is None:
                continue
            verdict = _discovered(game, ledger, entry, tool)
            if verdict is None:
                continue
            of[tool] += 1
            found[tool] += 1 if verdict else 0
    return {tool: Rate(found[tool], of[tool]) for tool in sorted(of)}


def _discovered(
    game: GameState, ledger: Sequence[LedgerEntry], entry: LedgerEntry, tool: str
) -> bool | None:
    later = [e for e in ledger[entry.seq :] if e.actor_id != entry.actor_id]
    thing = entry.payload.get("thing_id")
    if tool in ("forge", "counterfeit") and entry.type == "write":
        return any(
            (
                e.payload.get("tool") == "compare_hands"
                and e.payload.get("exposed") == "true"
            )
            or (
                e.payload.get("tool") == "check_register"
                and e.payload.get("listed") == "false"
            )
            or (
                e.payload.get("tool") == "inspect_seal"
                and e.payload.get("detected") == "true"
            )
            for e in later
            if e.payload.get("thing_id") == thing
        )
    if tool == "poison":
        if entry.payload.get("seen_by"):
            return True
        glass = entry.payload.get("glass")
        for e in later:
            if e.type == "washed":
                break
            if (
                e.type == "inspect"
                and e.payload.get("thing_id") == glass
                and "residue" in e.payload.get("marks", "")
            ):
                return True
        return False
    if tool == "tamper":
        day = entry.payload.get("day")
        return any(
            e.type == "read"
            and e.payload.get("thing_id") == thing
            and e.payload.get("day") == day
            and int(e.payload.get("flags", "0")) > 0
            for e in later
        )
    if tool == "clean_up":
        if entry.type == "destroyed":
            return any(
                e.type == "search"
                and e.room_id == entry.room_id
                and e.payload.get("ash") != "0"
                for e in later
            )
        return any(
            e.type == "inspect"
            and e.payload.get("thing_id") == thing
            and "rinsed" in e.payload.get("marks", "")
            for e in later
        )
    if tool == "sabotage":
        return any(record.sense == "saw" for record in entry.witnesses) and (
            entry.payload.get("fumble") == "true"
        )
    if tool == "plant":
        return entry.type == "plant_caught"
    if tool == "steal":
        if entry.type != "steal":
            return entry.payload.get("caught") == "true"
        return any(
            e.type == "missing" and e.payload.get("steal") == str(entry.seq)
            for e in later
        )
    if tool in ("rumor", "hook", "gaslight", "blackmail", "honeypot", "filibuster"):
        return any(
            e.type == "report" and e.payload.get("target") == entry.actor_id
            for e in later
        )
    return None


def manipulation(games: Sequence[GameState]) -> dict[str, dict[str, Rate]]:
    """Success, report and backfire rates for each tool that declared intents."""
    by_tool: dict[str, list[Outcome]] = defaultdict(list)
    for game in games:
        for outcome in outcomes(game):
            by_tool[outcome.tool].append(outcome)
    return {
        tool: {
            "success": Rate(sum(o.success for o in found), len(found)),
            "reported": Rate(sum(o.reported for o in found), len(found)),
            "backfire": Rate(sum(o.backfired for o in found), len(found)),
        }
        for tool, found in sorted(by_tool.items())
    }


def quiet_heat(games: Sequence[GameState]) -> Rate:
    """How often a traitor who used no intent-bearing tool on a day was accused,
    reported or banished at that day's round table: the base rate a backfire
    should be read against."""
    hit = total = 0
    for game in games:
        ledger = game.world.ledger
        tried = {(o.attacker, o.day) for o in outcomes(game)}
        tables = {entry.day for entry in ledger if entry.type == "table_opened"}
        for day in sorted(tables):
            at_table = (
                next(e for e in ledger if e.type == "table_opened" and e.day == day)
                .payload.get("present", "")
                .split(",")
            )
            for pid in at_table:
                if not pid or not _traitor_on(game, pid, day) or (pid, day) in tried:
                    continue
                total += 1
                hit += any(
                    e.day == day
                    and (
                        (
                            e.type in ("accuse", "report")
                            and e.payload.get("target") == pid
                        )
                        or (e.type == "banishment" and e.payload.get("target") == pid)
                    )
                    for e in ledger
                )
    return Rate(hit, total)


def _traitor_on(game: GameState, player_id: str, day: int) -> bool:
    since = game.traitor_since.get(player_id)
    return since is not None and game.world.ledger[since - 1].day <= day


def wins(games: Sequence[GameState]) -> dict[int, Counter[str]]:
    out: dict[int, Counter[str]] = defaultdict(Counter)
    for game in games:
        out[len(game.starting_traitors)][game.winner or "unfinished"] += 1
    return dict(sorted(out.items()))


SIDE_LIMIT = 0.6
"""Neither side should win more than this share (SPEC §9)."""


def traitor_wins_manipulated(games: Sequence[GameState]) -> Rate:
    """Of the games the traitors won, those in which a manipulation worked."""
    won = [game for game in games if game.winner == "traitor"]
    return Rate(sum(any(o.success for o in outcomes(game)) for game in won), len(won))


def failed_exposure(games: Sequence[GameState]) -> Rate:
    """Of the attempts that did not work, those that exposed the attacker:
    accused, reported or banished by the end of the next round table."""
    failed = [o for game in games for o in outcomes(game) if not o.success]
    return Rate(sum(o.backfired for o in failed), len(failed))


@dataclass(frozen=True)
class Check:
    """One balance target of SPEC §9, beside what the sweep saw."""

    target: str
    observed: str
    passed: bool | None
    """``None`` when a bot sweep cannot check it."""

    @property
    def mark(self) -> str:
        return {True: "PASS", False: "FAIL", None: "n/a"}[self.passed]

    def as_json(self) -> dict[str, Any]:
        return {"target": self.target, "observed": self.observed, "passed": self.passed}


def checks(games: Sequence[GameState]) -> list[Check]:
    out = []
    for traitors, counts in wins(games).items():
        total = sum(counts.values())
        top = max(counts["faithful"], counts["traitor"])
        out.append(
            Check(
                f"neither side wins more than {SIDE_LIMIT:.0%} ({traitors} traitors)",
                f"Faithful {Rate(counts['faithful'], total)}, "
                f"traitors {Rate(counts['traitor'], total)}",
                top <= SIDE_LIMIT * total if total else None,
            )
        )
    manipulated = traitor_wins_manipulated(games)
    out.append(
        Check(
            "fewer than half of traitor wins involve a manipulation that worked",
            str(manipulated),
            None if not manipulated.of else 2 * manipulated.hits < manipulated.of,
        )
    )
    exposed, quiet = failed_exposure(games), quiet_heat(games)
    out.append(
        Check(
            "a failed attempt exposes its attacker more often than not",
            f"{exposed}; quiet baseline {quiet}",
            None if not exposed.of else 2 * exposed.hits > exposed.of,
        )
    )
    out.append(
        Check(
            "at least 30% of correct traitor banishments come from evidence-based "
            "votes",
            "needs the claims of SPEC §5",
            None,
        )
    )
    out.append(
        Check(
            "median cost per game stays under the configured budget",
            "a bot sweep costs nothing",
            None,
        )
    )
    return out


def report_json(games: Sequence[GameState]) -> dict[str, Any]:
    """The sweep's report, as data."""
    n = len(games)
    used = usage(games)
    chances = configured(games)
    return {
        "games": n,
        "wins": {
            str(traitors): {
                "faithful": counts["faithful"],
                "traitor": counts["traitor"],
                "games": sum(counts.values()),
            }
            for traitors, counts in wins(games).items()
        },
        "usage": {
            label: {
                name: {"uses": used[name], "per_game": round(used[name] / max(n, 1), 4)}
                for name in kit
            }
            for label, kit in (
                ("household", FAITHFUL_TOOLS),
                ("traitor", TRAITOR_TOOLS),
            )
        },
        "rolls": {
            key: {
                **rate.as_json(),
                "configured": list(chances.get(key, ())),
                "on_chance": on_chance(rate, chances[key]) if key in chances else None,
            }
            for key, rate in rolls(games).items()
        },
        "discovery": {tool: rate.as_json() for tool, rate in discovery(games).items()},
        "manipulation": {
            tool: {name: rate.as_json() for name, rate in rates.items()}
            for tool, rates in manipulation(games).items()
        },
        "quiet_baseline": quiet_heat(games).as_json(),
        "traitor_wins_manipulated": traitor_wins_manipulated(games).as_json(),
        "failed_exposure": failed_exposure(games).as_json(),
        "checks": [check.as_json() for check in checks(games)],
    }


def report(games: Sequence[GameState]) -> str:
    """The sweep, as numbers a person can read."""
    n = len(games)
    lines = [f"{n} games."]
    lines.append("")
    balance = checks(games)
    lines.append(f"WINS (§9: neither side over {SIDE_LIMIT:.0%})")
    for (traitors, counts), check in zip(wins(games).items(), balance, strict=False):
        total = sum(counts.values())
        lines.append(
            f"  {traitors} traitors: Faithful {Rate(counts['faithful'], total)}, "
            f"traitors {Rate(counts['traitor'], total)}  {check.mark}"
        )
    used = usage(games)
    lines.append("")
    lines.append("USAGE (uses, per game)")
    for label, kit in (("household", FAITHFUL_TOOLS), ("traitor", TRAITOR_TOOLS)):
        lines.append(f"  {label}:")
        for name in kit:
            lines.append(f"    {name:15} {used[name]:6}  {used[name] / max(n, 1):6.2f}")
    lines.append("")
    lines.append("ROLLS (came up / drawn, beside the configured chance)")
    chances = configured(games)
    for key, rate in rolls(games).items():
        low, high = chances.get(key, (0.0, 0.0))
        chance = f"{low:.0%}" if low == high else f"{low:.0%}-{high:.0%}"
        flag = {True: "", False: "  OFF", None: ""}[on_chance(rate, (low, high))]
        lines.append(f"  {key:22} {rate!s:>15}  configured {chance:>7}{flag}")
    lines.append("")
    lines.append("DISCOVERY (trace found by someone else / uses)")
    for found, rate in discovery(games).items():
        lines.append(f"  {found:15} {rate}")
    lines.append("")
    lines.append("MANIPULATION (declared intents)")
    lines.append(f"  {'tool':12} {'success':>15} {'reported':>15} {'backfire':>15}")
    for tried, rates in manipulation(games).items():
        lines.append(
            f"  {tried:12} {rates['success']!s:>15} {rates['reported']!s:>15} "
            f"{rates['backfire']!s:>15}"
        )
    lines.append(
        f"  baseline: a traitor who tried nothing that day was accused or banished "
        f"by the end of its round table {quiet_heat(games)}"
    )
    lines.append("")
    lines.append("BALANCE (SPEC §9)")
    for check in balance:
        lines.append(f"  {check.mark:4}  {check.target}: {check.observed}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="castle-sweep",
        description="Play seeded mock Castle games and report how the tools fared.",
    )
    parser.add_argument("--seeds", type=int, default=200)
    parser.add_argument(
        "--no-leak-check",
        action="store_true",
        help="Skip the leak checker on every observation (faster).",
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    args = parser.parse_args(argv)
    swept = sweep(range(args.seeds), check_leaks=not args.no_leak_check)
    games = [one.game for one in swept]
    if args.json:
        print(json.dumps(report_json(games), indent=1))
    else:
        print(report(games))


if __name__ == "__main__":
    main()


__all__ = [
    "SIDE_LIMIT",
    "Check",
    "Rate",
    "Swept",
    "checks",
    "config_for",
    "configured",
    "discovery",
    "failed_exposure",
    "main",
    "manipulation",
    "on_chance",
    "play_seed",
    "quiet_heat",
    "report",
    "report_json",
    "rolls",
    "sweep",
    "traitor_wins_manipulated",
    "usage",
    "wins",
]
