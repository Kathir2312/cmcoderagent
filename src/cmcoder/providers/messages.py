"""Provider-neutral message and stream-event types.

The agent core only ever sees these types. Each provider adapter translates
them to and from its wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    # Raw JSON text as produced by the model. Parsed (and repaired) by the agent,
    # because weaker models often produce slightly malformed JSON.
    arguments: str


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
