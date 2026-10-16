"""Claims: read from what players said, checked against the game they came from.

The mock reads a bot game deterministically; every claim it returns names only
players, rooms and objects the game holds, and cites words its speaker wrote.
The model extractor is exercised only through a recorded fixture — no test
ever makes a network call — and keeps nothing that does not validate.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from badactors.castle.budget import Limits
from badactors.castle.claims import (
    Claim,
    ClaimError,
    MockExtractor,
    ModelExtractor,
    check,
    extract,
    extraction_schema,
    own_words,
    statements,
)
from badactors.castle.contract_schema import ROOT
from badactors.castle.contracts import LedgerEntry
from badactors.castle.game import GameState
from badactors.castle.ledger import append
from badactors.castle.play import start_game
from badactors.castle.provider import FixtureProvider, ModelInfo, load_fixtures
from badactors.castle.sweep import play_seed
from castle_scenes import CAST, CASTLE

FIXTURES = Path(__file__).parent / "fixtures" / "claims"
CHEAP = ModelInfo(
    id="qwen/qwen3.6-flash",
    supported=frozenset({"structured_outputs", "response_format"}),
    prompt_price=0.05e-6,
    completion_price=0.4e-6,
)


@pytest.fixture(scope="module")
def bot_game() -> GameState:
    return play_seed(7, check_leaks=False).game


def said(game: GameState, speaker: str, words: str, *, type: str = "talk") -> int:
    """Put words in a speaker's mouth, in the hall, as a ledger entry."""
    return append(
        game.world,
        type=type,
        text=f"{speaker} said:",
        actor_id=speaker,
        room_id="great_hall",
        quote=words,
    ).seq


def reading(game: GameState, words: str, speaker: str = "mara") -> list[Claim]:
    seq = said(game, speaker, words)
    return MockExtractor()(game, [game.world.ledger[seq - 1]])


@pytest.fixture
def fresh() -> GameState:
    return start_game(CASTLE, CAST.first(8), seed=13)


# ------------------------------------------------------------------ the mock


def test_the_mock_reads_a_bot_game_the_same_way_every_time(bot_game: GameState) -> None:
    first = extract(bot_game, MockExtractor())
    assert first == extract(bot_game, MockExtractor())
    kinds = Counter(claim.kind for claim in first)
    assert {"role", "location", "action"} <= set(kinds)
    spoken = {entry.seq for entry in statements(bot_game)}
    for claim in first:
        check(bot_game, claim)
        assert claim.seq in spoken


def test_an_alibi_answers_the_demand_it_was_given(bot_game: GameState) -> None:
    alibis = [
        claim
        for claim in extract(bot_game, MockExtractor())
        if bot_game.world.ledger[claim.seq - 1].type == "alibi"
    ]
    assert alibis
    for claim in alibis:
        entry = bot_game.world.ledger[claim.seq - 1]
        assert claim.kind == "location"
        assert claim.subject == claim.speaker == entry.actor_id
        assert (claim.room, claim.day, claim.phase) == (
            entry.payload["room"],
            int(entry.payload["day"]),
            entry.payload["phase"],
        )
        assert claim.answers == int(entry.payload["demand"])
        assert (claim.asked_day, claim.asked_phase) == (claim.day, claim.phase)


def test_a_vouch_is_a_claim_and_a_promise(bot_game: GameState) -> None:
    found = extract(bot_game, MockExtractor())
    confirmed = [
        entry
        for entry in bot_game.world.ledger
        if entry.type == "vouch" and entry.payload.get("confirm") == "true"
    ]
    for entry in confirmed:
        mine = [claim for claim in found if claim.seq == entry.seq]
        assert {claim.kind for claim in mine} == {"location", "commitment"}
        promise = next(claim for claim in mine if claim.kind == "commitment")
        assert (promise.act, promise.target, promise.polarity) == (
            "turn_on",
            entry.payload["target"],
            False,
        )


def test_plain_sentences_become_claims(fresh: GameState) -> None:
    tally = reading(fresh, "Sol earned 4 coins at the mission on day 1.")
    assert [(c.kind, c.subject, c.amount, c.day) for c in tally] == [
        ("tally", "sol", 4, 1)
    ]
    denial = reading(fresh, "I was not in the Chapel during mission on day 1.")
    assert [(c.kind, c.subject, c.room, c.polarity, c.firsthand) for c in denial] == [
        ("location", "mara", "chapel", False, True)
    ]
    guess = reading(fresh, "I think Iris is a traitor.")
    assert [(c.kind, c.subject, c.role, c.hedged) for c in guess] == [
        ("role", "iris", "traitor", True)
    ]
    promise = reading(fresh, "I'll vote for Knox today.")
    assert [(c.kind, c.act, c.target, c.subject) for c in promise] == [
        ("commitment", "vote", "knox", "mara")
    ]
    rule = reading(fresh, "There are three traitors. A shield does not stop poison.")
    assert [(c.rule, c.amount, c.polarity) for c in rule] == [
        ("traitors", 3, True),
        ("shield_stops_poison", None, False),
    ]


