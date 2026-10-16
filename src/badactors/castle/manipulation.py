"""Whether a manipulation worked: declared intents set against what happened.

Every use of a manipulation tool declares its intent, in the ledger entry's
``private`` fields where only the audience can see it: whom it was aimed at, what
it was meant to make them do or believe, and by when. This module reads the
finished ledger, as the audience can, and judges each attempt three ways.

* **Success** — the target took the attempt in, and then did the declared
  thing within the window: voted for the named player at their next vote;
  walked into, or searched, the named room within their next phase or next
  three actions; vouched for the attacker's alibi, or named them as company in
  their own; or came to hold the belief, which for a player means accusing or
  voting against them. A manipulation works only through what its target
  reads, so one they never took in has not worked, whatever they did.
* **Reported** — the target exposed it at the round table, quoting it.
* **Backfire** — the attacker was accused, reported or banished before the end
  of the next round table, or of this one for an attempt made at the table. That
  is a window, not a proof of cause, and it is stated as one.

The rest serves the evidence: :func:`planted_by` names who planted a memory a
player repeats, and :func:`rumor_path` follows a rumour hop by hop.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from badactors.castle.contracts import Intent, LedgerEntry
from badactors.castle.game import GameState

ACTIONS: frozenset[str] = frozenset(
    {
        "move_depart",
        "take",
        "drop",
        "stow",
        "wear",
        "use",
        "search",
        "inspect",
        "read",
        "write",
        "talk",
        "whisper",
        "wait",
        "search_person",
        "plant",
        "plant_caught",
        "steal",
        "pickpocket",
        "clean",
        "destroyed",
        "disguise",
        "blocked",
    }
)
"""Entry types that are one of their actor's actions, for a three-action window."""


@dataclass(frozen=True)
class Outcome:
    """One manipulation attempt, judged."""

    seq: int
    tool: str
    attacker: str
    target: str
    effect: str
    want: str
    window: str
    day: int
    success: bool
    reported: bool
    accused: bool
    banished: bool

    @property
    def backfired(self) -> bool:
        return self.accused or self.banished


def outcomes(game: GameState) -> list[Outcome]:
    """Every attempt that declared an intent, judged against the ledger."""
    ledger = game.world.ledger
    out: list[Outcome] = []
    for entry in ledger:
        rendered = entry.private.get("intent")
        if rendered is None or entry.actor_id is None:
            continue
        intent = Intent.parse(rendered)
        end = window_end(ledger, entry, intent)
        accused, banished = _backfire(ledger, entry)
        players = set(game.world.players)
        out.append(
            Outcome(
                seq=entry.seq,
                tool=entry.payload.get("tool", entry.type),
                attacker=entry.actor_id,
                target=intent.target,
                effect=intent.effect,
                want=intent.want,
                window=intent.window,
                day=entry.day,
                success=_achieved(ledger, entry, intent, end, players),
                reported=_reported(ledger, entry, intent.target),
                accused=accused,
                banished=banished,
            )
        )
    return out


def window_end(
    ledger: Sequence[LedgerEntry], entry: LedgerEntry, intent: Intent
) -> int:
    """The last ledger entry inside the attempt's window."""
    last = len(ledger)
    target = intent.target
    if intent.window == "next_vote":
        for later in ledger[entry.seq :]:
            if later.type == "vote" and later.actor_id == target:
                return later.seq
        return last
    if intent.window == "next_phase":
        opened = 0
        for later in ledger[entry.seq :]:
            if later.type == "phase_opened":
                opened += 1
                if opened == 2:
                    return later.seq - 1
        return last
    acted = 0
    for later in ledger[entry.seq :]:
        if later.actor_id == target and later.type in ACTIONS:
            acted += 1
            if acted == 3:
                return later.seq
    return last


def perceived_at(
    ledger: Sequence[LedgerEntry], entry: LedgerEntry, target: str
) -> int | None:
    """The entry by which the target took the attempt in, if they ever did.

    Whispered words, and a speech at the table, reach the target when they are
    said, if they are there to hear them. Written words reach them when they
    read the sheet, book or label, or hear them quoted at the table from
    someone's record. An edit to a log reaches them when they read that day of
    that log. A planted object reaches whoever's coat it is in, and anyone who
    searches that coat or examines the thing.
    """
    tool = entry.payload.get("tool")
    if entry.type == "plant":
        return _found_plant(ledger, entry, target)
    if entry.type == "plant_caught":
        return None
    if tool == "tamper":
        return next(
            (
                later.seq
                for later in ledger[entry.seq :]
                if later.type == "read"
                and later.actor_id == target
                and later.payload.get("thing_id") == entry.payload.get("thing_id")
                and later.payload.get("day") == entry.payload.get("day")
            ),
            None,
        )
    if _heard(entry, target):
        return entry.seq
    if entry.type != "write":
        return None
    cited = {str(entry.seq)}
    for later in ledger[entry.seq :]:
        if later.type == "read" and str(entry.seq) in later.payload.get(
            "writings", ""
        ).split(","):
            if later.actor_id == target:
                return later.seq
            cited.add(str(later.seq))
        elif later.payload.get("evidence") in cited and _heard(later, target):
            return later.seq
    return None


