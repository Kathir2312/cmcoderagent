"""Agent Protocol events: what the agent core emits to front ends.

Used by `-p --output-format stream-json` and by `--protocol stdio` (the VS
Code extension), whose client messages are in `messages.py`. These pydantic
models are the single source of truth; TypeScript types are generated from
`protocol_json_schema()`.
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


class Compacted(_Event):
    """The older part of the conversation was replaced by a summary."""

    type: Literal["compacted"] = "compacted"
    trigger: Literal["auto", "manual"]
    summarized_messages: int
    tokens_before: int  # estimates
    tokens_after: int
    model: str


class FileChange(BaseModel):
    """A proposed file change, for review in a diff editor."""

    path: str  # absolute
    before: str | None  # None: a new file
    after: str


class PermissionRequest(_Event):
    """The agent needs the user's approval for a tool call (stdio protocol).

    Answer with a `permission_response` carrying the same `request_id`."""

    type: Literal["permission_request"] = "permission_request"
    request_id: str
    tool_use_id: str
    name: str
    label: str
    input: dict[str, Any]
    suggested_rule: str
    reason: str = ""
    # False for high-risk commands: offer "allow once" only, never "always".
    can_remember: bool = True
    # Write/Edit: the whole file before and after (absent for big files).
    change: FileChange | None = None


class TodoUpdate(_Event):
    """The todo list changed (stdio protocol)."""

    type: Literal["todo_update"] = "todo_update"
    todos: list[dict[str, Any]]


class ModeChanged(_Event):
    type: Literal["mode_changed"] = "mode_changed"
    mode: str


class ModelChanged(_Event):
    type: Literal["model_changed"] = "model_changed"
    model: str
    context_window: int | None = None


class IdeToolRequest(_Event):
    """Run an IDE tool in the client; answer with `ide_tool_result`."""

    type: Literal["ide_tool_request"] = "ide_tool_request"
    request_id: str
    name: str
    input: dict[str, Any]


class SessionSummary(BaseModel):
    id: str
    title: str
    updated: float  # Unix time
    messages: int


class SessionList(_Event):
    type: Literal["session_list"] = "session_list"
    sessions: list[SessionSummary]


class HistoryItem(BaseModel):
    role: Literal["user", "assistant", "tool"]
    text: str  # for "tool": the call's label, e.g. Edit(app.py)


class History(_Event):
    """A resumed conversation so far, so the client can show it."""

    type: Literal["history"] = "history"
    messages: list[HistoryItem]


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
    | Compacted
    | PermissionRequest
    | TodoUpdate
    | ModeChanged
    | ModelChanged
    | IdeToolRequest
    | SessionList
    | History
    | Result,
    Field(discriminator="type"),
]

_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(data: dict[str, Any]) -> Event:
    return _adapter.validate_python(data)


def protocol_json_schema() -> dict[str, Any]:
    return _adapter.json_schema()
