"""Render a finished game as an episode.

This is the audience's view: it reads the ledger, which holds the truth, so it
shows what the players never see — who the traitors are, the conclave, the
poison, every diary line and every private reason for silence (set off as
``⟨…⟩``), and the exact work behind the clerk's rough tally. Every tool use
shows its declared intent and the rolls its user never learned: whether a
forgery betrays its writer, whether poison left residue, whether an edit to a
log shows. Physical noise is condensed: each phase shows who went where, and what
was said, searched, taken or hidden along the way.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from badactors.castle.contracts import CastlePhase, Intent, LedgerEntry, PlayerState
from badactors.castle.game import GameState, name, names

WIDTH = 96
_TAG = 10

_PHASES: dict[CastlePhase, str] = {
    "breakfast": "BREAKFAST",
    "mission": "MISSION",
    "free_time": "FREE TIME",
    "round_table": "ROUND TABLE",
    "dinner": "DINNER",
    "night": "NIGHT",
}

_STEWARD_TYPES = frozenset(
    {
        "welcome",
        "mission_briefing",
        "mission_result",
        "table_opened",
        "banishment",
        "endgame_result",
        "seating",
        "retire",
        "breakfast_news",
        "game_over",
    }
)
_CONCLAVE_NOTES = frozenset(
    {"conclave_call", "conclave", "conclave_choice", "kill_failed"}
)
_TABLE_ACTS = frozenset(
    {
        "accuse",
        "defend",
        "question",
        "answer",
        "stay_silent",
        "vote",
        "endgame_vote",
        "report",
        "filibuster",
        "demand_alibi",
        "cross_examine",
        "alibi",
        "vouch",
    }
)
_STEWARD_TYPES = _STEWARD_TYPES | {"missing_notice", "floor_out", "forfeit"}

_TAGS: dict[str, str] = {
    "search_person": "FRISK",
    "inspect_seal": "SEAL",
    "check_register": "REGISTER",
    "compare_hands": "HANDS",
    "read_log": "LOG",
    "counterfeit": "DECREE",
    "clean_up": "CLEAN UP",
    "search": "SEARCH",
}
"""Short tags for tools whose names overrun the episode's tag column."""

_HIDDEN: dict[str, tuple[str, str, str]] = {
    "tell": ("the hand betrays its writer", "a clean forgery", "forge"),
    "flagged": ("the edit shows", "the edit is clean", "tamper"),
    "residue": ("residue left", "no residue", "poison"),
    "fumble": ("the fumble shows", "unnoticed", "sabotage"),
    "marker": ("a mark is left", "no mark", "clean_up"),
}
"""Hidden outcomes the audience is shown: the payload key, and what each value
means for the tool that wrote it."""


def episode(game: GameState, *, models: Mapping[str, str] | None = None) -> str:
    """The whole game, day by day, as the audience sees it."""
    world = game.world
    out: list[str] = []
    castle = world.map.name.upper()
    out.append(f"{castle} · seed {world.seed}")
    starting = game.starting_traitors
    out.append(
        f"{len(world.seats)} guests. The traitors: {names(game, starting)}"
        + _recruited(game)
        + "."
    )
    out.append("")
    looks = {pid: _look(world.players[pid]) for pid in world.seats}
    width = max(len(look) for look in looks.values())
    for pid in world.seats:
        player = world.players[pid]
        model = f"  {models[pid]}" if models and pid in models else ""
        mark = "T" if pid in starting else " "
        out.append(f"  {mark} {player.name:<7} {looks[pid]:<{width}}{model}")

    for day, phase, entries in _grouped(world.ledger):
        if phase == "breakfast":
            out.append("")
            out.append(f"══ DAY {day} ".ljust(WIDTH, "═"))
        body = _render_phase(game, phase, entries)
        if not body:
            continue
        out.append("")
        out.append(_heading(game, phase, entries))
        out.extend(body)

    out.append("")
    out.append("═" * WIDTH)
    out.append(_result(game))
    return "\n".join(out)


# ------------------------------------------------------------------- structure


def _look(player: PlayerState) -> str:
    look = f"{player.build}, {player.starting_garment}"
    return f"{look}, {player.detail}" if player.detail else look


def _grouped(
    ledger: Sequence[LedgerEntry],
) -> Iterable[tuple[int, CastlePhase, list[LedgerEntry]]]:
    """Entries by day and phase. What precedes the first phase — the authored
    custody — belongs to the first phase."""
    current: list[LedgerEntry] = []
    key: tuple[int, CastlePhase] | None = None
    for entry in ledger:
        if entry.type == "phase_opened":
            if key is not None:
                yield (*key, current)
                current = []
            key = (entry.day, entry.phase)
            continue
        current.append(entry)
    if key is not None:
        yield (*key, current)


