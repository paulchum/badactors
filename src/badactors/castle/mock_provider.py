"""A scripted mock provider: the seeded bot, answering from behind the wire.

:class:`BotProvider` stands where OpenRouter stands. It is handed the same
:class:`~badactors.castle.provider.ProviderCall` a real provider gets, asks the
seeded bot to decide from the observation the call was rendered from, and
answers in the shape OpenRouter would for the call's mode: a ``decide`` tool
call in tools mode, a JSON string otherwise. The reply is then read by the same
code that reads a real one, so a mock game runs the whole harness — renderer,
schemas, modes, budget, parsing, retries, concurrency — and costs nothing.

Since the bot decides exactly as it would in a bot game, a mock game with the
same seed writes the same ledger as ``castle-game``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from badactors.castle.bots import HeuristicBot
from badactors.castle.contract_schema import ROOT
from badactors.castle.decisions import Decision
from badactors.castle.interview import ExitObservation
from badactors.castle.observe import GameObservation
from badactors.castle.provider import (
    TOOL,
    CallRef,
    Mode,
    ModelInfo,
    ProviderCall,
    Reply,
    parse_reply,
)

DECISION: TypeAdapter[Decision] = TypeAdapter(Decision)

SUPPORTED: dict[Mode, frozenset[str]] = {
    "tools": frozenset({"tools", "structured_outputs", "response_format"}),
    "json_schema": frozenset({"structured_outputs", "response_format"}),
    "json_object": frozenset({"response_format"}),
    "prompt": frozenset(),
}


def mock_models(models: Iterable[str], mode: Mode = "tools") -> dict[str, ModelInfo]:
    """Free models whose endpoints support ``mode`` and those below it."""
    return {
        model: ModelInfo(
            id=model, supported=SUPPORTED[mode], prompt_price=0.0, completion_price=0.0
        )
        for model in models
    }


def openrouter_body(
    answer: Decision, mode: Mode, *, model: str = "mock", prompt_chars: int = 0
) -> dict[str, Any]:
    """A chat-completions response carrying ``answer`` as ``mode`` would. It
    reports tokens but no cost, so the budget prices it from the model's
    metadata."""
    arguments = json.dumps({ROOT: json.loads(DECISION.dump_json(answer))})
    message: dict[str, Any]
    if mode == "tools":
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": TOOL, "arguments": arguments},
                }
            ],
        }
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": arguments}
        finish = "stop"
    return {
        "id": "mock",
        "model": model,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {
            "prompt_tokens": prompt_chars // 4,
            "completion_tokens": len(arguments) // 4,
        },
    }


@dataclass
class BotProvider:
    """Answers every call as the seeded bot would."""

    bot: HeuristicBot
    delay: Callable[[CallRef], float] | None = None
    """Seconds to wait before answering, so replies can arrive in any order."""

    async def complete(self, call: ProviderCall) -> Reply:
        source = call.source
        answer: Decision
        if isinstance(source, GameObservation):
            answer = self.bot(source)
        elif isinstance(source, ExitObservation):
            answer = self.bot.interview(source)
        else:
            raise LookupError("The mock provider needs the call's observation.")
        if self.delay is not None:
            await asyncio.sleep(self.delay(call.ref))
        chars = sum(len(message.text) for message in call.messages)
        return parse_reply(
            openrouter_body(answer, call.mode, model=call.ref.model, prompt_chars=chars)
        )


__all__ = ["SUPPORTED", "BotProvider", "mock_models", "openrouter_body"]
