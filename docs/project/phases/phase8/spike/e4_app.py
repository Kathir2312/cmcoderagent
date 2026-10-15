"""E4's program: built with PyInstaller, it runs one agent turn against the
gateway URL it's given and prints how long the imports and the turn took."""

from __future__ import annotations

import os
import sys
import time

start = time.perf_counter()
for name in list(os.environ):  # the tracing guard, before any LangChain import
    if name.startswith(("LANGSMITH_", "LANGCHAIN_TRACING", "LANGCHAIN_API_KEY")):
        del os.environ[name]

from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware import SummarizationMiddleware  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

imported = time.perf_counter()


@tool("Read")
def read(file_path: str) -> str:
    """Read a file."""
    return "def add(a, b):\n    return a - b"


if __name__ == "__main__":
    import sqlite3

    # LangChain doesn't know Qwen's window: cmcoder supplies it (its window detection).
    model = ChatOpenAI(
        model="qwen3-27b",
        base_url=sys.argv[1],
        api_key="sk-spike",
        streaming=True,
        max_retries=0,
        profile={"max_input_tokens": 32768},
    )
    agent = create_agent(
        model,
        [read],
        middleware=[SummarizationMiddleware(model, trigger=("fraction", 0.8))],
        checkpointer=SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)),
    )
    out = agent.invoke(
        {"messages": [("user", "What does app.py do?")]}, {"configurable": {"thread_id": "1"}}
    )
    print(
        f"imports {imported - start:.2f} s, turn {time.perf_counter() - imported:.2f} s: {out['messages'][-1].content}"
    )