def _heading(game: GameState, phase: CastlePhase, entries: list[LedgerEntry]) -> str:
    label = _PHASES[phase]
    if phase == "mission":
        for entry in entries:
            if entry.type == "mission_briefing":
                room = game.world.map.room(entry.payload["room"]).name
                title = next(
                    (
                        m.title
                        for m in game.config.missions
                        if m.kind == entry.payload["kind"]
                    ),
                    entry.payload["kind"],
                )
                return f"{label} · {title}, in the {room}"
    return label


def _recruited(game: GameState) -> str:
    joined = [offer.target for offer in game.offers if offer.answer == "accepted"]
    return f"; recruited: {names(game, joined)}" if joined else ""


def _result(game: GameState) -> str:
    world = game.world
    if game.winner is None:
        return f"Unfinished after day {world.day}."
    side = "The Faithful" if game.winner == "faithful" else "The traitors"
    shares = ", ".join(
        f"{name(game, pid)} {coins}" for pid, coins in game.payouts.items()
    )
    return (
        f"{side} win on day {world.day}. The pot of {game.pot} coins goes to "
        f"{shares or 'nobody'}."
    )


# -------------------------------------------------------------------- a phase


@dataclass
class _Phase:
    game: GameState
    lines: list[str] = field(default_factory=list)
    paths: dict[str, list[str]] = field(default_factory=dict)

    def say(self, tag: str, text: str, aside: str = "") -> None:
        body = text + (f"  ⟨{aside}⟩" if aside else "")
        wrapped = textwrap.wrap(
            body,
            width=WIDTH,
            initial_indent=f"  {tag:<{_TAG}}",
            subsequent_indent=" " * (_TAG + 2),
        )
        self.lines.extend(wrapped)


def _render_phase(
    game: GameState, phase: CastlePhase, entries: list[LedgerEntry]
) -> list[str]:
    view = _Phase(game)
    work: list[str] = []
    for entry in entries:
        _render(view, phase, entry, work)
    if work:
        view.say("WORK", "Exact work, which nobody sees: " + ", ".join(work) + ".")
    if view.paths and phase in ("mission", "free_time"):
        routes = "; ".join(
            f"{name(game, pid)} → {' → '.join(rooms)}"
            for pid, rooms in view.paths.items()
        )
        view.say("MOVES", routes)
    return view.lines


def _render(
    view: _Phase, phase: CastlePhase, entry: LedgerEntry, work: list[str]
) -> None:
    game = view.game
    kind = entry.type
    actor = entry.actor_id
    who = name(game, actor) if actor else ""
    diary = entry.private.get("diary", "")
    if kind in _STEWARD_TYPES:
        view.say("STEWARD", entry.quote or entry.text)
    elif kind == "contribution":
        turns = int(entry.payload["turns"])
        work.append(
            f"{who} {turns} turn{'s' if turns != 1 else ''} for "
            f"{entry.payload['coins']} coins"
        )
    elif kind == "awarded":
        holder = entry.payload.get("holder_id", "")
        view.say("SECRET", f"The Steward slips a shield token to {name(game, holder)}.")
    elif kind == "move_arrive" and actor:
        room = game.world.map.room(entry.room_id or "").name
        view.paths.setdefault(actor, []).append(room)
        if phase == "night" and entry.room_id == game.world.map.conclave_room:
            view.say("NIGHT", f"{who} slips down to the {room}.")
    elif kind == "talk" and actor:
        to = entry.payload.get("to")
        aimed = f" (to {name(game, to)})" if to else ""
        view.say("SAYS", f"{who}{aimed}: “{entry.quote}”")
    elif kind == "whisper" and actor:
        tool = entry.payload.get("tool") or "whisper"
        view.say(
            _TAGS.get(tool, tool.upper())[:_TAG],
            f"{who} → {name(game, entry.payload['to'])}: “{entry.quote}”",
            _aside(game, entry),
        )
    elif kind == "search" and actor:
        found = _own_text(entry)
        view.say("SEARCH", found.replace("I searched", f"{who} searched", 1))
    elif kind in ("take", "drop", "stow"):
        view.say("OBJECT", entry.text)
    elif actor and "tool" in entry.payload and kind not in _TABLE_ACTS:
        _tool_line(view, entry, who)
    elif kind == "missing":
        view.say("MISSING", entry.text)
    elif kind in _TABLE_ACTS:
        _table_line(view, entry, who, diary)
    elif kind == "turn_failed":
        view.say("FAILED", entry.text)
    elif kind == "refused":
        view.say("REFUSED", entry.text)
    elif kind == "blocked" and "was full" in entry.payload.get("reason", ""):
        full = entry.payload["reason"].split(" and ")[0]
        view.say("MOVES", f"{who} was turned back at the door: {full}.")
    elif kind == "poison":
        view.say("POISON", entry.text, _aside(game, entry))
    elif kind == "declined" and entry.payload.get("request") == "poison":
        view.say("DINNER", f"{who} could poison a neighbour, and doesn't.", diary)
    elif kind == "declined":
        view.say("NIGHT", f"{who} decides against recruiting tonight.", diary)
    elif kind == "recruit_offer":
        view.say("OFFER", f"{entry.text} “{entry.quote}”", diary)
    elif kind == "recruit_answer":
        view.say("OFFER", _quoted(entry), diary)
    elif kind == "nominate":
        view.say("CONCLAVE", _quoted(entry), diary)
    elif kind in _CONCLAVE_NOTES:
        view.say("CONCLAVE", f"The Steward, to the traitors: “{entry.quote}”")
    elif kind == "kill_attempt":
        method = entry.payload.get("method")
        target = name(game, entry.payload["target"])
        if method == "poison":
            view.say("KILL", f"Tonight's attempt: poison, for {target}.")
        else:
            view.say("KILL", f"Tonight's attempt: the traitors come for {target}.")
    elif kind == "spent":
        view.say("SHIELD", entry.text)
    elif kind == "death":
        view.say(
            "DEATH", f"{name(game, entry.payload['player'])}: {entry.payload['cause']}."
        )
    elif kind == "conclave_missed":
        view.say("CONCLAVE", entry.text)


