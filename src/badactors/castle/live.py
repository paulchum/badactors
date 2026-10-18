"""``castle-live``: play one Castle game with real models, within a budget.

Each seat is played by the model the cast file names for it, through
OpenRouter. The key comes from ``OPENROUTER_API_KEY`` and is never printed,
logged or recorded. The command refuses to start without all three limits —
requests, tokens and dollars — and prints a preflight estimate first, from a
free bot rehearsal of the same game through the same renderer; if the estimate
does not fit the budget it stops there unless ``--allow-partial`` is given.

    uv run castle-live --seed 7 --max-requests 600 --max-tokens 4000000 \\
        --max-dollars 15 --save data/live/seed7.json --record data/live/seed7.jsonl

A benchmark game comes from a schedule (``castle-bench --plan``): ``--schedule``
and ``--game`` take its seed, its seats and its arm from there, and the record
keeps the arm, so ``castle-bench`` rates it, or refuses it, without the
schedule.

    uv run castle-live --schedule out/schedule.json --game mixed-s0-r0 \\
        --max-requests 600 --max-tokens 4000000 --max-dollars 15

``--dry-run`` prints the estimate and spends nothing. ``--mock`` plays the whole
harness against the seeded bot behind a mock provider: no key, no network, no
cost, and the same ledger as ``castle-game`` with the same seed. Its seats are
recorded as ``mock:<id>``, never as the model, and it is never rated.

The saved record names ``model:<id>`` for every seat, replays to the identical
ledger, and says how the game ended. A game stopped by its budget, or aborted
by a 401, 402 or 403, is saved as far as it got and is never rated. A 402 for
OpenRouter's in-flight budget is waited out and the call sent again (logged as
``provider_wait``); only one that outlasts every wait aborts the game.

Beside the record go the harness log (``.harness.jsonl``: every call's tokens
and cost, mode changes, stops) and the game's verify bundle
(``.bundle.json.gz``): the record and a hashed receipt of every provider call —
provider, model id, attempt, the SHA-256 of the request and of the response,
and why an answer could not be used — chained to one head, which
``castle-verify`` checks. The receipt never holds a key, a header or a word.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from badactors.castle.balance import load_balance
from badactors.castle.bots import HeuristicBot
from badactors.castle.budget import Limits, preflight
from badactors.castle.cast import Cast, load_cast_file
from badactors.castle.configs import CAST, MAP
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
from badactors.castle.schedule import (
    Schedule,
    ScheduleError,
    Seating,
    load_schedule,
    traitor_seats,
)
from badactors.castle.verify import Bundle, HashingProvider, make_bundle, save_bundle

_CACHE = Path.home() / ".cache" / "badactors" / "openrouter-models.json"


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="castle-live",
        description="Play one Castle game with real models, within a budget.",
    )
    parser.add_argument("--seed", type=int, help="The game's seed (default 7).")
    parser.add_argument("--cast", default=str(CAST))
    parser.add_argument("--map", default=str(MAP))
    parser.add_argument(
        "--balance", help="Play under this balance file instead of the shipped one."
    )
    parser.add_argument("--players", type=int, help="Seat the first N of the cast.")
    parser.add_argument("--traitors", type=int, choices=(2, 3))
    parser.add_argument("--max-days", type=int)
    scheduled = parser.add_argument_group("a benchmark game")
    scheduled.add_argument("--schedule", help="A schedule from castle-bench --plan.")
    scheduled.add_argument("--game", help="Which of its games to play, by id.")
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
        "--reasoning",
        choices=("default", "least", "off", "minimal", "low", "medium", "high"),
        default="default",
        help=(
            "OpenRouter's reasoning setting for every seat. default sends none; "
            "least is off, or minimal where a model's reasoning is mandatory."
        ),
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
    seed = 7 if args.seed is None else args.seed
    seating: Seating | None = None
    overrides: dict[str, object] = {}
    if args.balance is not None:
        overrides.update(load_balance(args.balance).model_dump())
    if args.max_days is not None:
        overrides["max_days"] = args.max_days
    if args.schedule or args.game:
        schedule, seating = scheduled(args)
        try:
            cast = seating.cast(cast.first(schedule.players))
        except ScheduleError as exc:
            sys.exit(f"{exc} Pass the cast the schedule was planned from.")
        overrides["traitors"] = schedule.traitors
        seed = seating.seed
    else:
        if args.players is not None:
            cast = cast.first(args.players)
        if args.traitors is not None:
            overrides["traitors"] = args.traitors
    config = GameConfig.shipped(**overrides)
    if seating is not None and seating.traitors != traitor_seats(
        castle, cast, seed=seed, config=config
    ):
        sys.exit(
            f"Seed {seed} draws other traitor seats in this castle than the "
            f"schedule planned for {seating.id}; pass the map it was planned with."
        )

    models = _models(cast, args)
    estimate = preflight(
        castle,
        cast,
        models,
        seed=seed,
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

    save = (
        Path(args.save)
        if args.save
        else _default_save(seating.id if seating is not None else f"seed{seed}")
    )
    harness = HarnessLog(save.with_suffix(".harness.jsonl"))
    provider: Provider
    closers = []
    if args.mock:
        provider = BotProvider(HeuristicBot(seed=seed))
    else:
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            sys.exit("Set OPENROUTER_API_KEY to play with real models.")
        openrouter = OpenRouterProvider(
            api_key=key,
            timeout=args.timeout,
            reasoning=args.reasoning,
            on_fallback=lambda model: harness.write(
                "reasoning_fallback", model=model, setting="minimal"
            ),
        )
        harness.write("reasoning", setting=args.reasoning)
        closers.append(openrouter.aclose)
        provider = openrouter
    provider = HashingProvider(
        provider, harness, "mock:bot" if args.mock else "openrouter"
    )
    if args.record:
        provider = RecordingProvider(provider, Path(args.record))

    live = play_live(
        castle,
        cast,
        seed=seed,
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
        mock=args.mock,
        world_id=seating.world_id if seating is not None else "castle-game",
        arm=seating.arm if seating is not None else None,
    )
    save_record(live.record, save)
    bundle = make_bundle(live.record, live.harness.events)
    save_bundle(bundle, bundle_path(save))
    print(report(live, save, bundle))


def scheduled(args: argparse.Namespace) -> tuple[Schedule, Seating]:
    """The schedule, and the game of it ``--game`` names."""
    if not (args.schedule and args.game):
        sys.exit("--schedule and --game go together: a schedule, and its game.")
    if args.players is not None or args.traitors is not None:
        sys.exit("A scheduled game takes its seats and traitors from the schedule.")
    schedule = load_schedule(args.schedule)
    seating = next((game for game in schedule.games if game.id == args.game), None)
    if seating is None:
        sys.exit(f"{args.schedule} has no game {args.game!r}.")
    if seating.names_visible:
        sys.exit(
            "castle-live does not show models on the roster yet, so it cannot "
            "play a names-visible game."
        )
    if args.seed is not None and args.seed != seating.seed:
        sys.exit(
            f"Game {seating.id} is played on seed {seating.seed}, not {args.seed}."
        )
    return schedule, seating


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


def _default_save(name: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("data") / "live" / f"{name}-{stamp}.json"


def bundle_path(save: Path) -> Path:
    """Where the verify bundle of the record at ``save`` goes."""
    return save.with_suffix(".bundle.json.gz")


def report(live: LiveGame, save: Path, bundle: Bundle) -> str:
    stored = live.record
    failures = Counter(
        (entry.actor_id, entry.payload.get("reason", "?"))
        for entry in stored.ledger
        if entry.type == "turn_failed"
    )
    spent = live.budget.spent
    lines = [
        *([f"Arm: {stored.arm}, {stored.world_id}."] if stored.arm else []),
        f"Outcome: {stored.outcome}"
        + (f" ({live.referee.stopped_by})" if live.referee.stopped_by else "")
        + (f"; winner: {stored.winner}" if stored.winner else ""),
        f"Spent: {spent.requests} requests, {spent.tokens:,} tokens, "
        f"${spent.dollars:.4f}.",
        f"{len(stored.decisions)} decisions, {len(stored.ledger)} ledger entries.",
    ]
    for (seat, reason), count in sorted(failures.items(), key=str):
        lines.append(f"Failed turns: {seat} {reason} x{count}")
    waits = live.harness.of("provider_wait")
    if waits:
        seconds = sum(float(wait["seconds"]) for wait in waits)
        lines.append(
            f"Waited out OpenRouter's in-flight budget {len(waits)} times "
            f"({seconds:.0f} s in all)."
        )
    for change in live.harness.of("mode_downgrade"):
        lines.append(
            f"Mode: {change['model']} moved from {change['old']} to {change['new']} "
            f"({change['why']})"
        )
    lines.append(f"Saved the record to {save} and the harness log beside it.")
    lines.append(
        f"Verify bundle: {bundle_path(save)}, {len(bundle.exchanges)} exchanges, "
        f"head {bundle.head}."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
