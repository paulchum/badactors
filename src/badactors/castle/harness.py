"""Real models in the Castle: ask once, read what comes back, coerce nothing.

:class:`ModelPlayer` puts one question to one model: it renders the player's
observation (``prompt``), compiles the request's schema (``contract_schema``),
reserves each call's worst case (``budget``), and asks the provider in the model's
current mode (``provider``). A mandatory-reasoning rejection under ``least`` may
trigger a new, separately budgeted and receipted call with minimal reasoning.
What the model answers is read, never argued with:

* a provider's content filter, or a refusal in the model's own words, fails the
  turn at once as ``content_filter`` or ``refusal``. They are results;
* no reply at all fails it as ``transport``;
* an empty answer, one that is not JSON, or one that is not a legal answer is a
  :class:`~badactors.castle.play.BadAnswer`. The referee then asks once more,
  naming the exact error and restating every legal answer, passing and silence
  included, in the same words for every request; a second bad answer fails the
  turn as ``empty``, ``unparseable`` or ``invalid``.

Nothing is ever substituted for a failed answer: the character hesitates and
does nothing, labelled as a failure.

:class:`LiveReferee` is the referee with models in it. The engine stays
synchronous; the referee owns one event loop and runs each call to completion,
except for plan prompts. The plans asked for in one prompt round never depend
on one another — a plan's only trace is a record its own author holds — so the
referee starts them all at once (``asyncio.gather``, at most so many calls per
model at a time), and then lets the round run in seat order as always. Each
seat's answer is used only if the observation its turn builds is the one the
answer was given to, so the ledger is exactly the one a one-at-a-time game
would have written, whatever order the replies arrived in. Table speech,
votes, doses, offers, nominations and interviews are asked one at a time.

When the budget cannot cover the next call the game stops there, marked
``stopped:budget``; a 401, 402 or 403 aborts it as ``aborted:operator``. Either
way the partial record replays to the same point, and is never rated.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError

from badactors.castle.budget import (
    Budget,
    BudgetExhausted,
    Limits,
    Reservation,
    prompt_tokens,
)
from badactors.castle.cast import Cast
from badactors.castle.contract_schema import (
    ROOT,
    Schema,
    compile_exit_schema,
    compile_schema,
    validate,
)
from badactors.castle.contracts import CastleMap
from badactors.castle.decisions import (
    AUDIENCE_ONLY,
    Decision,
    ExitAnswer,
    IllegalDecision,
    Request,
    check_decision,
)
from badactors.castle.fouls import ContentScope, ScopeCheck
from badactors.castle.game import GameState, fork_game
from badactors.castle.interview import ExitObservation, exit_observation
from badactors.castle.observe import (
    GameObservation,
    assert_no_game_leak,
    observe_game,
    observed,
)
from badactors.castle.play import (
    UNRATED_ARMS,
    ArmKind,
    BadAnswer,
    Defused,
    GameRecord,
    Outcome,
    Played,
    Player,
    Referee,
    TurnFailure,
    plan_request,
    record,
    start_game,
)
from badactors.castle.prompt import Rendered, assert_render_clean, render, render_exit
from badactors.castle.provider import (
    REASONING,
    CallRef,
    Closer,
    HarnessLog,
    Mode,
    ModeBook,
    ModelInfo,
    OperatorError,
    ParameterRejected,
    Provider,
    ProviderCall,
    ReasoningRequired,
    Reply,
    TransportError,
    Usage,
)
from badactors.castle.rules import GameConfig

DECISION: TypeAdapter[Decision] = TypeAdapter(Decision)

_REFUSAL = re.compile(
    r"^\W*(?:(?:I'm|I am) sorry|I apologi[sz]e|Sorry)?[\s,.!]*(?:but\s+)?"
    r"I(?:\s+(?:can(?:no|')t|won't|will not|must|would rather not)|'d rather not|"
    r"'m (?:not able|unable|not comfortable|not going)|"
    r"\s+am (?:not able|unable|not comfortable|not going))"
    r"[^.]{0,60}?\b(?:help|assist|participate|take part|play|continue|comply|"
    r"do (?:that|this)|engage|provide|role-?play|pretend|deceive|lie|decline)",
    re.IGNORECASE,
)
"""A refusal in prose: an answer that opens by declining the task. Words in
character ("I can't believe Mara...") are not one."""
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


@dataclass(frozen=True)
class Question:
    """One question as one model is asked it."""

    ref: CallRef
    rendered: Rendered
    schema: Schema
    request: Request
    source: GameObservation | ExitObservation


@dataclass
class ReasoningBook:
    """The run's reasoning policy, shared by every seat of each model."""

    setting: str = "default"
    minimal: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.setting not in ("default", "least") and self.setting not in REASONING:
            raise ValueError(f"unknown reasoning setting {self.setting!r}")

    def for_model(self, model: str) -> str:
        if self.setting != "least":
            return self.setting
        return "minimal" if model in self.minimal else "off"

    def fallback(self, model: str, rejected: str) -> bool:
        # Several off requests can be in flight when the first rejection arrives.
        # Each may retry once, with its own reservation and minimal setting.
        if self.setting != "least" or rejected != "off":
            return False
        self.minimal.add(model)
        return True


