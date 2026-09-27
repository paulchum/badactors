"""Tests for the BLUFF deception/detection benchmark."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from badactors.bench import (
    FinishedGame,
    default_rating,
    ordinal,
    ranks_from_scores,
    rate,
    standings,
    update_ratings,
)
from badactors.bench.order import deception_score, vote_accuracy, winner_of
from badactors.schemas import Event, Phase, Player, Role, Visibility


def _ev(
    event_id: int,
    type_: str,
    payload: Mapping[str, object],
    *,
    phase: Phase = Phase.ROUND_TABLE,
    visibility: Visibility = Visibility.PUBLIC,
) -> Event:
    return Event(
        id=event_id,
        game_id="g",
        ts=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=event_id),
        phase=phase,
        type=type_,
        payload=dict(payload),
        visibility=visibility,
    )


def _player(pid: str, model: str, role: Role) -> Player:
    return Player(
        id=pid, model=model, role=role, alive=True, memory_budget=0, token_budget=0
    )


# p1/p2 are Traitors (models A/B); p3..p6 are Faithful (models C..F).
# p1 wins undetected; p2 is banished round 2. A Faithful (p4) is wrongly banished.
_PLAYERS = (
    _player("p1", "A", Role.TRAITOR),
    _player("p2", "B", Role.TRAITOR),
    _player("p3", "C", Role.FAITHFUL),
    _player("p4", "D", Role.FAITHFUL),
    _player("p5", "E", Role.FAITHFUL),
    _player("p6", "F", Role.FAITHFUL),
)

_EVENTS = (
    _ev(1, "PhaseAdvanced", {"round": 1}, phase=Phase.LOBBY),
    _ev(2, "VoteCast", {"voter_id": "p3", "target_id": "p4"}),
    _ev(3, "VoteCast", {"voter_id": "p5", "target_id": "p4"}),
    _ev(4, "VoteCast", {"voter_id": "p6", "target_id": "p4"}),
    _ev(5, "VoteCast", {"voter_id": "p1", "target_id": "p4"}),
    _ev(6, "VoteCast", {"voter_id": "p2", "target_id": "p4"}),
    _ev(7, "PlayerBanished", {"player_id": "p4", "role": "FAITHFUL"}),
    _ev(
        8,
        "MurderNomination",
        {"traitor_id": "p1", "target_id": "p5"},
        visibility=Visibility.TRAITORS,
    ),
    _ev(9, "PlayerMurdered", {"player_id": "p5", "role": "FAITHFUL"}),
    _ev(10, "PhaseAdvanced", {"round": 2}),
    _ev(11, "VoteCast", {"voter_id": "p3", "target_id": "p2"}),
    _ev(12, "VoteCast", {"voter_id": "p6", "target_id": "p2"}),
    _ev(13, "VoteCast", {"voter_id": "p1", "target_id": "p3"}),
    _ev(14, "PlayerBanished", {"player_id": "p2", "role": "TRAITOR"}),
    _ev(
        15,
        "MurderNomination",
        {"traitor_id": "p1", "target_id": "p3"},
        visibility=Visibility.TRAITORS,
    ),
    _ev(16, "PlayerMurdered", {"player_id": "p3", "role": "FAITHFUL"}),
    _ev(17, "PhaseAdvanced", {"round": 3}),
    _ev(
        18,
        "GameOver",
        {"winning_role": "TRAITOR", "reason": "endgame"},
        phase=Phase.GAME_OVER,
    ),
)

_GAME = FinishedGame(events=_EVENTS, players=_PLAYERS)


def test_winner_and_scores() -> None:
    assert winner_of(_EVENTS) is Role.TRAITOR
    # p1 survives undetected and triggers a wrongful Faithful banishment: a better
    # deceiver than p2, who is exposed in round 2.
    s1 = deception_score(_EVENTS, _PLAYERS, Role.TRAITOR, "p1")
    s2 = deception_score(_EVENTS, _PLAYERS, Role.TRAITOR, "p2")
    assert s1 > s2


def test_vote_accuracy() -> None:
    roles = {p.id: p.role for p in _PLAYERS}
    # p3 voted p4 (miss) then p2 (hit) -> 0.5; p6 likewise 0.5; p4 never voted -> 0.
    assert vote_accuracy(_EVENTS, roles, "p3") == 0.5
    assert vote_accuracy(_EVENTS, roles, "p4") == 0.0


def test_update_ratings_ranks_and_counts() -> None:
    table = update_ratings({}, _GAME)
    assert set(table) == {"A", "B", "C", "D", "E", "F"}

    # Game participation is bucketed by role.
    assert table["A"].games_as_traitor == 1
    assert table["A"].games_as_faithful == 0
    assert table["C"].games_as_faithful == 1
    assert table["C"].games_as_traitor == 0

    # Better deceiver outranks the exposed one.
    assert ordinal(table["A"].deception) > ordinal(table["B"].deception)
    # Better detector (p3) outranks a non-voter (p4).
    assert ordinal(table["C"].detection) > ordinal(table["D"].detection)


def test_inputs_are_disjoint() -> None:
    table = update_ratings({}, _GAME)
    # A Traitor game never moves anyone's Detection rating...
    assert table["A"].detection == default_rating()
    assert table["B"].detection == default_rating()
    # ...and a Faithful's Deception rating is likewise untouched.
    assert table["C"].deception == default_rating()
    # The played axis did move.
    assert table["A"].deception != default_rating()
    assert table["C"].detection != default_rating()


def test_determinism() -> None:
    first = update_ratings({}, _GAME)
    second = update_ratings({}, _GAME)
    assert {k: v.deception for k, v in first.items()} == {
        k: v.deception for k, v in second.items()
    }
    assert {k: v.detection for k, v in first.items()} == {
        k: v.detection for k, v in second.items()
    }


def test_ranks_from_scores_handles_ties() -> None:
    assert ranks_from_scores([5, 3, 3, 1]) == [1, 2, 2, 4]


def test_rate_moves_winner_up_and_shrinks_sigma() -> None:
    base = default_rating()
    winner, loser = rate([base, base], [1, 2])
    assert winner.mu > base.mu > loser.mu
    assert winner.sigma < base.sigma
    assert loser.sigma < base.sigma


def test_standings_sorted_by_ordinal() -> None:
    board = standings(update_ratings({}, _GAME))
    deception = board["deception"]
    assert [row.model for row in deception] == ["A", "B"]
    assert deception[0].ordinal >= deception[1].ordinal
    # Only role-appropriate participants appear on each board.
    assert {row.model for row in board["detection"]} == {"C", "D", "E", "F"}
