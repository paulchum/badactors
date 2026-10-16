"""``castle-qualify``: the frozen suite, and a gate that counts only breakage.

Nothing here touches the network: models answer through the mock provider, or
through a scripted one serving the recorded bad responses in
``fixtures/openrouter``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

import pytest

from badactors.castle.bots import HeuristicBot
from badactors.castle.budget import Limits
from badactors.castle.decisions import AUDIENCE_ONLY, TRAITOR_ONLY, RequestKind
from badactors.castle.mock_provider import BotProvider, mock_models
from badactors.castle.play import RELIABILITY_GATE
from badactors.castle.provider import (
    OperatorError,
    ProviderCall,
    Reply,
    TransportError,
    parse_reply,
)
from badactors.castle.qualify import (
    FLOOR,
    MIN_TURNS,
    Suite,
    allocate,
    build_suite,
    load_suite,
    main,
    qualify,
    result_path,
)
from castle_scenes import CAST, CASTLE

FIXTURES = Path(__file__).parent / "fixtures" / "openrouter"
MODEL = "lab-q/model-1"
INFO = mock_models([MODEL])[MODEL]
FREE = Limits(10_000, 10**10, 1_000.0)
BUDGET = ["--max-requests", "700", "--max-tokens", "20000000", "--max-dollars", "1"]


@pytest.fixture(scope="module")
def suite() -> Suite:
    return load_suite()


def test_the_frozen_suite_covers_every_turn_kind_in_each_role_asked_it(
    suite: Suite,
) -> None:
    wanted = set()
    for kind in get_args(RequestKind):
        if kind in AUDIENCE_ONLY:
            continue  # An exit interview is no turn in the game.
        roles = (
            ["traitor"]
            if kind in TRAITOR_ONLY
            else ["faithful"]
            if kind == "recruit_answer"
            else ["traitor", "faithful"]
        )
        wanted |= {f"{kind}/{role}" for role in roles}
    strata = suite.strata()
    assert set(strata) == wanted
    assert len(suite.items) == 300 >= MIN_TURNS
    assert all(count >= 3 for count in strata.values())
    assert [(i.seed, i.ordinal) for i in suite.items] == sorted(
        (i.seed, i.ordinal) for i in suite.items
    )
    assert len({(i.seed, i.ordinal) for i in suite.items}) == len(suite.items)


def test_a_build_is_stratified_and_the_same_every_time() -> None:
    first = build_suite(CASTLE, CAST.first(8), seeds=[0, 1], size=150)
    again = build_suite(CASTLE, CAST.first(8), seeds=[0, 1], size=150)
    assert first == again and first.digest == again.digest
    assert len(first.items) == 150
    assert first.strata()["plan/faithful"] > first.strata()["vote/traitor"]
    # Too small a size still keeps every stratum's floor: coverage comes first.
    tiny = build_suite(CASTLE, CAST.first(8), seeds=[0, 1], size=10)
    assert set(tiny.strata()) == set(first.strata())
    assert all(n >= min(FLOOR, first.strata()[k]) for k, n in tiny.strata().items())


def test_allocation_floors_every_stratum_then_shares_by_frequency() -> None:
    counts = {"a": 100, "b": 30, "c": 2, "d": 8}
    found = allocate(counts, 40, FLOOR)
    assert sum(found.values()) == 40
    assert found["c"] == 2 and found["d"] >= FLOOR and found["b"] >= FLOOR
    assert found["a"] > found["b"] > found["d"]
    assert allocate(counts, 500, FLOOR) == counts
    assert allocate(counts, 10, FLOOR) == {"a": 6, "b": 6, "c": 2, "d": 6}


def test_a_mock_model_answers_every_question_and_qualifies_nobody(
    suite: Suite,
) -> None:
    found = qualify(
        suite,
        MODEL,
        INFO,
        BotProvider(HeuristicBot(seed=0)),
        limits=FREE,
        provider_name="mock:bot",
        mock=True,
    )
    assert (found.outcome, found.turns, found.answered) == ("finished", 300, 300)
    assert found.rate == 0 and found.failed == {} and found.qualified is None
    assert found.suite_digest == suite.digest
    assert found.spent["requests"] == 300
    assert "qualifies nobody" in found.summary()


@dataclass
class Scripted:
    """The mock model, but some questions get a recorded bad response: by the
    question's ordinal modulo 50, on the attempts named."""

    script: dict[int, tuple[str, tuple[int, ...]]]
    inner: BotProvider = field(
        default_factory=lambda: BotProvider(HeuristicBot(seed=0))
    )
    calls: int = 0

    async def complete(self, call: ProviderCall) -> Reply:
        self.calls += 1
        name, attempts = self.script.get(call.ref.ordinal % 50, ("", ()))
        if call.ref.attempt in attempts:
            if name == "timeout":
                raise TransportError("timed out")
            if name == "payment_402":
                raise OperatorError("Insufficient credits.", status=402)
            recorded: dict[str, Any] = json.loads(
                (FIXTURES / f"{name}.json").read_text()
            )
            return parse_reply(recorded["body"])
        return await self.inner.complete(call)


