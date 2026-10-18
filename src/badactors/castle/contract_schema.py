"""The answer schema for one request, narrowed to what that request accepts.

The full :data:`~badactors.castle.decisions.Decision` union is about 26k
characters of JSON Schema. A request accepts a handful of its shapes, so each
one is compiled on its own:

* only the decisions the request accepts, and the table tools it offers;
* names narrowed to enums: the request's options for a target or a companion,
  its rooms for an alibi, its citable records for a report;
* a plan's steps filtered to the tools the player's kit carries, with rooms and
  players named from the castle and the roster;
* a subset every provider understands: one root object wrapping the answer as
  ``decision``, no additional properties, every ``$ref`` inlined, a tag such as
  a decision's ``kind`` given as a one-value ``enum`` (some providers drop
  ``const`` from a function's parameters, and a model then guesses the tag),
  and every length and range limit written into its description as well.

Each request compiles two ways. The **plain** schema, for tools, JSON mode and
the prompt, keeps the limits as keywords (``maxLength`` and the rest) and
requires what the decision types require, so a field they leave optional, such
as a wait step's reason, may be left out and takes its default. The **strict**
schema, for ``json_schema`` with ``strict: true``, requires every property
(optional ones nullable) and keeps the limits only in the descriptions, where
no provider can reject them.

:func:`validate` checks a value against either, so a schema-invalid answer is
named exactly, by path, before the decision types see it. Lengths and ranges
are left to the decision types, which name the cap and the length given.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from typing import Any

from badactors.castle.balance import ToolName
from badactors.castle.contracts import PLAN_TOOLS
from badactors.castle.decisions import (
    ANSWERS,
    TABLE_TOOLS,
    Accept,
    Accuse,
    Alibi,
    Answer,
    CrossExamine,
    Defend,
    DemandAlibi,
    EndgameVote,
    ExitAnswer,
    Filibuster,
    Nominate,
    Pass,
    PlanChoice,
    Poison,
    Question,
    Recruit,
    Refuse,
    Report,
    Request,
    StaySilent,
    Vote,
    Vouch,
)
from badactors.castle.interview import ExitObservation
from badactors.castle.kit import FAITHFUL_TOOLS, TRAITOR_TOOLS
from badactors.castle.observe import GameObservation
from badactors.castle.rules import Role

type Schema = dict[str, Any]

ROOT = "decision"
"""The one property of the root object, holding the answer."""

DECISIONS: dict[str, type[Any]] = {
    cls.model_fields["kind"].default: cls
    for cls in (
        PlanChoice,
        Accuse,
        Defend,
        Question,
        Answer,
        StaySilent,
        Vote,
        EndgameVote,
        Poison,
        Recruit,
        Pass,
        Nominate,
        Accept,
        Refuse,
        Report,
        Filibuster,
        DemandAlibi,
        CrossExamine,
        Alibi,
        Vouch,
        ExitAnswer,
    )
}

_DROPPED = frozenset({"title", "default", "discriminator", "examples"})
_LIMITS = {
    "minLength": "at least {} character{}",
    "maxLength": "at most {} character{}",
    "minimum": "at least {}{}",
    "maximum": "at most {}{}",
    "exclusiveMinimum": "more than {}{}",
    "exclusiveMaximum": "less than {}{}",
    "minItems": "at least {} item{}",
    "maxItems": "at most {} item{}",
}

_DESCRIPTIONS: dict[str, str] = {
    "diary": "Your private thoughts. No other player reads them; the game's "
    "audience does. May be empty.",
    "reason": "Your private reason. No other player reads it; the game's audience "
    "does.",
    "Intent": "What this is meant to achieve, and by when. No player sees it.",
    "want": "The player, room or belief wanted.",
    "Remember": "Keep one of your own records in your notebook: seq is its number. "
    "Takes no time, and nobody sees it.",
    "Relay": "Pass on, word for word, a rumour you were told: rumor is the number "
    "of your record of hearing it.",
    "goal": "What the plan is for, in a few words.",
}


def answer_kinds(request: Request) -> list[str]:
    """The decisions ``request`` accepts, tools offered included, in a fixed
    order."""
    return sorted(ANSWERS[request.kind]) + sorted(set(request.tools) & TABLE_TOOLS)


def kit_tools(role: Role) -> tuple[ToolName, ...]:
    """The plan tools a player of ``role`` carries."""
    carried = FAITHFUL_TOOLS + (TRAITOR_TOOLS if role == "traitor" else ())
    return tuple(name for name in carried if name in PLAN_TOOLS)


def compile_schema(observation: GameObservation, *, strict: bool = False) -> Schema:
    """The schema for the answer to ``observation.request``: the plain one, or
    the strict one."""
    request = observation.request
    rooms = [room.id for room in observation.castle.rooms]
    players = [character.id for character in observation.cast]
    variants = [
        _decision(
            kind,
            request,
            rooms=rooms,
            players=players,
            tools=kit_tools(observation.role),
            strict=strict,
        )
        for kind in answer_kinds(request)
    ]
    return _root(variants)


def compile_exit_schema(departing: ExitObservation, *, strict: bool = False) -> Schema:
    request = departing.request
    return _root(
        [
            _decision(
                "exit_interview", request, rooms=[], players=[], tools=(), strict=strict
            )
        ]
    )


def _root(variants: Sequence[Schema]) -> Schema:
    answer = variants[0] if len(variants) == 1 else {"anyOf": list(variants)}
    return {
        "type": "object",
        "properties": {ROOT: answer},
        "required": [ROOT],
        "additionalProperties": False,
    }


def _decision(
    kind: str,
    request: Request,
    *,
    rooms: Sequence[str],
    players: Sequence[str],
    tools: Sequence[ToolName],
    strict: bool,
) -> Schema:
    schema = copy.deepcopy(_base(kind, strict))
    properties = schema["properties"]
    if "target" in properties:
        _enum(properties["target"], request.options)
    if "companion" in properties:
        _enum(properties["companion"], request.options)
    if kind == "alibi":
        _enum(properties["room"], request.rooms)
    if "evidence" in properties:
        _enum(properties["evidence"], request.evidence)
    if kind == "filibuster":
        _enum(properties["intent"]["properties"]["target"], request.options)
    if kind == "exit_interview":
        properties["guesses"] = {
            "type": "object",
            "description": "For each seat, the model family you think played it, "
            "or unknown.",
            "properties": {
                seat: {"type": "string", "description": "at most 80 characters"}
                | ({} if strict else {"minLength": 1, "maxLength": 80})
                for seat in request.options
            },
            "required": list(request.options),
            "additionalProperties": False,
        }
    if kind == "plan":
        steps = properties["plan"]["properties"]["steps"]
        steps["items"] = {
            "anyOf": [
                _step(variant, rooms, players)
                for variant in steps["items"]["anyOf"]
                if _allowed(variant, tools)
            ]
        }
    return schema


def _allowed(step: Schema, tools: Sequence[ToolName]) -> bool:
    tool = _tag(step["properties"].get("tool", {}))
    return tool is None or tool in tools


_PLAYER_FIELDS = frozenset({"to", "hand", "as_player", "subject", "replace_with"})
_ROOM_FIELDS = frozenset({"room_id"})


def _step(step: Schema, rooms: Sequence[str], players: Sequence[str]) -> Schema:
    step = copy.deepcopy(step)
    for name, field in step["properties"].items():
        if name in _PLAYER_FIELDS:
            _enum(field, players)
        elif name in _ROOM_FIELDS:
            _enum(field, rooms)
        elif name == "intent":
            intent = field["anyOf"][0] if "anyOf" in field else field
            _enum(intent["properties"]["target"], players)
    return step


def _enum(field: Schema, values: Iterable[object]) -> None:
    """Narrow a property to ``values``, keeping it nullable if it was."""
    allowed = list(values)
    if not allowed:
        return
    target = field
    if "anyOf" in field:
        target = next(item for item in field["anyOf"] if item.get("type") != "null")
    target["enum"] = allowed
    target.pop("pattern", None)


# ----------------------------------------------------------- strict subset


@cache
def _base(kind: str, strict: bool) -> Schema:
    """One decision's schema in the subset, plain or strict, before any
    narrowing."""
    raw = DECISIONS[kind].model_json_schema(ref_template="#/$defs/{model}")
    defs = raw.pop("$defs", {})
    schema: Schema = _subset(_inline(raw, defs), name=None, strict=strict)
    return schema


def _inline(value: Any, defs: Mapping[str, Any]) -> Any:
    if isinstance(value, list):
        return [_inline(item, defs) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        name = value["$ref"].rsplit("/", 1)[-1]
        inlined = _inline(copy.deepcopy(defs[name]), defs)
        if name in _DESCRIPTIONS:
            inlined["description"] = _DESCRIPTIONS[name]
        return inlined
    return {key: _inline(item, defs) for key, item in value.items()}


def _subset(value: Any, *, name: str | None, strict: bool) -> Any:
    if isinstance(value, list):
        return [_subset(item, name=None, strict=strict) for item in value]
    if not isinstance(value, dict):
        return value
    notes = [
        template.format(
            value[key], "" if value[key] == 1 or "{}{}" in template else "s"
        )
        for key, template in _LIMITS.items()
        if key in value
    ]
    result: Schema = {}
    for key, item in value.items():
        if key in _DROPPED or key == "pattern" or (strict and key in _LIMITS):
            continue
        if key == "properties":
            result[key] = {
                prop: _subset(schema, name=prop, strict=strict)
                for prop, schema in item.items()
            }
        elif key == "const":
            result["enum"] = [item]
        else:
            result[key] = _subset(item, name=None, strict=strict)
    if "oneOf" in result:
        result["anyOf"] = result.pop("oneOf")
    if "anyOf" in result:
        result["anyOf"] = _flatten(result["anyOf"])
    description = _DESCRIPTIONS.get(name or "") or str(result.get("description", ""))
    description = " ".join(description.split())
    if notes:
        description = f"{description} ({', '.join(notes)})".strip()
    if description:
        result["description"] = description
    else:
        result.pop("description", None)
    if result.get("type") == "object" and "properties" in result:
        required = set(value.get("required", []))
        for prop, schema in result["properties"].items():
            if (
                prop not in required
                and not _nullable(schema)
                and "const" not in value["properties"].get(prop, {})
            ):
                default = value["properties"][prop].get("default", ...)
                if default is None:
                    result["properties"][prop] = {"anyOf": [schema, {"type": "null"}]}
        result["required"] = [
            prop
            for prop, schema in result["properties"].items()
            if strict or prop in required or _tag(schema) is not None
        ]
        result["additionalProperties"] = False
    return result


def _flatten(variants: list[Any]) -> list[Any]:
    flat: list[Any] = []
    for variant in variants:
        if isinstance(variant, dict) and set(variant) <= {"anyOf", "description"}:
            flat.extend(_flatten(variant["anyOf"]))
        else:
            flat.append(variant)
    return flat


def _nullable(schema: Schema) -> bool:
    return any(item.get("type") == "null" for item in schema.get("anyOf", []))


# --------------------------------------------------------------- validation

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def validate(schema: Schema, value: Any) -> list[str]:
    """Every way ``value`` breaks a schema :func:`compile_schema` wrote, by
    path. Understands exactly the subset it writes."""
    if not isinstance(value, dict) or set(value) != {ROOT}:
        return [f'the answer must be a JSON object with one key, "{ROOT}"']
    return _check(schema["properties"][ROOT], value[ROOT], ROOT)


def _check(schema: Schema, value: Any, path: str) -> list[str]:
    if "anyOf" in schema:
        return _any_of(schema["anyOf"], value, path)
    errors: list[str] = []
    kind = schema.get("type")
    if kind is not None and not _is(value, kind):
        return [f"{path}: expected {kind}, got {_typename(value)}"]
    tag = _tag(schema)
    if tag is not None and value != tag:
        return [f"{path}: must be {tag!r}"]
    if "enum" in schema and value not in schema["enum"]:
        choices = ", ".join(repr(item) for item in schema["enum"])
        return [f"{path}: {value!r} is not one of {choices}"]
    if kind == "object":
        properties = schema.get("properties", {})
        extra = sorted(set(value) - set(properties))
        if extra and schema.get("additionalProperties") is False:
            errors.append(f"{path}: unexpected {', '.join(extra)}")
        for prop in schema.get("required", []):
            if prop not in value:
                errors.append(f"{path}: missing {prop}")
        for prop, sub in properties.items():
            if prop in value:
                errors.extend(_check(sub, value[prop], f"{path}.{prop}"))
    if kind == "array" and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(_check(schema["items"], item, f"{path}[{index}]"))
    return errors


def _any_of(variants: Sequence[Schema], value: Any, path: str) -> list[str]:
    best: list[str] | None = None
    for variant in variants:
        errors = _check(variant, value, path)
        if not errors:
            return []
        if _discriminated(variant, value):
            return errors
        if best is None or len(errors) < len(best):
            best = errors
    tags = _tags(variants)
    if tags and isinstance(value, dict):
        if len(tags) <= _LISTED:
            return [f"{path}: kind must be one of {', '.join(tags)}"]
        kind, tool = value.get("kind"), value.get("tool")
        given = f"{kind}:{tool}" if tool is not None else str(kind)
        return [f"{path}: {given!r} is not one of the steps you can take"]
    return best or [f"{path}: matches none of the allowed shapes"]


_LISTED = 8
"""Up to this many allowed kinds are named in an error; past it, the request
already lists them."""


def _discriminated(variant: Schema, value: Any) -> bool:
    """Whether ``value`` carries this variant's kind (and tool), so the errors
    against it are the ones worth reporting."""
    if not isinstance(value, dict):
        return False
    properties = variant.get("properties", {})
    tags = {key: _tag(properties.get(key, {})) for key in ("kind", "tool")}
    keys = [key for key, tag in tags.items() if tag is not None]
    return bool(keys) and all(value.get(key) == tags[key] for key in keys)


def _tags(variants: Sequence[Schema]) -> list[str]:
    tags = []
    for variant in variants:
        properties = variant.get("properties", {})
        kind = _tag(properties.get("kind", {}))
        tool = _tag(properties.get("tool", {}))
        if kind is not None:
            tags.append(f"{kind}:{tool}" if tool else str(kind))
    return tags


def _tag(schema: Schema) -> Any:
    """The one value a tag property allows, or ``None`` for any other
    property."""
    if "const" in schema:
        return schema["const"]
    allowed = schema.get("enum")
    if isinstance(allowed, list) and len(allowed) == 1 and "type" in schema:
        return allowed[0]
    return None


def _is(value: Any, kind: str) -> bool:
    if kind in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[kind])


def _typename(value: Any) -> str:
    for name in _TYPES:
        if _is(value, name):
            return name
    return type(value).__name__


__all__ = [
    "DECISIONS",
    "ROOT",
    "Schema",
    "answer_kinds",
    "compile_exit_schema",
    "compile_schema",
    "kit_tools",
    "validate",
]
