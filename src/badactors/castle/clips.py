"""``castle-clips``: the props for a claim-versus-truth clip, from a feed.

A clip is made from the audience feed and one of its moments, and from nothing
else: :func:`clip_props` reads no record, replays nothing and asks no model. It
lays out the beats SPEC §8 puts in order, one at a time:

1. **the claim**, in the speaker's words, where and when they said it;
2. **the rewind** to what the ledger shows, in the room where it happened, drawn
   at that room's light: bright light names everyone there, dim light shows
   only their garments, the dark only shapes; beside it, what the witnesses
   actually wrote down, and the entries that prove the verdict, in plain words;
3. **the line**: the speaker's own private line, verbatim;
4. **the stamp**: the claim's verdict as the show stamps it (COLD-BLOODED LIE,
   GASLIT), with the fact-checker's class in the data; a feed that wasn't
   fact-checked stamps the moment's label instead, and says so;
5. **the receipt**: castle, game, seed, tick range and claim, so every clip can
   be checked against its game.

Seats are named as audience media names them, by model, unless asked for
characters; a seat a bot played is a "Reference bot", and a game no model played
says so on every frame. Each beat's length is set here, from its words, so the
render is a function of the props alone.

:func:`highlights` prints the same moments for a terminal: the twist first, if
the game has one, then the best of the rest, in about forty lines.

    uv run castle-game --seed 7 --feed data/feed.json --highlights
    uv run castle-clips data/feed.json --top 1
"""

from __future__ import annotations

import argparse
import math
import textwrap
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field

from badactors.castle.contracts import (
    GARMENT,
    CastlePhase,
    Confidence,
    Contract,
    Lighting,
)
from badactors.castle.episode import REFERENCE_BANNER
from badactors.castle.moments import LineField, title_line
from badactors.castle.show import (
    Feed,
    FeedClaim,
    FeedEvent,
    FeedMoment,
    FeedSeat,
    FeedWitness,
    NameStyle,
    load_feed,
)

FPS = 30
WIDTH = 1080
HEIGHT = 1920

LEDGER_ONLY = "Not fact-checked. This is just what the tape shows."
PROOF_LIMIT = 3
_ROUTINE = frozenset(
    {"plan_formed", "plan_cut", "replan_capped", "wait", "phase_opened"}
)
"""Entries too routine to hold up as proof on screen."""

_PHASES: dict[CastlePhase, str] = {
    "breakfast": "breakfast",
    "mission": "the mission",
    "free_time": "free time",
    "round_table": "the round table",
    "dinner": "dinner",
    "night": "night",
}
_LINES: dict[LineField, str] = {
    "diary": "private diary",
    "reason": "private reason",
    "goal": "private plan",
    "thoughts": "exit interview, unheard by the living",
}
_OWN_SENSES = frozenset({"own"})
WITNESS_LIMIT = 4

BeatName = Literal["claim", "rewind", "line", "stamp", "receipt"]
Look = Literal["named", "garment", "shape"]


class Beat(Contract):
    name: BeatName
    frames: int = Field(ge=1)


class Speaker(Contract):
    name: str
    character: str
    colour: str


class ClaimBeat(Contract):
    speaker: Speaker
    said: str
    """How the claim was made, as narration: "Knox accused Ash:"."""
    words: str
    """The words, exactly, with seats renamed in square brackets."""
    room: str
    day: int
    phase: str
    tick: int


class Figure(Contract):
    """Someone in the room at the moment rewound to, as its light shows them."""

    label: str
    look: Look
    garment: str | None = None
    colour: str | None = None
    """The seat's colour, shown only when the light names them."""
    actor: bool = False
    asleep: bool = False


class Witnessed(Contract):
    by: str
    confidence: Confidence
    text: str


class Proof(Contract):
    """One entry the verdict rests on, in plain words."""

    day: int
    phase: str
    tick: int
    text: str


class TruthBeat(Contract):
    text: str
    """What the ledger records, as narration."""
    words: str | None = None
    """The words written or spoken in it, exactly, if any."""
    room: str
    light: Lighting
    day: int
    phase: str
    tick: int
    figures: list[Figure]
    witnessed: list[Witnessed]
    """What the people there wrote down, light and all."""
    proof: list[Proof] = Field(default_factory=list)
    """What the verdict rests on: what really happened, then what the speaker
    had seen for themselves."""


class LineBeat(Contract):
    speaker: Speaker
    kind: str
    text: str


