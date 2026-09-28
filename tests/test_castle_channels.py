"""Every record says what it came through, and intentions stay with the audience.

Words and narration never share a string: narration is the simulator's, words are
someone's, and a prompt must be able to show the second as an attributed quote in
the channel it arrived by. The Steward's channel is the only official one, and
only the host writes to it. A ledger entry's ``private`` fields hold what an actor
meant, which no player is ever shown.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from badactors.castle.contracts import (
    QUOTE_LIMIT,
    Move,
    Plan,
    Talk,
    Wait,
    WitnessRecord,
    WorldState,
)
from badactors.castle.director import RoutineDecider, run_phase
from badactors.castle.mapfile import load_world
from badactors.castle.scope import assert_no_leak, build_observation
from badactors.castle.world import advance_phase, start, submit_plan, tick

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "configs/castle/map.json"
CAST_PATH = ROOT / "configs/castle/cast.example.json"

_CHANNEL_OF = {"talk": "speech", "summoned": "steward"}


def _world(seed: int = 13) -> WorldState:
    return start(load_world(MAP_PATH, CAST_PATH, seed=seed))


def _walk(world: WorldState, player_id: str, *rooms: str) -> WorldState:
    for room_id in rooms:
        world = submit_plan(world, player_id, Plan(steps=[Move(room_id=room_id)]))
        for _ in range(8):
            world = tick(world)
            if world.players[player_id].room_id == room_id:
                break
    assert world.players[player_id].room_id == rooms[-1]
    return world


# ----------------------------------------------------------------- the channels


def test_every_item_says_which_channel_it_came_through() -> None:
    world = _world()
    for _ in range(4):
        world = run_phase(world, RoutineDecider(seed=world.seed))
    seen: set[str] = set()
    by_seq = {entry.seq: entry for entry in world.ledger}
    for player_id in world.seats:
        for item in build_observation(world, player_id).recent:
            entry = by_seq[item.ledger_seq]
            assert item.channel == _CHANNEL_OF.get(entry.type, "direct"), entry.type
            seen.add(item.channel)
    assert {"direct", "speech", "steward"} <= seen


def test_speech_keeps_the_words_apart_from_the_narration() -> None:
    world = _world()
    words = "I was in the library at nine, ask the archivist."
    world = submit_plan(world, "mara", Plan(steps=[Talk(text=words)]))
    world = tick(world)
    talk = world.ledger[-1]
    assert (talk.type, talk.text, talk.quote) == ("talk", "Mara said:", words)

    heard_by_sol = talk.record_for("sol")
    assert heard_by_sol is not None
    assert heard_by_sol.channel == "speech"
    assert heard_by_sol.text == "Mara said:"
    assert heard_by_sol.quote == words
    assert heard_by_sol.author == "mara"

    own = talk.record_for("mara")
    assert own is not None
    assert (own.text, own.quote, own.author) == ("I said:", words, "mara")
    for record in talk.witnesses:
        assert words not in record.text


def test_an_unrecognised_speaker_is_quoted_without_an_author() -> None:
    route = ("dining_room", "kitchen", "cellar")
    world = _walk(_walk(_world(), "mara", *route), "sol", *route)
    assert world.map.room("cellar").lighting == "dark"
    world = submit_plan(world, "mara", Plan(steps=[Talk(text="It was me.")]))
    world = tick(world)
    record = world.ledger[-1].record_for("sol")
    assert record is not None
    assert record.quote == "It was me."
    assert record.author is None
    assert "Mara" not in record.text


def test_through_a_wall_speech_is_only_voices() -> None:
    world = _walk(_world(), "sol", "dining_room")
    world = submit_plan(world, "mara", Plan(steps=[Talk(text="Meet me at midnight.")]))
    world = tick(world)
    record = world.ledger[-1].record_for("sol")
    assert record is not None
    assert record.sense == "heard"
    assert record.channel == "speech"
    assert (record.quote, record.author) == (None, None)
    assert record.text == "a voice in conversation in the Great Hall"


def test_the_longest_legal_speech_is_recorded_whole() -> None:
    """Words used to be wrapped into the narration and overflowed it."""
    words = "x" * QUOTE_LIMIT
    world = submit_plan(_world(), "mara", Plan(steps=[Talk(text=words)]))
    world = tick(world)
    assert world.ledger[-1].quote == words
    for player_id in world.seats:
        assert_no_leak(build_observation(world, player_id), world)


def test_a_gathering_is_called_in_the_stewards_channel() -> None:
    world = _world()
    summons = next(entry for entry in world.ledger if entry.type == "summoned")
    assert summons.actor_id is None
    assert summons.quote == "The household is called to the Great Hall."
    assert {record.player_id for record in summons.witnesses} == set(world.seats)
    for record in summons.witnesses:
        assert record.channel == "steward"
        assert record.quote == summons.quote
        assert record.author is None
        assert record.recorded_actor is None


# ------------------------------------------------------------- the record rules


def test_a_wall_cannot_carry_words_or_a_name() -> None:
    for extra in ({"quote": "hello"}, {"recorded_actor": "mara"}):
        with pytest.raises(ValidationError, match="A wall carries"):
            WitnessRecord(
                player_id="sol",
                sense="heard",
                confidence="glimpse",
                channel="speech",
                text="a voice in conversation in the Great Hall",
                **extra,
            )


def test_direct_records_carry_no_words() -> None:
    with pytest.raises(ValidationError, match="no quoted words"):
        WitnessRecord(
            player_id="sol",
            sense="saw",
            confidence="clear",
            channel="direct",
            text="Mara left toward the Gallery.",
            quote="smuggled",
        )


def test_words_that_came_through_a_channel_are_kept() -> None:
    with pytest.raises(ValidationError, match="must be kept"):
        WitnessRecord(
            player_id="sol",
            sense="saw",
            confidence="clear",
            channel="note",
            text="Sol read a note:",
        )


def test_only_quoted_words_have_an_author() -> None:
    with pytest.raises(ValidationError, match="Only quoted words"):
        WitnessRecord(
            player_id="sol",
            sense="saw",
            confidence="clear",
            channel="direct",
            text="Mara waved.",
            author="mara",
        )


# --------------------------------------------------------- what the actor meant


def test_a_plans_goal_is_private_and_remembered_only_by_its_owner() -> None:
    world = submit_plan(
        _world(),
        "mara",
        Plan(goal="get the key before Knox does", steps=[Wait()]),
    )
    formed = world.ledger[-1]
    assert formed.type == "plan_formed"
    assert formed.private == {"goal": "get the key before Knox does"}
    assert "goal" not in formed.payload
    assert "get the key" not in formed.text
    assert [record.player_id for record in formed.witnesses] == ["mara"]


def test_a_reason_for_waiting_is_private() -> None:
    world = submit_plan(
        _world(), "mara", Plan(steps=[Wait(reason="watching who leaves first")])
    )
    world = tick(world)
    waited = world.ledger[-1]
    assert waited.type == "wait"
    assert waited.private == {"reason": "watching who leaves first"}
    assert waited.payload == {}


def test_private_fields_never_reach_another_player() -> None:
    world = _world()
    for _ in range(3):
        world = run_phase(world, RoutineDecider(seed=world.seed))
    world = advance_phase(world)
    meant = {
        (entry.actor_id, value)
        for entry in world.ledger
        for value in entry.private.values()
        if entry.actor_id is not None and len(value) > 8
    }
    assert meant, "expected the routine run to record some goals"
    for player_id in world.seats:
        shown = build_observation(world, player_id).model_dump_json()
        # Two players may mean the same thing; only someone else's is a leak.
        mine = {value for actor_id, value in meant if actor_id == player_id}
        for actor_id, value in meant:
            if actor_id != player_id and value not in mine:
                assert value not in shown, (player_id, actor_id, value)
