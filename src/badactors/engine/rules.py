"""Rule definitions for legal game phases and actions."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from badactors.schemas import (
    AgentTurnOutput,
    EndgameChoice,
    GameState,
    MurderNomination,
    Phase,
    Player,
    Role,
    Vote,
)


def living_players(state: GameState) -> list[Player]:
    return [player for player in state.players if player.alive]


def living_player_ids(state: GameState) -> list[str]:
    return [player.id for player in living_players(state)]


def living_traitor_ids(state: GameState) -> list[str]:
    return [
        player.id for player in living_players(state) if player.role is Role.TRAITOR
    ]


def valid_actions(phase: Phase) -> list[str]:
    if phase is Phase.BANISHMENT_VOTE:
        return ["vote"]
    if phase is Phase.CONCLAVE:
        return ["murder_nomination"]
    if phase is Phase.ENDGAME_VOTE:
        return ["endgame_choice"]
    return ["no_action"]


def vote_tally(
    outputs: Mapping[str, AgentTurnOutput],
    eligible_voters: Iterable[str],
    valid_targets: Iterable[str],
) -> dict[str, int]:
    voter_ids = set(eligible_voters)
    target_ids = set(valid_targets)
    counts: Counter[str] = Counter()
    for voter_id, output in outputs.items():
        if voter_id not in voter_ids:
            continue
        action = output.action
        if isinstance(action, Vote) and action.target_id in target_ids:
            counts[action.target_id] += 1
    return dict(sorted(counts.items()))


def murder_tally(
    outputs: Mapping[str, AgentTurnOutput],
    traitor_ids: Iterable[str],
    valid_targets: Iterable[str],
) -> dict[str, int]:
    eligible_traitors = set(traitor_ids)
    target_ids = set(valid_targets)
    counts: Counter[str] = Counter()
    for traitor_id, output in outputs.items():
        if traitor_id not in eligible_traitors:
            continue
        action = output.action
        if isinstance(action, MurderNomination) and action.target_id in target_ids:
            counts[action.target_id] += 1
    return dict(sorted(counts.items()))


def choose_from_tally(tally: Mapping[str, int], rng_seed: int) -> str | None:
    if not tally:
        return None
    max_votes = max(tally.values())
    tied = [target_id for target_id, count in tally.items() if count == max_votes]
    return min(tied, key=lambda target_id: _tie_break_key(target_id, rng_seed))


def banish_player(players: Sequence[Player], target_id: str) -> list[Player]:
    return [
        _set_alive(player, alive=False) if player.id == target_id else player
        for player in players
    ]


def murder_player(players: Sequence[Player], target_id: str) -> list[Player]:
    return [
        _set_alive(player, alive=False) if player.id == target_id else player
        for player in players
    ]


def player_by_id(players: Iterable[Player], player_id: str) -> Player:
    for player in players:
        if player.id == player_id:
            return player
    raise ValueError(f"Unknown player id: {player_id}")


def winning_role_after_end(state: GameState) -> Role:
    if living_traitor_ids(state):
        return Role.TRAITOR
    return Role.FAITHFUL


def check_win_condition(state: GameState, *, group_ended: bool = False) -> Role | None:
    if not living_traitor_ids(state):
        return Role.FAITHFUL
    if group_ended:
        return Role.TRAITOR
    return None


def should_enter_endgame(state: GameState) -> bool:
    return 0 < len(living_players(state)) <= 3


def endgame_unanimous_to_end(
    outputs: Mapping[str, AgentTurnOutput],
    living_ids: Iterable[str],
) -> bool:
    voters = list(living_ids)
    if not voters:
        return False
    for player_id in voters:
        output = outputs.get(player_id)
        if output is None:
            return False
        action = output.action
        if not isinstance(action, EndgameChoice) or action.choice != "end":
            return False
    return True


def _set_alive(player: Player, *, alive: bool) -> Player:
    return Player(
        id=player.id,
        model=player.model,
        role=player.role,
        alive=alive,
        memory_budget=player.memory_budget,
        token_budget=player.token_budget,
    )


def _tie_break_key(target_id: str, rng_seed: int) -> str:
    payload = f"{rng_seed}:{target_id}".encode()
    return hashlib.sha256(payload).hexdigest()
