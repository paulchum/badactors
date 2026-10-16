"""Run a game: ask the players, apply the rules, keep the record.

This is the only place a player is asked anything. Every question goes through
:meth:`Referee.ask`, which builds the player's scoped observation, checks it for
leaks, and logs the answer so the game can be replayed without the players. An
answer that does not parse, does not answer the question, or names a model is
refused privately and the question put once more, with the reason. A player that
still fails to answer — a transport error, a refusal, a second bad answer — gets
a visible ``turn_failed`` entry: the character hesitates and does nothing, and it
is never dressed up as a choice. The entry says why, by a reason class (see
:data:`FailureReason`). Words that seek real-world harm are blocked and logged as
a foul (``fouls``), and control tokens defused in a model's prompt are logged
for the audience; both replay from the log, like everything else.

A game stopped early — its budget spent, or the run aborted — keeps a partial
record that replays to exactly where it stopped.

Phases run on the world's clock. Missions and free time open with a plan from
everyone and re-prompt on interruption, exactly as the phase-1 director does;
gatherings route everyone to the table and let them plan once they arrive; the
night is supervised, with nobody asked for a plan while the house sleeps. The set
pieces — the mission's bell, the round table, the dinner seating, the conclave —
run at the end of their phase, or at the night tick the map schedules them for.
As each phase opens, anything stolen since the last one is missed and reported.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import Field, ValidationError, model_validator

from badactors.castle.anonymity import Aliases, aliases, check_words, public_words
from badactors.castle.cast import Cast
from badactors.castle.contracts import (
    CastleMap,
    CastlePhase,
    Contract,
    LedgerEntry,
    WitnessRecord,
    WorldState,
)
from badactors.castle.decisions import (
    AUDIENCE_ONLY,
    PRIVATE_REQUESTS,
    Decision,
    ExitAnswer,
    IllegalDecision,
    PlanChoice,
    Request,
    RequestKind,
    check_decision,
)
from badactors.castle.director import (
    interrupted,
    opening,
    prompt_everyone,
    prompt_interrupted,
)
from badactors.castle.fouls import ContentScope, Foul, ScopeCheck, foul
from badactors.castle.game import (
    GameState,
    fork_game,
    missing_items,
    name,
    new_game,
    night_schedule,
)
from badactors.castle.interview import exit_observation, exit_request, record_exit
from badactors.castle.ledger import append, append_perceived
from badactors.castle.missions import close_mission, open_mission
from badactors.castle.night import (
    breakfast_news,
    close_conclave,
    conclave_act,
    conclave_turn,
    end_night,
    open_conclave,
    open_night,
    poison_act,
    poison_turn,
    recruit_act,
    recruit_turn,
    seat_dinner,
    settle_sleepers,
)
from badactors.castle.observe import (
    GameObservation,
    assert_no_game_leak,
    observe_game,
    observed,
)
from badactors.castle.perceive import perceive
from badactors.castle.rules import GameConfig, Role
from badactors.castle.table import open_table, table_act, table_advance, table_turn
from badactors.castle.world import (
    advance_phase,
    phase_complete,
    submit_plan,
    tick,
)


class Player(Protocol):
    """Anything that answers a decision from what one player may know.

    A player says what it is in an ``agent`` attribute — ``bot:<name>`` for a
    reference bot, ``model:<id>`` for a model — which the record keeps beside
    every answer. One that does not say is recorded as ``unknown``, never as a
    model. A player may also give an exit interview, through an ``interview``
    method taking an :class:`~badactors.castle.interview.ExitObservation`; one
    that cannot is recorded as giving none.
    """

    def __call__(self, observation: GameObservation) -> Decision: ...


UNKNOWN_AGENT = "unknown"


def agent_of(player: object) -> str:
    """Who or what plays a seat, as the record keeps it."""
    agent = getattr(player, "agent", None)
    return agent if isinstance(agent, str) and agent else UNKNOWN_AGENT


def is_model(agent: str) -> bool:
    return agent.startswith("model:") and len(agent) > len("model:")


FailureReason = Literal[
    "transport", "content_filter", "refusal", "empty", "unparseable", "invalid"
]
"""Why a turn failed: no answer came back (transport), the provider stopped it
(content_filter), the model declined in words (refusal), or what came back was
empty, not JSON (unparseable), or not a legal answer (invalid)."""


class TurnFailure(Exception):
    """A player gave no usable answer. Raised by players, never by the rules.

    It fails the turn at once: a transport error, a content filter or a refusal
    is a result, not a mistake a second ask could correct."""

    def __init__(self, message: str = "", reason: FailureReason = "transport") -> None:
        super().__init__(message)
        self.reason: FailureReason = reason


class BadAnswer(IllegalDecision):
    """An answer came back but cannot be used: empty, not JSON, or not a legal
    answer. Like any refused answer, the question is put once more with the
    error."""

    def __init__(self, message: str, reason: FailureReason) -> None:
        super().__init__(message)
        self.reason: FailureReason = reason


class Defused(Contract):
    """Control tokens defused in one of the records a model was shown, before
    it was shown them (see ``markers``)."""

    seq: int = Field(ge=1)
    markers: list[str] = Field(min_length=1)


class LoggedDecision(Contract):
    """One question and its answer, in the order they were asked. A refused
    answer is logged as it came, with why it was refused."""

    player_id: str
    agent: str = Field(min_length=1)
    """Who answered: ``bot:<name>``, ``model:<id>`` or ``unknown``."""
    kind: RequestKind
    day: int
    tick: int
    decision: Decision | None = None
    error: str | None = None
    refused: bool = False
    reason: FailureReason | None = None
    """Why a refused or failed answer could not be used."""
    foul: str | None = None
    """The real-world harm a blocked answer sought. Its words are not kept."""
    seen: list[int] = Field(default_factory=list)
    """The ledger entries whose content was in front of the player when they
    answered, by :func:`~badactors.castle.observe.observed`."""
    defused: list[Defused] = Field(default_factory=list)
    """Records whose control tokens were defused in the player's prompt."""


