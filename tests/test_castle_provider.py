"""The provider boundary: one call each, the right parameters per mode, errors
sorted into the turn's and the run's, capabilities cached, nothing secret kept."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from badactors.castle.prompt import SYSTEM, Message, Part
from badactors.castle.provider import (
    IN_FLIGHT,
    CallRef,
    FixtureProvider,
    HarnessLog,
    InFlightFull,
    Mode,
    ModeBook,
    OpenRouterProvider,
    OperatorError,
    ParameterRejected,
    ProviderCall,
    ReasoningRequired,
    RecordingProvider,
    TransportError,
    load_fixtures,
    load_models,
    models_from,
    parse_reply,
    request_body,
)

SCHEMA = {
    "type": "object",
    "properties": {"decision": {"type": "string"}},
    "required": ["decision"],
    "additionalProperties": False,
}
KEY = "not-a-real-key"


def call(mode: Mode, model: str = "openai/gpt-6.1-sol") -> ProviderCall:
    return ProviderCall(
        ref=CallRef("g", "sol", model, "vote", 1, 4, 0, 1),
        messages=(
            Message("system", (Part(SYSTEM),)),
            Message("user", (Part("RULES", cache=True), Part("NOW"))),
        ),
        schema=SCHEMA,
        mode=mode,
        max_tokens=512,
    )


def provider(
    respond: Any, seen: list[httpx.Request] | None = None
) -> OpenRouterProvider:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        result: httpx.Response = respond(request)
        return result

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return OpenRouterProvider(api_key=KEY, client=client)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


GOOD = {
    "choices": [
        {
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"decision": "x"}'},
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 8, "cost": 0.00042},
}


def test_each_mode_sends_only_the_parameters_it_needs() -> None:
    tools = request_body(call("tools"))
    assert tools["tools"][0]["function"]["parameters"] == SCHEMA
    assert "response_format" not in tools and "tool_choice" not in tools
    assert tools["provider"] == {"require_parameters": True}
    schema = request_body(call("json_schema"))
    assert schema["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in schema
    json_object = request_body(call("json_object"))
    assert json_object["response_format"] == {"type": "json_object"}
    assert (
        json.dumps(SCHEMA, separators=(",", ":"))
        in json_object["messages"][-1]["content"]
    )
    prompt = request_body(call("prompt"))
    assert "response_format" not in prompt and "provider" not in prompt
    assert "JSON Schema" in prompt["messages"][-1]["content"]
    for body in (tools, schema, json_object, prompt):
        assert body["messages"][0] == {"role": "system", "content": SYSTEM}
        assert body["max_tokens"] == 512


def test_reasoning_is_sent_only_when_a_setting_is_chosen() -> None:
    assert "reasoning" not in request_body(call("tools"))
    assert request_body(replace(call("tools"), reasoning="off"))["reasoning"] == {
        "enabled": False
    }
    assert request_body(replace(call("json_schema"), reasoning="low"))["reasoning"] == {
        "effort": "low"
    }


def test_mandatory_reasoning_is_one_failed_http_call_without_a_hidden_retry() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        if sent["reasoning"] == {"enabled": False}:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Reasoning is mandatory for this endpoint "
                        "and cannot be disabled.",
                        "code": 400,
                    }
                },
            )
        return httpx.Response(200, json=GOOD)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond_and_keep(respond, seen))
    )
    single = OpenRouterProvider(api_key=KEY, client=client)
    with pytest.raises(ReasoningRequired):
        run(single.complete(replace(call("tools"), reasoning="off")))
    assert len(seen) == 1
    run(single.complete(replace(call("tools"), reasoning="minimal")))
    reasoning = [json.loads(r.content)["reasoning"] for r in seen]
    assert reasoning == [
        {"enabled": False},
        {"effort": "minimal"},
    ]


def respond_and_keep(respond: Any, seen: list[httpx.Request]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        result: httpx.Response = respond(request)
        return result

    return handle


def test_the_static_prefix_is_sent_first_and_marked_where_caching_needs_it() -> None:
    claude = request_body(call("tools", model="anthropic/claude-opus-5.5"))
    parts = claude["messages"][1]["content"]
    assert parts[0] == {
        "type": "text",
        "text": "RULES",
        "cache_control": {"type": "ephemeral"},
    }
    assert parts[1]["text"] == "NOW" and "cache_control" not in parts[1]
    gpt = request_body(call("tools"))
    assert gpt["messages"][1]["content"].startswith("RULES\n\nNOW")


def test_the_same_request_differs_between_models_only_in_the_model() -> None:
    first = request_body(call("tools", model="openai/gpt-6.1-sol"))
    second = request_body(call("tools", model="x-ai/grok-4.7"))
    first.pop("model")
    second.pop("model")
    assert first == second


def test_one_call_is_one_request_with_no_retries() -> None:
    seen: list[httpx.Request] = []
    flaky = provider(lambda r: httpx.Response(503, json={"error": "busy"}), seen)
    with pytest.raises(TransportError):
        run(flaky.complete(call("tools")))
    assert len(seen) == 1
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"error": {"message": "No auth credentials found"}}, OperatorError),
        (402, {"error": {"message": "Insufficient credits"}}, OperatorError),
        (
            402,
            {
                "error": {
                    "code": 402,
                    "metadata": {"reason": "in_flight_budget_exhausted"},
                }
            },
            InFlightFull,
        ),
        (403, {"error": {"message": "Key disabled"}}, OperatorError),
        (
            400,
            {"error": {"message": "response_format is not supported"}},
            ParameterRejected,
        ),
        (
            404,
            {"error": {"message": "No endpoints found that support tool use."}},
            ParameterRejected,
        ),
        (
            400,
            {"error": {"message": "This endpoint's maximum context length"}},
            TransportError,
        ),
        (429, {"error": {"message": "Rate limited: tools"}}, TransportError),
        (502, {"error": {"message": "Bad gateway"}}, TransportError),
    ],
)
def test_errors_are_sorted_into_the_turns_and_the_runs(
    status: int, body: dict[str, Any], error: type[Exception]
) -> None:
    failing = provider(lambda r: httpx.Response(status, json=body))
    with pytest.raises(error) as raised:
        run(failing.complete(call("tools")))
    assert type(raised.value) is error


def test_a_timeout_is_a_transport_error() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TransportError, match="timeout"):
        run(provider(slow).complete(call("tools")))


def test_an_error_inside_a_200_is_raised_as_what_it_is() -> None:
    body = {"error": {"code": 402, "message": "Insufficient credits"}}
    with pytest.raises(OperatorError):
        parse_reply(body)


def test_a_reply_carries_content_tool_arguments_refusal_and_usage() -> None:
    reply = run(
        provider(lambda r: httpx.Response(200, json=GOOD)).complete(call("tools"))
    )
    assert reply.content == '{"decision": "x"}' and reply.arguments is None
    assert reply.usage.prompt_tokens == 120 and reply.usage.cost == 0.00042
    tool = parse_reply(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "refusal": "  ",
                        "tool_calls": [
                            {"function": {"name": "decide", "arguments": "{}"}}
                        ],
                    },
                }
            ]
        }
    )
    assert tool.arguments == "{}" and tool.refusal is None
    assert tool.usage.cost is None
    refused = parse_reply(
        {"choices": [{"message": {"content": None, "refusal": "I can't help."}}]}
    )
    assert refused.refusal == "I can't help."


LISTING = {
    "data": [
        {
            "id": "a/tools",
            "supported_parameters": ["tools", "structured_outputs", "response_format"],
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
        },
        {
            "id": "b/schema",
            "supported_parameters": ["structured_outputs", "response_format"],
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        },
        {
            "id": "c/json",
            "supported_parameters": ["response_format"],
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "d/plain",
            "supported_parameters": [],
            "pricing": {},
            "top_provider": {"max_completion_tokens": 8_000},
        },
    ]
}


def test_capabilities_pick_the_best_mode_and_read_prices() -> None:
    models = models_from(LISTING)
    assert [
        models[m].best_mode() for m in ("a/tools", "b/schema", "c/json", "d/plain")
    ] == [
        "tools",
        "json_schema",
        "json_object",
        "prompt",
    ]
    assert models["a/tools"].prompt_price == 3e-6
    assert models["a/tools"].worst_case(1000, 100) == pytest.approx(0.0045)
    assert models["d/plain"].max_completion_tokens == 8_000
    assert models["a/tools"].max_completion_tokens is None


def test_capabilities_are_cached_for_their_ttl(tmp_path: Path) -> None:
    gets: list[httpx.Request] = []

    def listing(request: httpx.Request) -> httpx.Response:
        gets.append(request)
        return httpx.Response(200, json=LISTING)

    cache = tmp_path / "models.json"
    clock = [1_000.0]

    async def load() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(listing)) as client:
            return await load_models(cache, client=client, ttl=60, now=lambda: clock[0])

    assert set(run(load())) == {"a/tools", "b/schema", "c/json", "d/plain"}
    clock[0] += 59
    run(load())
    assert len(gets) == 1
    clock[0] += 2
    run(load())
    assert len(gets) == 2
    assert "Authorization" not in gets[0].headers


def test_the_mode_book_moves_a_model_down_once_per_cause() -> None:
    log = HarnessLog()
    book = ModeBook({"m": "tools"}, log, patience=2)
    book.rejected("m", "tools", "tools unsupported")
    assert book.mode("m") == "json_schema"
    book.rejected("m", "tools", "a late reply from the old mode")
    assert book.mode("m") == "json_schema"
    book.answered("m", "json_schema", schema_valid=False)
    book.answered("m", "json_schema", schema_valid=True)
    book.answered("m", "json_schema", schema_valid=False)
    assert book.mode("m") == "json_schema"
    book.answered("m", "json_schema", schema_valid=False)
    assert book.mode("m") == "json_object"
    assert [(e["old"], e["new"]) for e in log.of("mode_downgrade")] == [
        ("tools", "json_schema"),
        ("json_schema", "json_object"),
    ]
    book.modes["m"] = "prompt"
    book.rejected("m", "prompt", "nowhere lower")
    assert book.mode("m") == "prompt"


def test_recordings_keep_bodies_never_keys_and_play_back(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    responses = iter([httpx.Response(200, json=GOOD), httpx.Response(402, json={})])
    recording = RecordingProvider(provider(lambda r: next(responses)), path)
    first = run(recording.complete(call("tools")))
    second = call("tools")
    second = ProviderCall(
        ref=CallRef("g", "sol", second.ref.model, "vote", 1, 4, 1, 1),
        messages=second.messages,
        schema=second.schema,
        mode="tools",
        max_tokens=512,
    )
    with pytest.raises(OperatorError):
        run(recording.complete(second))
    text = path.read_text()
    assert KEY not in text and "Authorization" not in text and "Bearer" not in text
    fixtures = load_fixtures(path)
    assert set(fixtures) == {"sol/0/1", "sol/1/1"}
    replayed = FixtureProvider(fixtures)
    assert run(replayed.complete(call("tools"))).raw == first.raw
    with pytest.raises(OperatorError):
        run(replayed.complete(second))


def test_an_in_flight_402_reads_its_suggested_wait() -> None:
    body = {
        "error": {"metadata": {"reason": IN_FLIGHT, "headers": {"Retry-After": "120"}}}
    }
    failing = provider(lambda r: httpx.Response(402, json=body))
    with pytest.raises(InFlightFull) as raised:
        run(failing.complete(call("tools")))
    assert raised.value.retry_after == 120.0
    assert InFlightFull("busy", status=402, body="busy").retry_after is None
