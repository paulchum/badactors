"""``castle-qualify``: the qualification run the reliability gate reads.

Before it plays rated games, a model answers a **frozen suite**: about three
hundred scoped observations sampled from seeded bot games, covering every
request kind a turn can be, in each role the game asks it of. Each is put to
the model through the same :class:`~badactors.castle.harness.ModelPlayer` a live
game uses — the renderer, the answer schema, the structured-output mode, the
reader — one model at a time, within a budget. An answer the harness cannot use
is put once more with the error, as at the table, so each question is one turn
however it ends.

The gate reads failed turns over turns, from at least ``MIN_TURNS`` of them,
and counts only **reliability failures**: transport, empty, unparseable and
invalid. A refusal in the model's own words and a provider's content filter are
results, not breakage (SPEC §6); each has its own column and never counts
toward the gate. A model failing more than 2% of its turns does not qualify. It
is a cheap step of its own: the benchmark's pilot measures effect sizes only.

The suite is frozen: ``--build`` writes it once, from fixed seeds, to
``qualification.json.xz`` in :mod:`badactors.castle.configs`, and it is kept, so
every model answers the same questions and every result names the suite by its
digest.
Rebuild it only on purpose; results from two suites are not comparable.

    uv run castle-qualify anthropic/claude-opus-5.5 --max-requests 700 \\
        --max-tokens 12000000 --max-dollars 40
    uv run castle-qualify lab/model --mock --max-requests 700 \\
        --max-tokens 12000000 --max-dollars 1
    uv run castle-qualify --build

A ``--mock`` run puts the seeded bot behind the mock provider: it exercises
the whole harness and qualifies nobody.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import lzma
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field

from badactors.castle.bots import HeuristicBot
from badactors.castle.budget import (
    Budget,
    BudgetExhausted,
    Limits,
    prompt_tokens,
)
from badactors.castle.cast import Cast, load_cast_file
from badactors.castle.configs import CAST, MAP, SUITE
from badactors.castle.contract_schema import compile_schema
from badactors.castle.contracts import CastleMap, Contract
from badactors.castle.decisions import Decision, ExitAnswer
from badactors.castle.harness import ModelPlayer
from badactors.castle.interview import ExitObservation
from badactors.castle.mapfile import load_map
from badactors.castle.mock_provider import BotProvider, mock_models
from badactors.castle.observe import GameObservation
from badactors.castle.play import (
    ERROR_LIMIT,
    RELIABILITY_FAILURES,
    RELIABILITY_GATE,
    BadAnswer,
    FailureReason,
    Outcome,
    TurnFailure,
    run_game,
    start_game,
)
from badactors.castle.prompt import render
from badactors.castle.provider import (
    CallRef,
    HarnessLog,
    ModeBook,
    ModelInfo,
    OpenRouterProvider,
    OperatorError,
    Provider,
    load_models,
)
from badactors.castle.rng import draw
from badactors.castle.rules import GameConfig
from badactors.castle.verify import HashingProvider

_CACHE = Path.home() / ".cache" / "badactors" / "openrouter-models.json"

SUITE_PATH = SUITE
SUITE_SIZE = 300
SUITE_SEEDS = 8
"""Bot games the suite is sampled from, on seeds 0 to 7."""
FLOOR = 6
"""Every kind and role gets at least this many questions, or all there are."""
MIN_TURNS = 250
"""The gate decides nothing on fewer turns than this."""


# ------------------------------------------------------------------ the suite


class SuiteItem(Contract):
    """One question of the suite: an observation a seat was shown in a bot
    game, and where it came from."""

    seed: int
    ordinal: int = Field(ge=0)
    """Which observation of that game, counting from zero across every seat."""
    observation: GameObservation

    @property
    def stratum(self) -> str:
        return f"{self.observation.request.kind}/{self.observation.role}"


class Suite(Contract):
    version: Literal[1] = 1
    seeds: list[int]
    items: list[SuiteItem]

    @property
    def digest(self) -> str:
        """SHA-256 of the items: what a result says it answered."""
        canonical = json.dumps(
            [item.model_dump(mode="json") for item in self.items],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def strata(self) -> Counter[str]:
        return Counter(item.stratum for item in self.items)


def sampled_games(
    castle: CastleMap, cast: Cast, seeds: Iterable[int], config: GameConfig
) -> list[SuiteItem]:
    """Every observation the seeded bot was shown, game by game, in order."""
    out: list[SuiteItem] = []
    for seed in seeds:
        watching = _Watching(HeuristicBot(seed=seed))
        game = start_game(castle, cast, seed=seed, config=config)
        run_game(game, dict.fromkeys(game.world.seats, watching), check_leaks=False)
        out.extend(
            SuiteItem(seed=seed, ordinal=index, observation=observation)
            for index, observation in enumerate(watching.shown)
        )
    return out


@dataclass
class _Watching:
    """The seeded bot, keeping every observation it is shown."""

    bot: HeuristicBot
    shown: list[GameObservation] = field(default_factory=list)
    agent: str = "bot:heuristic"

    def __call__(self, observation: GameObservation) -> Decision:
        self.shown.append(observation)
        return self.bot(observation)

    def interview(self, departing: ExitObservation) -> ExitAnswer:
        return self.bot.interview(departing)


def allocate(counts: Mapping[str, int], size: int, floor: int) -> dict[str, int]:
    """How many questions each stratum gets: ``floor`` each (or all it has),
    the rest in proportion to how often the game asks it, by largest
    remainder."""
    base = {key: min(count, floor) for key, count in counts.items()}
    left = size - sum(base.values())
    spare = {key: counts[key] - base[key] for key in counts}
    total = sum(spare.values())
    if left <= 0 or total == 0:
        return base
    if total <= left:
        return dict(counts)
    shares = {key: left * spare[key] / total for key in counts}
    take = {key: base[key] + int(shares[key]) for key in counts}
    rest = left - sum(int(share) for share in shares.values())
    for key in sorted(counts, key=lambda k: (int(shares[k]) - shares[k], k))[:rest]:
        take[key] += 1
    return take


def build_suite(
    castle: CastleMap,
    cast: Cast,
    *,
    seeds: Sequence[int] = tuple(range(SUITE_SEEDS)),
    size: int = SUITE_SIZE,
    config: GameConfig | None = None,
) -> Suite:
    """Sample the suite from bot games, stratified by request kind and role,
    each stratum picked by rolls drawn from its name."""
    pool = sampled_games(castle, cast, seeds, config or GameConfig.shipped())
    by_stratum: dict[str, list[SuiteItem]] = defaultdict(list)
    for item in pool:
        by_stratum[item.stratum].append(item)
    counts = {key: len(items) for key, items in by_stratum.items()}
    chosen: list[SuiteItem] = []
    for key, take in sorted(allocate(counts, size, FLOOR).items()):
        items = list(by_stratum[key])
        for index in range(len(items) - 1, 0, -1):
            pick = int(draw(0, index, f"qualify:{key}").value * (index + 1))
            items[index], items[pick] = items[pick], items[index]
        chosen.extend(items[:take])
    chosen.sort(key=lambda item: (item.seed, item.ordinal))
    return Suite(seeds=list(seeds), items=chosen)


def save_suite(suite: Suite, path: str | Path = SUITE_PATH) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = suite.model_dump_json()
    target.write_bytes(lzma.compress(text.encode(), preset=9 | lzma.PRESET_EXTREME))


def load_suite(path: str | Path = SUITE_PATH) -> Suite:
    return Suite.model_validate_json(lzma.decompress(Path(path).read_bytes()))


# ------------------------------------------------------------------ the run


@dataclass(frozen=True)
class Turn:
    """One question, however it ended."""

    index: int
    stratum: str
    attempts: int
    failure: FailureReason | None


class Qualification(Contract):
    """One model's qualification run, and whether it passed the gate."""

    model: str
    provider: str
    mock: bool
    suite_version: int
    suite_digest: str
    questions: int
    outcome: Outcome
    turns: int
    """Questions that ended: answered, or failed for any reason."""
    answered: int
    retried: int
    """Turns whose first answer could not be used and were asked again."""
    failed: dict[str, int]
    """Failed turns by reason class."""
    reliability_failures: int
    refusals: int
    content_filters: int
    rate: float | None
    """Reliability failures over turns."""
    qualified: bool | None
    """``None`` when the run decides nothing: under ``MIN_TURNS`` turns, an
    aborted run, or a mock one."""
    by_stratum: dict[str, dict[str, int]]
    spent: dict[str, int | float]
    """Requests, tokens and dollars."""

    def summary(self) -> str:
        failed = ", ".join(f"{n} {why}" for why, n in sorted(self.failed.items()))
        verdict = (
            "a mock run qualifies nobody"
            if self.mock
            else "decides nothing"
            if self.qualified is None
            else "QUALIFIES"
            if self.qualified
            else "does NOT qualify"
        )
        rate = "n/a" if self.rate is None else f"{self.rate:.1%}"
        lines = [
            f"{self.model} ({self.provider}): {self.outcome}, {self.turns} of "
            f"{self.questions} turns; reliability failures {self.reliability_failures}"
            f" ({rate}, gate {RELIABILITY_GATE:.0%}, at least {MIN_TURNS} turns): "
            f"{verdict}.",
            f"  Failed turns: {failed or 'none'}. Refusals {self.refusals} and "
            f"content filters {self.content_filters} are results and count apart. "
            f"{self.retried} turns needed the retry.",
            f"  Suite v{self.suite_version} {self.suite_digest[:16]}. Spent "
            f"{int(self.spent['requests'])} requests, "
            f"{int(self.spent['tokens']):,} tokens, ${self.spent['dollars']:.4f}.",
        ]
        return "\n".join(lines)