def test_only_reliability_failures_count_toward_the_gate(suite: Suite) -> None:
    provider = Scripted(
        {
            1: ("malformed", (1,)),  # Corrected on the retry: answered.
            2: ("malformed", (1, 2)),
            3: ("refusal", (1,)),
            4: ("content_filter", (1,)),
            5: ("timeout", (1,)),
            6: ("empty", (1, 2)),
        }
    )
    found = qualify(
        suite,
        MODEL,
        INFO,
        provider,
        limits=FREE,
        provider_name="scripted",
        concurrency=1,
    )
    times = len([i for i in range(300) if i % 50 == 1])
    assert found.failed == {
        "content_filter": times,
        "empty": times,
        "refusal": times,
        "transport": times,
        "unparseable": times,
    }
    assert (found.refusals, found.content_filters) == (times, times)
    assert found.reliability_failures == 3 * times
    assert found.retried == 3 * times
    assert found.answered == 300 - 5 * times
    assert found.rate == pytest.approx(3 * times / 300)
    assert found.rate > RELIABILITY_GATE and found.qualified is False
    assert "does NOT qualify" in found.summary()

    declining = Scripted({3: ("refusal", (1,)), 4: ("content_filter", (1,))})
    passed = qualify(
        suite,
        MODEL,
        INFO,
        declining,
        limits=FREE,
        provider_name="scripted",
        concurrency=1,
    )
    assert passed.refusals == passed.content_filters == times
    assert passed.rate == 0 and passed.qualified is True
    assert "QUALIFIES" in passed.summary()


def test_a_run_cut_short_decides_nothing(suite: Suite) -> None:
    short = qualify(
        suite,
        MODEL,
        INFO,
        BotProvider(HeuristicBot(seed=0)),
        limits=Limits(100, 10**10, 1_000.0),
        provider_name="scripted",
    )
    assert short.outcome == "stopped:budget"
    assert short.turns < MIN_TURNS and short.qualified is None
    assert short.spent["requests"] <= 100

    provider = Scripted({9: ("payment_402", (1,))})
    aborted = qualify(
        suite,
        MODEL,
        INFO,
        provider,
        limits=FREE,
        provider_name="scripted",
        concurrency=1,
    )
    assert aborted.outcome == "aborted:operator"
    assert aborted.failed == {} and aborted.qualified is None
    assert aborted.turns == 9


def test_castle_qualify_needs_a_budget_and_writes_its_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for given in ([], BUDGET[:4]):
        with pytest.raises(SystemExit) as stopped:
            main([MODEL, "--mock", *given])
        assert "will not start without" in str(stopped.value.code)
    with pytest.raises(SystemExit, match="Name the model"):
        main([*BUDGET])

    out = tmp_path / "qualify"
    main([MODEL, "--mock", *BUDGET, "--out", str(out)])
    printed = capsys.readouterr().out
    assert printed.startswith("Preflight: 300 questions")
    written = json.loads(result_path(out, MODEL).read_text())
    assert written["mock"] and written["qualified"] is None
    assert written["turns"] == 300 and written["provider"] == "mock:bot"
    harness = result_path(out, MODEL).with_suffix(".harness.jsonl")
    events = [json.loads(line) for line in harness.read_text().splitlines()]
    assert sum(1 for e in events if e["event"] == "exchange") == 300

    main([MODEL, "--mock", "--dry-run", *BUDGET, "--out", str(tmp_path / "dry")])
    assert not (tmp_path / "dry").exists()


def test_castle_qualify_builds_a_suite_on_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], suite: Suite
) -> None:
    path = tmp_path / "suite.json.xz"
    main(["--build", "--suite", str(path)])
    assert f"digest {suite.digest}" in capsys.readouterr().out
    assert load_suite(path) == suite
