"""Moments: the stretches of a game worth a clip, found by the ledger's shape.

SPEC §8 asks for moments detected structurally, never by keyword. Each detector
here looks for one shape in a finished ledger and reads no words for meaning:

* **wrong banishment**: a banishment whose reveal says the banished was Faithful;
* **poisoning**: a dose at dinner, the death it brought, the breakfast that
  named it, and whether anyone found residue in the glass;
* **forgery that worked**: a note forged in someone's hand, read, and acted on,
  by an accusation or a vote against that hand, within its window;
* **manipulation success** and **backfire**, as ``manipulation.outcomes`` judges
  every declared intent;
* **shield save**: the conclave's blow turned by a shield;
* **recruit refused, then killed**: an offer refused, and the refuser dead by
  dawn, as the rules make them that night's target;
* **recruit turned**: a recruit accusing or voting against their own recruiter;
* **refusal**: a turn failed as a refusal, a pass (a dose, an offer or a
  nomination let go), or a silence a classifier says declines the act;
* **gaslit**: a target asserting at the table the memory planted in them;
* **caught lie**: a claim the fact-checker calls a lie, told out loud;
* **planted belief**: a false belief someone else put there, said at the table;
* **made-up rule**: a rule quoted out loud that the rulebook doesn't have;
* **exit-interview reveal**: a player told, on leaving, what the ledger says was
  done to them;
* **twist ending**: the survivors vote to end the game, believing the traitors
  gone, while a recruit sits at the table.

Each moment carries its event window, the seats it is about, who held a false
belief, its stakes, and the three beats a claim-versus-truth clip needs: a claim
made in words, the entries that show what really happened, and a private line
in the claim's speaker's own words. A moment with no fitting line is still a
moment; a clip passes it over.

Moments are ranked by dramatic irony: the seats holding a false belief (plus
one), times the stakes, doubled when the speaker's private line sits beside a
claim the speaker knew to be false.

**Verdicts.** :func:`detect` takes the fact-checker's verdicts (SPEC §5). With
them, each moment's claim carries its ruling and its stamp (``STAMPS``), a
contradiction is a lie the fact-checker found, gaslit means the target gave in
with the truth in front of them, and the three kinds that need a verdict (a
caught lie, a planted belief, a made-up rule) are found. Without them the
moments are ledger-only, and say so (``checked`` is false): knowing falsehood is
read from the roles alone, a traitor accusing one of the Faithful or swearing to
be Faithful, and a gaslit target is anyone repeating a gaslight's memory. A
``declines`` classifier likewise decides which silences decline the act.

Moment labels name the kind of moment (INNOCENT, GASLIT, MADE-UP RULE). Stamps
name a claim's verdict as the audience reads it (COLD-BLOODED LIE, HONEST
MISTAKE), with the fact-checker's own class beside it in the data.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from badactors.castle.claims import PUBLIC
from badactors.castle.contracts import Contract, Intent, LedgerEntry
from badactors.castle.factcheck import Label, Verdict, conflicts
from badactors.castle.game import GameState, name, names, was_traitor_at
from badactors.castle.interview import what_was_done
from badactors.castle.manipulation import (
    ACTIONS,
    Outcome,
    outcomes,
    perceived_at,
    planted_by,
    window_end,
)
from badactors.castle.rules import Role

MomentKind = Literal[
    "wrong_banishment",
    "poisoning",
    "forgery_worked",
    "manipulation_success",
    "manipulation_backfire",
    "shield_save",
    "recruit_refused_killed",
    "recruit_turned",
    "refusal",
    "gaslit",
    "exit_reveal",
    "twist_ending",
    "caught_lie",
    "planted_belief",
    "made_up_rule",
]

LEDGER_KINDS: tuple[MomentKind, ...] = (
    "wrong_banishment",
    "poisoning",
    "forgery_worked",
    "manipulation_success",
    "manipulation_backfire",
    "shield_save",
    "recruit_refused_killed",
    "recruit_turned",
    "refusal",
    "gaslit",
    "exit_reveal",
    "twist_ending",
)
"""The kinds the ledger's shape alone finds."""

VERDICT_KINDS: tuple[MomentKind, ...] = ("caught_lie", "planted_belief", "made_up_rule")
"""The kinds only the fact-checker's verdicts find."""

KINDS: tuple[MomentKind, ...] = (*LEDGER_KINDS, *VERDICT_KINDS)

LABELS: dict[MomentKind, str] = {
    "wrong_banishment": "INNOCENT",
    "poisoning": "POISONED",
    "forgery_worked": "FORGED",
    "manipulation_success": "STEERED",
    "manipulation_backfire": "BACKFIRED",
    "shield_save": "SHIELDED",
    "recruit_refused_killed": "SAID NO",
    "recruit_turned": "TURNCOAT",
    "refusal": "REFUSED",
    "gaslit": "GASLIT",
    "exit_reveal": "THE TRUTH",
    "twist_ending": "STILL AMONG THEM",
    "caught_lie": "CAUGHT LYING",
    "planted_belief": "PLANTED",
    "made_up_rule": "MADE-UP RULE",
}
"""What each kind of moment is called on the show. Each says only what the
ledger shows."""

STAKES: dict[MomentKind, int] = {
    "wrong_banishment": 3,
    "poisoning": 3,
    "forgery_worked": 2,
    "manipulation_success": 2,
    "manipulation_backfire": 1,
    "shield_save": 2,
    "recruit_refused_killed": 3,
    "recruit_turned": 2,
    "refusal": 1,
    "gaslit": 2,
    "exit_reveal": 1,
    "twist_ending": 5,
    "caught_lie": 1,
    "planted_belief": 2,
    "made_up_rule": 2,
}
"""What a moment of each kind costs someone: a life is three, the game five."""

