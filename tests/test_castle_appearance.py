"""What the household looks like, and what the light lets anyone see of it.

Everyone has a public build and a garment that is a real object, and garments are
shared: a dim witness whose roll fails records only the garment, so it must never
single anyone out. Bright light names people, dim light shows garments, the dark
shows silhouettes, and no record or view outside bright light ever carries a name,
a build or a distinctive detail. Changing garments is how disguise works.
"""

from __future__ import annotations

from collections import Counter

import pytest

from badactors.castle.bots import HeuristicBot
from badactors.castle.contracts import (
    GARMENT,
    Drop,
    Move,
    Plan,
    Presence,
    Step,
    Take,
    Talk,
    Wait,
    Wear,
    Whisper,
    WorldState,
    WornBy,
)
from badactors.castle.decisions import Request
from badactors.castle.game import GameState
from badactors.castle.ledger import custody_chain, replay_placements
from badactors.castle.mapfile import CastSpecError, build_world
from badactors.castle.observe import GameObservation, assert_no_game_leak, observe_game
from badactors.castle.perceive import garments, worn_garment
from badactors.castle.play import run_game, start_game
from badactors.castle.scope import LeakageError, assert_no_leak, build_observation
from badactors.castle.world import start, submit_plan, tick
from castle_scenes import CAST, CASTLE, MODELS


def _world(seed: int = 13, players: int = 8) -> WorldState:
    return start(
        build_world(CASTLE, CAST.first(players).characters(), world_id="w", seed=seed)
    )


def _walk(world: WorldState, player_id: str, *rooms: str) -> WorldState:
    for room_id in rooms:
        world = submit_plan(world, player_id, Plan(steps=[Move(room_id=room_id)]))
        for _ in range(8):
            world = tick(world)
            if world.players[player_id].room_id == room_id:
                break
    assert world.players[player_id].room_id == rooms[-1]
    return world


def _do(world: WorldState, player_id: str, *steps: Step) -> WorldState:
    world = submit_plan(world, player_id, Plan(steps=list(steps)))
    for _ in steps:
        world = tick(world)
    return world


def _garment_of(world: WorldState, player_id: str) -> str:
    garment = worn_garment(world, player_id)
    assert garment is not None
    return garment.id


# ----------------------------------------------------------------- the cast lint


def test_a_garment_worn_by_one_player_fails_the_lint() -> None:
    characters = CAST.first(8).characters()
    characters[0] = {**characters[0], "garment": "red cloak"}
    with pytest.raises(CastSpecError, match="Only 1 of the cast start in a red cloak"):
        build_world(CASTLE, characters, world_id="w", seed=1)


def test_three_wearers_are_required_when_the_cast_allows_it() -> None:
    # Ten players and two garments: every garment can have three, so a pair fails.
    characters = CAST.first(10).characters()
    for index, character in enumerate(characters):
        garment = "grey coat" if index < 8 else "dark shawl"
        characters[index] = {**character, "garment": garment}
    with pytest.raises(CastSpecError, match="at least 3 wearers"):
        build_world(CASTLE, characters, world_id="w", seed=1)


def test_the_shipped_cast_passes_at_every_size() -> None:
    for size in (8, 9, 10):
        world = _world(players=size)
        counts = Counter(p.starting_garment for p in world.players.values())
        assert min(counts.values()) >= 2, (size, counts)


def test_every_player_starts_in_a_real_garment_with_custody() -> None:
    world = _world()
    for pid in world.seats:
        garment = worn_garment(world, pid)
        assert garment is not None and garment.kind == GARMENT
        assert garment.name == world.players[pid].starting_garment
        assert world.placements[garment.id] == WornBy(player_id=pid)
        assert custody_chain(world.ledger, garment.id)


def test_a_garments_id_does_not_say_who_first_wore_it() -> None:
    maps = []
    for seed in range(6):
        world = _world(seed=seed)
        maps.append(
            {
                thing.id: placement.player_id
                for thing in garments(world)
                if isinstance(placement := world.placements[thing.id], WornBy)
            }
        )
    assert any(mapping != maps[0] for mapping in maps[1:])


# ---------------------------------------------------------- what records say


def test_at_game_start_no_dim_record_identifies_exactly_one_player() -> None:
    """On the first day, before anyone dies or changes, every garment a dim
    record names is worn by at least two of the living."""
    checked = 0
    for seed in range(12):
        game = start_game(CASTLE, CAST.first(8 + seed % 3), seed=seed)
        bot = HeuristicBot(seed=seed)
        game = run_game(game, {pid: bot for pid in game.world.seats}).game
        world = game.world
        wearers = Counter(p.starting_garment for p in world.players.values())
        for entry in world.ledger:
            if entry.day != 1 or entry.room_id is None:
                continue
            if world.map.room(entry.room_id).lighting != "dim":
                continue
            for record in entry.witnesses:
                if record.sense != "saw" or record.recorded_actor is not None:
                    continue
                named = [g for g in wearers if f"someone in a {g} " in record.text]
                assert len(named) == 1, record.text
                assert wearers[named[0]] >= 2, (seed, record.text)
                checked += 1
    assert checked, "expected some unrecognised dim sightings on day one"


