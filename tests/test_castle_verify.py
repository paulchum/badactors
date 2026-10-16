"""castle-verify: a receipt replays to every number, and any tampering fails."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from badactors.castle.bots import HeuristicBot
from badactors.castle.budget import Limits
from badactors.castle.decisions import Vote
from badactors.castle.harness import play_live
from badactors.castle.kit import SPECS
from badactors.castle.ledger import CUSTODY_TYPES
from badactors.castle.mock_provider import BotProvider, mock_models
from badactors.castle.play import GameRecord, record, run_game, save_record, start_game
from badactors.castle.prompt import SYSTEM, Message, Part
from badactors.castle.provider import (
    CallRef,
    HarnessLog,
    OpenRouterProvider,
    ProviderCall,
    TransportError,
)
from badactors.castle.rules import GameConfig
from badactors.castle.verify import (
    Bundle,
    HashingProvider,
    exchanges,
    load_bundle,
    main,
    make_bundle,
    save_bundle,
    verify,
)
from castle_scenes import CAST, CASTLE, MODELS

FREE = Limits(10_000, 10**9, 1000.0)


def bot_record(seed: int, players: int = 8, **config: object) -> GameRecord:
    cast = CAST.first(players)
    game = start_game(CASTLE, cast, seed=seed, config=GameConfig.shipped(**config))
    bot = HeuristicBot(seed=seed)
    played = run_game(
        game, {seat: bot for seat in game.world.seats}, check_leaks=False, models=MODELS
    )
    return record(CASTLE, cast, played)


@pytest.fixture(scope="module")
def stored() -> GameRecord:
    return bot_record(3)


def rechained(tampered: GameRecord, bundle: Bundle) -> Bundle:
    """What a forger would publish: the tampered game, chained afresh."""
    again = make_bundle(tampered)
    return again.model_copy(update={"exchanges": bundle.exchanges})


def checks(problems: list[object]) -> set[str]:
    return {problem.check for problem in problems}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("seed", "players", "traitors"), [(0, 8, 2), (11, 9, 2), (4, 10, 3)]
)
def test_an_honest_game_verifies(seed: int, players: int, traitors: int) -> None:
    bundle = make_bundle(bot_record(seed, players, traitors=traitors))
    assert verify(bundle) == []
    assert verify(bundle, head=bundle.head) == []
    assert bundle.head == bundle.links[-1]
    assert len(bundle.links) == 1 + len(bundle.record.decisions) + len(
        bundle.record.ledger
    )


def test_a_tampered_decision_fails(stored: GameRecord) -> None:
    bundle = make_bundle(stored)
    index, logged = next(
        (i, d) for i, d in enumerate(stored.decisions) if isinstance(d.decision, Vote)
    )
    assert isinstance(logged.decision, Vote)
    others = [
        d.decision.target
        for d in stored.decisions
        if isinstance(d.decision, Vote)
        and d.day == logged.day
        and d.decision.target not in (logged.decision.target, logged.player_id)
    ]
    changed = logged.model_copy(
        update={"decision": logged.decision.model_copy(update={"target": others[0]})}
    )
    decisions = list(stored.decisions)
    decisions[index] = changed
    tampered = stored.model_copy(update={"decisions": decisions})

    naive = bundle.model_copy(update={"record": tampered})
    assert "chain" in checks(verify(naive))
    forged = rechained(tampered, bundle)
    problems = verify(forged)
    assert problems and "chain" not in checks(problems)
    assert checks(problems) & {"replay", "ledger", "entry"}
    assert "chain" in checks(verify(forged, head=bundle.head))


def test_a_changed_config_number_fails(stored: GameRecord) -> None:
    bundle = make_bundle(stored)
    roll = next(
        roll
        for entry in stored.ledger
        for roll in entry.rolls
        if roll.purpose.split(":")[0] in SPECS and roll.threshold is not None
    )
    tool, what = roll.purpose.split(":")[:2]
    assert roll.threshold is not None
    config = stored.config.model_dump(mode="json")
    config["tools"][tool][what] = round(1 - roll.threshold, 3) or 0.5
    tampered = stored.model_copy(update={"config": GameConfig.model_validate(config)})

    assert "chain" in checks(verify(bundle.model_copy(update={"record": tampered})))
    problems = verify(rechained(tampered, bundle))
    assert "roll" in checks(problems)
    detail = next(str(p) for p in problems if p.check == "roll")
    assert "threshold" in detail or "rolls" in detail or "replay" in detail


def test_a_dropped_roll_fails(stored: GameRecord) -> None:
    bundle = make_bundle(stored)
    at = next(i for i, entry in enumerate(stored.ledger) if len(entry.rolls) >= 1)
    entry = stored.ledger[at]
    ledger = list(stored.ledger)
    ledger[at] = entry.model_copy(update={"rolls": entry.rolls[1:]})
    tampered = stored.model_copy(update={"ledger": ledger})

    assert "chain" in checks(verify(bundle.model_copy(update={"record": tampered})))
    problems = verify(rechained(tampered, bundle))
    assert any("dropped" in str(p) for p in problems)
    assert {"roll", "ledger"} <= checks(problems)


def test_a_rewritten_witness_record_or_custody_move_fails(stored: GameRecord) -> None:
    bundle = make_bundle(stored)
    seen = next(
        i
        for i, entry in enumerate(stored.ledger)
        if entry.witnesses and entry.witnesses[0].sense == "saw"
    )
    ledger = list(stored.ledger)
    witness = ledger[seen].witnesses[0].model_copy(update={"confidence": "glimpse"})
    ledger[seen] = ledger[seen].model_copy(
        update={"witnesses": [witness, *ledger[seen].witnesses[1:]]}
    )
    problems = verify(rechained(stored.model_copy(update={"ledger": ledger}), bundle))
    assert "perception" in checks(problems)

    moved = next(
        i
        for i, entry in enumerate(stored.ledger)
        if entry.type in CUSTODY_TYPES and "thing_id" in entry.payload
    )
    ledger = list(stored.ledger)
    payload = {**ledger[moved].payload, "thing_id": "nothing_real"}
    ledger[moved] = ledger[moved].model_copy(update={"payload": payload})
    problems = verify(rechained(stored.model_copy(update={"ledger": ledger}), bundle))
    assert "custody" in checks(problems)


@pytest.fixture(scope="module")
def live_bundle() -> Bundle:
    log = HarnessLog()
    live = play_live(
        CASTLE,
        CAST.first(8),
        seed=5,
        config=GameConfig.shipped(max_days=2),
        provider=HashingProvider(BotProvider(HeuristicBot(seed=5)), log, "mock:bot"),
        models=mock_models(MODELS),
        limits=FREE,
        harness=log,
    )
    return make_bundle(live.record, log.events)


def test_a_model_game_carries_a_hashed_exchange_for_every_call(
    live_bundle: Bundle,
) -> None:
    assert verify(live_bundle) == []
    calls = live_bundle.exchanges
    assert calls and all(call.provider == "mock:bot" for call in calls)
    cast = live_bundle.record.cast.models()
    assert all(call.model == cast[call.seat] for call in calls)
    assert all(call.response is not None and call.failure is None for call in calls)
    asked = [d for d in live_bundle.record.decisions if d.kind != "exit"]
    assert len(calls) >= len(asked)

    flipped = calls[0].model_copy(update={"response": "0" * 64})
    tampered = live_bundle.model_copy(update={"exchanges": [flipped, *calls[1:]]})
    assert "chain" in checks(verify(tampered))
    wrong = calls[0].model_copy(update={"model": "someone/else"})
    relinked = make_bundle(live_bundle.record)
    swapped = Bundle(
        record=relinked.record,
        exchanges=[wrong, *calls[1:]],
        links=relinked.links,
        head=relinked.head,
    )
    assert "exchange" in checks(verify(swapped))


def test_an_exchange_keeps_no_key_and_no_words(tmp_path: Path) -> None:
    planted = "sk-or-v1-PLANTED-0123456789"

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {planted}"
        if b"fail" in request.content:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"decision": "x"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    log = HarnessLog(tmp_path / "harness.jsonl")
    hashing = HashingProvider(
        OpenRouterProvider(api_key=planted, client=client), log, "openrouter"
    )

    def call(text: str, attempt: int) -> ProviderCall:
        return ProviderCall(
            ref=CallRef("g", "sol", MODELS[0], "vote", 1, 4, 0, attempt),
            messages=(
                Message("system", (Part(SYSTEM),)),
                Message("user", (Part(text),)),
            ),
            schema={"type": "object"},
            mode="json_object",
            max_tokens=64,
        )

    asyncio.run(hashing.complete(call("SECRET WORDS", 1)))
    with pytest.raises(TransportError):
        asyncio.run(hashing.complete(call("fail", 2)))
    written = (tmp_path / "harness.jsonl").read_text()
    for secret in (planted, "SECRET WORDS", "Bearer"):
        assert secret not in written
    found = exchanges(log.events)
    assert [x.failure for x in found] == [None, "transport"]
    assert found[1].status == 503
    assert planted not in json.dumps([x.model_dump() for x in found])


def test_castle_verify_passes_a_saved_game_and_fails_a_tampered_one(
    tmp_path: Path, stored: GameRecord, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = tmp_path / "game.json"
    save_record(stored, saved)
    main(["--make", str(saved), "--out", str(tmp_path / "game.bundle.json.gz")])
    bundle_path = tmp_path / "game.bundle.json.gz"
    bundle = load_bundle(bundle_path)
    assert bundle.head == make_bundle(stored).head
    main([str(bundle_path), "--head", bundle.head])
    assert "verified" in capsys.readouterr().out

    entry = stored.ledger[10]
    ledger = list(stored.ledger)
    ledger[10] = entry.model_copy(update={"tick": entry.tick + 1})
    forged = make_bundle(stored.model_copy(update={"ledger": ledger}))
    forged_path = tmp_path / "forged.json"
    save_bundle(forged, forged_path)
    with pytest.raises(SystemExit) as failed:
        main([str(bundle_path), str(forged_path)])
    assert "1 of 2 bundles failed" in str(failed.value.code)
    out = capsys.readouterr().out
    assert f"FAILED {forged_path}" in out and "[entry]" in out

    broken = tmp_path / "broken.json"
    broken.write_text('{"bundle_version": 1}')
    with pytest.raises(SystemExit):
        main([str(broken)])
    assert "not a verify bundle" in capsys.readouterr().out
