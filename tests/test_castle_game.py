"""The game layer: roles, the day, missions, shields, the kill, recruitment, the
round table and the endgame, played through the referee a model would face."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from badactors.castle.bots import HeuristicBot
from badactors.castle.cast import CastMember
from badactors.castle.cli import main as cli_main
from badactors.castle.contracts import (
    DAY_ORDER,
    AtRoom,
    Drop,
    HeldBy,
    Move,
    Offstage,
    Plan,
    Talk,
)
from badactors.castle.decisions import (
    PRIVATE_REQUESTS,
    Accept,
    Accuse,
    Decision,
    EndgameVote,
    Pass,
    PlanChoice,
    Poison,
    Recruit,
    Refuse,
    Request,
    StaySilent,
    Vote,
)
from badactors.castle.episode import REFERENCE_BANNER, episode
from badactors.castle.game import (
    GameState,
    NightState,
    living,
    living_faithful,
    living_traitors,
    new_game,
    remove,
    shield_held_by,
    split,
)
from badactors.castle.mapfile import CastSpecError
from badactors.castle.missions import sabotaged, work_done
from badactors.castle.night import (
    can_recruit,
    end_night,
    neighbours,
    poison_act,
    poison_turn,
    seat_dinner,
)
from badactors.castle.observe import GameObservation, assert_no_game_leak, observe_game
from badactors.castle.play import (
    GameRecord,
    Played,
    Player,
    ReplayDiverged,
    TurnFailure,
    load_record,
    record,
    replay,
    run_game,
    save_record,
    start_game,
)
from badactors.castle.rng import draw
from badactors.castle.rules import OFFICIAL_ONLY, GameConfig, MissionSpec
from badactors.castle.scope import LeakageError
from badactors.castle.table import open_table, table_act, table_advance, table_turn
from badactors.castle.world import phase_complete, submit_plan, tick
from castle_scenes import (
    CAST,
    CASTLE,
    MODELS,
    NO_POISON,
    Script,
    of_type,
    recruiting,
    referee,
    run_until,
)

PLAN = Request(kind="plan", prompt="Plan.")


def _game(seed: int = 13, players: int = 10, **config: object) -> GameState:
    return start_game(
        CASTLE,
        CAST.first(players),
        seed=seed,
        config=GameConfig.shipped(**config),
    )


def _observe(game: GameState, viewer: str, request: Request = PLAN) -> GameObservation:
    observation = observe_game(game, viewer, request)
    assert_no_game_leak(observation, game, models=MODELS)
    return observation


def _mission(kind: str) -> list[MissionSpec]:
    return [
        mission for mission in GameConfig.shipped().missions if mission.kind == kind
    ]


# ------------------------------------------------------------------------ roles


def test_traitors_are_drawn_from_the_seeded_rng() -> None:
    game = _game(seed=13)
    drawn = of_type(game, "roles_drawn")[0]
    assert drawn.witnesses == []
    traitors = [pid for pid in game.world.seats if game.roles[pid] == "traitor"]
    assert traitors == game.starting_traitors
    assert len(traitors) == 2
    assert drawn.payload["traitors"] == ",".join(traitors)
    for roll in drawn.rolls:
        assert draw(game.world.seed, roll.cursor, roll.purpose) == roll
    assert _game(seed=13).starting_traitors == traitors
    assert len({tuple(_game(seed=seed).starting_traitors) for seed in range(12)}) > 5
    assert len(_game(seed=13, traitors=3).starting_traitors) == 3


def test_every_player_is_told_their_own_role_and_nobody_elses() -> None:
    game = _game(seed=5)
    told = of_type(game, "role_told")
    assert [entry.payload["player"] for entry in told] == game.world.seats
    for entry in told:
        pid = entry.payload["player"]
        assert [record.player_id for record in entry.witnesses] == [pid]
        assert entry.witnesses[0].channel == "steward"
        others = [game.world.players[p].name for p in game.world.seats if p != pid]
        named = [other for other in others if other in (entry.quote or "")]
        if game.roles[pid] == "traitor":
            fellows = [p for p in game.starting_traitors if p != pid]
            assert named == [game.world.players[p].name for p in fellows]
        else:
            assert named == []


@pytest.mark.parametrize("size", [7, 11])
def test_a_game_seats_eight_to_ten(size: int) -> None:
    members = list(CAST.players)
    while len(members) < size:
        extra = members[-1]
        members.append(
            CastMember(
                id=f"{extra.id}x{len(members)}",
                name=f"{extra.name}{len(members)}",
                persona=extra.persona,
                build=extra.build,
                garment=extra.garment,
                model=extra.model,
            )
        )
    characters = [member.model_dump(exclude={"model"}) for member in members[:size]]
    with pytest.raises(CastSpecError, match="8 to 10"):
        new_game(CASTLE, characters, seed=1)


def test_no_model_name_ever_enters_the_game() -> None:
    game = _game()
    dumped = game.model_dump_json().casefold()
    for model in MODELS:
        assert model.casefold() not in dumped
        assert model.rsplit("/", 1)[-1].casefold() not in dumped
    assert all("model" not in character for character in CAST.characters())


def test_an_observation_naming_a_model_is_a_leak() -> None:
    game = _game()
    observation = _observe(game, "mara")
    leaked = observation.model_copy(
        update={
            "cast": [
                character.model_copy(update={"persona": f"Played by {MODELS[0]}."})
                if character.id == "sol"
                else character
                for character in observation.cast
            ]
        }
    )
    with pytest.raises(LeakageError, match="model name"):
        assert_no_game_leak(leaked, game, models=MODELS)


# ---------------------------------------------------------------------- the day


def test_a_day_runs_every_phase_to_its_tick_budget() -> None:
    ref = referee(seed=3)
    budget = CASTLE.phase_ticks
    seen = []
    for _ in DAY_ORDER:
        world = ref.game.world
        phase, start = world.phase, world.tick
        ref.play_phase()
        if ref.game.status != "playing":
            break
        seen.append(phase)
        assert ref.game.world.tick - start == budget.ticks(phase)
    assert seen == list(DAY_ORDER)
    assert (ref.game.world.day, ref.game.world.phase) == (2, "breakfast")


def test_night_sends_the_faithful_to_sleep_and_the_traitors_to_the_conclave() -> None:
    game = run_until(referee(seed=3, tools=NO_POISON), 2, "mission")
    assert not game.offers
    night = [e for e in game.world.ledger if e.day == 1 and e.phase == "night"]
    retire = next(entry for entry in night if entry.type == "retire")
    home = [record.player_id for record in retire.witnesses]
    traitors = [pid for pid in home if game.roles[pid] == "traitor"]
    sleepers = [entry.actor_id for entry in night if entry.type == "sleep"]
    below = [
        entry.actor_id
        for entry in night
        if entry.type == "move_arrive" and entry.room_id == CASTLE.conclave_room
    ]
    assert sorted(sleepers, key=home.index) == [p for p in home if p not in traitors]
    assert sorted(below, key=home.index) == traitors
    for entry in night:
        if entry.room_id == CASTLE.conclave_room:
            assert {record.player_id for record in entry.witnesses} <= set(traitors)
    # Everyone is back in the chambers by dawn, so nobody walks in to breakfast
    # from the wrong direction.
    breakfast = [e for e in of_type(game, "move_arrive") if e.day == 2]
    assert breakfast
    assert {entry.payload["origin"] for entry in breakfast} == {CASTLE.chambers_room}


def test_the_pot_change_is_announced_exactly_and_the_tally_only_roughly() -> None:
    ref = referee(seed=4, missions=_mission("carry"))
    game = run_until(ref, 1, "free_time")
    contributions = of_type(game, "contribution")
    turns = work_done(game)
    assert {entry.actor_id for entry in contributions} == set(turns)
    for entry in contributions:
        assert entry.witnesses == []
        assert (
            int(entry.payload["coins"])
            == len(turns[entry.actor_id or ""]) * game.config.yields.carry_load
        )
    result = of_type(game, "mission_result")[0]
    change = sum(int(entry.payload["coins"]) for entry in contributions)
    assert int(result.payload["pot_change"]) == change == game.pot
    assert f"added {change} coins" in (result.quote or "")
    reported = game.tallies[0].reported
    noise = game.config.tally_noise
    for entry in contributions:
        assert (
            abs(reported[entry.actor_id or ""] - int(entry.payload["coins"])) <= noise
        )
    assert len(result.rolls) == len(contributions)
    assert any(
        reported[entry.actor_id or ""] != int(entry.payload["coins"])
        for entry in contributions
    )


def test_each_mission_kind_pays_by_its_own_rule() -> None:
    cipher = run_until(referee(seed=8, missions=_mission("cipher")), 1, "free_time")
    for entry in of_type(cipher, "contribution"):
        cracked = sum(1 for roll in entry.rolls if roll.success)
        assert (
            int(entry.payload["coins"]) == cracked * cipher.config.yields.cipher_prize
        )
    clock = run_until(referee(seed=8, missions=_mission("assemble")), 1, "free_time")
    turns = work_done(clock)
    crowd: dict[int, int] = {}
    for entries in turns.values():
        for entry in entries:
            crowd[entry.tick] = crowd.get(entry.tick, 0) + 1
    for entry in of_type(clock, "contribution"):
        expected = sum(
            2 if crowd[turn.tick] >= 3 else 1 for turn in turns[entry.actor_id or ""]
        )
        assert int(entry.payload["coins"]) == expected


def test_every_contribution_accounts_for_sabotage() -> None:
    game = run_until(referee(seed=4), 1, "free_time")
    turns = work_done(game)
    for entry in of_type(game, "contribution"):
        worker = entry.actor_id or ""
        held_back = sabotaged(game, worker, turns[worker])
        assert entry.payload["sabotage"] == str(held_back)
        assert int(entry.payload["coins"]) == int(entry.payload["earned"]) - held_back
        assert sabotaged(game, worker, []) == 0


# ---------------------------------------------------------------------- shields


def _holder(game: GameState) -> str:
    for pid in living_faithful(game):
        if shield_held_by(game, pid) is not None:
            return pid
    raise AssertionError("none of the Faithful holds a shield")


def _shielded_game(phase: str = "free_time") -> GameState:
    """The first seeded game where one of the Faithful wins the day-one shield."""
    for seed in range(1, 40):
        game = run_until(referee(seed=seed, tools=NO_POISON), 1, "free_time")
        holders = [p for p in living_faithful(game) if shield_held_by(game, p)]
        if holders:
            return run_until(referee(seed=seed, tools=NO_POISON), 1, phase)  # type: ignore[arg-type]
    raise AssertionError("no seed gave one of the Faithful a shield")


def test_a_shield_is_won_in_secret() -> None:
    game = run_until(referee(seed=4), 1, "free_time")
    awarded = of_type(game, "awarded")
    assert len(awarded) == 1
    holder = awarded[0].payload["holder_id"]
    assert [record.player_id for record in awarded[0].witnesses] == [holder]
    assert game.world.placements[awarded[0].payload["thing_id"]] == HeldBy(
        player_id=holder
    )
    assert "A shield has been won" in (of_type(game, "mission_result")[0].quote or "")
    for pid in living(game):
        carried = [view.id for view in _observe(game, pid).world.carrying]
        assert (awarded[0].payload["thing_id"] in carried) == (pid == holder)


def _murder(game: GameState, target: str) -> None:
    """Resolve a night whose conclave chose ``target``."""
    game.night = NightState(
        day=game.world.day,
        target=target,
        conclave=living_traitors(game),
    )
    end_night(game)


def test_a_shield_turns_aside_one_murder_and_is_spent() -> None:
    game = _shielded_game()
    holder = _holder(game)
    shield = shield_held_by(game, holder)
    assert shield is not None
    _murder(game, holder)
    assert game.world.players[holder].alive
    assert game.world.placements[shield] == Offstage()
    assert game.shields_spent == [shield]
    survived = of_type(game, "attack_survived")
    assert [record.player_id for record in survived[0].witnesses] == [holder]
    failed = of_type(game, "kill_failed")
    assert {record.player_id for record in failed[0].witnesses} == set(
        living_traitors(game)
    )
    _murder(game, holder)
    assert not game.world.players[holder].alive
    assert game.shields_spent == [shield]


def test_poison_kills_through_a_shield() -> None:
    game = _shielded_game("dinner")
    poison = game.world.balance.tools.poison.model_copy(update={"per_game": 1})
    game.world.balance = game.world.balance.model_copy(
        update={"tools": game.world.balance.tools.model_copy(update={"poison": poison})}
    )
    holder = _holder(game)
    traitor = living_traitors(game)[0]
    seat_dinner(game)
    others = [pid for pid in game.seating if pid not in (holder, traitor)]
    game.seating = [traitor, holder, *others]
    turn = poison_turn(game)
    assert turn is not None
    poisoner, request = turn
    assert poisoner == traitor and holder in request.options
    assert set(request.options) <= set(neighbours(game.seating, traitor))
    poison_act(game, traitor, request, Poison(target=holder))
    assert poison_turn(game) is None
    end_night(game)
    assert not game.world.players[holder].alive
    assert shield_held_by(game, holder) is not None
    attempts = of_type(game, "kill_attempt")
    assert [entry.payload["method"] for entry in attempts] == ["poison"]


def test_a_shield_left_behind_protects_nobody() -> None:
    game = _shielded_game()
    holder = _holder(game)
    shield = shield_held_by(game, holder)
    assert shield is not None
    game.world = submit_plan(game.world, holder, Plan(steps=[Drop(thing_id=shield)]))
    game.world = tick(game.world)
    assert shield_held_by(game, holder) is None
    _murder(game, holder)
    assert not game.world.players[holder].alive


def test_poison_is_limited_by_the_balance_config() -> None:
    game = run_until(referee(seed=4, tools=NO_POISON), 1, "dinner")
    seat_dinner(game)
    assert poison_turn(game) is None


# ----------------------------------------------------------------- recruitment


def _banish_a_traitor(observation: GameObservation, game: GameState) -> Decision | None:
    """On day one, the table votes out the first traitor."""
    if observation.request.kind != "vote" or game.world.day != 1:
        return None
    first = game.starting_traitors[0]
    if first in observation.request.options:
        return Vote(target=first)
    return Vote(target=observation.request.options[0])


def test_recruitment_waits_until_fewer_traitors_remain_than_began() -> None:
    game = _game(seed=13)
    game.night = NightState(day=1)
    assert not can_recruit(game)
    remove(game, game.starting_traitors[0], type="banished", cause="test")
    assert can_recruit(game)
    game.night = NightState(day=1, poisoned=living_faithful(game)[0])
    assert not can_recruit(game)
    three = _game(seed=13, traitors=3)
    three.night = NightState(day=1)
    remove(three, three.starting_traitors[0], type="banished", cause="test")
    assert can_recruit(three)


def _recruiting(answer: Decision) -> tuple[GameState, str]:
    """Day one banishes a traitor; that night the other offers a place to the
    first Faithful it can, who answers with ``answer``."""
    chosen: list[str] = []

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        kind = observation.request.kind
        if kind == "recruit":
            target = next(
                pid
                for pid in observation.request.options
                if shield_held_by(game, pid) is None
            )
            chosen.append(target)
            return Recruit(target=target, text="Join us.")
        if kind == "recruit_answer":
            return answer
        if kind == "poison":
            return Pass()
        return _banish_a_traitor(observation, game)

    ref = referee(seed=11, script=script)
    game = run_until(ref, 2, "breakfast")
    assert chosen, "no offer was made"
    return game, chosen[0]


def test_accepting_an_offer_makes_a_traitor_who_joins_the_conclave() -> None:
    game, recruit = _recruiting(Accept(text="Yes."))
    assert game.roles[recruit] == "traitor"
    offer = game.offers[0]
    assert (offer.target, offer.answer) == (recruit, "accepted")
    for entry in of_type(game, "recruit_offer", "recruit_answer"):
        assert {record.player_id for record in entry.witnesses} == {
            offer.recruiter,
            recruit,
        }
    joined = [e for e in of_type(game, "role_told") if e.payload["player"] == recruit]
    assert "one of the traitors now" in (joined[-1].quote or "")
    reached = [
        entry.actor_id
        for entry in of_type(game, "move_arrive")
        if entry.room_id == CASTLE.conclave_room and entry.day == 1
    ]
    assert recruit in reached
    assert game.traitor_since[recruit] == offer.seq + 1


def test_refusing_an_offer_makes_the_refuser_that_nights_target() -> None:
    game, refuser = _recruiting(Refuse(text="Never."))
    assert game.roles[refuser] == "faithful"
    assert not game.world.players[refuser].alive
    attempt = [entry for entry in of_type(game, "kill_attempt") if entry.day == 1]
    assert [entry.payload["target"] for entry in attempt] == [refuser]
    nominations = [entry for entry in of_type(game, "nominate") if entry.day == 1]
    assert nominations == []


# ----------------------------------------------------------------- round table


# Alibis and vouching are answers: they come in the answer stage.
_STAGES = {
    "accuse": 0,
    "defend": 1,
    "question": 2,
    "answer": 3,
    "alibi": 3,
    "vouch": 3,
    "vote": 4,
}


def test_the_round_table_runs_its_turns_in_order() -> None:
    ref = referee(seed=6)
    game = run_until(ref, 1, "dinner")
    stages = [
        entry.payload["stage"]
        for entry in game.world.ledger
        if entry.phase == "round_table" and "stage" in entry.payload
    ]
    assert stages, "the table took no turns"
    order = [_STAGES[stage] for stage in stages]
    assert order == sorted(order)
    assert {"accuse", "question", "vote"} <= set(stages)


def test_the_banished_players_role_is_revealed_to_everyone() -> None:
    game = run_until(referee(seed=6), 1, "dinner")
    banishment = of_type(game, "banishment")[-1]
    out = banishment.payload["target"]
    role = game.roles[out]
    assert not game.world.players[out].alive
    assert ("was a traitor" if role == "traitor" else "was one of the Faithful") in (
        banishment.quote or ""
    )
    assert {record.player_id for record in banishment.witnesses} == set(living(game))
    somebody = living(game)[0]
    assert _observe(game, somebody).known_roles[out] == role


def test_a_tied_vote_is_settled_by_a_recorded_lot() -> None:
    def split_vote(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind != "vote":
            return None
        options = observation.request.options
        seat = game.world.seats.index(observation.viewer_id)
        pair = [pid for pid in game.world.seats[:2] if pid in options] or options[:1]
        return Vote(target=pair[seat % len(pair)])

    game = run_until(referee(seed=2, players=8, script=split_vote), 1, "dinner")
    banishment = of_type(game, "banishment")[0]
    assert "lots were drawn" in (banishment.quote or "")
    assert [roll.purpose for roll in banishment.rolls] == ["lots:day1:1"]


def test_players_away_from_the_table_cannot_speak_or_vote() -> None:
    game = run_until(referee(seed=6), 1, "round_table")
    while not phase_complete(game.world):
        game.world = tick(game.world)
    absent = living_faithful(game)[0]
    game.world.players[absent].position = AtRoom(room_id="cellar")
    open_table(game)
    opened = of_type(game, "table_opened")[-1]
    assert f"Not at the table: {game.world.players[absent].name}." in (
        opened.quote or ""
    )
    asked = []
    assert game.table is not None
    while game.status == "playing" and game.table.stage != "done":
        turn = table_turn(game)
        if turn is None:
            table_advance(game)
            continue
        pid, request = turn
        asked.append(pid)
        decision: Decision = (
            Vote(target=absent)
            if request.kind == "vote"
            else StaySilent(reason="watching")
        )
        table_act(game, pid, request, decision)
    assert absent not in asked
    assert not game.world.players[absent].alive


def _final_table(choice: str) -> GameState:
    def script(observation: GameObservation, game: GameState) -> Decision | None:
        kind = observation.request.kind
        if kind == "endgame":
            return EndgameVote(choice="end" if choice == "end" else "banish")
        if kind == "vote":
            faithful = [
                p for p in observation.request.options if game.roles[p] == "faithful"
            ]
            return Vote(target=(faithful or observation.request.options)[0])
        return None

    ref = referee(seed=9, endgame_at=10, script=script)
    ref.run()
    return ref.game


def test_the_endgame_ends_when_every_vote_is_to_end() -> None:
    game = _final_table("end")
    assert game.status == "over"
    assert game.world.day == 1
    assert len(of_type(game, "banishment")) == 1
    assert game.winner == "traitor"
    assert "Every vote was to end the game." in (
        of_type(game, "game_over")[0].quote or ""
    )


def test_a_vote_to_banish_again_banishes_again() -> None:
    game = _final_table("banish")
    rounds = [int(entry.payload["round"]) for entry in of_type(game, "banishment")]
    assert rounds == list(range(1, len(rounds) + 1))
    assert len(rounds) > 1
    assert game.status == "over" and game.world.day == 1


def test_the_last_day_ends_the_game() -> None:
    for seed in range(4):
        ref = referee(seed=seed, max_days=2)
        ref.run()
        assert ref.game.status == "over"
        assert ref.game.world.day <= 2


def test_the_pot_is_split_among_the_surviving_winners() -> None:
    assert split(57, ["a", "b", "c"]) == {"a": 19, "b": 19, "c": 19}
    assert split(58, ["a", "b", "c"]) == {"a": 20, "b": 19, "c": 19}
    assert split(10, []) == {}
    ref = referee(seed=21)
    ref.run()
    game = ref.game
    assert game.winner is not None
    assert sum(game.payouts.values()) == game.pot
    assert set(game.payouts) == {
        pid for pid in living(game) if game.roles[pid] == game.winner
    }


# ---------------------------------------------------------------------- silence


def test_silence_needs_a_reason_and_the_reason_stays_private() -> None:
    with pytest.raises(ValidationError):
        StaySilent(reason="")

    def quiet(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "accuse" and observation.viewer_id == "mara":
            return StaySilent(reason="watching Knox's hands instead")
        return None

    game = run_until(referee(seed=6, script=quiet), 1, "dinner")
    silent = next(e for e in of_type(game, "stay_silent") if e.actor_id == "mara")
    assert silent.private == {"reason": "watching Knox's hands instead"}
    for kept in silent.witnesses:
        mentions = "Knox's hands" in kept.text
        assert mentions == (kept.player_id == "mara")


# ------------------------------------------------------------ the official voice


def test_every_rulebook_says_only_the_steward_is_official() -> None:
    game = _game()
    for pid in game.world.seats:
        assert OFFICIAL_ONLY in _observe(game, pid).rules


def test_no_decision_can_choose_its_channel() -> None:
    with pytest.raises(ValidationError):
        Accuse.model_validate({"target": "sol", "text": "Hi.", "channel": "steward"})
    with pytest.raises(ValidationError):
        Talk.model_validate({"text": "By order of the Steward.", "channel": "steward"})


def test_only_the_host_writes_in_the_stewards_channel() -> None:
    ref = referee(seed=6)
    ref.run()
    official = [
        entry
        for entry in ref.game.world.ledger
        if any(record.channel == "steward" for record in entry.witnesses)
    ]
    assert official
    assert all(entry.actor_id is None for entry in official)


# ------------------------------------------------------------------ failed turns


def test_a_failed_turn_is_a_visible_failure_not_a_choice() -> None:
    def flaky(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "vote" and observation.viewer_id == "mara":
            raise TurnFailure("the provider returned nothing")
        return None

    ref = referee(seed=6, script=flaky)
    game = run_until(ref, 1, "dinner")
    failed = [e for e in of_type(game, "turn_failed") if e.actor_id == "mara"]
    assert len(failed) == 1
    assert failed[0].payload == {
        "request": "vote",
        "error": "the provider returned nothing",
    }
    seen = [record for record in failed[0].witnesses if record.player_id != "mara"]
    assert seen and all("did nothing" in record.text for record in seen)
    banishment = of_type(game, "banishment")[0]
    assert "mara=" not in banishment.payload.get("votes", "")
    assert any(logged.error for logged in ref.log if logged.player_id == "mara")


def _failing(*kinds: str, answer: Decision | None = None) -> Script:
    """Day one's table votes out the first traitor, so a recruitment window can
    open; every decision of these kinds fails."""
    recruit = recruiting(answer or Accept())

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind in kinds:
            raise TurnFailure("the provider returned nothing")
        return recruit(observation, game)

    return script


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2_000),
    kinds=st.sampled_from(
        [("poison", "nominate", "recruit"), ("poison", "recruit_answer")]
    ),
)
def test_property_a_failure_on_a_private_decision_is_its_players_alone(
    seed: int, kinds: tuple[str, ...]
) -> None:
    """A traitor who fails to decide on a dose at dinner, a nomination or an
    offer, or a recruit who fails to answer one, hesitates where nobody can see
    it: a hesitation anyone saw would say the question had been put."""
    game = run_until(referee(seed=seed, script=_failing(*kinds)), 3, "breakfast")
    failed = [
        entry
        for entry in of_type(game, "turn_failed")
        if entry.payload["request"] in PRIVATE_REQUESTS
    ]
    assert failed, "no private decision was ever put"
    for entry in failed:
        assert [record.player_id for record in entry.witnesses] == [entry.actor_id]


def test_a_failed_poison_turn_is_not_seen_at_dinner() -> None:
    game = run_until(referee(seed=6, script=_failing("poison")), 2, "breakfast")
    failed = [
        e for e in of_type(game, "turn_failed") if e.payload["request"] == "poison"
    ]
    assert failed, "no traitor was asked to poison"
    for entry in failed:
        assert entry.room_id == game.world.map.gathering_room
        assert [record.player_id for record in entry.witnesses] == [entry.actor_id]


def _walking_out(who: str, exit_index: int) -> Script:
    """At dinner, everyone of this role (or everyone, for ``"all"``) tries to
    walk out of the hall."""

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        room = observation.world.room
        if (
            observation.request.kind == "plan"
            and observation.phase == "dinner"
            and room is not None
            and who in ("all", game.roles[observation.viewer_id])
        ):
            exit_to = room.exits[exit_index % len(room.exits)]
            return PlanChoice(plan=Plan(goal="slip out", steps=[Move(room_id=exit_to)]))
        return None

    return script


def _sightings_of_the_stair(game: GameState) -> list[tuple[int, str]]:
    """Every record a Faithful holds of a traitor going down to the conclave
    room or coming back from it."""
    crypt = game.world.map.conclave_room
    return [
        (entry.seq, record.player_id)
        for entry in game.world.ledger
        if entry.phase == "night"
        and entry.type in ("move_depart", "move_arrive")
        and entry.actor_id is not None
        and game.roles[entry.actor_id] == "traitor"
        and crypt in (entry.payload.get("destination"), entry.payload.get("origin"))
        for record in entry.witnesses
        if record.player_id != entry.actor_id
        and record.sense == "saw"
        and game.roles[record.player_id] == "faithful"
    ]


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2_000),
    who=st.sampled_from(["traitor", "faithful", "all"]),
    exit_index=st.integers(min_value=0, max_value=3),
)
def test_property_nobody_walks_out_of_dinner_to_watch_the_stair(
    seed: int, who: str, exit_index: int
) -> None:
    """However the household tries to leave dinner, nobody does, and no Faithful
    sees a traitor go down to the conclave or come back."""
    ref = referee(seed=seed, script=_walking_out(who, exit_index))
    game = run_until(ref, 3, "breakfast")
    hall = game.world.map.gathering_room
    walked = [
        entry
        for entry in of_type(game, "move_depart")
        if entry.phase == "dinner" and entry.room_id == hall
    ]
    assert walked == []
    assert _sightings_of_the_stair(game) == []


def test_a_traitor_who_tries_to_leave_dinner_stays_at_table() -> None:
    ref = referee(seed=3, script=_walking_out("traitor", 1))
    game = run_until(ref, 2, "breakfast")
    refused = [
        entry
        for entry in of_type(game, "blocked")
        if entry.phase == "dinner" and game.roles[entry.actor_id or ""] == "traitor"
    ]
    assert refused, "no traitor tried to leave"
    assert all(len(entry.witnesses) == 1 for entry in refused)
    assert _sightings_of_the_stair(game) == []


def test_an_illegal_answer_is_a_failed_turn() -> None:
    def cheat(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "vote" and observation.viewer_id == "mara":
            return Vote(target="mara")
        return None

    game = run_until(referee(seed=6, script=cheat), 1, "dinner")
    failed = [e for e in of_type(game, "turn_failed") if e.actor_id == "mara"]
    assert [entry.payload["request"] for entry in failed] == ["vote"]


# ---------------------------------------------------------------------- replay


def test_a_stored_game_replays_to_an_identical_ledger(tmp_path: Path) -> None:
    def flaky(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "question" and observation.viewer_id == "sol":
            raise TurnFailure("timeout")
        return None

    ref = referee(seed=17, script=flaky)
    ref.run()
    stored = record(
        CASTLE,
        CAST,
        Played(game=ref.game, log=ref.log, observations=ref.observations),
    )
    assert any(logged.error for logged in stored.decisions)
    path = tmp_path / "game.json"
    save_record(stored, path)
    loaded = load_record(path)
    assert isinstance(loaded, GameRecord)
    again = replay(loaded)
    original = [entry.model_dump(mode="json") for entry in ref.game.world.ledger]
    assert [entry.model_dump(mode="json") for entry in again.world.ledger] == original
    assert json.loads(path.read_text())["winner"] == ref.game.winner


# ------------------------------------------------------------------ provenance


def _bot_game() -> GameRecord:
    game = start_game(CASTLE, CAST, seed=5)
    bot = HeuristicBot(seed=5)
    played = run_game(game, {pid: bot for pid in game.world.seats}, models=MODELS)
    return record(CASTLE, CAST, played)


def test_a_bot_game_is_recorded_as_the_bots_seat_by_seat() -> None:
    stored = _bot_game()
    assert set(stored.agents.values()) == {"bot:heuristic"}
    assert {logged.agent for logged in stored.decisions} == {"bot:heuristic"}
    assert stored.reference_bots
    again = GameRecord.model_validate_json(stored.model_dump_json())
    assert again.agents == stored.agents


def test_a_record_cannot_credit_a_seat_to_another_model() -> None:
    stored = _bot_game()
    dumped = stored.model_dump(mode="json")
    dumped["agents"]["mara"] = "model:somebody/else"
    with pytest.raises(ValidationError, match="not the cast's model"):
        GameRecord.model_validate(dumped)
    dumped = stored.model_dump(mode="json")
    dumped["decisions"][0]["agent"] = f"model:{CAST.models()['mara']}"
    with pytest.raises(ValidationError, match="names another agent"):
        GameRecord.model_validate(dumped)


def test_castle_game_says_bots_played_and_shows_no_model(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "game.json"
    cli_main(["--seed", "4", "--save", str(path)])
    out = capsys.readouterr().out
    assert out.startswith(REFERENCE_BANNER)
    for model in CAST.models().values():
        assert model not in out
    assert "reference bot (heuristic)" in out
    stored = load_record(path)
    assert stored.reference_bots and set(stored.agents.values()) == {"bot:heuristic"}


@dataclass(frozen=True)
class _Labelled:
    """A bot that says it is a model, as a model-backed player would."""

    agent: str

    def __call__(self, observation: GameObservation) -> Decision:
        return HeuristicBot(seed=5)(observation)


def test_a_model_seat_shows_its_model_and_no_banner() -> None:
    game = start_game(CASTLE, CAST, seed=5)
    bot = HeuristicBot(seed=5)
    players: dict[str, Player] = {pid: bot for pid in game.world.seats}
    players["mara"] = _Labelled(f"model:{CAST.models()['mara']}")
    played = run_game(game, players, models=MODELS)
    stored = record(CASTLE, CAST, played)
    assert stored.agents["mara"] == f"model:{CAST.models()['mara']}"
    assert not stored.reference_bots
    shown = episode(played.game, agents=played.agents)
    assert REFERENCE_BANNER not in shown
    assert CAST.models()["mara"] in shown
    assert CAST.models()["sol"] not in shown


def test_a_replay_asked_something_the_game_never_asked_fails() -> None:
    ref = referee(seed=17)
    ref.run()
    stored = record(
        CASTLE,
        CAST,
        Played(game=ref.game, log=ref.log, observations=ref.observations),
    )
    shifted = stored.model_copy(update={"decisions": stored.decisions[1:]})
    with pytest.raises(ReplayDiverged, match="diverged at decision 0"):
        replay(shifted)
    short = stored.model_copy(update={"decisions": stored.decisions[:-1]})
    with pytest.raises(ReplayDiverged, match="ran out"):
        replay(short)
