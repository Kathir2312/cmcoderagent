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
    # Answer plain file work done through Bash (cat > f << EOF, cat f, grep -r,
    # find -name, sed -i, python -c "open(...)") with "use the Write/Read/Grep/
    # Glob/Edit tool" instead of running it (core/steer.py).
    steer_bash_file_work: bool = Field(True, alias="steerBashFileWork")
    extra_body: dict[str, Any] = Field(default_factory=dict, alias="extraBody")
    # "ollama": an Ollama model behind Open WebUI. Requests then carry Ollama
    # options: num_ctx (Ollama silently drops what doesn't fit its window, so it
    # must match cmcoder's) and think (the thinking switch).
    backend: Literal["default", "ollama"] = "default"
    # Whether the model can see images. Unset: what the gateway reports
    # (LiteLLM's supports_vision, Open WebUI's vision capability), else a guess
    # from the name (VISION_NAME). A setting wins: {"match": "...", "vision": true}.
    vision: bool | None = None
    # Where context_window came from, shown by `cmcoder doctor` (not a setting).
    context_window_source: str = Field("built-in default", exclude=True)


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
        # Several tool calls in one reply: needed to run subagents in parallel.
        "parallelToolCalls": True,
        "reasoning": "auto",
        "thinkingSwitch": "chat_template_kwargs",
        "promptTier": "full",
        "temperature": 0.6,
        "topP": 0.95,
    },
]

# Names of models that see images: Qwen-VL / Qwen-Omni, LLaVA, Pixtral, Gemma 3,
# Llama 4, InternVL, MiniCPM-V, and names that say "vision".
VISION_NAME = re.compile(
    r"(^|[^a-z])(vl|vision|llava|pixtral|omni|internvl|minicpm-?v|gemma-?3|llama-?4)([^a-z]|$)"
)


def looks_like_vision(model: str) -> bool:
    return bool(VISION_NAME.search(model.lower()))


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
    learned_window: tuple[int, str] | None = None,
) -> ModelProfile:
    """Pick the profile for `model`.

    Order: built-in defaults < user overrides (settings `modelProfiles`) <
    values the server reports (e.g. LiteLLM /model/info).

    The context window has its own order, most trusted last: built-in <
    /model/info < learned from the server itself (`learned_window`: its
    "maximum context length" error or a probe) < an explicit `contextWindow`
    in settings, which is the way to correct a wrong value.
    """
    data: dict[str, Any] = {}
    source = "built-in default"
    for p in BUILTIN_PROFILES:
        if not fnmatch.fnmatch(model.lower(), p["match"]):
            continue
        if p.get("_small") and not _is_small(model):
            continue
        data = {k: v for k, v in p.items() if not k.startswith("_")}
        break
    user_window: int | None = None
    user_set: set[str] = set()
    for o in overrides or []:
        if fnmatch.fnmatch(model.lower(), str(o.get("match", "*")).lower()):
            data.update(o)
            user_set |= set(o)
            if o.get("contextWindow"):
                user_window = int(o["contextWindow"])
    if server_info:
        if server_info.get("max_input_tokens"):
            data["contextWindow"] = int(server_info["max_input_tokens"])
            source = str(server_info.get("window_source") or "server (/model/info)")
        if server_info.get("backend") == "ollama":
            data["backend"] = "ollama"
        if server_info.get("max_output_tokens"):
            data["maxOutput"] = int(server_info["max_output_tokens"])
        if server_info.get("supports_function_calling") is False:
            data["toolCalling"] = "prompted"
        if isinstance(server_info.get("supports_vision"), bool) and "vision" not in user_set:
            data["vision"] = server_info["supports_vision"]
    if learned_window:
        data["contextWindow"], source = learned_window
    if user_window:
        data["contextWindow"], source = user_window, "settings (modelProfiles)"
    data.setdefault("match", model)
    if data.get("vision") is None:
        data["vision"] = looks_like_vision(model)
    profile = ModelProfile.model_validate(data)
    profile.context_window_source = source
    return profile
