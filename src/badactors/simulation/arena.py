"""Residents enter social-deduction challenges and carry their own outcomes home."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from badactors.persistence import ConflictError
from badactors.providers.base import Budget, Message, ModelProvider, ProviderResult
from badactors.providers.openrouter import OpenRouterProvider
from badactors.runner import Cast, CastMember, default_mock_provider, run_game_record
from badactors.schemas import GameState, Role
from badactors.simulation.engine import remember
from badactors.simulation.models import Memory, Resident, WorldEvent, WorldState
from badactors.simulation.progression import update_objectives
from badactors.simulation.runtime import WorldRuntime


@dataclass
class _MeteredProvider:
    runtime: WorldRuntime
    state: WorldState
    owner: str
    provider: ModelProvider

    async def complete(
        self,
        messages: Sequence[Message],
        schema: type[BaseModel],
        model: str,
        budget: Budget,
    ) -> ProviderResult:
        self.runtime.reserve_requests(self.state, self.owner)
        return await self.provider.complete(messages, schema, model, budget)


async def run_arena(runtime: WorldRuntime, world_id: str) -> dict[str, object]:
    async with runtime.lease(world_id) as owner:
        revision, state = runtime.load(world_id)
        if state.status == "running":
            raise ConflictError("Pause the world before starting an arena challenge.")
        if state.active_arena_id is None:
            state.arena_runs += 1
            state.active_arena_id = f"{world_id}-arena-{state.arena_runs:04d}"
            runtime.commit(state, [], revision, owner)
            revision += 1
        game_id = state.active_arena_id
        cast = Cast(
            game_id=game_id,
            rng_seed=state.seed + state.arena_runs - 1,
            traitor_count=max(1, len(state.residents) // 3),
            members=tuple(
                CastMember(resident.id, resident.model, 100, state.max_tokens)
                for resident in state.residents.values()
            ),
        )

        def inject(game: GameState) -> GameState:
            updated = game.model_copy(deep=True)
            for resident in state.residents.values():
                updated.diaries[resident.id] = [
                    f"[Life before this challenge] {memory.text}"
                    for memory in _arena_memories(resident)
                ]
            return updated

        provider: ModelProvider = (
            default_mock_provider()
            if state.provider == "demo"
            else _MeteredProvider(
                runtime,
                state,
                owner,
                runtime.provider or OpenRouterProvider(timeout=45),
            )
        )
        try:
            result = await run_game_record(
                cast,
                provider,
                event_store=runtime.store,
                state_transform=inject,
                resume=True,
            )
        except Exception:
            state.error = (
                "Arena interrupted. Resume it or cancel to return to village life."
            )
            try:
                runtime.commit(state, [], revision, owner)
            except ConflictError:
                pass
            raise
        winner = Role(str(result.events[-1].payload["winning_role"]))
        state.arena_games.append(game_id)
        state.active_arena_id = None
        state.error = None
        for player in result.final_state.players:
            resident = state.residents[player.id]
            won = player.role == winner
            if won:
                resident.coins += 5
                resident.stats.coins_earned += 5
                resident.stats.arena_wins += 1
            remember(
                resident,
                state.tick,
                f"In challenge {game_id}, I {'won 5 coins' if won else 'lost'}. "
                "Roles are reassigned independently in every challenge.",
                "arena",
            )
            update_objectives(resident, state.tick)
        state.event_sequence += 1
        event = WorldEvent(
            sequence=state.event_sequence,
            tick=state.tick,
            type="arena",
            actor_id="host",
            location="square",
            text=f"Arena {game_id} ended. {winner.value} won; winners earned 5 coins.",
            witnesses=sorted(state.residents),
        )
        runtime.commit(state, [event], revision, owner)
        return {
            "game_id": game_id,
            "winning_role": winner.value,
            "world_id": world_id,
            "events": len(result.events),
        }


def _arena_memories(resident: Resident) -> list[Memory]:
    memories = {memory.sequence: memory for memory in resident.experiences}
    memories.update({memory.sequence: memory for memory in resident.memories[-12:]})
    return sorted(memories.values(), key=lambda item: item.sequence)[-20:]


async def cancel_arena(runtime: WorldRuntime, world_id: str) -> WorldState:
    async with runtime.lease(world_id) as owner:
        revision, state = runtime.load(world_id)
        if state.active_arena_id is None:
            raise ConflictError("There is no active arena to cancel.")
        game_id = state.active_arena_id
        state.active_arena_id = None
        state.error = None
        state.event_sequence += 1
        event = WorldEvent(
            sequence=state.event_sequence,
            tick=state.tick,
            type="arena_cancelled",
            actor_id="host",
            location="square",
            text=f"Arena {game_id} was cancelled. No rewards were issued.",
            witnesses=sorted(state.residents),
        )
        for resident in state.residents.values():
            remember(
                resident, state.tick, f"I left unfinished challenge {game_id}.", "arena"
            )
        runtime.commit(state, [event], revision, owner)
        return state
