"""Run a game: ask the players, apply the rules, keep the record.

This is the only place a player is asked anything. Every question goes through
:meth:`Referee.ask`, which builds the player's scoped observation, checks it for
leaks, and logs the answer so the game can be replayed without the players. An
answer that does not parse, does not answer the question, or names a model is
refused privately and the question put once more, with the reason. A player that
still fails to answer — a transport error, a refusal, a second bad answer — gets
a visible ``turn_failed`` entry: the character hesitates and does nothing, and it
is never dressed up as a choice.

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
from typing import Literal, Protocol

from pydantic import ValidationError

from badactors.castle.anonymity import Aliases, aliases, check_words
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
    PRIVATE_REQUESTS,
    Decision,
    IllegalDecision,
    PlanChoice,
    Request,
    RequestKind,
    check_decision,
)
from badactors.castle.director import prompt_everyone, prompt_interrupted
from badactors.castle.game import (
    GameState,
    fork_game,
    missing_items,
    name,
    new_game,
    night_schedule,
)
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
from badactors.castle.observe import GameObservation, assert_no_game_leak, observe_game
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
    """Anything that answers a decision from what one player may know."""

    def __call__(self, observation: GameObservation) -> Decision: ...


class TurnFailure(Exception):
    """A player gave no usable answer. Raised by players, never by the rules."""


class LoggedDecision(Contract):
    """One question and its answer, in the order they were asked. A refused
    answer is logged as it came, with why it was refused."""

    player_id: str
    kind: RequestKind
    day: int
    tick: int
    decision: Decision | None = None
    error: str | None = None
    refused: bool = False


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
    aliases: Aliases = field(init=False)

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
        return self.game

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
            try:
                decision = self.decide(player_id, asked)
                check_decision(request, decision)
                check_words(decision, self.aliases)
            except TurnFailure as exc:
                error = _error(exc)
                self._log(player_id, request, error=error)
                return None, turn_failed(self.game, player_id, request, error)
            except (IllegalDecision, ValidationError) as exc:
                error = _error(exc)
                self._log(player_id, request, decision, error=error, refused=True)
                if attempt == 2:
                    return None, turn_failed(self.game, player_id, request, error)
                refused(self.game, player_id, request, error)
                asked = request.model_copy(update={"refusal": error})
                continue
            self._log(player_id, request, decision)
            return decision, None
        raise AssertionError("unreachable")  # pragma: no cover

    def _log(
        self,
        player_id: str,
        request: Request,
        decision: Decision | None = None,
        *,
        error: str | None = None,
        refused: bool = False,
    ) -> None:
        world = self.game.world
        self.log.append(
            LoggedDecision(
                player_id=player_id,
                kind=request.kind,
                day=world.day,
                tick=world.tick,
                decision=decision,
                error=error,
                refused=refused,
            )
        )

    def decide(self, player_id: str, request: Request) -> Decision:
        """Show one player what they may know, checked for leaks, and take their
        answer."""
        observation = observe_game(self.game, player_id, request)
        if self.check_leaks:
            assert_no_game_leak(observation, self.game, models=self.models)
        self.observations += 1
        return self.players[player_id](observation)

    def ask_plan(self, world: WorldState, player_id: str) -> WorldState:
        self.game.world = world
        decision, _ = self.ask(player_id, plan_request(self.game))
        if isinstance(decision, PlanChoice):
            diary = {"diary": decision.diary} if decision.diary else {}
            return submit_plan(self.game.world, player_id, decision.plan, private=diary)
        return self.game.world

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
            game.world = prompt_everyone(game.world, self.ask_plan)
        while not phase_complete(game.world):
            before = game.world.ledger_seq
            game.world = tick(game.world)
            if phase == "night":
                self.night_tick()
            elif not phase_complete(game.world):
                game.world = prompt_interrupted(game.world, self.ask_plan, before)
        self.close_phase(phase)
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


def turn_failed(game: GameState, player_id: str, request: Request, error: str) -> int:
    """The character hesitates and does nothing, and everyone present sees a
    failure, not a choice. A failure on a decision nobody else knows was put —
    a traitor's dose, nomination or offer, or the answer to an offer — is
    recorded for its player alone, or the hesitation would give the question
    away."""
    world = game.world
    player = world.players[player_id]
    room_id = player.room_id
    text = f"{name(game, player_id)} failed to decide ({request.kind}) and did nothing."
    payload = {"request": request.kind, "error": error}
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


# ------------------------------------------------------------ playing and replay


@dataclass
class Played:
    game: GameState
    log: list[LoggedDecision]
    observations: int


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
    return Played(game=finished, log=referee.log, observations=referee.observations)


class GameRecord(Contract):
    """Everything needed to replay a game exactly, and the ledger it produced."""

    schema_version: Literal[1] = 1
    seed: int
    world_id: str
    castle: CastleMap
    cast: Cast
    config: GameConfig
    decisions: list[LoggedDecision]
    ledger: list[LedgerEntry]
    winner: Role | None


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
) -> GameRecord:
    game = played.game
    return GameRecord(
        seed=game.world.seed,
        world_id=game.world.id,
        castle=castle,
        cast=cast,
        config=game.config,
        decisions=played.log,
        ledger=game.world.ledger,
        winner=game.winner,
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

    def decide(self, player_id: str, request: Request) -> Decision:
        if self.cursor >= len(self.decisions):
            raise ReplayDiverged(f"The log ran out at decision {self.cursor}.")
        logged = self.decisions[self.cursor]
        asked = (player_id, request.kind, self.game.world.tick)
        if asked != (logged.player_id, logged.kind, logged.tick):
            raise ReplayDiverged(
                f"Replay diverged at decision {self.cursor}: asked {asked}, the log "
                f"has {(logged.player_id, logged.kind, logged.tick)}."
            )
        self.cursor += 1
        if logged.decision is not None:
            return logged.decision
        if logged.refused:
            raise IllegalDecision(logged.error or "refused")
        raise TurnFailure(logged.error or "failed")


class ReplayDiverged(AssertionError):
    """A replay was asked something the stored game never asked."""


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
    )
    finished = referee.run()
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
    "GameRecord",
    "LoggedDecision",
    "Played",
    "Player",
    "Referee",
    "ReplayDiverged",
    "ReplayReferee",
    "TurnFailure",
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
