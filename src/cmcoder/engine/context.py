"""What a run of the engine carries for its tools: LangGraph's runtime
context (`context_schema`), given to the graph per run and read inside a
node or tool with `langgraph.runtime.get_runtime()`."""

from __future__ import annotations

from dataclasses import dataclass

from ..tools.base import ToolContext


@dataclass
class EngineContext:
    tool_ctx: ToolContext
    """The working folder, the project, the shell and the rest that cmcoder's tools use."""
