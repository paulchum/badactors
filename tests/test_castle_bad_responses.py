"""Recorded bad provider responses, each driven through a real game.

Every seat but one is played by the seeded bot behind the mock provider. Sol's
first question goes over HTTP to the real OpenRouter provider, whose transport
serves one recorded response (``fixtures/openrouter``, in the format
``RecordingProvider`` writes). Each bad response must fail the turn with the
right reason class and log no decision as Sol's choice — never a pass, never a
silence — except a 402, which is the run's problem and aborts it, and a 402 for
OpenRouter's in-flight budget, which is nobody's: the call is sent again after
a wait. The game's verify bundle keeps each bad call's reason, and each wait,
in its hashed receipt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from badactors.castle.bots import HeuristicBot
from badactors.castle.budget import Limits
from badactors.castle.harness import LiveGame, play_live
from badactors.castle.mock_provider import BotProvider, mock_models
from badactors.castle.play import replay
from badactors.castle.provider import (
    CallRef,
    HarnessLog,
    OpenRouterProvider,
    Provider,
    ProviderCall,
    Reply,
    Routed,
    replay_fixture,
)
from badactors.castle.rules import GameConfig
from badactors.castle.verify import HashingProvider, make_bundle, parts, verify
from castle_scenes import CAST, CASTLE

FIXTURES = Path(__file__).parent / "fixtures" / "openrouter"
SEED = 7
SEAT = "sol"
MODEL = CAST.models()[SEAT]


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return loaded


def served(recorded: dict[str, Any], sent: list[dict[str, Any]]) -> OpenRouterProvider:
    """The real provider, its transport serving one recorded response."""

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if "error" in recorded:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(recorded["status"], json=recorded["body"])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return OpenRouterProvider(api_key="sk-test-not-a-key", client=client)


def play(name: str, *, first_mode: str = "tools") -> tuple[LiveGame, list[Any]]:
    sent: list[dict[str, Any]] = []
    bad = served(fixture(name), sent)
    models = mock_models(CAST.models().values())
    models.update(mock_models([MODEL], first_mode))  # type: ignore[arg-type]
    log = HarnessLog()
    routed = Routed(
        default=BotProvider(HeuristicBot(seed=SEED)),
        routes={f"{SEAT}/0/1": bad, f"{SEAT}/0/2": bad},
    )
    live = play_live(
        CASTLE,
        CAST,
        seed=SEED,
        config=GameConfig.shipped(max_days=2),
        provider=HashingProvider(routed, log, "openrouter"),
        models=models,
        limits=Limits(10_000, 10**9, 100.0),
        harness=log,
        closers=[bad.aclose],
    )
    return live, sent


def first_question(live: LiveGame) -> list[Any]:
    """Everything logged for Sol's first question: one entry per attempt."""
    logged = [entry for entry in live.record.decisions if entry.player_id == SEAT]
    first = logged[:1]
    if first and first[0].refused:
        first = logged[:2]
    return first


CASES = [
    ("json_schema_400", "transport", 1),
    ("empty", "empty", 2),
    ("malformed", "unparseable", 2),
    ("refusal", "refusal", 1),
    ("timeout", "transport", 1),
    ("content_filter", "content_filter", 1),
]


@pytest.mark.parametrize(("name", "reason", "calls"), CASES)
def test_a_bad_response_fails_the_turn_and_logs_no_decision(
    name: str, reason: str, calls: int
) -> None:
    first_mode = "json_schema" if name == "json_schema_400" else "tools"
    live, sent = play(name, first_mode=first_mode)
    stored = live.record
    assert stored.outcome == "finished"
    assert len(sent) == calls
    attempts = first_question(live)
    assert len(attempts) == calls
    assert all(entry.decision is None for entry in attempts)
    assert [entry.reason for entry in attempts] == [reason] * calls
    assert all(entry.refused for entry in attempts) == (calls == 2)
    assert all(entry.agent == f"model:{MODEL}" for entry in attempts)
    failed = [
        entry
        for entry in stored.ledger
        if entry.type == "turn_failed" and entry.actor_id == SEAT
    ]
    assert failed and failed[0].payload["reason"] == reason
    assert failed[0].payload["request"] == attempts[0].kind
    assert _dumped(replay(stored).world.ledger) == _dumped(stored.ledger)


@pytest.mark.parametrize(("name", "reason", "calls"), CASES)
def test_the_bundle_keeps_each_bad_calls_reason_and_verifies(
    name: str, reason: str, calls: int
) -> None:
    first_mode = "json_schema" if name == "json_schema_400" else "tools"
    live, _ = play(name, first_mode=first_mode)
    bundle = make_bundle(live.record, live.harness.events)
    assert verify(bundle) == []
    first = [call for call in bundle.exchanges if call.seat == SEAT][:calls]
    assert [(call.model, call.attempt) for call in first] == [
        (MODEL, attempt) for attempt in range(1, calls + 1)
    ]
    assert [call.failure for call in first] == [reason] * calls
    others = [call for call in bundle.exchanges if call not in first]
    assert others and all(call.failure is None for call in others)


def test_a_400_on_json_schema_moves_the_model_to_json_object_for_the_run() -> None:
    live, sent = play("json_schema_400", first_mode="json_schema")
    assert sent[0]["response_format"]["type"] == "json_schema"
    assert live.harness.of("mode_downgrade") == [
        {
            **live.harness.of("mode_downgrade")[0],
            "model": MODEL,
            "old": "json_schema",
            "new": "json_object",
        }
    ]
    later = [
        call["mode"] for call in live.harness.of("call") if call["ref"]["seat"] == SEAT
    ]
    assert later and set(later) == {"json_object"}


