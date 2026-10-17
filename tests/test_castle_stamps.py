"""The show's stamps, their inks and the moments' labels: one copy, SPEC §8's.

``castle/moments.py`` holds them and ``castle/show.py`` hands them on
(``vocabulary``). These tests hold that copy to SPEC.md word for word, and the
clips to it: the stamp in a clip's props and on a share card is printed in its
stamp's ink. The site's viewer and standings are held to the same copy by
``frontend/src/stamps.test.tsx``, through the ``show.json`` the feed export
writes from ``vocabulary``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from badactors.castle.clips import PALETTE, clip_props, clip_ready, receipt_card
from badactors.castle.moments import KINDS, LABELS, STAMPS, TONES, WORST, Stamp
from badactors.castle.play import record
from badactors.castle.show import Feed, audience_feed, checked, vocabulary
from badactors.castle.sweep import play_seed
from castle_scenes import CASTLE

SPEC = Path(__file__).resolve().parent.parent / "SPEC.md"


def spec_words() -> tuple[dict[str, str], list[str]]:
    """SPEC §8's stamps with the ink each is printed in, and its moment labels,
    read from the line that lists them."""
    [line] = [
        line
        for line in SPEC.read_text().splitlines()
        if line.strip().startswith("- Stamps, big")
    ]
    listed = line.split("damning: ", 1)[1]
    stamps, labels = listed.split(" Claims get stamps; moments get labels: ")
    inks: dict[str, str] = {}
    for group in re.sub(r" \([^)]*\)", "", stamps.rstrip(".")).split("; "):
        names, tone = group.rsplit(" in ", 1)
        for name in re.split(r", | and ", names):
            inks[name] = tone
    return inks, labels.split(". The technical class")[0].split(", ")


def test_the_stamps_and_their_inks_are_spec_s_exactly() -> None:
    inks, _ = spec_words()
    assert TONES == inks
    assert set(get_args(Stamp)) == set(inks)
    assert set(STAMPS.values()) | {"GASLIT"} == set(inks)
    assert sorted(WORST) == sorted(inks)
    assert set(PALETTE) == set(inks.values())


def test_the_moment_labels_are_spec_s_exactly() -> None:
    _, labels = spec_words()
    assert [LABELS[kind] for kind in KINDS] == labels
    assert len(set(labels)) == len(labels)


def test_the_vocabulary_hands_on_the_one_copy() -> None:
    words = vocabulary()
    assert words["stamps"] == [
        {"stamp": name, "tone": TONES[name]} for name in get_args(Stamp)
    ]
    assert words["classes"] == STAMPS
    assert words["gave_in"] == "GASLIT"
    assert words["worst"] == list(WORST)
    assert words["labels"] == LABELS


@pytest.fixture(scope="module")
def feed() -> Feed:
    swept = play_seed(56, check_leaks=False)
    stored = record(CASTLE, swept.cast, swept.played)
    return audience_feed(stored, checked(stored))


def test_clips_and_cards_print_each_stamp_in_its_ink(feed: Feed) -> None:
    ready = clip_ready(feed)
    assert ready
    for moment in ready:
        stamp = clip_props(feed, moment.id).stamp
        assert stamp.verdict
        assert stamp.colour == PALETTE[TONES[stamp.label]]
    tones = {TONES[claim.stamp] for claim in feed.claims}
    assert {"red", "green"} <= tones
    for claim in feed.claims:
        card = receipt_card(feed, claim, names="model")
        assert f'fill="{PALETTE[TONES[claim.stamp]]}"' in card
