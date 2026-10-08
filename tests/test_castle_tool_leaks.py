"""The leak classes the toolkits add, each with a planted leak that fails when real.

As with the other leak tests, there are two kinds of plant. The first doctors a
real scene at the moment it matters — a record written to the wrong player, an
observation or a request that says too much — and checks that the checker names
the class. The second puts the bug where it would live, in the code that writes
the record or the offer, and plays a game: the checker must stop the game at the
first observation that carries the leak.
"""

from __future__ import annotations

from typing import Any

import pytest

from badactors.castle import table as table_module
from badactors.castle import tools as tools_module
from badactors.castle.contracts import (
    Hook,
    Intent,
    LedgerEntry,
    Plan,
    Plant,
    Read,
    ScopedObservation,
    Search,
    Steal,
    Talk,
    WorldState,
)
from badactors.castle.decisions import Poison, Request
from badactors.castle.game import (
    GameState,
    living,
    living_faithful,
    living_traitors,
    missing_items,
    shield_held_by,
)
from badactors.castle.night import poison_act, poison_turn, seat_dinner
from badactors.castle.observe import GameObservation, assert_no_game_leak, observe_game
from badactors.castle.perceive import Perception
from badactors.castle.play import start_game
from badactors.castle.rules import GameConfig
from badactors.castle.scope import (
    LeakageError,
    assert_no_leak,
    build_observation,
    item_for,
)
from badactors.castle.world import submit_plan
from castle_scenes import (
    CAST,
    CASTLE,
    MODELS,
    of_type,
    pause_at,
    referee,
    run_until,
)
from castle_tools import act, give, last, own, put, scene, seen, tuned

VOTE_MARA = Intent(target="sol", effect="vote", want="mara", window="next_vote")
PLAN = Request(kind="plan", prompt="Plan.")


def _check(observation: GameObservation, game: GameState) -> None:
    assert_no_game_leak(observation, game, models=MODELS)


def _plant(
    world: WorldState, entry: LedgerEntry, viewer: str, **changes: Any
) -> LedgerEntry:
    """A world bug: ``viewer`` gets a record of ``entry`` they should never have
    had, copied from the last record written, as a bug would copy it."""
    record = entry.witnesses[-1].model_copy(update={"player_id": viewer, **changes})
    planted = entry.model_copy(update={"witnesses": [*entry.witnesses, record]})
    world.ledger[entry.seq - 1] = planted
    return planted


def _rewrite(
    world: WorldState, entry: LedgerEntry, viewer: str, **changes: Any
) -> LedgerEntry:
    """A world bug: ``viewer``'s record of ``entry`` says something else."""
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
    world.ledger[entry.seq - 1] = rewritten
    return rewritten


def _shows(observation: ScopedObservation, entry: LedgerEntry) -> bool:
    return any(item.ledger_seq == entry.seq for item in observation.recent)


# ------------------------------------------------------------ a declared intent


def test_planted_leak_a_declared_intent_in_the_plan() -> None:
    world = submit_plan(
        scene(),
        "knox",
        Plan(steps=[Hook(to="sol", text="Vote for Mara.", intent=VOTE_MARA)]),
    )
    honest = seen(world, "knox")
    leaked = honest.model_copy(
        update={"plan_remaining": list(world.players["knox"].plan.steps)}
    )
    with pytest.raises(LeakageError, match="declared intent"):
        assert_no_leak(leaked, world)


@pytest.mark.parametrize("viewer", ["sol", "knox"])
def test_planted_leak_a_declared_intent_in_a_record(viewer: str) -> None:
    """Neither the target nor the author may be shown what the act was for."""
    world = act(
        scene(), "knox", Hook(to="sol", text="Vote for Mara.", intent=VOTE_MARA)
    )
    hook = last(world, "hook")
    rendered = hook.private["intent"]
    _rewrite(world, hook, viewer, text=f"{own(hook, viewer).text} {rendered}")
    with pytest.raises(LeakageError, match="meant it to do"):
        assert_no_leak(build_observation(world, viewer), world)


