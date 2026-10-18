"""Per-request answer schemas: narrow, strict, and wide enough for real play.

The property test plays bot games over many seeds and holds every decision any
bot made to the schema compiled for the request it answered: if a narrowed enum
or a kit filter ever shut out a legal answer, a bot would find it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
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
from badactors.castle.contracts import Plan, Talk, Wait, Whisper, Write
from badactors.castle.decisions import (
    Decision,
    ExitAnswer,
    Pass,
    PlanChoice,
    Request,
    StaySilent,
)
from badactors.castle.game import GameState
from badactors.castle.interview import ExitObservation
from badactors.castle.observe import GameObservation
from badactors.castle.prompt import CONTRACT, render
from badactors.castle.provider import MODES, CallRef, Mode, ProviderCall, request_body
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
            strict = compile_schema(observation, strict=True)
            assert validate(strict, value) == [], (observation.request, value)
            assert DECISION.validate_json(json.dumps(value[ROOT])) == decision
            kinds.add(observation.request.kind)
            return decision

        def interview(self, departing: ExitObservation) -> ExitAnswer:
            answer: ExitAnswer = bot.interview(departing)  # type: ignore[attr-defined]
            for strict in (False, True):
                exit_schema = compile_exit_schema(departing, strict=strict)
                assert validate(exit_schema, _wrapped(answer)) == []
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


def _seen(**options: Any) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def collect(observation: GameObservation, game: GameState) -> None:
        seen.append(compile_schema(observation, **options))

    referee(seed=2, script=collect, max_days=2).run()
    return seen


ALWAYS_FORBIDDEN = {
    "$ref",
    "$defs",
    "oneOf",
    "const",
    "default",
    "title",
    "pattern",
    "discriminator",
}
LIMITS = {"minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"}


def test_every_strict_schema_is_in_the_strict_subset() -> None:
    for schema in _seen(strict=True):
        assert schema["type"] == "object" and schema["required"] == [ROOT]
        for node in _walk(schema):
            forbidden = (ALWAYS_FORBIDDEN | LIMITS) & set(node)
            assert not forbidden, forbidden
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])


def test_every_plain_schema_keeps_its_limits_and_requires_what_the_types_do() -> None:
    limited = 0
    for schema in _seen():
        assert schema["type"] == "object" and schema["required"] == [ROOT]
        for node in _walk(schema):
            assert not ALWAYS_FORBIDDEN & set(node), ALWAYS_FORBIDDEN & set(node)
            limited += bool(LIMITS & set(node))
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) <= set(node["properties"])
                for prop in ("kind", "tool"):
                    if prop in node["properties"]:
                        assert prop in node["required"]
    assert limited


def _steps(schema: dict[str, Any]) -> list[dict[str, Any]]:
    plan = schema["properties"][ROOT]["properties"]["plan"]
    steps: list[dict[str, Any]] = plan["properties"]["steps"]["items"]["anyOf"]
    return steps


def _step(schema: dict[str, Any], kind: str, tool: str | None = None) -> Any:
    return next(
        step
        for step in _steps(schema)
        if step["properties"]["kind"]["enum"] == [kind]
        and step["properties"].get("tool", {}).get("enum", [None]) == [tool]
    )


def test_tags_are_one_value_enums_and_caps_reach_the_model_either_way() -> None:
    _, plan = pause_at(lambda o, g: o.request.kind == "plan" and o.role == "faithful")
    plain, strict = compile_schema(plan), compile_schema(plan, strict=True)
    write = _step(plain, "tool", "write")["properties"]
    assert write["kind"] == {"type": "string", "enum": ["tool"]}
    assert write["tool"] == {"type": "string", "enum": ["write"]}
    assert write["text"]["maxLength"] == 400
    talk = _step(strict, "talk")["properties"]["text"]
    assert "maxLength" not in talk and "at most 400 characters" in talk["description"]
    diary = plain["properties"][ROOT]["properties"]["diary"]
    assert (
        diary["maxLength"] == 600 and "at most 600 characters" in diary["description"]
    )


# Gemini 3.1 Pro's plans in the seed-7 game of 2026-10-17, which left out what
# the decision types leave optional: a wait's reason, a talk's listener, whose
# letters to read.
GEMINI_PLANS = [
    {"goal": "Wait for dinner to conclude.", "steps": [{"kind": "wait"}]},
    {"goal": "Accuse Knox.", "steps": [{"kind": "talk", "text": "Knox lied."}]},
    {
        "goal": "Read the letters.",
        "steps": [{"kind": "tool", "tool": "read", "thing_id": "household_letters"}],
    },
]


def test_a_field_the_decision_types_leave_optional_may_be_left_out() -> None:
    _, plan = pause_at(lambda o, g: o.request.kind == "plan" and o.role == "faithful")
    for steps in GEMINI_PLANS:
        answer = {ROOT: {"kind": "plan", "diary": "Waiting.", "plan": steps}}
        assert validate(compile_schema(plan), answer) == []
        assert validate(compile_schema(plan, strict=True), answer)
        decision = DECISION.validate_json(json.dumps(answer[ROOT]))
        assert isinstance(decision, PlanChoice)
    wait = {"kind": "plan", "plan": {"goal": "", "steps": [{"kind": "wait"}]}}
    decided = DECISION.validate_json(json.dumps(wait))
    assert isinstance(decided, PlanChoice) and decided.diary == ""


def test_a_tool_named_as_its_own_kind_is_still_refused() -> None:
    _, plan = pause_at(lambda o, g: o.request.kind == "plan" and o.role == "faithful")
    steps = [
        {"kind": "write", "tool": "write", "thing_id": "guest_book", "text": "Iris"}
    ]
    answer = {ROOT: {"kind": "plan", "plan": {"goal": "", "steps": steps}}}
    assert validate(compile_schema(plan), answer) == [
        f"{ROOT}.plan.steps[0]: 'write:write' is not one of the steps you can take"
    ]


def test_targets_rooms_and_records_are_narrowed_to_the_request() -> None:
    _, vote = pause_at(lambda o, g: o.request.kind == "vote")
    schema = compile_schema(vote)
    assert schema["properties"][ROOT]["properties"]["target"]["enum"] == (
        vote.request.options
    )
    assert len(json.dumps(schema)) < 1_000

    _, alibi = pause_at(lambda o, g: o.request.kind == "alibi", seed=7)
    variants = compile_schema(alibi)["properties"][ROOT]["anyOf"]
    claim = next(v for v in variants if v["properties"]["kind"]["enum"] == ["alibi"])
    assert claim["properties"]["room"]["enum"] == alibi.request.rooms
    companion = claim["properties"]["companion"]["anyOf"]
    assert companion[0]["enum"] == alibi.request.options
    assert companion[1] == {"type": "null"}


def test_table_tools_appear_only_when_offered() -> None:
    _, accuse = pause_at(lambda o, g: o.request.kind == "accuse" and o.request.tools)
    kinds = {
        variant["properties"]["kind"]["enum"][0]
        for variant in compile_schema(accuse)["properties"][ROOT]["anyOf"]
    }
    assert kinds == {"accuse", "stay_silent", *accuse.request.tools}
    bare = accuse.model_copy(
        update={"request": accuse.request.model_copy(update={"tools": []})}
    )
    kinds = {
        variant["properties"]["kind"]["enum"][0]
        for variant in compile_schema(bare)["properties"][ROOT]["anyOf"]
    }
    assert kinds == {"accuse", "stay_silent"}


def _plan_tools(observation: GameObservation) -> set[str]:
    return {
        step["properties"]["tool"]["enum"][0]
        for step in _steps(compile_schema(observation))
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
    assert errors == [
        f"{ROOT}.plan.steps[0]: 'tool:hook' is not one of the steps you can take"
    ]


def test_validation_errors_name_the_path_and_the_choices() -> None:
    _, vote = pause_at(lambda o, g: o.request.kind == "vote")
    schema = compile_schema(vote)
    assert validate(schema, {ROOT: {"kind": "vote", "diary": "", "target": "zed"}}) == [
        f"{ROOT}.target: 'zed' is not one of "
        + ", ".join(repr(option) for option in vote.request.options)
    ]
    bare = {ROOT: {"kind": "vote", "target": vote.request.options[0]}}
    assert validate(schema, bare) == []
    assert validate(compile_schema(vote, strict=True), bare) == [
        f"{ROOT}: missing diary"
    ]
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


@pytest.mark.parametrize("mode", MODES)
def test_every_mode_tells_the_model_the_caps(mode: Mode) -> None:
    _, plan = pause_at(lambda o, g: o.request.kind == "plan" and o.role == "faithful")
    schema = compile_schema(plan, strict=mode == "json_schema")
    ref = CallRef("g", plan.viewer_id, "openai/gpt-6.1-sol", "plan", 1, 1, 0, 1)
    rendered = render(plan, recipient=ref.model)
    body = request_body(ProviderCall(ref, rendered.messages, schema, mode, 4096))
    sent = json.dumps(body, separators=(",", ":"))
    assert "a diary at most 600" in sent
    assert "(at most 600 characters)" in sent
    keyword = '"maxLength":600' in sent or '\\"maxLength\\":600' in sent
    assert keyword == (mode != "json_schema")


def test_the_caps_the_contract_states_are_the_decision_types_caps() -> None:
    def cap(model: Any, *path: str) -> int:
        node = model.model_json_schema()
        for step in path:
            node = node["properties"][step]
        value: int = node["maxLength"]
        return value

    for words in (Talk, Whisper, Write):
        assert cap(words, "text") == 400
    assert cap(PlanChoice, "diary") == 600
    assert cap(StaySilent, "reason") == cap(Pass, "reason") == 300
    assert cap(Plan, "goal") == cap(Wait, "reason") == 200
    stated = " ".join(CONTRACT.split())
    assert "writing at most 400; a diary at most 600" in stated
    assert "stay_silent or pass at most 300" in stated
    assert "a wait's reason at most 200" in stated