def test_words_in_quotation_are_not_the_speakers_own(fresh: GameState) -> None:
    assert reading(fresh, "Ash told me “Sol was in the Crypt” and I laughed.") == []
    seq = said(fresh, "mara", "Sol was in the Crypt.", type="report")
    assert own_words(fresh, fresh.world.ledger[seq - 1]) == ""


def test_a_relayed_rumour_is_not_its_relayers_statement(fresh: GameState) -> None:
    relayed = append(
        fresh.world,
        type="whisper",
        text="Mara passed on a rumour to Sol:",
        actor_id="mara",
        room_id="great_hall",
        quote="People are saying Iris was in the Crypt.",
        payload={"to": "sol", "tool": "relay", "rumor": "1"},
    )
    assert relayed.seq not in {entry.seq for entry in statements(fresh)}


# ---------------------------------------------------------------- the contract


def test_check_rejects_ids_the_game_does_not_hold(fresh: GameState) -> None:
    seq = said(fresh, "mara", "I was in the Library.")
    base = {"speaker": "mara", "seq": seq, "text": "I was in the Library."}
    good = Claim(kind="location", subject="mara", room="library", **base)
    check(fresh, good)
    bad = [
        good.model_copy(update={"subject": "nobody"}),
        good.model_copy(update={"room": "ballroom"}),
        good.model_copy(update={"target": "ghost"}),
        good.model_copy(update={"thing": "crown"}),
        good.model_copy(update={"speaker": "sol"}),
        good.model_copy(update={"seq": 99_999}),
    ]
    for claim in bad:
        with pytest.raises(ClaimError):
            check(fresh, claim)
    check(fresh, good.model_copy(update={"kind": "possession", "thing": "shield"}))


def test_a_claim_must_have_what_its_kind_needs() -> None:
    base = {"speaker": "mara", "seq": 3, "text": "words"}
    with pytest.raises(ValidationError):
        Claim(kind="location", subject="mara", **base)
    with pytest.raises(ValidationError):
        Claim(kind="commitment", subject="sol", act="vote", **base)
    with pytest.raises(ValidationError):
        Claim(kind="tally", subject="sol", day=1, **base)
    with pytest.raises(ValidationError):
        Claim(kind="role", subject="sol", role="traitor", phase="night", **base)
    with pytest.raises(ValidationError):
        Claim(
            kind="location",
            subject="sol",
            room="crypt",
            tick_from=9,
            tick_to=4,
            **base,
        )
    with pytest.raises(ValidationError):
        Claim(kind="rule", rule="per_day", amount=2, **base)


# ------------------------------------------------------------------ the model


def extractor(limits: Limits | None = None, batch: int = 3) -> ModelExtractor:
    return ModelExtractor(
        model=CHEAP.id,
        info=CHEAP,
        provider=FixtureProvider(load_fixtures(FIXTURES / "extractor.jsonl")),
        limits=limits or Limits(max_requests=10, max_tokens=60_000, max_dollars=0.05),
        batch=batch,
    )


def four_statements(game: GameState) -> list[LedgerEntry]:
    for speaker, words in (
        ("mara", "I was in the Library all through free time."),
        ("iris", "Sol is one of the traitors. He was down in the Crypt."),
        ("june", "Lovely weather for it."),
        ("knox", "Iris was in the Ballroom, I swear."),
    ):
        said(game, speaker, words)
    return statements(game)


def test_the_model_extractor_keeps_only_what_validates(fresh: GameState) -> None:
    four_statements(fresh)
    model = extractor()
    found = extract(fresh, model)
    assert [(c.speaker, c.kind, c.subject) for c in found] == [
        ("mara", "location", "mara"),
        ("iris", "role", "sol"),
    ]
    assert found[0].room == "library" and found[0].phase == "free_time"
    assert len(model.failures) == 2
    assert "needs room" in model.failures[0]
    assert "ballroom" in model.failures[1]
    assert model.stopped is None
    assert model.budget.spent.requests == 2
    assert model.budget.spent.dollars == pytest.approx(0.000412 + 0.000301)


def test_the_model_extractor_never_calls_past_its_budget(fresh: GameState) -> None:
    four_statements(fresh)
    model = ModelExtractor(
        model=CHEAP.id,
        info=CHEAP,
        provider=FixtureProvider({}),
        limits=Limits(max_requests=10, max_tokens=500, max_dollars=0.05),
    )
    assert extract(fresh, model) == []
    assert model.stopped is not None and model.stopped.startswith("stopped:budget")
    assert model.budget.spent.requests == 0


def test_the_answer_schema_names_every_id_and_requires_every_field(
    fresh: GameState,
) -> None:
    schema = extraction_schema(fresh, 3)
    item = schema["properties"][ROOT]["properties"]["claims"]["items"]
    assert set(item["required"]) == set(item["properties"])
    assert item["additionalProperties"] is False
    assert item["properties"]["statement"]["enum"] == [1, 2, 3]
    players = item["properties"]["subject"]["anyOf"][0]["enum"]
    assert players == sorted(fresh.world.players)
    rooms = item["properties"]["room"]["anyOf"][0]["enum"]
    assert "ballroom" not in rooms and "library" in rooms
