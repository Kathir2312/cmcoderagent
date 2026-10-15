"""What a run of the engine carries for its tools: LangGraph's runtime
context (`context_schema`), given to the graph per run and read inside a
node or tool with `langgraph.runtime.get_runtime()`."""

from __future__ import annotations

from dataclasses import dataclass

from ..tools.base import ToolContext


@dataclass
class EngineContext:
    tool_ctx: ToolContext | None = None
    """The working folder, the project, the shell and the rest that cmcoder's
    tools use. Optional: a LangGraph server (`langgraph dev`) makes the context
    with no arguments when a run gives none, and the tools then use their own."""
