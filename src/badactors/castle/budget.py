"""Money and calls: a live game never spends more than it was given.

Every live command takes three limits: requests, tokens and dollars. Before
each call the harness reserves that call's worst case — the prompt, estimated
generously from its length, plus every token it may generate, at the model's
price — and a call that would not fit is never made: the game stops there,
cleanly, marked ``stopped:budget`` and unrated, with a partial record that
replays to the same point. When the reply comes back the reservation is
settled at what it actually cost, from the usage the provider reports.

:func:`preflight` estimates a whole game before anything is spent, by playing
it once with the seeded bot in every seat and rendering every question each
seat would have been asked, through the same renderer the models read.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from badactors.castle.bots import HeuristicBot
from badactors.castle.cast import Cast
from badactors.castle.contract_schema import compile_exit_schema, compile_schema
from badactors.castle.contracts import CastleMap
from badactors.castle.decisions import Decision, ExitAnswer
from badactors.castle.interview import ExitObservation
from badactors.castle.observe import GameObservation
from badactors.castle.play import run_game, start_game
from badactors.castle.prompt import Rendered, render, render_exit
from badactors.castle.provider import ModelInfo, Usage
from badactors.castle.rules import GameConfig

CHARS_PER_TOKEN = 3.0
"""A generous guess at prompt tokens from characters. English prose runs near
four characters a token; JSON and ids run nearer three, so three overestimates
the prompt, which is the safe direction for a reservation."""


def prompt_tokens(rendered: Rendered, schema_chars: int = 0) -> int:
    """A generous estimate of a prompt's tokens, schema included."""
    return math.ceil((len(rendered.text) + schema_chars) / CHARS_PER_TOKEN) + 16


class BudgetExhausted(Exception):
    """The next call would not fit in what is left. The game stops here."""


@dataclass(frozen=True)
class Limits:
    max_requests: int
    max_tokens: int
    max_dollars: float

    def __post_init__(self) -> None:
        if self.max_requests < 1 or self.max_tokens < 1 or self.max_dollars <= 0:
            raise ValueError("Every budget limit must be positive.")


@dataclass
class Spend:
    requests: int = 0
    tokens: int = 0
    dollars: float = 0.0

    def plus(self, other: Spend) -> Spend:
        return Spend(
            self.requests + other.requests,
            self.tokens + other.tokens,
            self.dollars + other.dollars,
        )


@dataclass(frozen=True)
class Reservation:
    model: str
    worst: Spend


@dataclass
class Budget:
    """What a run may spend, what it has spent, and what is in flight."""

    limits: Limits
    spent: Spend = field(default_factory=Spend)
    held: Spend = field(default_factory=Spend)

    def reserve(self, info: ModelInfo, prompt: int, max_tokens: int) -> Reservation:
        """Hold one call's worst case, or raise :class:`BudgetExhausted`."""
        worst = Spend(1, prompt + max_tokens, info.worst_case(prompt, max_tokens))
        after = self.spent.plus(self.held).plus(worst)
        limits = self.limits
        if after.requests > limits.max_requests:
            raise BudgetExhausted(
                f"The request limit ({limits.max_requests}) is spent."
            )
        if after.tokens > limits.max_tokens:
            raise BudgetExhausted(
                f"The next call could take {worst.tokens} tokens; only "
                f"{limits.max_tokens - self.spent.tokens - self.held.tokens} are left."
            )
        if after.dollars > limits.max_dollars + 1e-12:
            raise BudgetExhausted(
                f"The next call could cost ${worst.dollars:.4f}; only "
                f"${limits.max_dollars - self.spent.dollars - self.held.dollars:.4f} "
                "is left."
            )
        self.held = self.held.plus(worst)
        return Reservation(info.id, worst)

    def settle(
        self, reservation: Reservation, info: ModelInfo, usage: Usage | None
    ) -> Spend:
        """Release a reservation and charge what the call cost. ``None`` means
        the outcome is unknown (a timeout), so the worst case is charged."""
        self.held = self.held.plus(_negative(reservation.worst))
        if usage is None:
            cost = reservation.worst
        else:
            tokens = usage.prompt_tokens + usage.completion_tokens
            dollars = (
                usage.cost
                if usage.cost is not None
                else usage.prompt_tokens * info.prompt_price
                + usage.completion_tokens * info.completion_price
                + info.request_price
            )
            cost = Spend(1, tokens, dollars)
        self.spent = self.spent.plus(cost)
        return cost

    def release(self, reservation: Reservation) -> None:
        """A call that was refused before it was answered (no charge but the
        request itself)."""
        self.held = self.held.plus(_negative(reservation.worst))
        self.spent = self.spent.plus(Spend(1, 0, 0.0))


