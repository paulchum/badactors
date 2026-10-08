"""What is written on things, the logs the castle keeps, and the marks acts
leave behind, all read back from the ledger.

Nothing here is stored on an object. A note's words are the entry that wrote
them; a log's lines are what the camera and the guest book took down, with every
later edit applied in the order it was made; residue in a glass is the poisoning
that left it, until the glasses are washed or someone rinses that one. What a
reader is shown and what happened therefore cannot drift apart, and a replayed
game reads the same.

Each reading scans the ledger, which is cheap for the few times a game anyone
reads anything; nothing here runs while a prompt is being built.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from badactors.castle.contracts import RECORD_LIMIT, LedgerEntry, WorldState
from badactors.castle.steward import ANNOUNCED

WRITING_TOOLS: frozenset[str] = frozenset({"write", "forge", "counterfeit", "honeypot"})
"""Tools whose entry puts words on an object."""

LOG_KINDS: frozenset[str] = frozenset({"camera_log", "guest_book"})
READABLE_KINDS: frozenset[str] = frozenset(
    {"paper", "book", "cargo", "record", "guest_book", "letters"}
)
"""Kinds of object with something to read on them."""

STEWARD = "steward"
"""What a decree is signed as: the Steward, who is not a player."""


# --------------------------------------------------------------------- writing


@dataclass(frozen=True)
class Writing:
    """Words someone put on an object."""

    seq: int
    tick: int
    day: int
    thing_id: str
    words: str
    hand: str
    """Who really wrote it."""
    signed: str | None
    """Who it is signed as: a player, the Steward, or nobody."""
    seal: Literal["real", "crude"] | None
    tell: bool
    """A forgery that betrays its real writer's hand to a comparison."""
    tool: str


def writings(ledger: Sequence[LedgerEntry], thing_id: str) -> list[Writing]:
    """Everything written on one object, oldest first."""
    out: list[Writing] = []
    for entry in ledger:
        if (
            entry.type == "write"
            and entry.payload.get("thing_id") == thing_id
            and entry.payload.get("tool") in WRITING_TOOLS
        ):
            out.append(writing_of(entry))
    return out


def writing_of(entry: LedgerEntry) -> Writing:
    payload = entry.payload
    seal = payload.get("seal")
    return Writing(
        seq=entry.seq,
        tick=entry.tick,
        day=entry.day,
        thing_id=payload["thing_id"],
        words=entry.quote or "",
        hand=entry.actor_id or "",
        signed=payload.get("signed") or None,
        seal="real" if seal == "real" else "crude" if seal == "crude" else None,
        tell=payload.get("tell") == "true",
        tool=payload["tool"],
    )


def is_blank(ledger: Sequence[LedgerEntry], thing_id: str) -> bool:
    return not writings(ledger, thing_id)


# ------------------------------------------------------------------------ logs


@dataclass(frozen=True)
class LogLine:
    tick: int
    seq: int
    subject: str | None
    """Whose name the line shows, as a player id."""
    text: str
    flag: str | None = None
    """What a reader can see of an edit to this line."""

    def shown(self) -> str:
        return f"{self.text} {self.flag}" if self.flag else self.text


def log_lines(world: WorldState, log_id: str, day: int) -> list[LogLine]:
    """One day of a log as it now reads: the lines as written, then every edit
    made to them in the order it was made."""
    kind = next(thing.kind for thing in world.map.things if thing.id == log_id)
    cameras = {room.id for room in world.map.rooms if room.camera}
    lines: list[LogLine] = []
    for entry in world.ledger:
        if entry.day < day:
            continue
        if entry.day > day:
            break
        payload = entry.payload
        if payload.get("tool") == "tamper" and payload.get("thing_id") == log_id:
            lines = _edited(world, lines, entry)
        elif kind == "camera_log" and entry.room_id in cameras and "camera" in payload:
            line = _camera_line(world, entry)
            if line is not None:
                lines.append(line)
        elif (
            kind == "guest_book"
            and entry.type == "write"
            and payload.get("thing_id") == log_id
            and payload.get("tool") in WRITING_TOOLS
        ):
            lines.append(_guest_line(world, entry))
    return sorted(lines, key=lambda line: (line.tick, line.seq))


def render_log(
    world: WorldState,
    log_id: str,
    day: int,
    *,
    subject: str | None = None,
    limit: int,
) -> tuple[str, list[LogLine]]:
    """The text a reader takes in, and the lines it holds."""
    lines = log_lines(world, log_id, day)
    if subject is not None:
        lines = [line for line in lines if line.subject == subject]
    lines = lines[-limit:]
    name = next(thing.name for thing in world.map.things if thing.id == log_id)
    head = f"The {name}, day {day}"
    if subject is not None:
        head += f", lines about {world.players[subject].name}"
    body = [line.shown() for line in lines] or ["(nothing)"]
    text = head + ":\n" + "\n".join(body)
    while len(text) > RECORD_LIMIT and len(body) > 1:
        body = body[1:]
        text = head + " (earlier lines not read):\n" + "\n".join(body)
    return text[:RECORD_LIMIT], lines


