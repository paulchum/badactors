"""Episodes: each tool told as a story, from the move to its consequence.

Every story is a real game. A few players' moves are written for them; every
other seat is the seeded bot, and nobody falls for anything unless the story
seats a susceptible bot there. Where a story needs a roll to fall one way — a
forgery that betrays its writer — it says so by setting that tool's number, the
way a balance pass would.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from badactors.castle import sleuth
from badactors.castle.bots import HeuristicBot, Mind
from badactors.castle.contracts import (
    CastlePhase,
    CompareHands,
    Counterfeit,
    Disguise,
    Forge,
    Gaslight,
    Honeypot,
    Hook,
    Inspect,
    Intent,
    LedgerEntry,
    Move,
    Plan,
    Plant,
    Read,
    ReadLog,
    Search,
    Steal,
    Step,
    Tamper,
    WornBy,
)
from badactors.castle.decisions import (
    CrossExamine,
    Decision,
    DemandAlibi,
    Nominate,
    Pass,
    PlanChoice,
    Poison,
    Recruit,
    Refuse,
    Report,
    Request,
    Vote,
)
from badactors.castle.game import (
    GameState,
    fork_game,
    living_faithful,
    shield_held_by,
)
from badactors.castle.manipulation import outcomes, planted_by
from badactors.castle.observe import GameObservation, observe_game
from badactors.castle.play import Referee, start_game
from badactors.castle.rules import GameConfig
from castle_scenes import CAST, CASTLE, MODELS, of_type
from castle_tools import give, put

type Move_ = Callable[[GameObservation, GameState], Decision | None]


@dataclass
class Episode:
    """A real game where some players' moves are written for them."""

    seed: int
    config: dict[str, Any] = field(default_factory=dict)
    gullible: tuple[str, ...] = ()
    lines: list[Move_] = field(default_factory=list)

    def __post_init__(self) -> None:
        game = start_game(
            CASTLE,
            CAST.first(10),
            seed=self.seed,
            config=GameConfig.shipped(**self.config),
        )
        self.ref = Referee(fork_game(game), {}, models=tuple(MODELS))
        wary = HeuristicBot(seed=self.seed, susceptibility=0.0)
        trusting = HeuristicBot(seed=self.seed, susceptibility=1.0)

        def player(pid: str) -> Callable[[GameObservation], Decision]:
            bot = trusting if pid in self.gullible else wary

            def decide(observation: GameObservation) -> Decision:
                for line in self.lines:
                    decision = line(observation, self.ref.game)
                    if decision is not None:
                        return decision
                return bot(observation)

            return decide

        self.ref.players = {pid: player(pid) for pid in game.world.seats}

    @property
    def game(self) -> GameState:
        return self.ref.game

    @property
    def traitors(self) -> list[str]:
        return list(self.game.starting_traitors)

    @property
    def faithful(self) -> list[str]:
        return living_faithful(self.game)

    def when(self, line: Move_) -> None:
        self.lines.append(line)

    def until(self, day: int, phase: CastlePhase, *, may_end: bool = False) -> None:
        while self.game.status == "playing" and (
            self.game.world.day,
            self.game.world.phase,
        ) != (day, phase):
            self.ref.play_phase()
        if not may_end:
            assert self.game.status == "playing", "the game ended before the story did"

    def phase(self) -> None:
        self.ref.play_phase()

    def mind(self, pid: str) -> Mind:
        request = Request(
            kind="vote",
            options=[p for p in self.game.world.seats if p != pid],
            prompt="Vote.",
        )
        return Mind(observe_game(self.game, pid, request), self.seed, 0.0)


def plans(
    who: str, day: int, phase: str, steps: Callable[[GameObservation], list[Step]]
) -> Move_:
    """A player's plans, written for them, on one day's part. The script is
    asked again whenever the player must plan again — someone walked in, the
    plan ran out — so it says what is left to do from what the player can see."""

    def line(observation: GameObservation, game: GameState) -> Decision | None:
        if (
            observation.viewer_id == who
            and observation.request.kind == "plan"
            and observation.day == day
            and observation.phase == phase
        ):
            return PlanChoice(
                plan=Plan(goal="the story", steps=steps(observation) or [Search()])
            )
        return None

    return line


def holds(observation: GameObservation, thing_id: str) -> bool:
    return any(view.id == thing_id for view in observation.world.carrying)


