"""No player may say which model plays them, or guess at anyone else's.

The check is the speaker's: words that name a model, a model family or its maker
are refused where they are written, the question is put once more, and a player
who insists fails the turn. It runs in every game, leak checks or not, and a
refusal is its player's alone.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from badactors.castle.anonymity import aliases, check_words
from badactors.castle.contracts import Hook, Intent, Plan, Talk, Wait
from badactors.castle.decisions import (
    Accuse,
    Decision,
    IllegalDecision,
    Nominate,
    PlanChoice,
    Request,
    StaySilent,
)
from badactors.castle.episode import episode
from badactors.castle.game import GameState
from badactors.castle.observe import GameObservation, assert_no_game_leak, observe_game
from badactors.castle.play import Played, record, replay
from badactors.castle.scope import LeakageError
from castle_scenes import (
    CAST,
    CASTLE,
    MODELS,
    Script,
    of_type,
    pause_at,
    plant_record,
    referee,
    run_until,
)

NAMES = [name for member in CAST.players for name in (member.id, member.name)]
KNOWN = aliases(MODELS, exempt=NAMES)

SAID = [
    "I am Claude Opus 5.5.",
    "Honestly, I'm GPT.",
    "Gemini would never vote like that.",
    "Kimi here, trust me.",
    "You argue like gpt-6.1, Sol.",
    "Qwen3 talks that way.",
    "That is an Opus 5.5 answer.",
    "Ask ChatGPT.",
    "Grok said so.",
    "DeepSeek-V4 is in this house.",
    "Anthropic trained me.",
    "OpenAI's model is lying.",
    "Mara is played by google/gemini-3.1-pro-preview.",
    "I run on z-ai/glm-5.3.",
]

PLAIN = [
    "I came straight here to the Great Hall. Ask anyone.",
    "Sol was in the kitchen, and Mara saw it.",
    "Wren played a sonnet in the gallery.",
    "This castle is my magnum opus of suspicion.",
    "It sounds like a fable to me.",
    "Claudette is not one of us.",
    "Knox, Iris, June, Ash, Vale and Hale: who went down the stair?",
]


@pytest.mark.parametrize("text", SAID)
def test_a_model_named_in_words_is_found(text: str) -> None:
    assert KNOWN.found(text) is not None
    with pytest.raises(IllegalDecision, match="name an AI model"):
        check_words(Accuse(target="sol", text=text), KNOWN)


@pytest.mark.parametrize("text", PLAIN)
def test_ordinary_words_and_the_casts_own_names_pass(text: str) -> None:
    assert KNOWN.found(text) is None
    check_words(Accuse(target="sol", text=text), KNOWN)


def test_words_another_player_receives_are_checked() -> None:
    with pytest.raises(IllegalDecision):
        check_words(
            PlanChoice(plan=Plan(steps=[Talk(text="I am Kimi.", to="sol")])), KNOWN
        )
    with pytest.raises(IllegalDecision):
        check_words(Accuse(target="sol", text="Spoken like Gemini."), KNOWN)
    with pytest.raises(IllegalDecision):
        check_words(Nominate(target="sol", text="Claude goes tonight."), KNOWN)


def test_a_model_name_in_private_words_passes() -> None:
    """A diary line, a reason, a plan's goal and a declared intent reach only
    the audience and their writer, so a model may break character in them."""
    check_words(
        Accuse(target="sol", text="Sol hid something.", diary="I am Claude."),
        KNOWN,
    )
    check_words(StaySilent(reason="As Claude, I won't accuse anyone."), KNOWN)
    check_words(
        PlanChoice(
            plan=Plan(goal="find whoever is GPT", steps=[Wait(reason="Gemini idles")])
        ),
        KNOWN,
    )
    check_words(
        PlanChoice(
            plan=Plan(
                steps=[
                    Hook(
                        to="sol",
                        text="Vote for Mara.",
                        intent=Intent(
                            target="sol",
                            effect="belief",
                            want="Sol thinks Mara is Claude",
                            window="next_vote",
                        ),
                    )
                ]
            )
        ),
        KNOWN,
    )


def test_a_private_reason_naming_a_model_is_kept_and_never_heard() -> None:
    reason = "As Claude, I won't accuse anyone."

    def silent(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "accuse" and observation.viewer_id == "mara":
            return StaySilent(reason=reason, diary="Speaking as GPT, this is odd.")
        return None

    ref = referee(seed=6, script=silent)
    game = run_until(ref, 1, "dinner")
    assert not of_type(game, "refused")
    (silence,) = [e for e in of_type(game, "stay_silent") if e.actor_id == "mara"]
    assert silence.private == {
        "reason": reason,
        "diary": "Speaking as GPT, this is odd.",
    }
    for pid in game.world.seats:
        if pid != "mara" and game.world.players[pid].alive:
            shown = observe_game(game, pid, Request(kind="plan", prompt="Plan."))
            assert reason not in shown.model_dump_json()
    mine = observe_game(game, "mara", Request(kind="plan", prompt="Plan."))
    assert reason in mine.model_dump_json(), "Mara sees her own reason"
    assert_no_game_leak(mine, game, models=MODELS)


_WORDS = st.sampled_from(
    sorted(
        {
            word.strip(".,:;!?'\"()").casefold()
            for text in PLAIN
            for word in text.split()
        }
        - {""}
    )
)
_ALIASES = st.sampled_from(
    ["Claude", "GPT", "gpt-6.1", "Gemini", "Kimi", "Qwen3", "Grok", "Opus 5.5"]
)


@settings(max_examples=60, deadline=None)
@given(
    before=st.lists(_WORDS, max_size=8),
    after=st.lists(_WORDS, max_size=8),
    alias=_ALIASES,
    joint=st.sampled_from([" ", ", ", " (", ": ", ' "', "\n"]),
)
def test_property_an_alias_anywhere_in_ordinary_words_is_refused(
    before: list[str], after: list[str], alias: str, joint: str
) -> None:
    plain = " ".join([*before, *after]) or "Hello."
    check_words(Accuse(target="sol", text=plain), KNOWN)
    text = (" ".join(before) + joint + alias + joint + " ".join(after)).strip()
    with pytest.raises(IllegalDecision):
        check_words(Accuse(target="sol", text=text), KNOWN)


# --------------------------------------------------------------- in a played game


def _blurting(said: str, *, insist: bool) -> tuple[list[GameObservation], Script]:
    """Mara names a model in her accusation on day one; ``insist`` has her do
    it again when asked a second time."""
    asked: list[GameObservation] = []

    def script(observation: GameObservation, game: GameState) -> Decision | None:
        if observation.request.kind == "accuse" and observation.viewer_id == "mara":
            asked.append(observation)
            options = observation.request.options
            if insist or observation.request.refusal is None:
                return Accuse(target=options[0], text=said)
            return Accuse(target=options[0], text="You were on the stair.")
        return None

    return asked, script


@pytest.mark.parametrize("said", ["I am Claude Opus 5.5.", "GPT", "Gemini", "Kimi"])
def test_a_model_named_aloud_is_refused_and_asked_again(said: str) -> None:
    asked, script = _blurting(said, insist=False)
    ref = referee(seed=6, script=script)
    game = run_until(ref, 1, "dinner")
    assert [observation.request.refusal is None for observation in asked] == [
        True,
        False,
    ]
    refusals = [e for e in of_type(game, "refused") if e.actor_id == "mara"]
    assert len(refusals) == 1
    assert [record.player_id for record in refusals[0].witnesses] == ["mara"]
    assert not [e for e in of_type(game, "turn_failed") if e.actor_id == "mara"]
    accusation = next(e for e in of_type(game, "accuse") if e.actor_id == "mara")
    assert accusation.quote == "You were on the stair."
    assert KNOWN.found(game.world.model_dump_json()) is None
    logged = [entry for entry in ref.log if entry.player_id == "mara"]
    assert any(entry.refused for entry in logged)


def test_planted_leak_a_refusal_shown_to_someone_else() -> None:
    _, script = _blurting("Gemini", insist=False)
    game, _ = pause_at(
        lambda observation, game: (
            bool(of_type(game, "refused")) and observation.viewer_id != "mara"
        ),
        script=script,
        seed=6,
    )
    refusal = of_type(game, "refused")[0]
    planted = plant_record(game, refusal, "sol", sense="saw")
    observation = observe_game(game, "sol", Request(kind="plan", prompt="Plan."))
    assert any(item.ledger_seq == planted.seq for item in observation.world.recent)
    with pytest.raises(LeakageError, match="a refused answer"):
        assert_no_game_leak(observation, game, models=MODELS)


def test_the_episode_shows_the_audience_a_refusal() -> None:
    _, script = _blurting("Gemini", insist=False)
    game = run_until(referee(seed=6, script=script), 1, "dinner")
    assert "REFUSED" in episode(game)


def test_a_player_who_insists_fails_the_turn_and_is_never_heard() -> None:
    asked, script = _blurting("I am Claude Opus 5.5.", insist=True)
    ref = referee(seed=6, script=script)
    game = run_until(ref, 1, "dinner")
    assert len(asked) == 2
    failed = [e for e in of_type(game, "turn_failed") if e.actor_id == "mara"]
    assert [entry.payload["request"] for entry in failed] == ["accuse"]
    assert KNOWN.found(game.world.model_dump_json()) is None


def test_the_check_runs_with_leak_checks_off() -> None:
    asked, script = _blurting("Kimi", insist=True)
    ref = referee(seed=6, script=script)
    ref.check_leaks = False
    game = run_until(ref, 1, "dinner")
    assert len(asked) == 2
    assert [e.actor_id for e in of_type(game, "turn_failed")] == ["mara"]
    assert KNOWN.found(game.world.model_dump_json()) is None


def test_a_game_with_refusals_replays_to_the_same_ledger() -> None:
    _, script = _blurting("GPT", insist=False)
    ref = referee(seed=6, script=script)
    ref.run()
    stored = record(
        CASTLE,
        CAST,
        Played(game=ref.game, log=ref.log, observations=ref.observations),
    )
    assert any(entry.refused for entry in stored.decisions)
    again = replay(stored)
    assert [e.model_dump(mode="json") for e in again.world.ledger] == [
        e.model_dump(mode="json") for e in ref.game.world.ledger
    ]
