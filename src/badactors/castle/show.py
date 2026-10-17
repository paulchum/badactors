"""The audience feed: one game, laid out for everything the audience watches.

SPEC §8 makes the event log the single source for the viewer and for clips.
:func:`audience_feed` turns a stored :class:`~badactors.castle.play.GameRecord`
into one versioned JSON document that every audience surface reads. It is a
pure function of the record: the record is replayed, which is deterministic, so
the roles, the recruits and the fates come from the rules that played the game,
and the replay is held to the record's own ledger before anything is read.

Each ledger entry becomes one event: its public text and words, its private
fields (a diary line, a reason for silence or for passing, a plan's goal, a
declared intent, what an exit interview told and guessed), every witness record
with its confidence, and every roll with the chance it was rolled against. Beside
the events sit what a viewer needs to draw the game: the rooms and their light,
where every player stood at the end of every tick (walking, asleep and dead
included), each dinner's seating plan, who played each seat, and the game's
moments (``moments``), best first, which is all a clip is made from.

Given the fact-checker's verdicts, the feed also carries every claim made out
loud (``claims``): the words, what they assert, the stamp the audience sees, the
fact-checker's own class and reasons, whether a gaslit speaker gave in or had
lost the memory, where a planted belief started, and the entries that prove it.
The moments then read the verdicts too. The feed is a pure function of the
record and the verdicts; :func:`checked` gives the verdicts the mock extractor's
claims get, which is what bot games are fact-checked with.

Players only ever know characters, but audience media names each seat by the
model that played it (SPEC §8). :class:`Naming` does that renaming for the feed
and the episode alike: narration names the model, and a quote keeps the model's
words exactly, with each character's name replaced by the model's in square
brackets, so the edit shows and the quote still matches the ledger. Seats one
model shares are numbered, and a seat a reference bot played is a "Reference
bot", never a model. The feed carries both names, so a renderer can switch.

The show's words for verdicts and moments live in one place: the stamps, the
ink each is printed in, and the moments' labels, as SPEC §8 lists them.
:func:`vocabulary` hands them to every renderer that is not Python (the site's
viewer and standings check their copies against it, and the clips carry each
stamp's colour in their props).

Nothing here reaches a player. The feed is the audience's, and holds the truth.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, get_args

from pydantic import Field

from badactors.castle.claims import PUBLIC, ClaimKind, MockExtractor, extract
from badactors.castle.contracts import (
    AtRoom,
    Build,
    CastlePhase,
    Channel,
    Confidence,
    Contract,
    Intent,
    LedgerEntry,
    Lighting,
    Sense,
    WorldState,
)
from badactors.castle.factcheck import Knowledge, Label, Truth, Verdict, describe
from badactors.castle.factcheck import verdicts as judge
from badactors.castle.game import GameState
from badactors.castle.interview import ExitWhy, why_out
from badactors.castle.moments import (
    LABELS,
    STAMPS,
    TONES,
    WORST,
    Moment,
    Stamp,
    detect,
    numbered,
    stamp,
)
from badactors.castle.play import (
    GameRecord,
    Outcome,
    ReplayDiverged,
    is_mock,
    is_model,
    replay,
    start_game,
)
from badactors.castle.rules import Role

FEED: Literal["badactors.castle.feed"] = "badactors.castle.feed"
FEED_VERSION: Literal[2] = 2

PlayedBy = Literal["model", "bot", "unknown"]

NameStyle = Literal["character", "model"]
"""How audience media names seats: by character, as the players know them, or
by the model that played each one."""

REFERENCE_BOT = "Reference bot"
UNLABELLED = "Unlabelled seat"

# ---------------------------------------------------------------------- naming

_CASED = {"gpt": "GPT", "glm": "GLM", "deepseek": "DeepSeek", "oss": "OSS"}
_VERSION = re.compile(r"^[a-z]?\d[\d.]*[a-z]?$")


def model_name(model: str) -> str:
    """A model id as the audience reads it: "anthropic/claude-opus-5.5" is
    "Claude Opus 5.5", and "openai/gpt-6.1-sol" is "GPT-6.1 Sol"."""
    slug = model.rpartition("/")[2].partition(":")[0]
    tokens: list[str] = []
    for token in slug.split("-"):
        if token.isdigit() and tokens and tokens[-1].replace(".", "").isdigit():
            tokens[-1] += f".{token}"
        elif token:
            tokens.append(token)
    words: list[str] = []
    for token in tokens:
        if token in _CASED:
            word = _CASED[token]
        elif re.match(r"^o\d", token):
            word = token
        elif _VERSION.match(token):
            word = token.upper()
        else:
            word = token[:1].upper() + token[1:]
        if words and words[-1] == "GPT" and word[:1].isdigit():
            words[-1] += f"-{word}"
        else:
            words.append(word)
    return " ".join(words) or model


_LAB_COLOURS = {
    "anthropic": "#D97757",
    "openai": "#10A37F",
    "google": "#4C8BF5",
    "x-ai": "#C9CED6",
    "deepseek": "#7B8CFF",
    "moonshotai": "#B07CFF",
    "qwen": "#F2B134",
    "z-ai": "#2EC4B6",
    "meta": "#5AA9FF",
    "mistralai": "#FA6E2A",
}
_SPARE_COLOURS = ("#E06C9F", "#9CCB5A", "#56C7E8", "#E8C547", "#C38D6B")
BOT_COLOUR = "#8A8F98"


def seat_colour(agent: str) -> str:
    """The colour a seat is drawn in: its lab's, or a reference bot's grey. Labs
    are told apart by name and colour, never by logo."""
    if not is_model(agent):
        return BOT_COLOUR
    lab = agent.removeprefix("model:").partition("/")[0]
    if lab in _LAB_COLOURS:
        return _LAB_COLOURS[lab]
    return _SPARE_COLOURS[sum(lab.encode()) % len(_SPARE_COLOURS)]


def audience_names(agents: Mapping[str, str], seats: Sequence[str]) -> dict[str, str]:
    """What audience media calls each seat, in seat order: the model that
    played it, a "Reference bot", or an unlabelled seat. Seats that share a
    name are numbered in seat order ("Claude Opus 5.5 #3")."""
    base: dict[str, str] = {}
    for pid in seats:
        agent = agents.get(pid, "")
        if is_model(agent):
            base[pid] = model_name(agent.removeprefix("model:"))
        elif agent.startswith("bot:") or is_mock(agent):
            base[pid] = REFERENCE_BOT
        else:
            base[pid] = UNLABELLED
    shared = Counter(base.values())
    seen: Counter[str] = Counter()
    names: dict[str, str] = {}
    for pid in seats:
        label = base[pid]
        if shared[label] > 1:
            seen[label] += 1
            label = f"{label} #{seen[label]}"
        names[pid] = label
    return names


class Naming:
    """Names every seat one way, in what the audience reads.

    ``labels`` is the name to use for each seat, and ``characters`` its
    character's name, both by seat. Narration names the label outright; inside
    a quote, where the words are a model's own, a character's name becomes the
    label in square brackets. Naming by character changes nothing."""

    def __init__(self, labels: Mapping[str, str], characters: Mapping[str, str]):
        self.labels = dict(labels)
        self.characters = dict(characters)
        self._renamed = {
            self.characters[pid]: label
            for pid, label in self.labels.items()
            if label != self.characters[pid]
        }
        alternatives = "|".join(
            re.escape(name) for name in sorted(self._renamed, key=len, reverse=True)
        )
        self._pattern = (
            re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)") if alternatives else None
        )

    @classmethod
    def of(
        cls,
        characters: Mapping[str, str],
        agents: Mapping[str, str] | None,
        style: NameStyle = "character",
    ) -> Naming:
        """Naming for seats with these characters (in seat order) and agents."""
        if style == "character":
            return cls(labels=characters, characters=characters)
        return cls(
            labels=audience_names(agents or {}, list(characters)),
            characters=characters,
        )

    def name(self, player_id: str) -> str:
        return self.labels[player_id]

    def names(self, player_ids: Sequence[str]) -> str:
        """ "A", "A and B", "A, B and C"."""
        labels = [self.name(pid) for pid in player_ids]
        if len(labels) <= 1:
            return "".join(labels)
        return ", ".join(labels[:-1]) + " and " + labels[-1]

    def narration(self, text: str) -> str:
        """Narration, which may hold quotes in curly marks: names outside the
        quotes are replaced, and names inside them bracketed."""
        if self._pattern is None:
            return text
        out: list[str] = []
        last = 0
        for quoted in _QUOTED.finditer(text):
            out.append(self._swap(text[last : quoted.start()], bracket=False))
            out.append(self._swap(quoted.group(0), bracket=True))
            last = quoted.end()
        out.append(self._swap(text[last:], bracket=False))
        return "".join(out)

    def words(self, text: str) -> str:
        """A model's own words, with every character's name bracketed."""
        return self._swap(text, bracket=True)

    def _swap(self, text: str, *, bracket: bool) -> str:
        if self._pattern is None:
            return text
        renamed = self._renamed
        if bracket:
            return self._pattern.sub(lambda match: f"[{renamed[match[0]]}]", text)
        return self._pattern.sub(lambda match: renamed[match[0]], text)


_QUOTED = re.compile(r"“[^”]*(?:”|$)")


class FeedGame(Contract):
    world_id: str
    castle_id: str
    castle: str
    seed: int
    outcome: Outcome
    rated: bool
    winner: Role | None
    reference_bots: bool
    """No seat was played by a model. Every renderer says so."""
    fact_checked: bool
    """Whether the claims were fact-checked. If not, ``claims`` is empty and the
    moments are ledger-only."""
    days: int = Field(ge=1)
    ticks: int = Field(ge=0)
    pot: int = Field(ge=0)
    payouts: dict[str, int]
    starting_traitors: list[str]


class FeedSeat(Contract):
    """One seat, and who played it. ``model`` is set only when a model did: a
    seat a reference bot played names no model, so its play is never read as
    one's."""

    id: str
    seat: int = Field(ge=1)
    character: str
    """The name the players know."""
    audience: str
    """The name audience media uses: the model, or a "Reference bot"."""
    colour: str
    agent: str
    played_by: PlayedBy
    model: str | None = None
    build: Build
    garment: str
    detail: str | None = None
    role: Role
    """The role the seat ended the game with."""
    traitor_from: int | None = None
    """The entry that made this seat a traitor: the draw, or a recruitment."""
    recruited_by: str | None = None
    fate: ExitWhy
    out_at: int | None = None
    """The entry that took them out of the game."""
    decisions: int = Field(ge=0)
    refused: int = Field(ge=0)
    failed: int = Field(ge=0)


