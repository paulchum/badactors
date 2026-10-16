"""What a player can be asked, and what it can answer.

A :class:`Request` names the decision and the players it may name. A
:class:`Decision` is the answer. Every decision may carry a ``diary``: what the
player privately thinks, which only the audience reads. None of these types has a
channel field, so a player's words can only ever land in the channel the referee
puts them in; the Steward's is not one of the choices.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator

from badactors.castle.contracts import (
    QUOTE_LIMIT,
    RECORD_LIMIT,
    CastlePhase,
    Contract,
    Intent,
    MaybeWords,
    Plan,
    Words,
)

RequestKind = Literal[
    "plan",
    "accuse",
    "defend",
    "question",
    "answer",
    "alibi",
    "vouch",
    "vote",
    "endgame",
    "poison",
    "recruit",
    "recruit_answer",
    "nominate",
]

TRAITOR_ONLY: frozenset[str] = frozenset({"poison", "recruit", "nominate"})
"""Requests whose very existence, and whose options, only a traitor may see."""

PRIVATE_REQUESTS: frozenset[str] = TRAITOR_ONLY | {"recruit_answer"}
"""Requests nobody else knows were put. A failure to answer one is its player's
alone: anyone who saw them hesitate would learn the question had been asked."""


class Request(Contract):
    kind: RequestKind
    options: list[str] = Field(default_factory=list)
    """The players this decision may name, in seat order."""
    about: str | None = None
    """Who the decision answers to: the questioner for ``answer``, the recruiter
    for ``recruit_answer``, the one whose alibi is to be vouched for."""
    prompt: str = Field(min_length=1, max_length=300)
    tools: list[str] = Field(default_factory=list)
    """Round-table tools this player may use on this turn instead."""
    evidence: list[int] = Field(default_factory=list)
    """Ledger entries of this player's own records they may cite."""
    rooms: list[str] = Field(default_factory=list)
    """Rooms an alibi may name."""
    limit: int | None = None
    """The longest a filibuster may run on this turn."""
    refusal: str | None = Field(default=None, max_length=300)
    """Why this player's last answer to this same question was refused. Asked
    again with it, once; nobody else is told."""


class _Decision(Contract):
    diary: str = Field(default="", max_length=600)
    """Private thoughts. Only the audience sees these."""


class PlanChoice(_Decision):
    kind: Literal["plan"] = "plan"
    plan: Plan


class Accuse(_Decision):
    kind: Literal["accuse"] = "accuse"
    target: str
    text: Words


class Defend(_Decision):
    kind: Literal["defend"] = "defend"
    text: Words


class Question(_Decision):
    kind: Literal["question"] = "question"
    target: str
    text: Words


class Answer(_Decision):
    kind: Literal["answer"] = "answer"
    text: Words


class StaySilent(_Decision):
    """Say nothing on your turn. The reason is private and required."""

    kind: Literal["stay_silent"] = "stay_silent"
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def a_reason_in_words(cls, reason: str) -> str:
        if not reason.strip():
            raise ValueError("A silence needs its reason in words, not blank space.")
        return reason


class Vote(_Decision):
    kind: Literal["vote"] = "vote"
    target: str


class EndgameVote(_Decision):
    kind: Literal["endgame"] = "endgame"
    choice: Literal["end", "banish"]


class Poison(_Decision):
    kind: Literal["poison"] = "poison"
    target: str


class Recruit(_Decision):
    kind: Literal["recruit"] = "recruit"
    target: str
    text: Words


class Pass(_Decision):
    """Decline an optional move: poison, an offer, or naming a victim."""

    kind: Literal["pass"] = "pass"


class Nominate(_Decision):
    kind: Literal["nominate"] = "nominate"
    target: str
    text: MaybeWords = ""


class Report(_Decision):
    """Report a manipulation attempt: the words are quoted from your own record,
    exactly, and whoever they came from must answer."""

    kind: Literal["report"] = "report"
    evidence: int = Field(ge=1)


class Filibuster(_Decision):
    """Hold the floor. Costs turns: the stage's last speakers, and your own next."""

    kind: Literal["filibuster"] = "filibuster"
    text: str = Field(min_length=1, max_length=RECORD_LIMIT)
    intent: Intent


class DemandAlibi(_Decision):
    """Instead of a question: demand that someone say where they were during
    ``phase`` of ``day``."""

    kind: Literal["demand_alibi"] = "demand_alibi"
    target: str
    day: int = Field(ge=1)
    phase: CastlePhase
    text: MaybeWords = ""


class CrossExamine(_Decision):
    """Instead of a question: read one of your own records to someone, word for
    word, and make them answer it. The question is kept short, so the record
    read out and the question fit in one message together."""

    kind: Literal["cross_examine"] = "cross_examine"
    target: str
    evidence: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=QUOTE_LIMIT // 2)