@dataclass
class ModelPlayer:
    """One seat, played by one model through one provider."""

    seat: str
    model: str
    info: ModelInfo
    provider: Provider
    modes: ModeBook
    budget: Budget
    log: HarnessLog
    max_tokens: int = 4096
    slots: asyncio.Semaphore | None = None
    """Shared by every seat of the same model: how many of its calls may be in
    flight at once."""
    mock: bool = False
    """The provider is the mock one: the seeded bot answers for the model."""
    reasoning: ReasoningBook = field(default_factory=ReasoningBook)
    attempts: dict[int, int] = field(default_factory=dict, init=False, repr=False)

    @property
    def agent(self) -> str:
        return f"{'mock' if self.mock else 'model'}:{self.model}"

    def __call__(self, observation: GameObservation) -> Decision:
        raise RuntimeError("A model seat is asked through the LiveReferee.")

    # ----------------------------------------------------------- questions

    def question(self, observation: GameObservation, ref: CallRef) -> Question:
        return Question(
            ref=ref,
            rendered=render(observation, recipient=self.model),
            schema=compile_schema(observation),
            request=observation.request,
            source=observation,
        )

    def exit_question(self, departing: ExitObservation, ref: CallRef) -> Question:
        return Question(
            ref=ref,
            rendered=render_exit(departing, recipient=self.model),
            schema=compile_exit_schema(departing),
            request=departing.request,
            source=departing,
        )

    def reserve(self, question: Question) -> Reservation:
        schema = len(json.dumps(question.schema))
        prompt = prompt_tokens(question.rendered, schema)
        return self.budget.reserve(self.info, prompt, self.max_tokens)

    # -------------------------------------------------------------- asking

    async def answer(
        self, question: Question, held: Reservation | None = None
    ) -> Decision:
        """Ask one question, with a budgeted reasoning fallback if needed.
        Raises ``TurnFailure`` or ``BadAnswer``
        for an answer that cannot be used, ``BudgetExhausted`` before a call
        that would not fit, and ``OperatorError`` for the run's own failures."""
        mode = self.modes.mode(self.model)
        while True:
            held = held if held is not None else self.reserve(question)
            ordinal = question.ref.ordinal
            attempt = max(question.ref.attempt, self.attempts.get(ordinal, 0) + 1)
            self.attempts[ordinal] = attempt
            call = ProviderCall(
                ref=replace(question.ref, attempt=attempt),
                messages=question.rendered.messages,
                schema=question.schema,
                mode=mode,
                max_tokens=self.max_tokens,
                source=question.source,
                reasoning=self.reasoning.for_model(self.model),
            )
            try:
                reply = await self._call(call)
            except ReasoningRequired as exc:
                self.budget.settle(held, self.info, Usage())
                if not self.reasoning.fallback(self.model, call.reasoning):
                    self._failed(call, "transport", str(exc))
                    raise TurnFailure(str(exc), "transport") from exc
                self.log.write(
                    "reasoning_fallback",
                    ref=_ref(call.ref),
                    model=self.model,
                    setting="minimal",
                )
                held = None
                continue
            except OperatorError as exc:
                self.budget.release(held)
                self.log.write("operator_error", ref=_ref(call.ref), status=exc.status)
                raise
            except ParameterRejected as exc:
                self.budget.settle(held, self.info, Usage())
                self.modes.rejected(self.model, mode, str(exc))
                self._failed(call, "transport", str(exc))
                raise TurnFailure(str(exc), "transport") from exc
            except TransportError as exc:
                known = Usage() if exc.status is not None else None
                self.budget.settle(held, self.info, known)
                self._failed(call, "transport", str(exc))
                raise TurnFailure(str(exc), "transport") from exc
            cost = self.budget.settle(held, self.info, reply.usage)
            self.log.write(
                "call",
                ref=_ref(call.ref),
                mode=mode,
                finish_reason=reply.finish_reason,
                prompt_tokens=reply.usage.prompt_tokens,
                completion_tokens=reply.usage.completion_tokens,
                dollars=cost.dollars,
            )
            try:
                return self.read(reply, question, mode)
            except (TurnFailure, BadAnswer) as exc:
                self._failed(call, exc.reason, str(exc))
                raise

    async def _call(self, call: ProviderCall) -> Reply:
        if self.slots is None:
            return await self.provider.complete(call)
        async with self.slots:
            return await self.provider.complete(call)

    def _failed(self, call: ProviderCall, reason: str, error: str) -> None:
        self.log.write(
            "bad_answer",
            ref=_ref(call.ref),
            mode=call.mode,
            reason=reason,
            error=error[:300],
        )

    def read(self, reply: Reply, question: Question, mode: Mode) -> Decision:
        """What the reply says, as a decision, or why it is not one."""
        if reply.finish_reason == "content_filter":
            raise TurnFailure(
                "The provider's content filter stopped the answer.", "content_filter"
            )
        if reply.refusal is not None:
            raise TurnFailure(f"The model declined: {reply.refusal[:160]}", "refusal")
        text = reply.arguments if reply.arguments is not None else reply.content
        if text is None or not text.strip():
            cut = (
                " It ran out of tokens first."
                if reply.finish_reason == "length"
                else ""
            )
            raise BadAnswer(f"The answer was empty.{cut}", "empty")
        value, problem = _json(text)
        if problem is not None:
            if "{" not in text and _REFUSAL.match(text):
                raise TurnFailure(
                    f"The model declined: {text.strip()[:160]}", "refusal"
                )
            self.modes.answered(self.model, mode, schema_valid=False)
            raise BadAnswer(
                f"The answer was not a JSON object: {problem}", "unparseable"
            )
        if isinstance(value, dict) and ROOT not in value and "kind" in value:
            value = {ROOT: value}
        errors = validate(question.schema, value)
        if errors:
            self.modes.answered(self.model, mode, schema_valid=False)
            raise BadAnswer("; ".join(errors[:3]), "invalid")
        self.modes.answered(self.model, mode, schema_valid=True)
        try:
            decision = DECISION.validate_json(json.dumps(value[ROOT]))
        except ValidationError as exc:
            raise BadAnswer(_errors(exc), "invalid") from exc
        try:
            check_decision(question.request, decision)
        except IllegalDecision as exc:
            raise BadAnswer(str(exc), "invalid") from exc
        return decision


