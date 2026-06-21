"""Scoped context construction for agent-visible state."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from badactors.engine.rules import valid_actions
from badactors.schemas import Event, GameState, Player, Role, ScopedContext, Visibility

RULES_TEXT = (
    "Play the hidden-role social deduction game. Speak publicly, keep a private "
    "diary for the audience, and choose only an action valid for the current phase."
)


class LeakageError(AssertionError):
    pass


def build_context(state: GameState, viewer_id: str) -> ScopedContext:
    viewer = _player_by_id(state.players, viewer_id)
    public_history = [
        event.model_copy(deep=True)
        for event in state.public_history
        if event.visibility is Visibility.PUBLIC
    ]
    players_alive = [player.id for player in state.players if player.alive]

    fellow_traitors: list[str] | None = None
    conclave_history: list[Event] | None = None
    if viewer.role is Role.TRAITOR:
        fellow_traitors = sorted(
            traitor_id for traitor_id in state.traitor_ids if traitor_id != viewer_id
        )
        conclave_history = [
            event.model_copy(deep=True)
            for event in state.conclave_history
            if event.visibility is Visibility.TRAITORS
        ]

    return ScopedContext(
        you_id=viewer.id,
        you_role=viewer.role,
        fellow_traitors=fellow_traitors,
        players_alive=players_alive,
        public_history=public_history,
        conclave_history=conclave_history,
        your_diary_history=list(state.diaries.get(viewer_id, [])),
        current_phase=state.phase,
        rules=RULES_TEXT,
        valid_actions=valid_actions(state.phase),
    )


def assert_no_leak(ctx: ScopedContext, state: GameState) -> None:
    viewer = _player_by_id(state.players, ctx.you_id)
    if ctx.you_role is not viewer.role:
        raise LeakageError("Scoped context has the wrong viewer role.")

    expected_diary = state.diaries.get(ctx.you_id, [])
    if ctx.your_diary_history != expected_diary:
        raise LeakageError("Scoped context includes a non-viewer diary.")

    if viewer.role is Role.FAITHFUL:
        if ctx.fellow_traitors is not None:
            raise LeakageError("Faithful context includes fellow Traitors.")
        if ctx.conclave_history is not None:
            raise LeakageError("Faithful context includes conclave content.")
    elif ctx.fellow_traitors is not None:
        non_traitors = set(ctx.fellow_traitors) - state.traitor_ids
        if non_traitors:
            raise LeakageError("Traitor context names a non-Traitor as fellow Traitor.")

    allowed_role_subjects = _allowed_role_subjects(viewer, state)
    _assert_public_events_safe(ctx.public_history, allowed_role_subjects)
    if ctx.conclave_history is not None:
        _assert_conclave_events_safe(ctx.conclave_history, ctx, allowed_role_subjects)


def _assert_public_events_safe(
    public_history: Iterable[Event],
    allowed_role_subjects: set[str],
) -> None:
    for event in public_history:
        if event.visibility is not Visibility.PUBLIC:
            raise LeakageError("Public history contains non-public event.")
        _assert_no_secret_role_payload(event.payload, allowed_role_subjects)
        if event.type == "DiaryEntry":
            raise LeakageError("Public history contains a diary event.")


def _assert_conclave_events_safe(
    conclave_history: Iterable[Event],
    ctx: ScopedContext,
    allowed_role_subjects: set[str],
) -> None:
    for event in conclave_history:
        if event.visibility is not Visibility.TRAITORS:
            raise LeakageError("Conclave history contains non-Traitor event.")
        _assert_no_secret_role_payload(event.payload, allowed_role_subjects)
        if event.type == "DiaryEntry" and event.payload.get("player_id") != ctx.you_id:
            raise LeakageError("Conclave history contains a non-viewer diary.")


def _assert_no_secret_role_payload(
    payload: Mapping[str, object],
    allowed_role_subjects: set[str],
) -> None:
    for player_id, _role in _role_claims(payload):
        if player_id not in allowed_role_subjects:
            raise LeakageError("Scoped context leaks an unrevealed living role.")


def _role_claims(payload: Mapping[str, object]) -> list[tuple[str, Role]]:
    claims: list[tuple[str, Role]] = []

    subject_id = _subject_id(payload)
    role = _role_from_object(payload.get("role"))
    if subject_id is not None and role is not None:
        claims.append((subject_id, role))

    roles = payload.get("roles")
    if isinstance(roles, Mapping):
        for key, value in roles.items():
            role = _role_from_object(value)
            if isinstance(key, str) and role is not None:
                claims.append((key, role))

    for key in ("traitor_ids", "fellow_traitors"):
        traitor_ids = payload.get(key)
        if isinstance(traitor_ids, list):
            claims.extend(
                (traitor_id, Role.TRAITOR)
                for traitor_id in traitor_ids
                if isinstance(traitor_id, str)
            )

    return claims


def _subject_id(payload: Mapping[str, object]) -> str | None:
    for key in ("player_id", "id", "target_id", "subject_id"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _role_from_object(value: object) -> Role | None:
    if isinstance(value, Role):
        return value
    if isinstance(value, str):
        try:
            return Role(value)
        except ValueError:
            return None
    return None


def _allowed_role_subjects(viewer: Player, state: GameState) -> set[str]:
    revealed = {player.id for player in state.players if not player.alive}
    allowed = set(revealed)
    allowed.add(viewer.id)
    if viewer.role is Role.TRAITOR:
        allowed.update(state.traitor_ids)
    return allowed


def _player_by_id(players: Iterable[Player], player_id: str) -> Player:
    for player in players:
        if player.id == player_id:
            return player
    raise ValueError(f"Unknown player id: {player_id}")
