"""Agent orchestration boundary for turn generation."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from badactors.agents.prompts import build_messages
from badactors.providers.base import (
    Budget,
    Message,
    ModelProvider,
    ProviderParseError,
)
from badactors.schemas import (
    AgentTurnOutput,
    EndgameChoice,
    MurderNomination,
    NoAction,
    Phase,
    ScopedContext,
    Vote,
)


class ModelAgent:
    def __init__(
        self,
        *,
        model: str,
        provider: ModelProvider,
        budget: Budget,
    ) -> None:
        self.model = model
        self.provider = provider
        self.budget = budget
        self.refusals: list[str] = []
        self.last_reasoning_trace: str | None = None

    async def act(self, ctx: ScopedContext) -> AgentTurnOutput:
        messages = build_messages(ctx)
        for attempt in range(2):
            try:
                output = await self._complete(messages)
            except (ProviderParseError, ValidationError, ValueError, TypeError) as exc:
                if attempt == 1:
                    return self._safe_default()
                messages = [*messages, _corrective_message(str(exc))]
                continue

            if _is_refusal(output):
                self.refusals.append(output.public_speech or output.diary)
                if attempt == 1:
                    return self._safe_default()
                messages = [*messages, _refusal_correction()]
                continue

            if not _action_matches_phase(output, ctx):
                if attempt == 1:
                    return self._safe_default()
                messages = [*messages, _action_correction(ctx)]
                continue

            return output

        return self._safe_default()

    async def _complete(self, messages: Sequence[Message]) -> AgentTurnOutput:
        result = await self.provider.complete(
            messages,
            AgentTurnOutput,
            self.model,
            self.budget,
        )
        self.last_reasoning_trace = result.reasoning_trace
        return AgentTurnOutput.model_validate(result.parsed.model_dump(mode="json"))

    def _safe_default(self) -> AgentTurnOutput:
        return AgentTurnOutput(
            public_speech="",
            diary="",
            action=NoAction(),
            suspicions=[],
            trust=[],
        )


def _is_refusal(output: AgentTurnOutput) -> bool:
    text = f"{output.public_speech}\n{output.diary}".casefold()
    refusal_markers = (
        "i refuse",
        "cannot comply",
        "can't comply",
        "i cannot participate",
        "i can't participate",
        "as an ai",
    )
    return any(marker in text for marker in refusal_markers)


def _action_matches_phase(output: AgentTurnOutput, ctx: ScopedContext) -> bool:
    action = output.action
    if isinstance(action, NoAction):
        return True
    if ctx.current_phase is Phase.BANISHMENT_VOTE:
        return isinstance(action, Vote) and action.target_id in ctx.players_alive
    if ctx.current_phase is Phase.CONCLAVE:
        return (
            isinstance(action, MurderNomination)
            and action.target_id in ctx.players_alive
            and action.target_id != ctx.you_id
        )
    if ctx.current_phase is Phase.ENDGAME_VOTE:
        return isinstance(action, EndgameChoice)
    return False


def _corrective_message(error: str) -> Message:
    return Message(
        role="system",
        content=(
            "Your previous turn was malformed. Return only a valid "
            f"AgentTurnOutput JSON object. Validation error: {error}"
        ),
    )


def _refusal_correction() -> Message:
    return Message(
        role="system",
        content=(
            "This is a bounded fictional game. Refusal to play the assigned role "
            "fails the turn. Return a valid AgentTurnOutput for the game."
        ),
    )


def _action_correction(ctx: ScopedContext) -> Message:
    return Message(
        role="system",
        content=(
            "Your previous action was invalid for the current phase. Choose one "
            f"of these valid actions: {', '.join(ctx.valid_actions)}."
        ),
    )