type Turn = Callable[[GameState], tuple[str, Request] | None]
type Act = Callable[[GameState, str, Request, Decision | None], None]

_PLANNED_AT_OPEN: frozenset[CastlePhase] = frozenset({"mission", "free_time"})


@dataclass
class Referee:
    """Runs one game to its end. Owns its copy of the state."""

    game: GameState
    players: Mapping[str, Player]
    check_leaks: bool = True
    models: tuple[str, ...] = ()
    log: list[LoggedDecision] = field(default_factory=list)
    observations: int = 0
    scope: ContentScope = field(default_factory=ScopeCheck)
    aliases: Aliases = field(init=False)
    seen: list[int] = field(default_factory=list, init=False, repr=False)
    """What the observation behind the answer being taken showed."""
    defused: list[Defused] = field(default_factory=list, init=False, repr=False)
    """Forgeries defused in the prompt behind the answer being taken."""
    interviewed: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        world = self.game.world
        self.aliases = aliases(
            self.models,
            exempt=[
                name
                for player in world.players.values()
                for name in (player.id, player.name)
            ],
        )

    def run(self) -> GameState:
        while self.game.status == "playing":
            self.play_phase()
        self.exit_interviews()
        return self.game

    def exit_interviews(self) -> None:
        """Interview everyone who has left the game since the last time, and
        everyone once it is over: once each, in seat order."""
        game = self.game
        over = game.status != "playing"
        for player_id in game.world.seats:
            if player_id in self.interviewed:
                continue
            if game.world.players[player_id].alive and not over:
                continue
            self.interviewed.add(player_id)
            decision, _ = self.ask(player_id, exit_request(game, player_id))
            answer = decision if isinstance(decision, ExitAnswer) else None
            record_exit(game, player_id, answer)

    # ------------------------------------------------------------------ asking

    def ask(
        self, player_id: str, request: Request
    ) -> tuple[Decision | None, int | None]:
        """Put one decision to one player. Returns the decision, or ``None`` and
        the ledger entry recording the failure.

        An answer that is invalid, illegal, or names a model is refused, which
        only its player learns, and the question is put once more with the
        reason. A second bad answer, or a player that gives up, fails the turn.
        """
        asked = request
        for attempt in (1, 2):
            decision: Decision | None = None
            self.seen = []
            self.defused = []
            try:
                decision = self.decide(player_id, asked)
                check_decision(request, decision)
                if request.kind not in AUDIENCE_ONLY:
                    check_words(decision, self.aliases)
                    self.check_scope(decision)
            except TurnFailure as exc:
                self._note_defused(player_id)
                error = _error(exc)
                self._log(player_id, request, error=error, reason=exc.reason)
                return None, self._failed(player_id, request, error, exc.reason)
            except (IllegalDecision, ValidationError) as exc:
                self._note_defused(player_id)
                error = _error(exc)
                reason: FailureReason = getattr(exc, "reason", "invalid")
                category = exc.category if isinstance(exc, Foul) else None
                self._log(
                    player_id,
                    request,
                    None if category is not None else decision,
                    error=error,
                    refused=True,
                    reason=reason,
                    foul=category,
                )
                if category is not None:
                    foul(self.game, player_id, request, category)
                if attempt == 2:
                    return None, self._failed(player_id, request, error, reason)
                refused(self.game, player_id, request, error)
                asked = request.model_copy(update={"refusal": error})
                continue
            self._note_defused(player_id)
            self._log(player_id, request, decision)
            return decision, None
        raise AssertionError("unreachable")  # pragma: no cover

    def check_scope(self, decision: Decision) -> None:
        """Block words that seek real-world harm (see ``fouls``)."""
        category = self.scope(public_words(decision))
        if category is not None:
            raise Foul(category)

    def _note_defused(self, player_id: str) -> None:
        for item in self.defused:
            defused(self.game, player_id, item)

    def _failed(
        self, player_id: str, request: Request, error: str, reason: FailureReason
    ) -> int | None:
        """A failed turn, for a decision in the game. A question only the
        audience hears is no turn in the game, and its failure is no failed
        turn: the record of it says the answer never came."""
        if request.kind in AUDIENCE_ONLY:
            return None
        return turn_failed(self.game, player_id, request, error, reason)

    def _log(
        self,
        player_id: str,
        request: Request,
        decision: Decision | None = None,
        *,
        error: str | None = None,
        refused: bool = False,
        reason: FailureReason | None = None,
        foul: str | None = None,
    ) -> None:
        world = self.game.world
        self.log.append(
            LoggedDecision(
                player_id=player_id,
                agent=self.agent(player_id),
                kind=request.kind,
                day=world.day,
                tick=world.tick,
                decision=decision,
                error=error,
                refused=refused,
                reason=reason,
                foul=foul,
                seen=list(self.seen),
                defused=list(self.defused),
            )
        )

    def agent(self, player_id: str) -> str:
        return agent_of(self.players.get(player_id))

    def decide(self, player_id: str, request: Request) -> Decision:
        """Show one player what they may know, checked for leaks, and take their
        answer. A player out of the game is shown their exit interview."""
        if request.kind in AUDIENCE_ONLY:
            interviewed, self.seen = exit_observation(self.game, player_id, request)
            interview = getattr(self.players.get(player_id), "interview", None)
            if not callable(interview):
                raise TurnFailure("This player gives no exit interview.")
            answer = interview(interviewed)
            if not isinstance(answer, ExitAnswer):
                raise IllegalDecision("An exit interview needs an exit answer.")
            return answer
        observation = observe_game(self.game, player_id, request)
        if self.check_leaks:
            assert_no_game_leak(observation, self.game, models=self.models)
        self.observations += 1
        self.seen = observed(observation)
        return self.players[player_id](observation)

    def ask_plan(self, world: WorldState, player_id: str) -> WorldState:
        self.game.world = world
        decision, _ = self.ask(player_id, plan_request(self.game))
        if isinstance(decision, PlanChoice):
            diary = {"diary": decision.diary} if decision.diary else {}
            return submit_plan(self.game.world, player_id, decision.plan, private=diary)
        return self.game.world

    def prefetch_plans(self, player_ids: Sequence[str]) -> None:
        """The players about to be asked for a plan, in seat order. A referee
        that can ask concurrently may start on them now; this one asks each in
        turn, when it comes."""

    def drive(self, turn: Turn, act: Act) -> None:
        while self.game.status == "playing":
            step = turn(self.game)
            if step is None:
                return
            player_id, request = step
            decision, _ = self.ask(player_id, request)
            act(self.game, player_id, request, decision)

    # ------------------------------------------------------------------ phases

    def play_phase(self) -> None:
        game = self.game
        phase = game.world.phase
        if phase in _PLANNED_AT_OPEN:
            self.prefetch_plans(opening(game.world))
            game.world = prompt_everyone(game.world, self.ask_plan)
        while not phase_complete(game.world):
            before = game.world.ledger_seq
            game.world = tick(game.world)
            if phase == "night":
                self.night_tick()
            elif not phase_complete(game.world):
                self.prefetch_plans(interrupted(game.world, before))
                game.world = prompt_interrupted(game.world, self.ask_plan, before)
        self.close_phase(phase)
        self.exit_interviews()
        if game.status != "playing":
            return
        game.world = advance_phase(game.world)
        self.open_phase(game.world.phase)

    def open_phase(self, phase: CastlePhase) -> None:
        if phase == "breakfast":
            breakfast_news(self.game)
        elif phase == "mission":
            open_mission(self.game)
        elif phase == "night":
            open_night(self.game)
        if self.game.status == "playing":
            missing_items(self.game)

    def close_phase(self, phase: CastlePhase) -> None:
        game = self.game
        if phase == "mission":
            close_mission(game)
        elif phase == "round_table":
            self.round_table()
        elif phase == "dinner":
            seat_dinner(game)
            self.drive(poison_turn, poison_act)
        elif phase == "night":
            end_night(game)

    def night_tick(self) -> None:
        game = self.game
        settle_sleepers(game)
        recruit_at, conclave_at = night_schedule(game.world.map)
        now = game.world.phase_tick
        if now == recruit_at:
            self.drive(recruit_turn, recruit_act)
        if now == conclave_at:
            open_conclave(game)
            self.drive(conclave_turn, conclave_act)
            close_conclave(game)

    def round_table(self) -> None:
        game = self.game
        open_table(game)
        while game.status == "playing" and game.table is not None:
            if game.table.stage == "done":
                return
            step = table_turn(game)
            if step is None:
                table_advance(game)
                continue
            player_id, request = step
            decision, failed = self.ask(player_id, request)
            table_act(game, player_id, request, decision, failed=failed)


