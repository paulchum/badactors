"""``castle-game``: play a mock game from a seed and a cast, and print it.

Every seat is played by the seeded heuristic bot, so there are no model calls and
nothing to configure; the same seed always prints the same episode. Every
observation a bot is given passes the leak checker first, model names included.

    uv run castle-game --seed 7
    uv run castle-game --seed 12 --players 8 --save data/game.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from badactors.castle.bots import HeuristicBot
from badactors.castle.cast import load_cast_file
from badactors.castle.episode import episode
from badactors.castle.mapfile import load_map
from badactors.castle.play import record, run_game, save_record, start_game
from badactors.castle.rules import GameConfig
from badactors.castle.timeline import timeline

_CONFIGS = Path(__file__).resolve().parents[3] / "configs" / "castle"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="castle-game",
        description="Play a mock Castle game with seeded bots and print the episode.",
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--cast", default=str(_CONFIGS / "cast.json"))
    parser.add_argument("--map", default=str(_CONFIGS / "map.json"))
    parser.add_argument(
        "--players", type=int, help="Seat the first N of the cast (8 to 10)."
    )
    parser.add_argument("--traitors", type=int, choices=(2, 3))
    parser.add_argument("--max-days", type=int)
    parser.add_argument("--save", help="Write a replayable game record to this path.")
    parser.add_argument(
        "--ledger",
        action="store_true",
        help="Also print the raw ledger, every entry and roll.",
    )
    args = parser.parse_args(argv)

    castle = load_map(args.map)
    cast = load_cast_file(args.cast)
    if args.players is not None:
        cast = cast.first(args.players)
    overrides: dict[str, int] = {}
    if args.traitors is not None:
        overrides["traitors"] = args.traitors
    if args.max_days is not None:
        overrides["max_days"] = args.max_days
    config = GameConfig.shipped(**overrides)

    game = start_game(castle, cast, seed=args.seed, config=config)
    bot = HeuristicBot(seed=args.seed)
    played = run_game(
        game,
        {pid: bot for pid in game.world.seats},
        models=list(cast.models().values()),
    )
    finished = played.game
    print(episode(finished, models=cast.models()))
    print(
        f"{played.observations} observations, every one leak-checked; "
        f"{len(finished.world.ledger)} ledger entries; "
        f"{sum(len(entry.rolls) for entry in finished.world.ledger)} recorded rolls."
    )
    if args.ledger:
        print()
        print(timeline(finished.world))
    if args.save:
        save_record(record(castle, cast, played), args.save)
        print(f"Saved a replayable record to {args.save}.")


if __name__ == "__main__":
    main()