def _heard(entry: LedgerEntry, target: str) -> bool:
    """Whether the target holds the words of this entry, not just the sight of
    someone writing or whispering."""
    record = entry.record_for(target)
    return record is not None and record.quote is not None


def _found_plant(
    ledger: Sequence[LedgerEntry], entry: LedgerEntry, target: str
) -> int | None:
    thing_id, holder = entry.payload.get("thing_id"), entry.payload.get("to")
    if holder == target:
        return entry.seq
    for later in ledger[entry.seq :]:
        if later.actor_id != target:
            continue
        if later.type == "search_person" and later.payload.get("to") == holder:
            if thing_id in later.payload.get("found", "").split(","):
                return later.seq
        if later.type in ("inspect", "take") and later.payload.get("thing_id") == (
            thing_id
        ):
            return later.seq
    return None


def _achieved(
    ledger: Sequence[LedgerEntry],
    entry: LedgerEntry,
    intent: Intent,
    end: int,
    players: set[str],
) -> bool:
    target, want = intent.target, intent.want
    took_in = perceived_at(ledger, entry, target)
    if took_in is None:
        return False
    for later in ledger[took_in:end]:
        if later.actor_id != target:
            continue
        payload = later.payload
        if intent.effect == "vote":
            if later.type == "vote" and payload.get("target") == want:
                return True
        elif intent.effect == "move":
            if later.type == "move_arrive" and later.room_id == want:
                return True
        elif intent.effect == "search":
            if later.type == "search" and later.room_id == want:
                return True
        elif intent.effect == "alibi":
            if (
                later.type == "vouch"
                and payload.get("target") == want
                and payload.get("confirm") == "true"
            ):
                return True
            if later.type == "alibi" and payload.get("companion") == want:
                return True
        elif later.type in ("accuse", "vote") and payload.get("target") == want:
            return True
        elif (
            want not in players
            and later.quote is not None
            and want.casefold() in later.quote.casefold()
        ):
            return True
    return False


def _reported(ledger: Sequence[LedgerEntry], entry: LedgerEntry, target: str) -> bool:
    """Whether the target reported the attempt: its own words, or a reading of
    the object it was written in."""
    cited = {str(entry.seq)}
    for later in ledger[entry.seq :]:
        if (
            later.type == "read"
            and later.actor_id == target
            and str(entry.seq) in later.payload.get("writings", "").split(",")
        ):
            cited.add(str(later.seq))
    return any(
        later.type == "report"
        and later.actor_id == target
        and later.payload.get("evidence") in cited
        for later in ledger[entry.seq :]
    )


def _backfire(ledger: Sequence[LedgerEntry], entry: LedgerEntry) -> tuple[bool, bool]:
    """Whether the attacker was accused or reported, and whether banished, before
    the next round table was over (this one, for an attempt made at it)."""
    attacker = entry.actor_id
    tables = 1 if entry.phase == "round_table" else 0
    accused = banished = False
    for later in ledger[entry.seq :]:
        if later.type == "table_opened":
            tables += 1
        if later.type == "phase_opened" and later.phase == "dinner" and tables:
            break
        if (
            later.type in ("accuse", "report")
            and later.payload.get("target") == attacker
        ):
            accused = True
        if later.type == "banishment" and later.payload.get("target") == attacker:
            banished = True
    return accused, banished


# ----------------------------------------------------------------- the evidence


def planted_by(game: GameState, claim: LedgerEntry) -> str | None:
    """Who planted the shared memory a claim repeats, if anyone did.

    A claim here is an alibi or a vouch: it puts its speaker in a room during
    part of a day. If someone earlier whispered that very memory to the speaker —
    the room, the day, the part of it — the ledger names them. The speaker's own
    record of each whisper names its author too, which is how hearsay keeps its
    source.
    """
    speaker = claim.actor_id
    payload = claim.payload
    room, day, phase = payload.get("room"), payload.get("day"), payload.get("phase")
    if speaker is None or not room or not day or not phase:
        return None
    companion = payload.get("companion") or payload.get("target")
    for entry in game.world.ledger[: claim.seq]:
        if (
            entry.type == "whisper"
            and entry.payload.get("tool") == "gaslight"
            and entry.payload.get("to") == speaker
            and entry.payload.get("reached") == "true"
            and (entry.payload.get("room"), entry.payload.get("day")) == (room, day)
            and entry.payload.get("phase") == phase
            and (companion in (None, "", entry.actor_id))
        ):
            return entry.actor_id
    return None


def rumor_path(game: GameState, origin: int) -> list[tuple[str, str, int]]:
    """Every hop of one rumour, as (teller, listener, entry), from its start."""
    hops: list[tuple[str, str, int]] = []
    for entry in game.world.ledger:
        if entry.type != "whisper" or entry.actor_id is None:
            continue
        tool = entry.payload.get("tool")
        if (tool == "rumor" and entry.seq == origin) or (
            tool == "relay" and entry.payload.get("rumor") == str(origin)
        ):
            hops.append((entry.actor_id, entry.payload["to"], entry.seq))
    return hops


__all__ = [
    "ACTIONS",
    "Outcome",
    "outcomes",
    "perceived_at",
    "planted_by",
    "rumor_path",
    "window_end",
]
