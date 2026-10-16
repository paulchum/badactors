"""Set-ups shared by the Castle game tests.

A scene is a real game played by the seeded bot, run to the point a test cares
about. Where a test needs control, a script answers first and the bot plays every
decision the script leaves alone; scripts read the true game state, which is
the one liberty tests take that a player never can.

The planted scenes at the end are for the fact-checker: one scripted first day
each, in which every seat the script leaves alone plays :class:`Quiet` and does
as little as the rules allow, so the one claim under test is the only thing that
happens: a lie, a repeated noisy tally, a repeated forged note, a gaslight that
took, a rumour relayed three hops, a bluff, an evasive alibi, and a signature
backdated into a log someone reads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from badactors.castle.bots import HeuristicBot
from badactors.castle.cast import load_cast_file
from badactors.castle.contracts import (
    CastlePhase,
    Forge,
    Gaslight,
    HeldBy,
    Intent,
    LedgerEntry,
    Move,
    Plan,
    Plant,
    Read,
    Relay,
    Rumor,
    Search,
    Step,
    Take,
    Tamper,
    Use,
    Wait,
    WitnessRecord,
)
from badactors.castle.decisions import (
    TRAITOR_ONLY,
    Accuse,
    Alibi,
    Decision,
    DemandAlibi,
    EndgameVote,
    ExitAnswer,
    Pass,
    PlanChoice,
    Question,
    Recruit,
    Refuse,
    Report,
    StaySilent,
    Vote,
)
from badactors.castle.game import GameState, fork_game, name, shield_held_by
from badactors.castle.interview import ExitObservation
from badactors.castle.mapfile import load_map
from badactors.castle.observe import GameObservation
from badactors.castle.play import LoggedDecision, Referee, start_game
from badactors.castle.rules import GameConfig

ROOT = Path(__file__).resolve().parent.parent
CASTLE = load_map(ROOT / "configs/castle/map.json")
CAST = load_cast_file(ROOT / "configs/castle/cast.json")
MODELS = sorted(set(CAST.models().values()))
NO_POISON = {"poison": {"per_game": 0}}
"""Tool overrides for a game where nobody may poison."""

type Script = Callable[[GameObservation, GameState], Decision | None]


class Pause(Exception):
    """Stops a game at the decision a test wants to look at."""

    def __init__(self, observation: GameObservation) -> None:
        super().__init__(observation.request.kind)
        self.observation = observation


def referee(
    *,
    seed: int = 13,
    players: int = 10,
    script: Script | None = None,
    **config: Any,
) -> Referee:
    """A referee for a fresh game, every seat played by the bot unless the
    script answers first."""
    cast = CAST.first(players)
    game = start_game(CASTLE, cast, seed=seed, config=GameConfig.shipped(**config))
    ref = Referee(fork_game(game), {}, models=tuple(MODELS))
    player = _Scripted(ref, HeuristicBot(seed=seed), script)
    ref.players = {pid: player for pid in game.world.seats}
    return ref


@dataclass
class _Scripted:
    """The bot in every seat, with a script answering first; exit interviews
    go to the bot."""

    ref: Referee
    bot: HeuristicBot | Quiet
    script: Script | None

    def __call__(self, observation: GameObservation) -> Decision:
        if self.script is not None:
            decision = self.script(observation, self.ref.game)
            if decision is not None:
                return decision
        return self.bot(observation)

    def interview(self, departing: ExitObservation) -> ExitAnswer:
        return self.bot.interview(departing)


def run_until(ref: Referee, day: int, phase: CastlePhase) -> GameState:
    """Play whole phases until ``phase`` of ``day`` has just opened."""
    while ref.game.status == "playing" and (
        ref.game.world.day,
        ref.game.world.phase,
    ) != (day, phase):
        ref.play_phase()
    return ref.game


def pause_at(
    when: Callable[[GameObservation, GameState], bool],
    *,
    script: Script | None = None,
    **options: Any,
) -> tuple[GameState, GameObservation]:
    """Play until ``when`` holds for a decision about to be asked, and return the
    game at that moment with the (already leak-checked) observation."""

    def stop(observation: GameObservation, game: GameState) -> Decision | None:
        if when(observation, game):
            raise Pause(observation)
        return script(observation, game) if script is not None else None

    ref = referee(script=stop, **options)
    try:
        ref.run()
    except Pause as paused:
        return ref.game, paused.observation
    raise AssertionError("The game ended before the moment the test waited for.")


def recruiting(answer: Decision) -> Script:
    """Day one's table votes out the first traitor; that night the other offers
    a place to one of the Faithful without a shield, who answers ``answer``."""

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        kind = observation.request.kind
        options = observation.request.options
        if kind == "vote" and game.world.day == 1:
            first = game.starting_traitors[0]
            return Vote(target=first if first in options else options[0])
        if kind == "poison":
            return Pass(reason="Not this time.")
        if kind == "recruit":
            unshielded = [pid for pid in options if shield_held_by(game, pid) is None]
            return Recruit(target=(unshielded or options)[0], text="Join us.")
        if kind == "recruit_answer":
            return answer
        return None

    return script


def of_type(game: GameState, *types: str) -> list[LedgerEntry]:
    return [entry for entry in game.world.ledger if entry.type in types]


def plant_record(
    game: GameState, entry: LedgerEntry, viewer: str, **changes: Any
) -> LedgerEntry:
    """Simulate a referee bug: give ``viewer`` a record of ``entry`` they should
    never have had. The record copies an existing one (or states the entry's own
    text), so it looks as legitimate as the bug that would write it."""
    if entry.witnesses:
        template = entry.witnesses[-1]
    else:
        template = WitnessRecord(
            player_id=viewer,
            sense="saw",
            confidence="clear",
            channel="direct",
            text=entry.text,
        )
    record = template.model_copy(update={"player_id": viewer, **changes})
    planted = entry.model_copy(update={"witnesses": [*entry.witnesses, record]})
    game.world.ledger[entry.seq - 1] = planted
    return planted


def rewrite_record(
    game: GameState, entry: LedgerEntry, viewer: str, **changes: Any
) -> LedgerEntry:
    """Simulate a referee bug that wrote ``viewer`` the wrong record of
    ``entry``."""
    rewritten = entry.model_copy(
        update={
            "witnesses": [
                record.model_copy(update=changes)
                if record.player_id == viewer
                else record
                for record in entry.witnesses
            ]
        }
    )
    game.world.ledger[entry.seq - 1] = rewritten
    return rewritten


# ------------------------------------------------------------ planted scenes


@dataclass(frozen=True)
class Quiet:
    """A seat that does as little as the rules allow: waits where it stands,
    keeps silent at the table, lets every chance at night pass, and votes for
    the first name it is offered."""

    agent: ClassVar[str] = "bot:quiet"

    def __call__(self, observation: GameObservation) -> Decision:
        request = observation.request
        kind = request.kind
        if kind == "plan":
            return PlanChoice(plan=Plan(goal="keep still", steps=[Wait()] * 5))
        if kind == "vote":
            return Vote(target=request.options[0])
        if kind == "endgame":
            return EndgameVote(choice="end")
        if kind in TRAITOR_ONLY:
            return Pass(reason="Not tonight.")
        if kind == "recruit_answer":
            return Refuse()
        return StaySilent(reason="Nothing to add.")

    def interview(self, departing: ExitObservation) -> ExitAnswer:
        return ExitAnswer(
            text="Nothing to add.",
            guesses={pid: "unknown" for pid in departing.request.options},
        )


@dataclass
class Staged:
    """A planted scene, played: the game, its decision log, and who is who."""

    game: GameState
    log: list[LoggedDecision]
    traitor: str
    faithful: list[str]

    def last(self, type: str, actor: str) -> LedgerEntry:
        found = [
            entry
            for entry in self.game.world.ledger
            if entry.type == type and entry.actor_id == actor
        ]
        assert found, f"{actor} wrote no {type} entry"
        return found[-1]


def sides(game: GameState) -> tuple[str, list[str]]:
    """The first traitor, and the Faithful in seat order."""
    traitors = game.starting_traitors
    return traitors[0], [pid for pid in game.world.seats if pid not in traitors]


def stage(
    script: Script,
    *,
    seed: int = 13,
    players: int = 8,
    until: tuple[int, CastlePhase] = (1, "dinner"),
    **config: Any,
) -> Staged:
    """Play a scripted first day, every other decision quiet, up to ``until``."""
    cast = CAST.first(players)
    game = start_game(CASTLE, cast, seed=seed, config=GameConfig.shipped(**config))
    ref = Referee(fork_game(game), {}, models=tuple(MODELS))
    seat = _Scripted(ref, Quiet(), script)
    ref.players = {pid: seat for pid in game.world.seats}
    played = run_until(ref, *until)
    traitor, faithful = sides(played)
    return Staged(played, ref.log, traitor, faithful)


def _plan(*steps: Step) -> PlanChoice:
    return PlanChoice(plan=Plan(goal="the scene", steps=list(steps)))


def _route(game: GameState, player_id: str, room_id: str) -> list[Step]:
    here = game.world.players[player_id].room_id or room_id
    return [Move(room_id=step) for step in game.world.map.route(here, room_id)]


def _did(game: GameState, actor: str, type: str, **payload: str) -> LedgerEntry | None:
    for entry in reversed(game.world.ledger):
        if (
            entry.type == type
            and entry.actor_id == actor
            and all(entry.payload.get(key) == value for key, value in payload.items())
        ):
            return entry
    return None


def _holds(game: GameState, player_id: str, thing_id: str) -> bool:
    return game.world.placements.get(thing_id) == HeldBy(player_id=player_id)


def _demand(observation: GameObservation, asker: str, target: str) -> Decision | None:
    """``asker`` demands ``target``'s alibi for free time on day one."""
    request = observation.request
    if (
        request.kind == "question"
        and observation.viewer_id == asker
        and target in request.options
        and "demand_alibi" in request.tools
    ):
        return DemandAlibi(target=target, day=1, phase="free_time")
    return None