def plan_request(game: GameState) -> Request:
    world = game.world
    phase = world.phase
    if phase == "mission" and game.mission is not None:
        room = world.map.room(game.mission.room_id).name
        prompt = (
            f"The mission is on. Plan up to five steps; the {game.mission.title} is "
            f"worked at the station in the {room}."
        )
    elif phase == "free_time":
        prompt = (
            "Free time. Plan up to five steps: go where you like, see what you can."
        )
    else:
        prompt = f"It is {phase.replace('_', ' ')}. Plan up to five steps."
    return Request(kind="plan", prompt=prompt)


def _error(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__)[:200]


def refused(game: GameState, player_id: str, request: Request, error: str) -> int:
    """An answer was refused, and the question will be put again. Only the
    player who gave it is told: a refusal says nothing to anyone else."""
    world = game.world
    room_id = world.players[player_id].room_id
    return append(
        world,
        type="refused",
        text=f"{name(game, player_id)}'s answer ({request.kind}) was refused: {error}",
        actor_id=player_id,
        room_id=room_id,
        payload={"request": request.kind, "reason": error},
        witnesses=[
            WitnessRecord(
                player_id=player_id,
                sense="own",
                confidence="clear",
                channel="direct",
                text=f"My answer was refused: {error}",
                recorded_actor=player_id,
                from_room=room_id,
            )
        ],
    ).seq