class FeedRoom(Contract):
    id: str
    name: str
    lighting: Lighting
    capacity: int
    camera: bool
    private: bool


class FeedPassage(Contract):
    rooms: list[str]
    travel_ticks: int


class FeedThing(Contract):
    id: str
    kind: str
    name: str


class Named(Contract):
    """Words as audience media prints them, seats named by model, where that
    differs from the ledger's: each field is set only if it changed."""

    text: str | None = None
    quote: str | None = None
    private: dict[str, str] = Field(default_factory=dict)
    told: list[str] | None = None


class FeedWitness(Contract):
    """What one player wrote down of an event, as the light let them."""

    player: str
    sense: Sense
    confidence: Confidence
    channel: Channel
    text: str
    quote: str | None = None
    author: str | None = None
    recorded_actor: str | None = None
    from_room: str | None = None
    noted: bool = False
    audience: Named | None = None


class FeedRoll(Contract):
    """One recorded roll. ``chance`` is what it had to come in under; a roll
    with none picked from a list."""

    cursor: int
    purpose: str
    value: float
    chance: float | None = None
    success: bool | None = None


class FeedEvent(Contract):
    """One ledger entry, as the audience may see all of it."""

    seq: int = Field(ge=1)
    tick: int = Field(ge=0)
    day: int = Field(ge=1)
    phase: CastlePhase
    type: str
    actor: str | None = None
    room: str | None = None
    light: Lighting | None = None
    text: str
    quote: str | None = None
    payload: dict[str, str] = Field(default_factory=dict)
    private: dict[str, str] = Field(default_factory=dict)
    """The actor's own private lines: ``diary``, ``reason``, ``goal``,
    ``thoughts``. Declared intents, told truths and guesses are parsed below."""
    intent: Intent | None = None
    told: list[str] | None = None
    guesses: dict[str, str] | None = None
    witnesses: list[FeedWitness] = Field(default_factory=list)
    rolls: list[FeedRoll] = Field(default_factory=list)
    audience: Named | None = None
    """The same words with seats named by model, where any differ. A renderer
    shows these or the ledger's, as it names the seats."""