def test_a_world_that_writes_intents_into_the_narration_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = tools_module.append_perceived

    def chatty(world: WorldState, perception: Perception, **kwargs: Any) -> LedgerEntry:
        entry = real(world, perception, **kwargs)
        rendered = entry.private.get("intent")
        if rendered is None:
            return entry
        told = entry.model_copy(
            update={
                "witnesses": [
                    record.model_copy(update={"text": f"{record.text} {rendered}"})
                    for record in entry.witnesses
                ]
            }
        )
        world.ledger[entry.seq - 1] = told
        return told

    monkeypatch.setattr(tools_module, "append_perceived", chatty)
    with pytest.raises(LeakageError, match="meant it to do"):
        referee(seed=3).run()


# ------------------------------------------------ a finding seen by someone else


def _watched_reading() -> tuple[WorldState, LedgerEntry]:
    world = scene()
    for pid in ("mara", "sol"):
        put(world, pid, "library")
    world = act(world, "mara", Read(thing_id="almanac"))
    return world, last(world, "read")


def test_planted_leak_a_finding_seen_by_someone_else() -> None:
    world, reading = _watched_reading()
    found = own(reading, "mara")
    assert found.quote is not None
    _rewrite(world, reading, "sol", quote=found.quote, channel=found.channel)
    with pytest.raises(LeakageError, match="a finding seen by someone else"):
        assert_no_leak(build_observation(world, "sol"), world)


def test_planted_leak_the_searchers_own_record_given_to_a_watcher() -> None:
    world = scene()
    for pid in ("mara", "sol"):
        put(world, pid, "library")
    world = act(world, "mara", Search())
    searched = last(world, "search")
    _rewrite(world, searched, "sol", text=own(searched, "mara").text)
    with pytest.raises(LeakageError, match="a finding seen by someone else"):
        assert_no_leak(build_observation(world, "sol"), world)


# ------------------------------------------------ a covert act seen by its victim


def test_planted_leak_a_covert_act_seen_by_its_victim() -> None:
    world = scene(plant={"success": 1.0})
    sheet = next(t.id for t in world.map.things if t.kind == "paper")
    give(world, "knox", sheet)
    world = act(world, "knox", Plant(thing_id=sheet, to="mara"))
    planted = last(world, "plant")
    assert planted.type == "plant" and planted.record_for("mara") is None
    _plant(world, planted, "mara")
    observation = build_observation(world, "mara")
    assert _shows(observation, planted)
    with pytest.raises(LeakageError, match="seen by its victim"):
        assert_no_leak(observation, world)


def test_planted_leak_a_caught_hand_seen_by_a_bystander() -> None:
    world = scene(plant={"success": 0.0})
    sheet = next(t.id for t in world.map.things if t.kind == "paper")
    give(world, "knox", sheet)
    world = act(world, "knox", Plant(thing_id=sheet, to="mara"))
    caught = last(world, "plant")
    assert caught.type == "plant_caught" and caught.record_for("sol") is None
    _plant(world, caught, "sol")
    with pytest.raises(LeakageError, match="never caught the hand"):
        assert_no_leak(build_observation(world, "sol"), world)


# --------------------------------------------------------------------- a notebook


def test_planted_leak_a_notebook_item_its_viewer_never_noted() -> None:
    world = act(scene(), "sol", Talk(text="Fine weather for it."))
    talk = world.ledger[-1]
    heard = own(talk, "mara")
    assert not heard.noted, "hearing small talk is not a finding"
    honest = seen(world, "mara")
    leaked = honest.model_copy(
        update={"evidence": [*honest.evidence, item_for(talk, heard)]}
    )
    with pytest.raises(LeakageError, match="never noted"):
        assert_no_leak(leaked, world)


def test_a_notebook_index_that_files_someone_elses_record_is_caught() -> None:
    world = act(scene(), "sol", Talk(text="Fine weather for it."))
    talk = world.ledger[-1]
    world.notebook.setdefault("mara", []).append(talk.seq)
    with pytest.raises(LeakageError, match="never noted"):
        assert_no_leak(build_observation(world, "mara"), world)


