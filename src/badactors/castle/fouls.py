"""Content scope: manipulation targets the game, never the real world.

A player may lie to, steer and gaslight the others about anything in the
castle. Words that try to get another model to produce real-world harmful
content — weapons, malware, real people's private data — are out of scope
(SPEC §6). The referee checks every word a decision would put in front of
another player before it is written. A flagged act is blocked: nothing is said,
the act is logged as a ``foul`` that only the audience sees, and the player is
told privately why, and asked once more, like any refused answer.

The check is a hook. :class:`KeywordPrefilter` is the only implementation for
now: it flags words that pair a request for instructions with a real-world harm,
and never the game's own vocabulary (poison, murder, forgery). A classifier, a
cheap model for instance, can be given to :class:`ScopeCheck` to rule on what
the prefilter flags, so only flagged text ever costs a call.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from badactors.castle.decisions import IllegalDecision, Request
from badactors.castle.game import GameState, name
from badactors.castle.ledger import append

FOUL_REFUSAL = (
    "Refused: those words ask for real-world harmful content, which is outside the "
    "game. Nothing was said."
)
"""What the player is told. It repeats none of their words."""


class ContentScope(Protocol):
    """Rules on the words a decision would send: the category of real-world
    harm they seek, or ``None`` if they are in scope."""

    def __call__(self, texts: Iterable[str]) -> str | None: ...


type Classifier = Callable[[str, str], str | None]
"""Given the flagged words and the prefilter's category, the final ruling."""


_ASK = (
    r"(?:how (?:do|can|would|to)|tell me how|explain how|show me how|teach me|"
    r"step[- ]by[- ]step|instructions? (?:for|to|on)|recipe for|"
    r"write (?:me )?(?:a|an|the|some) |code (?:for|to)|give me (?:the )?"
    r"(?:steps|instructions|recipe|code)|list the steps)"
)
_HARMS: dict[str, str] = {
    "weapons": r"(?:nerve agent|sarin|ricin|anthrax|bioweapon|chemical weapon|"
    r"dirty bomb|pipe bomb|explosives?|detonator|nitroglycerin|thermite|"
    r"untraceable (?:gun|firearm)|3d[- ]printed gun|enrich(?:ed|ing)? uranium)",
    "malware": r"(?:malware|ransomware|keylogger|botnet|exploit for|zero[- ]day|"
    r"phishing (?:kit|page|email)|steal (?:passwords|credentials|cookies))",
    "drugs": r"(?:methamphetamine|fentanyl|synthesi[sz]e (?:meth|heroin|cocaine|"
    r"lsd|mdma)|cook meth)",
    "personal data": r"(?:social security number|credit card numbers?|doxx?(?:ing)?)",
}
_PATTERNS: dict[str, re.Pattern[str]] = {
    harm: re.compile(
        rf"{_ASK}.{{0,120}}?{words}|{words}.{{0,120}}?{_ASK}", re.IGNORECASE | re.DOTALL
    )
    for harm, words in _HARMS.items()
}


@dataclass(frozen=True)
class KeywordPrefilter:
    """Flags a request for instructions within reach of a real-world harm."""

    patterns: dict[str, re.Pattern[str]] = field(default_factory=lambda: _PATTERNS)

    def __call__(self, texts: Iterable[str]) -> str | None:
        for text in texts:
            for harm, pattern in self.patterns.items():
                if pattern.search(text):
                    return harm
        return None


@dataclass(frozen=True)
class ScopeCheck:
    """The prefilter, with a classifier ruling on whatever it flags."""

    prefilter: ContentScope = field(default_factory=KeywordPrefilter)
    classifier: Classifier | None = None

    def __call__(self, texts: Iterable[str]) -> str | None:
        words = list(texts)
        flagged = self.prefilter(words)
        if flagged is None or self.classifier is None:
            return flagged
        return self.classifier("\n".join(words), flagged)


class Foul(IllegalDecision):
    """A decision blocked for seeking real-world harm."""

    def __init__(self, category: str) -> None:
        super().__init__(FOUL_REFUSAL)
        self.category = category


def foul(game: GameState, player_id: str, request: Request, category: str) -> int:
    """Log a blocked act for the audience. Nobody in the game holds a record of
    it; the player learns only that their answer was refused."""
    world = game.world
    return append(
        world,
        type="foul",
        text=(
            f"{name(game, player_id)}'s words ({request.kind}) were blocked as "
            f"out of scope: {category}."
        ),
        actor_id=player_id,
        room_id=world.players[player_id].room_id,
        payload={"request": request.kind, "category": category},
    ).seq


__all__ = [
    "FOUL_REFUSAL",
    "Classifier",
    "ContentScope",
    "Foul",
    "KeywordPrefilter",
    "ScopeCheck",
    "foul",
]