def lie_scene(**options: Any) -> Staged:
    """A traitor who spent free time in the Great Hall swears, when asked,
    that they were in the Library."""

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        traitor, faithful = sides(game)
        if observation.request.kind == "alibi" and observation.viewer_id == traitor:
            return Alibi(room="library", diary="Far from anything that happened.")
        return _demand(observation, faithful[0], traitor)

    return stage(script, **options)


def evasive_scene(**options: Any) -> Staged:
    """A traitor spends a tick of free time in the Great Hall and the rest in
    the Study, and, asked where they were, names the Great Hall."""

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        traitor, faithful = sides(game)
        me, kind = observation.viewer_id, observation.request.kind
        if me == traitor and kind == "plan" and game.world.phase == "free_time":
            if _did(game, traitor, "move_arrive") is None:
                return _plan(Wait(), Move(room_id="study"), Wait(), Wait(), Wait())
        if me == traitor and kind == "alibi":
            return Alibi(room="great_hall", diary="True, as far as it goes.")
        return _demand(observation, faithful[0], traitor)

    return stage(script, **options)


def bluff_scene(
    diary: str = "I made it up. Let them chase it.", **options: Any
) -> Staged:
    """A traitor away in the Study during free time tells the table that one
    of the Faithful was in the Library then; by default their diary owns up."""

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        traitor, faithful = sides(game)
        me, kind = observation.viewer_id, observation.request.kind
        world = game.world
        if me == traitor and kind == "plan" and world.phase == "free_time":
            if world.players[traitor].room_id != "study":
                return _plan(Move(room_id="study"), Wait(), Wait(), Wait(), Wait())
        if me == traitor and kind == "accuse":
            victim = faithful[0]
            return Accuse(
                target=victim,
                text=f"{name(game, victim)} was in the Library during free time on "
                "day 1.",
                diary=diary,
            )
        return None

    return stage(script, **options)


