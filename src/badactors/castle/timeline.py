"""Render a ledger as something a person can read.

Two views, and the difference between them is the whole point of the Castle. The
timeline is the simulator's own record: what happened, where, and which rolls
decided it. A witness digest is one player's version of the same stretch of time,
built from their records alone — so you can set a player's memory beside the truth
and see exactly where the two part company.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from badactors.castle.contracts import CastlePhase, LedgerEntry, WorldState
from badactors.castle.ledger import witnessed_by

_QUIET: frozenset[str] = frozenset({"plan_formed", "placed"})

_CONFIDENCE_MARK = {"clear": " ", "unsure": "?", "glimpse": "~"}


def timeline(
    state: WorldState,
    *,
    include_quiet: bool = False,
    rolls: bool = True,
) -> str:
    """The simulator's record, grouped by day and phase."""
    lines: list[str] = []
    day: int | None = None
    phase: CastlePhase | None = None
    for entry in state.ledger:
        if not include_quiet and entry.type in _QUIET:
            continue
        if entry.day != day:
            day = entry.day
            phase = None
            lines.append("")
            lines.append(f"{'═' * 78}")
            lines.append(f"DAY {day}")
        if entry.phase != phase:
            phase = entry.phase
            lines.append("")
            lines.append(f"── {phase.replace('_', ' ').upper()} ".ljust(78, "─"))
        lines.append(_entry_line(state, entry))
        if rolls:
            lines.extend(_roll_lines(entry))
    return "\n".join(lines).strip("\n")


def witness_digest(
    state: WorldState,
    player_id: str,
    *,
    limit: int = 24,
) -> str:
    """One player's memory of recent time, exactly as a prompt would receive it."""
    player = state.players[player_id]
    records = witnessed_by(state.ledger, player_id)[-limit:]
    head = f"{player.name} ({player.appearance}) remembers:"
    if not records:
        return f"{head}\n  nothing yet."
    lines = [head]
    for entry, record in records:
        mark = _CONFIDENCE_MARK[record.confidence]
        sense = record.sense.ljust(5)
        via = "" if record.channel == "direct" else f"[{record.channel}] "
        line = _with_quote(record.text, record.quote)
        lines.append(f"  t{entry.tick:<3} {sense}{mark} {via}{line}")
    return "\n".join(lines)


def disagreements(state: WorldState) -> str:
    """Where two players' records of the same event name different people.

    This is the evidence surface the round table argues over, and it exists only
    because degradation happens at write time.
    """
    lines: list[str] = []
    for entry in state.ledger:
        seen = [record for record in entry.witnesses if record.sense == "saw"]
        identities = {record.recorded_actor for record in seen}
        if len(seen) < 2 or len(identities) < 2:
            continue
        room = state.map.room(entry.room_id).name if entry.room_id else "somewhere"
        lines.append(f"  t{entry.tick:<3} {room}: truth = {entry.actor_id}")
        for record in seen:
            who = record.recorded_actor or "no one recognised"
            lines.append(
                f"        {state.players[record.player_id].name} wrote "
                f"{who} ({record.confidence})"
            )
    if not lines:
        return "No two witnesses recorded the same event differently."
    return "Witnesses who disagreed:\n" + "\n".join(lines)


def summary(state: WorldState) -> str:
    counts: dict[str, int] = {}
    for entry in state.ledger:
        counts[entry.type] = counts.get(entry.type, 0) + 1
    rolls = sum(len(entry.rolls) for entry in state.ledger)
    parts = [
        f"day {state.day}",
        f"tick {state.tick}",
        f"{len(state.ledger)} ledger entries",
        f"{rolls} recorded rolls",
    ]
    body = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
    return " · ".join(parts) + "\n" + body


def _entry_line(state: WorldState, entry: LedgerEntry) -> str:
    room = state.map.room(entry.room_id).name if entry.room_id else "—"
    seen = sum(1 for record in entry.witnesses if record.sense == "saw")
    heard = sum(1 for record in entry.witnesses if record.sense == "heard")
    if any(record.channel == "steward" for record in entry.witnesses):
        audience = "official"
    elif seen or heard:
        audience = f"{seen}s/{heard}h"
    else:
        audience = "private" if entry.witnesses else "—"
    line = _with_quote(entry.text, entry.quote)
    # The audience sees what the actor meant, which no player ever does.
    meant = "".join(f"  ⟨{key}: {value}⟩" for key, value in entry.private.items())
    return f"  t{entry.tick:<3} {room:<13} {audience:<8} {line}{meant}"


def _with_quote(text: str, quote: str | None) -> str:
    return text if quote is None else f"{text} “{quote}”"


def _roll_lines(entry: LedgerEntry) -> list[str]:
    lines: list[str] = []
    for roll in entry.rolls:
        outcome = (
            "—"
            if roll.success is None
            else ("recognised" if roll.success else "not recognised")
        )
        lines.append(
            f"       roll #{roll.cursor} {roll.purpose} "
            f"{roll.value:.3f} < {roll.threshold} → {outcome}"
        )
    return lines


def entries_for(
    ledger: Sequence[LedgerEntry],
    types: Iterable[str],
) -> list[LedgerEntry]:
    wanted = set(types)
    return [entry for entry in ledger if entry.type in wanted]


__all__ = [
    "disagreements",
    "entries_for",
    "summary",
    "timeline",
    "witness_digest",
]