def tally(
    turns: Sequence[Turn],
    *,
    suite: Suite,
    model: str,
    provider: str,
    mock: bool,
    outcome: Outcome,
    budget: Budget,
) -> Qualification:
    failed = Counter(turn.failure for turn in turns if turn.failure is not None)
    broken = sum(failed[reason] for reason in RELIABILITY_FAILURES)
    rate = broken / len(turns) if turns else None
    decided = outcome == "finished" or (
        outcome == "stopped:budget" and len(turns) >= MIN_TURNS
    )
    qualified = (
        None
        if mock or rate is None or not decided or len(turns) < MIN_TURNS
        else rate <= RELIABILITY_GATE
    )
    strata: dict[str, Counter[str]] = defaultdict(Counter)
    for turn in turns:
        strata[turn.stratum]["turns"] += 1
        if turn.failure in RELIABILITY_FAILURES:
            strata[turn.stratum]["failed"] += 1
        elif turn.failure is not None:
            strata[turn.stratum]["refused"] += 1
    spent = budget.spent
    return Qualification(
        model=model,
        provider=provider,
        mock=mock,
        suite_version=suite.version,
        suite_digest=suite.digest,
        questions=len(suite.items),
        outcome=outcome,
        turns=len(turns),
        answered=sum(1 for turn in turns if turn.failure is None),
        retried=sum(1 for turn in turns if turn.attempts == 2),
        failed={str(why): n for why, n in sorted(failed.items())},
        reliability_failures=broken,
        refusals=failed["refusal"],
        content_filters=failed["content_filter"],
        rate=None if rate is None else round(rate, 6),
        qualified=qualified,
        by_stratum={key: dict(found) for key, found in sorted(strata.items())},
        spent={
            "requests": spent.requests,
            "tokens": spent.tokens,
            "dollars": round(spent.dollars, 6),
        },
    )


