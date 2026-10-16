"""What each seat did, in numbers, read from a finished game.

Ratings need a few numbers per seat. Anything that gives them fits
:class:`SeatMetrics`: a finished game in, a mapping from player id to named
numbers out. :func:`ledger_metrics` gives the ones the ledger holds today — the
side and whether it won, survival, the votes and accusations each seat cast
against players who were traitors at that moment, the heat each traitor drew,
and manipulation outcomes. The truth layer's claims and verdicts (lie rate, lie
success, accusation calibration; SPEC §5) will be another source, merged in
with :func:`combined`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Protocol

from badactors.castle.game import GameState, was_traitor_at
from badactors.castle.manipulation import outcomes

type Numbers = Mapping[str, float]

BALLOTS: frozenset[str] = frozenset({"vote", "accuse"})
"""Entry types that point a finger: a vote to banish, and an accusation."""


class SeatMetrics(Protocol):
    """Per-seat numbers from one finished game, by player id."""

    def __call__(self, game: GameState) -> Mapping[str, Numbers]: ...


def ledger_metrics(game: GameState) -> dict[str, dict[str, float]]:
    """The numbers the ledger gives for every seat.

    * ``traitor`` — 1 if the seat ended the game a traitor; ``recruited`` — 1
      if it became one after the draw.
    * ``won`` — its side at the end won; ``survived`` — alive at the end.
    * ``ballots`` — votes and accusations it cast while Faithful;
      ``ballots_on_traitors`` — those aimed at someone who was a traitor at
      that moment (:func:`was_traitor_at`).
    * ``heat`` — votes and accusations others aimed at it while it was a
      traitor and alive; ``heat_of`` — all the ballots others cast in that time.
    * ``attempts`` and ``steered`` — manipulations it declared, and those that
      worked; ``targeted`` and ``fell_for`` — the same, aimed at it.
    """
    world = game.world
    ledger = world.ledger
    gone: dict[str, int] = {}
    for entry in ledger:
        if entry.type in ("death", "banished"):
            gone.setdefault(entry.payload.get("player", ""), entry.seq)
    out: dict[str, dict[str, float]] = {}
    for pid in world.seats:
        role = game.roles[pid]
        out[pid] = defaultdict(float)
        out[pid].update(
            traitor=float(role == "traitor"),
            recruited=float(
                pid in game.traitor_since and pid not in game.starting_traitors
            ),
            won=float(game.winner == role),
            survived=float(world.players[pid].alive),
        )
    for entry in ledger:
        if entry.type not in BALLOTS or entry.actor_id is None:
            continue
        voter, target = entry.actor_id, entry.payload.get("target")
        if target not in out or voter not in out:
            continue
        if not was_traitor_at(game, voter, entry.seq):
            out[voter]["ballots"] += 1
            if was_traitor_at(game, target, entry.seq):
                out[voter]["ballots_on_traitors"] += 1
        for pid in out:
            if (
                pid != voter
                and was_traitor_at(game, pid, entry.seq)
                and entry.seq < gone.get(pid, len(ledger) + 1)
            ):
                out[pid]["heat_of"] += 1
                if target == pid:
                    out[pid]["heat"] += 1
    for attempt in outcomes(game):
        if attempt.attacker in out:
            out[attempt.attacker]["attempts"] += 1
            out[attempt.attacker]["steered"] += float(attempt.success)
        if attempt.target in out:
            out[attempt.target]["targeted"] += 1
            out[attempt.target]["fell_for"] += float(attempt.success)
    return {pid: dict(numbers) for pid, numbers in out.items()}


def combined(*sources: SeatMetrics) -> SeatMetrics:
    """One source from several; a later source's number wins a clash."""

    def merged(game: GameState) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for source in sources:
            for pid, numbers in source(game).items():
                out.setdefault(pid, {}).update(numbers)
        return out

    return merged


__all__ = ["BALLOTS", "Numbers", "SeatMetrics", "combined", "ledger_metrics"]