def tally_scene(**options: Any) -> Staged:
    """Three of the Faithful work the mission, and a fourth repeats the clerk's
    rough tally for one of them as if it were exact."""

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        _, faithful = sides(game)
        me, kind = observation.viewer_id, observation.request.kind
        world, mission = game.world, game.mission
        if kind == "plan" and world.phase == "mission" and me in faithful[:3]:
            if mission is not None:
                route = _route(game, me, mission.room_id)
                work = [Use(thing_id=mission.station)] * (5 - len(route))
                return _plan(*route, *work)
        if kind == "question" and me == faithful[3]:
            found = misreported(game)
            if found is not None and found[0] in observation.request.options:
                worker, coins = found
                return Question(
                    target=worker,
                    text=f"{name(game, worker)} earned {coins} coins at the mission. "
                    "Why so few?",
                )
        return None

    return stage(script, **options)


def misreported(game: GameState) -> tuple[str, int] | None:
    """The first worker whose share the clerk's last tally got wrong, with the
    share the clerk gave."""
    if not game.tallies:
        return None
    tally = game.tallies[-1]
    exact = {
        entry.actor_id: int(entry.payload["coins"])
        for entry in game.world.ledger
        if entry.type == "contribution" and entry.day == tally.day
    }
    for pid, coins in tally.reported.items():
        if exact.get(pid) != coins:
            return pid, coins
    return None


