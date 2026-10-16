"""Every class of game secret has a planted leak, and each fails when real.

Two kinds of plant per class. The first doctors a real game at the moment it
matters — a record written to the wrong player, or an observation that says too
much — and checks that the checker names the class. The second puts the bug where
it would really live, in the referee's own code, and plays a game: the checker
must stop the game at the first observation that carries the leak.
"""

from __future__ import annotations

from typing import Any

import pytest

from badactors.castle import game as game_module
from badactors.castle import missions as missions_module
from badactors.castle import night as night_module
from badactors.castle import table as table_module
from badactors.castle import world as world_module
from badactors.castle.contracts import (
    LedgerEntry,
    ThingView,
    WitnessRecord,
    WorldState,
)
from badactors.castle.decisions import (
    Accept,
    Accuse,
    Decision,
    Nominate,
    Refuse,
    Request,
)
from badactors.castle.game import (
    GameState,
    living,
    living_faithful,
    living_traitors,
    shield_held_by,
)
from badactors.castle.ledger import append
from badactors.castle.night import (
    close_conclave,
    conclave_act,
    conclave_turn,
    end_night,
    settle_sleepers,
)
from badactors.castle.observe import (
    GameObservation,
    OfferReceived,
    assert_no_game_leak,
    observe_game,
)
from badactors.castle.perceive import Perception, awake_in
from badactors.castle.play import TurnFailure
from badactors.castle.scope import LeakageError
from badactors.castle.world import phase_complete, tick
from castle_scenes import (
    MODELS,
    NO_POISON,
    of_type,
    pause_at,
    plant_record,
    recruiting,
    referee,
    rewrite_record,
    run_until,
)

PLAN = Request(kind="plan", prompt="Plan.")


def _check(observation: GameObservation, game: GameState) -> None:
    assert_no_game_leak(observation, game, models=MODELS)


def _shows(observation: GameObservation, entry: LedgerEntry) -> bool:
    items = (*observation.world.recent, *observation.table)
    return any(item.ledger_seq == entry.seq for item in items)


def _leaked(game: GameState, viewer: str, entry: LedgerEntry) -> GameObservation:
    """The viewer's observation, proven to carry ``entry``."""
    observation = observe_game(game, viewer, PLAN)
    assert _shows(observation, entry), "the planted record never reached a prompt"
    return observation


def _alive(world: WorldState) -> list[str]:
    return [pid for pid in world.seats if world.players[pid].alive]


# ---------------------------------------------- a failure on a private decision


