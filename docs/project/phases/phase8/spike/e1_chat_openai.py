"""E1: ChatOpenAI in create_agent against the vLLM-strict mock gateway.

a. a tool round trip; b. the model's reasoning text; c. a tool call with
broken JSON, then one more message (cmcoder's 400 of 14 Oct); d. an empty
reply, then one more message (the empty replies of 14 Oct)."""

from __future__ import annotations

import json

from common import chat_model, check, finding, mock
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver


@tool("Read")
def read(file_path: str) -> str:
    """Read a text file."""
    return "     1\tdef add(a, b):\n     2\t    return a - b"


def roles(body: dict) -> list[str]:
    return [m["role"] for m in body["messages"]]


# a. A tool round trip.
server = mock(
    [
        {"tool_calls": [{"id": "call_1", "name": "Read", "arguments": {"file_path": "app.py"}}]},
        {"content": "app.py subtracts instead of adding."},
    ]
)
agent = create_agent(chat_model(server), [read], system_prompt="You are a test agent.")
out = agent.invoke({"messages": [("user", "What does app.py do?")]})
check(
    "a. tool round trip",
    out["messages"][-1].content == "app.py subtracts instead of adding."
    and roles(server.requests[1]) == ["system", "user", "assistant", "tool"],
    f"requests: {[roles(r) for r in server.requests]}",
)
server.__exit__(None, None, None)

# b. Reasoning text (vLLM's reasoning_content field).
server = mock([{"content": "Hello.", "reasoning": "The user greets me."}])
msg = chat_model(server).invoke("hi")
kept = json.dumps(msg.additional_kwargs) + json.dumps(
    getattr(msg, "content_blocks", []), default=str
)
finding(
    "b. reasoning_content kept by ChatOpenAI",
    "yes" if "The user greets me." in kept else "no: the model's reasoning is dropped",
)
server.__exit__(None, None, None)

# c. A tool call with broken JSON, then "ping" in the same thread.
broken = '{"sql": "SELECT "Name" FROM t", "max_rows": 5}'
server = mock(
    [
        {"tool_calls": [{"id": "call_1", "name": "Read", "arguments": broken}]},
        {"content": "pong"},
    ]
)
agent = create_agent(chat_model(server), [read], checkpointer=InMemorySaver())
cfg = {"configurable": {"thread_id": "c"}}
first = agent.invoke({"messages": [("user", "query it")]}, cfg)
last = first["messages"][-1]
finding(
    "c1. broken tool call",
    f"tool_calls={last.tool_calls}, invalid_tool_calls={len(last.invalid_tool_calls)}; "
    f"the turn ended after {len(server.requests)} request(s) without telling the model",
)
try:
    agent.invoke({"messages": [("user", "ping")]}, cfg)
    sent = [c for m in server.requests[-1]["messages"] for c in m.get("tool_calls") or []]
    check("c2. next message after a broken call", True, f"sent back as {sent}")
except Exception as e:  # what cmcoder hit with vLLM
    check("c2. next message after a broken call", False, f"{type(e).__name__}: {str(e)[:160]}")
server.__exit__(None, None, None)

# d. An empty reply, then "continue".
server = mock([{"content": ""}, {"content": "Back on track."}])
agent = create_agent(chat_model(server), [read], checkpointer=InMemorySaver())
cfg = {"configurable": {"thread_id": "d"}}
agent.invoke({"messages": [("user", "go on")]}, cfg)
agent.invoke({"messages": [("user", "continue")]}, cfg)
history = server.requests[-1]["messages"]
empty = [
    m
    for m in history
    if m["role"] == "assistant" and not m.get("content") and not m.get("tool_calls")
]
check(
    "d. empty reply left out of the next request",
    not empty,
    f"roles sent: {roles(server.requests[-1])}; empty assistant messages: {len(empty)}",
)
server.__exit__(None, None, None)