SHEET = "paper_1"
"""The notepaper a forger takes from the Steward's desk."""


def forged_note_scene(**options: Any) -> Staged:
    """A traitor forges a note in one Faithful's hand and slips it into
    another's coat; the reader reports it at the table, word for word, as the
    note it seems to be."""
    options.setdefault("tools", {"plant": {"success": 1.0}})

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        traitor, faithful = sides(game)
        hand, reader = faithful[0], faithful[1]
        me, kind = observation.viewer_id, observation.request.kind
        world = game.world
        forged = _did(game, traitor, "write", tool="forge")
        if me == traitor and kind == "plan" and world.phase == "mission":
            if forged is not None:
                return None
            forge = Forge(
                thing_id=SHEET,
                hand=hand,
                text="Meet me in the crypt after the lamps. Burn this.",
            )
            if world.players[traitor].room_id != "study":
                return _plan(
                    *_route(game, me, "study"), Search(), Take(thing_id=SHEET), forge
                )
            if not _holds(game, traitor, SHEET):
                return _plan(Search(), Take(thing_id=SHEET), forge)
            return _plan(forge)
        if me == traitor and kind == "plan" and world.phase == "free_time":
            if forged is not None and _holds(game, traitor, SHEET):
                return _plan(
                    *_route(game, me, "great_hall"), Plant(thing_id=SHEET, to=reader)
                )
        if me == reader and kind == "plan" and world.phase == "round_table":
            if _holds(game, reader, SHEET) and _did(game, reader, "read") is None:
                return _plan(Read(thing_id=SHEET))
        if me == reader and kind == "accuse":
            read = _did(game, reader, "read", thing_id=SHEET)
            if read is not None and read.seq in observation.request.evidence:
                return Report(evidence=read.seq, diary="A note in a hand I know.")
        return None

    return stage(script, **options)


