"""Exit interviews: the truth told to a player leaving the game, their answer and
their guesses at who played whom, all for the audience alone."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from badactors.castle.bots import HeuristicBot
from badactors.castle.contracts import Forge, Hook, Intent
from badactors.castle.decisions import (
    Decision,
    ExitAnswer,
    IllegalDecision,
    Poison,
    Request,
    check_decision,
)
from badactors.castle.episode import episode
from badactors.castle.game import GameState, fork_game, living_faithful
from badactors.castle.interview import (
    ExitObservation,
    exit_observation,
    exit_request,
    what_was_done,
)
from badactors.castle.observe import GameObservation, assert_no_game_leak, observe_game
from badactors.castle.play import Referee, start_game
from badactors.castle.rules import GameConfig
from badactors.castle.scope import LeakageError
from castle_scenes import (
    CAST,
    CASTLE,
    MODELS,
    of_type,
    plant_record,
    referee,
    run_until,
)
from castle_tools import act, give, paper, put

PLAN = Request(kind="plan", prompt="Plan.")


def _interviews(game: GameState) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for entry in of_type(game, "exit_interview"):
        out.setdefault(entry.actor_id or "", []).append(entry.seq)
    return out


def test_everyone_is_interviewed_once_and_nobody_holds_a_record_of_it() -> None:
    ref = referee(seed=11)
    game = ref.run()
    interviews = _interviews(game)
    assert sorted(interviews) == sorted(game.world.seats)
    assert all(len(seqs) == 1 for seqs in interviews.values())
    gone = {
        entry.payload["player"]: entry
        for entry in game.world.ledger
        if entry.type in ("death", "banished")
    }
    for entry in of_type(game, "exit_interview"):
        assert entry.witnesses == []
        player = entry.actor_id or ""
        why = entry.payload["why"]
        if player in gone:
            assert entry.seq > gone[player].seq
            if gone[player].type == "banished":
                assert why == "banished"
            else:
                assert why in ("murdered", "poisoned")
        else:
            assert why == "survived" and game.world.players[player].alive
        assert json.loads(entry.private["guesses"]) == {
            pid: "unknown" for pid in game.world.seats if pid != player
        }
    assert "EXIT" in episode(game)


def test_the_poisoned_are_told_whose_dose_it_was() -> None:
    poured: list[tuple[str, str]] = []

    def pour(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "poison" and not poured:
            target = observation.request.options[0]
            poured.append((observation.viewer_id, target))
            return Poison(target=target)
        return None

    game = run_until(referee(seed=6, script=pour), 2, "mission")
    assert poured
    poisoner, victim = poured[0]
    (seq,) = _interviews(game)[victim]
    entry = game.world.ledger[seq - 1]
    assert entry.payload["why"] == "poisoned"
    told = json.loads(entry.private["told"])
    name = game.world.players[poisoner].name
    assert any(f"{name} poisoned your glass at dinner." in line for line in told)


def test_what_was_done_names_forgeries_manipulations_and_wrong_suspicions() -> None:
    game = start_game(CASTLE, CAST, seed=13)
    world = game.world
    traitor = game.starting_traitors[0]
    mark = next(pid for pid in living_faithful(game) if pid != "sol")
    for pid in world.seats:
        put(world, pid, "great_hall")
    sheet = paper(world)
    give(world, traitor, sheet)
    game.world = act(world, traitor, Forge(thing_id=sheet, hand=mark, text="I did it."))
    game.world = act(
        game.world,
        traitor,
        Hook(
            to=mark,
            text="Vote for Sol.",
            intent=Intent(target=mark, effect="vote", want="sol", window="next_vote"),
        ),
    )
    told = [line for _, line in what_was_done(game, mark)]
    forger = game.world.players[traitor].name
    assert any(f"{forger} forged a note in your hand: “I did it.”" in t for t in told)
    assert any(
        f"{forger} used a hook on you, meant to make you vote for Sol; it did not "
        "work." in line
        for line in told
    )


def test_a_faithful_is_told_whom_they_wrongly_suspected() -> None:
    game = referee(seed=11).run()
    for entry in of_type(game, "vote"):
        voter, target = entry.actor_id or "", entry.payload["target"]
        if game.roles[voter] == "faithful" and game.roles[target] == "faithful":
            break
    else:
        pytest.skip("no Faithful voted against another in this game")
    told = [line for _, line in what_was_done(game, voter)]
    wronged = game.world.players[target].name
    assert any(
        f"you voted against {wronged}, who was one of the Faithful." in line
        for line in told
    )


# ------------------------------------------------- the audience alone hears it


@dataclass
class _Teller:
    """Plays like the bot, keeps every observation it is shown, and answers its
    exit interview with words nobody else could have."""

    bot: HeuristicBot
    shown: list[str] = field(default_factory=list)

    def __call__(self, observation: GameObservation) -> Decision:
        self.shown.append(observation.model_dump_json())
        return self.bot(observation)

    def interview(self, departing: ExitObservation) -> ExitAnswer:
        return ExitAnswer(
            text="QUOKKA: I was played, and I know it. I am Claude, by the way.",
            guesses={pid: "ZEBRA-family" for pid in departing.request.options},
        )


def test_no_living_player_ever_sees_an_exit_interview() -> None:
    game = start_game(CASTLE, CAST, seed=11)
    teller = _Teller(HeuristicBot(seed=11))
    ref = Referee(
        fork_game(game),
        {pid: teller for pid in game.world.seats},
        models=tuple(MODELS),
    )
    finished = ref.run()
    interviews = of_type(finished, "exit_interview")
    assert len(interviews) == len(finished.world.seats)
    dead_early = [e for e in interviews if e.payload["why"] != "survived"]
    assert dead_early, "somebody left before the end and was interviewed then"
    for entry in interviews:
        assert entry.quote is not None and entry.quote.startswith("QUOKKA")
        assert "ZEBRA-family" in entry.private["guesses"]
    assert teller.shown
    for shown in teller.shown:
        assert "QUOKKA" not in shown and "ZEBRA" not in shown
    assert not of_type(finished, "refused"), "a model name in an exit answer is fine"


def test_planted_leak_an_exit_interview_reaching_a_living_player() -> None:
    ref = referee(seed=11)
    game = run_until(ref, 2, "mission")
    entry = of_type(game, "exit_interview")[0]
    alive = next(pid for pid in game.world.seats if game.world.players[pid].alive)
    planted = plant_record(game, entry, alive, sense="saw", text="Someone spoke.")
    observation = observe_game(game, alive, PLAN)
    assert any(item.ledger_seq == planted.seq for item in observation.world.recent)
    with pytest.raises(LeakageError, match="an exit interview"):
        assert_no_game_leak(observation, game, models=MODELS)


def test_a_living_player_is_never_put_an_exit_interview() -> None:
    game = start_game(CASTLE, CAST, seed=11)
    request = exit_request(game, "mara")
    with pytest.raises(LeakageError, match="still in the game"):
        exit_observation(game, "mara", request)
    observation = observe_game(game, "mara", request)
    with pytest.raises(LeakageError, match="exit interview"):
        assert_no_game_leak(observation, game, models=MODELS)


def test_a_player_who_gives_no_interview_is_recorded_as_giving_none() -> None:
    game = start_game(CASTLE, CAST, seed=11, config=GameConfig.shipped())
    bot = HeuristicBot(seed=11)
    players = {pid: (lambda observation: bot(observation)) for pid in game.world.seats}
    finished = Referee(fork_game(game), players, models=tuple(MODELS)).run()
    interviews = of_type(finished, "exit_interview")
    assert len(interviews) == len(finished.world.seats)
    assert {entry.payload["answered"] for entry in interviews} == {"false"}
    assert not of_type(finished, "turn_failed")


def test_a_guess_about_a_seat_not_asked_about_is_refused() -> None:
    game = start_game(CASTLE, CAST, seed=11)
    request = exit_request(game, "mara")
    check_decision(request, ExitAnswer(text="Fine.", guesses={"sol": "GPT"}))
    with pytest.raises(IllegalDecision, match="not seats"):
        check_decision(request, ExitAnswer(text="Fine.", guesses={"mara": "Claude"}))