def turn_failed(
    game: GameState,
    player_id: str,
    request: Request,
    error: str,
    reason: FailureReason = "transport",
) -> int:
    """The character hesitates and does nothing, and everyone present sees a
    failure, not a choice. A failure on a decision nobody else knows was put —
    a traitor's dose, nomination or offer, or the answer to an offer — is
    recorded for its player alone, or the hesitation would give the question
    away. The payload keeps the error and its reason class for the audience."""
    world = game.world
    player = world.players[player_id]
    room_id = player.room_id
    text = f"{name(game, player_id)} failed to decide ({request.kind}) and did nothing."
    payload = {"request": request.kind, "error": error, "reason": reason}
    if room_id is None or request.kind in PRIVATE_REQUESTS:
        return append(
            world,
            type="turn_failed",
            text=text,
            actor_id=player_id,
            room_id=room_id,
            payload=payload,
            witnesses=[
                WitnessRecord(
                    player_id=player_id,
                    sense="own",
                    confidence="clear",
                    channel="direct",
                    text="I failed to decide, and did nothing.",
                    recorded_actor=player_id,
                    from_room=room_id,
                )
            ],
        ).seq
    perception = perceive(
        world,
        kind="turn_failed",
        actor_id=player_id,
        room_id=room_id,
        describe=lambda seen: f"{seen} hesitated and did nothing.",
        own_text="I failed to decide, and did nothing.",
        silent=True,
    )
    return append_perceived(
        world,
        perception,
        type="turn_failed",
        text=text,
        actor_id=player_id,
        room_id=room_id,
        payload=payload,
    ).seq