class Whereabouts(Contract):
    """Where one player was at the end of a tick: in a room, or on the way
    between two (``origin`` and ``destination``)."""

    room: str | None = None
    origin: str | None = None
    destination: str | None = None
    asleep: bool = False
    alive: bool = True


class Frame(Contract):
    tick: int = Field(ge=0)
    day: int = Field(ge=1)
    phase: CastlePhase
    seats: dict[str, Whereabouts]


class Seating(Contract):
    """One dinner's seats, round the table, in order."""

    day: int = Field(ge=1)
    seq: int = Field(ge=1)
    order: list[str]


Took = Literal["gave_in", "lost_memory"]
"""How a speaker came to hold a planted belief: with a record of the truth in
front of them (gave in), or after it had left their mind (lost the memory)."""


class ClaimNamed(Contract):
    """A claim's words as audience media prints them, seats named by model,
    where that differs: each field is set only if it changed."""

    said: str | None = None
    what: str | None = None
    why: str | None = None


class FeedClaim(Contract):
    """One claim made out loud, and its verdict.

    ``stamp`` is what the audience sees; ``label`` is the fact-checker's class,
    for the Method page. ``proof`` is what really happened, ``knew`` the
    speaker's own records that said otherwise, ``grounds`` those that agreed,
    and ``path`` how a planted belief reached them, its origin first: all by
    event."""

    id: str
    seq: int = Field(ge=1)
    tick: int = Field(ge=0)
    day: int = Field(ge=1)
    phase: CastlePhase
    speaker: str
    kind: ClaimKind
    said: str
    """The speaker's words the claim was read from."""
    what: str
    """What the claim asserts, in plain words."""
    stamp: Stamp
    label: Label
    truth: Truth
    knowledge: Knowledge
    hedged: bool = False
    took: Took | None = None
    in_view: bool | None = None
    """For a lie, or a falsehood the speaker forgot: whether the record against
    it was in front of the speaker."""
    own_plant: int | None = None
    """For a lie: the event in which the speaker planted it themselves."""
    invented_as: Literal["bluff", "confabulation"] | None = None
    planted_by: str | None = None
    origin: int | None = None
    """The event the planted words started in."""
    path: list[int] = Field(default_factory=list)
    proof: list[int] = Field(default_factory=list)
    knew: list[int] = Field(default_factory=list)
    grounds: list[int] = Field(default_factory=list)
    why: str
    """The verdict in a sentence, for the audience."""
    reason: str
    """The fact-checker's own reason."""
    audience: ClaimNamed | None = None


