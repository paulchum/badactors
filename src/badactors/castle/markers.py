"""Control tokens a player might type to forge a turn boundary, per model family.

Words are fair game in the Castle; control tokens are not (SPEC §6). A chat
template marks where a system, user or assistant turn begins and ends with
special strings — ``<|im_start|>``, ``<|eot_id|>``, ``<end_of_turn>`` — and a
serving stack that parses them out of ordinary text would let one player's
words pose as the house. So before any player-authored text reaches a model it
is neutralised for that model's family: each marker the family's tokenizer
treats as special has its first and last characters swapped for look-alikes
(``<|im_end|>`` comes out between single angle quotation marks, U+2039 and
U+203A). The forgery stays readable, which is the evidence, and no longer
matches the token.

Only the recipient's own markers are touched, so ordinary text is never
mangled. A recipient whose family is unknown gets every family's markers. Plain
words such as ``SYSTEM:`` or ``Human:`` are not markers: whether a model
believes them is part of the game.

The catalogue is kept in the repo. TokenIR's survey covers open-weight models
only, and its families are the same ones listed here.
"""

from __future__ import annotations

import re
from functools import cache


def _deepseek(name: str) -> str:
    """DeepSeek's markers use a full-width bar and a lower block for spaces."""
    return "<\uff5c" + name.replace("_", "\u2581") + "\uff5c>"


FAMILIES: dict[str, tuple[str, ...]] = {
    "chatml": ("<|im_start|>", "<|im_end|>", "<|im_sep|>", "<|endoftext|>"),
    "harmony": (
        "<|start|>",
        "<|end|>",
        "<|message|>",
        "<|channel|>",
        "<|constrain|>",
        "<|return|>",
        "<|call|>",
        "<|startoftext|>",
        "<|endoftext|>",
    ),
    "llama3": (
        "<|begin_of_text|>",
        "<|end_of_text|>",
        "<|start_header_id|>",
        "<|end_header_id|>",
        "<|eot_id|>",
        "<|eom_id|>",
        "<|python_tag|>",
        "<|finetune_right_pad_id|>",
    ),
    "llama2": ("[INST]", "[/INST]", "<<SYS>>", "<</SYS>>", "<s>", "</s>"),
    "mistral": (
        "[INST]",
        "[/INST]",
        "[SYSTEM_PROMPT]",
        "[/SYSTEM_PROMPT]",
        "[AVAILABLE_TOOLS]",
        "[/AVAILABLE_TOOLS]",
        "[TOOL_CALLS]",
        "[TOOL_RESULTS]",
        "[/TOOL_RESULTS]",
        "[THINK]",
        "[/THINK]",
        "<s>",
        "</s>",
    ),
    "gemma": (
        "<start_of_turn>",
        "<end_of_turn>",
        "<bos>",
        "<eos>",
        "<start_of_image>",
        "<end_of_image>",
    ),
    "deepseek": (
        *(
            _deepseek(name)
            for name in (
                "begin_of_sentence",
                "end_of_sentence",
                "User",
                "Assistant",
                "tool_calls_begin",
                "tool_calls_end",
                "tool_call_begin",
                "tool_call_end",
                "tool_sep",
                "tool_outputs_begin",
                "tool_outputs_end",
                "tool_output_begin",
                "tool_output_end",
            )
        ),
        "<think>",
        "</think>",
    ),
    "qwen": (
        "<|im_start|>",
        "<|im_end|>",
        "<|endoftext|>",
        "<|object_ref_start|>",
        "<|object_ref_end|>",
        "<|box_start|>",
        "<|box_end|>",
        "<|quad_start|>",
        "<|quad_end|>",
        "<|vision_start|>",
        "<|vision_end|>",
        "<|fim_prefix|>",
        "<|fim_middle|>",
        "<|fim_suffix|>",
        "<tool_call>",
        "</tool_call>",
        "<tool_response>",
        "</tool_response>",
        "<think>",
        "</think>",
    ),
    "glm": (
        "[gMASK]",
        "<sop>",
        "<eop>",
        "<|system|>",
        "<|user|>",
        "<|assistant|>",
        "<|observation|>",
        "<|endoftext|>",
        "<think>",
        "</think>",
    ),
    "kimi": (
        "<|im_system|>",
        "<|im_user|>",
        "<|im_assistant|>",
        "<|im_middle|>",
        "<|im_end|>",
        "<|tool_calls_section_begin|>",
        "<|tool_calls_section_end|>",
        "<|tool_call_begin|>",
        "<|tool_call_end|>",
        "<|tool_call_argument_begin|>",
        "[BOS]",
        "[EOS]",
    ),
    "phi": ("<|system|>", "<|user|>", "<|assistant|>", "<|end|>", "<|endoftext|>"),
    "command": (
        "<BOS_TOKEN>",
        "<|START_OF_TURN_TOKEN|>",
        "<|END_OF_TURN_TOKEN|>",
        "<|SYSTEM_TOKEN|>",
        "<|USER_TOKEN|>",
        "<|CHATBOT_TOKEN|>",
        "<|START_THINKING|>",
        "<|END_THINKING|>",
        "<|START_RESPONSE|>",
        "<|END_RESPONSE|>",
    ),
    "grok": ("<|separator|>", "<|eos|>"),
    "anthropic": (),
}
"""Each family's special strings: the turn and role markers of its chat
template, and the reasoning and tool-call delimiters its tokenizer reserves.
Anthropic's API exposes no raw template; ``Human:`` and ``Assistant:`` there are
words like any other."""

VENDORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("openai/gpt-oss", ("harmony",)),
    ("openai/", ("harmony", "chatml")),
    ("anthropic/", ("anthropic",)),
    ("google/", ("gemma",)),
    ("meta-llama/", ("llama3", "llama2")),
    ("meta/", ("llama3", "llama2")),
    ("mistralai/", ("mistral",)),
    ("deepseek/", ("deepseek",)),
    ("qwen/", ("qwen",)),
    ("z-ai/", ("glm",)),
    ("thudm/", ("glm",)),
    ("moonshotai/", ("kimi",)),
    ("x-ai/", ("grok",)),
    ("microsoft/", ("phi", "chatml")),
    ("cohere/", ("command",)),
)
"""Model id prefixes, most specific first, and the families they speak."""

_LOOKALIKE = str.maketrans({"<": "\u2039", ">": "\u203a", "[": "\u27e6", "]": "\u27e7"})
"""Single angle quotation marks for angle brackets, white square brackets for
square ones."""


def families(model: str) -> tuple[str, ...]:
    """The families whose markers ``model`` may parse: every family when the
    vendor is unknown, since a marker missed is worse than one defused."""
    folded = model.casefold()
    for prefix, named in VENDORS:
        if folded.startswith(prefix):
            return named
    return tuple(FAMILIES)


def markers(model: str) -> tuple[str, ...]:
    return _compiled(model)[0]


def defuse(marker: str) -> str:
    """The marker with its delimiters swapped for look-alikes."""
    return (
        marker[0].translate(_LOOKALIKE)
        + marker[1:-1]
        + marker[-1].translate(_LOOKALIKE)
    )


def neutralise(text: str, model: str) -> tuple[str, tuple[str, ...]]:
    """``text`` as it may reach ``model``, and the markers it held, in order."""
    found_markers, pattern = _compiled(model)
    if pattern is None or not any(marker in text for marker in found_markers):
        return text, ()
    found: list[str] = []

    def swap(match: re.Match[str]) -> str:
        found.append(match.group(0))
        return defuse(match.group(0))

    return pattern.sub(swap, text), tuple(found)


@cache
def _compiled(model: str) -> tuple[tuple[str, ...], re.Pattern[str] | None]:
    every = sorted(
        {marker for family in families(model) for marker in FAMILIES[family]},
        key=lambda marker: (-len(marker), marker),
    )
    if not every:
        return (), None
    return tuple(every), re.compile("|".join(re.escape(marker) for marker in every))


__all__ = ["FAMILIES", "VENDORS", "defuse", "families", "markers", "neutralise"]
