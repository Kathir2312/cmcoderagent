"""E6: the cost of saving every step with the SQLite checkpointer: one turn
of 25 tool calls, each returning 20,000 characters, timed against the
in-memory checkpointer, and the database's size afterwards.

(Run it on Windows too: antivirus scanning makes small file writes slower.)"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

from common import chat_model, check, finding, mock
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

STEPS = 25


@tool("Read")
def read(file_path: str) -> str:
    """Read a file."""
    return ("x = 1  # " + file_path + "\n") * 1000  # about 20,000 characters


SCRIPT = [
    {"tool_calls": [{"name": "Read", "arguments": {"file_path": f"f{i}.py"}}]} for i in range(STEPS)
] + [{"content": "Read them all."}]


def timed(saver: object) -> float:
    server = mock(SCRIPT, context_window=1_000_000)
    agent = create_agent(chat_model(server), [read], checkpointer=saver)
    start = time.perf_counter()
    agent.invoke(
        {"messages": [("user", "read them")]},
        {"configurable": {"thread_id": "t"}, "recursion_limit": 200},
    )
    server.__exit__(None, None, None)
    return time.perf_counter() - start


memory = timed(InMemorySaver())
db = Path(tempfile.mkdtemp()) / "checkpoints.sqlite"
sqlite = timed(SqliteSaver(sqlite3.connect(db, check_same_thread=False)))
extra = (sqlite - memory) / (STEPS * 2 + 1)
finding(
    "time for the turn",
    f"in-memory {memory:.2f} s, SQLite {sqlite:.2f} s, about {extra * 1000:.0f} ms more per step",
)
finding(
    "database size after one turn", f"{db.stat().st_size / 1e6:.1f} MB for {STEPS} reads of 20 kB"
)
check("SQLite adds under 50 ms per step here", extra < 0.05)
