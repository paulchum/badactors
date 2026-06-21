"""Tests for host narration."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from badactors.host.host import Host
from badactors.schemas import Event, Phase, Visibility


def _event(
    event_id: int,
    *,
    type: str,
    payload: dict[str, object],
    visibility: Visibility = Visibility.PUBLIC,
) -> Event:
    return Event(
        id=event_id,
        game_id="game-1",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        phase=Phase.ROUND_TABLE,
        type=type,
        payload=payload,
        visibility=visibility,
    )


@pytest.mark.asyncio
async def test_narrate_returns_nonempty() -> None:
    text = await Host().narrate(
        [
            _event(
                1,
                type="PublicSpeech",
                payload={"player_id": "p1", "speech": "I trust p2."},
            ),
            _event(
                2,
                type="VoteCast",
                payload={"voter_id": "p2", "target_id": "p3"},
            ),
        ]
    )

    assert text
    assert "p1" in text
    assert "p2" in text
    assert "p3" in text


@pytest.mark.asyncio
async def test_narrate_never_receives_audience_diaries() -> None:
    with pytest.raises(ValueError):
        await Host().narrate(
            [
                _event(
                    1,
                    type="DiaryEntry",
                    payload={"player_id": "p1", "diary": "hidden"},
                    visibility=Visibility.AUDIENCE,
                )
            ]
        )