async def ask(player: ModelPlayer, item: SuiteItem, index: int, game: str) -> Turn:
    """Put one question, and once more with the error if the answer could not
    be used."""
    observation = item.observation
    for attempt in (1, 2):
        ref = CallRef(
            game_id=game,
            seat=observation.viewer_id,
            model=player.model,
            kind=observation.request.kind,
            day=observation.day,
            tick=observation.world.tick,
            ordinal=index,
            attempt=attempt,
        )
        try:
            await player.answer(player.question(observation, ref))
        except BadAnswer as exc:
            if attempt == 2:
                return Turn(index, item.stratum, attempt, exc.reason)
            error = (str(exc) or type(exc).__name__)[:ERROR_LIMIT]
            request = observation.request.model_copy(update={"refusal": error})
            observation = observation.model_copy(update={"request": request})
            continue
        except TurnFailure as exc:
            return Turn(index, item.stratum, attempt, exc.reason)
        return Turn(index, item.stratum, attempt, None)
    raise AssertionError("unreachable")  # pragma: no cover


def qualify(
    suite: Suite,
    model: str,
    info: ModelInfo,
    provider: Provider,
    *,
    limits: Limits,
    provider_name: str,
    mock: bool = False,
    max_tokens: int = 4096,
    concurrency: int = 4,
    log: HarnessLog | None = None,
    closers: Sequence[Callable[[], Awaitable[None]]] = (),
) -> Qualification:
    """Put every question of the suite to one model, and read the gate."""
    harness = log if log is not None else HarnessLog()
    budget = Budget(limits)
    player = ModelPlayer(
        seat="qualify",
        model=model,
        info=info,
        provider=provider,
        modes=ModeBook.starting({model: info}, harness),
        budget=budget,
        log=harness,
        max_tokens=max_tokens,
    )
    game = f"qualify-{suite.digest[:12]}"
    outcome: Outcome = "finished"

    async def run() -> list[Turn]:
        nonlocal outcome
        slots = asyncio.Semaphore(concurrency)
        stop = asyncio.Event()

        async def one(index: int, item: SuiteItem) -> Turn | None:
            nonlocal outcome
            async with slots:
                if stop.is_set():
                    return None
                try:
                    return await ask(player, item, index, game)
                except BudgetExhausted as exc:
                    if outcome == "finished":
                        outcome = "stopped:budget"
                        harness.write("stopped", outcome=outcome, why=str(exc)[:300])
                except OperatorError as exc:
                    outcome = "aborted:operator"
                    harness.write("stopped", outcome=outcome, status=exc.status)
                stop.set()
                return None

        try:
            found = await asyncio.gather(
                *(one(index, item) for index, item in enumerate(suite.items))
            )
        finally:
            for close in closers:
                await close()
        return [turn for turn in found if turn is not None]

    turns = asyncio.run(run())
    result = tally(
        turns,
        suite=suite,
        model=model,
        provider=provider_name,
        mock=mock,
        outcome=outcome,
        budget=budget,
    )
    harness.write(
        "qualified",
        model=model,
        outcome=outcome,
        turns=result.turns,
        rate=result.rate,
        qualified=result.qualified,
    )
    return result