class FeedMoment(Moment):
    """A moment, with its headline as audience media prints it."""

    audience: Named | None = None


class Feed(Contract):
    feed: Literal["badactors.castle.feed"] = FEED
    version: Literal[2] = FEED_VERSION
    game: FeedGame
    seats: list[FeedSeat]
    rooms: list[FeedRoom]
    passages: list[FeedPassage]
    things: list[FeedThing]
    events: list[FeedEvent]
    positions: list[Frame]
    """One frame for every tick of the game, from the first to the last."""
    seatings: list[Seating]
    moments: list[FeedMoment]
    """Every moment the ledger's shape gives, best first (see ``moments``)."""
    claims: list[FeedClaim] = Field(default_factory=list)
    """Every claim made out loud, in the order said, with its verdict."""


# ------------------------------------------------------------------ building


def checked(stored: GameRecord) -> list[Verdict]:
    """The verdicts on a stored game's claims, read by the mock extractor: how
    bot games are fact-checked. No model is asked anything."""
    game = replay(stored)
    return judge(game, extract(game, MockExtractor()), log=stored.decisions)


def audience_feed(
    stored: GameRecord, verdicts: Sequence[Verdict] | None = None
) -> Feed:
    """The audience feed for one stored game, fact-checked if ``verdicts`` are
    given. Same record and verdicts, same feed."""
    game = replay(stored)
    if game.world.ledger != stored.ledger:
        raise ReplayDiverged("The record's ledger is not the one its game writes.")
    start = start_game(
        stored.castle,
        stored.cast,
        seed=stored.seed,
        config=stored.config,
        world_id=stored.world_id,
    ).world
    world = game.world
    ledger = stored.ledger
    naming = Naming.of(
        {pid: world.players[pid].name for pid in world.seats}, stored.agents, "model"
    )
    return Feed(
        game=FeedGame(
            world_id=stored.world_id,
            castle_id=stored.castle.id,
            castle=stored.castle.name,
            seed=stored.seed,
            outcome=stored.outcome,
            rated=stored.rated,
            winner=stored.winner,
            reference_bots=stored.reference_bots,
            fact_checked=verdicts is not None,
            days=world.day,
            ticks=world.tick,
            pot=game.pot,
            payouts=dict(game.payouts),
            starting_traitors=list(game.starting_traitors),
        ),
        seats=_seats(stored, game, naming),
        rooms=[
            FeedRoom(
                id=room.id,
                name=room.name,
                lighting=room.lighting,
                capacity=room.capacity,
                camera=room.camera,
                private=room.private,
            )
            for room in world.map.rooms
        ],
        passages=[
            FeedPassage(rooms=list(passage.rooms), travel_ticks=passage.travel_ticks)
            for passage in world.map.passages
        ],
        things=[
            FeedThing(id=thing.id, kind=thing.kind, name=thing.name)
            for thing in world.map.things
        ],
        events=[_event(world, entry, naming) for entry in ledger],
        positions=positions(start, ledger),
        seatings=[
            Seating(
                day=entry.day,
                seq=entry.seq,
                order=[pid for pid in entry.payload["seating"].split(",") if pid],
            )
            for entry in ledger
            if entry.type == "seating"
        ],
        moments=[
            FeedMoment(
                **moment.model_dump(), audience=named(naming, moment.headline, None)
            )
            for moment in detect(game, verdicts=verdicts)
        ],
        claims=_claims(game, verdicts or (), naming),
    )