class Stamp(Contract):
    label: str
    """The stamp: the claim's verdict as the show calls it, or, unchecked, the
    moment's label."""
    verdict: bool
    """Whether the fact-checker gave it, rather than the ledger alone."""
    kind: str | None = None
    """The fact-checker's own class ("lie", "implanted"), for the Method page."""
    moment: str
    """What kind of moment it is ("INNOCENT", "GASLIT")."""
    note: str
    """The verdict in a sentence, or that there was none."""
    headline: str


class Receipt(Contract):
    castle: str
    world_id: str
    seed: int
    moment: str
    claim: str | None = None
    """The claim's id, as the site's receipt links take it."""
    day: int
    first_tick: int
    last_tick: int
    first_seq: int
    last_seq: int
    reference_bots: bool


class ClipProps(Contract):
    """Everything the ClaimVsTruth composition draws, and how long each beat
    lasts."""

    version: Literal[1] = 1
    fps: int = FPS
    width: int = WIDTH
    height: int = HEIGHT
    duration: int = Field(ge=1)
    beats: list[Beat]
    banner: str | None = None
    claim: ClaimBeat
    truth: TruthBeat
    line: LineBeat
    stamp: Stamp
    receipt: Receipt


class NotClipReady(ValueError):
    """The moment lacks a beat a clip needs, most often a private line that
    fits, or a verdict on its claim to stamp. Choose another moment."""


# ---------------------------------------------------------------- the props


def clip_props(feed: Feed, moment_id: str, *, names: NameStyle = "model") -> ClipProps:
    """The props for one moment's clip, from the feed alone."""
    moment = next((m for m in feed.moments if m.id == moment_id), None)
    if moment is None:
        raise KeyError(f"The feed has no moment {moment_id!r}.")
    if not moment.clip_ready or moment.claim is None or moment.line is None:
        raise NotClipReady(
            f"Moment {moment_id!r} has no claim in words, no truth to rewind to, "
            "no private line in the speaker's own words, or no stamp."
        )
    view = _View(feed, names)
    ruled = view.claim(moment)
    claim = view.event(moment.claim)
    truth = view.event(moment.truth[0])
    held = view.event(moment.line.seq)
    speaker = view.speaker(claim.actor or "")
    claim_beat = ClaimBeat(
        speaker=speaker,
        said=view.narration(claim),
        words=view.quote(claim),
        room=view.room_name(claim.room or view.room_at(claim)),
        day=claim.day,
        phase=_PHASES[claim.phase],
        tick=claim.tick,
    )
    room = truth.room or view.room_at(truth) or claim.room or ""
    light = view.light(room)
    truth_beat = TruthBeat(
        text=view.narration(truth),
        words=view.quote(truth) or None,
        room=view.room_name(room),
        light=light,
        day=truth.day,
        phase=_PHASES[truth.phase],
        tick=truth.tick,
        figures=view.figures(truth, room, light),
        witnessed=view.witnessed(truth),
        proof=view.proof(ruled, shown=truth.seq) if ruled is not None else [],
    )
    line_beat = LineBeat(
        speaker=speaker,
        kind=_LINES[moment.line.field],
        text=view.private(held, moment.line.field),
    )
    stamp = Stamp(
        label=ruled.stamp if ruled is not None else moment.label,
        verdict=ruled is not None,
        kind=ruled.label if ruled is not None else None,
        moment=moment.label,
        note=view.why(ruled) if ruled is not None else LEDGER_ONLY,
        headline=view.headline(moment),
    )
    receipt = Receipt(
        castle=feed.game.castle,
        world_id=feed.game.world_id,
        seed=feed.game.seed,
        moment=moment.id,
        claim=ruled.id if ruled is not None else None,
        day=moment.day,
        first_tick=moment.first_tick,
        last_tick=moment.last_tick,
        first_seq=moment.first_seq,
        last_seq=moment.last_seq,
        reference_bots=feed.game.reference_bots,
    )
    beats = [
        Beat(name="claim", frames=_reading(claim_beat.words, floor=120)),
        Beat(
            name="rewind",
            frames=min(
                390,
                150 + 30 * len(truth_beat.witnessed) + 45 * len(truth_beat.proof),
            ),
        ),
        Beat(name="line", frames=_reading(line_beat.text, floor=120)),
        Beat(name="stamp", frames=75),
        Beat(name="receipt", frames=90),
    ]
    return ClipProps(
        duration=sum(beat.frames for beat in beats),
        beats=beats,
        banner=REFERENCE_BANNER if feed.game.reference_bots else None,
        claim=claim_beat,
        truth=truth_beat,
        line=line_beat,
        stamp=stamp,
        receipt=receipt,
    )


