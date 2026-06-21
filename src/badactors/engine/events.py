"""Event construction helpers for engine outputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from badactors.schemas import Event, Phase, Role, Visibility

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class EventIdAllocator:
    game_id: str
    next_id: int = 1

    def create(
        self,
        *,
        phase: Phase,
        type: str,
        payload: Mapping[str, object],
        visibility: Visibility,
    ) -> Event:
        event_id = self.next_id
        self.next_id += 1
        return Event(
            id=event_id,
            game_id=self.game_id,
            ts=_EPOCH + timedelta(seconds=event_id),
            phase=phase,
            type=type,
            payload=dict(payload),
            visibility=visibility,
        )


def phase_advanced(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    from_phase: Phase,
    to_phase: Phase,
    round: int,
) -> Event:
    return allocator.create(
        phase=phase,
        type="PhaseAdvanced",
        payload={
            "from_phase": from_phase.value,
            "to_phase": to_phase.value,
            "round": round,
        },
        visibility=Visibility.PUBLIC,
    )


def public_speech(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    player_id: str,
    speech: str,
) -> Event:
    return allocator.create(
        phase=phase,
        type="PublicSpeech",
        payload={"player_id": player_id, "speech": speech},
        visibility=Visibility.PUBLIC,
    )


def diary_entry(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    player_id: str,
    diary: str,
) -> Event:
    return allocator.create(
        phase=phase,
        type="DiaryEntry",
        payload={"player_id": player_id, "diary": diary},
        visibility=Visibility.AUDIENCE,
    )


def vote_cast(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    voter_id: str,
    target_id: str,
) -> Event:
    return allocator.create(
        phase=phase,
        type="VoteCast",
        payload={"voter_id": voter_id, "target_id": target_id},
        visibility=Visibility.PUBLIC,
    )


def vote_tallied(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    tally: Mapping[str, int],
    selected_id: str | None,
) -> Event:
    return allocator.create(
        phase=phase,
        type="VoteTallied",
        payload={"tally": dict(tally), "selected_id": selected_id},
        visibility=Visibility.PUBLIC,
    )


def player_banished(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    player_id: str,
    role: Role,
) -> Event:
    return allocator.create(
        phase=phase,
        type="PlayerBanished",
        payload={"player_id": player_id, "role": role.value},
        visibility=Visibility.PUBLIC,
    )


def murder_nomination(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    traitor_id: str,
    target_id: str,
) -> Event:
    return allocator.create(
        phase=phase,
        type="MurderNomination",
        payload={"traitor_id": traitor_id, "target_id": target_id},
        visibility=Visibility.TRAITORS,
    )


def player_murdered(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    player_id: str,
    role: Role,
) -> Event:
    return allocator.create(
        phase=phase,
        type="PlayerMurdered",
        payload={"player_id": player_id, "role": role.value},
        visibility=Visibility.PUBLIC,
    )


def no_murder(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
) -> Event:
    return allocator.create(
        phase=phase,
        type="NoMurder",
        payload={},
        visibility=Visibility.PUBLIC,
    )


def endgame_choice(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    player_id: str,
    choice: str,
) -> Event:
    return allocator.create(
        phase=phase,
        type="EndgameChoice",
        payload={"player_id": player_id, "choice": choice},
        visibility=Visibility.PUBLIC,
    )


def game_over(
    allocator: EventIdAllocator,
    *,
    phase: Phase,
    winning_role: Role,
    reason: str,
) -> Event:
    return allocator.create(
        phase=phase,
        type="GameOver",
        payload={"winning_role": winning_role.value, "reason": reason},
        visibility=Visibility.PUBLIC,
    )