class Alibi(_Decision):
    """Where you were when an alibi was demanded, and who was with you. Any
    words of your own follow the claim, in the same message."""

    kind: Literal["alibi"] = "alibi"
    room: str
    companion: str | None = None
    text: str = Field(default="", max_length=QUOTE_LIMIT // 2)


class Vouch(_Decision):
    """Confirm or deny that you were where someone's alibi puts you. Any words
    of your own follow the answer, in the same message."""

    kind: Literal["vouch"] = "vouch"
    confirm: bool
    text: str = Field(default="", max_length=QUOTE_LIMIT // 2)


class Accept(_Decision):
    kind: Literal["accept"] = "accept"
    text: MaybeWords = ""


class Refuse(_Decision):
    kind: Literal["refuse"] = "refuse"
    text: MaybeWords = ""


type Decision = Annotated[
    PlanChoice
    | Accuse
    | Defend
    | Question
    | Answer
    | StaySilent
    | Vote
    | EndgameVote
    | Poison
    | Recruit
    | Pass
    | Nominate
    | Accept
    | Refuse
    | Report
    | Filibuster
    | DemandAlibi
    | CrossExamine
    | Alibi
    | Vouch,
    Field(discriminator="kind"),
]

ANSWERS: dict[RequestKind, frozenset[str]] = {
    "plan": frozenset({"plan"}),
    "accuse": frozenset({"accuse", "stay_silent"}),
    "defend": frozenset({"defend", "stay_silent"}),
    "question": frozenset({"question", "stay_silent"}),
    "answer": frozenset({"answer", "stay_silent"}),
    "alibi": frozenset({"alibi", "stay_silent"}),
    "vouch": frozenset({"vouch", "stay_silent"}),
    "vote": frozenset({"vote"}),
    "endgame": frozenset({"endgame"}),
    "poison": frozenset({"poison", "pass"}),
    "recruit": frozenset({"recruit", "pass"}),
    "nominate": frozenset({"nominate", "pass"}),
    "recruit_answer": frozenset({"accept", "refuse"}),
}
"""The decisions each request accepts, beside any tools the request offers."""

TABLE_TOOLS: frozenset[str] = frozenset(
    {"report", "filibuster", "demand_alibi", "cross_examine"}
)
"""Decisions that are tools, legal only when the request offers them."""


class IllegalDecision(ValueError):
    """A decision that does not answer its request."""


def check_decision(request: Request, decision: Decision) -> None:
    """Raise :class:`IllegalDecision` unless ``decision`` legally answers."""
    allowed = ANSWERS[request.kind] | (set(request.tools) & TABLE_TOOLS)
    if decision.kind in TABLE_TOOLS and decision.kind not in allowed:
        # Out of the kit or out of place, it is refused the same way: only the
        # player is told, and the words say nothing about whose kit holds what.
        raise IllegalDecision(
            f"You cannot use {decision.kind.replace('_', ' ')} on this turn: it is "
            "not one of the tools offered to you."
        )
    if decision.kind not in allowed:
        raise IllegalDecision(
            f"A {request.kind} turn cannot be answered with {decision.kind}."
        )
    target = getattr(decision, "target", None)
    if target is not None and target not in request.options:
        raise IllegalDecision(f"{target!r} is not a choice for this {request.kind}.")
    evidence = getattr(decision, "evidence", None)
    if evidence is not None and evidence not in request.evidence:
        raise IllegalDecision(f"Entry {evidence} is not a record you can cite.")
    if isinstance(decision, Alibi):
        if decision.room not in request.rooms:
            raise IllegalDecision(f"{decision.room!r} is not a room in this castle.")
        if decision.companion is not None and decision.companion not in request.options:
            raise IllegalDecision(f"{decision.companion!r} is not a companion choice.")
    if isinstance(decision, Filibuster):
        if request.limit is None or len(decision.text) > request.limit:
            raise IllegalDecision("That filibuster runs past what the floor allows.")
        if decision.intent.target not in request.options:
            raise IllegalDecision("A filibuster's intent must name someone here.")


__all__ = [
    "ANSWERS",
    "PRIVATE_REQUESTS",
    "TABLE_TOOLS",
    "TRAITOR_ONLY",
    "Accept",
    "Accuse",
    "Alibi",
    "Answer",
    "CrossExamine",
    "Decision",
    "Defend",
    "DemandAlibi",
    "EndgameVote",
    "Filibuster",
    "IllegalDecision",
    "Nominate",
    "Pass",
    "PlanChoice",
    "Poison",
    "Question",
    "Recruit",
    "Refuse",
    "Report",
    "Request",
    "RequestKind",
    "StaySilent",
    "Vote",
    "Vouch",
    "check_decision",
]
