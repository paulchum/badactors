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

**Verdicts.** The fact-checker (SPEC §5) will judge claims LIE, MISTAKE or
IMPLANTED. Until it does, each moment carries a ledger-only label for its stamp,
and knowing falsehood is read from the roles alone: a traitor accusing one of
the Faithful, or swearing to be Faithful. :func:`detect` takes a ``verdicts``
lookup, claim entry to verdict, which fills each moment's verdict, replaces the
role-based reading of a contradiction, and settles whether a gaslit target gave
in. A ``declines`` classifier likewise decides which silences decline the act.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from badactors.castle.contracts import Contract, Intent, LedgerEntry
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
]

KINDS: tuple[MomentKind, ...] = (
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
}
"""The stamp each kind of moment carries until the fact-checker gives a verdict.
Each says only what the ledger shows."""

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

VerdictLabel = Literal["LIE", "MISTAKE", "IMPLANTED"]


class Verdict(Contract):
    """The fact-checker's ruling on one claim (SPEC §5)."""

    label: VerdictLabel
    planted_by: str | None = None


type Verdicts = Callable[[int], Verdict | None]
"""A claim's verdict, by the ledger entry that holds the claim."""

type Declines = Callable[[LedgerEntry], bool]
"""Whether a silence's private reason declines the act it was asked for."""


def no_verdicts(seq: int) -> Verdict | None:
    """The fact-checker's place, until it exists: no claim has a verdict."""
    return None


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
    """The stamp: the verdict's, once there is one, else the ledger's."""
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
    verdict: Verdict | None = None
    evidence: list[int] = Field(default_factory=list)
    """Every entry in the moment's story, in order."""
    notes: dict[str, str] = Field(default_factory=dict)

    @property
    def clip_ready(self) -> bool:
        """Whether it has every beat a claim-versus-truth clip needs."""
        return self.claim is not None and self.line is not None and bool(self.truth)


def detect(
    game: GameState,
    *,
    verdicts: Verdicts = no_verdicts,
    declines: Declines = no_silence_declines,
) -> list[Moment]:
    """Every moment in a finished game, best first."""
    finder = _Finder(game, verdicts, declines)
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
    ):
        found.extend(detector())
    return sorted(found, key=lambda moment: (-moment.score, moment.anchor))


# ----------------------------------------------------------------- the finder


@dataclass
class _Finder:
    game: GameState
    verdicts: Verdicts
    declines: Declines

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
        verdict = self.verdicts(claim.seq) if claim is not None else None
        if verdict is not None:
            contradiction = verdict.label == "LIE" and line is not None
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
            label=verdict.label if verdict is not None else LABELS[kind],
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
        for claim in self.of_type("alibi", "vouch"):
            if claim.type == "vouch" and claim.payload.get("confirm") != "true":
                continue  # Refusing to vouch is denying the memory, not holding it.
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
            verdict = self.verdicts(claim.seq)
            gave_in = (
                "unknown"
                if verdict is None
                else ("true" if verdict.label == "IMPLANTED" else "false")
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
                notes={"gave_in": gave_in},
            )

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


def _first[T](items: Sequence[T], *rules: Callable[[T], bool]) -> T | None:
    """The first item the first rule picks; failing that, the second's."""
    for rule in rules:
        for item in items:
            if rule(item):
                return item
    return None


def _last[T](items: Sequence[T]) -> T | None:
    return items[-1] if items else None


def _actors(entries: Iterable[LedgerEntry]) -> list[str]:
    return list(dict.fromkeys(e.actor_id for e in entries if e.actor_id is not None))


__all__ = [
    "CLAIMS",
    "KINDS",
    "LABELS",
    "STAKES",
    "STANDS",
    "Declines",
    "Line",
    "Moment",
    "MomentKind",
    "Verdict",
    "VerdictLabel",
    "Verdicts",
    "detect",
    "no_silence_declines",
    "no_verdicts",
]
