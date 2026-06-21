"""Host orchestration for game narration and flow."""

from __future__ import annotations

from collections.abc import Sequence

from badactors.schemas import Event, Visibility


class Host:
    async def narrate(self, events: Sequence[Event]) -> str:
        if any(event.visibility is Visibility.AUDIENCE for event in events):
            raise ValueError("Host narration cannot receive audience-only events.")

        lines = [_line_for_event(event) for event in events]
        return "\n".join(line for line in lines if line)

    def cast(self) -> None:
        """Select the v2 season cast and secretly designate Traitors."""
        raise NotImplementedError("Host casting is implemented in Prompt 13.")


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
        return f"{payload.get('player_id')} is banished and revealed as {payload.get('role')}."
    if event.type == "MurderNomination":
        return (
            f"{payload.get('traitor_id')} nominates "
            f"{payload.get('target_id')} for murder."
        )
    if event.type == "PlayerMurdered":
        return f"{payload.get('player_id')} is murdered and revealed as {payload.get('role')}."
    if event.type == "NoMurder":
        return "No murder occurs."
    if event.type == "EndgameChoice":
        return f"{payload.get('player_id')} chooses {payload.get('choice')}."
    if event.type == "GameOver":
        return f"Game over. {payload.get('winning_role')} wins by {payload.get('reason')}."

    return f"{event.type}: {payload}"
