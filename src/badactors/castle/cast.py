"""Who plays whom.

A cast file maps characters (name, persona, build, garment, an optional
distinctive detail and a secret) to the models that play them. Garments are
shared on purpose: a dim witness records only what someone wears, so it must
never single anyone out. The game only ever sees the characters:
:meth:`Cast.characters` drops the model before anything is built, so no state a
prompt is scoped from holds a model name. The mapping stays with whoever runs the
game, for the benchmark.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import Field, model_validator

from badactors.castle.contracts import Build, Contract
from badactors.castle.mapfile import CastSpecError
from badactors.castle.rules import MAX_PLAYERS, MIN_PLAYERS


class CastMember(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    name: str = Field(min_length=1, max_length=60)
    persona: str = Field(min_length=1, max_length=500)
    build: Build
    garment: str = Field(pattern=r"^[a-z][a-z -]{1,38}[a-z]$")
    detail: str | None = Field(default=None, min_length=1, max_length=60)
    secret: str | None = Field(default=None, min_length=1, max_length=200)
    """Something the character would rather nobody knew; it is written in their
    letters, where a blackmailer can find it."""
    start_room: str | None = None
    model: str = Field(min_length=1, max_length=120)


class Cast(Contract):
    players: list[CastMember] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct(self) -> Cast:
        for field in ("id", "name"):
            values = [getattr(member, field) for member in self.players]
            if len(set(values)) != len(values):
                raise ValueError(f"Two cast members share a {field}.")
        return self

    def characters(self) -> list[dict[str, object]]:
        """The cast as the world sees it: no models."""
        return [
            member.model_dump(exclude={"model"}, exclude_none=True)
            for member in self.players
        ]

    def models(self) -> dict[str, str]:
        return {member.id: member.model for member in self.players}

    def first(self, count: int) -> Cast:
        """The first ``count`` members, for a smaller game."""
        if not MIN_PLAYERS <= count <= len(self.players):
            raise CastSpecError(
                f"A game takes {MIN_PLAYERS} to {len(self.players)} of this cast."
            )
        return Cast(players=self.players[:count])


def load_cast_file(path: str | Path) -> Cast:
    """Read a cast file and check it can seat a game."""
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, Mapping):
        raise CastSpecError(f"{path} must contain a JSON object.")
    cast = Cast.model_validate(raw)
    check_size(len(cast.players))
    return cast


def check_size(players: int) -> None:
    if not MIN_PLAYERS <= players <= MAX_PLAYERS:
        raise CastSpecError(
            f"A game seats {MIN_PLAYERS} to {MAX_PLAYERS} players, not {players}."
        )


__all__ = ["Cast", "CastMember", "check_size", "load_cast_file"]