def test_wearing_another_players_garment_changes_what_dim_witnesses_record() -> None:
    world = _world(seed=3)
    shawl = _garment_of(world, "sol")
    coat = _garment_of(world, "mara")
    assert world.players["sol"].starting_garment == "dark shawl"
    assert world.players["mara"].starting_garment == "grey coat"
    world = _do(world, "sol", Drop(thing_id=shawl))
    world = _do(world, "mara", Take(thing_id=shawl), Wear(thing_id=shawl))
    assert world.placements[shawl] == WornBy(player_id="mara")
    assert world.placements[coat].where == "held"

    for pid in ("mara", "iris", "knox", "june"):
        world = _walk(world, pid, "gallery")
    assert world.map.room("gallery").lighting == "dim"
    before = world.ledger_seq
    for line in ("Who is there?", "Only me.", "Keep your voice down."):
        world = _do(world, "mara", Talk(text=line))
    unrecognised = [
        record.text
        for entry in world.ledger
        if entry.seq > before and entry.actor_id == "mara"
        for record in entry.witnesses
        if record.sense == "saw" and record.recorded_actor is None
    ]
    assert unrecognised, "expected at least one failed recognition roll"
    for text in unrecognised:
        assert text.startswith("someone in a dark shawl"), text
        assert "grey coat" not in text


def test_bright_light_still_names_the_wearer() -> None:
    world = _world(seed=3)
    shawl = _garment_of(world, "sol")
    world = _do(world, "sol", Drop(thing_id=shawl))
    world = _do(world, "mara", Take(thing_id=shawl), Wear(thing_id=shawl))
    change = next(e for e in world.ledger if e.type == "wear")
    record = change.record_for("iris")
    assert record is not None and record.recorded_actor == "mara"
    assert record.text == "Mara changed into a dark shawl."


def test_a_garment_change_is_witnessed_and_both_garments_keep_custody() -> None:
    world = _world(seed=3)
    shawl, coat = _garment_of(world, "sol"), _garment_of(world, "mara")
    world = _do(world, "sol", Drop(thing_id=shawl))
    world = _do(world, "mara", Take(thing_id=shawl), Wear(thing_id=shawl))
    world = _walk(world, "mara", "dining_room", "kitchen", "cellar")
    world = _do(world, "mara", Drop(thing_id=coat, hidden=True))

    assert replay_placements(world.ledger) == world.placements
    shawl_chain = [link.text for link in custody_chain(world.ledger, shawl)]
    assert shawl_chain[-3:] == [
        "Sol took off and set down the dark shawl.",
        "Mara took the dark shawl.",
        "Mara put on a dark shawl.",
    ]
    coat_chain = [link.text for link in custody_chain(world.ledger, coat)]
    assert coat_chain[-2:] == [
        "Mara took off a grey coat.",
        "Mara hid the grey coat.",
    ]
    change = next(e for e in world.ledger if e.type == "wear")
    assert {r.player_id for r in change.witnesses if r.sense == "saw"} >= {"sol"}


def test_someone_with_no_garment_is_conspicuous_in_dim_light() -> None:
    world = _world(seed=5)
    for pid in ("mara", "iris", "knox", "june"):
        world = _walk(world, pid, "gallery")
    before = world.ledger_seq
    world = _do(world, "mara", Drop(thing_id=_garment_of(world, "mara")))
    for _ in range(3):
        world = _do(world, "mara", Wait())
    texts = [
        record.text
        for entry in world.ledger
        if entry.seq > before and entry.actor_id == "mara" and entry.type == "wait"
        for record in entry.witnesses
        if record.sense == "saw" and record.recorded_actor is None
    ]
    assert texts and all(text.startswith("someone in shirtsleeves") for text in texts)


# ------------------------------------------------------------ who is present


def test_presence_follows_the_light() -> None:
    world = _world(seed=2)
    bright = build_observation(world, "mara").present
    assert {p.player_id for p in bright} == set(world.seats) - {"mara"}
    iris = next(p for p in bright if p.player_id == "iris")
    assert (iris.label, iris.garment, iris.detail) == (
        "Iris",
        "canvas jacket",
        "ink-stained cuffs",
    )

    for pid in ("mara", "iris", "knox"):
        world = _walk(world, pid, "gallery")
    dim = build_observation(world, "mara").present
    assert dim and all(p.player_id is None and p.detail is None for p in dim)
    assert sorted(p.label for p in dim) == [
        "someone in a canvas jacket",
        "someone in a grey coat",
    ]

    for pid in ("mara", "iris", "knox"):
        world = _walk(world, pid, "great_hall", "dining_room", "kitchen", "cellar")
    dark = build_observation(world, "mara").present
    assert [(p.label, p.player_id, p.garment) for p in dark] == [
        ("someone", None, None),
        ("someone", None, None),
    ]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"player_id": "iris"}, "names someone"),
        ({"detail": "ink-stained cuffs"}, "distinctive detail"),
        ({"label": "Iris"}, "more than garments"),
    ],
)
def test_planted_leak_presence_beyond_what_dim_light_shows(
    change: dict[str, object], match: str
) -> None:
    world = _world(seed=2)
    for pid in ("mara", "iris", "knox"):
        world = _walk(world, pid, "gallery")
    observation = build_observation(world, "mara")
    first = observation.present[0]
    leaked = observation.model_copy(
        update={"present": [first.model_copy(update=change), *observation.present[1:]]}
    )
    with pytest.raises(LeakageError, match=match):
        assert_no_leak(leaked, world)


