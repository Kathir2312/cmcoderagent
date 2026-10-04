"""Agent Protocol client messages: what a front end sends to `cmcoder --protocol stdio`.

One JSON object per line on the agent's stdin. The agent answers with the
events in `events.py` on its stdout.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class _Message(BaseModel):
    pass


class IdeSelection(BaseModel):
    path: str
    start_line: int  # 1-based, inclusive
    end_line: int
    text: str


class IdeDiagnostic(BaseModel):
    path: str
    line: int  # 1-based
    severity: Literal["error", "warning", "info", "hint"]
    message: str
    source: str | None = None


class IdeContext(BaseModel):
    """What the user has open in the editor, sent with a message."""

    active_file: str | None = None
    selection: IdeSelection | None = None
    diagnostics: list[IdeDiagnostic] = Field(default_factory=list)


class UserMessage(_Message):
    """Start a turn. Only one turn runs at a time."""

    type: Literal["user_message"] = "user_message"
    text: str = Field(min_length=1)
    # The editor's state; given to the model as a note, not shown as the prompt.
    context: IdeContext | None = None


class Interrupt(_Message):
    """Stop the running turn (like Ctrl+C in the CLI)."""

    type: Literal["interrupt"] = "interrupt"


class PermissionResponse(_Message):
    """The answer to a `permission_request`."""

    type: Literal["permission_response"] = "permission_response"
    request_id: str
    allow: bool
    # "Always": save an allow rule (ignored when the request can't be remembered).
    remember: bool = False
    # When denying: what the model should do instead.
    feedback: str | None = None


class SetMode(_Message):
    type: Literal["set_mode"] = "set_mode"
    mode: str = Field(min_length=1)


class SetModel(_Message):
    type: Literal["set_model"] = "set_model"
    model: str = Field(min_length=1)


class IdeCapabilities(_Message):
    """The client can run these IDE tools (e.g. getDiagnostics, openFile).

    Send once after `system_init`; the agent offers them to the model and
    asks the client to run them with `ide_tool_request`."""

    type: Literal["ide_capabilities"] = "ide_capabilities"
    tools: list[str]


class IdeToolResult(_Message):
    """The answer to an `ide_tool_request`."""

    type: Literal["ide_tool_result"] = "ide_tool_result"
    request_id: str
    content: str
    is_error: bool = False


class ListSessions(_Message):
    """Ask for this project's saved conversations (answered with `session_list`)."""

    type: Literal["list_sessions"] = "list_sessions"


class ListCommands(_Message):
    """Ask for the slash commands (answered with `command_list`). A
    `user_message` whose text starts with one of them runs it."""

    type: Literal["list_commands"] = "list_commands"


class Rewind(_Message):
    """Go back to before user message `turn` (from `rewind_points`): undo the
    agent's file changes (`code`), the conversation, or both. Files outside the
    project are only restored with `outside`."""

    type: Literal["rewind"] = "rewind"
    turn: int
    code: bool = True
    conversation: bool = True
    outside: bool = False


class Index(_Message):
    """Code search: the index's state, or update / rebuild / clear it. Answered
    with `index_status` (and `index_progress` while it works)."""

    type: Literal["index"] = "index"
    action: Literal["status", "update", "rebuild", "clear"] = "status"


class RagCandidates(_Message):
    """The gateways' models, for picking an embedding model (`rag_candidates`)."""

    type: Literal["rag_candidates"] = "rag_candidates"


class RagSetup(_Message):
    """Set up code search, as `cmcoder rag setup` does (`rag_setup_result`)."""

    type: Literal["rag_setup"] = "rag_setup"
    embedding_model: str
    store: Literal["local", "chroma", "chroma-server"] = "local"
    url: str | None = None
    api_key: str | None = None  # a Chroma server's; goes to the keychain
    scope: Literal["user", "project"] = "user"
    read_only: bool = False
    index_now: bool = True


class Shutdown(_Message):
    """Stop the running turn, save the session and exit (EOF does the same)."""

    type: Literal["shutdown"] = "shutdown"


ClientMessage = Annotated[
    UserMessage
    | Interrupt
    | PermissionResponse
    | SetMode
    | SetModel
    | IdeCapabilities
    | IdeToolResult
    | ListSessions
    | ListCommands
    | Rewind
    | Index
    | RagCandidates
    | RagSetup
    | Shutdown,
    Field(discriminator="type"),
]

_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_message(line: str | bytes) -> ClientMessage:
    """Parse one line; raises pydantic.ValidationError for bad JSON or fields."""
    return _adapter.validate_json(line)


def messages_json_schema() -> dict[str, Any]:
    return _adapter.json_schema()