@dataclass(frozen=True)
class Estimate:
    requests: int
    prompt_tokens: int
    completion_tokens: int
    dollars: float
    worst_dollars: float
    """Every question asked twice, and every answer at its token limit."""

    def fits(self, limits: Limits) -> bool:
        return (
            self.requests <= limits.max_requests
            and self.prompt_tokens + self.completion_tokens <= limits.max_tokens
            and self.dollars <= limits.max_dollars
        )

    def summary(self) -> str:
        return (
            f"Preflight: {self.requests} questions, about {self.prompt_tokens:,} "
            f"prompt and {self.completion_tokens:,} completion tokens, "
            f"${self.dollars:.2f}, if each is answered once; "
            f"${self.worst_dollars:.2f} if every one is asked twice and runs to its "
            "token limit. Cached prefixes cost less."
        )


def estimate(
    suite: Suite,
    model: str,
    info: ModelInfo,
    *,
    max_tokens: int,
    answer_tokens: int = 400,
) -> Estimate:
    prompt = dollars = worst = 0.0
    for item in suite.items:
        rendered = render(item.observation, recipient=model)
        tokens = prompt_tokens(
            rendered, len(json.dumps(compile_schema(item.observation)))
        )
        prompt += tokens
        dollars += info.worst_case(tokens, answer_tokens)
        worst += 2 * info.worst_case(tokens, max_tokens)
    count = len(suite.items)
    return Estimate(count, int(prompt), count * answer_tokens, dollars, worst)