def test_planted_leak_a_garment_seen_in_the_dark() -> None:
    world = _world(seed=2)
    for pid in ("mara", "iris"):
        world = _walk(world, pid, "dining_room", "kitchen", "cellar")
    observation = build_observation(world, "mara")
    leaked = observation.model_copy(
        update={"present": [Presence(label="someone in a canvas jacket")]}
    )
    with pytest.raises(LeakageError, match="silhouettes"):
        assert_no_leak(leaked, world)


# ------------------------------------------- addressing someone you cannot see


def _whisper_in_the_cellar(sol_there: bool) -> WorldState:
    world = _world(seed=4)
    route = ("dining_room", "kitchen", "cellar")
    world = _walk(world, "mara", *route)
    if sol_there:
        world = _walk(world, "sol", *route)
    return _do(world, "mara", Whisper(to="sol", text="Meet me at dawn."))


def test_whispering_in_the_dark_never_says_who_is_there() -> None:
    absent, present = _whisper_in_the_cellar(False), _whisper_in_the_cellar(True)
    for world in (absent, present):
        assert world.ledger[-1].type == "whisper"
        own = world.ledger[-1].record_for("mara")
        assert own is not None and own.text == "I whispered to Sol:"
    assert absent.ledger[-1].payload["reached"] == "false"
    assert absent.ledger[-1].record_for("sol") is None
    assert present.ledger[-1].payload["reached"] == "true"


def test_talking_to_someone_absent_in_the_dark_is_not_refused() -> None:
    world = _world(seed=4)
    for pid in ("mara", "iris"):
        world = _walk(world, pid, "dining_room", "kitchen", "cellar")
    world = _do(world, "mara", Talk(text="Sol, is that you?", to="sol"))
    assert world.ledger[-1].type == "talk"


def test_in_bright_light_an_absent_listener_is_plain_to_see() -> None:
    world = _world(seed=4)
    world = _walk(world, "sol", "dining_room")
    world = _do(world, "mara", Whisper(to="sol", text="Psst."))
    assert world.ledger[-1].type == "blocked"


# ------------------------------------------------------------------- the roster


def _observe(game: GameState, viewer: str) -> GameObservation:
    observation = observe_game(game, viewer, Request(kind="plan", prompt="Plan."))
    assert_no_game_leak(observation, game, models=MODELS)
    return observation


def test_the_roster_shows_builds_and_starting_garments_but_no_details() -> None:
    game = start_game(CASTLE, CAST.first(8), seed=6)
    roster = _observe(game, "mara").cast
    assert [(c.id, c.build, c.garment) for c in roster] == [
        (
            pid,
            game.world.players[pid].build,
            game.world.players[pid].starting_garment,
        )
        for pid in game.world.seats
    ]
    assert _observe(game, "sol").cast == roster
    assert "detail" not in roster[0].model_dump()


def test_the_roster_keeps_the_garments_people_began_in() -> None:
    """It is the household as introduced, not a live record of who wears what."""
    game = start_game(CASTLE, CAST.first(8), seed=6)
    world = game.world
    shawl = _garment_of(world, "sol")
    world = _do(world, "sol", Drop(thing_id=shawl))
    world = _do(world, "mara", Take(thing_id=shawl), Wear(thing_id=shawl))
    game = game.model_copy(update={"world": world})
    mara = next(c for c in _observe(game, "iris").cast if c.id == "mara")
    assert mara.garment == "grey coat"


@pytest.mark.parametrize(
    "change",
    [{"garment": "dark shawl"}, {"persona": "Wears ink-stained cuffs."}],
)
def test_planted_leak_a_roster_that_is_not_the_one_introduced(
    change: dict[str, str],
) -> None:
    game = start_game(CASTLE, CAST.first(8), seed=6)
    observation = _observe(game, "mara")
    leaked = observation.model_copy(
        update={
            "cast": [
                observation.cast[0].model_copy(update=change),
                *observation.cast[1:],
            ]
        }
    )
    with pytest.raises(LeakageError, match="household as introduced"):
        assert_no_game_leak(leaked, game, models=MODELS)
