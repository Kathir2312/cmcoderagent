"""E2: an approval with interrupt() and Command(resume=...) runs each tool
exactly once, with two tool calls in one reply (one needs approval, one
doesn't), with the in-memory and the SQLite checkpointer.

The 'hook' counter stands for a PreToolUse hook run in the middleware
before the approval: it shows what reruns when the graph resumes."""

from __future__ import annotations

import sqlite3
from collections import Counter

from common import chat_model, check, finding, mock
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, interrupt

runs: Counter[str] = Counter()


@tool("Read")
def read(file_path: str) -> str:
    """Read a file (no approval needed)."""
    runs["Read"] += 1
    return "contents"


@tool("Bash")
def bash(command: str) -> str:
    """Run a command (needs approval)."""
    runs["Bash"] += 1
    return "ran"


@wrap_tool_call
def permissions(request, handler):  # type: ignore[no-untyped-def]
    name = request.tool_call["name"]
    runs[f"hook {name}"] += 1
    if name == "Bash":
        answer = interrupt({"tool": name, "args": request.tool_call["args"]})
        if not answer.get("allow"):
            from langchain_core.messages import ToolMessage

            return ToolMessage("The user denied this action.", tool_call_id=request.tool_call["id"])
    return handler(request)


SCRIPT = [
    {
        "tool_calls": [
            {"id": "call_r", "name": "Read", "arguments": {"file_path": "a.py"}},
            {"id": "call_b", "name": "Bash", "arguments": {"command": "pytest"}},
        ]
    },
    {"content": "Done."},
]

for label, saver in (
    ("in-memory", InMemorySaver()),
    ("SQLite", SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))),
):
    runs.clear()
    server = mock(SCRIPT)
    agent = create_agent(
        chat_model(server), [read, bash], middleware=[permissions], checkpointer=saver
    )
    cfg = {"configurable": {"thread_id": label}}
    paused = agent.invoke({"messages": [("user", "test it")]}, cfg)
    asked = paused.get("__interrupt__")
    check(
        f"{label}: paused for approval", bool(asked), f"payload {asked[0].value if asked else None}"
    )
    done = agent.invoke(Command(resume={"allow": True}), cfg)
    check(
        f"{label}: each tool ran exactly once",
        runs["Read"] == 1 and runs["Bash"] == 1,
        f"Read {runs['Read']}, Bash {runs['Bash']}; final: {done['messages'][-1].content!r}",
    )
    finding(
        f"{label}: middleware before the approval on resume",
        f"hook for Read ran {runs['hook Read']}x, for Bash {runs['hook Bash']}x "
        "(code before interrupt() runs again on resume)",
    )
    server.__exit__(None, None, None)
