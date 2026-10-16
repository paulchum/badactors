"""``castle-live``: play one Castle game with real models, within a budget.

Each seat is played by the model the cast file names for it, through
OpenRouter. The key comes from ``OPENROUTER_API_KEY`` and is never printed,
logged or recorded. The command refuses to start without all three limits —
requests, tokens and dollars — and prints a preflight estimate first, from a
free bot rehearsal of the same game through the same renderer; if the estimate
does not fit the budget it stops there unless ``--allow-partial`` is given.

    uv run castle-live --seed 7 --max-requests 600 --max-tokens 4000000 \\
        --max-dollars 15 --save data/live/seed7.json --record data/live/seed7.jsonl

``--dry-run`` prints the estimate and spends nothing. ``--mock`` plays the whole
harness against the seeded bot behind a mock provider: no key, no network, no
cost, and the same ledger as ``castle-game`` with the same seed.

The saved record names ``model:<id>`` for every seat, replays to the identical
ledger, and says how the game ended. A game stopped by its budget, or aborted
by a 401, 402 or 403, is saved as far as it got and is never rated.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from badactors.castle.bots import HeuristicBot
from badactors.castle.budget import Limits, preflight
from badactors.castle.cast import Cast, load_cast_file
from badactors.castle.harness import LiveGame, play_live
from badactors.castle.mapfile import load_map
from badactors.castle.mock_provider import BotProvider, mock_models
from badactors.castle.play import save_record
from badactors.castle.provider import (
    HarnessLog,
    ModelInfo,
    OpenRouterProvider,
    Provider,
    RecordingProvider,
    load_models,
)
from badactors.castle.rules import GameConfig

_ROOT = Path(__file__).resolve().parents[3]
_CONFIGS = _ROOT / "configs" / "castle"
_CACHE = Path.home() / ".cache" / "badactors" / "openrouter-models.json"


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="castle-live",
        description="Play one Castle game with real models, within a budget.",
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--cast", default=str(_CONFIGS / "cast.json"))
    parser.add_argument("--map", default=str(_CONFIGS / "map.json"))
    parser.add_argument("--players", type=int, help="Seat the first N of the cast.")
    parser.add_argument("--traitors", type=int, choices=(2, 3))
    parser.add_argument("--max-days", type=int)
    budget = parser.add_argument_group("budget (all three required)")
    budget.add_argument("--max-requests", type=int)
    budget.add_argument("--max-tokens", type=int)
    budget.add_argument("--max-dollars", type=float)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="The most one answer may generate; each call reserves this.",
    )
    parser.add_argument(
        "--concurrency", type=int, default=4, help="Calls in flight per model."
    )
    parser.add_argument("--sequential", action="store_true", help="No prefetching.")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--save", help="Where to write the game record.")
    parser.add_argument(
        "--record", help="Also write every raw provider response here, as fixtures."
    )
    parser.add_argument("--models-cache", default=str(_CACHE))
    parser.add_argument(
        "--models-ttl-hours", type=float, default=24.0, help="Model metadata TTL."
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Start even if the estimate does not fit the budget.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Estimate only.")
    parser.add_argument(
        "--mock", action="store_true", help="The seeded bot behind a mock provider."
    )
    parser.add_argument(
        "--no-leak-check", action="store_true", help="Skip the per-prompt checkers."
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    missing = [
        flag
        for flag, value in (
            ("--max-requests", args.max_requests),
            ("--max-tokens", args.max_tokens),
            ("--max-dollars", args.max_dollars),
        )
        if value is None
    ]
    if missing:
        sys.exit(f"castle-live will not start without {', '.join(missing)}.")
    limits = Limits(args.max_requests, args.max_tokens, args.max_dollars)

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

    models = _models(cast, args)
    estimate = preflight(
        castle,
        cast,
        models,
        seed=args.seed,
        config=config,
        max_tokens=args.max_output_tokens,
    )
    print(estimate.summary())
    if args.dry_run:
        return
    if not estimate.fits(limits) and not args.allow_partial:
        sys.exit(
            "The estimate does not fit the budget; raise it, or pass "
            "--allow-partial to play until it runs out."
        )

    save = Path(args.save) if args.save else _default_save(args.seed)
    harness = HarnessLog(save.with_suffix(".harness.jsonl"))
    provider: Provider
    closers = []
    if args.mock:
        provider = BotProvider(HeuristicBot(seed=args.seed))
    else:
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            sys.exit("Set OPENROUTER_API_KEY to play with real models.")
        openrouter = OpenRouterProvider(api_key=key, timeout=args.timeout)
        closers.append(openrouter.aclose)
        provider = openrouter
    if args.record:
        provider = RecordingProvider(provider, Path(args.record))

    live = play_live(
        castle,
        cast,
        seed=args.seed,
        config=config,
        provider=provider,
        models=models,
        limits=limits,
        max_tokens=args.max_output_tokens,
        concurrency=args.concurrency,
        harness=harness,
        check_leaks=not args.no_leak_check,
        concurrent=not args.sequential,
        closers=closers,
    )
    save_record(live.record, save)
    print(report(live, save))


def _models(cast: Cast, args: argparse.Namespace) -> dict[str, ModelInfo]:
    wanted = sorted(set(cast.models().values()))
    if args.mock:
        return mock_models(wanted)
    known = asyncio.run(
        load_models(Path(args.models_cache), ttl=args.models_ttl_hours * 3600)
    )
    unknown = [model for model in wanted if model not in known]
    if unknown:
        sys.exit(f"OpenRouter does not list {', '.join(unknown)}; check the cast.")
    return {model: known[model] for model in wanted}


def _default_save(seed: int) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return _ROOT / "data" / "live" / f"seed{seed}-{stamp}.json"


def report(live: LiveGame, save: Path) -> str:
    stored = live.record
    failures = Counter(
        (entry.actor_id, entry.payload.get("reason", "?"))
        for entry in stored.ledger
        if entry.type == "turn_failed"
    )
    spent = live.budget.spent
    lines = [
        f"Outcome: {stored.outcome}"
        + (f" ({live.referee.stopped_by})" if live.referee.stopped_by else "")
        + (f"; winner: {stored.winner}" if stored.winner else ""),
        f"Spent: {spent.requests} requests, {spent.tokens:,} tokens, "
        f"${spent.dollars:.4f}.",
        f"{len(stored.decisions)} decisions, {len(stored.ledger)} ledger entries.",
    ]
    for (seat, reason), count in sorted(failures.items(), key=str):
        lines.append(f"Failed turns: {seat} {reason} x{count}")
    for change in live.harness.of("mode_downgrade"):
        lines.append(
            f"Mode: {change['model']} moved from {change['old']} to {change['new']} "
            f"({change['why']})"
        )
    lines.append(f"Saved the record to {save} and the harness log beside it.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
