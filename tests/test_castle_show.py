"""The audience feed: a stored game laid out for the show, as a pure function of
its record."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from badactors.castle import play
from badactors.castle.bots import HeuristicBot
from badactors.castle.cli import main as cli_main
from badactors.castle.contracts import AtRoom, WorldState
from badactors.castle.play import GameRecord, ReplayDiverged, record, run_game
from badactors.castle.show import (
    Feed,
    Whereabouts,
    audience_feed,
    load_feed,
    save_feed,
)
from castle_scenes import CAST, CASTLE, MODELS

SEED = 7


def _standing(world: WorldState) -> dict[str, Whereabouts]:
    out: dict[str, Whereabouts] = {}
    for pid in world.seats:
        player = world.players[pid]
        position = player.position
        if isinstance(position, AtRoom):
            where = Whereabouts(room=position.room_id)
        else:
            where = Whereabouts(
                origin=position.origin, destination=position.destination
            )
        out[pid] = where.model_copy(
            update={"asleep": player.asleep, "alive": player.alive}
        )
    return out


@pytest.fixture(scope="module")
def watched() -> Iterator[tuple[GameRecord, dict[int, dict[str, Whereabouts]]]]:
    """A bot game, and where everyone truly stood at the end of every tick: the
    world as it was handed to the next tick, and as it finished."""
    truth: dict[int, dict[str, Whereabouts]] = {}
    real = play.tick

    def spy(state: WorldState) -> WorldState:
        truth[state.tick] = _standing(state)
        return real(state)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(play, "tick", spy)
        game = play.start_game(CASTLE, CAST, seed=SEED)
        bot = HeuristicBot(seed=SEED)
        played = run_game(
            game,
            {pid: bot for pid in game.world.seats},
            check_leaks=False,
            models=MODELS,
        )
    truth[played.game.world.tick] = _standing(played.game.world)
    yield record(CASTLE, CAST, played), truth


@pytest.fixture(scope="module")
def stored(watched: tuple[GameRecord, dict[int, dict[str, Whereabouts]]]) -> GameRecord:
    return watched[0]


@pytest.fixture(scope="module")
def feed(stored: GameRecord) -> Feed:
    return audience_feed(stored)


def test_the_feed_is_a_pure_function_of_the_record(
    stored: GameRecord, feed: Feed
) -> None:
    again = audience_feed(GameRecord.model_validate_json(stored.model_dump_json()))
    assert again.model_dump_json() == feed.model_dump_json()


def test_the_feed_round_trips_through_its_file(feed: Feed, tmp_path: Path) -> None:
    path = tmp_path / "feed.json"
    save_feed(feed, path)
    assert load_feed(path) == feed


def test_every_ledger_entry_is_one_event_with_its_private_lines_and_rolls(
    stored: GameRecord, feed: Feed
) -> None:
    assert [event.seq for event in feed.events] == [e.seq for e in stored.ledger]
    rooms = {room.id: room.lighting for room in CASTLE.rooms}
    for event, entry in zip(feed.events, stored.ledger, strict=True):
        assert (event.type, event.text, event.quote, event.actor, event.room) == (
            entry.type,
            entry.text,
            entry.quote,
            entry.actor_id,
            entry.room_id,
        )
        assert event.light == (rooms[entry.room_id] if entry.room_id else None)
        assert [w.player for w in event.witnesses] == [
            w.player_id for w in entry.witnesses
        ]
        assert [w.confidence for w in event.witnesses] == [
            w.confidence for w in entry.witnesses
        ]
        assert [(r.value, r.chance) for r in event.rolls] == [
            (r.value, r.threshold) for r in entry.rolls
        ]
        if "diary" in entry.private:
            assert event.private["diary"] == entry.private["diary"]
        if "intent" in entry.private:
            assert event.intent is not None
            assert event.intent.render() == entry.private["intent"]
            assert "intent" not in event.private
    interviews = [event for event in feed.events if event.type == "exit_interview"]
    assert interviews and all(event.told is not None for event in interviews)
    assert any(roll.chance is not None for event in feed.events for roll in event.rolls)


def test_positions_match_the_world_at_the_end_of_every_tick(
    watched: tuple[GameRecord, dict[int, dict[str, Whereabouts]]], feed: Feed
) -> None:
    _, truth = watched
    assert [frame.tick for frame in feed.positions] == sorted(truth)
    for frame in feed.positions:
        assert frame.seats == truth[frame.tick], frame.tick
    states = [where for frame in feed.positions for where in frame.seats.values()]
    assert any(where.room is None for where in states), "nobody was ever walking"
    assert any(where.asleep for where in states)
    assert any(not where.alive for where in states)


def test_seats_carry_their_provenance_and_a_bot_seat_names_no_model(
    stored: GameRecord, feed: Feed
) -> None:
    assert feed.game.reference_bots
    assert [seat.id for seat in feed.seats] == [m.id for m in CAST.players]
    for seat in feed.seats:
        assert seat.agent == "bot:heuristic" and seat.played_by == "bot"
        assert seat.model is None
        assert seat.decisions == sum(
            1 for d in stored.decisions if d.player_id == seat.id
        )
    assert "anthropic/" not in feed.model_dump_json()
    traitors = {seat.id for seat in feed.seats if seat.traitor_from is not None}
    assert set(feed.game.starting_traitors) <= traitors


def test_every_dinner_seating_is_kept(stored: GameRecord, feed: Feed) -> None:
    dinners = [entry for entry in stored.ledger if entry.type == "seating"]
    assert [seating.seq for seating in feed.seatings] == [e.seq for e in dinners]
    for seating, entry in zip(feed.seatings, dinners, strict=True):
        assert ",".join(seating.order) == entry.payload["seating"]


def test_a_record_whose_ledger_was_altered_is_refused(stored: GameRecord) -> None:
    ledger = list(stored.ledger)
    ledger[-1] = ledger[-1].model_copy(update={"text": "Something else happened."})
    with pytest.raises(ReplayDiverged):
        audience_feed(stored.model_copy(update={"ledger": ledger}))


def test_castle_game_writes_the_feed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "feed.json"
    cli_main(["--seed", "4", "--players", "8", "--feed", str(path)])
    assert f"Saved the audience feed to {path}." in capsys.readouterr().out
    written = load_feed(path)
    assert written.game.seed == 4 and len(written.seats) == 8