# ------------------------------------------------------------------ the command


def result_path(out: Path, model: str) -> Path:
    return out / f"{model.replace('/', '--')}.json"


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="castle-qualify",
        description="Put the frozen qualification suite to one model, within a "
        "budget, and read the reliability gate.",
    )
    parser.add_argument("model", nargs="?", help="The model id to qualify.")
    parser.add_argument("--suite", default=str(SUITE_PATH))
    parser.add_argument(
        "--build",
        action="store_true",
        help="Rebuild the suite from bot games. Results from two suites differ.",
    )
    parser.add_argument("--cast", default=str(CAST))
    parser.add_argument("--map", default=str(MAP))
    budget = parser.add_argument_group("budget (all three required)")
    budget.add_argument("--max-requests", type=int)
    budget.add_argument("--max-tokens", type=int)
    budget.add_argument("--max-dollars", type=float)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--out", default="out/qualify")
    parser.add_argument("--models-cache", default=str(_CACHE))
    parser.add_argument("--models-ttl-hours", type=float, default=24.0)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Estimate only.")
    parser.add_argument(
        "--mock", action="store_true", help="The seeded bot behind a mock provider."
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.build:
        suite = build_suite(load_map(args.map), load_cast_file(args.cast))
        save_suite(suite, args.suite)
        strata = ", ".join(f"{key} {n}" for key, n in sorted(suite.strata().items()))
        print(
            f"Wrote {args.suite}: {len(suite.items)} questions from seeds "
            f"{suite.seeds[0]}-{suite.seeds[-1]} ({strata}); digest {suite.digest}."
        )
        return
    if not args.model:
        sys.exit("Name the model to qualify, or --build the suite.")
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
        sys.exit(f"castle-qualify will not start without {', '.join(missing)}.")
    limits = Limits(args.max_requests, args.max_tokens, args.max_dollars)
    suite = load_suite(args.suite)
    info = _info(args)
    found = estimate(suite, args.model, info, max_tokens=args.max_output_tokens)
    print(found.summary())
    if args.dry_run:
        return
    if not found.fits(limits) and not args.allow_partial:
        sys.exit(
            "The estimate does not fit the budget; raise it, or pass "
            "--allow-partial to ask until it runs out."
        )
    out = Path(args.out)
    path = result_path(out, args.model)
    log = HarnessLog(path.with_suffix(".harness.jsonl"))
    provider: Provider
    closers: list[Callable[[], Awaitable[None]]] = []
    if args.mock:
        provider = BotProvider(HeuristicBot(seed=0))
    else:
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            sys.exit("Set OPENROUTER_API_KEY to qualify a real model.")
        openrouter = OpenRouterProvider(api_key=key, timeout=args.timeout)
        closers.append(openrouter.aclose)
        provider = openrouter
    name = "mock:bot" if args.mock else "openrouter"
    result = qualify(
        suite,
        args.model,
        info,
        HashingProvider(provider, log, name),
        limits=limits,
        provider_name=name,
        mock=args.mock,
        max_tokens=args.max_output_tokens,
        concurrency=args.concurrency,
        log=log,
        closers=closers,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=1))
    print(result.summary())
    print(f"Wrote {path} and the harness log beside it.")


def _info(args: argparse.Namespace) -> ModelInfo:
    if args.mock:
        return mock_models([args.model])[args.model]
    known = asyncio.run(
        load_models(Path(args.models_cache), ttl=args.models_ttl_hours * 3600)
    )
    if args.model not in known:
        sys.exit(f"OpenRouter does not list {args.model}.")
    return known[args.model]


if __name__ == "__main__":
    main()


__all__ = [
    "MIN_TURNS",
    "SUITE_PATH",
    "Qualification",
    "Suite",
    "SuiteItem",
    "Turn",
    "allocate",
    "ask",
    "build_suite",
    "estimate",
    "load_suite",
    "main",
    "qualify",
    "result_path",
    "sampled_games",
    "save_suite",
    "tally",
]
