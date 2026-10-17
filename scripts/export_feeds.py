"""Write the audience feeds the static spectator bundles, and their index.

Each seed is played exactly as ``castle-game --seed N`` plays it: the shipped
map, cast and numbers, with the seeded heuristic bot in every seat. No model is
asked anything. The feeds land in ``frontend/public/games/`` beside an
``index.json`` the site reads first, and every one is labelled a bot game.
They are built, not committed: ``npm run build``, ``npm run dev`` and the
frontend tests run this first (``npm run feeds``), and git ignores the folder.

    uv run python scripts/export_feeds.py
    uv run python scripts/export_feeds.py --seeds 56 35 --out /tmp/games
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from badactors.castle.bots import HeuristicBot
from badactors.castle.cast import load_cast_file
from badactors.castle.configs import CAST, MAP
from badactors.castle.mapfile import load_map
from badactors.castle.play import record, run_game, start_game
from badactors.castle.rules import GameConfig
from badactors.castle.show import Feed, audience_feed, save_feed

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "public" / "games"

SEEDS: tuple[int, ...] = (56, 35, 145, 36, 169, 30)
"""The bundled games, the first shown first. 56 ends with a recruit still at
the table when the household votes to stop (the only such ending in the first
200 seeds); 56 and 145 have a recruit turn on their recruiter; 36, 145 and 169
have a forged note that works; in 35 the household banishes five of its own
and the traitors win; on day 1 of 30 a traitor's accusation fools every one of
the Faithful. Each has a round table for the landing game."""

INDEX_VERSION = 1


def play(seed: int) -> Feed:
    """The feed of the game ``castle-game --seed <seed>`` plays."""
    castle = load_map(MAP)
    cast = load_cast_file(CAST)
    game = start_game(castle, cast, seed=seed, config=GameConfig.shipped())
    bot = HeuristicBot(seed=seed)
    played = run_game(
        game,
        {pid: bot for pid in game.world.seats},
        models=list(cast.models().values()),
    )
    return audience_feed(record(castle, cast, played))


def summary(game_id: str, feed: Feed) -> dict[str, object]:
    """What the site's game list shows before it loads a feed."""
    game = feed.game
    kinds = Counter(moment.kind for moment in feed.moments)
    top = feed.moments[0] if feed.moments else None
    return {
        "id": game_id,
        "file": f"{game_id}.json",
        "castle": game.castle,
        "seed": game.seed,
        "winner": game.winner,
        "days": game.days,
        "ticks": game.ticks,
        "seats": len(feed.seats),
        "reference_bots": game.reference_bots,
        "played_by": sorted({seat.played_by for seat in feed.seats}),
        "moments": len(feed.moments),
        "kinds": dict(sorted(kinds.items())),
        "twist": kinds["twist_ending"] > 0,
        "headline": top.headline if top is not None else None,
    }


def export(seeds: list[int], out: Path) -> list[dict[str, object]]:
    out.mkdir(parents=True, exist_ok=True)
    games: list[dict[str, object]] = []
    for seed in seeds:
        game_id = f"seed-{seed}"
        feed = play(seed)
        if not feed.game.reference_bots:
            raise SystemExit(f"Seed {seed} was not a bot game; refusing to bundle it.")
        save_feed(feed, out / f"{game_id}.json")
        games.append(summary(game_id, feed))
        print(f"{game_id}: {len(feed.events)} events, {len(feed.moments)} moments")
    keep = {str(game["file"]) for game in games} | {"index.json"}
    for stale in out.glob("*.json"):
        if stale.name not in keep:
            stale.unlink()
    index = {"version": INDEX_VERSION, "games": games}
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    return games


def main() -> None:
    parser = argparse.ArgumentParser(prog="export-feeds", description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    games = export(args.seeds, args.out)
    print(f"Wrote {len(games)} bot games and index.json to {args.out}.")


if __name__ == "__main__":
    main()