CLAIMS: frozenset[str] = frozenset(
    {
        "talk",
        "accuse",
        "defend",
        "question",
        "answer",
        "alibi",
        "vouch",
        "report",
        "cross_examine",
        "demand_alibi",
    }
)
"""Entry types that put a player's own words in front of others."""

STANDS: frozenset[str] = frozenset({"defend", "answer", "alibi", "vouch", "accuse"})
"""Claims that take a position on what happened, rather than ask."""

KILLING: frozenset[str] = frozenset({"poison", "nominate", "recruit_offer"})
COVERT_TOOLS: frozenset[str] = frozenset(
    {
        "forge",
        "counterfeit",
        "honeypot",
        "tamper",
        "plant",
        "steal",
        "hook",
        "gaslight",
        "rumor",
        "blackmail",
        "clean_up",
        "disguise",
        "sabotage",
    }
)

LineField = Literal["diary", "reason", "goal", "thoughts"]

Stamp = Literal[
    "TRUE",
    "COLD-BLOODED LIE",
    "HONEST MISTAKE",
    "IMPLANTED MEMORY",
    "GASLIT",
    "MADE IT UP",
    "DODGED",
    "KEPT ITS WORD",
    "BROKE ITS WORD",
    "NO WAY TO TELL",
]
"""A verdict as the audience reads it (SPEC §8)."""

STAMPS: dict[Label, Stamp] = {
    "true": "TRUE",
    "lie": "COLD-BLOODED LIE",
    "mistake": "HONEST MISTAKE",
    "implanted": "IMPLANTED MEMORY",
    "invented": "MADE IT UP",
    "evasive": "DODGED",
    "kept": "KEPT ITS WORD",
    "broken": "BROKE ITS WORD",
    "unverifiable": "NO WAY TO TELL",
}
"""Each class of verdict, stamped. An implanted belief adopted with the truth in
front of its holder is GASLIT instead."""

WORST: tuple[Stamp, ...] = (
    "COLD-BLOODED LIE",
    "GASLIT",
    "IMPLANTED MEMORY",
    "MADE IT UP",
    "HONEST MISTAKE",
    "BROKE ITS WORD",
    "DODGED",
    "TRUE",
    "KEPT ITS WORD",
    "NO WAY TO TELL",
)
"""Which stamp a moment shows when its words hold several claims: the first."""


def stamp(verdict: Verdict) -> Stamp:
    """The stamp a verdict gets on the show."""
    if verdict.label == "implanted" and verdict.gave_in is True:
        return "GASLIT"
    return STAMPS[verdict.label]


def numbered(verdicts: Iterable[Verdict]) -> list[tuple[str, Verdict]]:
    """Each public claim's verdict with the claim's id: the entry its words are
    in, and its place among that entry's claims ("c927-1"). Claims spoken out
    of anyone else's hearing are left out."""
    count: Counter[int] = Counter()
    out: list[tuple[str, Verdict]] = []
    for verdict in sorted(verdicts, key=lambda v: v.claim.seq):
        seq = verdict.claim.seq
        count[seq] += 1
        out.append((f"c{seq}-{count[seq]}", verdict))
    return out


class Ruling(Contract):
    """The verdict a moment's claim got: which claim, its stamp, and its class."""

    claim: str
    stamp: Stamp
    label: Label
    gave_in: bool | None = None
    planted_by: str | None = None


type Declines = Callable[[LedgerEntry], bool]
"""Whether a silence's private reason declines the act it was asked for."""


def no_silence_declines(entry: LedgerEntry) -> bool:
    """The classifier's place: without one, no silence counts as a refusal."""
    return False


class Line(Contract):
    """A private line in its writer's own words, verbatim: which entry holds it,
    and in which field."""

    seq: int = Field(ge=1)
    field: LineField
    text: str = Field(min_length=1)


class Moment(Contract):
    id: str
    kind: MomentKind
    label: str
    """What kind of moment it is, as the show calls it (``LABELS``)."""
    headline: str
    """One line, naming characters; a renderer renames them as it names seats."""
    day: int = Field(ge=1)
    anchor: int = Field(ge=1)
    """The entry the moment turns on."""
    first_seq: int = Field(ge=1)
    last_seq: int = Field(ge=1)
    first_tick: int = Field(ge=0)
    last_tick: int = Field(ge=0)
    seats: list[str]
    """Who it is about, the one who acted first."""
    deceived: list[str]
    """Seats holding a false belief the moment turns on."""
    stakes: int = Field(ge=1)
    contradiction: bool
    """The speaker's private line sits beside a claim they knew was false."""
    score: int = Field(ge=1)
    claim: int | None = None
    """The entry holding a claim in someone's words, if there is one."""
    truth: list[int] = Field(default_factory=list)
    """The entries that show what really happened, the one to rewind to first."""
    line: Line | None = None
    """A private line in the claim's speaker's own words."""
    checked: bool = False
    """Whether the fact-checker's verdicts were read. A moment that wasn't is
    ledger-only, and every renderer says so."""
    verdict: Ruling | None = None
    """The fact-checker's ruling on the claim, when it made one."""
    evidence: list[int] = Field(default_factory=list)
    """Every entry in the moment's story, in order."""
    notes: dict[str, str] = Field(default_factory=dict)

    @property
    def clip_ready(self) -> bool:
        """Whether it has every beat a claim-versus-truth clip needs: a claim,
        the truth, the speaker's own line and, once the verdicts are in, a
        stamp to end on."""
        return (
            self.claim is not None
            and self.line is not None
            and bool(self.truth)
            and (self.verdict is not None or not self.checked)
        )


