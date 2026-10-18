"""``castle-game``: play a mock game from a seed and a cast, and print it.

Every seat is played by the seeded heuristic bot, so there are no model calls and
nothing to configure; the same seed always prints the same episode. The episode
and the saved record say so, seat by seat, and show no model ids: the cast's
models are who would play, not who did. Every observation a bot is given passes
the leak checker first, model names included.

    uv run castle-game --seed 7
    uv run castle-game --seed 12 --players 8 --save data/game.json
    uv run castle-game --seed 7 --verdicts
    uv run castle-game --seed 7 --feed data/feed.json
    uv run castle-game --seed 7 --highlights
"""

from __future__ import annotations

import argparse

from badactors.castle.balance import load_balance
from badactors.castle.bots import HeuristicBot
from badactors.castle.cast import load_cast_file
from badactors.castle.clips import highlights
from badactors.castle.configs import CAST, MAP
from badactors.castle.episode import episode
from badactors.castle.factcheck import printout
from badactors.castle.mapfile import load_map
from badactors.castle.metrics import per_game
from badactors.castle.metrics import printout as measures
from badactors.castle.play import record, run_game, save_record, start_game
from badactors.castle.rules import GameConfig
from badactors.castle.show import NameStyle, audience_feed, checked, save_feed
from badactors.castle.timeline import timeline


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="castle-game",
        description="Play a mock Castle game with seeded bots and print the episode.",
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--cast", default=str(CAST))
    parser.add_argument("--map", default=str(MAP))
    parser.add_argument(
        "--balance", help="Play under this balance file instead of the shipped one."
    )
    parser.add_argument(
        "--players", type=int, help="Seat the first N of the cast (8 to 10)."
    )
    parser.add_argument("--traitors", type=int, choices=(2, 3))
    parser.add_argument("--max-days", type=int)
    parser.add_argument("--save", help="Write a replayable game record to this path.")
    parser.add_argument(
        "--feed", help="Write the audience feed, which clips are made from, here."
    )
    parser.add_argument(
        "--highlights",
        action="store_true",
        help="Print only the game's best moments, the twist first, not the episode.",
    )
    parser.add_argument(
        "--model-names",
        action="store_true",
        help="Name each seat by the model that played it, as audience media do; "
        "a seat a bot played is a Reference bot.",
    )
    parser.add_argument(
        "--ledger",
        action="store_true",
        help="Also print the raw ledger, every entry and roll.",
    )
    parser.add_argument(
        "--verdicts",
        action="store_true",
        help="Also fact-check every claim made aloud, with the mock extractor, "
        "and print each verdict and every player's measures.",
    )
    args = parser.parse_args(argv)

    castle = load_map(args.map)
    cast = load_cast_file(args.cast)
    if args.players is not None:
        cast = cast.first(args.players)
    overrides: dict[str, object] = {}
    if args.balance is not None:
        overrides.update(load_balance(args.balance).model_dump())
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
    names: NameStyle = "model" if args.model_names else "character"
    stored = record(castle, cast, played)
    found = checked(stored)
    feed = audience_feed(stored, found)
    if args.highlights:
        print(highlights(feed, names=names))
    else:
        print(
            episode(finished, agents=played.agents, names=names, moments=feed.moments)
        )
        print(
            f"Every one of the {played.observations} things the bots were shown "
            f"was leak-checked first. {len(finished.world.ledger)} things happened "
            f"on tape, and {sum(len(entry.rolls) for entry in finished.world.ledger)}"
            " dice were rolled."
        )
    if args.ledger:
        print()
        print(timeline(finished.world))
    if args.verdicts:
        print()
        print(printout(finished, found))
        print()
        print(measures(finished, per_game(finished, found)))
    if args.save:
        save_record(stored, args.save)
        print(f"Saved a replayable record to {args.save}.")
    if args.feed:
        save_feed(feed, args.feed)
        print(f"Saved the audience feed to {args.feed}.")


if __name__ == "__main__":
    main()