def _negative(spend: Spend) -> Spend:
    return Spend(-spend.requests, -spend.tokens, -spend.dollars)


# --------------------------------------------------------------- preflight


@dataclass(frozen=True)
class Estimate:
    requests: int
    prompt_tokens: int
    completion_tokens: int
    dollars: float
    worst_dollars: float
    by_kind: Mapping[str, int]

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def fits(self, limits: Limits) -> bool:
        return (
            self.requests <= limits.max_requests
            and self.tokens <= limits.max_tokens
            and self.dollars <= limits.max_dollars
        )

    def summary(self) -> str:
        kinds = ", ".join(f"{kind} {count}" for kind, count in self.by_kind.items())
        return (
            f"Preflight, from one bot rehearsal of this game: {self.requests} "
            f"requests ({kinds}); about {self.prompt_tokens:,} prompt and "
            f"{self.completion_tokens:,} completion tokens; about "
            f"${self.dollars:.2f}, ${self.worst_dollars:.2f} if every answer ran to "
            "its token limit. Retries, and models that talk longer than the bot, "
            "cost more; cached prefixes cost less."
        )


def preflight(
    castle: CastleMap,
    cast: Cast,
    models: Mapping[str, ModelInfo],
    *,
    seed: int,
    config: GameConfig,
    max_tokens: int,
    answer_tokens: int = 400,
) -> Estimate:
    """Estimate a game's cost by playing it with the bot in every seat and
    pricing every question as its seat's model would be asked it."""
    seat_model = cast.models()
    game = start_game(castle, cast, seed=seed, config=config)
    bot = HeuristicBot(seed=seed)
    kinds: Counter[str] = Counter()
    prompt = 0
    dollars = 0.0
    worst = 0.0

    def price(seat: str, rendered: Rendered, schema_chars: int, kind: str) -> None:
        nonlocal prompt, dollars, worst
        info = models[seat_model[seat]]
        tokens = prompt_tokens(rendered, schema_chars)
        kinds[kind] += 1
        prompt += tokens
        dollars += info.worst_case(tokens, answer_tokens)
        worst += info.worst_case(tokens, max_tokens)

    class Rehearsal:
        agent = "bot:heuristic"

        def __call__(self, observation: GameObservation) -> Decision:
            seat = observation.viewer_id
            rendered = render(observation, recipient=seat_model[seat])
            schema = json.dumps(compile_schema(observation))
            price(seat, rendered, len(schema), observation.request.kind)
            return bot(observation)

        def interview(self, departing: ExitObservation) -> ExitAnswer:
            seat = departing.viewer_id
            rendered = render_exit(departing, recipient=seat_model[seat])
            schema = json.dumps(compile_exit_schema(departing))
            price(seat, rendered, len(schema), departing.request.kind)
            return bot.interview(departing)

    rehearsal = Rehearsal()
    run_game(game, {seat: rehearsal for seat in game.world.seats}, check_leaks=False)
    requests = sum(kinds.values())
    return Estimate(
        requests=requests,
        prompt_tokens=prompt,
        completion_tokens=requests * answer_tokens,
        dollars=dollars,
        worst_dollars=worst,
        by_kind=dict(kinds.most_common()),
    )


__all__ = [
    "CHARS_PER_TOKEN",
    "Budget",
    "BudgetExhausted",
    "Estimate",
    "Limits",
    "Reservation",
    "Spend",
    "preflight",
    "prompt_tokens",
]
