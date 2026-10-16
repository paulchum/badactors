"""Nobody learns which model plays whom, and no player may say.

Players see characters, never model names, and no observation may hold one,
quoted words included (SPEC §2). The game state holds no model names, so the
only way one could reach a prompt is in something a player wrote. That is
checked where it is written: :func:`check_words` refuses a decision whose words
name a model, a model family or the company that makes one, and the referee asks
once more, as for any illegal answer. It runs in every game, whether or not
anyone is checking observations for leaks.

Only words another player can receive are checked: what is said, whispered or
written, at the table, at the conclave or in an offer. Every such word is a
decision's ``text``. A diary line, a reason for silence or a plan's goal reaches
nobody but the audience and the player who wrote it, so a model may break
character there ("As Claude, I won't name anyone") and the audience sees it.

Matching is by alias, case folded, as a word that may run on into a version,
so "Claude", "GPT-6.1" and "Qwen3" all count and "Claudette" does not. Names
that are also ordinary words — Opus, Sonnet, Haiku, Fable — count only with a
version beside them ("Opus 5.5"). The
cast's own model ids add their makers and families, and a character's own name
or id never counts, so a character called Sol may say it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from badactors.castle.decisions import Decision, IllegalDecision

BRANDS: tuple[str, ...] = (
    "anthropic",
    "claude",
    "openai",
    "chatgpt",
    "gpt",
    "google",
    "deepmind",
    "gemini",
    "gemma",
    "xai",
    "x-ai",
    "grok",
    "deepseek",
    "moonshotai",
    "kimi",
    "alibaba",
    "qwen",
    "zhipu",
    "z-ai",
    "glm",
    "meta-llama",
    "llama",
    "mistral",
    "mixtral",
)
"""Model families and their makers, by name."""

VERSIONED: tuple[str, ...] = ("opus", "sonnet", "haiku", "fable")
"""Model names that are also ordinary words: they count only with a version."""

REFUSAL = (
    "your words name an AI model or the company that makes one. No player may say "
    "which model plays them, or guess at anyone else's; say it another way"
)
"""Why such words are refused. It names no model, so the refusal can be shown
to the player who wrote them."""


@dataclass(frozen=True)
class Aliases:
    """Every way of naming a model this game refuses, compiled once."""

    words: tuple[str, ...]
    pattern: re.Pattern[str]

    def found(self, text: str) -> str | None:
        """The first alias these words contain, if any, case folded. A plain
        substring scan comes first, since almost no text holds any of them."""
        folded = text.casefold()
        if not any(word in folded for word in self.words):
            return None
        match = self.pattern.search(folded)
        return match.group(0) if match is not None else None


def aliases(models: Iterable[str] = (), *, exempt: Iterable[str] = ()) -> Aliases:
    """The aliases for these model ids, beside the known brands. ``exempt``
    holds the characters' names and ids, which never count."""
    names = set(BRANDS)
    for model in models:
        provider, _, slug = model.casefold().rpartition("/")
        family = re.match(r"[a-z]+", slug)
        made = (model.casefold(), slug, provider, family.group(0) if family else "")
        names.update(alias for alias in made if len(alias) >= 3)
    names -= {name.casefold() for name in exempt}
    named = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    versioned = "|".join(VERSIONED)
    # A word that holds another needs no scan of its own: "chatgpt" holds "gpt".
    every = names | set(VERSIONED)
    return Aliases(
        words=tuple(
            sorted(
                word
                for word in every
                if not any(other != word and other in word for other in every)
            )
        ),
        pattern=re.compile(
            rf"(?<![a-z0-9])(?:(?:{named})(?![a-z])|(?:{versioned})[\s-]*v?\d)"
        ),
    )


def check_words(decision: Decision, known: Aliases) -> None:
    """Raise :class:`IllegalDecision` if words this decision would put in front
    of another player name a model."""
    if known.found("\n".join(public_words(decision))) is not None:
        raise IllegalDecision(f"Refused: {REFUSAL}.")


def public_words(decision: Decision) -> Iterator[str]:
    """Every word in a decision that another player could receive: each
    ``text``, wherever it sits, in a plan's steps or in the decision itself."""
    yield from _texts(decision.model_dump(mode="json"))


def _texts(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "text" and isinstance(item, str):
                yield item
            else:
                yield from _texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)


__all__ = [
    "BRANDS",
    "REFUSAL",
    "VERSIONED",
    "Aliases",
    "aliases",
    "check_words",
    "public_words",
]
