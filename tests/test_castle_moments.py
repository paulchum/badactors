"""Moments found by the ledger's shape: every detector fires in thirty seeded
bot games, and none fires on a game where what it looks for never happened.
With the fact-checker's verdicts, the moments carry their stamps, and the kinds
only a verdict can find are found."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import pytest

from badactors.castle.bots import HeuristicBot
from badactors.castle.claims import PUBLIC, Claim, MockExtractor, extract
from badactors.castle.contracts import LedgerEntry
from badactors.castle.factcheck import Verdict, verdicts
from badactors.castle.game import GameState
from badactors.castle.moments import (
    CLAIMS,
    LABELS,
    LEDGER_KINDS,
    STAMPS,
    WORST,
    Moment,
    MomentKind,
    detect,
    numbered,
    stamp,
)
from badactors.castle.play import record, run_game, start_game
from badactors.castle.rules import GameConfig
from badactors.castle.show import audience_feed, checked
from badactors.castle.sweep import config_for, play_seed
from castle_scenes import CAST, CASTLE, MODELS, gaslight_scene, lie_scene

SEEDS = range(30, 60)
"""Thirty consecutive sweep seeds. The bots rarely recruit a player who turns,
or end on a twist, and this run holds both."""


def _unchecked(seed: int) -> tuple[GameState, list[Verdict]]:
    swept = play_seed(seed, check_leaks=False)
    game = swept.game
    return game, verdicts(game, extract(game, MockExtractor()), log=swept.played.log)


@pytest.fixture(scope="module")
def played() -> list[tuple[GameState, list[Verdict]]]:
    with ProcessPoolExecutor() as pool:
        return list(pool.map(_unchecked, SEEDS, chunksize=3))


@pytest.fixture(scope="module")
def games(played: list[tuple[GameState, list[Verdict]]]) -> list[GameState]:
    return [game for game, _ in played]


@pytest.fixture(scope="module")
def found(games: list[GameState]) -> list[list[Moment]]:
    return [detect(game) for game in games]


@pytest.fixture(scope="module")
def judged(played: list[tuple[GameState, list[Verdict]]]) -> list[list[Moment]]:
    return [detect(game, verdicts=found) for game, found in played]


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
        for kind in LEDGER_KINDS:
            if not PRECONDITIONS[kind](game):
                assert kind not in kinds, (game.world.seed, kind)
    assert set(fired) == set(LEDGER_KINDS), set(LEDGER_KINDS) - set(fired)


@pytest.mark.parametrize("which", ["found", "judged"])
def test_every_moment_is_a_well_formed_window_on_the_ledger(
    games: list[GameState], which: str, request: pytest.FixtureRequest
) -> None:
    every: list[list[Moment]] = request.getfixturevalue(which)
    for game, moments in zip(games, every, strict=True):
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
            assert moment.label == LABELS[moment.kind]
            assert moment.checked == (which == "judged")
            if not moment.checked:
                assert moment.verdict is None
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
    assert kinds_with_claims >= set(LEDGER_KINDS) - {"recruit_turned"}
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


def test_verdicts_stamp_each_claim_with_its_worst_ruling(
    played: list[tuple[GameState, list[Verdict]]], judged: list[list[Moment]]
) -> None:
    stamped: Counter[str] = Counter()
    for (game, found), moments in zip(played, judged, strict=True):
        ids = {id(verdict): claim_id for claim_id, verdict in numbered(found)}
        public = [v for v in found if game.world.ledger[v.claim.seq - 1].type in PUBLIC]
        for moment in moments:
            on = [v for v in public if v.claim.seq == moment.claim]
            if not on:
                assert moment.verdict is None and not moment.contradiction
                assert not moment.clip_ready
                continue
            worst = min(on, key=lambda v: WORST.index(stamp(v)))
            ruling = moment.verdict
            assert ruling is not None
            assert (ruling.claim, ruling.stamp, ruling.label) == (
                ids[id(worst)],
                stamp(worst),
                worst.label,
            )
            assert moment.contradiction == (
                ruling.stamp == "COLD-BLOODED LIE" and moment.line is not None
            )
            stamped[ruling.stamp] += 1
    assert {"COLD-BLOODED LIE", "TRUE"} <= set(stamped)


def test_gaslit_means_gave_in_and_every_lie_is_caught_on_tape(
    played: list[tuple[GameState, list[Verdict]]], judged: list[list[Moment]]
) -> None:
    kinds: Counter[str] = Counter()
    for (game, found), moments in zip(played, judged, strict=True):
        gave_in = {
            v.claim.seq
            for v in found
            if v.label == "implanted"
            and v.gave_in is True
            and game.world.ledger[v.claim.seq - 1].type in PUBLIC
        }
        lies = {
            v.claim.seq
            for v in found
            if stamp(v) == "COLD-BLOODED LIE"
            and game.world.ledger[v.claim.seq - 1].type in PUBLIC
        }
        assert {m.claim for m in moments if m.kind == "gaslit"} == gave_in
        assert {m.claim for m in moments if m.kind == "caught_lie"} == lies
        for moment in moments:
            kinds[moment.kind] += 1
            if moment.kind == "gaslit":
                assert moment.notes["gave_in"] == "true"
                assert moment.verdict is not None
                assert moment.verdict.stamp == "GASLIT"
            if moment.kind == "caught_lie" and "called_out" in moment.notes:
                assert moment.stakes == 2
    assert kinds["gaslit"] and kinds["caught_lie"]
    assert not kinds["made_up_rule"], "bots never quote the rules"


def test_without_verdicts_the_moments_say_they_are_ledger_only(
    found: list[list[Moment]],
) -> None:
    for moments in found:
        for moment in moments:
            assert not moment.checked and moment.verdict is None
            assert moment.kind in LEDGER_KINDS
            if moment.kind == "gaslit":
                assert moment.notes["gave_in"] == "unknown"


def test_the_stamps_read_every_class_and_gaslit_only_when_they_gave_in() -> None:
    staged = gaslight_scene()
    game = staged.game
    found = verdicts(game, extract(game, MockExtractor()), log=staged.log)
    lit = next(v for v in found if v.label == "implanted")
    assert lit.gave_in is True and stamp(lit) == "GASLIT"
    forgot = lit.model_copy(update={"gave_in": False})
    assert stamp(forgot) == "IMPLANTED MEMORY"
    assert {label: STAMPS[label] for label in ("lie", "mistake", "invented")} == {
        "lie": "COLD-BLOODED LIE",
        "mistake": "HONEST MISTAKE",
        "invented": "MADE IT UP",
    }
    assert (STAMPS["true"], STAMPS["evasive"]) == ("TRUE", "DODGED")
    moments = detect(game, verdicts=found)
    gaslit = next(m for m in moments if m.kind == "gaslit")
    assert gaslit.claim == lit.claim.seq and gaslit.seats[0] == lit.planted_by
    # The rewind opens on what the target could see for themselves.
    assert gaslit.truth[0] == lit.against[0]


def _ruled(claim: Claim, **verdict: object) -> Verdict:
    return Verdict(
        claim=claim,
        truth="false",
        knowledge="contradicted",
        reason="Planted for the test.",
        **verdict,  # type: ignore[arg-type]
    )


def test_a_rule_quoted_wrong_out_loud_is_a_made_up_rule() -> None:
    staged = lie_scene()
    game = staged.game
    alibi = staged.last("alibi", staged.traitor)
    claim = Claim(
        speaker=staged.traitor,
        seq=alibi.seq,
        kind="rule",
        rule="traitors",
        amount=5,
        text="words",
    )
    found = [_ruled(claim, label="lie")]
    moments = detect(game, verdicts=found)
    rule = next(m for m in moments if m.kind == "made_up_rule")
    assert rule.label == "MADE-UP RULE" and rule.claim == alibi.seq
    assert rule.verdict is not None and rule.verdict.stamp == "COLD-BLOODED LIE"
    welcome = next(e for e in game.world.ledger if e.type == "welcome")
    assert rule.truth == [welcome.seq]
    assert rule.notes == {"claim": f"c{alibi.seq}-1", "rule": "traitors"}


def test_a_planted_belief_said_at_the_table_by_someone_who_forgot() -> None:
    staged = gaslight_scene(recent_limit=4)
    game = staged.game
    found = verdicts(game, extract(game, MockExtractor()), log=staged.log)
    lit = next(v for v in found if v.label == "implanted")
    assert lit.gave_in is not True
    moments = detect(game, verdicts=found)
    assert not [m for m in moments if m.kind == "gaslit"]
    planted = next(m for m in moments if m.kind == "planted_belief")
    assert planted.label == "PLANTED" and planted.claim == lit.claim.seq
    assert planted.verdict is not None
    assert planted.verdict.stamp == "IMPLANTED MEMORY"
    assert planted.seats == [lit.planted_by, lit.claim.speaker]


def test_a_moment_needs_a_stamp_for_a_clip_once_the_verdicts_are_in(
    found: list[list[Moment]], judged: list[list[Moment]]
) -> None:
    plain = sum(m.clip_ready for moments in found for m in moments)
    stamped = [m for moments in judged for m in moments if m.clip_ready]
    assert stamped and all(m.verdict is not None for m in stamped)
    assert plain > 0


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
    stored = record(CASTLE, swept.cast, swept.played)
    feed = audience_feed(stored, checked(stored))
    assert [m.id for m in feed.moments] == [
        m.id for m in detect(swept.game, verdicts=checked(stored))
    ]
    assert all(m.checked for m in feed.moments)
    twist = next(m for m in feed.moments if m.kind == "twist_ending")
    assert twist.audience is not None and twist.audience.text is not None
    assert "Reference bot #" in twist.audience.text
