"""Model profiles: what a given model can do and how to talk to it."""

from __future__ import annotations

import fnmatch
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    match: str = "*"
    context_window: int = Field(32768, alias="contextWindow")
    max_output: int = Field(8192, alias="maxOutput")
    tool_calling: Literal["native", "prompted", "native-unreliable"] = Field(
        "native", alias="toolCalling"
    )
    parallel_tool_calls: bool = Field(False, alias="parallelToolCalls")
    # auto: split <think> tags out of content and accept reasoning fields.
    # think-open: output starts inside <think> (Qwen3 Thinking-2507 templates);
    # "auto" detects this and switches by itself.
    reasoning: Literal["none", "field", "think-tags", "think-open", "auto"] = "auto"
    # How to turn thinking off for quick calls.
    thinking_switch: Literal["chat_template_kwargs", "prompt", "none"] = Field(
        "none", alias="thinkingSwitch"
    )
    edit_format: Literal["str_replace", "search_replace_blocks", "whole_file", "udiff"] = Field(
        "str_replace", alias="editFormat"
    )
    prompt_tier: Literal["full", "compact"] = Field("full", alias="promptTier")
    temperature: float | None = None
    top_p: float | None = Field(None, alias="topP")
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = Field(
        "max_tokens", alias="maxTokensParam"
    )
    stream_usage: bool = Field(True, alias="streamUsage")
    extra_body: dict[str, Any] = Field(default_factory=dict, alias="extraBody")


# Built-in profiles, most specific first. Matched against the model name the
# user configured (a LiteLLM alias), case-insensitively.
BUILTIN_PROFILES: list[dict[str, Any]] = [
    {
        # Small Qwen3 models: used for quick jobs, not as the main agent.
        "match": "*qwen3*[0-9]b*",
        "_small": True,
        "contextWindow": 32768,
        "maxOutput": 8192,
        "reasoning": "auto",
        "thinkingSwitch": "chat_template_kwargs",
        "promptTier": "compact",
        "temperature": 0.7,
        "topP": 0.8,
    },
    {
        "match": "*qwen3*",
        "contextWindow": 32768,
        "maxOutput": 8192,
        "reasoning": "auto",
        "thinkingSwitch": "chat_template_kwargs",
        "promptTier": "full",
        "temperature": 0.6,
        "topP": 0.95,
    },
]

_SIZE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)b(?![a-z])")


def model_size_b(model: str) -> float | None:
    """Parameter count in billions parsed from a name like "qwen3-27b" or "qwen3:8b".

    MoE names such as "30b-a3b" give the total size (the largest number).
    """
    sizes = [float(m) for m in _SIZE_RE.findall(model.lower())]
    return max(sizes) if sizes else None


def _is_small(model: str) -> bool:
    size = model_size_b(model)
    return size is not None and size < 10


def resolve_profile(
    model: str,
    overrides: list[dict[str, Any]] | None = None,
    server_info: dict[str, Any] | None = None,
) -> ModelProfile:
    """Pick the profile for `model`.

    Order: built-in defaults < user overrides (settings `modelProfiles`) <
    values the server reports (e.g. LiteLLM /model/info context window).
    """
    data: dict[str, Any] = {}
    for p in BUILTIN_PROFILES:
        if not fnmatch.fnmatch(model.lower(), p["match"]):
            continue
        if p.get("_small") and not _is_small(model):
            continue
        data = {k: v for k, v in p.items() if not k.startswith("_")}
        break
    for o in overrides or []:
        if fnmatch.fnmatch(model.lower(), str(o.get("match", "*")).lower()):
            data.update(o)
    if server_info:
        if server_info.get("max_input_tokens"):
            data["contextWindow"] = int(server_info["max_input_tokens"])
        if server_info.get("max_output_tokens"):
            data["maxOutput"] = int(server_info["max_output_tokens"])
        if server_info.get("supports_function_calling") is False:
            data["toolCalling"] = "prompted"
    data.setdefault("match", model)
    return ModelProfile.model_validate(data)
