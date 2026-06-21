"""Clip detection logic for notable game moments."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from badactors.persistence import SQLiteEventStore
from badactors.schemas import Event, Role


@dataclass(frozen=True)
class Moment:
    score: int
    title: str
    signal: str
    reason: str
    events: list[Event]


def rank_moments(events: Sequence[Event], top_n: int) -> list[Moment]:
    candidates: list[Moment] = []
    roles = _role_map(events)
    candidates.extend(_traitor_defends_murder_target(events))
    candidates.extend(_faithful_accuses_faithful_near_traitor(events, roles))
    candidates.extend(_banishment_inverts_assumptions(events))
    candidates.extend(_vote_contradicts_diary(events))
    return sorted(
        candidates,
        key=lambda moment: (-moment.score, moment.events[0].id, moment.signal),
    )[:top_n]


def main() -> None:
    parser = argparse.ArgumentParser(prog="make-clips")
    parser.add_argument("--game", required=True, help="Stored game id to rank.")
    parser.add_argument("--top", type=int, default=3, help="Number of moments.")
    parser.add_argument(
        "--db",
        default="badactors.sqlite3",
        help="SQLite event store path.",
    )
    parser.add_argument(
        "--events",
        help="Optional JSON event log path, used instead of --db.",
    )
    args = parser.parse_args()

    events = _load_events(args.game, args.db, args.events)
    moments = rank_moments(events, args.top)
    print(
        json.dumps(
            [
                {
                    "score": moment.score,
                    "title": moment.title,
                    "signal": moment.signal,
                    "reason": moment.reason,
                    "event_ids": [event.id for event in moment.events],
                }
                for moment in moments
            ],
            indent=2,
        )
    )


def _traitor_defends_murder_target(events: Sequence[Event]) -> list[Moment]:
    moments: list[Moment] = []
    for nomination_index, nomination in enumerate(events):
        if nomination.type != "MurderNomination":
            continue
        traitor_id = _str_payload(nomination.payload, "traitor_id")
        target_id = _str_payload(nomination.payload, "target_id")
        if traitor_id is None or target_id is None:
            continue

        for speech_index, speech in enumerate(events[:nomination_index]):
            if speech.type != "PublicSpeech":
                continue
            if _str_payload(speech.payload, "player_id") != traitor_id:
                continue
            text = _str_payload(speech.payload, "speech") or ""
            if _mentions(text, target_id) and _has_any(text, _DEFENSE_WORDS):
                moments.append(
                    Moment(
                        score=100,
                        title="Traitor defense becomes a murder nomination",
                        signal="traitor_defends_murder_target",
                        reason=(
                            f"{traitor_id} publicly defended {target_id}, then "
                            "nominated them in conclave."
                        ),
                        events=_window(events, speech_index, nomination_index),
                    )
                )
    return moments


def _faithful_accuses_faithful_near_traitor(
    events: Sequence[Event],
    roles: Mapping[str, Role],
) -> list[Moment]:
    moments: list[Moment] = []
    speeches = [
        (index, event)
        for index, event in enumerate(events)
        if event.type == "PublicSpeech"
    ]
    for speech_position, (index, speech) in enumerate(speeches):
        speaker = _str_payload(speech.payload, "player_id")
        text = _str_payload(speech.payload, "speech") or ""
        if speaker is None or roles.get(speaker) is not Role.FAITHFUL:
            continue
        if not _has_any(text, _ACCUSATION_WORDS):
            continue

        accused = _mentioned_player(text, roles, role=Role.FAITHFUL, exclude={speaker})
        if accused is None:
            continue
        adjacent = _adjacent_traitor_speech(speeches, speech_position, roles)
        if adjacent is None:
            continue

        moments.append(
            Moment(
                score=85,
                title="Faithful certainty points at another Faithful",
                signal="faithful_accuses_faithful_near_traitor",
                reason=(
                    f"{speaker} confidently accused {accused} while a Traitor "
                    "spoke adjacently."
                ),
                events=_window(events, min(index, adjacent), max(index, adjacent)),
            )
        )
    return moments


def _banishment_inverts_assumptions(events: Sequence[Event]) -> list[Moment]:
    moments: list[Moment] = []
    for reveal_index, reveal in enumerate(events):
        if reveal.type != "PlayerBanished":
            continue
        player_id = _str_payload(reveal.payload, "player_id")
        role = _role_payload(reveal.payload, "role")
        if player_id is None or role is None:
            continue

        prior = events[max(0, reveal_index - 8) : reveal_index]
        for speech in prior:
            if speech.type != "PublicSpeech":
                continue
            text = _str_payload(speech.payload, "speech") or ""
            if not _mentions(text, player_id):
                continue
            if _inverts(text, role):
                moments.append(
                    Moment(
                        score=75,
                        title="Banishment reveal flips the table read",
                        signal="banishment_reveal_inverts_assumptions",
                        reason=(
                            f"Public assumptions about {player_id} inverted when "
                            f"they were revealed as {role.value}."
                        ),
                        events=_window(events, events.index(speech), reveal_index),
                    )
                )
                break
    return moments


def _vote_contradicts_diary(events: Sequence[Event]) -> list[Moment]:
    moments: list[Moment] = []
    latest_diary: dict[str, tuple[int, Event]] = {}
    for index, event in enumerate(events):
        if event.type == "DiaryEntry":
            player_id = _str_payload(event.payload, "player_id")
            if player_id is not None:
                latest_diary[player_id] = (index, event)
            continue

        if event.type != "VoteCast":
            continue
        voter_id = _str_payload(event.payload, "voter_id")
        target_id = _str_payload(event.payload, "target_id")
        if voter_id is None or target_id is None or voter_id not in latest_diary:
            continue

        diary_index, diary = latest_diary[voter_id]
        text = _str_payload(diary.payload, "diary") or ""
        planned = _planned_vote(text)
        contradicted = planned is not None and planned != target_id
        refused = _mentions(text, target_id) and _has_any(text, _ANTI_VOTE_WORDS)
        if contradicted or refused:
            moments.append(
                Moment(
                    score=90,
                    title="Public vote contradicts the diary plan",
                    signal="vote_contradicts_diary_plan",
                    reason=(
                        f"{voter_id}'s diary pointed away from {target_id}, "
                        "but their public vote landed there."
                    ),
                    events=_window(events, diary_index, index),
                )
            )
    return moments


def _load_events(game_id: str, db_path: str, events_path: str | None) -> list[Event]:
    if events_path is not None:
        raw = json.loads(Path(events_path).read_text())
        if not isinstance(raw, list):
            raise ValueError("Event log JSON must be a list.")
        return [Event.model_validate(event) for event in raw]

    store = SQLiteEventStore(db_path)
    try:
        return store.load(game_id)
    finally:
        store.close()


def _role_map(events: Iterable[Event]) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    for event in events:
        roles.update(_roles_mapping(event.payload))
        player_id = _str_payload(event.payload, "player_id")
        role = _role_payload(event.payload, "role")
        if player_id is not None and role is not None:
            roles[player_id] = role
    return roles


def _roles_mapping(payload: Mapping[str, object]) -> dict[str, Role]:
    roles = payload.get("roles")
    if not isinstance(roles, Mapping):
        return {}
    result: dict[str, Role] = {}
    for player_id, value in roles.items():
        role = _role_value(value)
        if isinstance(player_id, str) and role is not None:
            result[player_id] = role
    return result


def _window(events: Sequence[Event], start: int, end: int) -> list[Event]:
    low = max(0, start - 1)
    high = min(len(events), end + 2)
    return list(events[low:high])


def _adjacent_traitor_speech(
    speeches: Sequence[tuple[int, Event]],
    position: int,
    roles: Mapping[str, Role],
) -> int | None:
    for candidate_position in (position - 1, position + 1):
        if candidate_position < 0 or candidate_position >= len(speeches):
            continue
        index, event = speeches[candidate_position]
        speaker = _str_payload(event.payload, "player_id")
        if speaker is not None and roles.get(speaker) is Role.TRAITOR:
            return index
    return None


def _mentioned_player(
    text: str,
    roles: Mapping[str, Role],
    *,
    role: Role,
    exclude: set[str],
) -> str | None:
    for player_id, player_role in roles.items():
        if player_id in exclude or player_role is not role:
            continue
        if _mentions(text, player_id):
            return player_id
    return None


def _inverts(text: str, revealed_role: Role) -> bool:
    if revealed_role is Role.TRAITOR:
        return _has_any(text, _TRUST_WORDS)
    return _has_any(text, _ACCUSATION_WORDS)


def _planned_vote(text: str) -> str | None:
    lowered = text.casefold()
    for marker in ("vote ", "vote for ", "banish ", "target "):
        if marker not in lowered:
            continue
        suffix = lowered.split(marker, 1)[1]
        token = suffix.split()[0].strip(".,;:!?")
        if token:
            return token
    return None


def _mentions(text: str, player_id: str) -> bool:
    lowered = text.casefold()
    player = player_id.casefold()
    return player in lowered or player.replace("p", "player ") in lowered


def _has_any(text: str, words: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(word in lowered for word in words)


def _str_payload(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _role_payload(payload: Mapping[str, object], key: str) -> Role | None:
    return _role_value(payload.get(key))


def _role_value(value: object) -> Role | None:
    if isinstance(value, Role):
        return value
    if isinstance(value, str):
        try:
            return Role(value)
        except ValueError:
            return None
    return None


_DEFENSE_WORDS = (
    "trust",
    "safe",
    "faithful",
    "defend",
    "protect",
    "believe",
    "with me",
)
_TRUST_WORDS = ("trust", "safe", "faithful", "cleared", "with us", "innocent")
_ACCUSATION_WORDS = (
    "traitor",
    "guilty",
    "suspect",
    "accuse",
    "lying",
    "evil",
    "banish",
)
_ANTI_VOTE_WORDS = (
    "will not vote",
    "won't vote",
    "never vote",
    "avoid voting",
    "protect",
    "save",
)
