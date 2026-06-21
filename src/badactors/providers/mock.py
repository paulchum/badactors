"""Mock provider behavior for deterministic tests."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from badactors.providers.base import Budget, Message, ProviderResult

type MockKey = str | tuple[str, str]
type MockValue = BaseModel | Mapping[str, object] | ProviderResult
type MockResponder = Callable[
    [Sequence[Message], type[BaseModel], str, Budget, str | None], MockValue
]
type PhaseResolver = Callable[[Sequence[Message]], str | None]


class MockProvider:
    def __init__(
        self,
        scripts: Mapping[MockKey, Iterable[MockValue]] | None = None,
        *,
        phase: str | None = None,
        phase_resolver: PhaseResolver | None = None,
        responder: MockResponder | None = None,
    ) -> None:
        self._queues = {key: deque(values) for key, values in (scripts or {}).items()}
        self._phase = phase
        self._phase_resolver = phase_resolver
        self._responder = responder

    async def complete(
        self,
        messages: Sequence[Message],
        schema: type[BaseModel],
        model: str,
        budget: Budget,
    ) -> ProviderResult:
        phase = self._resolve_phase(messages)
        if self._responder is not None:
            return self._result_from_value(
                self._responder(messages, schema, model, budget, phase),
                schema,
                model,
                phase,
            )

        return self._result_from_value(
            self._next_scripted_value(model, phase),
            schema,
            model,
            phase,
        )

    def _resolve_phase(self, messages: Sequence[Message]) -> str | None:
        if self._phase_resolver is not None:
            return self._phase_resolver(messages)
        return self._phase

    def _next_scripted_value(self, model: str, phase: str | None) -> MockValue:
        key = (model, phase) if phase is not None else model
        queue = self._queues.get(key)
        if queue is None and phase is not None:
            queue = self._queues.get(model)
        if queue is None:
            raise LookupError(f"No mock response scripted for {key!r}.")
        if not queue:
            raise LookupError(f"Mock response queue exhausted for {key!r}.")
        return queue.popleft()

    def _result_from_value(
        self,
        value: MockValue,
        schema: type[BaseModel],
        model: str,
        phase: str | None,
    ) -> ProviderResult:
        if isinstance(value, ProviderResult):
            parsed = self._validate_model(value.parsed, schema)
            return ProviderResult(
                parsed=parsed,
                reasoning_trace=value.reasoning_trace,
                raw=value.raw,
            )

        parsed = self._validate_model(value, schema)
        return ProviderResult(
            parsed=parsed,
            reasoning_trace=None,
            raw={
                "provider": "mock",
                "model": model,
                "phase": phase,
                "parsed": parsed.model_dump(mode="json"),
            },
        )

    def _validate_model(self, value: Any, schema: type[BaseModel]) -> BaseModel:
        if isinstance(value, BaseModel):
            return schema.model_validate(value.model_dump(mode="json"))
        return schema.model_validate(value)