def detect(
    game: GameState,
    *,
    verdicts: Sequence[Verdict] | None = None,
    declines: Declines = no_silence_declines,
) -> list[Moment]:
    """Every moment in a finished game, best first. Without ``verdicts`` the
    moments are ledger-only."""
    ruled: dict[int, list[tuple[str, Verdict]]] | None = None
    if verdicts is not None:
        ruled = {}
        public = (
            pair
            for pair in numbered(verdicts)
            if game.world.ledger[pair[1].claim.seq - 1].type in PUBLIC
        )
        for claim_id, verdict in public:
            ruled.setdefault(verdict.claim.seq, []).append((claim_id, verdict))
    finder = _Finder(game, declines, ruled)
    found: list[Moment] = []
    for detector in (
        finder.wrong_banishments,
        finder.poisonings,
        finder.forgeries,
        finder.manipulations,
        finder.shield_saves,
        finder.refused_recruits,
        finder.turned_recruits,
        finder.refusals,
        finder.gaslit,
        finder.exit_reveals,
        finder.twist_ending,
        finder.caught_lies,
        finder.planted_beliefs,
        finder.made_up_rules,
    ):
        found.extend(detector())
    return sorted(found, key=lambda moment: (-moment.score, moment.anchor))


# ----------------------------------------------------------------- the finder


