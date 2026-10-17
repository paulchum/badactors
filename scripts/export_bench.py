"""Write the bot cast's standings into the static site, for the standings pages.

It runs the benchmark's dry run (``castle-bench --bots``): reference bot
profiles of known susceptibility play the mixed arm of a schedule, and the
leaderboard and the attacker-by-target matrix are rated from their games. No
model is asked anything, so every number here is the bot cast's and says so.

Into ``frontend/public/bench/`` it writes castle-bench's own JSON unchanged
(``leaderboard.json``, ``matrix.json``, ``profiles.json``, ``schedule.json``),
each game's verify bundle under ``replays/bots/``, and ``seats.json`` with what
castle-bench's JSON does not carry and the pages need:

* ``run`` — the command, the rotation, the qualification suite's digest, and
  the commit and its date (the run date, so a rebuild of the same commit
  writes the same bytes);
* ``min_games`` — below this many games a stat card is not drawn;
* ``games`` — each game's seed, winner, ticks and bundle, for receipts;
* ``seats`` — one row per seat per game from the fact-checker (lies, unforced
  lies, lies nobody caught, memories planted in others and in this seat,
  evidence-based votes), the recruitment offers it refused and the
  manipulations at it that worked, each with the ticks it happened at.

Like the feeds, all of it is built, not committed: ``npm run bench`` runs this
before the site's dev server, its build and its tests, and git ignores the
folder.

    uv run python scripts/export_bench.py
    uv run python scripts/export_bench.py --seeds 20 --out /tmp/bench
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from badactors.bench import cli
from badactors.bench.leaderboard import Finished
from badactors.castle.cast import load_cast_file
from badactors.castle.claims import MockExtractor, extract
from badactors.castle.factcheck import verdicts
from badactors.castle.game import was_traitor_at
from badactors.castle.manipulation import outcomes
from badactors.castle.mapfile import load_map
from badactors.castle.metrics import per_game
from badactors.castle.qualify import load_suite
from badactors.castle.rules import GameConfig
from badactors.castle.schedule import Scenario

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "public" / "bench"

SEEDS = 8
"""Eight seeds of the compact rotation: 32 games, every profile in both roles
on every seed and every attacker-target pair met, in about fifteen seconds."""

MIN_GAMES = 5
"""SPEC §8: single-stat cards are made only above a minimum number of games."""

VERSION = 1
COPIED = ("leaderboard.json", "matrix.json", "profiles.json", "schedule.json")


def run_bench(seeds: int, out: Path) -> list[Finished]:
    """``castle-bench --bots --seeds <seeds> --out <out>``, keeping the games."""
    args = cli.parser().parse_args(["--bots", "--seeds", str(seeds), "--out", str(out)])
    scenario = Scenario(
        castle=load_map(args.map),
        cast=load_cast_file(args.cast).first(args.players),
        config=GameConfig.shipped(traitors=args.traitors),
    )
    games, refused = cli.dry_run(scenario, args, out, args.rotation)
    cli.write(games, refused, out, resamples=args.resamples, seed=args.seed)
    return games


def _span(ticks: Sequence[int]) -> list[int]:
    return [min(ticks), max(ticks)]


def seat_rows(finished: Finished) -> list[dict[str, Any]]:
    """Every seat's game, as the fact-checker and the ledger read it."""
    game = finished.game
    ledger = game.world.ledger
    found = verdicts(
        game, extract(game, MockExtractor()), log=finished.record.decisions
    )
    judged = per_game(game, found)
    measured = judged.players

    def tick(seq: int) -> int:
        return ledger[seq - 1].tick

    when: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for verdict in found:
        claim = verdict.claim
        if verdict.label == "lie":
            when[claim.speaker]["lies"].append(tick(claim.seq))
            if not was_traitor_at(game, claim.speaker, claim.seq):
                when[claim.speaker]["unforced_lies"].append(tick(claim.seq))
        if verdict.label == "implanted":
            when[claim.speaker]["implanted"].append(tick(claim.seq))
            if verdict.planted_by in measured:
                when[verdict.planted_by]["planted"].append(tick(claim.seq))
    for vote in judged.votes:
        if vote.evidence_based:
            when[vote.voter]["evidence_votes"].append(tick(vote.seq))
    offers: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for entry in ledger:
        if entry.type == "recruit_answer" and entry.actor_id is not None:
            offers[entry.actor_id][0] += 1
            if entry.payload.get("answer") == "refused":
                offers[entry.actor_id][1] += 1
                when[entry.actor_id]["refused_offer"].append(entry.tick)
    steered: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for attempt in outcomes(game):
        steered[attempt.attacker][0] += 1
        steered[attempt.target][1] += 1
        if attempt.success:
            when[attempt.attacker]["steered"].append(tick(attempt.seq))
            when[attempt.target]["fell_for"].append(tick(attempt.seq))
    rows = []
    for seat, agent in sorted(finished.record.agents.items()):
        stats = measured[seat]
        rows.append(
            {
                "game": finished.id,
                "seat": seat,
                "entrant": agent,
                "traitor": stats.role == "traitor",
                "recruited": seat in game.traitor_since
                and seat not in game.starting_traitors,
                "won": game.winner == stats.role,
                "survived": stats.survived,
                "claims": stats.claims,
                "checkable": stats.checkable,
                "lies": stats.lies,
                "lies_exposed": stats.lies_exposed,
                "unforced_lies": stats.unforced_lies,
                "planted": stats.planted,
                "implanted": stats.implanted_in,
                "gave_in": stats.gave_in,
                "votes": stats.votes,
                "evidence_votes": stats.evidence_votes,
                "offers": offers[seat][0],
                "offers_refused": offers[seat][1],
                "attempts": steered[seat][0],
                "targeted": steered[seat][1],
                "steered": len(when[seat]["steered"]),
                "fell_for": len(when[seat]["fell_for"]),
                "ticks": {
                    kind: _span(ticks)
                    for kind, ticks in sorted(when[seat].items())
                    if ticks
                },
            }
        )
    return rows


