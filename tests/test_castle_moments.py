"""Moments found by the ledger's shape: every detector fires in thirty seeded
bot games, and none fires on a game where what it looks for never happened."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import pytest

from badactors.castle.bots import HeuristicBot
from badactors.castle.contracts import LedgerEntry
from badactors.castle.game import GameState
from badactors.castle.moments import (
    CLAIMS,
    KINDS,
    LABELS,
    Moment,
    MomentKind,
    Verdict,
    detect,
)
from badactors.castle.play import record, run_game, start_game
from badactors.castle.rules import GameConfig
from badactors.castle.show import audience_feed
from badactors.castle.sweep import Swept, config_for, play_seed
from castle_scenes import CAST, CASTLE, MODELS

SEEDS = range(30, 60)
"""Thirty consecutive sweep seeds. The bots rarely recruit a player who turns,
or end on a twist, and this run holds both."""


def _unchecked(seed: int) -> Swept:
    return play_seed(seed, check_leaks=False)


@pytest.fixture(scope="module")
def games() -> list[GameState]:
    with ProcessPoolExecutor() as pool:
        return [swept.game for swept in pool.map(_unchecked, SEEDS, chunksize=3)]


@pytest.fixture(scope="module")
def found(games: list[GameState]) -> list[list[Moment]]:
    return [detect(game) for game in games]


# ------------------------------------------------------- what each one needs


def _types(game: GameState, *types: str) -> Iterator[LedgerEntry]:
    return (entry for entry in game.world.ledger if entry.type in types)


def _forged_and_read(game: GameState) -> bool:
    forged = {
        str(e.seq) for e in _types(game, "write") if e.payload.get("tool") == "forge"
    }
    return any(
        forged & set(e.payload.get("writings", "").split(","))
        for e in _types(game, "read")
    )


def _intents(game: GameState) -> list[LedgerEntry]:
    return [e for e in game.world.ledger if "intent" in e.private]


def _backfire_possible(game: GameState) -> bool:
    for attempt in _intents(game):
        for later in game.world.ledger[attempt.seq :]:
            if later.type in ("accuse", "report", "banishment") and (
                later.payload.get("target") == attempt.actor_id
            ):
                return True
    return False


def _died(game: GameState, player: str) -> bool:
    return any(e.payload.get("player") == player for e in _types(game, "death"))


def _answers(game: GameState, answer: str) -> list[LedgerEntry]:
    return [
        e for e in _types(game, "recruit_answer") if e.payload.get("answer") == answer
    ]


def _turned(game: GameState) -> bool:
    for answer in _answers(game, "accepted"):
        for later in game.world.ledger[answer.seq :]:
            if (
                later.actor_id == answer.actor_id
                and later.type in ("accuse", "vote")
                and later.payload.get("target") == answer.payload.get("target")
            ):
                return True
    return False


def _gaslit(game: GameState) -> bool:
    lit = {
        e.payload["to"]
        for e in _types(game, "whisper")
        if e.payload.get("tool") == "gaslight" and e.payload.get("reached") == "true"
    }
    return any(
        e.actor_id in lit
        for e in _types(game, "alibi", "vouch")
        if e.type == "alibi" or e.payload.get("confirm") == "true"
    )


def _twist_possible(game: GameState) -> bool:
    over = [e for e in _types(game, "game_over")]
    recruits = [e.actor_id for e in _answers(game, "accepted")]
    return (
        bool(over)
        and over[-1].payload.get("winner") == "traitor"
        and any(r is not None and game.world.players[r].alive for r in recruits)
        and any(_types(game, "endgame_vote"))
    )


PRECONDITIONS: dict[MomentKind, Callable[[GameState], bool]] = {
    "wrong_banishment": lambda g: any(
        e.payload.get("role") == "faithful" for e in _types(g, "banishment")
    ),
    "poisoning": lambda g: any(_types(g, "poison")),
    "forgery_worked": _forged_and_read,
    "manipulation_success": lambda g: bool(_intents(g)),
    "manipulation_backfire": _backfire_possible,
    "shield_save": lambda g: any(_types(g, "spent")),
    "recruit_refused_killed": lambda g: any(
        _died(g, e.actor_id or "") for e in _answers(g, "refused")
    ),
    "recruit_turned": _turned,
    "refusal": lambda g: (
        any(_types(g, "declined"))
        or any(e.payload.get("reason") == "refusal" for e in _types(g, "turn_failed"))
    ),
    "gaslit": _gaslit,
    "exit_reveal": lambda g: any(
        e.private.get("told", "[]") != "[]" for e in _types(g, "exit_interview")
    ),
    "twist_ending": _twist_possible,
}
"""What has to have happened for each kind of moment to be possible, read from
the ledger without the detectors."""


def test_every_detector_fires_and_never_without_its_preconditions(
    games: list[GameState], found: list[list[Moment]]
) -> None:
    fired: Counter[str] = Counter()
    for game, moments in zip(games, found, strict=True):
        kinds = {moment.kind for moment in moments}
        fired.update(kinds)
        for kind in KINDS:
            if not PRECONDITIONS[kind](game):
                assert kind not in kinds, (game.world.seed, kind)
    assert set(fired) == set(KINDS), set(KINDS) - set(fired)


def test_every_moment_is_a_well_formed_window_on_the_ledger(
    games: list[GameState], found: list[list[Moment]]
) -> None:
    for game, moments in zip(games, found, strict=True):
        ledger = game.world.ledger
        assert [m.score for m in moments] == sorted(
            (m.score for m in moments), reverse=True
        )
        assert len({m.id for m in moments}) == len(moments)
        for moment in moments:
            beats = [
                moment.anchor,
                *moment.truth,
                *([moment.claim] if moment.claim else []),
                *([moment.line.seq] if moment.line else []),
            ]
            assert all(moment.first_seq <= seq <= moment.last_seq for seq in beats)
            assert moment.evidence == sorted(set(moment.evidence))
            assert set(beats) <= set(moment.evidence)
            first, last = ledger[moment.first_seq - 1], ledger[moment.last_seq - 1]
            assert (moment.first_tick, moment.last_tick) == (first.tick, last.tick)
            assert moment.score == (1 + len(moment.deceived)) * moment.stakes * (
                2 if moment.contradiction else 1
            )
            assert moment.label == LABELS[moment.kind] and moment.verdict is None
            assert moment.seats and moment.headline
            if moment.claim is not None:
                claim = ledger[moment.claim - 1]
                assert claim.quote is not None and claim.actor_id is not None
            if moment.line is not None:
                # The line is the speaker's own, word for word.
                holder = ledger[moment.line.seq - 1]
                assert moment.claim is not None
                assert holder.actor_id == ledger[moment.claim - 1].actor_id
                assert holder.private[moment.line.field] == moment.line.text


def test_a_twist_ending_has_the_highest_stakes_in_its_game(
    found: list[list[Moment]],
) -> None:
    twists = [
        moments for moments in found if any(m.kind == "twist_ending" for m in moments)
    ]
    assert twists
    for moments in twists:
        twist = next(m for m in moments if m.kind == "twist_ending")
        assert twist.deceived and twist.clip_ready
        assert twist.stakes > max(m.stakes for m in moments if m is not twist)


def test_the_claims_are_words_said_in_front_of_others(
    found: list[list[Moment]],
) -> None:
    kinds_with_claims: set[str] = set()
    for moments in found:
        for moment in moments:
            if moment.claim is not None:
                kinds_with_claims.add(moment.kind)
    # Every kind but a turned recruit, who in a bot game only ever votes,
    # has a moment with a claim in words.
    assert kinds_with_claims >= set(KINDS) - {"recruit_turned"}
    assert {"talk", "accuse"} <= CLAIMS


# ------------------------------------------------------ preconditions removed


def _played(seed: int, **overrides: Any) -> GameState:
    """The sweep's game for this seed, with the config changed."""
    base = config_for(seed).model_dump(exclude={"missions"})
    config = GameConfig.shipped(traitors=base["traitors"], **overrides)
    cast = CAST.first(8 + seed % 3)
    game = start_game(CASTLE, cast, seed=seed, config=config)
    bot = HeuristicBot(seed=seed)
    played = run_game(
        game, {pid: bot for pid in game.world.seats}, check_leaks=False, models=MODELS
    )
    return played.game