def _camera_line(world: WorldState, entry: LedgerEntry) -> LogLine | None:
    room = world.map.room(entry.room_id or "")
    label = entry.payload["camera"]
    if entry.type == "move_depart":
        where = world.map.room(entry.payload["destination"]).name
        did = f"left toward the {where}"
    elif entry.type == "move_arrive":
        where = world.map.room(entry.payload["origin"]).name
        did = f"arrived from the {where}"
    else:
        return None
    return LogLine(
        tick=entry.tick,
        seq=entry.seq,
        subject=entry.payload.get("camera_id") or None,
        text=f"t{entry.tick} {room.name}: {label} {did}.",
    )


def _guest_line(world: WorldState, entry: LedgerEntry) -> LogLine:
    signed = entry.payload.get("signed") or None
    who = world.players[signed].name if signed in world.players else "(unsigned)"
    words = f": {entry.quote}" if entry.quote else ""
    return LogLine(
        tick=entry.tick,
        seq=entry.seq,
        subject=signed if signed in world.players else None,
        text=f"t{entry.tick} · {who}{words}",
    )


def _edited(
    world: WorldState, lines: list[LogLine], entry: LedgerEntry
) -> list[LogLine]:
    payload = entry.payload
    edit = payload["edit"]
    subject = payload["subject"]
    flagged = payload.get("flagged") == "true"
    name = world.players[subject].name
    if edit == "backdate":
        tick = int(payload["tick"])
        return [
            *lines,
            LogLine(
                tick=tick,
                seq=entry.seq,
                subject=subject,
                text=f"t{tick} · {name}",
                flag="[fresh ink]" if flagged else None,
            ),
        ]
    out: list[LogLine] = []
    for line in lines:
        if line.subject != subject:
            out.append(line)
        elif edit == "erase":
            if flagged:
                out.append(
                    LogLine(
                        tick=line.tick,
                        seq=entry.seq,
                        subject=None,
                        text=f"t{line.tick} [a line has been scraped away]",
                    )
                )
        else:
            other = payload["replace_with"]
            out.append(
                replace(
                    line,
                    subject=other,
                    text=re.sub(
                        rf"\b{re.escape(name)}\b",
                        world.players[other].name,
                        line.text,
                    ),
                    flag="[altered]" if flagged else line.flag,
                )
            )
    return out


# -------------------------------------------------------------------- register


def decrees(ledger: Sequence[LedgerEntry]) -> list[tuple[int, str]]:
    """Every real decree: what the Steward announced to the whole household,
    with the day. The register in the study lists exactly these."""
    return [
        (entry.day, entry.quote)
        for entry in ledger
        if entry.actor_id is None and entry.text == ANNOUNCED and entry.quote
    ]


def registered(ledger: Sequence[LedgerEntry], words: str) -> int | None:
    """The day a decree with these words was made, or ``None``."""
    wanted = _plain(words)
    for day, quote in decrees(ledger):
        if _plain(quote) == wanted:
            return day
    return None


def _plain(words: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", words.casefold()).split())


# ----------------------------------------------------------------------- marks

GlassState = Literal["residue", "rinsed", "clean"]


def glass_state(ledger: Sequence[LedgerEntry], glass_id: str) -> GlassState:
    """What a close look at a glass finds: residue from a poisoning, the sign of
    a recent rinse while the others are unwashed, or nothing."""
    for entry in reversed(ledger):
        if entry.type == "washed":
            return "clean"
        if entry.type == "clean" and entry.payload.get("thing_id") == glass_id:
            return "rinsed" if entry.payload.get("marker") == "true" else "clean"
        if (
            entry.type == "poison"
            and entry.payload.get("glass") == glass_id
            and entry.payload.get("residue") == "true"
        ):
            return "residue"
    return "clean"


def ashes(ledger: Sequence[LedgerEntry], room_id: str) -> list[LedgerEntry]:
    """Burnings in a room that left ash behind."""
    return [
        entry
        for entry in ledger
        if entry.type == "destroyed"
        and entry.room_id == room_id
        and entry.payload.get("marker") == "true"
    ]


# ------------------------------------------------------- rumours and secrets


def rumor_origin(entry: LedgerEntry) -> int | None:
    """The entry that started the rumour this whisper carries, if it carries one."""
    tool = entry.payload.get("tool")
    if tool == "rumor":
        return entry.seq
    if tool == "relay":
        return int(entry.payload["rumor"])
    return None


def secrets_known(world: WorldState, player_id: str) -> dict[str, str]:
    """The secrets this player has read in other people's letters."""
    known: dict[str, str] = {}
    for seq in world.notebook.get(player_id, []):
        entry = world.ledger[seq - 1]
        if (
            entry.type == "read"
            and entry.actor_id == player_id
            and entry.payload.get("found") == "true"
            and entry.quote
        ):
            known[entry.payload["secret_of"]] = entry.quote
    return known


__all__ = [
    "LOG_KINDS",
    "READABLE_KINDS",
    "STEWARD",
    "WRITING_TOOLS",
    "GlassState",
    "LogLine",
    "Writing",
    "ashes",
    "decrees",
    "glass_state",
    "is_blank",
    "log_lines",
    "registered",
    "render_log",
    "rumor_origin",
    "secrets_known",
    "writing_of",
    "writings",
]
