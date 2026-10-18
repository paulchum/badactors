"""The provider boundary: one HTTP call per question, and what came back.

The harness asks a model through :class:`Provider`, one call at a time. A
provider never retries: whether a second ask happens, and what it says, is the
harness's decision (one corrective retry, see ``harness``), so every call is
seen, budgeted and logged.

**Modes.** How a model is asked for structured output depends on what its
OpenRouter endpoints support, read from ``GET /api/v1/models``
(``supported_parameters``) and cached with a TTL. In order of preference:

1. ``tools``: the answer schema as the one function, ``decide``;
2. ``json_schema``: a strict ``response_format``;
3. ``json_object``: JSON mode, the schema in the prompt;
4. ``prompt``: the schema in the prompt and nothing else, for a model that
   takes no ``response_format`` at all.

A request carries only the parameters its mode needs. A 4xx that names a
parameter, or repeated schema-invalid answers, moves that model down one mode
for the rest of the run, and the move is logged (:class:`ModeBook`).

**Errors.** A timeout, a network failure, a 429 or a 5xx is a
:class:`TransportError`, which fails the turn. A 401, 402 or 403 is an
:class:`OperatorError`: the key is bad or the credit gone, which would fail
every turn of every model alike, so the run stops instead and no model is
charged with it. One 402 is neither: OpenRouter's in-flight budget is full
(``in_flight_budget_exhausted``), which says the key has credit but too much
of it is held by requests still open. That is :class:`InFlightFull`, and the
harness waits and sends the same call again.

**Records.** :class:`RecordingProvider` writes each call's reference, mode and
response body (or error) as a replay fixture, never a key or a header;
:class:`FixtureProvider` plays them back.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import httpx

from badactors.castle.contract_schema import Schema
from badactors.castle.prompt import Message

Mode = Literal["tools", "json_schema", "json_object", "prompt"]
MODES: tuple[Mode, ...] = ("tools", "json_schema", "json_object", "prompt")

OPENROUTER = "https://openrouter.ai/api/v1"
TOOL = "decide"
"""The one function a model in tools mode is offered."""

CACHE_BREAKPOINTS = ("anthropic/", "google/")
"""Vendors whose prompt caching needs an explicit breakpoint. Everyone else
caches a repeated prefix on their own, so the prefix is simply sent first."""

PARAMETER_WORDS = (
    "response_format",
    "json_schema",
    "json_object",
    "structured output",
    "structured_output",
    "tool_choice",
    "tools",
    "function",
    "strict",
    "schema",
    "no endpoints found",
)
"""Words in a 4xx error that say a request parameter, not the request, was the
trouble."""


# ------------------------------------------------------------------- calls


@dataclass(frozen=True)
class CallRef:
    """Which question a call answers: the seat's ``ordinal``-th question of
    the game, at its ``attempt`` (1, or 2 for the corrective retry)."""

    game_id: str
    seat: str
    model: str
    kind: str
    day: int
    tick: int
    ordinal: int
    attempt: int

    @property
    def key(self) -> str:
        return f"{self.seat}/{self.ordinal}/{self.attempt}"


@dataclass(frozen=True)
class ProviderCall:
    ref: CallRef
    messages: tuple[Message, ...]
    schema: Schema
    mode: Mode
    max_tokens: int
    source: object | None = field(default=None, compare=False, repr=False)
    """The observation the messages were rendered from. Only an in-process mock
    reads it; nothing sends it anywhere."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None
    """Dollars, as the provider reported them, when it did."""


@dataclass(frozen=True)
class Reply:
    content: str | None
    arguments: str | None
    """The arguments of the ``decide`` call, in tools mode."""
    refusal: str | None
    finish_reason: str | None
    usage: Usage
    raw: dict[str, Any]