def _reading(words: str, *, floor: int) -> int:
    """Frames to type the words out at a character and a half a frame, and
    hold them long enough to read."""
    return max(floor, min(330, 30 + math.ceil(len(words) / 1.5) + 75))


def clip_ready(feed: Feed) -> list[FeedMoment]:
    """The moments a clip can be made from, best first."""
    return [moment for moment in feed.moments if moment.clip_ready]


class _View:
    """The feed, read as one clip names it."""

    def __init__(self, feed: Feed, names: NameStyle) -> None:
        self.feed = feed
        self.by_model = names == "model"
        self.seats = {seat.id: seat for seat in feed.seats}
        self.rooms = {room.id: room for room in feed.rooms}
        self.garments = {t.id: t.name for t in feed.things if t.kind == GARMENT}
        self.claims = {claim.id: claim for claim in feed.claims}

    def event(self, seq: int) -> FeedEvent:
        return self.feed.events[seq - 1]

    def name(self, player_id: str) -> str:
        seat = self.seats[player_id]
        return seat.audience if self.by_model else seat.character

    def names(self, player_ids: Sequence[str]) -> str:
        labels = [self.name(pid) for pid in player_ids]
        if len(labels) <= 1:
            return "".join(labels)
        return ", ".join(labels[:-1]) + " and " + labels[-1]

    def speaker(self, player_id: str) -> Speaker:
        seat: FeedSeat = self.seats[player_id]
        return Speaker(
            name=self.name(player_id), character=seat.character, colour=seat.colour
        )

    def narration(self, event: FeedEvent) -> str:
        named = event.audience
        if self.by_model and named is not None and named.text is not None:
            return named.text
        return event.text

    def quote(self, event: FeedEvent) -> str:
        named = event.audience
        if self.by_model and named is not None and named.quote is not None:
            return named.quote
        return event.quote or ""

    def private(self, event: FeedEvent, field: str) -> str:
        named = event.audience
        if self.by_model and named is not None and field in named.private:
            return named.private[field]
        return event.private[field]

    def claim(self, moment: FeedMoment) -> FeedClaim | None:
        """The claim a moment's stamp is for, if the fact-checker ruled on it."""
        if moment.verdict is None:
            return None
        return self.claims.get(moment.verdict.claim)

    def why(self, claim: FeedClaim) -> str:
        named = claim.audience
        if self.by_model and named is not None and named.why is not None:
            return named.why
        return claim.why

    def headline(self, moment: FeedMoment) -> str:
        named = moment.audience
        if self.by_model and named is not None and named.text is not None:
            return named.text
        return moment.headline

    def witness_text(self, record: FeedWitness) -> str:
        named = record.audience
        if self.by_model and named is not None and named.text is not None:
            return named.text
        return record.text

    def room_name(self, room_id: str | None) -> str:
        return self.rooms[room_id].name if room_id in self.rooms else "the castle"

    def light(self, room_id: str) -> Lighting:
        return self.rooms[room_id].lighting if room_id in self.rooms else "dark"

    def frame_at(self, tick: int) -> dict[str, str | None]:
        """Where everyone stood at the end of a tick."""
        frame = self.feed.positions[min(tick, len(self.feed.positions) - 1)]
        return {pid: where.room for pid, where in frame.seats.items()}

    def room_at(self, event: FeedEvent) -> str | None:
        """The room an event happened in, when the ledger set none: where its
        subject was at the time."""
        subject = (
            event.payload.get("target")
            or event.payload.get("player")
            or event.actor
            or ""
        )
        return self.frame_at(event.tick).get(subject)

    def worn(self, before: int) -> dict[str, str]:
        """Each player's outer garment when an entry was written, from the
        garments' custody."""
        wearer: dict[str, str] = {}
        for event in self.feed.events[: before - 1]:
            thing = event.payload.get("thing_id", "")
            if thing not in self.garments or "where" not in event.payload:
                continue
            if event.payload["where"] == "worn":
                wearer[thing] = event.payload["wearer_id"]
            else:
                wearer.pop(thing, None)
        return {pid: self.garments[thing] for thing, pid in wearer.items()}

    def figures(self, event: FeedEvent, room: str, light: Lighting) -> list[Figure]:
        frame = self.feed.positions[min(event.tick, len(self.feed.positions) - 1)]
        here = [
            pid
            for pid, where in frame.seats.items()
            if where.room == room and where.alive
        ]
        if event.actor is not None and event.actor not in here:
            here.insert(0, event.actor)
        worn = self.worn(event.seq)
        out: list[Figure] = []
        for pid in here:
            garment = worn.get(pid)
            asleep = frame.seats[pid].asleep
            actor = pid == event.actor
            if light == "bright":
                out.append(
                    Figure(
                        label=self.name(pid),
                        look="named",
                        garment=garment,
                        colour=self.seats[pid].colour,
                        actor=actor,
                        asleep=asleep,
                    )
                )
            elif light == "dim":
                out.append(
                    Figure(
                        label=f"someone in a {garment}"
                        if garment
                        else "someone in shirtsleeves",
                        look="garment",
                        garment=garment,
                        actor=actor,
                        asleep=asleep,
                    )
                )
            else:
                out.append(
                    Figure(label="someone", look="shape", actor=actor, asleep=asleep)
                )
        return out

    def proof(self, claim: FeedClaim, *, shown: int) -> list[Proof]:
        """The entries a verdict rests on, as narration: what happened first,
        then what the speaker had seen, then how a planted belief came. The
        one the rewind already shows is left out."""
        groups = [
            [seq for seq in group if self.event(seq).type not in _ROUTINE]
            for group in (claim.proof, claim.knew, claim.path)
        ]
        firsts = [group[0] for group in groups if group]
        seqs = [
            seq
            for seq in dict.fromkeys([*firsts, *groups[0], *groups[1], *groups[2]])
            if seq != shown
        ]
        out: list[Proof] = []
        for seq in seqs[:PROOF_LIMIT]:
            event = self.event(seq)
            quote = textwrap.shorten(self.quote(event), 160, placeholder=" …")
            text = self.narration(event)
            if quote:
                text = f"{text} “{quote}”"
            out.append(
                Proof(
                    day=event.day,
                    phase=_PHASES[event.phase],
                    tick=event.tick,
                    text=text,
                )
            )
        return out

    def witnessed(self, event: FeedEvent) -> list[Witnessed]:
        records = [
            record
            for record in event.witnesses
            if record.sense not in _OWN_SENSES and record.player != event.actor
        ]
        return [
            Witnessed(
                by=self.name(record.player),
                confidence=record.confidence,
                text=self.witness_text(record),
            )
            for record in records[:WITNESS_LIMIT]
        ]