def test_planted_leak_a_failed_poison_turn_seen_at_dinner() -> None:
    def flaky(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "poison":
            raise TurnFailure("the provider returned nothing")
        return None

    ref = referee(seed=6, script=flaky)
    game = run_until(ref, 1, "night")
    failed = next(
        entry
        for entry in of_type(game, "turn_failed")
        if entry.payload["request"] == "poison"
    )
    faithful = next(
        pid
        for pid in game.seating
        if game.roles[pid] == "faithful" and pid in _alive(game.world)
    )
    planted = plant_record(
        game,
        failed,
        faithful,
        sense="saw",
        text="Someone hesitated and did nothing.",
        recorded_actor=None,
    )
    with pytest.raises(LeakageError, match="a failure on a private decision"):
        _check(_leaked(game, faithful, planted), game)


# ----------------------------------------------------- another player's role


def _first_decision() -> GameState:
    game, _ = pause_at(lambda observation, game: True)
    return game


def test_planted_leak_another_players_role_in_memory() -> None:
    game = _first_decision()
    faithful, traitor = living_faithful(game)[0], living_traitors(game)[0]
    told = next(
        entry
        for entry in of_type(game, "role_told")
        if entry.payload["player"] == traitor
    )
    planted = plant_record(game, told, faithful)
    with pytest.raises(LeakageError, match="another player's role"):
        _check(_leaked(game, faithful, planted), game)


def test_planted_leak_another_living_players_role_in_the_roles_known() -> None:
    game = _first_decision()
    faithful, traitor = living_faithful(game)[0], living_traitors(game)[0]
    honest = observe_game(game, faithful, PLAN)
    _check(honest, game)
    for leaked in (
        honest.model_copy(
            update={"known_roles": {**honest.known_roles, traitor: "traitor"}}
        ),
        honest.model_copy(update={"fellow_traitors": [traitor]}),
    ):
        with pytest.raises(LeakageError, match="another living player's role"):
            _check(leaked, game)


def test_a_referee_that_tells_everyone_every_role_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = game_module.tell

    def loud(world: WorldState, recipients: Any, **kwargs: Any) -> LedgerEntry:
        if kwargs.get("type") == "role_told":
            recipients = _alive(world)
        return real(world, recipients, **kwargs)

    monkeypatch.setattr(game_module, "tell", loud)
    with pytest.raises(LeakageError, match="another player's role"):
        referee(seed=3).run()


# ------------------------------------------------ conclave content, and the kill


def _conclave() -> tuple[GameState, GameObservation]:
    return pause_at(
        lambda observation, game: observation.request.kind == "nominate",
        tools=NO_POISON,
    )


def test_planted_leak_conclave_content_reaching_a_faithful() -> None:
    game, _ = _conclave()
    notice = of_type(game, "conclave")[-1]
    faithful = living_faithful(game)[0]
    planted = plant_record(game, notice, faithful)
    with pytest.raises(LeakageError, match="conclave content"):
        _check(_leaked(game, faithful, planted), game)


def test_planted_leak_a_traitors_decision_put_to_a_faithful() -> None:
    game, observation = _conclave()
    leaked = observe_game(game, living_faithful(game)[0], observation.request)
    with pytest.raises(LeakageError, match="conclave content reaching a Faithful"):
        _check(leaked, game)


def test_a_referee_that_opens_the_conclave_to_everyone_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = night_module.tell

    def loud(world: WorldState, recipients: Any, **kwargs: Any) -> LedgerEntry:
        if kwargs.get("type") in ("conclave_call", "conclave"):
            recipients = _alive(world)
        return real(world, recipients, **kwargs)

    monkeypatch.setattr(night_module, "tell", loud)
    with pytest.raises(LeakageError, match="conclave content"):
        referee(seed=3, tools=NO_POISON).run()


def _dawn(game: GameState) -> None:
    """Finish a paused conclave and the rest of the night, as the referee does."""
    while (turn := conclave_turn(game)) is not None:
        pid, request = turn
        victims = [p for p in request.options if shield_held_by(game, p) is None]
        conclave_act(
            game, pid, request, Nominate(target=(victims or request.options)[0])
        )
    close_conclave(game)
    while not phase_complete(game.world):
        game.world = tick(game.world)
        settle_sleepers(game)
    end_night(game)


def test_planted_leak_the_nights_target_before_breakfast() -> None:
    game, _ = _conclave()
    _dawn(game)
    announced = {fate.player_id for fate in game.fates}
    victim = next(
        pid
        for pid in game.world.seats
        if not game.world.players[pid].alive and pid not in announced
    )
    witness = living_faithful(game)[0]
    honest = observe_game(game, witness, PLAN)
    _check(honest, game)
    assert victim in honest.living
    leaked = honest.model_copy(
        update={"living": [pid for pid in honest.living if pid != victim]}
    )
    with pytest.raises(LeakageError, match="the night's target before breakfast"):
        _check(leaked, game)


def test_planted_leak_the_kill_in_a_faithfuls_memory() -> None:
    game, _ = _conclave()
    _dawn(game)
    attempt = of_type(game, "kill_attempt")[-1]
    witness = next(
        pid for pid in living_faithful(game) if pid != attempt.payload["target"]
    )
    planted = plant_record(game, attempt, witness)
    with pytest.raises(LeakageError, match="the night's target"):
        _check(_leaked(game, witness, planted), game)


def test_a_referee_that_lets_the_house_hear_the_kill_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = night_module.note

    def loud(world: WorldState, **kwargs: Any) -> LedgerEntry:
        if kwargs.get("type") != "kill_attempt":
            return real(world, **kwargs)
        heard = [
            WitnessRecord(
                player_id=pid,
                sense="saw",
                confidence="clear",
                channel="direct",
                text=kwargs["text"],
            )
            for pid in _alive(world)
        ]
        return append(world, **kwargs, witnesses=heard)

    monkeypatch.setattr(night_module, "note", loud)
    with pytest.raises(LeakageError, match="the night's target"):
        referee(seed=3, tools=NO_POISON).run()


# --------------------------------------------- another player's diary or reason


def _at_a_vote_after(kind: str) -> tuple[GameState, GameObservation]:
    def when(observation: GameObservation, game: GameState) -> bool:
        return observation.request.kind == "vote" and any(
            item.kind == kind and item.actor not in (None, observation.viewer_id)
            for item in observation.table
        )

    return pause_at(when, seed=6)


def test_planted_leak_another_players_silence_reason() -> None:
    game, observation = _at_a_vote_after("stay_silent")
    viewer = observation.viewer_id
    item = next(
        item
        for item in observation.table
        if item.kind == "stay_silent" and item.actor not in (None, viewer)
    )
    entry = game.world.ledger[item.ledger_seq - 1]
    rewrite_record(game, entry, viewer, text=f"{item.text} ({entry.private['reason']})")
    leaked = observe_game(game, viewer, observation.request)
    with pytest.raises(LeakageError, match="diary or silence reason"):
        _check(leaked, game)


def test_planted_leak_another_players_diary() -> None:
    game, observation = _at_a_vote_after("accuse")
    viewer = observation.viewer_id
    item = next(
        item
        for item in observation.table
        if item.kind == "accuse" and item.actor not in (None, viewer)
    )
    entry = game.world.ledger[item.ledger_seq - 1]
    rewrite_record(game, entry, viewer, text=f"{item.text} {entry.private['diary']}")
    leaked = observe_game(game, viewer, observation.request)
    with pytest.raises(LeakageError, match="diary or silence reason"):
        _check(leaked, game)


def test_a_referee_that_reads_out_reasons_for_silence_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = table_module.append_perceived

    def loud(world: WorldState, perception: Perception, **kwargs: Any) -> LedgerEntry:
        if kwargs.get("type") == "stay_silent":
            reason = kwargs["private"]["reason"]
            perception.records = [
                record
                if record.player_id == kwargs["actor_id"]
                else record.model_copy(update={"text": f"{record.text} ({reason})"})
                for record in perception.records
            ]
        return real(world, perception, **kwargs)

    monkeypatch.setattr(table_module, "append_perceived", loud)
    with pytest.raises(LeakageError, match="diary or silence reason"):
        referee(seed=6).run()


# ------------------------------------------------------- who holds a shield


def _after_the_award() -> tuple[GameState, str, str]:
    game, _ = pause_at(
        lambda observation, game: (
            observation.phase == "free_time" and bool(of_type(game, "awarded"))
        )
    )
    award = of_type(game, "awarded")[-1]
    holder = award.payload["holder_id"]
    other = next(pid for pid in living(game) if pid != holder)
    return game, holder, other


def test_planted_leak_who_holds_a_shield() -> None:
    game, _, other = _after_the_award()
    planted = plant_record(game, of_type(game, "awarded")[-1], other)
    with pytest.raises(LeakageError, match="who holds a shield"):
        _check(_leaked(game, other, planted), game)


def test_planted_leak_a_shield_in_someone_elses_hands() -> None:
    game, _, other = _after_the_award()
    shield = of_type(game, "awarded")[-1].payload["thing_id"]
    honest = observe_game(game, other, PLAN)
    _check(honest, game)
    token = ThingView(id=shield, kind="shield", name="shield token")
    leaked = honest.model_copy(
        update={
            "world": honest.world.model_copy(
                update={"carrying": [*honest.world.carrying, token]}
            )
        }
    )
    with pytest.raises(LeakageError, match="carries"):
        _check(leaked, game)


def test_a_referee_that_hands_out_shields_in_public_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = missions_module.append

    def loud(world: WorldState, **kwargs: Any) -> LedgerEntry:
        if kwargs.get("type") == "awarded":
            (told,) = kwargs["witnesses"]
            kwargs["witnesses"] = [
                told.model_copy(update={"player_id": pid}) for pid in _alive(world)
            ]
        return real(world, **kwargs)

    monkeypatch.setattr(missions_module, "append", loud)
    with pytest.raises(LeakageError, match="who holds a shield"):
        referee(seed=4).run()


# ------------------------------------------------------ a recruitment offer


def _offer() -> tuple[GameState, GameObservation, LedgerEntry, str]:
    game, observation = pause_at(
        lambda observation, game: observation.request.kind == "recruit_answer",
        script=recruiting(Accept()),
        seed=11,
    )
    offer = of_type(game, "recruit_offer")[-1]
    party = (offer.actor_id, offer.payload["target"])
    bystander = next(pid for pid in living(game) if pid not in party)
    return game, observation, offer, bystander


def test_planted_leak_a_recruitment_offer_seen_by_a_bystander() -> None:
    game, _, offer, bystander = _offer()
    planted = plant_record(game, offer, bystander)
    with pytest.raises(LeakageError, match="a recruitment offer"):
        _check(_leaked(game, bystander, planted), game)


def test_planted_leak_an_offer_shown_to_someone_it_was_not_made_to() -> None:
    game, observation, offer, bystander = _offer()
    honest = observe_game(game, bystander, PLAN)
    _check(honest, game)
    received = OfferReceived(
        day=offer.day, recruiter=offer.actor_id or "", seq=offer.seq
    )
    for leaked in (
        honest.model_copy(update={"offers": [received]}),
        observe_game(game, bystander, observation.request),
    ):
        with pytest.raises(LeakageError, match="seen by a non-participant"):
            _check(leaked, game)


def test_a_referee_that_makes_offers_aloud_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = night_module.append

    def loud(world: WorldState, **kwargs: Any) -> LedgerEntry:
        if kwargs.get("type") == "recruit_offer":
            own, heard = kwargs["witnesses"]
            room = kwargs["room_id"]
            others = [
                pid
                for pid in _alive(world)
                if world.players[pid].room_id == room
                and pid not in (own.player_id, heard.player_id)
            ]
            kwargs["witnesses"] = [
                own,
                heard,
                *(heard.model_copy(update={"player_id": pid}) for pid in others),
            ]
        return real(world, **kwargs)

    monkeypatch.setattr(night_module, "append", loud)
    with pytest.raises(LeakageError, match="a recruitment offer"):
        referee(seed=11, script=recruiting(Refuse())).run()


# ---------------------------------------------------------------- a whisper


def test_planted_leak_a_whisper_overheard() -> None:
    game, _ = pause_at(lambda observation, game: bool(of_type(game, "whisper")))
    whisper = of_type(game, "whisper")[-1]
    pair = (whisper.actor_id, whisper.payload["to"])
    bystander = next(pid for pid in living(game) if pid not in pair)
    planted = plant_record(game, whisper, bystander)
    with pytest.raises(LeakageError, match="a whisper"):
        _check(_leaked(game, bystander, planted), game)


def test_a_world_that_lets_the_room_hear_whispers_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = world_module.append

    def loud(world: WorldState, **kwargs: Any) -> LedgerEntry:
        if kwargs.get("type") == "whisper":
            own, heard = kwargs["witnesses"]
            others = [
                pid
                for pid in awake_in(world, kwargs["room_id"])
                if pid not in (own.player_id, heard.player_id)
            ]
            kwargs["witnesses"] = [
                own,
                heard,
                *(heard.model_copy(update={"player_id": pid}) for pid in others),
            ]
        return real(world, **kwargs)

    monkeypatch.setattr(world_module, "append", loud)
    with pytest.raises(LeakageError, match="a whisper"):
        referee(seed=3).run()


# ----------------------------------------------------------------- model names


def test_a_player_naming_a_model_aloud_is_refused_and_never_heard() -> None:
    def blurt(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "accuse" and observation.viewer_id == "mara":
            return Accuse(
                target=observation.request.options[0],
                text=f"You argue exactly like {MODELS[0]}.",
            )
        return None

    ref = referee(seed=6, script=blurt)
    ref.run()
    assert ref.game.status == "over"
    assert MODELS[0].casefold() not in ref.game.world.model_dump_json().casefold()
    failed = of_type(ref.game, "turn_failed")
    assert failed
    assert {(e.actor_id, e.payload["request"]) for e in failed} == {("mara", "accuse")}
    assert len(of_type(ref.game, "refused")) == len(failed)
