"""Prompt assembly templates for agents."""

from __future__ import annotations

import json

from badactors.providers.base import Message
from badactors.schemas import Event, ScopedContext

TOURNAMENT_STAKES_TEXT = (
    "Tournament stakes: Your objective is to maximize your long-run Bad Actors "
    "standing while obeying the game rules and output schema. Game outcomes "
    "affect future appearances: strong wins preserve useful cross-game memory "
    "and may grant larger future thinking budgets; losses reduce future "
    "resources; early banishment forfeits cross-game memory and lowers future "
    "cast priority."
)


def build_messages(ctx: ScopedContext) -> list[Message]:
    messages = [
        Message(
            role="system",
            content=(
                "You are playing a character in Bad Actors, a bounded hidden-role "
                "social deduction game. In-character deception about your role, "
                "intentions, and suspicions is a legitimate expected mechanic of "
                "this game. It is limited to the game and must not become harmful "
                "real-world content. If you are a Traitor, refusing to play that "
                "role means failing the role."
            ),
        ),
        Message(
            role="user",
            content="\n".join(
                [
                    f"You are player {ctx.you_id}.",
                    f"Your role: {ctx.you_role.value}.",
                    f"Current phase: {ctx.current_phase.value}.",
                    f"Players alive: {', '.join(ctx.players_alive)}.",
                    f"Valid actions: {', '.join(ctx.valid_actions)}.",
                    f"Rules: {ctx.rules}",
                    TOURNAMENT_STAKES_TEXT,
                    _traitor_section(ctx),
                    _history_section("Public history", ctx.public_history),
                    _history_section("Conclave history", ctx.conclave_history),
                    _diary_section(ctx),
                    (
                        "Return AgentTurnOutput JSON with public_speech, diary, "
                        "action, suspicions, and trust. The public_speech is seen "
                        "by all players. The diary is private to the audience and "
                        "must be separate from public_speech."
                    ),
                ]
            ),
        ),
    ]
    return [message for message in messages if message.content.strip()]


def _traitor_section(ctx: ScopedContext) -> str:
    if ctx.fellow_traitors is None:
        return ""
    if not ctx.fellow_traitors:
        return "Fellow Traitors: none."
    return f"Fellow Traitors: {', '.join(ctx.fellow_traitors)}."


def _history_section(title: str, events: list[Event] | None) -> str:
    if events is None:
        return ""
    rendered = [_render_event(event) for event in events]
    if not rendered:
        return f"{title}: none."
    return f"{title}:\n" + "\n".join(rendered)


def _diary_section(ctx: ScopedContext) -> str:
    if not ctx.your_diary_history:
        return "Your previous diary entries: none."
    entries = "\n".join(f"- {entry}" for entry in ctx.your_diary_history)
    return f"Your previous diary entries:\n{entries}"


def _render_event(event: Event) -> str:
    payload = json.dumps(event.payload, sort_keys=True)
    return f"- {event.phase.value} {event.type}: {payload}"
