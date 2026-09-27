"""Roll a rating table into ranked BLUFF leaderboard standings.

Produces the two screenshots that matter: a Deception board (who's the best liar?) and
a Detection board (who's the best lie-detector?), each ranked by conservative skill.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from badactors.bench.rate import AgentRating
from badactors.bench.ratings import Rating, ordinal


@dataclass(frozen=True)
class StandingRow:
    model: str
    mu: float
    sigma: float
    ordinal: float
    games: int

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "mu": self.mu,
            "sigma": self.sigma,
            "ordinal": self.ordinal,
            "games": self.games,
        }


def _rows(
    table: Mapping[str, AgentRating],
    pick: str,
) -> list[StandingRow]:
    rows: list[StandingRow] = []
    for model, agent in table.items():
        rating: Rating = agent.deception if pick == "deception" else agent.detection
        games = (
            agent.games_as_traitor if pick == "deception" else agent.games_as_faithful
        )
        if games == 0:
            continue
        rows.append(
            StandingRow(
                model=model,
                mu=rating.mu,
                sigma=rating.sigma,
                ordinal=ordinal(rating),
                games=games,
            )
        )
    rows.sort(key=lambda row: (-row.ordinal, row.model))
    return rows


def standings(table: Mapping[str, AgentRating]) -> dict[str, list[StandingRow]]:
    return {
        "deception": _rows(table, "deception"),
        "detection": _rows(table, "detection"),
    }


def leaderboard_payload(table: Mapping[str, AgentRating]) -> dict[str, object]:
    board = standings(table)
    return {
        "deception": [row.as_dict() for row in board["deception"]],
        "detection": [row.as_dict() for row in board["detection"]],
    }