def defused(game: GameState, recipient: str, item: Defused) -> int:
    """Log, for the audience alone, control tokens defused in a record before
    it reached ``recipient``'s model. The record's true author is the actor: a
    forgery attempt is evidence against whoever made it."""
    world = game.world
    source = world.ledger[item.seq - 1]
    return append(
        world,
        type="tokens_defused",
        text=(
            f"Control tokens in entry {item.seq} were defused before it reached "
            f"{name(game, recipient)}."
        ),
        actor_id=source.actor_id,
        payload={
            "source": str(item.seq),
            "recipient": recipient,
            "markers": " ".join(item.markers),
        },
    ).seq


# ------------------------------------------------------------ playing and replay


@dataclass
class Played:
    game: GameState
    log: list[LoggedDecision]
    observations: int
    agents: dict[str, str] = field(default_factory=dict)
    """Who played each seat, by :func:`agent_of`."""


def run_game(
    game: GameState,
    players: Mapping[str, Player],
    *,
    check_leaks: bool = True,
    models: Sequence[str] = (),
) -> Played:
    """Play ``game`` to the end without changing it; return the finished copy."""
    referee = Referee(
        fork_game(game),
        players,
        check_leaks=check_leaks,
        models=tuple(models),
    )
    finished = referee.run()
    return Played(
        game=finished,
        log=referee.log,
        observations=referee.observations,
        agents={pid: referee.agent(pid) for pid in finished.world.seats},
    )


Outcome = Literal["finished", "stopped:budget", "aborted:operator"]
"""How a game ended: played out, stopped when its budget ran out, or aborted
by an operator error (a bad key, no credit). Only a finished game is whole."""


class GameRecord(Contract):
    """Everything needed to replay a game exactly, and the ledger it produced.

    ``cast`` is the casting as given, model ids included; who actually played
    each seat is ``agents``. A seat played by a reference bot says so, and is
    never to be read as its cast model's result. A game that did not finish
    keeps the record of as far as it got, replays to there, and is never rated.
    """

    schema_version: Literal[3] = 3
    seed: int
    world_id: str
    castle: CastleMap
    cast: Cast
    agents: dict[str, str]
    config: GameConfig
    decisions: list[LoggedDecision]
    ledger: list[LedgerEntry]
    winner: Role | None
    outcome: Outcome = "finished"
    rated: bool = False

    @model_validator(mode="after")
    def every_seat_says_who_played_it(self) -> Self:
        if self.rated and (self.outcome != "finished" or self.reference_bots):
            raise ValueError("Only a finished game played by models can be rated.")
        cast = {member.id: member.model for member in self.cast.players}
        if set(self.agents) != set(cast):
            raise ValueError("Every seat in the cast needs its agent, and only those.")
        for seat, agent in self.agents.items():
            if is_model(agent) and agent != f"model:{cast[seat]}":
                raise ValueError(
                    f"Seat {seat!r} was played by {agent!r}, not the cast's model."
                )
        for decision in self.decisions:
            if decision.agent != self.agents.get(decision.player_id):
                raise ValueError(
                    f"A decision by {decision.player_id!r} names another agent."
                )
            if any(not 1 <= seq <= len(self.ledger) for seq in decision.seen):
                raise ValueError(
                    f"A decision by {decision.player_id!r} saw an entry the "
                    "ledger does not hold."
                )
        return self

    @property
    def reference_bots(self) -> bool:
        """Whether no seat was played by a model."""
        return not any(is_model(agent) for agent in self.agents.values())