def _json(text: str) -> tuple[Any, str | None]:
    """The JSON object in ``text``, allowing a code fence or words round it."""
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1)
    try:
        return json.loads(stripped), None
    except json.JSONDecodeError as exc:
        problem = str(exc)
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(stripped[start : end + 1]), None
        except json.JSONDecodeError as exc:
            problem = str(exc)
    return None, problem


def _errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{ROOT}." + ".".join(str(part) for part in error["loc"]) + f": {error['msg']}"
        for error in exc.errors()[:3]
    )


def _ref(ref: CallRef) -> dict[str, object]:
    return {
        "seat": ref.seat,
        "model": ref.model,
        "kind": ref.kind,
        "day": ref.day,
        "tick": ref.tick,
        "ordinal": ref.ordinal,
        "attempt": ref.attempt,
    }


T = TypeVar("T")


@dataclass(frozen=True)
class _Early:
    """A plan answered ahead of its turn, and the observation it answered."""

    observation: GameObservation
    result: Decision | BaseException

    def take(self) -> Decision:
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@dataclass
class LiveReferee(Referee):
    """The referee, with model seats asked through providers."""

    game_id: str = "castle-live"
    harness: HarnessLog = field(default_factory=HarnessLog)
    concurrent: bool = True
    outcome: Outcome = "finished"
    stopped_by: str | None = None
    runner: asyncio.Runner = field(default_factory=asyncio.Runner, repr=False)
    closers: list[Closer] = field(default_factory=list, repr=False)
    ordinals: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    early: dict[str, _Early] = field(default_factory=dict, init=False, repr=False)
    reported: set[tuple[str, int]] = field(default_factory=set, init=False, repr=False)

    def run(self) -> GameState:
        try:
            return super().run()
        except BudgetExhausted as exc:
            self._stop("stopped:budget", str(exc))
        except OperatorError as exc:
            self._stop("aborted:operator", str(exc))
        return self.game

    def _stop(self, outcome: Outcome, why: str) -> None:
        self.outcome = outcome
        self.stopped_by = why
        self.harness.write("stopped", outcome=outcome, why=why[:300])

    def close(self) -> None:
        for closer in self.closers:
            self.runner.run(closer())
        self.runner.close()

    def _await(self, work: Coroutine[Any, Any, T]) -> T:
        return self.runner.run(work)

    # --------------------------------------------------------------- asking

    def ask(
        self, player_id: str, request: Request
    ) -> tuple[Decision | None, int | None]:
        try:
            return super().ask(player_id, request)
        finally:
            self.ordinals[player_id] = self.ordinals.get(player_id, 0) + 1

    def _ref(self, player: ModelPlayer, request: Request) -> CallRef:
        world = self.game.world
        return CallRef(
            game_id=self.game_id,
            seat=player.seat,
            model=player.model,
            kind=request.kind,
            day=world.day,
            tick=world.tick,
            ordinal=self.ordinals.get(player.seat, 0),
            attempt=2 if request.refusal is not None else 1,
        )

    def decide(self, player_id: str, request: Request) -> Decision:
        player = self.players.get(player_id)
        if not isinstance(player, ModelPlayer):
            return super().decide(player_id, request)
        ref = self._ref(player, request)
        if request.kind in AUDIENCE_ONLY:
            departing, self.seen = exit_observation(self.game, player_id, request)
            exit_answer = self._await(
                player.answer(player.exit_question(departing, ref))
            )
            if not isinstance(exit_answer, ExitAnswer):
                raise BadAnswer("An exit interview needs an exit answer.", "invalid")
            return exit_answer
        observation, question = self._prepare(player, request, ref)
        self.observations += 1
        self.seen = observed(observation)
        self.defused = self._forgeries(player_id, question.rendered)
        early = self.early.pop(player_id, None)
        if early is not None and ref.attempt == 1 and early.observation == observation:
            return early.take()
        return self._await(player.answer(question))

    def _prepare(
        self, player: ModelPlayer, request: Request, ref: CallRef
    ) -> tuple[GameObservation, Question]:
        """The observation and the prompt, both leak-checked before anything is
        sent."""
        observation = observe_game(self.game, player.seat, request)
        if self.check_leaks:
            assert_no_game_leak(observation, self.game, models=self.models)
        question = player.question(observation, ref)
        if self.check_leaks:
            assert_render_clean(
                question.rendered, self.game, player.seat, request, trusted=observation
            )
        return observation, question

    def _forgeries(self, player_id: str, rendered: Rendered) -> list[Defused]:
        """Forgeries defused for this player that have not been logged yet."""
        fresh = []
        for item in rendered.neutralised:
            if (player_id, item.seq) not in self.reported:
                self.reported.add((player_id, item.seq))
                fresh.append(Defused(seq=item.seq, markers=list(item.markers)))
        return fresh

    # ----------------------------------------------------------- prefetch

    def prefetch_plans(self, player_ids: Sequence[str]) -> None:
        self._discard_early()
        if not self.concurrent:
            return
        seats = [
            pid for pid in player_ids if isinstance(self.players.get(pid), ModelPlayer)
        ]
        if len(seats) < 2:
            return
        request = plan_request(self.game)
        jobs: list[tuple[str, GameObservation, Awaitable[Decision]]] = []
        for seat in seats:
            player = self.players[seat]
            assert isinstance(player, ModelPlayer)
            observation, question = self._prepare(
                player, request, self._ref(player, request)
            )
            try:
                held = player.reserve(question)
            except BudgetExhausted:
                break
            jobs.append((seat, observation, player.answer(question, held)))
        if not jobs:
            return
        self.harness.write("prefetch", seats=[seat for seat, _, _ in jobs])
        results = self._await(_gather([job for _, _, job in jobs]))
        for (seat, observation, _), result in zip(jobs, results, strict=True):
            self.early[seat] = _Early(observation, result)

    def _discard_early(self) -> None:
        for seat in self.early:
            self.harness.write("prefetch_unused", seat=seat)
        self.early.clear()