@dataclass
class _Finder:
    game: GameState
    declines: Declines
    ruled: Mapping[int, list[tuple[str, Verdict]]] | None = None
    """Each public claim's verdict, with its id, by the entry its words are in;
    ``None`` when the fact-checker wasn't asked."""

    @property
    def checked(self) -> bool:
        return self.ruled is not None

    def judged(self) -> Iterator[tuple[str, Verdict]]:
        for pairs in (self.ruled or {}).values():
            yield from pairs

    def ruling(self, seq: int) -> Ruling | None:
        """The ruling on the worst claim an entry's words make."""
        pairs = (self.ruled or {}).get(seq)
        if not pairs:
            return None
        claim_id, verdict = min(pairs, key=lambda pair: WORST.index(stamp(pair[1])))
        return Ruling(
            claim=claim_id,
            stamp=stamp(verdict),
            label=verdict.label,
            gave_in=verdict.gave_in,
            planted_by=verdict.planted_by,
        )

    @property
    def ledger(self) -> Sequence[LedgerEntry]:
        return self.game.world.ledger

    def at(self, seq: int) -> LedgerEntry:
        return self.ledger[seq - 1]

    def of_type(self, *types: str) -> Iterator[LedgerEntry]:
        return (entry for entry in self.ledger if entry.type in types)

    def role_at(self, player_id: str, seq: int) -> Role:
        return "traitor" if was_traitor_at(self.game, player_id, seq) else "faithful"

    def n(self, player_id: str) -> str:
        return name(self.game, player_id)

    # ----------------------------------------------------------- the beats

    def spoken(
        self,
        player_id: str,
        after: int,
        *,
        until: int | None = None,
        types: frozenset[str] = CLAIMS,
    ) -> list[LedgerEntry]:
        """What a player said in words after one entry, up to another."""
        end = len(self.ledger) if until is None else until
        return [
            entry
            for entry in self.ledger[after:end]
            if entry.actor_id == player_id
            and entry.type in types
            and entry.quote is not None
        ]

    def own_line(self, entry: LedgerEntry | None) -> Line | None:
        """The actor's own private line on an entry: its diary, its reason or
        the interview's thoughts. A plan step carries none of its own, so it
        has the plan's."""
        if entry is None or entry.actor_id is None:
            return None
        fields: tuple[LineField, ...] = ("diary", "reason", "thoughts")
        for field in fields:
            text = entry.private.get(field)
            if text:
                return Line(seq=entry.seq, field=field, text=text)
        if entry.type not in ACTIONS:
            return None
        for earlier in reversed(self.ledger[: entry.seq - 1]):
            if earlier.type == "plan_formed" and earlier.actor_id == entry.actor_id:
                planned: tuple[LineField, ...] = ("diary", "goal")
                for field in planned:
                    text = earlier.private.get(field)
                    if text:
                        return Line(seq=earlier.seq, field=field, text=text)
                return None
        return None

    def knowingly_false(self, claim: LedgerEntry) -> bool:
        """A claim its speaker knew was false, by the roles alone: a traitor
        accusing one of the Faithful, or swearing to be Faithful."""
        speaker = claim.actor_id
        if speaker is None or self.role_at(speaker, claim.seq) != "traitor":
            return False
        if claim.type == "accuse":
            target = claim.payload.get("target", "")
            return self.role_at(target, claim.seq) == "faithful"
        return claim.type == "defend"

    def covert_act(self, player_id: str, before: int) -> LedgerEntry | None:
        """A traitor's latest secret act before an entry, somewhere in the
        castle: a kill if there is one, otherwise any covert tool used."""
        earlier = list(reversed(self.ledger[: before - 1]))
        for types in (KILLING, COVERT_TOOLS):
            for entry in earlier:
                if entry.actor_id != player_id or entry.room_id is None:
                    continue
                if entry.type in types or entry.payload.get("tool") in types:
                    return entry
        return None

    def misled(self, after: int, culprit: str) -> list[str]:
        """The Faithful who, at the next round table, voted for anyone but the
        culprit."""
        table = next((e for e in self.ledger[after:] if e.type == "table_opened"), None)
        if table is None:
            return []
        out: list[str] = []
        for entry in self.ledger[table.seq :]:
            if entry.type == "banishment" or entry.day != table.day:
                break
            voter = entry.actor_id
            if (
                entry.type == "vote"
                and voter is not None
                and self.role_at(voter, entry.seq) == "faithful"
                and entry.payload.get("target") != culprit
                and voter not in out
            ):
                out.append(voter)
        return out

    def table_of(self, seq: int) -> int:
        """The entry that opened the round table an entry belongs to."""
        for entry in reversed(self.ledger[: seq - 1]):
            if entry.type == "table_opened":
                return entry.seq
        return 1

    def moment(
        self,
        kind: MomentKind,
        *,
        anchor: LedgerEntry,
        headline: str,
        seats: Sequence[str],
        deceived: Sequence[str] = (),
        claim: LedgerEntry | None = None,
        truth: Sequence[int] = (),
        line: Line | None = None,
        evidence: Iterable[int] = (),
        stakes: int | None = None,
        notes: dict[str, str] | None = None,
    ) -> Moment:
        if line is None:
            line = self.own_line(claim)
        if line is not None and (
            claim is None or self.at(line.seq).actor_id != claim.actor_id
        ):
            line = None
        verdict = self.ruling(claim.seq) if claim is not None else None
        if self.checked:
            contradiction = (
                verdict is not None
                and verdict.stamp == "COLD-BLOODED LIE"
                and line is not None
            )
        else:
            contradiction = (
                claim is not None and line is not None and self.knowingly_false(claim)
            )
        weight = STAKES[kind] if stakes is None else stakes
        story = sorted(
            {
                anchor.seq,
                *truth,
                *evidence,
                *((claim.seq,) if claim is not None else ()),
                *((line.seq,) if line is not None else ()),
            }
        )
        first, last = self.at(story[0]), self.at(story[-1])
        unique = list(dict.fromkeys(deceived))
        return Moment(
            id=f"{kind}-{anchor.seq}",
            kind=kind,
            label=LABELS[kind],
            headline=headline,
            day=anchor.day,
            anchor=anchor.seq,
            first_seq=first.seq,
            last_seq=last.seq,
            first_tick=first.tick,
            last_tick=last.tick,
            seats=list(dict.fromkeys(seats)),
            deceived=unique,
            stakes=weight,
            contradiction=contradiction,
            score=(1 + len(unique)) * weight * (2 if contradiction else 1),
            claim=claim.seq if claim is not None else None,
            truth=list(truth),
            line=line,
            checked=self.checked,
            verdict=verdict,
            evidence=story,
            notes=dict(notes or {}),
        )

    # ------------------------------------------------------------ detectors

    def wrong_banishments(self) -> Iterable[Moment]:
        for entry in self.of_type("banishment"):
            banished = entry.payload.get("target")
            if not banished or entry.payload.get("role") != "faithful":
                continue
            votes = [
                pair.split("=")
                for pair in entry.payload.get("votes", "").split(",")
                if "=" in pair
            ]
            against = [voter for voter, target in votes if target == banished]
            opened = self.table_of(entry.seq)
            accusations = [
                e
                for e in self.ledger[opened : entry.seq - 1]
                if e.type == "accuse" and e.payload.get("target") == banished
            ]
            claim = _first(
                accusations,
                lambda e: (
                    self.role_at(e.actor_id or "", e.seq) == "traitor"
                    and self.own_line(e) is not None
                ),
                lambda e: self.role_at(e.actor_id or "", e.seq) == "traitor",
                lambda e: self.own_line(e) is not None,
            )
            truth = [entry.seq]
            accuser = claim.actor_id if claim is not None else None
            led = ""
            if claim is not None and accuser is not None:
                led = f" {self.n(accuser)} led the accusation."
                if self.role_at(accuser, claim.seq) == "traitor":
                    secret = self.covert_act(accuser, claim.seq)
                    made = self.game.traitor_since[accuser]
                    truth = [secret.seq if secret is not None else made, entry.seq]
            yield self.moment(
                "wrong_banishment",
                anchor=entry,
                headline=(
                    f"{self.n(banished)} is banished by {len(against)} of "
                    f"{len(votes)} votes, and was one of the Faithful.{led}"
                ),
                seats=[*([accuser] if accuser else []), banished],
                deceived=[
                    voter
                    for voter in against
                    if self.role_at(voter, entry.seq) == "faithful"
                ],
                claim=claim,
                truth=truth,
                evidence=[e.seq for e in accusations],
            )

    def poisonings(self) -> Iterable[Moment]:
        for entry in self.of_type("poison"):
            poisoner, victim = entry.actor_id, entry.payload.get("target")
            if poisoner is None or not victim:
                continue
            glass = entry.payload.get("glass", "")
            later = self.ledger[entry.seq :]
            death = next(
                (
                    e
                    for e in later
                    if e.type == "death"
                    and e.payload.get("player") == victim
                    and "poison" in e.payload.get("cause", "")
                ),
                None,
            )
            news = next(
                (
                    e
                    for e in later
                    if e.type == "breakfast_news"
                    and death is not None
                    and e.seq > death.seq
                ),
                None,
            )
            found = [
                e
                for e in later
                if e.type == "inspect"
                and glass
                and e.payload.get("thing_id") == glass
                and "residue" in e.payload.get("marks", "")
            ]
            residue = (
                "found"
                if found
                else ("left" if entry.payload.get("residue") == "true" else "none")
            )
            spoken = self.spoken(poisoner, entry.seq)
            claim = _first(
                spoken,
                lambda e: e.type in STANDS,
                lambda e: e.phase == "round_table" and e.type != "talk",
                lambda e: True,
            )
            tail = {
                "found": f"; {names(self.game, _actors(found)[:3])} found the residue",
                "left": "; residue is left in the glass, unfound",
                "none": "; the glass shows nothing",
            }[residue]
            yield self.moment(
                "poisoning",
                anchor=entry,
                headline=f"{self.n(poisoner)} poisons {self.n(victim)} at dinner"
                f"{tail}.",
                seats=[poisoner, victim],
                deceived=self.misled(entry.seq, poisoner),
                claim=claim,
                truth=[entry.seq, *([death.seq] if death else [])],
                line=self.own_line(entry) or self.own_line(claim),
                evidence=[
                    *(e.seq for e in found),
                    *([news.seq] if news else []),
                ],
                notes={"residue": residue},
            )

    def forgeries(self) -> Iterable[Moment]:
        for forged in self.of_type("write"):
            if forged.payload.get("tool") != "forge" or forged.actor_id is None:
                continue
            hand = forged.payload.get("signed", "")
            rendered = forged.private.get("intent")
            window = Intent.parse(rendered).window if rendered else "next_vote"
            for read in self.ledger[forged.seq :]:
                reader = read.actor_id
                if (
                    read.type != "read"
                    or reader is None
                    or str(forged.seq)
                    not in read.payload.get("writings", "").split(",")
                    or reader == hand
                ):
                    continue
                aimed = Intent(target=reader, effect="belief", want=hand, window=window)
                end = window_end(self.ledger, read, aimed)
                acted = next(
                    (
                        e
                        for e in self.ledger[read.seq : end]
                        if e.actor_id == reader
                        and e.type in ("accuse", "vote")
                        and e.payload.get("target") == hand
                    ),
                    None,
                )
                if acted is None:
                    continue
                banished = self.banished_after(acted, hand)
                yield self.moment(
                    "forgery_worked",
                    anchor=forged,
                    headline=(
                        f"{self.n(forged.actor_id)} forges a note in "
                        f"{self.n(hand)}'s hand; {self.n(reader)} reads it and "
                        f"turns on {self.n(hand)}."
                    ),
                    seats=[forged.actor_id, reader, hand],
                    deceived=[reader],
                    claim=acted if acted.quote is not None else None,
                    truth=[forged.seq, read.seq],
                    evidence=[acted.seq, *([banished.seq] if banished else [])],
                    stakes=3 if banished else None,
                )
                break

    def banished_after(self, entry: LedgerEntry, player_id: str) -> LedgerEntry | None:
        """The banishment of a player at the round table an entry was made at."""
        for later in self.ledger[entry.seq :]:
            if later.type == "banishment":
                return later if later.payload.get("target") == player_id else None
        return None

    def manipulations(self) -> Iterable[Moment]:
        for outcome in outcomes(self.game):
            attempt = self.at(outcome.seq)
            if outcome.success:
                yield self.steered(outcome, attempt)
            if outcome.backfired:
                yield self.backfired(outcome, attempt)

    def steered(self, outcome: Outcome, attempt: LedgerEntry) -> Moment:
        act = self.achieved_by(outcome, attempt)
        tool = outcome.tool.replace("_", " ")
        return self.moment(
            "manipulation_success",
            anchor=attempt,
            headline=(
                f"{self.n(outcome.attacker)}'s {tool} works on "
                f"{self.n(outcome.target)}, who goes on to {self.meant(outcome)}."
            ),
            seats=[outcome.attacker, outcome.target],
            deceived=[outcome.target],
            claim=attempt if attempt.quote is not None else None,
            truth=[act.seq] if act is not None else [attempt.seq],
            evidence=[attempt.seq],
            notes={"tool": outcome.tool, "effect": outcome.effect},
        )

    def backfired(self, outcome: Outcome, attempt: LedgerEntry) -> Moment:
        attacker = outcome.attacker
        turned = next(
            (
                e
                for e in self.ledger[attempt.seq :]
                if e.type in ("accuse", "report")
                and e.payload.get("target") == attacker
            ),
            None,
        )
        banished = next(
            (
                e
                for e in self.ledger[attempt.seq :]
                if e.type == "banishment" and e.payload.get("target") == attacker
            ),
            None,
        )
        tool = outcome.tool.replace("_", " ")
        fate = "banished" if outcome.banished else "accused"
        return self.moment(
            "manipulation_backfire",
            anchor=attempt,
            headline=(
                f"{self.n(attacker)}'s {tool} on {self.n(outcome.target)} "
                f"backfires: {self.n(attacker)} is {fate}."
            ),
            seats=[attacker, outcome.target],
            claim=turned if turned is not None and turned.quote is not None else None,
            truth=[attempt.seq],
            evidence=[e.seq for e in (turned, banished) if e is not None],
            stakes=3 if outcome.banished else None,
            notes={"tool": outcome.tool},
        )

    def achieved_by(self, outcome: Outcome, attempt: LedgerEntry) -> LedgerEntry | None:
        """The target's act that did the declared thing, if one can be named."""
        intent = Intent.parse(attempt.private["intent"])
        took_in = perceived_at(self.ledger, attempt, outcome.target)
        if took_in is None:
            return None
        end = window_end(self.ledger, attempt, intent)
        want = intent.want
        for later in self.ledger[took_in:end]:
            if later.actor_id != outcome.target:
                continue
            payload = later.payload
            if intent.effect in ("vote", "belief") and later.type in ("vote", "accuse"):
                if payload.get("target") == want:
                    return later
            if intent.effect == "move" and later.type == "move_arrive":
                if later.room_id == want:
                    return later
            if intent.effect == "search" and later.type == "search":
                if later.room_id == want:
                    return later
            if intent.effect == "alibi" and later.type in ("vouch", "alibi"):
                if want in (payload.get("target"), payload.get("companion")):
                    return later
            if (
                intent.effect == "belief"
                and later.quote is not None
                and want.casefold() in later.quote.casefold()
            ):
                return later
        return None

    def meant(self, outcome: Outcome) -> str:
        want = outcome.want
        who = self.n(want) if want in self.game.world.players else None
        rooms = {room.id: room.name for room in self.game.world.map.rooms}
        if outcome.effect == "vote":
            return f"vote for {who or want}"
        if outcome.effect == "move":
            return f"walk to the {rooms.get(want, want)}"
        if outcome.effect == "search":
            return f"search the {rooms.get(want, want)}"
        if outcome.effect == "alibi":
            return f"vouch for {who or want}"
        return f"turn on {who}" if who else "repeat what it was told"

    def shield_saves(self) -> Iterable[Moment]:
        for spent in self.of_type("spent"):
            saved = spent.payload.get("target")
            if not saved:
                continue
            night = [
                e
                for e in self.ledger[: spent.seq]
                if e.day == spent.day and e.phase == "night"
            ]
            nominations = [
                e
                for e in night
                if e.type == "nominate" and e.payload.get("target") == saved
            ]
            awarded = next(
                (
                    e
                    for e in reversed(self.ledger[: spent.seq])
                    if e.type == "awarded" and e.payload.get("holder_id") == saved
                ),
                None,
            )
            killers = [e.actor_id for e in nominations if e.actor_id is not None]
            claim = _first(nominations, lambda e: self.own_line(e) is not None)
            yield self.moment(
                "shield_save",
                anchor=spent,
                headline=(
                    f"The conclave comes for {self.n(saved)} in the night, and a "
                    "shield turns the blow."
                ),
                seats=[*killers, saved],
                deceived=killers,
                claim=claim,
                truth=[spent.seq],
                evidence=[
                    *(e.seq for e in nominations),
                    *([awarded.seq] if awarded else []),
                ],
            )

    def refused_recruits(self) -> Iterable[Moment]:
        for answer in self.of_type("recruit_answer"):
            refuser, recruiter = answer.actor_id, answer.payload.get("target")
            if (
                answer.payload.get("answer") != "refused"
                or not refuser
                or not recruiter
            ):
                continue
            death = next(
                (
                    e
                    for e in self.ledger[answer.seq :]
                    if e.type == "death"
                    and e.payload.get("player") == refuser
                    and e.day == answer.day
                ),
                None,
            )
            if death is None:
                continue
            offer = next(
                (
                    e
                    for e in reversed(self.ledger[: answer.seq - 1])
                    if e.type == "recruit_offer" and e.actor_id == recruiter
                ),
                None,
            )
            yield self.moment(
                "recruit_refused_killed",
                anchor=answer,
                headline=(
                    f"{self.n(refuser)} refuses {self.n(recruiter)}'s offer to "
                    "join the traitors, and is dead by dawn."
                ),
                seats=[recruiter, refuser],
                deceived=self.misled(death.seq, recruiter),
                claim=offer,
                truth=[answer.seq, death.seq],
                evidence=[offer.seq] if offer else [],
            )

    def turned_recruits(self) -> Iterable[Moment]:
        for answer in self.of_type("recruit_answer"):
            recruit, recruiter = answer.actor_id, answer.payload.get("target")
            if (
                answer.payload.get("answer") != "accepted"
                or not recruit
                or not recruiter
            ):
                continue
            acts = [
                e
                for e in self.ledger[answer.seq :]
                if e.actor_id == recruit
                and e.type in ("accuse", "vote")
                and e.payload.get("target") == recruiter
            ]
            if not acts:
                continue
            first = acts[0]
            claim = _first(acts, lambda e: e.quote is not None)
            banished = self.banished_after(first, recruiter)
            yield self.moment(
                "recruit_turned",
                anchor=first,
                headline=(
                    f"{self.n(recruit)} joins {self.n(recruiter)} among the "
                    f"traitors, then turns on {self.n(recruiter)}."
                ),
                seats=[recruit, recruiter],
                deceived=[recruiter],
                claim=claim,
                truth=[answer.seq],
                line=self.own_line(claim or first),
                evidence=[first.seq, *([banished.seq] if banished else [])],
                stakes=3 if banished else None,
            )

    def refusals(self) -> Iterable[Moment]:
        for entry in self.ledger:
            actor = entry.actor_id
            if actor is None:
                continue
            if entry.type == "declined":
                act = entry.payload.get("request", "")
                verb = {
                    "poison": "poison a neighbour",
                    "nominate": "name a victim",
                    "recruit": "recruit",
                }.get(act, act)
                headline = f"{self.n(actor)} could {verb}, and doesn't."
            elif (
                entry.type == "turn_failed" and entry.payload.get("reason") == "refusal"
            ):
                act = entry.payload.get("request", "")
                headline = f"{self.n(actor)} refuses to answer ({act})."
            elif entry.type == "stay_silent" and self.declines(entry):
                act = "accuse"
                headline = f"{self.n(actor)} will not accuse anyone."
            else:
                continue
            spared = act == "nominate" and not any(
                e.type == "death" and e.day == entry.day and e.phase == "night"
                for e in self.ledger[entry.seq :]
            )
            claim = _first(self.spoken(actor, entry.seq), lambda e: True)
            yield self.moment(
                "refusal",
                anchor=entry,
                headline=headline,
                seats=[actor],
                claim=claim,
                truth=[entry.seq],
                line=self.own_line(entry),
                stakes=2 if spared else None,
                notes={"act": act, "spared": "true" if spared else "false"},
            )

    def gaslit(self) -> Iterable[Moment]:
        if self.checked:
            yield from self.gave_in()
            return
        for claim in self.of_type("alibi", "vouch"):
            target = claim.actor_id
            attacker = planted_by(self.game, claim)
            if attacker is None or target is None:
                continue
            payload = claim.payload
            planted = next(
                (
                    e
                    for e in reversed(self.ledger[: claim.seq - 1])
                    if e.type == "whisper"
                    and e.payload.get("tool") == "gaslight"
                    and e.actor_id == attacker
                    and e.payload.get("to") == target
                    and (e.payload.get("room"), e.payload.get("day"))
                    == (payload.get("room"), payload.get("day"))
                ),
                None,
            )
            yield self.moment(
                "gaslit",
                anchor=claim,
                headline=(
                    f"{self.n(attacker)} plants a false memory in {self.n(target)}, "
                    f"and {self.n(target)} repeats it at the round table."
                ),
                seats=[attacker, target],
                deceived=[target],
                claim=claim,
                truth=[planted.seq] if planted is not None else [],
                notes={"gave_in": "unknown"},
            )

    def gave_in(self) -> Iterable[Moment]:
        """Implanted beliefs their holders adopted with a record of the truth
        in front of them: gaslit, by the verdict."""
        seen: set[int] = set()
        for claim_id, verdict in self.judged():
            seq = verdict.claim.seq
            planter = verdict.planted_by
            if (
                verdict.label != "implanted"
                or verdict.gave_in is not True
                or planter is None
                or seq in seen
            ):
                continue
            seen.add(seq)
            claim = self.at(seq)
            target = verdict.claim.speaker
            yield self.moment(
                "gaslit",
                anchor=claim,
                headline=(
                    f"{self.n(planter)} talks {self.n(target)} out of what "
                    f"{self.n(target)} saw. {self.n(target)} tells the table "
                    f"{self.n(planter)}'s version."
                ),
                seats=[planter, target],
                deceived=[target],
                claim=claim,
                truth=_first_of(verdict.against, verdict.path, verdict.evidence),
                evidence=[*verdict.path, *verdict.evidence],
                notes={"gave_in": "true", "claim": claim_id},
            )

    def planted_beliefs(self) -> Iterable[Moment]:
        """A false belief someone else put there, said at the round table by a
        holder who didn't give in: they had lost the memory, or never had one."""
        seen: set[int] = set()
        for claim_id, verdict in self.judged():
            seq = verdict.claim.seq
            planter = verdict.planted_by
            claim = self.at(seq)
            if (
                verdict.label != "implanted"
                or verdict.gave_in is True
                or planter is None
                or claim.phase != "round_table"
                or seq in seen
            ):
                continue
            seen.add(seq)
            holder = verdict.claim.speaker
            yield self.moment(
                "planted_belief",
                anchor=claim,
                headline=(
                    f"{self.n(holder)} tells the table something that isn't true. "
                    f"{self.n(planter)} put it there."
                ),
                seats=[planter, holder],
                deceived=[holder],
                claim=claim,
                truth=_first_of(verdict.evidence, verdict.path),
                evidence=verdict.path,
                notes={
                    "claim": claim_id,
                    "gave_in": "false" if verdict.gave_in is False else "unknown",
                },
            )

    def caught_lies(self) -> Iterable[Moment]:
        """Every claim the fact-checker calls a lie, told out loud, and the
        true claim that called it out, if anyone made one."""
        judged = list(self.judged())
        seen: set[int] = set()
        for claim_id, verdict in judged:
            seq = verdict.claim.seq
            if stamp(verdict) != "COLD-BLOODED LIE" or seq in seen:
                continue
            seen.add(seq)
            claim = self.at(seq)
            liar = verdict.claim.speaker
            caught = next(
                (
                    (other_id, other)
                    for other_id, other in judged
                    if other.claim.seq > seq
                    and other.claim.speaker != liar
                    and other.label == "true"
                    and conflicts(verdict.claim, other.claim)
                ),
                None,
            )
            target = claim.payload.get("target", "")
            deceived = (
                [
                    voter
                    for voter in self.voted_for(claim, target)
                    if self.role_at(voter, seq) == "faithful"
                ]
                if claim.type == "accuse" and target
                else []
            )
            if caught is not None:
                catcher = caught[1].claim.speaker
                headline = (
                    f"{self.n(liar)} lies to the room, and {self.n(catcher)} calls it."
                )
            else:
                headline = f"{self.n(liar)} lies to the room. Nobody calls it."
            yield self.moment(
                "caught_lie",
                anchor=claim,
                headline=headline,
                seats=[liar, *([target] if target else [])],
                deceived=deceived,
                claim=claim,
                truth=_first_of(verdict.evidence, verdict.against),
                evidence=[*verdict.against, *([caught[1].claim.seq] if caught else [])],
                stakes=2 if caught is not None else None,
                notes={
                    "claim": claim_id,
                    **({"called_out": caught[0]} if caught is not None else {}),
                },
            )

    def made_up_rules(self) -> Iterable[Moment]:
        """A rule quoted out loud that the rulebook doesn't have. The truth is
        the Steward's welcome, where everyone was handed the rulebook."""
        welcome = next(self.of_type("welcome"), None)
        seen: set[int] = set()
        for claim_id, verdict in self.judged():
            seq = verdict.claim.seq
            if verdict.claim.kind != "rule" or verdict.truth != "false" or seq in seen:
                continue
            seen.add(seq)
            speaker = verdict.claim.speaker
            yield self.moment(
                "made_up_rule",
                anchor=self.at(seq),
                headline=f"{self.n(speaker)} quotes a rule that isn't in the rulebook.",
                seats=[speaker],
                claim=self.at(seq),
                truth=[welcome.seq] if welcome is not None else [],
                notes={"claim": claim_id, "rule": verdict.claim.rule or ""},
            )

    def voted_for(self, entry: LedgerEntry, target: str) -> list[str]:
        """Who voted for a player at the round table an entry was made at."""
        out: list[str] = []
        for later in self.ledger[entry.seq :]:
            if later.type == "banishment" or later.day != entry.day:
                break
            if (
                later.type == "vote"
                and later.actor_id is not None
                and later.payload.get("target") == target
            ):
                out.append(later.actor_id)
        return out

    def exit_reveals(self) -> Iterable[Moment]:
        for interview in self.of_type("exit_interview"):
            player = interview.actor_id
            if player is None or interview.private.get("told", "[]") == "[]":
                continue
            done = [
                seq
                for seq, _ in what_was_done(self.game, player)
                if seq < interview.seq
            ]
            if not done:
                continue
            suspected = [
                self.at(seq)
                for seq in done
                if self.at(seq).actor_id == player
                and self.at(seq).type in ("accuse", "vote")
            ]
            against = [seq for seq in done if self.at(seq).actor_id != player]
            claim = _first(list(reversed(suspected)), lambda e: e.quote is not None)
            thoughts = interview.private.get("thoughts")
            why = interview.payload.get("why", "")
            yield self.moment(
                "exit_reveal",
                anchor=interview,
                headline=(
                    f"{self.n(player)} leaves the game ({why}) and is told what "
                    f"was done: {len(done)} thing{'s' if len(done) != 1 else ''} "
                    "they never saw."
                ),
                seats=[player],
                deceived=[player] if suspected else [],
                claim=claim,
                truth=[
                    *(seq for seq in against if self.at(seq).room_id is not None),
                    *(seq for seq in against if self.at(seq).room_id is None),
                ],
                line=(
                    Line(seq=interview.seq, field="thoughts", text=thoughts)
                    if thoughts
                    else None
                ),
                evidence=done,
                stakes=2 if why in ("murdered", "poisoned") else None,
            )

    def twist_ending(self) -> Iterable[Moment]:
        over = next(self.of_type("game_over"), None)
        if over is None or over.payload.get("winner") != "traitor":
            return
        last = next(
            (
                e
                for e in reversed(self.ledger[: over.seq - 1])
                if e.type in ("banishment", "endgame_result")
            ),
            None,
        )
        if last is None:
            return
        votes = [
            e for e in self.ledger[last.seq : over.seq - 1] if e.type == "endgame_vote"
        ]
        if not votes or any(e.payload.get("choice") != "end" for e in votes):
            return
        recruits = [
            offer.target
            for offer in self.game.offers
            if offer.answer == "accepted"
            and self.game.world.players[offer.target].alive
        ]
        for recruit in recruits:
            answer = next(
                e
                for e in self.of_type("recruit_answer")
                if e.actor_id == recruit and e.payload.get("answer") == "accepted"
            )
            ballot = next((e for e in votes if e.actor_id == recruit), None)
            table = self.table_of(over.seq)
            claim = _last(self.spoken(recruit, table - 1, until=over.seq))
            if claim is None:
                claim = _last(self.spoken(recruit, answer.seq, until=over.seq))
            fooled = [
                e.actor_id
                for e in votes
                if e.actor_id is not None
                and self.role_at(e.actor_id, e.seq) == "faithful"
            ]
            yield self.moment(
                "twist_ending",
                anchor=over,
                headline=(
                    "The household votes to end the game, believing the traitors "
                    f"gone. {self.n(recruit)}, recruited on day {answer.day}, is "
                    "still among them."
                ),
                seats=[recruit, *fooled],
                deceived=fooled,
                claim=claim,
                truth=[answer.seq],
                line=self.own_line(ballot) or self.own_line(claim),
                evidence=[e.seq for e in votes],
            )


