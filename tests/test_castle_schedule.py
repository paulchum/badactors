"""Role rotation: every entrant plays both sides of the same scenario."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from badactors.castle.play import start_game
from badactors.castle.rules import GameConfig
from badactors.castle.schedule import (
    Scenario,
    Schedule,
    ScheduleError,
    Seating,
    check,
    corruption_schedule,
    deal,
    invader_arm,
    load_schedule,
    mixed_schedule,
    save_schedule,
    single_arm,
    traitor_seats,
)
from castle_scenes import CAST, CASTLE

MODELS = [f"lab{i}/model-{i}" for i in range(8)]


def scenario(players: int = 8, traitors: int = 2) -> Scenario:
    return Scenario(
        castle=CASTLE,
        cast=CAST.first(players),
        config=GameConfig.shipped(traitors=traitors),
    )


def test_the_seed_draws_the_same_traitor_seats_whoever_plays_them() -> None:
    cast = CAST.first(8)
    turned = cast.model_copy(
        update={
            "players": [
                member.model_copy(update={"model": MODELS[(i + 3) % 8]})
                for i, member in enumerate(cast.players)
            ]
        }
    )
    config = GameConfig.shipped()
    for seed in range(6):
        first = start_game(CASTLE, cast, seed=seed, config=config)
        second = start_game(CASTLE, turned, seed=seed, config=config)
        assert first.starting_traitors == second.starting_traitors
        assert first.starting_traitors == traitor_seats(
            CASTLE, turned, seed=seed, config=config
        )


def test_the_latin_square_seats_every_entrant_everywhere_once_a_seed() -> None:
    schedule = mixed_schedule(scenario(), MODELS, seeds=[0, 1, 2])
    check(schedule)
    for seed in (0, 1, 2):
        games = [game for game in schedule.games if game.seed == seed]
        assert len(games) == 8
        for seat in games[0].seats:
            assert sorted(game.seats[seat] for game in games) == sorted(MODELS)
        traitor_turns = Counter(
            game.seats[seat] for game in games for seat in game.traitors
        )
        assert traitor_turns == dict.fromkeys(MODELS, 2)


def test_compact_rotation_gives_everyone_one_traitor_turn_a_seed() -> None:
    schedule = mixed_schedule(
        scenario(), MODELS, seeds=list(range(12)), rotation="compact"
    )
    coverage = check(schedule)
    assert coverage[0].pairs == coverage[0].pairs_wanted == 8 * 7
    for seed in range(12):
        games = [game for game in schedule.games if game.seed == seed]
        assert len(games) == 4
        traitor_turns = Counter(
            game.seats[seat] for game in games for seat in game.traitors
        )
        assert traitor_turns == dict.fromkeys(MODELS, 1)


def test_a_compact_seed_alone_can_leave_a_pair_unmet_and_the_check_says_so() -> None:
    schedule = mixed_schedule(scenario(), MODELS, seeds=[5], rotation="compact")
    with pytest.raises(ScheduleError, match="never attacks"):
        check(schedule)


def test_fewer_entrants_than_seats_share_the_extra_seats_round_the_seeds() -> None:
    four = MODELS[:4]
    schedule = mixed_schedule(scenario(), four, seeds=list(range(4)))
    check(schedule)
    doubled = set()
    for seed in range(4):
        dealt = deal(four, 8, seed=seed)
        assert Counter(dealt) == dict.fromkeys(four, 2)
        doubled.add(tuple(dealt))
    assert len(doubled) > 1
    with pytest.raises(ScheduleError, match="cannot all sit"):
        deal(MODELS, 4, seed=0)


def test_single_model_castles_are_one_game_a_seed() -> None:
    games = single_arm(scenario(), MODELS[:2], seeds=[0, 1, 2])
    assert len(games) == 6
    assert {frozenset(game.seats.values()) for game in games} == {
        frozenset({MODELS[0]}),
        frozenset({MODELS[1]}),
    }
    check(Schedule(players=8, traitors=2, rotation="latin", games=games))


def test_the_invader_goes_round_every_seat_both_ways() -> None:
    a, b = MODELS[:2]
    games = invader_arm(scenario(), a, b, seeds=[3])
    assert {game.group for game in games} == {
        "lab0-model-0-in-lab1-model-1",
        "lab1-model-1-in-lab0-model-0",
    }
    invaded = [game for game in games if game.group.startswith("lab0")]
    assert len(invaded) == 8
    seats_of_a = {
        seat for game in invaded for seat, who in game.seats.items() if who == a
    }
    assert seats_of_a == set(invaded[0].seats)
    coverage = check(Schedule(players=8, traitors=2, rotation="latin", games=games))
    assert coverage[0].pairs == coverage[0].pairs_wanted == 4


def test_the_corruption_test_has_four_arms_and_names_visible_is_never_rated() -> None:
    a, b = MODELS[:2]
    schedule = corruption_schedule(
        scenario(),
        MODELS,
        a,
        b,
        seeds=[0, 1],
        tiers={m: "frontier" if i < 4 else "open" for i, m in enumerate(MODELS)},
    )
    arms = {cover.arm for cover in check(schedule)}
    assert arms == {"single", "mixed", "invader", "names_visible"}
    for game in schedule.games:
        assert game.rated == (game.arm != "names_visible")
        assert game.names_visible == (game.arm == "names_visible")
    with pytest.raises(ValueError, match="names-visible"):
        Seating(
            id="mixed-s0-r0",
            arm="mixed",
            seed=0,
            rotation=0,
            seats={"mara": a},
            traitors=[],
            names_visible=True,
        )


def test_a_mixed_arm_needs_frontier_and_open_weight_models() -> None:
    schedule = mixed_schedule(
        scenario(), MODELS, seeds=[0], tiers=dict.fromkeys(MODELS, "frontier")
    )
    with pytest.raises(ScheduleError, match="frontier and open-weight"):
        check(schedule)


def test_the_check_names_an_entrant_who_only_plays_one_side() -> None:
    schedule = mixed_schedule(scenario(), MODELS, seeds=[0])
    kept = [
        game
        for game in schedule.games
        if MODELS[0] not in {game.seats[seat] for seat in game.traitors}
    ]
    broken = schedule.model_copy(update={"games": kept})
    with pytest.raises(ScheduleError, match=f"{MODELS[0]} plays only faithful"):
        check(broken)


def test_a_seating_casts_its_entrants_and_the_schedule_round_trips(
    tmp_path: Path,
) -> None:
    schedule = mixed_schedule(scenario(), MODELS, seeds=[0])
    game = schedule.games[0]
    cast = game.cast(CAST.first(8))
    assert cast.models() == game.seats
    with pytest.raises(ScheduleError, match="another cast"):
        game.cast(CAST.first(9))
    path = tmp_path / "schedule.json"
    save_schedule(schedule, path)
    assert load_schedule(path) == schedule
    assert mixed_schedule(scenario(), MODELS, seeds=[0]) == schedule


@settings(max_examples=15, deadline=None)
@given(
    players=st.integers(min_value=8, max_value=10),
    traitors=st.integers(min_value=2, max_value=3),
    entrants=st.integers(min_value=2, max_value=8),
    seed=st.integers(min_value=0, max_value=10_000),
)
def test_every_latin_schedule_covers_both_roles_and_every_pair(
    players: int, traitors: int, entrants: int, seed: int
) -> None:
    schedule = mixed_schedule(
        scenario(players, traitors), MODELS[:entrants], seeds=[seed, seed + 1]
    )
    for cover in check(schedule):
        assert cover.pairs == cover.pairs_wanted == entrants * (entrants - 1)