def _claims(
    game: GameState, verdicts: Sequence[Verdict], naming: Naming
) -> list[FeedClaim]:
    ledger = game.world.ledger
    out: list[FeedClaim] = []
    for claim_id, verdict in numbered(verdicts):
        claim = verdict.claim
        entry = ledger[claim.seq - 1]
        if entry.type not in PUBLIC:
            continue
        what = describe(game, claim)
        why = _why(game, verdict)
        took: Took | None = None
        if verdict.label == "implanted" and verdict.gave_in is not None:
            took = "gave_in" if verdict.gave_in else "lost_memory"
        renamed = ClaimNamed(
            said=_changed(naming.words(claim.text), claim.text),
            what=_changed(naming.narration(what), what),
            why=_changed(naming.narration(why), why),
        )
        out.append(
            FeedClaim(
                id=claim_id,
                seq=claim.seq,
                tick=entry.tick,
                day=entry.day,
                phase=entry.phase,
                speaker=claim.speaker,
                kind=claim.kind,
                said=claim.text,
                what=what,
                stamp=stamp(verdict),
                label=verdict.label,
                truth=verdict.truth,
                knowledge=verdict.knowledge,
                hedged=claim.hedged,
                took=took,
                in_view=verdict.in_view,
                own_plant=verdict.own_plant,
                invented_as=verdict.invented_as,
                planted_by=verdict.planted_by,
                origin=verdict.path[0] if verdict.path else None,
                path=list(verdict.path),
                proof=list(verdict.evidence),
                knew=list(verdict.against),
                grounds=list(verdict.grounds),
                why=why,
                reason=verdict.reason,
                audience=renamed if renamed != ClaimNamed() else None,
            )
        )
    return out


def _changed(renamed: str, original: str) -> str | None:
    return renamed if renamed != original else None


def _why(game: GameState, verdict: Verdict) -> str:
    """The verdict in one plain sentence, naming characters; the feed renames
    them as it names seats."""
    world = game.world
    speaker = world.players[verdict.claim.speaker].name
    planter = (
        world.players[verdict.planted_by].name
        if verdict.planted_by in world.players
        else "Someone"
    )
    label = verdict.label
    if label == "true":
        return "That's what happened."
    if label == "evasive":
        return "True, and not what was asked."
    if label == "lie":
        if verdict.own_plant is not None:
            return f"{speaker} planted that story first, then told it again."
        return f"{speaker} knew better. The proof was in plain view."
    if label == "mistake":
        if verdict.in_view is False:
            return f"Wrong. {speaker} knew once, and forgot."
        if verdict.claim.hedged and not verdict.grounds:
            return "A guess. A wrong one."
        return f"Wrong, but it's what {speaker} saw."
    if label == "implanted":
        if verdict.gave_in is True:
            return (
                f"{planter} said so, and {speaker} took that over what {speaker} saw."
            )
        if verdict.gave_in is False:
            return (
                f"{planter} said so first, and by then {speaker} had forgotten "
                "what really happened."
            )
        return f"{planter} said so first, and {speaker} took it on trust."
    if label == "invented":
        if verdict.invented_as == "bluff":
            return f"{speaker} made it up, on purpose."
        return f"{speaker} made it up, with no sign of knowing it."
    if label == "kept":
        return f"{speaker} did what {speaker} said."
    if label == "broken":
        return f"{speaker} said one thing and did another."
    return "Nothing on the tape either way."