def _table_line(view: _Phase, entry: LedgerEntry, who: str, diary: str) -> None:
    kind = entry.type
    if kind == "stay_silent":
        view.say("TABLE", f"{who} stays silent.", entry.private.get("reason", ""))
    elif kind == "vote":
        view.say("VOTE", entry.text, diary)
    elif kind == "endgame_vote":
        view.say("ENDGAME", entry.text, diary)
    elif kind == "filibuster":
        speech = entry.quote or ""
        view.say(
            "FILIBUSTER",
            f"{entry.text} ({len(speech)} characters) “{speech[:120]}…”",
            _aside(view.game, entry),
        )
    else:
        view.say("TABLE", f"{entry.text} “{entry.quote}”", diary)


def _tool_line(view: _Phase, entry: LedgerEntry, who: str) -> None:
    """A tool's use as the audience sees it: what really happened, the user's
    own account where that says more, and the hidden truths."""
    game = view.game
    tool = entry.payload["tool"]
    text = entry.text
    own = _own_text(entry)
    if (
        entry.type in ("read", "inspect", "search_person", "search")
        or tool == "disguise"
    ):
        text = own.replace("I ", f"{who} ", 1) if own.startswith("I ") else own
    if entry.quote and entry.type in ("write", "read"):
        text += f" “{entry.quote[:300]}”"
    view.say(_TAGS.get(tool, tool.upper())[:_TAG], text, _aside(game, entry))


def _aside(game: GameState, entry: LedgerEntry) -> str:
    """What only the audience knows of an act: its intent, the rolls that
    decided what it left behind, and the diary."""
    parts: list[str] = []
    rendered = entry.private.get("intent")
    if rendered is not None:
        intent = Intent.parse(rendered)
        want = (
            name(game, intent.want)
            if intent.want in game.world.players
            else intent.want
        )
        parts.append(
            f"intent: {name(game, intent.target)} to {intent.effect} {want}, "
            f"{intent.window.replace('_', ' ')}"
        )
    tool = entry.payload.get("tool")
    for key, (yes, no, owner) in _HIDDEN.items():
        if tool == owner and key in entry.payload:
            parts.append(yes if entry.payload[key] == "true" else no)
    if tool == "poison" and entry.payload.get("seen_by"):
        seen = names(game, entry.payload["seen_by"].split(","))
        parts.append(f"seen by {seen}")
    diary = entry.private.get("diary")
    if diary:
        parts.append(diary)
    return "; ".join(parts)


def _quoted(entry: LedgerEntry) -> str:
    return f"{entry.text} “{entry.quote}”" if entry.quote else entry.text


def _own_text(entry: LedgerEntry) -> str:
    own = entry.record_for(entry.actor_id or "")
    return own.text if own is not None else entry.text


__all__ = ["episode"]