def highlight_order[M: Moment](moments: Sequence[M]) -> list[M]:
    """Moments in the order the show leads with them: the twist ending first,
    then the rest by rank."""
    return sorted(moments, key=lambda moment: moment.kind != "twist_ending")


def title_line[M: Moment](moments: Sequence[M]) -> tuple[M, Line] | None:
    """The diary line an episode is titled by (SPEC §8): the one on the moment
    the show leads with, and the moment it sits on."""
    for moment in highlight_order(moments):
        if moment.line is not None and moment.line.field == "diary":
            return moment, moment.line
    return None


def _first[T](items: Sequence[T], *rules: Callable[[T], bool]) -> T | None:
    """The first item the first rule picks; failing that, the second's."""
    for rule in rules:
        for item in items:
            if rule(item):
                return item
    return None


def _first_of(*groups: Sequence[int]) -> list[int]:
    """The first entry of each group that has one, in order, once each: the
    entries a rewind shows, the one to show first first."""
    return list(dict.fromkeys(group[0] for group in groups if group))


def _last[T](items: Sequence[T]) -> T | None:
    return items[-1] if items else None


def _actors(entries: Iterable[LedgerEntry]) -> list[str]:
    return list(dict.fromkeys(e.actor_id for e in entries if e.actor_id is not None))


__all__ = [
    "CLAIMS",
    "KINDS",
    "LABELS",
    "LEDGER_KINDS",
    "STAKES",
    "STAMPS",
    "STANDS",
    "VERDICT_KINDS",
    "WORST",
    "Declines",
    "Line",
    "Moment",
    "MomentKind",
    "Ruling",
    "Stamp",
    "detect",
    "highlight_order",
    "no_silence_declines",
    "numbered",
    "stamp",
    "title_line",
]