# ---------------------------------------------------------------- highlights

HIGHLIGHT_LINES = 40
WIDTH_CHARS = 96
PER_KIND = 2
"""The most moments of one kind the highlights show, so a game's best stretch
is not five banishments in a row."""


def highlights(
    feed: Feed, *, names: NameStyle = "character", lines: int = HIGHLIGHT_LINES
) -> str:
    """A game's best moments for a terminal, in about ``lines`` lines: the
    episode's title, the twist ending called out first, then the rest by rank,
    each claim with its stamp."""
    view = _View(feed, names)
    game = feed.game
    out: list[str] = []
    if game.reference_bots:
        out.append(REFERENCE_BANNER)
    out.append(_title(view))
    out.append(_cast(view))
    out.append(_result(feed))
    twist = next((m for m in feed.moments if m.kind == "twist_ending"), None)
    if twist is not None:
        out.extend(["", *_block(view, twist, "THE TWIST")])
    out.extend(["", "THE BEST MOMENTS"])
    shown: Counter[str] = Counter()
    said = {twist.claim} if twist is not None else set()
    footer = 2
    for moment in feed.moments:
        if (
            moment is twist
            or shown[moment.kind] >= PER_KIND
            or (moment.claim is not None and moment.claim in said)
        ):
            continue
        block = _block(view, moment, f"{sum(shown.values()) + 1}. {moment.label}")
        if len(out) + len(block) + footer > lines:
            break
        out.extend(block)
        shown[moment.kind] += 1
        said.add(moment.claim)
    checked = "" if game.fact_checked else " None of it is fact-checked yet."
    out.extend(
        [
            "",
            f"{len(feed.moments)} moments in all.{checked} castle-clips turns the "
            "best of them into a clip.",
        ]
    )
    return "\n".join(out)


def _title(view: _View) -> str:
    """ "EPISODE 56 · “End it now, while I'm still here.”", by the diary line
    on the moment the show leads with."""
    best = title_line(view.feed.moments)
    seed = view.feed.game.seed
    if best is None:
        return f"EPISODE {seed}"
    line = best[1]
    return f"EPISODE {seed} · “{view.private(view.event(line.seq), line.field)}”"


