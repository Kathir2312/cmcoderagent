"""Agent Protocol events: what the agent core emits to front ends.

Used by `-p --output-format stream-json` now, and by the VS Code extension
(`--protocol stdio`) in Phase 2. These pydantic models are the single source of
truth; TypeScript types are generated from `protocol_json_schema()`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

PROTOCOL_VERSION = 1


class _Event(BaseModel):
    pass


class SystemInit(_Event):
    type: Literal["system_init"] = "system_init"
    protocol_version: int = PROTOCOL_VERSION
    session_id: str
    cwd: str
    model: str
    provider: str
    tools: list[str]
    permission_mode: str


class AssistantDelta(_Event):
    type: Literal["assistant_delta"] = "assistant_delta"
    text: str


class ReasoningDelta(_Event):
    type: Literal["reasoning_delta"] = "reasoning_delta"
    text: str


class AssistantMessage(_Event):
    type: Literal["assistant_message"] = "assistant_message"
    text: str
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model: str | None = None


class ToolUse(_Event):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]
    label: str


class ToolResult(_Event):
    type: Literal["tool_result"] = "tool_result"
    id: str
    name: str
    content: str
    is_error: bool = False
    summary: str | None = None


class PermissionDenied(_Event):
    type: Literal["permission_denied"] = "permission_denied"
    id: str
    name: str
    reason: str


class UsageUpdate(_Event):
    type: Literal["usage"] = "usage"
    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False
    cost: float | None = None
    context_window: int | None = None


class Warning(_Event):  # noqa: A001 - protocol name
    type: Literal["warning"] = "warning"
    message: str


class Error(_Event):
    type: Literal["error"] = "error"
    kind: str
    message: str
    hint: str | None = None


class Result(_Event):
    type: Literal["result"] = "result"
    subtype: Literal["success", "error", "max_turns", "interrupted"]
    is_error: bool
    result: str
    num_turns: int
    duration_ms: int
    usage: dict[str, Any]
    session_id: str


Event = Annotated[
    SystemInit
    | AssistantDelta
    | ReasoningDelta
    | AssistantMessage
    | ToolUse
    | ToolResult
    | PermissionDenied
    | UsageUpdate
    | Warning
    | Error
    | Result,
    Field(discriminator="type"),
]

_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(data: dict[str, Any]) -> Event:
    return _adapter.validate_python(data)


def protocol_json_schema() -> dict[str, Any]:
    return _adapter.json_schema()
