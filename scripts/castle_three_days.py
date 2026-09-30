"""Run three days of the Castle with a deterministic decider and print the result.

No model calls and no network. Everything here comes from the seeded cursor in the
world state, so two runs with the same seed print byte-identical output.

    uv run python scripts/castle_three_days.py
    uv run python scripts/castle_three_days.py --seed 29 --days 2 --quiet-entries
"""

from __future__ import annotations

import argparse
from pathlib import Path

from badactors.castle.director import DirectorStats, RoutineDecider, run_day
from badactors.castle.ledger import replay_placements
from badactors.castle.mapfile import load_world
from badactors.castle.timeline import (
    disagreements,
    summary,
    timeline,
    witness_digest,
)
from badactors.castle.world import start

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP = ROOT / "configs/castle/map.json"
DEFAULT_CAST = ROOT / "configs/castle/cast.json"


def main() -> None:
    parser = argparse.ArgumentParser(prog="castle-three-days")
    parser.add_argument("--map", default=str(DEFAULT_MAP))
    parser.add_argument("--cast", default=str(DEFAULT_CAST))
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--no-rolls", action="store_true", help="Hide the dice.")
    parser.add_argument(
        "--quiet-entries",
        action="store_true",
        help="Include plan and setup entries the timeline normally hides.",
    )
    parser.add_argument(
        "--digests",
        type=int,
        default=2,
        help="How many players' private memories to print.",
    )
    args = parser.parse_args()

    world = start(load_world(args.map, args.cast, seed=args.seed))
    stats = DirectorStats()
    decider = RoutineDecider(seed=args.seed)
    for _ in range(args.days):
        world = run_day(world, decider, stats=stats)

    print(f"{world.map.name} · seed {world.seed} · {args.days} day(s)")
    print(_rule())
    print(
        timeline(
            world,
            include_quiet=args.quiet_entries,
            rolls=not args.no_rolls,
        )
    )

    print()
    print(_rule())
    print("WHAT EACH WITNESS BELIEVES")
    for player_id in world.seats[: max(0, args.digests)]:
        print()
        print(witness_digest(world, player_id, limit=12))

    print()
    print(_rule())
    print(disagreements(world))

    print()
    print(_rule())
    print(summary(world))
    print(f"decisions asked of each player: {sorted(stats.calls.items())}")
    print(f"most decisions asked of one player: {stats.most()}")
    cached = {
        key: value.model_dump(mode="json") for key, value in world.placements.items()
    }
    replayed = {
        key: value.model_dump(mode="json")
        for key, value in replay_placements(world.ledger).items()
    }
    print(f"custody cache equals a ledger replay: {cached == replayed}")


def _rule() -> str:
    return "═" * 78


if __name__ == "__main__":
    main()