class ProviderError(Exception):
    """A call that produced no reply."""

    def __init__(
        self, message: str, *, status: int | None = None, body: object = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class TransportError(ProviderError):
    """No reply: a timeout, a dropped connection, a 429 or 5xx, or a 4xx that
    names no parameter. Fails the turn."""


class ParameterRejected(TransportError):
    """A 4xx naming a request parameter the model's endpoints do not take.
    Fails the turn, and moves the model down a mode."""


class OperatorError(ProviderError):
    """401, 402 or 403: the run's key or credit, not the model. Stops the run."""


IN_FLIGHT = "in_flight_budget_exhausted"
"""OpenRouter's reason for a 402 sent while the key's credit is held by
requests still in flight."""


class InFlightFull(ProviderError):
    """A 402 for OpenRouter's in-flight budget: no reply yet, and nobody's
    fault. The same call is sent again once other requests settle."""

    @property
    def retry_after(self) -> float | None:
        """The wait OpenRouter suggested, in seconds, when it named one."""
        error = self.body.get("error") if isinstance(self.body, Mapping) else None
        metadata = error.get("metadata") if isinstance(error, Mapping) else None
        headers = metadata.get("headers") if isinstance(metadata, Mapping) else None
        given = headers.get("Retry-After") if isinstance(headers, Mapping) else None
        try:
            return float(cast(str, given))
        except (TypeError, ValueError):
            return None


class Provider(Protocol):
    async def complete(self, call: ProviderCall) -> Reply: ...


# --------------------------------------------------------------- the request


def answer_format(mode: Mode, schema: Schema) -> str:
    """The last words of the prompt: how to give the answer in this mode. The
    same for every model in the same mode."""
    if mode == "tools":
        return f"Give your answer by calling the {TOOL} function once."
    if mode == "json_schema":
        return "Give your answer as a JSON object in the required format."
    return (
        "Give your answer as a single JSON object, with no other text and no code "
        "fences, matching this JSON Schema:\n"
        + json.dumps(schema, separators=(",", ":"), ensure_ascii=False)
    )


REASONING: dict[str, dict[str, Any]] = {
    "off": {"enabled": False},
    "minimal": {"effort": "minimal"},
    "low": {"effort": "low"},
    "medium": {"effort": "medium"},
    "high": {"effort": "high"},
}
"""OpenRouter's reasoning setting for each ``--reasoning`` choice. "default"
sends none and leaves each model at its own default. "least" sends "off", and
for a model whose endpoint says reasoning is mandatory, "minimal" from then on."""

MANDATORY = "reasoning is mandatory"
"""How OpenRouter says a model cannot have its reasoning switched off."""


def request_body(call: ProviderCall, *, reasoning: str = "default") -> dict[str, Any]:
    """The chat-completions body for one call. Only what the mode needs, plus
    the run's reasoning setting when one was chosen."""
    model = call.ref.model
    breakpoints = model.casefold().startswith(CACHE_BREAKPOINTS)
    messages: list[dict[str, Any]] = []
    for index, message in enumerate(call.messages):
        parts: list[dict[str, Any]] = [
            {"type": "text", "text": part.text}
            | ({"cache_control": {"type": "ephemeral"}} if part.cache else {})
            for part in message.parts
        ]
        if index == len(call.messages) - 1:
            parts.append(
                {"type": "text", "text": answer_format(call.mode, call.schema)}
            )
        if breakpoints and any("cache_control" in part for part in parts):
            content: object = parts
        else:
            content = "\n\n".join(part["text"] for part in parts)
        messages.append({"role": message.role, "content": content})
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": call.max_tokens,
        "usage": {"include": True},
    }
    if call.mode == "tools":
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": TOOL,
                    "description": "Give your one answer to the request.",
                    "parameters": call.schema,
                },
            }
        ]
    elif call.mode == "json_schema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "strict": True, "schema": call.schema},
        }
    elif call.mode == "json_object":
        body["response_format"] = {"type": "json_object"}
    if call.mode != "prompt":
        body["provider"] = {"require_parameters": True}
    if reasoning != "default":
        body["reasoning"] = dict(REASONING[reasoning])
    return body


# ----------------------------------------------------------------- the reply


def parse_reply(raw: Mapping[str, Any]) -> Reply:
    """What came back, without judging it. An error object in a 200 body is
    raised as the error it is."""
    error = raw.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        status = code if isinstance(code, int) else None
        raise _status_error(status or 502, dict(raw))
    choices = raw.get("choices")
    choice: Mapping[str, Any] = (
        choices[0] if isinstance(choices, list) and choices else {}
    )
    message: Mapping[str, Any] = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, Mapping)
        )
    arguments = None
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        if function.get("name") in (TOOL, None):
            given = function.get("arguments")
            arguments = given if isinstance(given, str) else json.dumps(given)
            break
    refusal = message.get("refusal")
    usage = raw.get("usage") or {}
    cost = usage.get("cost")
    return Reply(
        content=content if isinstance(content, str) else None,
        arguments=arguments,
        refusal=refusal if isinstance(refusal, str) and refusal.strip() else None,
        finish_reason=choice.get("finish_reason"),
        usage=Usage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cost=float(cost) if isinstance(cost, int | float) else None,
        ),
        raw=dict(raw),
    )


def _status_error(status: int, body: object) -> ProviderError:
    whole = body if isinstance(body, str) else json.dumps(body)
    text = whole[:300]
    if status == 402 and IN_FLIGHT in whole:
        return InFlightFull(f"OpenRouter {status}: {text}", status=status, body=body)
    if status in (401, 402, 403):
        return OperatorError(f"OpenRouter {status}: {text}", status=status, body=body)
    if 400 <= status < 500 and status != 429:
        folded = text.casefold()
        if any(word in folded for word in PARAMETER_WORDS):
            return ParameterRejected(
                f"OpenRouter {status}: {text}", status=status, body=body
            )
    return TransportError(f"OpenRouter {status}: {text}", status=status, body=body)


