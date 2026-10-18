"""Reasoning compatibility requests are budgeted, receipted and replayable."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from badactors.castle.budget import Budget, BudgetExhausted, Limits
from badactors.castle.decisions import Vote
from badactors.castle.harness import ModelPlayer, Question, ReasoningBook, play_live
from badactors.castle.mock_provider import mock_models
from badactors.castle.play import BadAnswer, TurnFailure, replay
from badactors.castle.provider import (
    CallRef,
    FixtureProvider,
    HarnessLog,
    ModeBook,
    ModelInfo,
    OpenRouterProvider,
    OperatorError,
    RecordingProvider,
    load_fixtures,
)
from badactors.castle.rules import GameConfig
from badactors.castle.verify import (
    HashingProvider,
    digest,
    exchanges,
    make_bundle,
    verify,
)
from castle_scenes import CAST, CASTLE, MODELS, pause_at

MANDATORY = {"error": {"message": "Reasoning is mandatory for this endpoint."}}
FREE = Limits(100, 10**8, 100.0)


@dataclass
class Rig:
    player: ModelPlayer
    question: Question
    sent: list[dict[str, Any]]
    replies: list[httpx.Response]
    recording: Path
    good: dict[str, Any]


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    game, observation = pause_at(lambda o, g: o.request.kind == "vote", seed=7)
    decision = Vote(target=observation.request.options[0])
    good = {
        "choices": [
            {"message": {"content": json.dumps({"decision": decision.model_dump()})}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.0},
    }
    sent: list[dict[str, Any]] = []
    replies: list[httpx.Response] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if replies:
            return replies.pop(0)
        if body.get("reasoning") == {"enabled": False}:
            return httpx.Response(400, json=MANDATORY)
        return httpx.Response(200, json=good)

    wire = OpenRouterProvider(
        api_key="not-a-real-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    log = HarnessLog()
    recording = tmp_path / "responses.jsonl"
    info = ModelInfo(MODELS[0], frozenset({"response_format"}), 0.0, 0.0)
    player = ModelPlayer(
        seat=observation.viewer_id,
        model=info.id,
        info=info,
        provider=RecordingProvider(HashingProvider(wire, log, "openrouter"), recording),
        modes=ModeBook.starting({info.id: info}, log),
        budget=Budget(FREE),
        log=log,
        reasoning=ReasoningBook("least"),
    )
    ref = CallRef(
        "g", player.seat, info.id, "vote", game.world.day, game.world.tick, 0, 1
    )
    yield Rig(player, player.question(observation, ref), sent, replies, recording, good)
    asyncio.run(wire.aclose())


def test_fallback_reserves_and_receipts_both_actual_requests(rig: Rig) -> None:
    result = asyncio.run(rig.player.answer(rig.question))
    assert isinstance(result, Vote)
    assert rig.player.budget.spent.requests == len(rig.sent) == 2
    assert rig.player.budget.held.requests == 0
    assert [body["reasoning"] for body in rig.sent] == [
        {"enabled": False},
        {"effort": "minimal"},
    ]
    found = exchanges(rig.player.log.events)
    assert [call.attempt for call in found] == [1, 2]
    assert [call.failure for call in found] == ["transport", None]
    assert [call.request for call in found] == [digest(body) for body in rig.sent]
    assert found[0].request != found[1].request
    assert len(rig.player.log.of("reasoning_fallback")) == 1
    assert rig.player.modes.mode(rig.player.model) == "json_object"

    # Raw response recordings retain both calls, so replay takes the same path.
    fixtures = load_fixtures(rig.recording)
    assert len(fixtures) == 2
    replayed = replace(
        rig.player,
        provider=FixtureProvider(fixtures),
        budget=Budget(FREE),
        log=HarnessLog(),
        reasoning=ReasoningBook("least"),
    )
    assert asyncio.run(replayed.answer(rig.question)) == result
    assert replayed.budget.spent.requests == 2

    next_question = replace(rig.question, ref=replace(rig.question.ref, ordinal=1))
    assert asyncio.run(rig.player.answer(next_question)) == result
    assert len(rig.sent) == 3
    assert rig.sent[-1]["reasoning"] == {"effort": "minimal"}
    assert len(rig.player.log.of("reasoning_fallback")) == 1


@pytest.mark.parametrize("limit", ["requests", "dollars"])
def test_fallback_never_sends_a_request_its_new_reservation_cannot_cover(
    rig: Rig, limit: str
) -> None:
    if limit == "requests":
        rig.player.budget = Budget(Limits(1, 10**8, 100.0))
    else:
        rig.player.info = replace(rig.player.info, request_price=1.0)
        rig.player.budget = Budget(Limits(100, 10**8, 1.0))
    with pytest.raises(BudgetExhausted):
        asyncio.run(rig.player.answer(rig.question))
    assert len(rig.sent) == rig.player.budget.spent.requests == 1
    assert rig.player.budget.held.requests == 0
    assert len(exchanges(rig.player.log.events)) == 1


@pytest.mark.parametrize("setting", ["default", "off", "minimal", "low"])
def test_only_least_allows_the_configuration_retry(rig: Rig, setting: str) -> None:
    rig.player.reasoning = ReasoningBook(setting)
    rig.replies.append(httpx.Response(400, json=MANDATORY))
    with pytest.raises(TurnFailure) as failed:
        asyncio.run(rig.player.answer(rig.question))
    assert failed.value.reason == "transport"
    assert len(rig.sent) == rig.player.budget.spent.requests == 1
    assert not rig.player.log.of("reasoning_fallback")


def test_a_minimal_rejection_cannot_loop(rig: Rig) -> None:
    rig.replies.extend([httpx.Response(400, json=MANDATORY) for _ in range(2)])
    with pytest.raises(TurnFailure):
        asyncio.run(rig.player.answer(rig.question))
    assert len(rig.sent) == rig.player.budget.spent.requests == 2
    assert len(rig.player.log.of("reasoning_fallback")) == 1


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (400, "response_format is unsupported"),
        (429, "reasoning is mandatory"),
        (503, "reasoning is mandatory"),
    ],
)
def test_other_http_errors_do_not_trigger_a_reasoning_retry(
    rig: Rig, status: int, message: str
) -> None:
    rig.replies.append(httpx.Response(status, json={"error": {"message": message}}))
    with pytest.raises(TurnFailure):
        asyncio.run(rig.player.answer(rig.question))
    assert len(rig.sent) == rig.player.budget.spent.requests == 1
    assert not rig.player.log.of("reasoning_fallback")


def test_a_corrective_answer_after_fallback_has_its_own_receipt(rig: Rig) -> None:
    rig.replies.extend(
        [
            httpx.Response(400, json=MANDATORY),
            httpx.Response(200, json={"choices": [{"message": {"content": "{"}}]}),
        ]
    )
    with pytest.raises(BadAnswer):
        asyncio.run(rig.player.answer(rig.question))
    corrected = replace(rig.question, ref=replace(rig.question.ref, attempt=2))
    assert isinstance(asyncio.run(rig.player.answer(corrected)), Vote)
    assert rig.player.budget.spent.requests == 3
    found = exchanges(rig.player.log.events)
    assert [call.attempt for call in found] == [1, 2, 3]
    assert [call.failure for call in found] == ["transport", "unparseable", None]
    assert len(load_fixtures(rig.recording)) == 3


def test_an_operator_error_after_fallback_still_aborts(rig: Rig) -> None:
    rig.replies.extend(
        [
            httpx.Response(400, json=MANDATORY),
            httpx.Response(402, json={"error": {"message": "no credit"}}),
        ]
    )
    with pytest.raises(OperatorError):
        asyncio.run(rig.player.answer(rig.question))
    assert rig.player.budget.spent.requests == len(rig.sent) == 2
    assert rig.player.budget.held.requests == 0
    assert [call.status for call in exchanges(rig.player.log.events)] == [400, 402]


def test_in_flight_off_requests_each_reserve_their_minimal_retry(rig: Rig) -> None:
    sent: list[dict[str, Any]] = []

    async def run() -> None:
        both_off = asyncio.Event()

        async def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            sent.append(body)
            if body["reasoning"] == {"enabled": False}:
                if len(sent) == 2:
                    both_off.set()
                await both_off.wait()
                return httpx.Response(400, json=MANDATORY)
            return httpx.Response(200, json=rig.good)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            wire = OpenRouterProvider(api_key="not-a-real-key", client=client)
            rig.player.provider = HashingProvider(wire, rig.player.log, "openrouter")
            rig.player.budget = Budget(Limits(4, 10**8, 100.0))
            other = replace(rig.question, ref=replace(rig.question.ref, ordinal=1))
            results = await asyncio.gather(
                rig.player.answer(rig.question), rig.player.answer(other)
            )
            assert all(isinstance(result, Vote) for result in results)

    asyncio.run(run())
    assert rig.player.budget.spent.requests == len(sent) == 4
    assert rig.player.budget.held.requests == 0
    assert [body["reasoning"] for body in sent[:2]] == [{"enabled": False}] * 2
    assert [body["reasoning"] for body in sent[2:]] == [{"effort": "minimal"}] * 2
    found = exchanges(rig.player.log.events)
    assert {(call.ordinal, call.attempt) for call in found} == {
        (0, 1),
        (0, 2),
        (1, 1),
        (1, 2),
    }


def test_a_budget_stop_during_fallback_is_unrated_and_replays() -> None:
    sent: list[object] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(400, json=MANDATORY)

    wire = OpenRouterProvider(
        api_key="not-a-real-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    log = HarnessLog()
    live = play_live(
        CASTLE,
        CAST,
        seed=7,
        config=GameConfig.shipped(max_days=2),
        provider=HashingProvider(wire, log, "openrouter"),
        models=mock_models(MODELS),
        limits=Limits(1, 10**8, 100.0),
        reasoning="least",
        concurrent=False,
        harness=log,
        closers=[wire.aclose],
    )
    assert len(sent) == live.budget.spent.requests == 1
    assert live.record.outcome == "stopped:budget" and not live.record.rated
    assert not live.record.decisions
    assert not [entry for entry in live.record.ledger if entry.type == "turn_failed"]
    assert replay(live.record).world.ledger == live.record.ledger
    assert verify(make_bundle(live.record, log.events)) == []


def test_unknown_reasoning_is_rejected_before_any_call() -> None:
    with pytest.raises(ValueError, match="unknown reasoning setting"):
        ReasoningBook("loud")
