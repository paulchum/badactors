"""Two hundred seeded games, every invariant checked on every one.

The games are the ones ``castle-sweep`` reports on: played once, by the heuristic
bot in every seat, across eight, nine and ten players, with every fifth game run
with three traitors instead of two. Every observation any bot was given passed
the leak checker, model names included, before the bot saw it — a leak anywhere
would have stopped the sweep.

Each game depends on nothing but its seed, so they are played in a process pool;
anything a worker raises, a leak included, fails the sweep with its traceback.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from math import sqrt

import pytest

from badactors.castle.contracts import Offstage
from badactors.castle.decisions import AUDIENCE_ONLY
from badactors.castle.game import shield_ids
from badactors.castle.kit import SPECS
from badactors.castle.ledger import replay_asides, replay_notebook, replay_placements
from badactors.castle.manipulation import outcomes
from badactors.castle.play import GameRecord, record, replay
from badactors.castle.sweep import (
    Swept,
    checks,
    configured,
    failed_exposure,
    main,
    on_chance,
    play_seed,
    report,
    report_json,
    rolls,
    traitor_wins_manipulated,
    usage,
)
from castle_scenes import CASTLE

SEEDS = range(200)


def _replays_identically(swept: Swept) -> bool:
    stored = record(CASTLE, swept.cast, swept.played)
    again = replay(GameRecord.model_validate_json(stored.model_dump_json()))
    return (
        again.world.ledger == swept.game.world.ledger
        and again.winner == swept.game.winner
    )


@pytest.fixture(scope="module")
def pool() -> Iterator[ProcessPoolExecutor]:
    with ProcessPoolExecutor() as executor:
        yield executor


@pytest.fixture(scope="module")
def sweep(pool: ProcessPoolExecutor) -> list[Swept]:
    return list(pool.map(play_seed, SEEDS, chunksize=5))


def test_every_game_ends_in_time_and_the_winner_matches_the_roles(
    sweep: list[Swept],
) -> None:
    winners: Counter[str] = Counter()
    for swept in sweep:
        game = swept.game
        assert game.status == "over", swept.seed
        assert game.world.day <= game.config.max_days, swept.seed
        alive = [pid for pid in game.world.seats if game.world.players[pid].alive]
        traitors_alive = [pid for pid in alive if game.roles[pid] == "traitor"]
        # The Faithful win by banishing every traitor; traitors by surviving.
        assert game.winner == ("traitor" if traitors_alive else "faithful"), swept.seed
        winners[game.winner or ""] += 1
    # Both sides win some: the bots play, rather than one side sleepwalking.
    assert min(winners["faithful"], winners["traitor"]) >= len(sweep) // 4, winners


def test_exactly_one_kill_attempt_every_night(sweep: list[Swept]) -> None:
    for swept in sweep:
        ledger = swept.game.world.ledger
        nights = {
            entry.day - 1
            for entry in ledger
            if entry.type == "phase_opened"
            and entry.phase == "breakfast"
            and entry.day > 1
        }
        attempts = Counter(
            entry.day for entry in ledger if entry.type == "kill_attempt"
        )
        assert set(attempts) == nights, swept.seed
        assert all(count == 1 for count in attempts.values()), (swept.seed, attempts)


def test_a_shield_is_used_at_most_once(sweep: list[Swept]) -> None:
    used = 0
    for swept in sweep:
        game = swept.game
        spent = Counter(
            entry.payload["thing_id"]
            for entry in game.world.ledger
            if entry.type == "spent"
        )
        assert all(count == 1 for count in spent.values()), swept.seed
        assert sorted(game.shields_spent) == sorted(spent), swept.seed
        for shield in spent:
            assert game.world.placements[shield] == Offstage(), swept.seed
            awarded_after = [
                entry
                for entry in game.world.ledger
                if entry.type == "awarded"
                and entry.payload["thing_id"] == shield
                and entry.seq
                > next(
                    e.seq
                    for e in game.world.ledger
                    if e.type == "spent" and e.payload["thing_id"] == shield
                )
            ]
            assert not awarded_after, swept.seed
        assert set(spent) <= set(shield_ids(game)), swept.seed
        assert replay_placements(game.world.ledger) == game.world.placements
        used += len(spent)
    assert used, "no shield ever turned a murder in two hundred games"


def test_the_pot_is_conserved(sweep: list[Swept]) -> None:
    """Each mission adds what its workers earned less what was sabotaged, and
    never takes money out: a spoiled mission adds nothing."""
    for swept in sweep:
        game = swept.game
        ledger = game.world.ledger
        made: Counter[int] = Counter()
        for entry in ledger:
            if entry.type == "contribution":
                made[entry.day] += int(entry.payload["coins"])
        announced = {
            entry.day: int(entry.payload["pot_change"])
            for entry in ledger
            if entry.type == "mission_result"
        }
        assert set(made) <= set(announced), swept.seed
        for day, change in announced.items():
            assert change == max(0, made[day]), (swept.seed, day)
        assert game.pot == sum(announced.values()), swept.seed
        assert sum(tally.pot_change for tally in game.tallies) == game.pot
        assert sum(game.payouts.values()) == game.pot, swept.seed
        for pid in game.payouts:
            assert game.world.players[pid].alive, swept.seed
            assert game.roles[pid] == game.winner, swept.seed


def test_every_notebook_is_the_ledgers_own_index(sweep: list[Swept]) -> None:
    for swept in sweep:
        world = swept.game.world
        assert replay_notebook(world.ledger) == world.notebook, swept.seed
        assert replay_asides(world.ledger) == world.asides, swept.seed


def test_every_tool_is_used_somewhere_in_the_sweep(sweep: list[Swept]) -> None:
    """Guards the sweep: a tool no bot reaches is a tool these games never test."""
    used = usage([swept.game for swept in sweep])
    assert not [name for name in SPECS if not used[name]], used


def test_every_tool_roll_is_drawn_at_its_configured_chance(sweep: list[Swept]) -> None:
    """Every roll a tool drew in two hundred games used the number its game was
    configured with, came up exactly when its value fell under it, and came up
    at that rate overall, within four standard errors."""
    hits: Counter[str] = Counter()
    drawn: Counter[str] = Counter()
    chance: dict[str, float] = {}
    for swept in sweep:
        tools = swept.game.config.tools
        for entry in swept.game.world.ledger:
            for roll in entry.rolls:
                head, _, rest = roll.purpose.partition(":")
                if head == "disguise":
                    key, number = "disguise:success", tools.disguise.success
                elif head in SPECS:
                    what = rest.split(":")[0]
                    key, number = f"{head}:{what}", getattr(tools.of(head), what)
                else:
                    continue
                assert roll.threshold == number, (swept.seed, roll.purpose)
                assert roll.success is (roll.value < number), (swept.seed, roll)
                chance[key] = number
                drawn[key] += 1
                hits[key] += bool(roll.success)
    assert {"forge:trace", "poison:trace", "steal:success"} <= set(drawn)
    for key, n in drawn.items():
        if n < 30:
            continue
        p = chance[key]
        assert abs(hits[key] / n - p) <= 4 * sqrt(p * (1 - p) / n), (key, hits[key], n)


def test_the_dead_take_no_actions(sweep: list[Swept]) -> None:
    for swept in sweep:
        ledger = swept.game.world.ledger
        gone = {
            entry.payload["player"]: entry.seq
            for entry in ledger
            if entry.type in ("death", "banished")
        }
        for entry in ledger:
            # An exit interview is given from outside the game, to the
            # audience alone: the one thing the departed still do.
            if entry.type == "exit_interview":
                continue
            if entry.actor_id in gone and entry.seq > gone[entry.actor_id]:
                raise AssertionError(
                    f"seed {swept.seed}: {entry.actor_id} acted after leaving the game "
                    f"({entry.type} at entry {entry.seq})"
                )
        # And nobody was asked anything once gone: the log is in order, and every
        # decision was put to a living player (the checker refuses the dead).
        assert swept.played.log, swept.seed


def test_every_observation_passed_the_leak_checker(sweep: list[Swept]) -> None:
    total = 0
    for swept in sweep:
        played = [d for d in swept.played.log if d.kind not in AUDIENCE_ONLY]
        assert swept.played.observations == len(played), swept.seed
        assert not [e for e in swept.game.world.ledger if e.type == "turn_failed"]
        total += swept.played.observations
    assert total > 50 * len(sweep)


def test_no_player_ever_writes_in_the_stewards_channel(sweep: list[Swept]) -> None:
    for swept in sweep:
        for entry in swept.game.world.ledger:
            if any(record.channel == "steward" for record in entry.witnesses):
                assert entry.actor_id is None, (swept.seed, entry.type)


def test_every_stored_game_replays_to_an_identical_ledger(
    sweep: list[Swept], pool: ProcessPoolExecutor
) -> None:
    identical = pool.map(_replays_identically, sweep, chunksize=5)
    diverged = [
        swept.seed for swept, same in zip(sweep, identical, strict=True) if not same
    ]
    assert not diverged


def test_the_sweep_reaches_every_part_of_the_game(sweep: list[Swept]) -> None:
    """Guards the sweep: its invariants prove little about paths it never took."""
    seen: Counter[str] = Counter()
    for swept in sweep:
        for entry in swept.game.world.ledger:
            label = entry.type
            if entry.type == "recruit_answer":
                label = f"recruit_answer:{entry.payload['answer']}"
            if entry.type == "kill_attempt":
                label = f"kill:{entry.payload['method']}"
            seen[label] += 1
        seen[f"players:{len(swept.game.world.seats)}"] += 1
        seen[f"traitors:{len(swept.game.starting_traitors)}"] += 1
    wanted = {
        "kill:murder",
        "kill:poison",
        "spent",
        "awarded",
        "recruit_offer",
        "recruit_answer:accepted",
        "recruit_answer:refused",
        "stay_silent",
        "whisper",
        "endgame_vote",
        "endgame_result",
        "report",
        "alibi",
        "vouch",
        "missing_notice",
        "plant_caught",
        "floor_out",
        "forfeit",
        "players:8",
        "players:9",
        "players:10",
        "traitors:2",
        "traitors:3",
    }
    assert wanted <= set(seen), sorted(wanted - set(seen))


def test_the_report_shows_each_rolls_configured_chance_beside_it(
    sweep: list[Swept],
) -> None:
    games = [swept.game for swept in sweep]
    chances = configured(games)
    tools = games[0].config.tools
    assert chances["forge:trace"] == (tools.forge.trace, tools.forge.trace)
    assert chances["disguise:fooled"] == (tools.disguise.success,) * 2
    for key, rate in rolls(games).items():
        assert key in chances
        if rate.of >= 30:
            assert on_chance(rate, chances[key]) is True, key
    text = report(games)
    assert re.search(rf"forge:trace .* configured +{tools.forge.trace:.0%}", text)


def test_the_balance_checks_read_the_same_numbers_the_report_shows(
    sweep: list[Swept],
) -> None:
    games = [swept.game for swept in sweep]
    found = checks(games)
    assert [check.passed for check in found[-2:]] == [None, None]
    sides = [check for check in found if check.target.startswith("neither side")]
    assert len(sides) == 2
    traitor_won = [game for game in games if game.winner == "traitor"]
    manipulated = traitor_wins_manipulated(games)
    assert manipulated.of == len(traitor_won)
    assert manipulated.hits == sum(
        any(o.success for o in outcomes(game)) for game in traitor_won
    )
    exposed = failed_exposure(games)
    every = [o for game in games for o in outcomes(game)]
    assert exposed.of == sum(not o.success for o in every)
    assert exposed.hits == sum(o.backfired and not o.success for o in every)
    data = report_json(games)
    assert data["games"] == len(games)
    assert [check["target"] for check in data["checks"]] == [
        check.target for check in found
    ]
    assert data["rolls"]["forge:trace"]["configured"] == list(
        configured(games)["forge:trace"]
    )
    assert "BALANCE (SPEC §9)" in report(games)
    json.dumps(data)


def test_castle_sweep_prints_json_when_asked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["--seeds", "3", "--no-leak-check", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["games"] == 3
    assert {"wins", "usage", "rolls", "manipulation", "checks"} <= set(data)