@pytest.mark.parametrize(
    ("seed", "overrides", "absent"),
    [
        (31, {"tools": {"poison": {"per_game": 0}}}, {"poisoning"}),
        (
            56,
            {"tools": {"recruit": {"per_game": 0}}},
            {"recruit_turned", "recruit_refused_killed", "twist_ending"},
        ),
        (44, {"tools": {"forge": {"per_game": 0}}}, {"forgery_worked"}),
        (35, {"tools": {"gaslight": {"per_game": 0}}}, {"gaslit"}),
        (34, {"shields": 0}, {"shield_save"}),
    ],
)
def test_no_moment_without_the_act_it_needs(
    found: list[list[Moment]], seed: int, overrides: dict[str, Any], absent: set[str]
) -> None:
    assert absent & {moment.kind for moment in found[SEEDS.index(seed)]}
    game = _played(seed, **overrides)
    assert not absent & {moment.kind for moment in detect(game)}


def test_no_manipulation_moment_in_a_game_without_manipulation() -> None:
    off = {
        tool: {"per_game": 0}
        for tool in (
            "forge",
            "counterfeit",
            "honeypot",
            "hook",
            "gaslight",
            "rumor",
            "blackmail",
            "plant",
            "tamper",
        )
    }
    game = _played(41, tools=off)
    kinds = {moment.kind for moment in detect(game)}
    assert not kinds & {"manipulation_success", "manipulation_backfire", "gaslit"}


# ------------------------------------------------------------------- the hooks


def test_verdicts_stamp_the_claims_and_settle_whether_a_target_gave_in(
    games: list[GameState],
) -> None:
    game = games[SEEDS.index(56)]
    plain = {m.id: m for m in detect(game)}
    judged = detect(game, verdicts=lambda seq: Verdict(label="IMPLANTED"))
    for moment in judged:
        before = plain[moment.id]
        if moment.claim is None:
            assert moment.verdict is None and moment.label == before.label
            continue
        assert moment.label == "IMPLANTED" and not moment.contradiction
        if moment.kind == "gaslit":
            assert moment.notes["gave_in"] == "true"
    lies = detect(game, verdicts=lambda seq: Verdict(label="LIE"))
    assert all(m.contradiction == (m.line is not None) for m in lies if m.claim)


def test_a_silence_counts_as_a_refusal_only_when_the_classifier_says_so(
    games: list[GameState],
) -> None:
    game = games[0]
    silent = sum(1 for _ in _types(game, "stay_silent"))
    assert silent
    plain = [m for m in detect(game) if m.kind == "refusal"]
    every = [m for m in detect(game, declines=lambda e: True) if m.kind == "refusal"]
    assert len(every) == len(plain) + silent


def test_the_feed_carries_the_moments_with_audience_headlines() -> None:
    swept = play_seed(56, check_leaks=False)
    feed = audience_feed(record(CASTLE, swept.cast, swept.played))
    assert [m.id for m in feed.moments] == [m.id for m in detect(swept.game)]
    twist = next(m for m in feed.moments if m.kind == "twist_ending")
    assert twist.audience is not None and twist.audience.text is not None
    assert "Reference bot #" in twist.audience.text
