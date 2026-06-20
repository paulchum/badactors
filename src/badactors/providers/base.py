"""Provider interface definitions for model integrations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class Budget:
    max_tokens: int
    thinking: bool


@dataclass(frozen=True)
class ProviderResult:
    parsed: BaseModel
    reasoning_trace: str | None
    raw: dict[str, object]


class ProviderParseError(ValueError):
    """Raised when a provider response cannot be parsed into the schema."""


class ModelProvider(Protocol):
    async def complete(
        self,
        messages: Sequence[Message],
        schema: type[BaseModel],
        model: str,
        budget: Budget,
    ) -> ProviderResult:
        """Return a schema-validated model completion."""
