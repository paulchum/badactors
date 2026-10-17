"""Clip props: everything a claim-versus-truth clip draws comes from the feed
and one of its moments, and from nothing else."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from badactors.castle import clips, play, show
from badactors.castle.cli import main as castle_game
from badactors.castle.clips import (
    HIGHLIGHT_LINES,
    LEDGER_ONLY,
    PER_KIND,
    REFERENCE_BANNER,
    ClipProps,
    NotClipReady,
    clip_props,
    clip_ready,
    highlights,
    main,
)
from badactors.castle.play import record
from badactors.castle.show import Feed, audience_feed, checked, load_feed, save_feed
from badactors.castle.sweep import play_seed
from castle_scenes import CAST, CASTLE

SEEDS = (34, 41, 44, 56)
"""Bot games whose clips rewind into bright, dim and dark rooms."""


def _feed(seed: int) -> Feed:
    swept = play_seed(seed, check_leaks=False)
    stored = record(CASTLE, swept.cast, swept.played)
    return audience_feed(stored, checked(stored))


AUDIENCE_BANNED = re.compile(
    r"\b(?:ledger|seq|entry|entries|scoped?|verdicts?)\b", re.I
)
"""Builders' words, which nothing the audience reads may use."""


@pytest.fixture(scope="module")
def feeds() -> Iterator[list[Feed]]:
    with ProcessPoolExecutor() as pool:
        yield list(pool.map(_feed, SEEDS))


@pytest.fixture(scope="module")
def props(feeds: list[Feed]) -> list[tuple[Feed, ClipProps]]:
    return [
        (feed, clip_props(feed, moment.id))
        for feed in feeds
        for moment in clip_ready(feed)
    ]


