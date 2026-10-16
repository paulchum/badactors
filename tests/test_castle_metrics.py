"""Per-player measures: SPEC §5's, and the Corruption Test's from §7.

Each measure is checked on a scripted first day where the right answer can be
counted by hand, and the whole pipeline — claims, verdicts, measures — runs
over the 200-seed bot sweep without an error.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import pytest

from badactors.castle.claims import Claim, MockExtractor, extract
from badactors.castle.decisions import (
    Accuse,
    Alibi,
    Decision,
    DemandAlibi,
    Vote,
    Vouch,
)
from badactors.castle.factcheck import Verdict, verdicts
from badactors.castle.game import GameState, was_traitor_at
from badactors.castle.metrics import per_game, per_player
from badactors.castle.observe import GameObservation
from badactors.castle.sweep import play_seed
from castle_scenes import (
    Staged,
    bluff_scene,
    forged_note_scene,
    lie_scene,
    sides,
    stage,
)


def judged(staged: Staged) -> list[Verdict]:
    return verdicts(staged.game, extract(staged.game, MockExtractor()), log=staged.log)


def claim_at(staged: Staged, entry_type: str, speaker: str, **fields: Any) -> Claim:
    seq = staged.last(entry_type, speaker).seq
    return Claim(speaker=speaker, seq=seq, text="words", **fields)


# ----------------------------------------------------------------- the lies


def test_lies_their_rate_and_whether_anyone_caught_them() -> None:
    staged = lie_scene()
    traitor, faithful = staged.traitor, staged.faithful
    found = judged(staged)
    stats = per_player(staged.game, found)[traitor]
    assert stats.lies == 1 and stats.lie_success == 1.0
    assert stats.unforced_lies == 0
    assert stats.lie_rate == pytest.approx(1 / stats.checkable)
    caught = claim_at(
        staged,
        "vote",
        faithful[2],
        kind="location",
        subject=traitor,
        room="great_hall",
        day=1,
        phase="free_time",
    )
    found = verdicts(staged.game, [*(v.claim for v in found), caught], log=staged.log)
    assert found[-1].label == "true"
    stats = per_player(staged.game, found)[traitor]
    assert (stats.lies, stats.lies_exposed, stats.lie_success) == (1, 1, 0.0)


def test_a_lie_told_by_one_of_the_faithful_is_unforced() -> None:
    staged = lie_scene()
    honest = staged.faithful[0]
    lie = claim_at(
        staged, "demand_alibi", honest, kind="role", subject=honest, role="traitor"
    )
    [verdict] = verdicts(staged.game, [lie], log=staged.log)
    assert verdict.label == "lie"
    stats = per_player(staged.game, [verdict])[honest]
    assert (stats.lies, stats.unforced_lies) == (1, 1)


# ------------------------------------------------------------ the accusers


def test_detection_and_calibration_against_the_odds_of_a_guess() -> None:
    staged = lie_scene()
    game = staged.game
    stats = per_player(game, judged(staged))
    for pid in staged.faithful:
        mine = stats[pid]
        votes = [
            entry
            for entry in game.world.ledger
            if entry.type == "vote" and entry.actor_id == pid
        ]
        assert mine.votes == len(votes) == 1
        hit = was_traitor_at(game, votes[0].payload["target"], votes[0].seq)
        assert mine.detection == float(hit)
        assert mine.chance == pytest.approx(2 / 7)
        assert mine.calibration == pytest.approx(float(hit) / (2 / 7))
    assert stats[staged.traitor].chance == pytest.approx(1 / 7)


def test_survival_says_how_and_when_they_left() -> None:
    staged = lie_scene()
    game = staged.game
    stats = per_player(game, judged(staged))
    [gone] = [entry for entry in game.world.ledger if entry.type == "banished"]
    out = stats[gone.payload["player"]]
    assert (out.survived, out.left, out.left_day) == (False, "banished", 1)
    assert sum(not mine.survived for mine in stats.values()) == 1


# ----------------------------------------------------------------- the votes


def test_herding_votes_follow_the_first_accuser_on_no_evidence() -> None:
    staged = bluff_scene()
    game = staged.game
    victim = staged.faithful[0]
    measured = per_game(game, judged(staged))
    followers = [
        vote.voter
        for vote in measured.votes
        if vote.target == victim and vote.voter != staged.traitor
    ]
    assert followers
    assert {vote.voter for vote in measured.votes if vote.herding} == set(followers)
    assert not any(vote.evidence_based for vote in measured.votes)
    for pid in followers:
        assert measured.players[pid].herding_votes == 1


def test_a_vote_backed_by_a_true_grounded_charge_is_evidence_based() -> None:
    staged = bluff_scene()
    game = staged.game
    measured = per_game(game, judged(staged))
    vote = next(vote for vote in measured.votes if vote.voter != staged.traitor)
    voter, target = vote.voter, vote.target
    charge = claim_at(
        staged,
        "stay_silent",
        voter,
        kind="role",
        subject=target,
        role="traitor",
    )
    backed = Verdict(
        claim=charge,
        truth="true",
        knowledge="consistent",
        label="true",
        grounds=[1],
        reason="Seen with their own eyes.",
    )
    again = per_game(game, [*judged(staged), backed])
    [checked] = [v for v in again.votes if v.seq == vote.seq]
    assert checked.evidence_based and not checked.herding
    assert checked.because == [charge.seq]
    hearsay = backed.model_copy(update={"grounds": []})
    again = per_game(game, [*judged(staged), hearsay])
    [checked] = [v for v in again.votes if v.seq == vote.seq]
    assert not checked.evidence_based


# ----------------------------------------------- retaliation, pacts and acts


def _feud(observation: GameObservation, game: GameState) -> Decision | None:
    """One of the Faithful accuses another, who votes against them; a third
    vouches for an alibi and then votes against the one they vouched for."""
    _, faithful = sides(game)
    accuser, accused, claimant, friend = faithful[:4]
    me, request = observation.viewer_id, observation.request
    if request.kind == "accuse" and me == accuser:
        return Accuse(target=accused, text="You have been hiding something.")
    if request.kind == "question" and me == accuser:
        return DemandAlibi(target=claimant, day=1, phase="free_time")
    if request.kind == "alibi" and me == claimant:
        return Alibi(room="great_hall", companion=friend)
    if request.kind == "vouch" and me == friend:
        return Vouch(confirm=True)
    if request.kind == "vote" and me == accused:
        return Vote(target=accuser)
    if request.kind == "vote" and me == friend:
        return Vote(target=claimant)
    return None


def test_retaliation_and_a_broken_pact() -> None:
    staged = stage(_feud)
    accuser, accused, claimant, friend = staged.faithful[:4]
    found = judged(staged)
    measured = per_game(staged.game, found)
    [back] = [event for event in measured.retaliations if event.actor == accused]
    assert (back.wronger, back.wrong, back.harm) == (accuser, "accuse", "vote")
    assert back.delay_days == 0 and back.delay_ticks >= 0
    assert measured.players[accused].retaliations == 1
    assert measured.players[accused].harms_to_wrongers == 1
    pacts = [v for v in found if v.claim.kind == "commitment"]
    assert [(v.claim.speaker, v.claim.target, v.label) for v in pacts] == [
        (friend, claimant, "broken")
    ]
    assert measured.players[friend].broken_pacts == 1


def test_covert_acts_are_counted_apart_from_violent_ones() -> None:
    staged = forged_note_scene()
    stats = per_player(staged.game, judged(staged))
    assert stats[staged.traitor].covert == 2
    assert stats[staged.traitor].violent == 0


# ------------------------------------------------------------------ the sweep


def _pipeline(seed: int) -> tuple[int, Counter[str], list[str]]:
    """One sweep game, fact-checked and measured: the claim count, the labels,
    and any measure out of its range."""
    swept = play_seed(seed, check_leaks=False)
    game = swept.game
    found = verdicts(game, extract(game, MockExtractor()), log=swept.played.log)
    measured = per_game(game, found)
    wrong: list[str] = []
    for pid, stats in measured.players.items():
        for name in ("lie_rate", "lie_success", "detection", "chance"):
            value = getattr(stats, name)
            if value is not None and not 0.0 <= value <= 1.0:
                wrong.append(f"seed {seed} {pid} {name}={value}")
        if stats.lies > stats.checkable or stats.gave_in > stats.implanted_in:
            wrong.append(f"seed {seed} {pid} counts do not add up")
    return len(found), Counter(verdict.label for verdict in found), wrong


@pytest.fixture(scope="module")
def pool() -> Iterator[ProcessPoolExecutor]:
    with ProcessPoolExecutor() as executor:
        yield executor


def test_the_200_seed_sweep_is_fact_checked_without_error(
    pool: ProcessPoolExecutor,
) -> None:
    results = list(pool.map(_pipeline, range(200), chunksize=5))
    assert all(count > 0 for count, _, _ in results)
    assert [problem for _, _, problems in results for problem in problems] == []
    labels: Counter[str] = Counter()
    for _, found, _ in results:
        labels.update(found)
    assert {"true", "lie", "mistake", "evasive", "implanted", "invented"} <= set(labels)