def vocabulary() -> dict[str, object]:
    """The show's words, as every renderer must print them (SPEC §8): each
    stamp and its ink in the spec's order, the stamp each fact-checker class
    gets (GASLIT for an implanted belief taken up with the truth in view), the
    order a moment picks among its claims' stamps, and each moment's label."""
    return {
        "stamps": [{"stamp": name, "tone": TONES[name]} for name in get_args(Stamp)],
        "classes": dict(STAMPS),
        "gave_in": "GASLIT",
        "worst": list(WORST),
        "labels": dict(LABELS),
    }


def played_by(agent: str) -> PlayedBy:
    if is_model(agent):
        return "model"
    if agent.startswith("bot:") or is_mock(agent):
        return "bot"
    return "unknown"


def _seats(stored: GameRecord, game: GameState, naming: Naming) -> list[FeedSeat]:
    world = game.world
    recruiters = {
        offer.target: offer.recruiter
        for offer in game.offers
        if offer.answer == "accepted"
    }
    out_at = {
        entry.payload["player"]: entry.seq
        for entry in stored.ledger
        if entry.type in ("death", "banished")
    }
    failed = [entry.actor_id for entry in stored.ledger if entry.type == "turn_failed"]
    seats: list[FeedSeat] = []
    for index, pid in enumerate(world.seats, start=1):
        player = world.players[pid]
        agent = stored.agents[pid]
        asked = [d for d in stored.decisions if d.player_id == pid]
        seats.append(
            FeedSeat(
                id=pid,
                seat=index,
                character=player.name,
                audience=naming.name(pid),
                colour=seat_colour(agent),
                agent=agent,
                played_by=played_by(agent),
                model=agent.removeprefix("model:") if is_model(agent) else None,
                build=player.build,
                garment=player.starting_garment,
                detail=player.detail,
                role=game.roles[pid],
                traitor_from=game.traitor_since.get(pid),
                recruited_by=recruiters.get(pid),
                fate=why_out(game, pid),
                out_at=out_at.get(pid),
                decisions=len(asked),
                refused=sum(1 for d in asked if d.refused),
                failed=failed.count(pid),
            )
        )
    return seats


_PARSED = frozenset({"intent", "told", "guesses"})


def _event(world: WorldState, entry: LedgerEntry, naming: Naming) -> FeedEvent:
    private = entry.private
    intent = private.get("intent")
    told = private.get("told")
    guesses = private.get("guesses")
    light = world.map.room(entry.room_id).lighting if entry.room_id else None
    lines = {key: value for key, value in private.items() if key not in _PARSED}
    truths = json.loads(told) if told is not None else None
    return FeedEvent(
        seq=entry.seq,
        tick=entry.tick,
        day=entry.day,
        phase=entry.phase,
        type=entry.type,
        actor=entry.actor_id,
        room=entry.room_id,
        light=light,
        text=entry.text,
        quote=entry.quote,
        payload=dict(entry.payload),
        private=lines,
        intent=Intent.parse(intent) if intent is not None else None,
        told=truths,
        guesses=json.loads(guesses) if guesses is not None else None,
        witnesses=[
            FeedWitness(
                player=record.player_id,
                sense=record.sense,
                confidence=record.confidence,
                channel=record.channel,
                text=record.text,
                quote=record.quote,
                author=record.author,
                recorded_actor=record.recorded_actor,
                from_room=record.from_room,
                noted=record.noted,
                audience=named(naming, record.text, record.quote),
            )
            for record in entry.witnesses
        ],
        rolls=[
            FeedRoll(
                cursor=roll.cursor,
                purpose=roll.purpose,
                value=roll.value,
                chance=roll.threshold,
                success=roll.success,
            )
            for roll in entry.rolls
        ],
        audience=named(naming, entry.text, entry.quote, lines, truths),
    )