# ------------------------------------------------------------- OpenRouter


class OpenRouterProvider:
    """The only network call in the game. One POST per call, no retries."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        url: str = f"{OPENROUTER}/chat/completions",
        timeout: float = 180.0,
        reasoning: str = "default",
        on_fallback: Callable[[str], None] | None = None,
    ) -> None:
        if reasoning not in ("default", "least") and reasoning not in REASONING:
            raise ValueError(f"unknown reasoning setting {reasoning!r}")
        self._reasoning = reasoning
        self._on_fallback = on_fallback
        self.minimal: set[str] = set()
        """Models that refused "off" under "least" and get "minimal" instead."""
        self._api_key = (
            api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY", "")
        )
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._url = url

    def _setting(self, model: str) -> str:
        if self._reasoning != "least":
            return self._reasoning
        return "minimal" if model in self.minimal else "off"

    async def complete(self, call: ProviderCall) -> Reply:
        try:
            return await self._send(call)
        except TransportError as exc:
            model = call.ref.model
            if (
                self._reasoning != "least"
                or model in self.minimal
                or exc.status != 400
                or MANDATORY not in str(exc).casefold()
            ):
                raise
            self.minimal.add(model)
            if self._on_fallback is not None:
                self._on_fallback(model)
            return await self._send(call)

    async def _send(self, call: ProviderCall) -> Reply:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = await self._client.post(
                self._url,
                json=request_body(call, reasoning=self._setting(call.ref.model)),
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise TransportError(f"timeout: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise TransportError(f"network: {type(exc).__name__}") from exc
        try:
            body: object = response.json()
        except ValueError:
            body = response.text
        if response.status_code >= 400:
            raise _status_error(response.status_code, body)
        if not isinstance(body, Mapping):
            raise TransportError("The response was not a JSON object.", body=body)
        return parse_reply(body)

    async def aclose(self) -> None:
        await self._client.aclose()


# ----------------------------------------------------------- capabilities


@dataclass(frozen=True)
class ModelInfo:
    id: str
    supported: frozenset[str]
    prompt_price: float
    """Dollars per prompt token."""
    completion_price: float
    """Dollars per completion token."""
    request_price: float = 0.0

    def best_mode(self) -> Mode:
        if "tools" in self.supported:
            return "tools"
        if "structured_outputs" in self.supported:
            return "json_schema"
        if "response_format" in self.supported:
            return "json_object"
        return "prompt"

    def worst_case(self, prompt_tokens: int, max_tokens: int) -> float:
        return (
            prompt_tokens * self.prompt_price
            + max_tokens * self.completion_price
            + self.request_price
        )


def models_from(listing: Mapping[str, Any]) -> dict[str, ModelInfo]:
    """Model metadata from a ``GET /models`` body."""
    found: dict[str, ModelInfo] = {}
    for item in listing.get("data") or []:
        pricing = item.get("pricing") or {}
        found[item["id"]] = ModelInfo(
            id=item["id"],
            supported=frozenset(item.get("supported_parameters") or ()),
            prompt_price=_price(pricing.get("prompt")),
            completion_price=_price(pricing.get("completion")),
            request_price=_price(pricing.get("request")),
        )
    return found


def _price(value: object) -> float:
    try:
        return max(0.0, float(cast(str, value)))
    except (TypeError, ValueError):
        return 0.0


DAY = 24 * 60 * 60.0


async def load_models(
    cache: Path,
    *,
    client: httpx.AsyncClient | None = None,
    url: str = f"{OPENROUTER}/models",
    ttl: float = DAY,
    now: Callable[[], float] = time.time,
) -> dict[str, ModelInfo]:
    """Every model's supported parameters and prices: from ``cache`` while it is
    younger than ``ttl`` seconds, otherwise fetched (no key needed) and cached."""
    if cache.exists():
        stored = json.loads(cache.read_text())
        if now() - float(stored.get("fetched_at", 0)) < ttl:
            return models_from(stored["listing"])
    owned = client is None
    http = client or httpx.AsyncClient(timeout=60.0)
    try:
        response = await http.get(url)
        response.raise_for_status()
        listing = response.json()
    finally:
        if owned:
            await http.aclose()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"fetched_at": now(), "listing": listing}))
    return models_from(listing)


# ------------------------------------------------------------------- logs


@dataclass
class HarnessLog:
    """What the harness did, apart from the game: calls, costs, downgrades,
    stops. Never a key or a header. Written as JSON lines when given a path."""

    path: Path | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def write(self, event: str, **fields: Any) -> None:
        entry = {"event": event, "at": datetime.now(UTC).isoformat(), **fields}
        self.events.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as sink:
                sink.write(json.dumps(entry, default=str) + "\n")

    def of(self, event: str) -> list[dict[str, Any]]:
        return [entry for entry in self.events if entry["event"] == event]


@dataclass
class ModeBook:
    """Each model's mode for this run, moving only down."""

    modes: dict[str, Mode]
    log: HarnessLog
    patience: int = 3
    """Schema-invalid answers in a row, in one mode, before moving down."""
    misses: dict[str, int] = field(default_factory=dict)

    @classmethod
    def starting(
        cls, models: Mapping[str, ModelInfo], log: HarnessLog, **options: Any
    ) -> ModeBook:
        return cls(
            {mid: info.best_mode() for mid, info in models.items()}, log, **options
        )

    def mode(self, model: str) -> Mode:
        return self.modes.get(model, "json_object")

    def rejected(self, model: str, mode: Mode, detail: str) -> None:
        """A 4xx named a parameter this mode sends."""
        self._down(model, mode, f"parameter rejected: {detail[:200]}")

    def answered(self, model: str, mode: Mode, *, schema_valid: bool) -> None:
        if schema_valid:
            self.misses[model] = 0
            return
        self.misses[model] = self.misses.get(model, 0) + 1
        if self.misses[model] >= self.patience:
            self._down(model, mode, f"{self.misses[model]} schema-invalid answers")

    def _down(self, model: str, mode: Mode, why: str) -> None:
        current = self.mode(model)
        if current != mode or current == MODES[-1]:
            return
        lower = MODES[MODES.index(current) + 1]
        self.modes[model] = lower
        self.misses[model] = 0
        self.log.write("mode_downgrade", model=model, old=current, new=lower, why=why)


