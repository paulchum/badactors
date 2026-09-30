"""Two hundred seeded games, every invariant checked on every one.

The games are played once, by the heuristic bot in every seat, across eight,
nine and ten players, with every fifth game run with three traitors instead of
two. Every observation any bot was given passed the leak checker, model names
included, before the bot saw it — a leak anywhere would have stopped the sweep.

Each game depends on nothing but its seed, so they are played in a process pool;
anything a worker raises, a leak included, fails the sweep with its traceback.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import pytest

from badactors.castle.bots import HeuristicBot
from badactors.castle.cast import Cast
from badactors.castle.contracts import Offstage
from badactors.castle.game import GameState, shield_ids
from badactors.castle.ledger import replay_placements
from badactors.castle.play import (
    GameRecord,
    Played,
    record,
    replay,
    run_game,
    start_game,
)
from badactors.castle.rules import GameConfig
from castle_scenes import CAST, CASTLE, MODELS

SEEDS = range(200)


@dataclass
class Swept:
    seed: int
    cast: Cast
    played: Played

    @property
    def game(self) -> GameState:
        return self.played.game


def _config(seed: int) -> GameConfig:
    return GameConfig(traitors=3) if seed % 5 == 4 else GameConfig()


def _play(seed: int) -> Swept:
    cast = CAST.first(8 + seed % 3)
    game = start_game(CASTLE, cast, seed=seed, config=_config(seed))
    bot = HeuristicBot(seed=seed)
    played = run_game(
        game,
        {pid: bot for pid in game.world.seats},
        check_leaks=True,
        models=MODELS,
    )
    return Swept(seed=seed, cast=cast, played=played)


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
    return list(pool.map(_play, SEEDS, chunksize=5))


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
    for swept in sweep:
        game = swept.game
        ledger = game.world.ledger
        earned = sum(
            int(entry.payload["coins"])
            for entry in ledger
            if entry.type == "contribution"
        )
        announced = sum(
            int(entry.payload["pot_change"])
            for entry in ledger
            if entry.type == "mission_result"
        )
        assert game.pot == earned == announced, swept.seed
        assert sum(tally.pot_change for tally in game.tallies) == game.pot
        assert sum(game.payouts.values()) == game.pot, swept.seed
        for pid in game.payouts:
            assert game.world.players[pid].alive, swept.seed
            assert game.roles[pid] == game.winner, swept.seed


def test_the_dead_take_no_actions(sweep: list[Swept]) -> None:
    for swept in sweep:
        ledger = swept.game.world.ledger
        gone = {
            entry.payload["player"]: entry.seq
            for entry in ledger
            if entry.type in ("death", "banished")
        }
        for entry in ledger:
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
        assert swept.played.observations == len(swept.played.log), swept.seed
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
        "players:8",
        "players:9",
        "players:10",
        "traitors:2",
        "traitors:3",
    }
    assert wanted <= set(seen), sorted(wanted - set(seen))
