"""Per-request answer schemas: narrow, strict, and wide enough for real play.

The property test plays bot games over many seeds and holds every decision any
bot made to the schema compiled for the request it answered: if a narrowed enum
or a kit filter ever shut out a legal answer, a bot would find it.
"""

from __future__ import annotations

import json
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter

from badactors.castle.contract_schema import (
    ROOT,
    compile_exit_schema,
    compile_schema,
    kit_tools,
    validate,
)
from badactors.castle.decisions import Decision, ExitAnswer, Request
from badactors.castle.game import GameState
from badactors.castle.interview import ExitObservation
from badactors.castle.observe import GameObservation
from castle_scenes import pause_at, referee

DECISION: TypeAdapter[Decision] = TypeAdapter(Decision)


def _wrapped(decision: Decision) -> dict[str, Any]:
    return {ROOT: json.loads(DECISION.dump_json(decision))}


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=100_000), players=st.sampled_from([8, 10])
)
def test_property_every_bot_decision_validates_against_its_compiled_schema(
    seed: int, players: int
) -> None:
    kinds: set[str] = set()
    ref = referee(seed=seed, players=players)
    bot = ref.players[ref.game.world.seats[0]]

    class Checked:
        agent = "bot:heuristic"

        def __call__(self, observation: GameObservation) -> Decision:
            decision = bot(observation)
            schema = compile_schema(observation)
            value = _wrapped(decision)
            assert validate(schema, value) == [], (observation.request, value)
            assert DECISION.validate_json(json.dumps(value[ROOT])) == decision
            kinds.add(observation.request.kind)
            return decision

        def interview(self, departing: ExitObservation) -> ExitAnswer:
            answer: ExitAnswer = bot.interview(departing)  # type: ignore[attr-defined]
            assert validate(compile_exit_schema(departing), _wrapped(answer)) == []
            return answer

    checked = Checked()
    ref.players = {pid: checked for pid in ref.game.world.seats}
    ref.run()
    assert {"plan", "accuse", "vote"} <= kinds


def _walk(schema: Any) -> list[dict[str, Any]]:
    if isinstance(schema, list):
        return [node for item in schema for node in _walk(item)]
    if not isinstance(schema, dict):
        return []
    return [schema, *(node for value in schema.values() for node in _walk(value))]


def test_every_schema_is_in_the_strict_subset() -> None:
    seen: list[dict[str, Any]] = []

    def collect(observation: GameObservation, game: GameState) -> None:
        seen.append(compile_schema(observation))

    referee(seed=2, script=collect, max_days=2).run()
    forbidden = {
        "$ref",
        "$defs",
        "oneOf",
        "default",
        "title",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "pattern",
        "discriminator",
    }
    for schema in seen:
        assert schema["type"] == "object" and schema["required"] == [ROOT]
        for node in _walk(schema):
            assert not forbidden & set(node), forbidden & set(node)
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])


def test_targets_rooms_and_records_are_narrowed_to_the_request() -> None:
    _, vote = pause_at(lambda o, g: o.request.kind == "vote")
    schema = compile_schema(vote)
    assert schema["properties"][ROOT]["properties"]["target"]["enum"] == (
        vote.request.options
    )
    assert len(json.dumps(schema)) < 1_000

    _, alibi = pause_at(lambda o, g: o.request.kind == "alibi", seed=7)
    variants = compile_schema(alibi)["properties"][ROOT]["anyOf"]
    claim = next(v for v in variants if v["properties"]["kind"]["const"] == "alibi")
    assert claim["properties"]["room"]["enum"] == alibi.request.rooms
    companion = claim["properties"]["companion"]["anyOf"]
    assert companion[0]["enum"] == alibi.request.options
    assert companion[1] == {"type": "null"}


def test_table_tools_appear_only_when_offered() -> None:
    _, accuse = pause_at(lambda o, g: o.request.kind == "accuse" and o.request.tools)
    kinds = {
        variant["properties"]["kind"]["const"]
        for variant in compile_schema(accuse)["properties"][ROOT]["anyOf"]
    }
    assert kinds == {"accuse", "stay_silent", *accuse.request.tools}
    bare = accuse.model_copy(
        update={"request": accuse.request.model_copy(update={"tools": []})}
    )
    kinds = {
        variant["properties"]["kind"]["const"]
        for variant in compile_schema(bare)["properties"][ROOT]["anyOf"]
    }
    assert kinds == {"accuse", "stay_silent"}


def _plan_tools(observation: GameObservation) -> set[str]:
    plan = compile_schema(observation)["properties"][ROOT]
    steps = plan["properties"]["plan"]["properties"]["steps"]["items"]["anyOf"]
    return {
        step["properties"]["tool"]["const"]
        for step in steps
        if "tool" in step["properties"]
    }


def test_plan_steps_are_filtered_to_the_players_kit() -> None:
    _, faithful = pause_at(
        lambda o, g: o.request.kind == "plan" and o.role == "faithful"
    )
    _, traitor = pause_at(lambda o, g: o.request.kind == "plan" and o.role == "traitor")
    assert _plan_tools(faithful) == set(kit_tools("faithful"))
    assert _plan_tools(traitor) == set(kit_tools("traitor"))
    assert {"forge", "hook", "gaslight"} <= _plan_tools(traitor)
    assert not {"forge", "hook", "gaslight", "poison"} & _plan_tools(faithful)
    hook = {"kind": "tool", "tool": "hook", "to": "sol", "text": "Vote Iris."}
    answer = {
        ROOT: {"kind": "plan", "diary": "", "plan": {"goal": "", "steps": [hook]}}
    }
    errors = validate(compile_schema(faithful), answer)
    assert errors and "kind must be one of" in errors[0]


def test_validation_errors_name_the_path_and_the_choices() -> None:
    _, vote = pause_at(lambda o, g: o.request.kind == "vote")
    schema = compile_schema(vote)
    assert validate(schema, {ROOT: {"kind": "vote", "diary": "", "target": "zed"}}) == [
        f"{ROOT}.target: 'zed' is not one of "
        + ", ".join(repr(option) for option in vote.request.options)
    ]
    assert validate(schema, {ROOT: {"kind": "vote", "target": vote.request.options[0]}})
    assert validate(schema, {"kind": "vote"}) == [
        f'the answer must be a JSON object with one key, "{ROOT}"'
    ]
    assert validate(schema, {ROOT: {"kind": "pass", "diary": "", "reason": "x"}})


def test_an_exit_interview_asks_a_guess_for_every_other_seat() -> None:
    request = Request(kind="exit_interview", options=["sol", "iris"], prompt="Well?")
    departing = ExitObservation(
        viewer_id="mara",
        name="Mara",
        role="faithful",
        why="banished",
        day=2,
        request=request,
    )
    guesses = compile_exit_schema(departing)["properties"][ROOT]["properties"][
        "guesses"
    ]
    assert guesses["required"] == ["sol", "iris"]
