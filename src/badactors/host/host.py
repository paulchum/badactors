"""Host orchestration for game narration and flow."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from badactors.platform.audition import AuditionResult, Casting
from badactors.schemas import Event, Phase, Player, Role, Visibility


class Host:
    async def narrate(self, events: Sequence[Event]) -> str:
        if any(event.visibility is Visibility.AUDIENCE for event in events):
            raise ValueError("Host narration cannot receive audience-only events.")

        lines = [_line_for_event(event) for event in events]
        return "\n".join(line for line in lines if line)

    def cast(
        self,
        auditions: Sequence[AuditionResult],
        *,
        game_id: str = "cast-game",
        rng_seed: int = 13,
    ) -> Casting:
        """Select the v2 season cast and secretly designate Traitors."""
        selected = _selected_auditions(auditions)
        players = _players_from_auditions(selected)
        traitor_ids = frozenset(_traitor_ids(players))
        events = (
            _roles_assigned_event(game_id, players, traitor_ids),
            _casting_announcement_event(game_id, players),
        )
        return Casting(
            game_id=game_id,
            rng_seed=rng_seed,
            players=tuple(players),
            traitor_ids=traitor_ids,
            events=events,
        )


def _line_for_event(event: Event) -> str:
    payload = event.payload

    if event.type == "PhaseAdvanced":
        return (
            f"The game moves from {payload.get('from_phase')} "
            f"to {payload.get('to_phase')}."
        )
    if event.type == "PublicSpeech":
        return f"{payload.get('player_id')} says: {payload.get('speech')}"
    if event.type == "VoteCast":
        return f"{payload.get('voter_id')} votes for {payload.get('target_id')}."
    if event.type == "VoteTallied":
        selected_id = payload.get("selected_id")
        if selected_id is None:
            return "The vote is tallied with no banishment."
        return f"The vote is tallied. {selected_id} is selected."
    if event.type == "PlayerBanished":
        return (
            f"{payload.get('player_id')} is banished and revealed as "
            f"{payload.get('role')}."
        )
    if event.type == "MurderNomination":
        return (
            f"{payload.get('traitor_id')} nominates "
            f"{payload.get('target_id')} for murder."
        )
    if event.type == "PlayerMurdered":
        return (
            f"{payload.get('player_id')} is murdered and revealed as "
            f"{payload.get('role')}."
        )
    if event.type == "NoMurder":
        return "No murder occurs."
    if event.type == "EndgameChoice":
        return f"{payload.get('player_id')} chooses {payload.get('choice')}."
    if event.type == "GameOver":
        return (
            f"Game over. {payload.get('winning_role')} wins by {payload.get('reason')}."
        )
    if event.type == "CastingAnnouncement":
        cast = payload.get("players")
        return f"The season cast is announced: {cast}."

    return f"{event.type}: {payload}"


def _selected_auditions(
    auditions: Sequence[AuditionResult],
    *,
    cast_size: int = 6,
) -> list[AuditionResult]:
    if len(auditions) < 3:
        raise ValueError("At least three auditions are required for casting.")
    ordered = sorted(
        auditions,
        key=lambda item: (
            -item.total_score,
            -item.play_quality,
            item.submission.agent_name,
        ),
    )
    return ordered[: min(cast_size, len(ordered))]


def _players_from_auditions(auditions: Sequence[AuditionResult]) -> list[Player]:
    players: list[Player] = []
    for index, result in enumerate(auditions, start=1):
        players.append(
            Player(
                id=f"p{index}",
                model=result.submission.base_model,
                role=Role.FAITHFUL,
                memory_budget=100,
                token_budget=200,
            )
        )
    return players


def _traitor_ids(players: Sequence[Player]) -> set[str]:
    traitor_count = max(1, len(players) // 3)
    return {player.id for player in players[-traitor_count:]}


def _roles_assigned_event(
    game_id: str,
    players: Sequence[Player],
    traitor_ids: frozenset[str],
) -> Event:
    return Event(
        id=1,
        game_id=game_id,
        ts=_CAST_TS,
        phase=Phase.LOBBY,
        type="RolesAssigned",
        payload={
            "roles": {
                player.id: (
                    Role.TRAITOR.value
                    if player.id in traitor_ids
                    else Role.FAITHFUL.value
                )
                for player in players
            },
            "traitor_ids": sorted(traitor_ids),
        },
        visibility=Visibility.AUDIENCE,
    )


def _casting_announcement_event(game_id: str, players: Sequence[Player]) -> Event:
    return Event(
        id=2,
        game_id=game_id,
        ts=_CAST_TS,
        phase=Phase.LOBBY,
        type="CastingAnnouncement",
        payload={"players": [player.id for player in players]},
        visibility=Visibility.PUBLIC,
    )


_CAST_TS = datetime(2026, 1, 1, tzinfo=UTC)
