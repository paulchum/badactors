"""Cost-bounded auditions for submitted agents."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, Field

from badactors.platform.submissions import Submission
from badactors.providers.base import Budget, Message, ModelProvider
from badactors.schemas import Event, Player


class AuditionScore(BaseModel):
    coherence: int = Field(ge=0, le=10)
    play_quality: int = Field(ge=0, le=10)
    notes: str = ""


@dataclass(frozen=True)
class AuditionResult:
    submission: Submission
    coherence: int
    play_quality: int
    calls_used: int
    tokens_used: int
    notes: str

    @property
    def total_score(self) -> int:
        return self.coherence + self.play_quality


@dataclass(frozen=True)
class Casting:
    game_id: str
    rng_seed: int
    players: tuple[Player, ...]
    traitor_ids: frozenset[str]
    events: tuple[Event, ...]


async def audition_submission(
    submission: Submission,
    provider: ModelProvider,
    *,
    max_calls: int = 1,
    max_tokens: int = 128,
) -> AuditionResult:
    if max_calls < 1:
        raise ValueError("max_calls must permit one audition call.")

    result = await provider.complete(
        _audition_messages(submission),
        AuditionScore,
        submission.base_model,
        Budget(max_tokens=max_tokens, thinking=False),
    )
    score = AuditionScore.model_validate(result.parsed.model_dump(mode="json"))
    return AuditionResult(
        submission=submission,
        coherence=score.coherence,
        play_quality=score.play_quality,
        calls_used=1,
        tokens_used=max_tokens,
        notes=score.notes,
    )


def _audition_messages(submission: Submission) -> Sequence[Message]:
    return [
        Message(
            role="system",
            content=(
                "Run a short, cost-capped Bad Actors audition. Score only "
                "coherence and social-deduction play quality from 0 to 10."
            ),
        ),
        Message(
            role="user",
            content="\n".join(
                [
                    f"Agent name: {submission.agent_name}",
                    f"Creator: {submission.creator}",
                    "Scenario: You are at the first round table. Give a quick "
                    "public read and a private audience diary.",
                    "Character description:",
                    submission.system_prompt,
                    "Return AuditionScore JSON.",
                ]
            ),
        ),
    ]