def game_row(finished: Finished) -> dict[str, Any]:
    ledger = finished.game.world.ledger
    return {
        "id": finished.id,
        "seed": finished.record.seed,
        "winner": finished.record.winner,
        "ticks": [ledger[0].tick, ledger[-1].tick] if ledger else [0, 0],
        "bundle": f"replays/bots/{finished.id}.json.gz",
    }


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.strip() or None


def run_info(seeds: int, schedule: dict[str, Any], games: int) -> dict[str, Any]:
    return {
        "command": f"castle-bench --bots --seeds {seeds}",
        "seeds": seeds,
        "games": games,
        "players": schedule["players"],
        "traitors": schedule["traitors"],
        "rotation": schedule["rotation"],
        "suite_digest": load_suite().digest,
        "commit": _git("rev-parse", "--short=12", "HEAD"),
        "date": _git("log", "-1", "--format=%cs"),
    }


def export(seeds: int, out: Path, *, min_games: int = MIN_GAMES) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        games = sorted(run_bench(seeds, work), key=lambda f: (f.record.seed, f.id))
        if not all(finished.record.reference_bots for finished in games):
            raise SystemExit("A game with a model in it reached the bot export.")
        seats = [row for finished in games for row in seat_rows(finished)]
        if out.exists():
            shutil.rmtree(out)
        (out / "replays").mkdir(parents=True)
        for name in COPIED:
            shutil.copyfile(work / name, out / name)
        shutil.copytree(work / "replays" / "bots", out / "replays" / "bots")
    schedule = json.loads((out / "schedule.json").read_text())
    site = {
        "version": VERSION,
        "reference_bots": True,
        "run": run_info(seeds, schedule, len(games)),
        "min_games": min_games,
        "games": [game_row(finished) for finished in games],
        "seats": seats,
    }
    (out / "seats.json").write_text(json.dumps(site, indent=1) + "\n")
    return site


def main() -> None:
    parser = argparse.ArgumentParser(prog="export-bench", description=__doc__)
    parser.add_argument("--seeds", type=int, default=SEEDS)
    parser.add_argument("--min-games", type=int, default=MIN_GAMES)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    site = export(args.seeds, args.out, min_games=args.min_games)
    print(
        f"Wrote the bot cast's standings, {len(site['games'])} games and "
        f"{len(site['seats'])} seats, to {args.out}."
    )


if __name__ == "__main__":
    main()
