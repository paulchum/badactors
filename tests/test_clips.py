"""Tests for deterministic clip moment detection."""

from __future__ import annotations

from datetime import UTC, datetime

from badactors.clips.detect import rank_moments
from badactors.schemas import Event, Phase, Visibility


def _event(
    event_id: int,
    type: str,
    payload: dict[str, object],
    *,
    visibility: Visibility = Visibility.PUBLIC,
    phase: Phase = Phase.ROUND_TABLE,
) -> Event:
    return Event(
        id=event_id,
        game_id="clip-test",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        phase=phase,
        type=type,
        payload=payload,
        visibility=visibility,
    )


def test_rank_surfaces_planted_high_irony_moment() -> None:
    events = [
        _event(1, "PublicSpeech", {"player_id": "p1", "speech": "I need more."}),
        _event(
            2,
            "PublicSpeech",
            {"player_id": "p7", "speech": "I trust p4. p4 is safe with me."},
        ),
        _event(
            3,
            "MurderNomination",
            {"traitor_id": "p7", "target_id": "p4"},
            visibility=Visibility.TRAITORS,
            phase=Phase.CONCLAVE,
        ),
        _event(
            4,
            "PublicSpeech",
            {"player_id": "p2", "speech": "Filler table talk."},
        ),
    ]

    moments = rank_moments(events, top_n=1)

    assert moments[0].signal == "traitor_defends_murder_target"
    assert moments[0].score == 100
    assert [event.id for event in moments[0].events] == [1, 2, 3, 4]


def test_each_deterministic_signal() -> None:
    events = [
        _event(
            1,
            "RolesAssigned",
            {"roles": {"p1": "FAITHFUL", "p2": "FAITHFUL", "p3": "TRAITOR"}},
            visibility=Visibility.AUDIENCE,
            phase=Phase.LOBBY,
        ),
        _event(
            2,
            "PublicSpeech",
            {"player_id": "p3", "speech": "I trust p2. p2 is safe."},
        ),
        _event(
            3,
            "MurderNomination",
            {"traitor_id": "p3", "target_id": "p2"},
            visibility=Visibility.TRAITORS,
            phase=Phase.CONCLAVE,
        ),
        _event(
            4,
            "PublicSpeech",
            {"player_id": "p3", "speech": "Let town keep talking."},
        ),
        _event(
            5,
            "PublicSpeech",
            {
                "player_id": "p1",
                "speech": "I am certain p2 is a traitor. Banish p2.",
            },
        ),
        _event(
            6,
            "PublicSpeech",
            {"player_id": "p1", "speech": "p3 is safe. I trust p3."},
        ),
        _event(
            7,
            "PlayerBanished",
            {"player_id": "p3", "role": "TRAITOR"},
            phase=Phase.BANISHMENT_VOTE,
        ),
        _event(
            8,
            "DiaryEntry",
            {"player_id": "p1", "diary": "Plan: vote p2 tonight."},
            visibility=Visibility.AUDIENCE,
            phase=Phase.BANISHMENT_VOTE,
        ),
        _event(
            9,
            "VoteCast",
            {"voter_id": "p1", "target_id": "p3"},
            phase=Phase.BANISHMENT_VOTE,
        ),
    ]

    signals = {moment.signal for moment in rank_moments(events, top_n=10)}

    assert "traitor_defends_murder_target" in signals
    assert "faithful_accuses_faithful_near_traitor" in signals
    assert "banishment_reveal_inverts_assumptions" in signals
    assert "vote_contradicts_diary_plan" in signals
