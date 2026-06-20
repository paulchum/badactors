"""OpenRouter provider integration boundary."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, cast

import httpx
from pydantic import BaseModel, ValidationError

from badactors.providers.base import (
    Budget,
    Message,
    ProviderParseError,
    ProviderResult,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
JSON_ONLY_INSTRUCTION = (
    "Return only valid JSON matching the requested schema. "
    "No prose, no Markdown, no code fences."
)


class OpenRouterProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        url: str = OPENROUTER_URL,
        structured_outputs: bool = True,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY")
        )
        self._client = client
        self._url = url
        self._structured_outputs = structured_outputs
        self._timeout = timeout

    async def complete(
        self,
        messages: Sequence[Message],
        schema: type[BaseModel],
        model: str,
        budget: Budget,
    ) -> ProviderResult:
        request_messages = self._messages_for_request(messages)
        last_error: ProviderParseError | None = None

        for attempt in range(2):
            raw = await self._post(
                self._request_body(request_messages, schema, model, budget)
            )
            try:
                return ProviderResult(
                    parsed=self._parse_response(raw, schema),
                    reasoning_trace=self._reasoning_trace(raw),
                    raw=raw,
                )
            except ProviderParseError as exc:
                last_error = exc
                if attempt == 1:
                    raise ProviderParseError(
                        "Provider response did not match the requested schema "
                        "after retry."
                    ) from exc
                request_messages.append(self._corrective_message(exc))

        raise ProviderParseError(
            "Provider response did not match the requested schema."
        ) from last_error

    def _messages_for_request(self, messages: Sequence[Message]) -> list[Message]:
        request_messages = list(messages)
        if not self._structured_outputs:
            request_messages = [
                Message(role="system", content=JSON_ONLY_INSTRUCTION),
                *request_messages,
            ]
        return request_messages

    def _request_body(
        self,
        messages: Sequence[Message],
        schema: type[BaseModel],
        model: str,
        budget: Budget,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "max_tokens": budget.max_tokens,
        }

        if self._structured_outputs:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            }

        if budget.thinking:
            body["reasoning"] = {"enabled": True}

        return body

    async def _post(self, body: dict[str, object]) -> dict[str, object]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        if self._client is not None:
            response = await self._client.post(self._url, json=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=body, headers=headers)

        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict):
            raise ProviderParseError("Provider response was not a JSON object.")
        return cast(dict[str, object], raw)

    def _parse_response(
        self, raw: Mapping[str, object], schema: type[BaseModel]
    ) -> BaseModel:
        try:
            payload = self._message_payload(raw)
            return schema.model_validate(payload)
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise ProviderParseError(str(exc)) from exc

    def _message_payload(self, raw: Mapping[str, object]) -> object:
        message = self._first_message(raw)
        parsed = message.get("parsed")
        if parsed is not None:
            return parsed

        content = message.get("content")
        if isinstance(content, str):
            return json.loads(content)
        if isinstance(content, dict):
            return content

        raise ProviderParseError("Provider message had no JSON content.")

    def _first_message(self, raw: Mapping[str, object]) -> Mapping[str, Any]:
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderParseError("Provider response had no choices.")

        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderParseError("Provider choice was not an object.")

        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderParseError("Provider choice had no message object.")

        return cast(Mapping[str, Any], message)

    def _reasoning_trace(self, raw: Mapping[str, object]) -> str | None:
        try:
            message = self._first_message(raw)
        except ProviderParseError:
            return None

        for key in ("reasoning", "reasoning_trace"):
            trace = message.get(key)
            if isinstance(trace, str):
                return trace

        details = message.get("reasoning_details")
        if details is None:
            return None
        return json.dumps(details, sort_keys=True)

    def _corrective_message(self, error: ProviderParseError) -> Message:
        return Message(
            role="system",
            content=(
                "The previous response did not validate against the requested "
                f"schema. Return corrected JSON only. Validation error: {error}"
            ),
        )
