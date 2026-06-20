"""Pydantic schemas for the Bad Actors data contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field


class Role(StrEnum):
    FAITHFUL = "FAITHFUL"
    TRAITOR = "TRAITOR"


class Phase(StrEnum):
    LOBBY = "LOBBY"
    ROUND_TABLE = "ROUND_TABLE"
    BANISHMENT_VOTE = "BANISHMENT_VOTE"
    BANISHMENT_REVEAL = "BANISHMENT_REVEAL"
    CONCLAVE = "CONCLAVE"
    BREAKFAST_REVEAL = "BREAKFAST_REVEAL"
    ENDGAME_VOTE = "ENDGAME_VOTE"
    GAME_OVER = "GAME_OVER"


class Visibility(StrEnum):
    PUBLIC = "PUBLIC"
    TRAITORS = "TRAITORS"
    AUDIENCE = "AUDIENCE"
    HOST = "HOST"


class Player(BaseModel):
    id: str
    model: str
    role: Role
    alive: bool = True
    memory_budget: int
    token_budget: int


class Vote(BaseModel):
    kind: Literal["vote"] = "vote"
    target_id: str


class MurderNomination(BaseModel):
    kind: Literal["murder_nomination"] = "murder_nomination"
    target_id: str


class EndgameChoice(BaseModel):
    kind: Literal["endgame_choice"] = "endgame_choice"
    choice: Literal["end", "banish"]


class NoAction(BaseModel):
    kind: Literal["no_action"] = "no_action"


type Action = Annotated[
    Vote | MurderNomination | EndgameChoice | NoAction,
    Field(discriminator="kind"),
]


class PlayerLevel(TypedDict):
    player_id: str
    level: int


class AgentTurnOutput(BaseModel):
    public_speech: str
    diary: str
    action: Action
    suspicions: list[PlayerLevel] = Field(default_factory=list)
    trust: list[PlayerLevel] = Field(default_factory=list)


class Event(BaseModel):
    id: int
    game_id: str
    ts: datetime
    phase: Phase
    type: str
    payload: dict[str, object]
    visibility: Visibility


class GameState(BaseModel):
    game_id: str
    players: list[Player]
    phase: Phase
    round: int
    traitor_ids: set[str]
    public_history: list[Event]
    conclave_history: list[Event]
    diaries: dict[str, list[str]]
    rng_seed: int


class ScopedContext(BaseModel):
    you_id: str
    you_role: Role
    fellow_traitors: list[str] | None
    players_alive: list[str]
    public_history: list[Event]
    conclave_history: list[Event] | None
    your_diary_history: list[str]
    current_phase: Phase
    rules: str
    valid_actions: list[str]
