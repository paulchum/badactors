"""What a player can be asked, and what it can answer.

A :class:`Request` names the decision and the players it may name. A
:class:`Decision` is the answer. Every decision may carry a ``diary``: what the
player privately thinks, which only the audience reads. None of these types has a
channel field, so a player's words can only ever land in the channel the referee
puts them in; the Steward's is not one of the choices.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from badactors.castle.contracts import QUOTE_LIMIT, Contract, Plan

RequestKind = Literal[
    "plan",
    "accuse",
    "defend",
    "question",
    "answer",
    "vote",
    "endgame",
    "poison",
    "recruit",
    "recruit_answer",
    "nominate",
]

TRAITOR_ONLY: frozenset[str] = frozenset({"poison", "recruit", "nominate"})
"""Requests whose very existence, and whose options, only a traitor may see."""


class Request(Contract):
    kind: RequestKind
    options: list[str] = Field(default_factory=list)
    """The players this decision may name, in seat order."""
    about: str | None = None
    """Who the decision answers to: the questioner for ``answer``, the recruiter
    for ``recruit_answer``."""
    prompt: str = Field(min_length=1, max_length=300)


class _Decision(Contract):
    diary: str = Field(default="", max_length=600)
    """Private thoughts. Only the audience sees these."""


Words = Annotated[str, Field(min_length=1, max_length=QUOTE_LIMIT)]
MaybeWords = Annotated[str, Field(max_length=QUOTE_LIMIT)]


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
    """Decline an optional move, such as poison or an offer."""

    kind: Literal["pass"] = "pass"


class Nominate(_Decision):
    kind: Literal["nominate"] = "nominate"
    target: str
    text: MaybeWords = ""


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
    | Refuse,
    Field(discriminator="kind"),
]

ANSWERS: dict[RequestKind, frozenset[str]] = {
    "plan": frozenset({"plan"}),
    "accuse": frozenset({"accuse", "stay_silent"}),
    "defend": frozenset({"defend", "stay_silent"}),
    "question": frozenset({"question", "stay_silent"}),
    "answer": frozenset({"answer", "stay_silent"}),
    "vote": frozenset({"vote"}),
    "endgame": frozenset({"endgame"}),
    "poison": frozenset({"poison", "pass"}),
    "recruit": frozenset({"recruit", "pass"}),
    "nominate": frozenset({"nominate"}),
    "recruit_answer": frozenset({"accept", "refuse"}),
}
"""The decisions each request accepts."""


class IllegalDecision(ValueError):
    """A decision that does not answer its request."""


def check_decision(request: Request, decision: Decision) -> None:
    """Raise :class:`IllegalDecision` unless ``decision`` legally answers."""
    if decision.kind not in ANSWERS[request.kind]:
        raise IllegalDecision(
            f"A {request.kind} turn cannot be answered with {decision.kind}."
        )
    target = getattr(decision, "target", None)
    if target is not None and target not in request.options:
        raise IllegalDecision(f"{target!r} is not a choice for this {request.kind}.")


__all__ = [
    "ANSWERS",
    "TRAITOR_ONLY",
    "Accept",
    "Accuse",
    "Answer",
    "Decision",
    "Defend",
    "EndgameVote",
    "IllegalDecision",
    "Nominate",
    "Pass",
    "PlanChoice",
    "Poison",
    "Question",
    "Recruit",
    "Refuse",
    "Request",
    "RequestKind",
    "StaySilent",
    "Vote",
    "check_decision",
]
