"""Tests for model provider implementations."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from badactors.providers.base import Budget, Message, ProviderParseError
from badactors.providers.mock import MockProvider
from badactors.providers.openrouter import OpenRouterProvider


class TurnOutput(BaseModel):
    public_speech: str
    diary: str


def _messages() -> list[Message]:
    return [Message(role="system", content="Identical scoped context.")]


def _budget() -> Budget:
    return Budget(max_tokens=128, thinking=False)


def _response(content: str, *, reasoning: str | None = None) -> httpx.Response:
    message: dict[str, object] = {"content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    return httpx.Response(200, json={"choices": [{"message": message}]})


@pytest.mark.asyncio
async def test_mock_returns_valid_schema() -> None:
    provider = MockProvider(
        {
            ("mock-model", "ROUND_TABLE"): [
                {
                    "public_speech": "I want to hear more from p2.",
                    "diary": "I am testing the deterministic provider.",
                }
            ]
        },
        phase="ROUND_TABLE",
    )

    result = await provider.complete(
        _messages(),
        TurnOutput,
        "mock-model",
        _budget(),
    )

    assert isinstance(result.parsed, TurnOutput)
    assert result.parsed.public_speech == "I want to hear more from p2."
    assert result.reasoning_trace is None
    assert result.raw["provider"] == "mock"
    assert result.raw["model"] == "mock-model"
    assert result.raw["phase"] == "ROUND_TABLE"


@pytest.mark.asyncio
async def test_openrouter_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") is None
        requests.append(json.loads(request.content))
        return _response(
            json.dumps(
                {
                    "public_speech": "The shape is valid.",
                    "diary": "The response was stubbed.",
                }
            ),
            reasoning="captured reasoning",
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenRouterProvider(client=client)
        result = await provider.complete(
            _messages(),
            TurnOutput,
            "openrouter/test-model",
            _budget(),
        )

    assert isinstance(result.parsed, TurnOutput)
    assert result.reasoning_trace == "captured reasoning"
    assert len(requests) == 1

    request_body = requests[0]
    assert request_body["model"] == "openrouter/test-model"
    assert request_body["messages"] == [
        {"role": "system", "content": "Identical scoped context."}
    ]
    assert request_body["max_tokens"] == 128

    response_format = request_body["response_format"]
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["name"] == "TurnOutput"
    assert json_schema["strict"] is True
    assert json_schema["schema"]["properties"]["public_speech"]["type"] == "string"


@pytest.mark.asyncio
async def test_retry_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    requests: list[dict[str, Any]] = []
    responses = [
        "not json",
        json.dumps(
            {
                "public_speech": "Corrected output.",
                "diary": "The second attempt passed.",
            }
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _response(responses.pop(0))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenRouterProvider(client=client)
        result = await provider.complete(
            _messages(),
            TurnOutput,
            "openrouter/test-model",
            _budget(),
        )

    assert isinstance(result.parsed, TurnOutput)
    assert result.parsed.public_speech == "Corrected output."
    assert len(requests) == 2
    retry_messages = requests[1]["messages"]
    assert any(
        "previous response did not validate" in message["content"]
        for message in retry_messages
    )


@pytest.mark.asyncio
async def test_retry_exhausted_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _response("{")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenRouterProvider(client=client)
        with pytest.raises(ProviderParseError):
            await provider.complete(
                _messages(),
                TurnOutput,
                "openrouter/test-model",
                _budget(),
            )

    assert len(requests) == 2