def test_a_retry_names_the_error_and_restates_the_legal_answers() -> None:
    live, sent = play("malformed")
    retry = sent[1]["messages"][-1]["content"]
    assert "could not be used" in retry
    assert "not a JSON object" in retry
    assert "- plan: up to five steps" in retry
    refused = [
        entry
        for entry in live.record.ledger
        if entry.type == "refused" and entry.actor_id == SEAT
    ]
    assert [w.player_id for w in refused[0].witnesses] == [SEAT]


def test_a_402_aborts_the_run_and_charges_no_model() -> None:
    live, sent = play("payment_402")
    stored = live.record
    assert len(sent) == 1
    assert stored.outcome == "aborted:operator" and not stored.rated
    assert "402" in (live.referee.stopped_by or "")
    assert not [e for e in stored.ledger if e.type == "turn_failed"]
    assert not [entry for entry in stored.decisions if entry.player_id == SEAT]
    assert stored.winner is None
    assert _dumped(replay(stored).world.ledger) == _dumped(stored.ledger)
    bundle = make_bundle(stored, live.harness.events)
    assert verify(bundle) == []
    (paid,) = [call for call in bundle.exchanges if call.status == 402]
    assert paid.seat == SEAT and paid.failure is None


class Busy:
    """OpenRouter's recorded in-flight 402 for Sol's first question, ``times``
    times, then the bot behind the mock provider answers it."""

    def __init__(self, inner: Provider, times: int) -> None:
        self.inner = inner
        self.times = times
        self.sent: list[CallRef] = []

    async def complete(self, call: ProviderCall) -> Reply:
        if call.ref.key == f"{SEAT}/0/1":
            self.sent.append(call.ref)
            if len(self.sent) <= self.times:
                return replay_fixture(fixture("in_flight_402"))
        return await self.inner.complete(call)


def busy_game(times: int) -> tuple[LiveGame, Busy, list[float]]:
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    log = HarnessLog()
    busy = Busy(BotProvider(HeuristicBot(seed=SEED)), times)
    live = play_live(
        CASTLE,
        CAST,
        seed=SEED,
        config=GameConfig.shipped(max_days=2),
        provider=HashingProvider(busy, log, "openrouter"),
        models=mock_models(CAST.models().values()),
        limits=Limits(10_000, 10**9, 100.0),
        harness=log,
        sleep=sleep,
    )
    return live, busy, pauses


def test_an_in_flight_402_waits_and_sends_the_same_call_again() -> None:
    live, busy, pauses = busy_game(times=2)
    stored = live.record
    assert stored.outcome == "finished"
    assert len(busy.sent) == 3 and len(set(busy.sent)) == 1
    assert pauses == [2.0, 4.0]
    waits = live.harness.of("provider_wait")
    assert [(w["ref"]["seat"], w["tries"], w["seconds"]) for w in waits] == [
        (SEAT, 1, 2.0),
        (SEAT, 2, 4.0),
    ]
    assert waits[0]["retry_after"] == 120.0 and waits[0]["status"] == 402
    first = first_question(live)
    assert len(first) == 1 and first[0].decision is not None
    assert first[0].reason is None and not first[0].refused
    assert not [e for e in stored.ledger if e.type == "turn_failed"]
    assert live.budget.spent.requests == len(live.harness.of("call"))
    bundle = make_bundle(stored, live.harness.events)
    assert verify(bundle) == []
    mine = [call for call in bundle.exchanges if call.seat == SEAT][:3]
    assert [(c.status, c.wait, c.failure) for c in mine] == [
        (402, 2.0, None),
        (402, 4.0, None),
        (None, None, None),
    ]


def test_the_same_game_without_the_402s_writes_the_same_ledger() -> None:
    waited, _, _ = busy_game(times=3)
    plain, _, _ = busy_game(times=0)
    assert _dumped(waited.record.ledger) == _dumped(plain.record.ledger)


def test_an_in_flight_budget_that_stays_full_aborts_after_the_last_wait() -> None:
    live, busy, pauses = busy_game(times=100)
    stored = live.record
    assert pauses == [2.0, 4.0, 8.0, 16.0, 30.0]
    assert len(busy.sent) == 6
    assert stored.outcome == "aborted:operator" and not stored.rated
    assert "in-flight budget was still full" in (live.referee.stopped_by or "")
    assert not [e for e in stored.ledger if e.type == "turn_failed"]
    assert not [entry for entry in stored.decisions if entry.player_id == SEAT]
    assert _dumped(replay(stored).world.ledger) == _dumped(stored.ledger)
    bundle = make_bundle(stored, live.harness.events)
    assert verify(bundle) == []
    mine = [call for call in bundle.exchanges if call.seat == SEAT]
    assert [c.wait for c in mine] == [2.0, 4.0, 8.0, 16.0, 30.0, None]
    assert all(c.status == 402 and c.failure is None for c in mine)


def test_a_bundle_without_waits_hashes_as_before_the_field() -> None:
    live, _ = play("empty")
    bundle = make_bundle(live.record, live.harness.events)
    assert all(call.wait is None for call in bundle.exchanges)
    assert all(
        "wait" not in part
        for kind, part in parts(live.record, bundle.exchanges)
        if kind == "exchange"
    )


def _dumped(entries: Any) -> list[Any]:
    return [entry.model_dump(mode="json") for entry in entries]