async def _gather(
    jobs: Sequence[Awaitable[Decision]],
) -> list[Decision | BaseException]:
    return list(await asyncio.gather(*jobs, return_exceptions=True))


# ---------------------------------------------------------------- live games


@dataclass
class LiveGame:
    """A finished (or stopped) live game: its record, and what it cost."""

    record: GameRecord
    referee: LiveReferee
    budget: Budget
    harness: HarnessLog


def play_live(
    castle: CastleMap,
    cast: Cast,
    *,
    seed: int,
    config: GameConfig,
    provider: Provider,
    models: Mapping[str, ModelInfo],
    limits: Limits,
    max_tokens: int = 4096,
    concurrency: int = 4,
    harness: HarnessLog | None = None,
    modes: ModeBook | None = None,
    reasoning: str = "default",
    scope: ContentScope | None = None,
    check_leaks: bool = True,
    concurrent: bool = True,
    closers: Sequence[Closer] = (),
    game_id: str | None = None,
    players: Mapping[str, Player] | None = None,
    mock: bool = False,
    world_id: str = "castle-game",
    arm: ArmKind | None = None,
) -> LiveGame:
    """Play one game with every seat's cast model, or the given ``players`` in
    the seats they name, and return its record. With ``mock``, the provider is
    the mock one: its seats are recorded as ``mock:<id>`` and never rated. A
    game played from a schedule passes its ``world_id`` and ``arm``, which the
    record keeps; a game of an arm that is never rated is not."""
    log = harness if harness is not None else HarnessLog()
    seat_models = cast.models()
    missing = sorted({model for model in seat_models.values() if model not in models})
    if missing:
        raise ValueError(f"No metadata for {', '.join(missing)}.")
    book = (
        modes
        if modes is not None
        else ModeBook.starting(
            {model: models[model] for model in seat_models.values()}, log
        )
    )
    budget = Budget(limits)
    thinking = ReasoningBook(reasoning)
    slots = {
        model: asyncio.Semaphore(concurrency) for model in set(seat_models.values())
    }
    seated: dict[str, Player] = {
        seat: ModelPlayer(
            seat=seat,
            model=model,
            info=models[model],
            provider=provider,
            modes=book,
            budget=budget,
            log=log,
            max_tokens=max_tokens,
            slots=slots[model],
            mock=mock,
            reasoning=thinking,
        )
        for seat, model in seat_models.items()
    }
    seated.update(players or {})
    game = start_game(castle, cast, seed=seed, config=config, world_id=world_id)
    referee = LiveReferee(
        fork_game(game),
        seated,
        check_leaks=check_leaks,
        models=tuple(sorted(set(seat_models.values()))),
        scope=scope if scope is not None else ScopeCheck(),
        game_id=game_id or f"{game.world.id}-{seed}",
        harness=log,
        concurrent=concurrent,
        closers=list(closers),
    )
    for model, mode in sorted(book.modes.items()):
        log.write("mode", model=model, mode=mode)
    log.write("reasoning", setting=reasoning)
    try:
        referee.run()
    finally:
        referee.close()
    played = Played(
        game=referee.game,
        log=referee.log,
        observations=referee.observations,
        agents={seat: referee.agent(seat) for seat in referee.game.world.seats},
    )
    stored = record(
        castle,
        cast,
        played,
        outcome=referee.outcome,
        rated=(
            referee.outcome == "finished"
            and not players
            and not mock
            and arm not in UNRATED_ARMS
        ),
        arm=arm,
    )
    log.write(
        "spent",
        requests=budget.spent.requests,
        tokens=budget.spent.tokens,
        dollars=budget.spent.dollars,
        outcome=referee.outcome,
    )
    return LiveGame(record=stored, referee=referee, budget=budget, harness=log)


__all__ = [
    "LiveGame",
    "LiveReferee",
    "ModelPlayer",
    "Question",
    "ReasoningBook",
    "play_live",
]
