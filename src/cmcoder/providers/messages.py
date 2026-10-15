"""Provider-neutral message and stream-event types.

The agent core only ever sees these types. Each provider adapter translates
them to and from its wire format.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ..images import Image

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    # Raw JSON text as produced by the model. Parsed (and repaired) by
    # parse_tool_arguments, because weaker models often produce slightly
    # malformed JSON; the gateway gets it back strict (wire_arguments).
    arguments: str


def parse_tool_arguments(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse model-produced JSON arguments, repairing common mistakes."""
    text = (raw or "").strip()
    if not text:
        return {}, None
    attempts = [text]
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    attempts.append(re.sub(r",\s*([}\]])", r"\1", fenced))
    value: Any = None
    error: str | None = None
    for candidate in attempts:
        try:
            value = json.loads(candidate, strict=False)
            error = None
            break
        except ValueError as e:
            error = str(e)
    if error is not None:
        # Python-style dicts ({'path': 'a', 'all': True}) from some small models.
        try:
            value = ast.literal_eval(fenced)
            error = None
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass
    if error is not None:
        return None, f"arguments are not valid JSON ({error})"
    if isinstance(value, str):  # double-encoded
        try:
            value = json.loads(value, strict=False)
        except ValueError:
            pass
    if not isinstance(value, dict):
        return None, "arguments must be a JSON object"
    return value, None


def wire_arguments(raw: str) -> str:
    """A past tool call's arguments as the strict JSON object servers expect.

    vLLM parses every one in the history and rejects the whole request over
    one the model got wrong (400 "Expecting ',' delimiter"), which would fail
    every later message in the session too. So: as written when it's strict
    JSON, else as repaired (what the tool ran with), else what the model wrote
    under "_raw" (its tool result says what was wrong with it)."""
    try:
        if isinstance(json.loads(raw), dict):
            return raw
    except ValueError:
        pass
    args, _ = parse_tool_arguments(raw)
    return json.dumps(args if args is not None else {"_raw": raw}, ensure_ascii=False)


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    # Model reasoning (e.g. Qwen3 <think> text). Kept for display and logs,
    # never sent back to the model.
    reasoning: str = ""
    # On user messages: the turn number in the session (for /rewind). Not sent.
    turn: int | None = None
    # On user messages: images the user attached (see images.py).
    images: list[Image] = field(default_factory=list)

    @classmethod
    def system(cls, content: str) -> Message:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    @classmethod
    def tool_result(cls, call_id: str, name: str, content: str) -> Message:
        return cls(role="tool", content=content, tool_call_id=call_id, name=name)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # True when the server did not report usage and the numbers are estimates.
    estimated: bool = False
    cost: float | None = None

    def add(self, other: Usage) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.estimated = self.estimated or other.estimated
        if other.cost is not None:
            self.cost = (self.cost or 0.0) + other.cost


# --- Stream events -----------------------------------------------------------


@dataclass
class TextDelta:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ReasoningDelta:
    text: str
    type: Literal["reasoning"] = "reasoning"


@dataclass
class ToolCallStarted:
    index: int
    name: str
    type: Literal["tool_call_started"] = "tool_call_started"


@dataclass
class StreamDone:
    message: Message
    usage: Usage
    finish_reason: str | None
    # The model that actually answered (LiteLLM may route to a fallback).
    model: str | None = None
    type: Literal["done"] = "done"


StreamEvent = TextDelta | ReasoningDelta | ToolCallStarted | StreamDone