def gaslight_scene(**options: Any) -> Staged:
    """A traitor whispers to one of the Faithful that the two of them spent
    free time together in the Library; asked for an alibi, the Faithful gives
    exactly that one, though they never left the Great Hall. Their own memory
    of the afternoon is in front of them when they do, unless ``recent_limit``
    is set short enough to have pushed it out."""
    options.setdefault("recent_limit", 120)

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        traitor, faithful = sides(game)
        asker, victim = faithful[0], faithful[1]
        me, kind = observation.viewer_id, observation.request.kind
        world = game.world
        if me == traitor and kind == "plan" and world.phase == "free_time":
            if _did(game, traitor, "whisper", tool="gaslight") is None:
                return _plan(
                    Gaslight(
                        to=victim,
                        room_id="library",
                        day=1,
                        phase="free_time",
                        text="You and I were in the Library all through free time "
                        "on day 1. Tell them, if anyone asks.",
                        intent=Intent(
                            target=victim,
                            effect="alibi",
                            want=traitor,
                            window="next_phase",
                        ),
                    )
                )
        if me == victim and kind == "alibi":
            return Alibi(
                room="library",
                companion=traitor if traitor in observation.request.options else None,
                diary="We were in the Library together; I remember it now.",
            )
        return _demand(observation, asker, victim)

    return stage(script, **options)


def rumour_scene(**options: Any) -> Staged:
    """A traitor starts a rumour that one of the Faithful spent free time in the
    Library; it is relayed three times, and the last to hear it tells the
    table as fact."""

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        traitor, faithful = sides(game)
        subject, chain = faithful[0], faithful[1:5]
        me, kind = observation.viewer_id, observation.request.kind
        world = game.world
        if kind == "plan" and world.phase == "free_time":
            if me == traitor and _did(game, traitor, "whisper", tool="rumor") is None:
                return _plan(
                    Rumor(
                        to=chain[0],
                        text=f"People are saying {name(game, subject)} was in the "
                        "Library during free time on day 1.",
                        intent=Intent(
                            target=chain[0],
                            effect="belief",
                            want=subject,
                            window="next_vote",
                        ),
                    )
                )
            if me in chain[:-1]:
                heard = _heard_rumour(game, me)
                passed = _did(game, me, "whisper", tool="relay")
                if heard is not None and passed is None:
                    after = chain[chain.index(me) + 1]
                    return _plan(Relay(to=after, rumor=heard.seq))
        if kind == "accuse" and me == chain[-1]:
            return Accuse(
                target=subject,
                text=f"{name(game, subject)} was in the Library during free time on "
                "day 1.",
                diary="Everyone is saying it.",
            )
        return None

    return stage(script, **options)


def tampered_log_scene(**options: Any) -> Staged:
    """A traitor walks to the Chapel and backdates one of the Faithful's
    signature into the guest book, at the first tick of free time; another of
    the Faithful walks there after them and reads the book."""

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        traitor, faithful = sides(game)
        subject, reader = faithful[0], faithful[1]
        me, kind, world = observation.viewer_id, observation.request.kind, game.world
        if kind != "plan" or world.phase != "free_time":
            return None
        opened = world.tick - world.phase_tick
        if me == traitor and _did(game, traitor, "write", tool="tamper") is None:
            return _plan(
                *_route(game, me, "chapel"),
                Tamper(
                    thing_id="guest_book",
                    edit="backdate",
                    subject=subject,
                    tick=opened + 1,
                ),
            )
        if me == reader and _did(game, reader, "read", thing_id="guest_book") is None:
            return _plan(
                *_route(game, me, "chapel"), Wait(), Read(thing_id="guest_book")
            )
        return None

    return stage(script, **options)


def _heard_rumour(game: GameState, player_id: str) -> LedgerEntry | None:
    for entry in game.world.ledger:
        if (
            entry.type == "whisper"
            and entry.payload.get("to") == player_id
            and entry.payload.get("tool") in ("rumor", "relay")
        ):
            return entry
    return None


__all__ = [
    "CAST",
    "CASTLE",
    "MODELS",
    "NO_POISON",
    "SHEET",
    "Pause",
    "Quiet",
    "Script",
    "Staged",
    "bluff_scene",
    "evasive_scene",
    "forged_note_scene",
    "gaslight_scene",
    "lie_scene",
    "misreported",
    "of_type",
    "pause_at",
    "plant_record",
    "recruiting",
    "referee",
    "rewrite_record",
    "rumour_scene",
    "run_until",
    "sides",
    "stage",
    "tally_scene",
    "tampered_log_scene",
]
