"""Agent Protocol client messages: what a front end sends to `cmcoder --protocol stdio`.

One JSON object per line on the agent's stdin. The agent answers with the
events in `events.py` on its stdout.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class _Message(BaseModel):
    pass


class UserMessage(_Message):
    """Start a turn. Only one turn runs at a time."""

    type: Literal["user_message"] = "user_message"
    text: str = Field(min_length=1)


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


class Shutdown(_Message):
    """Stop the running turn, save the session and exit (EOF does the same)."""

    type: Literal["shutdown"] = "shutdown"


ClientMessage = Annotated[
    UserMessage | Interrupt | PermissionResponse | SetMode | SetModel | Shutdown,
    Field(discriminator="type"),
]

_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_message(line: str | bytes) -> ClientMessage:
    """Parse one line; raises pydantic.ValidationError for bad JSON or fields."""
    return _adapter.validate_json(line)


def messages_json_schema() -> dict[str, Any]:
    return _adapter.json_schema()
