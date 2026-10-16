"""cmcoder's messages (providers/messages.py) and LangChain's, both ways.

Until M5 moves sessions to LangGraph's checkpointer, the classic message list
(`agent.messages`) stays what sessions, /rewind and /compact work on. Each
turn the graph starts from it, and `Mirror` writes the graph's messages back
after every step. It keeps each message the same Python object from step to
step, because the session file writes only messages it hasn't seen
(core/sessions.py tells them apart by identity).

Messages from cmcoder (the conversation so far, the user's new message) map
back to the objects they came from: those keep what LangChain has no place
for (a user message's turn number and images). Messages the graph made
(replies, tool results, reminders) are converted, then updated in place.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ..llm.chat_openai import REASONING
from ..providers.content import user_content
from ..providers.messages import Message, ToolCall, parse_tool_arguments


def to_langchain(m: Message, *, vision: bool, msg_id: str | None = None) -> BaseMessage:
    """One cmcoder message as LangChain's (images as image parts when the
    model sees them, else as the vision model's description)."""
    mid = msg_id or f"cm-{uuid.uuid4().hex[:12]}"
    if m.role == "system":
        return SystemMessage(m.content, id=mid)
    if m.role == "user":
        return HumanMessage(user_content(m, vision), id=mid)  # type: ignore[arg-type]
    if m.role == "tool":
        return ToolMessage(m.content, tool_call_id=m.tool_call_id or "", name=m.name, id=mid)
    calls: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    for c in m.tool_calls:
        args, error = parse_tool_arguments(c.arguments)
        if args is None:  # sent back as {"_raw": …} (llm/chat_openai.py, wire_history)
            broken.append({"type": "invalid_tool_call", "id": c.id, "name": c.name,
                           "args": c.arguments, "error": error})
        else:
            calls.append({"type": "tool_call", "id": c.id, "name": c.name, "args": args})
    kwargs = {REASONING: m.reasoning} if m.reasoning else {}
    return AIMessage(
        m.content, tool_calls=calls, invalid_tool_calls=broken, additional_kwargs=kwargs, id=mid
    )


def from_langchain(lc: BaseMessage) -> Message:
    m = Message(role="user")
    update(m, lc)
    return m


def update(m: Message, lc: BaseMessage) -> None:
    """Set `m` (a message the graph made) from its LangChain version."""
    if isinstance(lc, AIMessage):
        m.role = "assistant"
        m.content = lc.text
        m.reasoning = str(lc.additional_kwargs.get(REASONING, ""))
        m.tool_calls = [
            ToolCall(c["id"] or "", c["name"], json.dumps(c["args"], ensure_ascii=False))
            for c in lc.tool_calls
        ] + [
            ToolCall(c.get("id") or "", c.get("name") or "", str(c.get("args") or ""))
            for c in lc.invalid_tool_calls
        ]
    elif isinstance(lc, ToolMessage):
        m.role = "tool"
        m.content = lc.text
        m.tool_call_id = lc.tool_call_id
        m.name = lc.name
    elif isinstance(lc, SystemMessage):
        m.role = "system"
        m.content = lc.text
    else:
        m.role = "user"
        m.content = lc.text


def is_empty_reply(lc: BaseMessage) -> bool:
    """A reply with nothing in it: kept out of the history (the model copies it)."""
    return (
        isinstance(lc, AIMessage)
        and not lc.text.strip()
        and not lc.tool_calls
        and not lc.invalid_tool_calls
    )


class Mirror:
    """Keeps the classic message list in step with the graph's (see above)."""

    def __init__(self) -> None:
        self._ours: dict[str, Message] = {}  # LangChain id -> the cmcoder message it came from
        self._made: dict[str, Message] = {}  # LangChain id -> what a graph message became

    def to_graph(self, messages: list[Message], *, vision: bool) -> list[BaseMessage]:
        out = []
        for m in messages:
            lc = to_langchain(m, vision=vision)
            assert lc.id is not None
            self._ours[lc.id] = m
            out.append(lc)
        return out

    def replace(self, m: Message, lc_id: str, *, vision: bool) -> BaseMessage:
        """`m` changed on cmcoder's side (a hook's note, code context): its new
        LangChain version, under the same id (which replaces it in the state)."""
        self._ours[lc_id] = m
        return to_langchain(m, vision=vision, msg_id=lc_id)

    def message_for(self, lc_id: str) -> Message | None:
        return self._ours.get(lc_id)

    def appended(self, lc: BaseMessage, extra: str, *, vision: bool) -> BaseMessage:
        """`lc` with `extra` after its text, under the same id (a reminder
        added to the last message, as the classic engine does)."""
        if lc.id is not None and (m := self._ours.get(lc.id)) is not None:
            m.content += extra
            return to_langchain(m, vision=vision, msg_id=lc.id)
        return lc.model_copy(update={"content": lc.text + extra})

    def from_graph(self, messages: list[BaseMessage]) -> list[Message]:
        out: list[Message] = []
        for lc in messages:
            if is_empty_reply(lc):
                continue
            key = lc.id or ""
            if (m := self._ours.get(key)) is not None:
                out.append(m)
                continue
            if (m := self._made.get(key)) is None:
                m = self._made[key] = from_langchain(lc)
            else:
                update(m, lc)
            out.append(m)
        return out