def test_the_feed_is_the_only_input_the_props_need(
    feeds: list[Feed], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = feeds[0]
    path = tmp_path / "feed.json"
    save_feed(feed, path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("Clip props read only the feed.")

    for module, attr in (
        (play, "replay"),
        (play, "load_record"),
        (show, "replay"),
        (show, "audience_feed"),
    ):
        monkeypatch.setattr(module, attr, refuse)
    loaded = load_feed(path)
    for moment in clip_ready(feed)[:5]:
        assert clip_props(loaded, moment.id) == clip_props(feed, moment.id)


def test_every_clip_runs_its_beats_one_at_a_time_in_order(
    props: list[tuple[Feed, ClipProps]],
) -> None:
    assert props
    for _, clip in props:
        assert [beat.name for beat in clip.beats] == [
            "claim",
            "rewind",
            "line",
            "stamp",
            "receipt",
        ]
        assert clip.duration == sum(beat.frames for beat in clip.beats)
        assert (clip.width, clip.height, clip.fps) == (1080, 1920, 30)


def test_a_bot_game_says_so_and_names_no_model(
    props: list[tuple[Feed, ClipProps]],
) -> None:
    for _, clip in props:
        assert clip.banner == REFERENCE_BANNER and clip.receipt.reference_bots
        assert clip.claim.speaker.name.startswith("Reference bot #")
        dumped = clip.model_dump_json()
        for model in CAST.models().values():
            assert model not in dumped
            assert show.model_name(model) not in dumped


def test_the_rewind_is_drawn_at_the_rooms_light(
    props: list[tuple[Feed, ClipProps]],
) -> None:
    lights: set[str] = set()
    for feed, clip in props:
        names = {seat.audience for seat in feed.seats}
        truth = clip.truth
        lights.add(truth.light)
        assert truth.figures, clip.receipt.moment
        assert sum(figure.actor for figure in truth.figures) <= 1
        for figure in truth.figures:
            if truth.light == "bright":
                assert figure.look == "named" and figure.label in names
                assert figure.colour is not None
            elif truth.light == "dim":
                assert figure.look == "garment" and figure.colour is None
                assert figure.label in (
                    f"someone in a {figure.garment}",
                    "someone in shirtsleeves",
                )
            else:
                assert figure.look == "shape" and figure.label == "someone"
                assert figure.garment is None and figure.colour is None
    assert {"bright", "dim", "dark"} <= lights, lights


def _unbracketed(feed: Feed, words: str) -> str:
    for seat in feed.seats:
        words = words.replace(f"[{seat.audience}]", seat.character)
    return words


def test_the_claim_and_the_line_are_the_speakers_own_words(
    props: list[tuple[Feed, ClipProps]],
) -> None:
    for feed, clip in props:
        moment = next(m for m in feed.moments if m.id == clip.receipt.moment)
        assert moment.claim is not None and moment.line is not None
        said = feed.events[moment.claim - 1]
        held = feed.events[moment.line.seq - 1]
        assert held.actor == said.actor
        assert _unbracketed(feed, clip.claim.words) == said.quote
        assert _unbracketed(feed, clip.line.text) == held.private[moment.line.field]
        assert clip.line.speaker == clip.claim.speaker


def test_every_clip_ends_with_its_receipt(props: list[tuple[Feed, ClipProps]]) -> None:
    for feed, clip in props:
        moment = next(m for m in feed.moments if m.id == clip.receipt.moment)
        receipt = clip.receipt
        assert (receipt.seed, receipt.world_id, receipt.castle) == (
            feed.game.seed,
            feed.game.world_id,
            feed.game.castle,
        )
        assert (receipt.first_tick, receipt.last_tick) == (
            moment.first_tick,
            moment.last_tick,
        )
        assert (receipt.first_seq, receipt.last_seq) == (
            moment.first_seq,
            moment.last_seq,
        )
        ruling = moment.verdict
        assert ruling is not None
        claim = next(c for c in feed.claims if c.id == ruling.claim)
        assert receipt.claim == claim.id and clip.stamp.verdict
        assert (clip.stamp.label, clip.stamp.kind) == (claim.stamp, claim.label)
        assert clip.stamp.moment == moment.label
        named = claim.audience.why if claim.audience else None
        assert clip.stamp.note == (named or claim.why)


def test_the_rewind_shows_the_proof_in_plain_words(
    props: list[tuple[Feed, ClipProps]],
) -> None:
    proved = 0
    for _, clip in props:
        assert len(clip.truth.proof) <= clips.PROOF_LIMIT
        proved += bool(clip.truth.proof)
        for proof in clip.truth.proof:
            assert proof.text and not AUDIENCE_BANNED.search(proof.text)
    assert proved


def test_nothing_the_audience_reads_on_a_clip_uses_a_builders_word(
    props: list[tuple[Feed, ClipProps]],
) -> None:
    for _, clip in props:
        shown = [
            clip.banner or "",
            clip.claim.said,
            clip.truth.text,
            clip.line.kind,
            clip.stamp.label,
            clip.stamp.note,
            clip.stamp.headline,
        ]
        for text in shown:
            assert not AUDIENCE_BANNED.search(text), text


def test_a_feed_that_was_not_fact_checked_stamps_the_moment_and_says_so(
    feeds: list[Feed],
) -> None:
    swept = play_seed(SEEDS[0], check_leaks=False)
    plain = audience_feed(record(CASTLE, swept.cast, swept.played))
    moment = clip_ready(plain)[0]
    clip = clip_props(plain, moment.id)
    assert clip.stamp.label == moment.label and not clip.stamp.verdict
    assert clip.stamp.note == LEDGER_ONLY and clip.receipt.claim is None
    assert clip.truth.proof == []


def test_a_moment_without_a_fitting_line_makes_no_clip(feeds: list[Feed]) -> None:
    feed = feeds[0]
    unready = next(m for m in feed.moments if not m.clip_ready)
    with pytest.raises(NotClipReady):
        clip_props(feed, unready.id)
    with pytest.raises(KeyError):
        clip_props(feed, "no-such-moment")


def test_character_names_on_request(feeds: list[Feed]) -> None:
    feed = feeds[0]
    moment = clip_ready(feed)[0]
    clip = clip_props(feed, moment.id, names="character")
    characters = {seat.character for seat in feed.seats}
    assert clip.claim.speaker.name in characters
    assert "Reference bot #" not in clip.claim.words + clip.line.text
    assert clip.banner == REFERENCE_BANNER


def test_castle_clips_writes_props_for_the_best_moments(
    feeds: list[Feed], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    feed = feeds[1]
    path = tmp_path / "feed.json"
    save_feed(feed, path)
    out = tmp_path / "clips"
    main([str(path), "--top", "2", "--out", str(out)])
    written = sorted(out.iterdir())
    best = [m.id for m in clip_ready(feed)[:2]]
    assert [p.name for p in written] == sorted(
        f"{feed.game.world_id}-{feed.game.seed}-{moment}.json" for moment in best
    )
    for file in written:
        ClipProps.model_validate(json.loads(file.read_text()))
    printed = capsys.readouterr().out
    assert "npx remotion render src/Root.tsx ClaimVsTruth" in printed
    unready = next(m for m in feed.moments if not m.clip_ready)
    with pytest.raises(SystemExit) as stopped:
        main([str(path), "--moment", unready.id, "--out", str(out)])
    assert stopped.value.code == 1


def test_the_composition_reads_the_props_python_writes() -> None:
    """The TypeScript props type names every field ClipProps has."""
    source = (
        Path(__file__).resolve().parent.parent / "clips/render/src/props.ts"
    ).read_text()
    for model in (
        clips.ClipProps,
        clips.ClaimBeat,
        clips.TruthBeat,
        clips.Figure,
        clips.Witnessed,
        clips.LineBeat,
        clips.Proof,
        clips.Stamp,
        clips.Receipt,
        clips.Speaker,
        clips.Beat,
    ):
        for field in model.model_fields:
            assert re.search(rf"\b{field}\??:", source), (model.__name__, field)


# ------------------------------------------------------------------ highlights


def test_highlights_call_out_the_twist_then_the_best_of_the_rest(
    feeds: list[Feed],
) -> None:
    feed = feeds[SEEDS.index(56)]
    shown = highlights(feed)
    lines = shown.splitlines()
    assert len(lines) <= HIGHLIGHT_LINES
    assert lines[0] == REFERENCE_BANNER
    assert lines[1] == "EPISODE 56 · “End it now, while I'm still here.”"
    assert lines[3] == "The killers win on day 4."
    assert not AUDIENCE_BANNED.search(shown)
    stamps = {claim.stamp for claim in feed.claims}
    assert any(line.strip().split(". ")[0] in stamps for line in lines)
    twist = lines.index(next(line for line in lines if line.startswith("THE TWIST")))
    assert twist < lines.index("THE BEST MOMENTS")
    assert "still among them" in "\n".join(lines[twist : twist + 3])
    labels = Counter(
        line.split(" · ")[0].split(". ", 1)[1]
        for line in lines
        if re.match(r"^\d+\. ", line)
    )
    assert labels and max(labels.values()) <= PER_KIND
    named = highlights(feed, names="model")
    assert "Reference bot #" in named and "[Reference bot #" in named


def test_castle_game_leads_with_the_highlights(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "feed.json"
    castle_game(["--seed", "56", "--feed", str(path), "--highlights"])
    out = capsys.readouterr().out
    assert len(out.splitlines()) <= HIGHLIGHT_LINES + 1
    assert "THE TWIST" in out and "══ DAY 1" not in out
    assert out.splitlines()[-1] == f"Saved the audience feed to {path}."
