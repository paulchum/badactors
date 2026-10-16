"""``castle-bench``: the leaderboard, the matrix and a receipt for every game.

Two sources of games:

* ``--records DIR ...`` reads saved game records (``castle-live --save``), each
  with the harness log beside it when one was kept, replays each to check it,
  and rates them. A game played from a schedule (``castle-live --schedule``)
  names its arm in its record; Corruption Test and names-visible games are not
  rated.
* ``--bots`` is a dry run: it plays the mixed arm of a schedule with reference
  bot profiles of known susceptibility, and spends nothing.

Either way it writes, under ``--out`` (``out/`` by default):

* ``leaderboard.json`` — OpenSkill deception and detection ratings with 95%
  intervals, reliability (failed turns by reason class, the refusal rate, the
  2% gate) and cost per game, for models and, apart, for reference bots;
* ``matrix.json`` — the attacker-by-target manipulation matrix with the
  steer/resist fit, susceptibility by technique and usage rates, likewise
  apart;
* ``replays/models/`` and ``replays/bots/`` — a verify bundle per game, which
  ``castle-verify`` replays to every number.

Reference bots are labeled as such everywhere and never mixed with model
results: a record any seat of which a bot played is refused by the model board
and listed with the reason.

``--plan`` writes the schedule for a paid run of the cast's models instead, and
prints the power estimate from a bot pilot first (``castle-power``).

    uv run castle-bench --bots --seeds 20
    uv run castle-bench --records data/live
    uv run castle-bench --plan --seeds 40 --open-weight qwen/qwen3.8-27b
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from badactors.bench.dryrun import play_schedule, profiles
from badactors.bench.leaderboard import Board, Finished, Refusal, board
from badactors.bench.matrix import ALL, matrix
from badactors.bench.steer import spearman
from badactors.castle.cast import load_cast_file
from badactors.castle.mapfile import load_map
from badactors.castle.play import load_record, replay
from badactors.castle.power import arm_loads, plan, run_pilot
from badactors.castle.rules import GameConfig
from badactors.castle.schedule import (
    Rotation,
    Scenario,
    Tier,
    check,
    mixed_schedule,
    save_schedule,
)
from badactors.castle.verify import make_bundle, read_events, save_bundle

_CONFIGS = Path(__file__).resolve().parents[3] / "configs" / "castle"

NOTE = (
    "Reference bots are labeled as such and never mixed with model results; "
    "a game any seat of which a bot played is refused by the model board."
)


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="castle-bench",
        description="Rate Castle games and write the leaderboard, the matrix and "
        "a verify bundle per game.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bots", action="store_true", help="A dry run with bots.")
    source.add_argument("--records", nargs="+", help="Saved records, or folders.")
    source.add_argument("--plan", action="store_true", help="Schedule a paid run.")
    parser.add_argument("--out", default="out")
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--profiles", type=int, help="Bot profiles (default: players).")
    parser.add_argument("--players", type=int, default=8)
    parser.add_argument("--traitors", type=int, choices=(2, 3), default=2)
    parser.add_argument("--rotation", choices=("latin", "compact"), default="latin")
    parser.add_argument("--seed", type=int, default=0, help="Deals the bot profiles.")
    parser.add_argument("--resamples", type=int, default=200, help="Bootstrap draws.")
    parser.add_argument("--no-leak-check", action="store_true")
    parser.add_argument("--cast", default=str(_CONFIGS / "cast.json"))
    parser.add_argument("--map", default=str(_CONFIGS / "map.json"))
    parser.add_argument(
        "--open-weight",
        nargs="*",
        default=None,
        help="With --plan: the cast's open-weight models; the rest are frontier.",
    )
    parser.add_argument("--pilot-seeds", type=int, default=6)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    out = Path(args.out)
    scenario = Scenario(
        castle=load_map(args.map),
        cast=load_cast_file(args.cast).first(args.players),
        config=GameConfig.shipped(traitors=args.traitors),
    )
    rotation: Rotation = args.rotation
    if args.plan:
        plan_run(scenario, args, out, rotation)
        return
    if args.bots:
        games, refused = dry_run(scenario, args, out, rotation)
    else:
        games, refused = read_records(args.records, out)
    write(games, refused, out, resamples=args.resamples, seed=args.seed)


def dry_run(
    scenario: Scenario, args: argparse.Namespace, out: Path, rotation: Rotation
) -> tuple[list[Finished], list[Refusal]]:
    levels = profiles(args.profiles or args.players, seed=args.seed)
    schedule = mixed_schedule(
        scenario, list(levels), seeds=list(range(args.seeds)), rotation=rotation
    )
    for cover in check(schedule):
        print(
            f"Schedule: {cover.arm}, {cover.games} games over {cover.seeds} seeds, "
            f"{cover.entrants} reference bot profiles, every one both roles each "
            f"seed, {cover.pairs}/{cover.pairs_wanted} attacker-target pairs met."
        )
    save_schedule(schedule, out / "schedule.json")
    replays = out / "replays" / "bots"
    replays.mkdir(parents=True, exist_ok=True)
    games = play_schedule(
        schedule.games,
        scenario,
        levels,
        check_leaks=not args.no_leak_check,
        replays=replays,
    )
    known = {f"bot:heuristic/{name}": level for name, level in levels.items()}
    pooled = matrix(games, reference=True).fits(resamples=0).get(ALL)
    recovered = (
        spearman(
            [known[entrant] for entrant in sorted(known)],
            [pooled.fit.susceptibility(entrant) for entrant in sorted(known)],
        )
        if pooled is not None
        else None
    )
    if recovered is not None:
        print(
            "The steer/resist fit puts the profiles' known susceptibilities in "
            f"order with Spearman {recovered:.2f}."
        )
    (out / "profiles.json").write_text(
        json.dumps(
            {
                "reference_bots": True,
                "susceptibility": known,
                "fitted": {
                    entrant: round(pooled.fit.susceptibility(entrant), 4)
                    for entrant in sorted(known)
                }
                if pooled is not None
                else None,
                "spearman": None if recovered is None else round(recovered, 4),
            },
            indent=1,
        )
    )
    return games, []


def record_paths(sources: Sequence[str]) -> Iterator[Path]:
    for source in sources:
        path = Path(source)
        if path.is_dir():
            yield from sorted(
                p
                for p in path.rglob("*.json")
                if not p.name.endswith((".bundle.json", ".harness.json"))
            )
        else:
            yield path


def read_records(
    sources: Sequence[str], out: Path
) -> tuple[list[Finished], list[Refusal]]:
    """Load, replay and bundle every record; refuse any that does not replay to
    its own ledger. A scheduled game goes by its schedule id, any other by its
    file name."""
    games: list[Finished] = []
    refused: list[Refusal] = []
    for path in record_paths(sources):
        try:
            stored = load_record(path)
        except ValueError as exc:
            refused.append(Refusal(path.stem, f"not a game record: {exc}"[:200]))
            continue
        harness = path.with_suffix(".harness.jsonl")
        events = read_events(harness) if harness.exists() else []
        name = stored.world_id if stored.arm is not None else path.stem
        try:
            game = replay(stored)
        except Exception as exc:
            refused.append(Refusal(name, f"does not replay: {exc}"[:200]))
            continue
        if game.world.ledger != stored.ledger:
            refused.append(Refusal(name, "its ledger is not the one its replay writes"))
            continue
        kind = "bots" if stored.reference_bots else "models"
        save_bundle(
            make_bundle(stored, events), out / "replays" / kind / f"{name}.json.gz"
        )
        games.append(
            Finished(
                id=name,
                record=stored,
                game=game,
                calls=tuple(e for e in events if e.get("event") == "call"),
                spent=_spent(events),
            )
        )
    return games, refused


def _spent(events: Sequence[dict[str, Any]]) -> float | None:
    spent = [e for e in events if e.get("event") == "spent"]
    return float(spent[-1]["dollars"]) if spent else None


def write(
    games: Sequence[Finished],
    refused: Sequence[Refusal],
    out: Path,
    *,
    resamples: int,
    seed: int,
) -> None:
    models = board(games, refused=refused)
    bots = board(games, reference=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "leaderboard.json").write_text(
        json.dumps(
            {
                "benchmark": "castle",
                "note": NOTE,
                "ratings": "OpenSkill (Weng-Lin Bradley-Terry); deception from "
                "traitor seats, detection from Faithful seats; mu with a 95% "
                "interval and ordinal mu - 3 sigma",
                "models": models.as_json(),
                "reference_bots": bots.as_json(),
            },
            indent=1,
        )
    )
    (out / "matrix.json").write_text(
        json.dumps(
            {
                "benchmark": "castle",
                "note": NOTE,
                "models": matrix(games).as_json(resamples=resamples, seed=seed),
                "reference_bots": matrix(games, reference=True).as_json(
                    resamples=resamples, seed=seed
                ),
            },
            indent=1,
        )
    )
    print(summary(models))
    print(summary(bots))
    print(
        f"Wrote {out / 'leaderboard.json'}, {out / 'matrix.json'} and "
        f"{len(games)} verify bundles under {out / 'replays'}."
    )


def summary(rated: Board) -> str:
    title = (
        "REFERENCE BOTS (never mixed with model results)"
        if rated.reference_bots
        else "MODELS"
    )
    lines = [f"{title}: {len(rated.games)} games rated, {len(rated.refused)} refused."]
    for row in rated.ranked():
        lines.append(
            f"  {row.label:34} deception {row.deception.mu:5.1f} "
            f"[{row.deception.low:5.1f}, {row.deception.high:5.1f}]  detection "
            f"{row.detection.mu:5.1f} [{row.detection.low:5.1f}, "
            f"{row.detection.high:5.1f}]  failed {row.failure_rate:.1%}"
        )
    reasons: dict[str, int] = {}
    for item in rated.refused:
        reasons[item.reason] = reasons.get(item.reason, 0) + 1
    for reason, count in sorted(reasons.items()):
        lines.append(f"  refused {count}: {reason}")
    return "\n".join(lines)


def plan_run(
    scenario: Scenario, args: argparse.Namespace, out: Path, rotation: Rotation
) -> None:
    """Print the power estimate, then write the schedule for the cast's models."""
    models = list(dict.fromkeys(scenario.cast.models().values()))
    pilot = run_pilot(
        scenario, seeds=args.pilot_seeds, entrants=len(models), rotation=rotation
    )
    print(plan(pilot, arm_loads(scenario, models, rotation=rotation)).summary())
    print()
    tiers: dict[str, Tier] = {}
    if args.open_weight is not None:
        unknown = sorted(set(args.open_weight) - set(models))
        if unknown:
            sys.exit(f"Not in the cast: {', '.join(unknown)}.")
        tiers = {m: "open" if m in args.open_weight else "frontier" for m in models}
    schedule = mixed_schedule(
        scenario, models, seeds=list(range(args.seeds)), rotation=rotation, tiers=tiers
    )
    for cover in check(schedule):
        print(
            f"Schedule: {cover.arm}, {cover.games} games over {cover.seeds} seeds, "
            f"{cover.entrants} models, {cover.pairs}/{cover.pairs_wanted} pairs met."
        )
    save_schedule(schedule, out / "schedule.json")
    print(f"Wrote {out / 'schedule.json'}. Nothing was played and nothing spent.")


if __name__ == "__main__":
    main()


__all__ = ["main", "parser", "read_records", "record_paths", "summary", "write"]