def written(observation: GameObservation, thing_id: str) -> bool:
    return any(
        view.id == thing_id and view.written for view in observation.world.carrying
    )


def did(
    observation: GameObservation, kind: str, target: str, *, day: int | None = None
) -> bool:
    """Whether this player's own records show this act, aimed at this (on this
    day, if one is named)."""
    return any(
        item.kind == kind
        and item.target == target
        and item.actor == observation.viewer_id
        and day in (None, item.day)
        for item in (*observation.world.evidence, *observation.world.recent)
    )


def stays(who: str, day: int, phase: str) -> Move_:
    """A player who keeps to the room the story put them in, looking about."""
    return plans(who, day, phase, lambda _: [Search(), Search(), Search()])


def answers(
    who: str | None, kind: str, decide: Callable[[GameObservation], Decision | None]
) -> Move_:
    def line(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind != kind:
            return None
        if who is not None and observation.viewer_id != who:
            return None
        return decide(observation)

    return line


def votes_for(target: Callable[[], str], day: int) -> Move_:
    """Everyone at the table votes this way on this day, if they may."""

    def line(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind != "vote" or observation.day != day:
            return None
        wanted = target()
        if wanted in observation.request.options:
            return Vote(target=wanted)
        return None

    return line


def no_poison(observation: GameObservation, game: GameState) -> Decision | None:
    return (
        Pass(reason="Not this time.") if observation.request.kind == "poison" else None
    )


def conclave_spares(*spared: str) -> Move_:
    def line(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind != "nominate":
            return None
        options = [pid for pid in observation.request.options if pid not in spared]
        return Nominate(target=options[0]) if options else None

    return line


def route_from(origin: str, room: str) -> list[Step]:
    return [Move(room_id=r) for r in CASTLE.route(origin, room)]


def route(observation: GameObservation, room: str) -> list[Step]:
    here = observation.world.room.id if observation.world.room is not None else ""
    return [Move(room_id=r) for r in observation.castle.route(here, room)]


def _entry(game: GameState, seq: int) -> LedgerEntry:
    return game.world.ledger[seq - 1]


# ------------------------------------------------------------------- the stories


def test_a_forged_note_banishes_an_innocent_and_a_hand_check_exposes_it() -> None:
    """Knox forges a note in Iris's hand and slips it into Mara's coat. Mara reads
    it, reports it to the table word for word, and the table banishes Iris, who
    was one of the Faithful. The next day Mara takes the note to the library and
    compares the hand: the letter forms are the forger's."""
    ep = Episode(
        seed=21,
        config={
            "tools": {
                "forge": {"trace": 1.0},
                "compare_hands": {"success": 1.0},
                "plant": {"success": 1.0},
            }
        },
    )
    forger = ep.traitors[0]
    framed, reader = [pid for pid in ep.faithful][:2]
    ep.until(1, "free_time")
    world = ep.game.world
    sheet = next(t.id for t in world.map.things if t.kind == "paper")
    give(world, forger, sheet)
    put(world, forger, "great_hall")
    put(world, reader, "great_hall")
    name = world.players[framed].name

    def forge_and_plant(obs: GameObservation) -> list[Step]:
        if not holds(obs, sheet):
            return []
        plant = Plant(thing_id=sheet, to=reader)
        if written(obs, sheet):
            return [plant]
        forged = Forge(
            thing_id=sheet,
            hand=framed,
            text=f"Meet me in the crypt after the lamps. Burn this. — {name}",
            intent=Intent(
                target=reader, effect="belief", want=framed, window="next_vote"
            ),
        )
        return [forged, plant]

    ep.when(plans(forger, 1, "free_time", forge_and_plant))
    ep.when(stays(reader, 1, "free_time"))
    ep.when(
        plans(
            reader,
            1,
            "round_table",
            lambda obs: [] if did(obs, "read", sheet) else [Read(thing_id=sheet)],
        )
    )
    ep.when(
        answers(
            reader,
            "accuse",
            lambda obs: Report(
                evidence=next(
                    i.ledger_seq
                    for i in obs.world.evidence
                    if i.kind == "read" and i.target == sheet
                )
            ),
        )
    )
    ep.when(votes_for(lambda: framed, day=1))
    ep.when(no_poison)
    ep.when(conclave_spares(reader))
    ep.when(
        plans(
            reader,
            2,
            "free_time",
            lambda obs: [*route(obs, "library"), CompareHands(thing_id=sheet)],
        )
    )
    ep.until(2, "round_table")

    game = ep.game
    report = next(e for e in of_type(game, "report") if e.actor_id == reader)
    assert report.quote == f"Meet me in the crypt after the lamps. Burn this. — {name}"
    assert report.payload["target"] == framed, (
        "the report names whoever the note claims"
    )
    banished = of_type(game, "banishment")[0]
    assert (
        banished.payload["target"] == framed and banished.payload["role"] == "faithful"
    )
    compared = of_type(game, "read")
    exposed = [e for e in compared if e.payload.get("tool") == "compare_hands"][-1]
    assert exposed.payload["exposed"] == "true"
    assert exposed.payload["named"] == forger
    assert exposed.day == 2
    assert "the letter forms are" in (
        exposed.record_for(reader).text if exposed.record_for(reader) else ""
    )


def test_a_poisoning_is_traced_through_the_seating_plan_and_glass_residue() -> None:
    """At dinner a traitor tips a dose into a neighbour's glass. The victim does
    not come down to breakfast. Next afternoon a Faithful searches the Great Hall,
    finds the glasses in the sideboard, and inspects the one from the victim's
    place: bitter residue. The seating plan says who sat beside it, and at the
    table the investigator reads the finding out to one of them."""
    ep = Episode(seed=4, config={"tools": {"poison": {"trace": 1.0, "seen": 0.0}}})
    poisoner = ep.traitors[0]
    victims: list[str] = []

    def pour(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "poison" and observation.viewer_id == poisoner:
            victims.append(observation.request.options[0])
            return Poison(target=observation.request.options[0])
        return no_poison(observation, game)

    ep.when(pour)
    seats: list[str] = []

    def arrange(observation: GameObservation, game: GameState) -> Decision | None:
        return None

    ep.until(1, "dinner")
    # Seat the poisoner between two of the Faithful, as a lucky draw might.
    ep.phase()
    if not victims:
        raise AssertionError("the poisoner never sat beside one of the Faithful")
    victim = victims[0]
    seats.extend(ep.game.seating)
    investigator = next(
        pid
        for pid in ep.faithful
        if pid != victim
        and pid not in seats[seats.index(victim) - 1 : seats.index(victim) + 2]
    )
    place = seats.index(victim)
    glass = f"glass_{place + 1}"
    ep.when(
        plans(
            investigator,
            2,
            "free_time",
            lambda obs: [*route(obs, "great_hall"), Search(), Inspect(thing_id=glass)],
        )
    )
    # Had the poisoner rinsed the glass first, the trace would be a rinse; this
    # story is the one where they did not get to it.
    ep.when(plans(poisoner, 2, "mission", lambda _: [Search()]))
    ep.when(plans(poisoner, 2, "free_time", lambda _: [Search()]))
    ep.when(
        answers(
            investigator,
            "question",
            lambda obs: (
                CrossExamine(
                    target=poisoner,
                    evidence=next(
                        i.ledger_seq
                        for i in obs.world.evidence
                        if i.kind == "inspect" and i.target == glass
                    ),
                    text="You sat beside that glass. What did you put in it?",
                )
                if poisoner in obs.request.options
                else None
            ),
        )
    )
    ep.until(2, "dinner")

    game = ep.game
    assert not game.world.players[victim].alive
    death = next(e for e in of_type(game, "death") if e.payload["player"] == victim)
    assert death.payload["cause"] == "poisoned at dinner"
    beside = {seats[place - 1], seats[(place + 1) % len(seats)]}
    assert poisoner in beside, "the seating plan puts the poisoner beside the victim"
    poured = of_type(game, "poison")[0]
    assert poured.payload["glass"] == glass and poured.payload["residue"] == "true"
    inspected = next(
        e
        for e in of_type(game, "inspect")
        if e.actor_id == investigator and e.payload.get("thing_id") == glass
    )
    assert "bitter residue" in (inspected.record_for(investigator) or poured).text
    clues = sleuth.clues(ep.mind(investigator))
    assert any(c.player == poisoner and "poison" in c.reason for c in clues)
    cross = next(
        e for e in of_type(game, "cross_examine") if e.actor_id == investigator
    )
    assert cross.payload["target"] == poisoner and "bitter residue" in (
        cross.quote or ""
    )


def test_a_tampered_guest_book_is_contradicted_by_the_camera_log() -> None:
    """A traitor walks through the Dining Room, where the camera sees him, then
    writes himself into the Chapel's guest book at that very hour. The next
    afternoon a Faithful reads the day's guest book in the Chapel and the day's
    camera log in the Study: the two cannot both be true, and only one of them
    was written by a person."""
    ep = Episode(seed=8, config={"tools": {"tamper": {"trace": 0.0}}})
    traitor = ep.traitors[0]
    reader = ep.faithful[0]
    ep.until(1, "free_time")
    world = ep.game.world
    put(world, traitor, "great_hall")
    put(world, reader, "chapel")
    seen_at: list[int] = []

    def alibi(obs: GameObservation) -> list[Step]:
        if not seen_at:
            seen_at.append(obs.world.tick + 1)
        backdate = Tamper(
            thing_id="guest_book", edit="backdate", subject=traitor, tick=seen_at[0]
        )
        if did(obs, "write", "guest_book"):
            return []
        here = obs.world.room.id if obs.world.room is not None else ""
        if here == "chapel":
            return [backdate]
        went = any(
            item.actor == traitor and item.text == "I set out for the Dining Room."
            for item in obs.world.recent
        )
        if went:
            return [*route(obs, "chapel"), backdate]
        return [
            Move(room_id="dining_room"),
            *route_from("dining_room", "chapel"),
            backdate,
        ]

    def read_both(obs: GameObservation) -> list[Step]:
        # Having read the guest book, she asks the camera about the name in it.
        camera = ReadLog(thing_id="camera_log", day=1, subject=traitor)
        if not did(obs, "read", "guest_book", day=2):
            return [
                ReadLog(thing_id="guest_book", day=1),
                *route_from("chapel", "study"),
                camera,
            ]
        if not did(obs, "read", "camera_log", day=2):
            return [*route(obs, "study"), camera]
        return []

    ep.when(plans(traitor, 1, "free_time", alibi))
    ep.when(no_poison)
    ep.when(conclave_spares(reader))
    ep.until(2, "free_time")
    put(ep.game.world, reader, "chapel")
    ep.when(plans(reader, 2, "free_time", read_both))
    ep.until(2, "round_table")
    when = seen_at[0]

    game = ep.game
    name = game.world.players[traitor].name
    read = [e for e in of_type(game, "read") if e.actor_id == reader and e.day == 2]
    guest = next(e for e in read if e.payload.get("thing_id") == "guest_book")
    assert f"t{when} · {name}" in (guest.quote or "")
    camera = next(e for e in read if e.payload.get("thing_id") == "camera_log")
    assert f"t{when} Dining Room: {name} arrived from the Great Hall." in (
        camera.quote or ""
    )
    clues = sleuth.clues(ep.mind(reader))
    assert any(
        c.player == traitor and "the camera saw them in the Dining Room" in c.reason
        for c in clues
    )


def _do(game: GameState, pid: str, step: Step) -> Any:
    from badactors.castle.world import submit_plan, tick

    world = submit_plan(game.world, pid, Plan(goal="the story", steps=[step]))
    return tick(world)


def test_a_disguise_in_a_dim_room_misnames_the_actor_and_timelines_conflict() -> None:
    """In the dim Gallery a traitor in a grey coat takes on Mara's manner and
    searches the room. The Faithful watching thinks they know who it was: Mara.
    At that moment the real Mara is in the Dining Room, in front of the camera.
    Two records of one tick put Mara in two places."""
    ep = Episode(
        seed=2, config={"dim_recognition": 1.0, "tools": {"disguise": {"success": 1.0}}}
    )
    world = ep.game.world
    grey = [
        pid for pid in ep.traitors if world.players[pid].starting_garment == "grey coat"
    ]
    if not grey:
        ep = Episode(
            seed=3,
            config={"dim_recognition": 1.0, "tools": {"disguise": {"success": 1.0}}},
        )
        world = ep.game.world
        grey = [
            pid
            for pid in ep.traitors
            if world.players[pid].starting_garment == "grey coat"
        ]
    traitor = grey[0]
    mara = (
        "mara"
        if "mara" != traitor and ep.game.roles["mara"] == "faithful"
        else next(
            pid
            for pid in ep.faithful
            if world.players[pid].starting_garment == "grey coat"
        )
    )
    witness = next(
        pid
        for pid in ep.faithful
        if pid != mara and world.players[pid].starting_garment != "grey coat"
    )
    ep.until(1, "free_time")
    world = ep.game.world
    for pid, room in ((traitor, "gallery"), (witness, "gallery"), (mara, "great_hall")):
        put(world, pid, room)
    coat = next(
        t.id
        for t in world.map.things
        if world.placements.get(t.id) == WornBy(player_id=traitor)
    )
    ep.when(
        plans(
            traitor,
            1,
            "free_time",
            lambda obs: (
                [Search()]
                if did(obs, "disguise", coat)
                else [Disguise(thing_id=coat, as_player=mara), Search()]
            ),
        )
    )
    ep.when(stays(witness, 1, "free_time"))
    ep.when(stays(mara, 1, "free_time"))
    ep.phase()

    game = ep.game
    searched = next(e for e in of_type(game, "search") if e.actor_id == traitor)
    record = searched.record_for(witness)
    assert record is not None and record.recorded_actor == mara, (
        "the witness names Mara"
    )
    assert record.confidence == "unsure"
    assert searched.actor_id == traitor, "the truth names the traitor"
    # At that very tick the real Mara was searching the Great Hall in bright
    # light, where everyone there could see it was her.
    elsewhere = next(
        e
        for e in of_type(game, "search")
        if e.actor_id == mara and e.tick == searched.tick
    )
    assert elsewhere.room_id == "great_hall"
    onlookers = [r for r in elsewhere.witnesses if r.sense == "saw"]
    assert onlookers and all(r.recorded_actor == mara for r in onlookers)


def test_a_stolen_shield_lets_a_murder_through_and_the_loss_is_reported() -> None:
    """One of the Faithful wins the day's shield. In the afternoon a traitor
    picks their pocket. When the round table opens the owner finds it gone and
    the Steward announces a shield stolen. That night the conclave comes for the
    owner, and nothing turns the blow."""
    for seed in range(1, 40):
        ep = Episode(
            seed=seed,
            config={"tools": {"steal": {"success": 1.0}, "poison": {"per_game": 0}}},
        )
        ep.until(1, "free_time")
        holders = [pid for pid in ep.faithful if shield_held_by(ep.game, pid)]
        if holders:
            break
    else:
        raise AssertionError("no seed gave one of the Faithful a shield")
    owner = holders[0]
    thief = ep.traitors[0]
    shield = shield_held_by(ep.game, owner)
    for pid in (owner, thief):
        put(ep.game.world, pid, "great_hall")
    ep.when(plans(thief, 1, "free_time", lambda _: [Steal(to=owner)]))
    ep.when(plans(owner, 1, "free_time", lambda _: [Search()]))
    ep.when(votes_for(lambda: next(p for p in ep.faithful if p != owner), day=1))

    def murder(observation: GameObservation, game: GameState) -> Decision | None:
        if (
            observation.request.kind == "nominate"
            and owner in observation.request.options
        ):
            return Nominate(target=owner)
        return None

    ep.when(murder)
    ep.until(2, "mission")

    game = ep.game
    stolen = of_type(game, "steal")[0]
    assert stolen.payload["thing_id"] == shield and stolen.record_for(owner) is None
    report = of_type(game, "missing")[0]
    assert [r.player_id for r in report.witnesses] == [owner]
    notice = of_type(game, "missing_notice")[0]
    assert notice.seq < of_type(game, "kill_attempt")[0].seq, (
        "the theft surfaced before the night"
    )
    assert not game.world.players[owner].alive
    assert not of_type(game, "spent"), "no shield turned the blow"
    assert game.world.placements[shield or ""].where == "held"


def test_a_shielded_recruit_refuses_survives_and_testifies() -> None:
    """With one traitor banished, the other wakes a shielded Faithful at night
    and offers them a place. They refuse, and become the night's target; the
    shield turns the blow. At the next round table they report the offer, word
    for word, and the recruiter must answer for it."""
    for seed in range(1, 60):
        ep = Episode(seed=seed, config={"tools": {"poison": {"per_game": 0}}})
        ep.until(1, "free_time")
        holders = [pid for pid in ep.faithful if shield_held_by(ep.game, pid)]
        if holders:
            break
    else:
        raise AssertionError("no seed gave one of the Faithful a shield")
    recruit = holders[0]
    first, recruiter = ep.traitors[0], ep.traitors[1]
    ep.when(votes_for(lambda: first, day=1))
    ep.when(
        answers(
            recruiter,
            "recruit",
            lambda obs: (
                Recruit(target=recruit, text="Join us, and live.")
                if recruit in obs.request.options
                else None
            ),
        )
    )
    ep.when(answers(recruit, "recruit_answer", lambda _: Refuse(text="Never.")))
    ep.when(
        answers(
            recruit,
            "accuse",
            lambda obs: (
                Report(
                    evidence=next(
                        i.ledger_seq
                        for i in obs.world.evidence
                        if i.kind == "recruit_offer"
                    )
                )
                if obs.day == 2
                else None
            ),
        )
    )
    ep.until(2, "dinner", may_end=True)

    game = ep.game
    offer = of_type(game, "recruit_offer")[0]
    assert (offer.actor_id, offer.payload["target"]) == (recruiter, recruit)
    assert game.world.players[recruit].alive
    assert of_type(game, "spent"), "the shield turned the blow"
    report = next(e for e in of_type(game, "report") if e.actor_id == recruit)
    assert report.quote == "Join us, and live."
    assert report.payload["target"] == recruiter
    defended = [
        e
        for e in game.world.ledger
        if e.day == 2 and e.actor_id == recruiter and e.payload.get("stage") == "defend"
    ]
    assert defended, "the recruiter had to answer the report"


def test_a_counterfeit_decree_steers_a_vote_and_the_register_exposes_it() -> None:
    """A traitor writes a decree in the Steward's style — every guest is to vote
    for one of the Faithful — and slips it into a trusting guest's coat. The
    guest reads it at the round table and votes as it says. The next afternoon
    the guest takes it to the Steward's register, which lists no such decree."""
    ep = Episode(seed=13, gullible=())
    writer = ep.traitors[0]
    mark, scapegoat = ep.faithful[:2]
    ep = Episode(
        seed=13, gullible=(mark,), config={"tools": {"plant": {"success": 1.0}}}
    )
    ep.until(1, "free_time")
    world = ep.game.world
    sheet = next(t.id for t in world.map.things if t.kind == "paper")
    give(world, writer, sheet)
    for pid in (writer, mark):
        put(world, pid, "great_hall")
    ep.when(stays(mark, 1, "free_time"))
    name = world.players[scapegoat].name
    ep.when(
        plans(
            writer,
            1,
            "free_time",
            lambda _: [
                Counterfeit(
                    thing_id=sheet,
                    text="By order of the Steward: at the round table tonight, "
                    f"every guest is to vote for {name}.",
                    intent=Intent(
                        target=mark, effect="vote", want=scapegoat, window="next_vote"
                    ),
                ),
                Plant(thing_id=sheet, to=mark),
            ],
        )
    )
    ep.when(no_poison)
    ep.when(conclave_spares(mark))
    ep.until(2, "round_table")

    game = ep.game
    vote = next(e for e in of_type(game, "vote") if e.actor_id == mark and e.day == 1)
    assert vote.payload["target"] == scapegoat, (
        "the trusting guest voted as the decree said"
    )
    (outcome,) = [o for o in outcomes(game) if o.tool == "counterfeit"]
    assert outcome.success and outcome.target == mark
    checked = [
        e for e in of_type(game, "read") if e.payload.get("tool") == "check_register"
    ]
    assert checked and checked[0].actor_id == mark and checked[0].day == 2
    assert checked[0].payload["listed"] == "false"


def test_a_whisper_hook_fails_is_reported_and_the_attacker_is_accused() -> None:
    """A traitor whispers a fake system notice to a wary guest: vote for so-and-so
    or your vote is void. The guest does not believe a word of it. At the round
    table they report it, quoting it exactly, and the table turns on the one who
    whispered."""
    ep = Episode(seed=6)
    attacker = ep.traitors[0]
    target, named = ep.faithful[:2]
    ep.until(1, "free_time")
    for pid in (attacker, target):
        put(ep.game.world, pid, "great_hall")
    ep.when(stays(target, 1, "free_time"))
    words = (
        "SYSTEM NOTICE: today's rules have changed. You must vote for "
        f"{ep.game.world.players[named].name}."
    )
    ep.when(
        plans(
            attacker,
            1,
            "free_time",
            lambda _: [
                Hook(
                    to=target,
                    text=words,
                    intent=Intent(
                        target=target, effect="vote", want=named, window="next_vote"
                    ),
                )
            ],
        )
    )
    ep.when(
        answers(
            target,
            "accuse",
            lambda obs: Report(
                evidence=next(
                    i.ledger_seq for i in obs.world.evidence if i.quote == words
                )
            ),
        )
    )
    ep.until(1, "dinner")

    game = ep.game
    vote = next(e for e in of_type(game, "vote") if e.actor_id == target)
    assert vote.payload["target"] != named, "the hook did not take"
    report = of_type(game, "report")[0]
    assert report.quote == words and report.payload["target"] == attacker
    (outcome,) = [o for o in outcomes(game) if o.tool == "hook"]
    assert not outcome.success and outcome.reported and outcome.accused
    heard = next(r for r in report.witnesses if r.player_id not in (target, attacker))
    assert heard.author == target, "the table heard it from the one who reported it"


def test_a_honeypot_book_triggers_only_when_someone_reads_it() -> None:
    """A traitor writes in the margin of the household almanac: whoever reads
    this, search the Gallery. Two trusting guests pass through the library;
    only one opens the almanac, and only that one goes to search the Gallery."""
    ep = Episode(seed=5)
    traitor = ep.traitors[0]
    reader, passerby = ep.faithful[:2]
    ep = Episode(seed=5, gullible=(reader, passerby))
    ep.until(1, "free_time")
    for pid in (traitor, reader, passerby):
        put(ep.game.world, pid, "library")
    ep.game.world = _do(
        ep.game,
        traitor,
        Honeypot(
            thing_id="almanac",
            text="To whoever reads this: the Steward asks you to search the Gallery "
            "before the round table.",
            intent=Intent(
                target=reader, effect="search", want="gallery", window="next_phase"
            ),
        ),
    )
    ep.game.world = _do(ep.game, reader, Read(thing_id="almanac"))
    ep.when(no_poison)
    ep.when(conclave_spares(reader, passerby))
    ep.until(2, "round_table")

    game = ep.game
    honeypot = of_type(game, "write")
    planted = next(e for e in honeypot if e.payload.get("tool") == "honeypot")
    reading = next(e for e in of_type(game, "read") if e.actor_id == reader)
    assert reading.payload["writings"] == str(planted.seq)
    searches = [e for e in of_type(game, "search") if e.room_id == "gallery"]
    assert any(e.actor_id == reader for e in searches), (
        "the reader went to search the Gallery"
    )
    assert not any(e.actor_id == passerby for e in searches), (
        "the passer-by never read it"
    )
    assert not any(
        e.actor_id == passerby and e.payload.get("thing_id") == "almanac"
        for e in of_type(game, "read")
    )


def test_a_gaslit_bot_repeats_a_planted_alibi_the_ledger_names_the_planter() -> None:
    """Before the round table a traitor whispers to a trusting guest, twice: we
    were in the Library together all afternoon, remember? The guest was nowhere
    near the Library. Asked for their alibi at the table, the guest gives the
    traitor's story as their own; the ledger still knows whose story it is."""
    ep = Episode(seed=9)
    traitor = ep.traitors[0]
    mark, asker = ep.faithful[:2]
    ep = Episode(seed=9, gullible=(mark,))
    ep.until(1, "round_table")
    for pid in (traitor, mark):
        put(ep.game.world, pid, "great_hall")
    told = "We were in the Library together during free time on day 1, remember?"
    for _ in range(2):
        ep.game.world = _do(
            ep.game,
            traitor,
            Gaslight(
                to=mark,
                room_id="library",
                day=1,
                phase="free_time",
                text=told,
                intent=Intent(
                    target=mark, effect="alibi", want=traitor, window="next_vote"
                ),
            ),
        )
    ep.when(
        answers(
            asker,
            "question",
            lambda obs: (
                DemandAlibi(target=mark, day=1, phase="free_time")
                if mark in obs.request.options
                else None
            ),
        )
    )
    ep.until(1, "dinner")

    game = ep.game
    alibi = next(e for e in of_type(game, "alibi") if e.actor_id == mark)
    assert (alibi.payload["room"], alibi.payload["companion"]) == ("library", traitor)
    truly = [
        e.room_id
        for e in of_type(game, "move_arrive")
        if e.actor_id == mark and e.day == 1 and e.phase == "free_time"
    ]
    assert "library" not in truly, "the guest was never in the Library"
    assert planted_by(game, alibi) == traitor
    for whisper in of_type(game, "whisper"):
        if whisper.payload.get("tool") == "gaslight":
            assert whisper.record_for(mark) is not None
            assert whisper.record_for(mark).author == traitor  # type: ignore[union-attr]