def test_planted_leak_someone_elses_secret() -> None:
    world = scene()
    honest = seen(world, "mara")
    assert honest.secret == world.players["mara"].secret
    leaked = honest.model_copy(update={"secret": world.players["sol"].secret})
    with pytest.raises(LeakageError, match="not their own"):
        assert_no_leak(leaked, world)


# ------------------------------------------------------ a stolen shield's owner


def test_planted_leak_a_missing_shield_reported_to_someone_else() -> None:
    game = run_until(referee(seed=4), 1, "free_time")
    holder = next(pid for pid in living(game) if shield_held_by(game, pid))
    thief = next(pid for pid in living_traitors(game) if pid != holder)
    game.world.balance = tuned(game.world.balance, steal={"success": 1.0})
    put(game.world, thief, game.world.players[holder].room_id or "great_hall")
    game.world = act(game.world, thief, Steal(to=holder))
    missing_items(game)
    report = of_type(game, "missing")[-1]
    bystander = next(pid for pid in living(game) if pid not in (holder, thief))
    planted = _plant(game.world, report, bystander)
    observation = observe_game(game, bystander, PLAN)
    assert _shows(observation.world, planted)
    with pytest.raises(LeakageError, match="a stolen shield's owner"):
        _check(observation, game)


# ------------------------------------------------------------- a poison noticer


def test_planted_leak_a_poisoning_seen_by_someone_who_never_noticed() -> None:
    config = GameConfig.shipped(tools={"poison": {"seen": 0.0}})
    game = start_game(CASTLE, CAST.first(8), seed=3, config=config)
    world = game.world
    world.phase = "dinner"
    for pid in world.seats:
        put(world, pid, world.map.gathering_room)
    seat_dinner(game)
    traitor = living_traitors(game)[0]
    left, right = living_faithful(game)[:2]
    rest = [pid for pid in game.seating if pid not in (left, traitor, right)]
    game.seating = [left, traitor, right, *rest]
    turn = poison_turn(game)
    assert turn is not None
    poison_act(game, traitor, turn[1], Poison(target=left))
    poured = of_type(game, "poison")[-1]
    assert poured.payload["seen_by"] == "" and poured.record_for(right) is None
    planted = _plant(game.world, poured, right)
    observation = observe_game(game, right, PLAN)
    assert _shows(observation.world, planted)
    with pytest.raises(LeakageError, match="the night's target"):
        _check(observation, game)


# --------------------------------------------- a tool its user does not carry


def _faithful_accusing() -> tuple[GameState, GameObservation]:
    return pause_at(
        lambda observation, game: (
            observation.request.kind == "accuse"
            and game.roles[observation.viewer_id] == "faithful"
        )
    )


def test_planted_leak_a_traitor_tool_offered_to_one_of_the_faithful() -> None:
    game, observation = _faithful_accusing()
    request = observation.request
    assert "filibuster" not in request.tools
    leaked = observation.model_copy(
        update={
            "request": request.model_copy(
                update={"tools": [*request.tools, "filibuster"]}
            )
        }
    )
    with pytest.raises(LeakageError, match="kit does not hold"):
        _check(leaked, game)


def test_planted_leak_evidence_the_viewer_never_held() -> None:
    game, observation = _faithful_accusing()
    viewer = observation.viewer_id
    unseen = next(
        entry
        for entry in game.world.ledger
        if entry.witnesses and entry.record_for(viewer) is None
    )
    request = observation.request
    leaked = observation.model_copy(
        update={
            "request": request.model_copy(
                update={"evidence": [*request.evidence, unseen.seq]}
            )
        }
    )
    with pytest.raises(LeakageError, match="never witnessed"):
        _check(leaked, game)


def test_a_table_that_forgets_to_check_the_kit_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(table_module, "refusal", lambda world, pid, tool: None)
    with pytest.raises(LeakageError, match="kit does not hold"):
        referee(seed=6).run()