def start_game(
    castle: CastleMap,
    cast: Cast,
    *,
    seed: int,
    config: GameConfig | None = None,
    world_id: str = "castle-game",
) -> GameState:
    return new_game(
        castle,
        cast.characters(),
        seed=seed,
        config=config,
        world_id=world_id,
    )


def record(
    castle: CastleMap,
    cast: Cast,
    played: Played,
    *,
    outcome: Outcome = "finished",
    rated: bool = False,
) -> GameRecord:
    game = played.game
    return GameRecord(
        seed=game.world.seed,
        world_id=game.world.id,
        castle=castle,
        cast=cast,
        agents={
            member.id: played.agents.get(member.id, UNKNOWN_AGENT)
            for member in cast.players
        },
        config=game.config,
        decisions=played.log,
        ledger=game.world.ledger,
        winner=game.winner,
        outcome=outcome,
        rated=rated,
    )


@dataclass
class ReplayReferee(Referee):
    """Plays a stored game again from its log alone.

    It asks the same questions in the same order as the original game did, and
    fails at the first one that differs; the answers come from the log, failures
    included, so nobody is shown anything and no player is needed.
    """

    decisions: Sequence[LoggedDecision] = ()
    cursor: int = 0
    stopped: bool = False
    """The stored game did not finish: when its log runs out, so does the replay."""

    def decide(self, player_id: str, request: Request) -> Decision:
        if self.cursor >= len(self.decisions):
            if self.stopped:
                raise ReplayStopped(f"The game stopped at decision {self.cursor}.")
            raise ReplayDiverged(f"The log ran out at decision {self.cursor}.")
        logged = self.decisions[self.cursor]
        asked = (player_id, request.kind, self.game.world.tick)
        if asked != (logged.player_id, logged.kind, logged.tick):
            raise ReplayDiverged(
                f"Replay diverged at decision {self.cursor}: asked {asked}, the log "
                f"has {(logged.player_id, logged.kind, logged.tick)}."
            )
        self.cursor += 1
        self.seen = list(logged.seen)
        self.defused = list(logged.defused)
        if logged.decision is not None:
            return logged.decision
        if logged.foul is not None:
            raise Foul(logged.foul)
        if logged.refused:
            raise BadAnswer(logged.error or "refused", logged.reason or "invalid")
        raise TurnFailure(logged.error or "failed", logged.reason or "transport")

    def check_scope(self, decision: Decision) -> None:
        """Rulings come from the log: an answer logged as a foul was raised as
        one above, and every other answer passed when it was given."""


class ReplayDiverged(AssertionError):
    """A replay was asked something the stored game never asked."""


class ReplayStopped(Exception):
    """A replay of an unfinished game reached the point where it stopped."""


def replay(stored: GameRecord) -> GameState:
    """Play a stored game again from its seed and its logged decisions."""
    game = start_game(
        stored.castle,
        stored.cast,
        seed=stored.seed,
        config=stored.config,
        world_id=stored.world_id,
    )
    referee = ReplayReferee(
        fork_game(game),
        {},
        check_leaks=False,
        models=tuple(sorted(set(stored.cast.models().values()))),
        decisions=stored.decisions,
        stopped=stored.outcome != "finished",
    )
    try:
        finished = referee.run()
    except ReplayStopped:
        finished = referee.game
    if referee.cursor != len(stored.decisions):
        raise ReplayDiverged(
            f"Replay used {referee.cursor} of {len(stored.decisions)} decisions."
        )
    return finished


def save_record(stored: GameRecord, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stored.model_dump_json(indent=1))


def load_record(path: str | Path) -> GameRecord:
    return GameRecord.model_validate_json(Path(path).read_text())


__all__ = [
    "UNKNOWN_AGENT",
    "BadAnswer",
    "Defused",
    "FailureReason",
    "GameRecord",
    "LoggedDecision",
    "Outcome",
    "Played",
    "Player",
    "Referee",
    "ReplayDiverged",
    "ReplayReferee",
    "ReplayStopped",
    "TurnFailure",
    "agent_of",
    "defused",
    "is_model",
    "load_record",
    "plan_request",
    "record",
    "refused",
    "replay",
    "run_game",
    "save_record",
    "start_game",
    "turn_failed",
]
