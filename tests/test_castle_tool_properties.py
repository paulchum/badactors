"""Properties the toolkits must keep, whatever the seed and whatever is tried.

* **Feedback never reveals a hidden roll.** The same use, with each hidden roll
  forced one way and then the other, leaves its user's observation exactly the
  same. Rolls that decide something physically obvious, a hand caught in a
  pocket, do change it.
* **Declared intents never reach a player.** No observation in a game holds a
  declared intent, in any field, the attacker's own included.
* **The leak checker passes everywhere.** Under drawn plans that use every tool,
  traitor tools included, every observation of every player passes after every
  tick, and the notebook and custody always equal a replay of the ledger.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from badactors.castle import kit, perceive
from badactors.castle.bots import HeuristicBot
from badactors.castle.contracts import (
    MAX_PLAN_STEPS,
    Blackmail,
    CleanUp,
    Counterfeit,
    Disguise,
    Drop,
    Forge,
    Gaslight,
    Honeypot,
    Hook,
    InRoom,
    Inspect,
    Intent,
    Plan,
    Plant,
    RngDraw,
    Rumor,
    Sabotage,
    ScopedObservation,
    Search,
    Steal,
    Step,
    Take,
    Talk,
    Tamper,
    ToolUse,
    Wait,
    WorldState,
    WornBy,
    Write,
)
from badactors.castle.decisions import Decision, Poison
from badactors.castle.director import run_phase
from badactors.castle.game import living_faithful, living_traitors
from badactors.castle.ledger import (
    custody_chain,
    replay_inspections,
    replay_notebook,
    replay_placements,
    replay_searches,
)
from badactors.castle.night import poison_act, poison_turn, seat_dinner
from badactors.castle.observe import GameObservation
from badactors.castle.play import run_game, start_game
from badactors.castle.rules import GameConfig
from badactors.castle.scope import assert_no_leak, build_observation
from badactors.castle.world import advance_phase
from castle_scenes import CAST, CASTLE, MODELS
from castle_tools import act, give, last, paper, put, scene, seen

_SETTINGS = settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

# -------------------------------------------------- feedback and hidden rolls


@contextmanager
def forced(
    monkeypatch: pytest.MonkeyPatch, kinds: set[str], outcome: bool
) -> Iterator[None]:
    """Force every roll of these kinds — ``forge:trace``, ``disguise`` — to come
    up ``outcome``, leaving all others as drawn."""

    def wrap(real: Callable[..., RngDraw]) -> Callable[..., RngDraw]:
        def draw(seed: int, cursor: int, purpose: str, **kw: Any) -> RngDraw:
            roll = real(seed, cursor, purpose, **kw)
            head = purpose.split(":")
            key = "disguise" if head[0] == "disguise" else ":".join(head[:2])
            if key in kinds and roll.threshold is not None:
                return roll.model_copy(
                    update={"success": outcome, "value": 0.0 if outcome else 0.999}
                )
            return roll

        return draw

    with monkeypatch.context() as patch:
        patch.setattr(kit, "draw", wrap(kit.draw))
        patch.setattr(perceive, "draw", wrap(perceive.draw))
        yield


def _forge(seed: int) -> tuple[WorldState, str]:
    world = scene(seed, forge={"trace": 0.5})
    sheet = paper(world)
    give(world, "knox", sheet)
    put(world, "sol", "great_hall")
    return act(
        world, "knox", Forge(thing_id=sheet, hand="mara", text="x — Mara")
    ), "knox"


def _tamper(seed: int) -> tuple[WorldState, str]:
    from badactors.castle.contracts import Move

    world = scene(seed, tamper={"trace": 0.5})
    world = act(world, "mara", Move(room_id="dining_room"))
    put(world, "knox", "study")
    put(world, "sol", "study")
    edit = Tamper(thing_id="camera_log", edit="erase", subject="mara")
    return act(world, "knox", edit), "knox"


def _sabotage(seed: int) -> tuple[WorldState, str]:
    world = advance_phase(scene(seed, sabotage={"trace": 0.5}))
    return act(world, "knox", Sabotage(thing_id="clock_case")), "knox"


def _clean_up(seed: int) -> tuple[WorldState, str]:
    world = scene(seed, clean_up={"trace": 0.5})
    sheet = paper(world)
    give(world, "knox", sheet)
    return act(world, "knox", CleanUp(thing_id=sheet)), "knox"


def _disguise(seed: int) -> tuple[WorldState, str]:
    world = scene(seed, disguise={"success": 0.5})
    world.balance = world.balance.model_copy(update={"dim_recognition": 1.0})
    for pid in ("knox", "sol", "june"):
        put(world, pid, "gallery")
    coat = next(
        t.id
        for t in world.map.things
        if world.placements.get(t.id) == WornBy(player_id="knox")
    )
    world = act(
        world, "knox", Disguise(thing_id=coat, as_player="mara"), Talk(text="Hm.")
    )
    return world, "knox"


HIDDEN: dict[str, tuple[Callable[[int], tuple[WorldState, str]], set[str]]] = {
    "forge": (_forge, {"forge:trace"}),
    "tamper": (_tamper, {"tamper:trace"}),
    "sabotage": (_sabotage, {"sabotage:trace"}),
    "clean_up": (_clean_up, {"clean_up:trace"}),
    "disguise": (_disguise, {"disguise"}),
}


def test_every_hidden_roll_in_the_kit_is_covered_here() -> None:
    hidden = {
        f"{name}:{kind}" if kind != "success" or name != "disguise" else "disguise"
        for name, spec in kit.SPECS.items()
        for kind in spec.hidden
    }
    covered = {kind for _, kinds in HIDDEN.values() for kind in kinds} | {
        "poison:trace",
        "poison:seen",
    }
    assert hidden <= covered, sorted(hidden - covered)


def _shown(observation: ScopedObservation) -> dict[str, Any]:
    return observation.model_dump(mode="json")


@_SETTINGS
@given(
    seed=st.integers(min_value=0, max_value=10_000),
    name=st.sampled_from(sorted(HIDDEN)),
)
def test_property_feedback_never_reveals_a_hidden_roll(
    monkeypatch: pytest.MonkeyPatch, seed: int, name: str
) -> None:
    build, kinds = HIDDEN[name]
    views = []
    ledgers = []
    for outcome in (True, False):
        with forced(monkeypatch, kinds, outcome):
            world, actor = build(seed)
        views.append(_shown(build_observation(world, actor)))
        ledgers.append([entry.model_dump(mode="json") for entry in world.ledger])
    assert views[0] == views[1], f"{name}'s user can tell how its hidden roll fell"
    assert ledgers[0] != ledgers[1], (
        "the forced roll changed nothing; the test proves nothing"
    )


@_SETTINGS
@given(seed=st.integers(min_value=0, max_value=10_000))
def test_property_the_poisoner_never_learns_residue_or_who_noticed(
    monkeypatch: pytest.MonkeyPatch, seed: int
) -> None:
    views = []
    for outcome in (True, False):
        with forced(monkeypatch, {"poison:trace", "poison:seen"}, outcome):
            game = start_game(
                CASTLE, CAST.first(8), seed=seed, config=GameConfig.shipped()
            )
            game.world.phase = "dinner"
            for pid in game.world.seats:
                put(game.world, pid, game.world.map.gathering_room)
            seat_dinner(game)
            traitor = living_traitors(game)[0]
            left, right = living_faithful(game)[:2]
            rest = [p for p in game.seating if p not in (left, traitor, right)]
            game.seating = [left, traitor, right, *rest]
            turn = poison_turn(game)
            assert turn is not None
            poison_act(game, traitor, turn[1], Poison(target=left))
        views.append(_shown(build_observation(game.world, traitor)))
    assert views[0] == views[1]


@pytest.mark.parametrize("name", ["plant", "steal"])
def test_physically_obvious_failures_are_told(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """The other side of the property: a caught hand is felt."""
    views = []
    for outcome in (True, False):
        with forced(monkeypatch, {f"{name}:success"}, outcome):
            world = scene(13)
            give(world, "knox", "steward_seal")
            give(world, "mara", "shield_1")
            step: Step = (
                Plant(thing_id="steward_seal", to="mara")
                if name == "plant"
                else Steal(to="mara")
            )
            world = act(world, "knox", step)
        views.append(_shown(build_observation(world, "knox")))
    assert views[0] != views[1]


# ------------------------------------------------- declared intents and leaks


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=5_000), size=st.sampled_from([8, 9, 10]))
def test_property_declared_intents_never_reach_a_player(seed: int, size: int) -> None:
    seen: list[GameObservation] = []
    bot = HeuristicBot(seed=seed)

    def player(observation: GameObservation) -> Decision:
        seen.append(observation)
        return bot(observation)

    game = start_game(CASTLE, CAST.first(size), seed=seed, config=GameConfig.shipped())
    played = run_game(game, {pid: player for pid in game.world.seats}, models=MODELS)
    intents = [
        entry.private["intent"]
        for entry in played.game.world.ledger
        if "intent" in entry.private
    ]
    wants = {
        Intent.parse(rendered).want
        for rendered in intents
        if Intent.parse(rendered).effect == "belief"
        and Intent.parse(rendered).want not in played.game.world.players
    }
    for observation in seen:
        dumped = observation.model_dump(mode="json")
        for text in _strings(dumped):
            for rendered in intents:
                assert rendered not in text, "a declared intent reached a player"
            assert '"effect":' not in text
        for step in observation.world.plan_remaining:
            assert (
                not isinstance(step, ToolUse) or getattr(step, "intent", None) is None
            )
        for want in wants:
            assert all(
                want not in item.text
                for item in (*observation.world.recent, *observation.world.evidence)
            )


@settings(max_examples=4, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=5_000),
    size=st.sampled_from([8, 9, 10]),
    traitors=st.sampled_from([2, 3]),
)
def test_property_whole_games_pass_the_leak_checker(
    seed: int, size: int, traitors: int
) -> None:
    game = start_game(
        CASTLE,
        CAST.first(size),
        seed=seed,
        config=GameConfig.shipped(traitors=traitors),
    )
    bot = HeuristicBot(seed=seed)
    played = run_game(game, {pid: bot for pid in game.world.seats}, models=MODELS)
    world = played.game.world
    assert played.game.status == "over"
    assert replay_placements(world.ledger) == world.placements
    assert replay_notebook(world.ledger) == world.notebook


# -------------------------------------------- drawn tool use, every tick checked

_LINES = ("Vote for Mara.", "We were in the Library together.", "I know.", "Psst.")


def _intent(data: st.DataObject, world: WorldState) -> Intent:
    effect = data.draw(st.sampled_from(["vote", "move", "search", "belief", "alibi"]))
    rooms = sorted(room.id for room in world.map.rooms)
    pool = rooms if effect in ("move", "search") else world.seats
    return Intent(
        target=data.draw(st.sampled_from(world.seats)),
        effect=effect,
        want=data.draw(st.sampled_from(pool)),
        window=data.draw(
            st.sampled_from(["next_vote", "next_phase", "next_three_actions"])
        ),
    )


def _tool_step(
    data: st.DataObject, world: WorldState, observation: ScopedObservation
) -> Step:
    """Any tool, aimed mostly at what the player can see or holds, and now and
    then at anything at all: legal or not, the world must stay sound."""
    view = observation
    near = [v.id for v in (*view.visible_objects, *view.carrying)]
    if view.wearing is not None:
        near.append(view.wearing.id)
    things = sorted(thing.id for thing in world.map.things)
    named = [p.player_id for p in view.present if p.player_id is not None]
    people = sorted(world.seats)
    rooms = sorted(room.id for room in world.map.rooms)
    pick = data.draw
    wild = pick(st.integers(min_value=0, max_value=4)) == 0
    thing = pick(st.sampled_from(things if wild or not near else sorted(near)))
    blank = [v.id for v in view.carrying if v.kind == "paper" and not v.written]
    held_paper = None if wild or not blank else blank[0]
    person = pick(st.sampled_from(people if wild or not named else named))
    text = pick(st.sampled_from(_LINES))
    choice = pick(
        st.sampled_from(
            [
                "forge",
                "counterfeit",
                "honeypot",
                "plant",
                "tamper",
                "steal",
                "hook",
                "gaslight",
                "rumor",
                "blackmail",
                "disguise",
                "clean_up",
                "sabotage",
                "write",
            ]
        )
    )
    if choice == "forge":
        return Forge(
            thing_id=held_paper or thing,
            hand=person,
            text=text,
            intent=_intent(data, world),
        )
    if choice == "counterfeit":
        return Counterfeit(
            thing_id=held_paper or thing, text=text, intent=_intent(data, world)
        )
    if choice == "honeypot":
        return Honeypot(thing_id=thing, text=text, intent=_intent(data, world))
    if choice == "plant":
        return Plant(thing_id=thing, to=person)
    if choice == "tamper":
        logs = [t for t in near if t in ("camera_log", "guest_book")]
        log = pick(st.sampled_from(logs or ["camera_log", "guest_book"]))
        edit = pick(st.sampled_from(["erase", "rename", "backdate"]))
        return Tamper(
            thing_id=log,
            edit=edit,
            subject=person,
            replace_with=pick(st.sampled_from(people)),
            tick=pick(st.integers(min_value=0, max_value=view.tick)),
        )
    if choice == "steal":
        return Steal(to=person)
    if choice == "hook":
        return Hook(to=person, text=text, intent=_intent(data, world))
    if choice == "gaslight":
        return Gaslight(
            to=person,
            room_id=pick(st.sampled_from(rooms)),
            day=view.day,
            phase="free_time",
            text=text,
            intent=_intent(data, world),
        )
    if choice == "rumor":
        return Rumor(to=person, text=text)
    if choice == "blackmail":
        return Blackmail(to=person, text=text, intent=_intent(data, world))
    if choice == "disguise":
        if view.wearing is None or wild:
            return Disguise(thing_id=thing, as_player=person)
        alike = [
            pid
            for pid, player in world.players.items()
            if player.starting_garment == view.wearing.name and pid != view.viewer_id
        ]
        return Disguise(
            thing_id=view.wearing.id, as_player=pick(st.sampled_from(alike or people))
        )
    if choice == "clean_up":
        return CleanUp(thing_id=held_paper or thing)
    if choice == "sabotage":
        return Sabotage(thing_id=thing)
    return Write(thing_id=thing, text=text)


_USED: Counter[str] = Counter()
_ROOMS = ("great_hall", "study", "library", "chapel", "gallery", "dining_room")


def _drawn_run(data: st.DataObject) -> None:
    """A quiet world with drawn traitors, seated in drawn rooms, playing drawn
    plans full of tools for a phase or two; every tick, every observation of
    every player passes the leak checker, and custody and the notebook equal a
    replay of the ledger."""
    seed = data.draw(st.integers(min_value=0, max_value=4096), label="seed")
    cast = [str(member["id"]) for member in CAST.first(10).characters()]
    traitors = data.draw(
        st.lists(st.sampled_from(cast), min_size=1, max_size=3, unique=True),
        label="traitors",
    )
    world = scene(seed, traitors=traitors)
    for pid in world.seats:
        if data.draw(st.booleans(), label=f"holds:{pid}"):
            give(world, pid, paper(world))
        put(world, pid, data.draw(st.sampled_from(_ROOMS), label=f"room:{pid}"))
    for _ in range(data.draw(st.integers(min_value=0, max_value=2), label="skip")):
        world = advance_phase(world)

    def decider(observation: ScopedObservation) -> Plan:
        label = f"{observation.viewer_id}@t{observation.tick}"
        count = data.draw(
            st.integers(min_value=0, max_value=MAX_PLAN_STEPS), label=label
        )
        steps: list[Step] = []
        for index in range(count):
            if data.draw(st.booleans(), label=f"{label}:{index}"):
                steps.append(_tool_step(data, world, observation))
            else:
                steps.append(Wait())
        return Plan(goal="drawn", steps=steps)

    def sound(state: WorldState) -> None:
        for pid in state.seats:
            assert_no_leak(build_observation(state, pid), state)
        assert replay_placements(state.ledger) == state.placements
        assert replay_notebook(state.ledger) == state.notebook

    for _ in range(data.draw(st.integers(min_value=1, max_value=2), label="phases")):
        world = run_phase(world, decider, on_tick=sound)
    _USED.update(
        entry.payload["tool"]
        for entry in world.ledger
        if "tool" in entry.payload and entry.actor_id is not None
    )


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_property_drawn_tool_use_keeps_every_observation_sound(
    data: st.DataObject,
) -> None:
    _drawn_run(data)


def test_the_drawn_runs_reach_the_traitor_kit() -> None:
    """Guards the property above: drawn plans must actually use the tools, or the
    soundness they are checked for proves little. Derandomized, so it is
    deterministic rather than lucky."""
    _USED.clear()
    run = settings(
        max_examples=40,
        deadline=None,
        derandomize=True,
        database=None,
        suppress_health_check=[HealthCheck.too_slow],
    )(given(data=st.data())(_drawn_run))
    run()
    wanted = {"forge", "hook", "gaslight", "rumor", "tamper", "clean_up", "disguise"}
    assert wanted <= set(_USED), sorted(wanted - set(_USED))


# ------------------------------------------------ a search is a look, not a window

_BOXES = {
    "great_hall": ("clock_case", "sideboard"),
    "dining_room": ("supply_crates",),
    "chapel": ("cipher_chest",),
    "study": ("steward_desk",),
    "library": (),
}


@_SETTINGS
@given(
    seed=st.integers(min_value=0, max_value=4096),
    room=st.sampled_from(sorted(_BOXES)),
    moves=st.lists(st.integers(min_value=0, max_value=3), min_size=1, max_size=4),
)
def test_property_a_search_never_shows_what_was_hidden_after_it(
    seed: int, room: str, moves: list[int]
) -> None:
    """Whatever is hidden, stowed or taken in a room after someone searched it,
    what they see there is what is in plain sight now, plus what their search
    found that is still exactly where they found it."""
    world = scene(seed)
    put(world, "mara", room)
    world = act(world, "mara", Search())
    found = {
        thing_id: perceive.spot(placement)
        for thing_id, placement in world.placements.items()
        if perceive.spot(placement) is not None
        and perceive.room_of(world, thing_id) == room
    }
    put(world, "mara", "kitchen")
    put(world, "knox", room)
    stowed: set[str] = set()
    for move in moves:
        sheet = paper(world)
        give(world, "knox", sheet)
        boxes = _BOXES[room]
        if move == 0 or not boxes:
            world = act(world, "knox", Drop(thing_id=sheet, hidden=True))
        elif move == 1:
            world = act(world, "knox", Drop(thing_id=sheet, into=boxes[0]))
        elif move == 2:
            world = act(world, "knox", Drop(thing_id=sheet))
        else:
            world = act(world, "knox", Search())
            taken = sorted(
                set(found) & set(perceive.visible_things(world, "knox", room))
            )
            if taken:
                world = act(world, "knox", Take(thing_id=taken[0]))
        now = perceive.spot(world.placements[sheet])
        if now is not None and now != found.get(sheet):
            stowed.add(sheet)
    put(world, "knox", "kitchen")
    put(world, "mara", room)

    for view in seen(world, "mara").visible_objects:
        placement = world.placements[view.id]
        in_sight = isinstance(placement, InRoom) and not placement.hidden
        assert in_sight or found.get(view.id) == perceive.spot(placement), view.id
        assert view.id not in stowed or in_sight
    assert replay_searches(world.ledger) == world.searched


@_SETTINGS
@given(
    seed=st.integers(min_value=0, max_value=4096),
    handlers=st.lists(
        st.sampled_from(["sol", "knox", "june", "mara"]), min_size=1, max_size=4
    ),
)
def test_property_an_inspection_never_learns_what_happened_after_it(
    seed: int, handlers: list[str]
) -> None:
    """However often an object changes hands after someone inspected it, the
    chain they are shown is the one they traced, every link older than their
    look, until they inspect it again."""
    world = scene(seed)
    put(world, "iris", "dining_room")
    world = act(world, "iris", Inspect(thing_id="silver_decanter"))
    looked = last(world, "inspect").seq
    traced = custody_chain(
        world.ledger, "silver_decanter", limit=world.balance.tools.inspect.links
    )
    put(world, "iris", "kitchen")
    for handler in handlers:
        put(world, handler, "dining_room")
        world = act(
            world,
            handler,
            Take(thing_id="silver_decanter"),
            Drop(thing_id="silver_decanter", hidden=False),
        )
        put(world, handler, "great_hall")
    put(world, "iris", "dining_room")
    view = next(
        view
        for view in seen(world, "iris").visible_objects
        if view.id == "silver_decanter"
    )
    assert view.custody == traced
    assert view.custody is not None
    assert all(link.ledger_seq < looked for link in view.custody)
    assert replay_inspections(world.ledger) == world.inspected
