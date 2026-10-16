"""Verdicts: each claim against the ledger, and against what its speaker knew.

The planted scenes (``castle_scenes``) each script one first day so that one
claim of each class is made: a lie, a repeated noisy tally and a repeated
forged note (mistakes), a gaslight that took and a rumour relayed three hops
(implanted), a bluff, and an evasive alibi. The properties hold over a whole
bot game: a claim read straight off the ledger by the player who did it is
true and consistent, and nobody is ever truly placed in a room they never set
foot in.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from badactors.castle.claims import PUBLIC, Claim, MockExtractor, act_of, extract
from badactors.castle.cli import main as castle_game
from badactors.castle.contracts import DAY_ORDER, HeldBy, LedgerEntry, WornBy
from badactors.castle.factcheck import (
    Book,
    Verdict,
    _aimed,
    decisions_by_entry,
    printout,
    rule_facts,
    verdicts,
)
from badactors.castle.game import name, was_traitor_at
from badactors.castle.ledger import CUSTODY_TYPES, placement_of
from badactors.castle.metrics import per_player
from badactors.castle.observe import rules_for
from badactors.castle.play import Played
from badactors.castle.sweep import play_seed
from castle_scenes import (
    SHEET,
    Staged,
    bluff_scene,
    evasive_scene,
    forged_note_scene,
    gaslight_scene,
    lie_scene,
    misreported,
    rumour_scene,
    tally_scene,
    tampered_log_scene,
)


def judged(staged: Staged) -> list[Verdict]:
    return verdicts(staged.game, extract(staged.game, MockExtractor()), log=staged.log)


def verdict_on(found: list[Verdict], entry: LedgerEntry, kind: str) -> Verdict:
    matching = [
        verdict
        for verdict in found
        if verdict.claim.seq == entry.seq and verdict.claim.kind == kind
    ]
    assert len(matching) == 1, [verdict.claim for verdict in found]
    return matching[0]


# ------------------------------------------------------------ planted scenes


def test_a_lie_is_false_and_contradicted_by_what_they_knew() -> None:
    staged = lie_scene()
    alibi = staged.last("alibi", staged.traitor)
    verdict = verdict_on(judged(staged), alibi, "location")
    assert verdict.claim.room == "library"
    assert (verdict.truth, verdict.knowledge, verdict.label) == (
        "false",
        "contradicted",
        "lie",
    )
    assert verdict.against
    assert verdict.in_view is True


def test_repeating_the_clerks_rough_tally_is_a_mistake() -> None:
    staged = tally_scene()
    worker, coins = misreported(staged.game) or ("", 0)
    question = staged.last("question", staged.faithful[3])
    verdict = verdict_on(judged(staged), question, "tally")
    assert (verdict.claim.subject, verdict.claim.amount) == (worker, coins)
    assert (verdict.truth, verdict.knowledge, verdict.label) == (
        "false",
        "consistent",
        "mistake",
    )
    result = next(
        entry for entry in staged.game.world.ledger if entry.type == "mission_result"
    )
    assert verdict.grounds == [result.seq]


def test_repeating_a_forged_note_is_a_mistake() -> None:
    staged = forged_note_scene()
    hand, reader = staged.faithful[0], staged.faithful[1]
    forged = staged.last("write", staged.traitor)
    assert forged.payload["signed"] == hand
    report = staged.last("report", reader)
    verdict = verdict_on(judged(staged), report, "action")
    claim = verdict.claim
    assert (claim.subject, claim.act, claim.thing) == (hand, "write", SHEET)
    assert (verdict.truth, verdict.knowledge, verdict.label) == (
        "false",
        "consistent",
        "mistake",
    )
    assert verdict.planted_by is None
    assert staged.last("read", reader).seq in verdict.grounds


def test_a_gaslight_that_took_is_implanted_and_the_victim_gave_in() -> None:
    staged = gaslight_scene()
    victim = staged.faithful[1]
    gaslight = staged.last("whisper", staged.traitor)
    alibi = staged.last("alibi", victim)
    found = judged(staged)
    verdict = verdict_on(found, alibi, "location")
    assert (verdict.claim.room, verdict.claim.target) == ("library", staged.traitor)
    assert (verdict.truth, verdict.label) == ("false", "implanted")
    assert verdict.planted_by == staged.traitor
    assert verdict.path == [gaslight.seq]
    assert verdict.knowledge == "contradicted"
    assert verdict.gave_in is True
    stats = per_player(staged.game, found)
    assert stats[staged.traitor].planted == 1
    assert (stats[victim].implanted_in, stats[victim].gave_in) == (1, 1)


def test_a_victim_whose_memory_had_gone_did_not_give_in() -> None:
    staged = gaslight_scene(recent_limit=8)
    alibi = staged.last("alibi", staged.faithful[1])
    verdict = verdict_on(judged(staged), alibi, "location")
    assert verdict.label == "implanted"
    assert verdict.against
    assert verdict.gave_in is False


def test_a_rumour_relayed_three_hops_names_its_origin() -> None:
    staged = rumour_scene()
    subject, chain = staged.faithful[0], staged.faithful[1:5]
    start = staged.last("whisper", staged.traitor)
    relays = [staged.last("whisper", teller) for teller in chain[:3]]
    accusation = staged.last("accuse", chain[-1])
    found = judged(staged)
    verdict = verdict_on(found, accusation, "location")
    assert (verdict.claim.subject, verdict.claim.room) == (subject, "library")
    assert (verdict.truth, verdict.label) == ("false", "implanted")
    assert verdict.planted_by == staged.traitor
    assert verdict.path == [start.seq, *(relay.seq for relay in relays)]
    assert all(relay.payload["rumor"] == str(start.seq) for relay in relays)
    relayed = {relay.seq for relay in relays}
    assert not any(verdict.claim.seq in relayed for verdict in found)


def test_a_line_tampered_into_a_log_is_traced_to_the_tamperer() -> None:
    staged = tampered_log_scene()
    subject, reader = staged.faithful[0], staged.faithful[1]
    tamper = staged.last("write", staged.traitor)
    assert tamper.payload["tool"] == "tamper"
    read = staged.last("read", reader)
    assert read.seq > tamper.seq
    backdated = int(tamper.payload["tick"])
    claim = Claim(
        speaker=reader,
        seq=staged.last("vote", reader).seq,
        kind="location",
        subject=subject,
        room="chapel",
        tick_from=backdated,
        tick_to=backdated,
        text="The guest book has her in the Chapel then.",
    )
    [verdict] = verdicts(staged.game, [claim], log=staged.log)
    assert (verdict.truth, verdict.label) == ("false", "implanted")
    assert verdict.planted_by == staged.traitor
    assert verdict.path == [tamper.seq, read.seq]


def test_a_bluff_is_invented_and_the_diary_owns_up() -> None:
    staged = bluff_scene()
    accusation = staged.last("accuse", staged.traitor)
    verdict = verdict_on(judged(staged), accusation, "location")
    assert (verdict.truth, verdict.knowledge, verdict.label) == (
        "false",
        "consistent",
        "invented",
    )
    assert verdict.invented_as == "bluff"
    assert verdict.planted_by is None and not verdict.grounds


def test_an_invention_the_diary_believes_is_a_confabulation() -> None:
    staged = bluff_scene(diary="I am certain of it. I know what I saw.")
    accusation = staged.last("accuse", staged.traitor)
    verdict = verdict_on(judged(staged), accusation, "location")
    assert (verdict.label, verdict.invented_as) == ("invented", "confabulation")


def test_an_alibi_true_only_of_a_moment_is_evasive() -> None:
    staged = evasive_scene()
    alibi = staged.last("alibi", staged.traitor)
    verdict = verdict_on(judged(staged), alibi, "location")
    assert verdict.claim.room == "great_hall"
    assert (verdict.truth, verdict.label) == ("true", "evasive")


def test_a_traitor_calling_themselves_faithful_lies_in_full_view() -> None:
    staged = lie_scene()
    found = judged(staged)
    claim = Claim(
        speaker=staged.traitor,
        seq=staged.last("alibi", staged.traitor).seq,
        kind="role",
        subject=staged.traitor,
        role="faithful",
        text="I am Faithful.",
    )
    [verdict] = verdicts(staged.game, [claim], log=staged.log)
    assert (verdict.label, verdict.in_view) == ("lie", True)
    honest = claim.model_copy(update={"role": "traitor"})
    [verdict] = verdicts(staged.game, [honest], log=staged.log)
    assert verdict.label == "true"
    assert found


# ---------------------------------------------------------------- the rules


def test_rule_facts_are_what_the_rulebook_says() -> None:
    game = lie_scene().game
    facts = rule_facts(game)
    rules = rules_for(game)
    assert f"{facts['traitors']} of you begin as traitors" in rules
    assert f"one of {facts['players']} guests" in rules
    assert f"on day\n  {facts['max_days']}" in rules or (
        f"on day {facts['max_days']}" in rules
    )
    assert f"when {facts['endgame_at']} or fewer remain" in rules
    assert "only speak and vote if you are at the table" in rules
    assert "even if they carry a shield" in rules
    assert facts["shield_stops_poison"] is False
    assert facts["absent_may_vote"] is False


def test_a_false_rule_claim_is_a_lie_the_rulebook_was_there_to_stop() -> None:
    staged = lie_scene()
    seq = staged.last("alibi", staged.traitor).seq
    base = {"speaker": staged.traitor, "seq": seq, "text": "words", "kind": "rule"}
    claims = [
        Claim(rule="traitors", amount=3, **base),
        Claim(rule="traitors", amount=2, **base),
        Claim(rule="vote_restricted", **base),
        Claim(rule="per_day", act="rumor", amount=2, **base),
        Claim(rule="per_day", act="search", amount=1, **base),
    ]
    found = verdicts(staged.game, claims, log=staged.log)
    assert [verdict.label for verdict in found] == [
        "lie",
        "true",
        "lie",
        "true",
        "lie",
    ]
    assert all(verdict.in_view for verdict in found if verdict.label == "lie")


# ------------------------------------------------------- properties of a game


@pytest.fixture(scope="module")
def played() -> Played:
    return play_seed(11, check_leaks=False).played


def test_every_accepted_answer_finds_the_entry_it_wrote(played: Played) -> None:
    paired = decisions_by_entry(played.game, played.log)
    accepted = [
        logged
        for logged in played.log
        if logged.decision is not None and not logged.refused
    ]
    assert len(paired) == len(accepted)
    for seq, logged in paired.items():
        entry = played.game.world.ledger[seq - 1]
        assert (entry.actor_id, entry.tick) == (logged.player_id, logged.tick)


PRESENT = frozenset(
    {
        "move_depart",
        "move_arrive",
        "wait",
        "use",
        "take",
        "drop",
        "stow",
        "talk",
        "whisper",
        "search",
        "inspect",
        "read",
        "write",
        "search_person",
        "sleep",
    }
)


def _ledger_claims(played: Played, entry: LedgerEntry) -> list[Claim]:
    """What the player behind an entry could truthfully say of it."""
    game = played.game
    actor = entry.actor_id or ""
    last = len(game.world.ledger)
    base = {
        "speaker": actor,
        "seq": last,
        "text": entry.text,
        "tick_from": entry.tick,
        "tick_to": entry.tick,
    }
    out = [
        Claim(kind="location", subject=actor, room=entry.room_id, **base),
    ]
    act = act_of(entry)
    if act is not None:
        out.append(
            Claim(
                kind="action",
                subject=actor,
                act=act,
                target=_aimed(entry),
                thing=entry.payload.get("thing_id"),
                **base,
            )
        )
    out.append(
        Claim(
            kind="role",
            subject=actor,
            role="traitor" if was_traitor_at(game, actor, last) else "faithful",
            speaker=actor,
            seq=last,
            text=entry.text,
        )
    )
    return out


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_claims_read_off_the_ledger_are_true_and_consistent(
    played: Played, data: st.DataObject
) -> None:
    ledger = played.game.world.ledger
    acts = [
        entry
        for entry in ledger
        if entry.actor_id is not None
        and entry.room_id is not None
        and entry.type in PRESENT
    ]
    entry = data.draw(st.sampled_from(acts))
    for verdict in verdicts(played.game, _ledger_claims(played, entry), log=played.log):
        assert (verdict.truth, verdict.knowledge) == ("true", "consistent"), verdict


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_holdings_read_off_the_ledger_are_true_and_consistent(
    played: Played, data: st.DataObject
) -> None:
    ledger = played.game.world.ledger
    moves = []
    for entry in ledger:
        if entry.type not in CUSTODY_TYPES or "thing_id" not in entry.payload:
            continue
        placement = placement_of(entry)
        if not isinstance(placement, HeldBy | WornBy):
            continue
        record = entry.record_for(placement.player_id)
        if record is not None and record.sense == "own":
            moves.append((entry, placement.player_id))
    entry, holder = data.draw(st.sampled_from(moves))
    claim = Claim(
        speaker=holder,
        seq=len(ledger),
        kind="possession",
        subject=holder,
        thing=entry.payload["thing_id"],
        tick_from=entry.tick,
        tick_to=entry.tick,
        text=entry.text,
    )
    [verdict] = verdicts(played.game, [claim], log=played.log)
    assert (verdict.truth, verdict.knowledge) == ("true", "consistent"), verdict


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_nobody_is_truly_placed_where_they_never_went(
    played: Played, data: st.DataObject
) -> None:
    game = played.game
    world = game.world
    book = Book(game, {})
    visited = {
        pid: {room for _, _, room in book.changes[pid] if room}
        | ({book.start[pid]} if book.start[pid] else set())
        for pid in world.seats
    }
    pid = data.draw(st.sampled_from(world.seats))
    never = sorted({room.id for room in world.map.rooms} - visited[pid])
    if not never:
        return
    room = data.draw(st.sampled_from(never))
    speaker = data.draw(st.sampled_from(world.seats))
    when = data.draw(
        st.one_of(
            st.just({}),
            st.builds(
                lambda day, phase: {"day": day, "phase": phase},
                st.integers(1, world.day + 1),
                st.sampled_from(DAY_ORDER),
            ),
            st.builds(lambda day: {"day": day}, st.integers(1, world.day)),
        )
    )
    claim = Claim(
        speaker=speaker,
        seq=len(world.ledger),
        kind="location",
        subject=pid,
        room=room,
        text="They were there.",
        **when,
    )
    [verdict] = verdicts(game, [claim], log=played.log)
    assert verdict.truth in ("false", "unverifiable")
    assert verdict.label not in ("true", "evasive")


# ------------------------------------------------------------------- printing


def test_the_printout_shows_every_claim_made_aloud_and_nothing_else() -> None:
    staged = gaslight_scene()
    found = judged(staged)
    text = printout(staged.game, found)
    aloud = [
        verdict
        for verdict in found
        if staged.game.world.ledger[verdict.claim.seq - 1].type in PUBLIC
    ]
    assert len(aloud) < len(found)
    assert text.count("→") == len(aloud)
    assert f"IMPLANTED by {name(staged.game, staged.traitor)}, gave in" in text


def test_castle_game_prints_the_verdicts(capsys: pytest.CaptureFixture[str]) -> None:
    castle_game(["--seed", "7", "--max-days", "2", "--verdicts"])
    out = capsys.readouterr().out
    assert "FACT CHECK" in out and "PLAYERS" in out
    assert "→ LIE" in out and "→ TRUE" in out