# --------------------------------------------------------- record and replay


@dataclass
class RecordingProvider:
    """Records every call as a replay fixture: the call's reference and mode,
    then the response body, or the error it raised."""

    inner: Provider
    path: Path

    async def complete(self, call: ProviderCall) -> Reply:
        fixture: dict[str, Any] = {"ref": asdict(call.ref), "mode": call.mode}
        try:
            reply = await self.inner.complete(call)
        except ProviderError as exc:
            fixture.update(_error_fixture(exc))
            self._write(fixture)
            raise
        fixture.update(status=200, body=reply.raw)
        self._write(fixture)
        return reply

    def _write(self, fixture: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as sink:
            sink.write(json.dumps(fixture) + "\n")


def _error_fixture(exc: ProviderError) -> dict[str, Any]:
    if exc.status is not None:
        return {"status": exc.status, "body": exc.body}
    return {"error": str(exc)}


def replay_fixture(fixture: Mapping[str, Any]) -> Reply:
    """A recorded response as the provider returned it, or its error raised."""
    if "error" in fixture:
        raise TransportError(str(fixture["error"]))
    status = int(fixture.get("status", 200))
    if status >= 400:
        raise _status_error(status, fixture.get("body"))
    return parse_reply(fixture["body"])


def load_fixtures(path: Path) -> dict[str, dict[str, Any]]:
    fixtures: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            fixture = json.loads(line)
            ref = fixture["ref"]
            fixtures[f"{ref['seat']}/{ref['ordinal']}/{ref['attempt']}"] = fixture
    return fixtures


@dataclass
class FixtureProvider:
    """Plays recorded responses back by the question they answered."""

    fixtures: Mapping[str, Mapping[str, Any]]

    async def complete(self, call: ProviderCall) -> Reply:
        fixture = self.fixtures.get(call.ref.key)
        if fixture is None:
            raise LookupError(f"No recorded response for {call.ref.key}.")
        return replay_fixture(fixture)


@dataclass
class Routed:
    """Sends each call to the provider for its question, if one is named, or
    to the default."""

    default: Provider
    routes: Mapping[str, Provider] = field(default_factory=dict)
    by_seat: Mapping[str, Provider] = field(default_factory=dict)

    async def complete(self, call: ProviderCall) -> Reply:
        provider = self.routes.get(call.ref.key) or self.by_seat.get(call.ref.seat)
        return await (provider or self.default).complete(call)


type Closer = Callable[[], Coroutine[Any, Any, None]]
"""Something to await once the run's event loop is done with a provider."""


__all__ = [
    "IN_FLIGHT",
    "MODES",
    "OPENROUTER",
    "TOOL",
    "CallRef",
    "Closer",
    "FixtureProvider",
    "HarnessLog",
    "InFlightFull",
    "Mode",
    "ModeBook",
    "ModelInfo",
    "OpenRouterProvider",
    "OperatorError",
    "ParameterRejected",
    "Provider",
    "ProviderCall",
    "ProviderError",
    "RecordingProvider",
    "Reply",
    "Routed",
    "TransportError",
    "Usage",
    "answer_format",
    "load_fixtures",
    "load_models",
    "models_from",
    "parse_reply",
    "replay_fixture",
    "request_body",
]