def named(
    naming: Naming,
    text: str,
    quote: str | None,
    private: Mapping[str, str] | None = None,
    told: Sequence[str] | None = None,
) -> Named | None:
    """The audience's form of some words, or ``None`` if naming changes none.
    Narration names the model; a quote and a private line, which are the
    player's own words, bracket it."""
    renamed = naming.narration(text)
    words = naming.words(quote) if quote is not None else None
    lines = {
        key: naming.words(value)
        for key, value in (private or {}).items()
        if naming.words(value) != value
    }
    truths = [naming.narration(line) for line in told] if told is not None else None
    if renamed == text and words == quote and not lines and truths == told:
        return None
    return Named(
        text=renamed if renamed != text else None,
        quote=words if words != quote else None,
        private=lines,
        told=truths if truths != told else None,
    )


# ----------------------------------------------------------------- positions


def positions(start: WorldState, ledger: Sequence[LedgerEntry]) -> list[Frame]:
    """Where everyone was at the end of every tick, from the ledger alone.

    A player moves only by setting out and arriving, or by turning back at a
    full door, and each is an entry; so is falling asleep, waking, dying and
    being banished. Folding them from where the cast began gives the whole
    game's movement, which a test holds to the world's own state at every
    tick."""
    here = {pid: _standing(start, pid) for pid in start.seats}
    frames: list[Frame] = []
    day, phase = start.day, start.phase
    last = ledger[-1].tick if ledger else start.tick
    index = 0
    for tick in range(start.tick, last + 1):
        while index < len(ledger) and ledger[index].tick <= tick:
            entry = ledger[index]
            index += 1
            day, phase = entry.day, entry.phase
            _fold(here, entry)
        frames.append(Frame(tick=tick, day=day, phase=phase, seats=dict(here)))
    return frames


def _standing(world: WorldState, player_id: str) -> Whereabouts:
    player = world.players[player_id]
    position = player.position
    if isinstance(position, AtRoom):
        return Whereabouts(
            room=position.room_id, asleep=player.asleep, alive=player.alive
        )
    return Whereabouts(
        origin=position.origin,
        destination=position.destination,
        asleep=player.asleep,
        alive=player.alive,
    )


def _fold(here: dict[str, Whereabouts], entry: LedgerEntry) -> None:
    """Apply one entry to everyone's whereabouts. Each change is a new value,
    so the frames already taken keep what they held."""
    actor = entry.actor_id
    kind = entry.type
    if kind in ("death", "banished"):
        pid = entry.payload["player"]
        here[pid] = here[pid].model_copy(update={"alive": False, "asleep": False})
    elif actor is None or actor not in here:
        return
    elif kind == "move_depart":
        here[actor] = here[actor].model_copy(
            update={
                "room": None,
                "origin": entry.room_id,
                "destination": entry.payload["destination"],
            }
        )
    elif kind == "move_arrive" or (kind == "blocked" and here[actor].room is None):
        # A player still on the way who is blocked has turned back at a full
        # door: the entry is written in the room they came back to.
        here[actor] = here[actor].model_copy(
            update={"room": entry.room_id, "origin": None, "destination": None}
        )
    elif kind == "sleep":
        here[actor] = here[actor].model_copy(update={"asleep": True})
    elif kind == "wake":
        here[actor] = here[actor].model_copy(update={"asleep": False})


# ----------------------------------------------------------------------- files


def save_feed(feed: Feed, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(feed.model_dump_json())


def load_feed(path: str | Path) -> Feed:
    return Feed.model_validate_json(Path(path).read_text())


__all__ = [
    "BOT_COLOUR",
    "FEED",
    "FEED_VERSION",
    "REFERENCE_BOT",
    "UNLABELLED",
    "ClaimNamed",
    "Feed",
    "FeedClaim",
    "FeedEvent",
    "FeedGame",
    "FeedMoment",
    "FeedPassage",
    "FeedRoll",
    "FeedRoom",
    "FeedSeat",
    "FeedThing",
    "FeedWitness",
    "Frame",
    "NameStyle",
    "Named",
    "Naming",
    "PlayedBy",
    "Seating",
    "Took",
    "Whereabouts",
    "audience_feed",
    "audience_names",
    "checked",
    "load_feed",
    "model_name",
    "named",
    "played_by",
    "positions",
    "save_feed",
    "seat_colour",
    "vocabulary",
]