def _cast(view: _View) -> str:
    feed = view.feed
    starting = feed.game.starting_traitors
    count = "Two" if len(starting) == 2 else str(len(starting))
    later = "".join(
        f" {view.name(seat.id)} joined them on day {view.event(seat.traitor_from).day}."
        for seat in feed.seats
        if seat.recruited_by is not None and seat.traitor_from is not None
    )
    return (
        f"{feed.game.castle}. {len(feed.seats)} guests. {count} are killers: "
        f"{view.names(starting)}.{later}"
    )


def _result(feed: Feed) -> str:
    game = feed.game
    if game.winner is None:
        return f"Nobody wins. It stopped after day {game.days}."
    side = "The Faithful" if game.winner == "faithful" else "The killers"
    return f"{side} win on day {game.days}."


def _block(view: _View, moment: FeedMoment, title: str) -> list[str]:
    """One moment: its title and when, its headline, and its claim, the claim's
    stamp and the private line when it has them."""
    indent = " " * 4
    ticks = (
        f"tick {moment.first_tick}"
        if moment.first_tick == moment.last_tick
        else f"ticks {moment.first_tick}-{moment.last_tick}"
    )
    lines = [f"{title} · day {moment.day} · {ticks}"]
    lines.extend(_wrapped(view.headline(moment), indent, 2))
    if moment.claim is not None:
        claim = view.event(moment.claim)
        who = view.name(claim.actor or "")
        lines.extend(_wrapped(f"{who}: “{view.quote(claim)}”", indent, 1))
    if moment.line is not None:
        held = view.event(moment.line.seq)
        words = view.private(held, moment.line.field)
        who = view.name(held.actor or "")
        lines.extend(_wrapped(f"⟨{who}, privately: {words}⟩", indent, 2))
    ruled = view.claim(moment)
    if ruled is not None:
        lines.extend(_wrapped(f"{ruled.stamp}. {view.why(ruled)}", indent, 1))
    return lines


def _wrapped(text: str, indent: str, most: int) -> list[str]:
    return textwrap.wrap(
        text,
        width=WIDTH_CHARS,
        initial_indent=indent,
        subsequent_indent=indent,
        max_lines=most,
        placeholder=" …",
    )


# ----------------------------------------------------------------------- CLI


def write_props(props: ClipProps, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(props.model_dump_json(indent=1))
    return target


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="castle-clips",
        description="Write claim-versus-truth clip props from an audience feed.",
    )
    parser.add_argument("feed", help="An audience feed, from castle-game --feed.")
    parser.add_argument(
        "--top", type=int, default=1, help="Props for the best N moments a clip fits."
    )
    parser.add_argument("--moment", help="Props for this moment id instead.")
    parser.add_argument("--out", default="data/clips", help="Directory for the props.")
    parser.add_argument(
        "--character-names",
        action="store_true",
        help="Name seats by character, as the players know them, not by model.",
    )
    args = parser.parse_args(argv)

    feed = load_feed(args.feed)
    names: NameStyle = "character" if args.character_names else "model"
    if args.moment:
        chosen = [args.moment]
    else:
        chosen = [moment.id for moment in clip_ready(feed)[: args.top]]
    if not chosen:
        parser.exit(1, "No moment in this feed has every beat a clip needs.\n")
    for moment_id in chosen:
        try:
            props = clip_props(feed, moment_id, names=names)
        except (KeyError, NotClipReady) as exc:
            parser.exit(1, f"{exc}\n")
        path = write_props(
            props,
            Path(args.out) / f"{feed.game.world_id}-{feed.game.seed}-{moment_id}.json",
        )
        print(f"{moment_id}: {props.stamp.headline}")
        print(f"  props: {path}  ({props.duration} frames at {props.fps} fps)")
        print(
            "  render: cd clips/render && npx remotion render src/Root.tsx "
            f"ClaimVsTruth out/{moment_id}.mp4 --props={path.resolve()}"
        )


if __name__ == "__main__":
    main()


__all__ = [
    "FPS",
    "HEIGHT",
    "HIGHLIGHT_LINES",
    "LEDGER_ONLY",
    "PROOF_LIMIT",
    "REFERENCE_BANNER",
    "WIDTH",
    "Beat",
    "ClaimBeat",
    "ClipProps",
    "Figure",
    "LineBeat",
    "NotClipReady",
    "Proof",
    "Receipt",
    "Speaker",
    "Stamp",
    "TruthBeat",
    "Witnessed",
    "clip_props",
    "clip_ready",
    "highlights",
    "main",
    "write_props",
]
