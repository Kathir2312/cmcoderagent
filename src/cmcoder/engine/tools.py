"""cmcoder's tools as LangChain tools.

Each tool (Read, Edit, Bash, …) keeps its own code; `CmcoderTool` wraps it as
a LangChain `BaseTool`, so LangGraph can run it in its tool node:

- the model sees the schema it sees from the classic engine (`Tool.spec()`,
  a JSON schema), not one LangChain derives; LangChain only inlines shared
  definitions (`$defs`/`$ref`, as in TodoWrite's items): the same schema,
  and simpler for small models' tool parsers;
- the arguments are checked with the tool's own pydantic `Input`, with the
  same error text as before;
- a failed tool becomes a `ToolMessage` with `status="error"` (LangChain's
  `ToolException`); its output is cut to the run's limit as before;
- the tool runs where the run is: in the graph's runtime context
  (`EngineContext`), or the `ToolContext` it was made with.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import BaseTool, ToolException
from pydantic import ConfigDict, ValidationError

from ..providers.messages import ToolSpec
from ..tools.base import Tool, ToolContext, truncate_middle
from .context import EngineContext


def format_validation_error(e: ValidationError) -> str:
    """As the classic engine words it: `file_path: Field required`."""
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def run_context(fallback: ToolContext | None) -> ToolContext:
    """The run's ToolContext: from the graph's runtime context when the tool
    runs inside a graph, else the one the tool was made with."""
    from langgraph.runtime import get_runtime

    try:
        runtime = get_runtime()
    except (RuntimeError, LookupError):  # not inside a graph
        runtime = None
    context = runtime.context if runtime is not None else None
    if isinstance(context, EngineContext) and context.tool_ctx is not None:
        return context.tool_ctx
    if fallback is None:
        raise ToolException("No working folder for this tool (no EngineContext in the run).")
    return fallback


class CmcoderTool(BaseTool):
    """One of cmcoder's tools as a LangChain tool (see the module's docstring)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Carried, not validated: a cmcoder Tool and its ToolContext (whose
    # type-checking-only imports pydantic can't resolve).
    tool: Any
    ctx: Any = None
    handle_tool_error: bool | str | Any = True
    handle_validation_error: bool | str | Any = True
    # (content, artifact): the artifact carries the tool's one-line summary for the UI.
    response_format: Any = "content_and_artifact"

    def __init__(self, tool: Tool, ctx: ToolContext | None = None, **kwargs: Any) -> None:
        spec: ToolSpec = tool.spec()
        super().__init__(
            name=spec.name,
            description=spec.description,
            args_schema=spec.parameters,  # a JSON schema: sent to the model as it is
            tool=tool,  # type: ignore[call-arg]
            ctx=ctx,  # type: ignore[call-arg]
            **kwargs,
        )

    def _run(self, *args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        return asyncio.run(self._arun(*args, **kwargs))

    async def _arun(self, *args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        kwargs.pop("run_manager", None)
        kwargs.pop("config", None)
        tool: Tool = self.tool
        ctx = run_context(self.ctx)
        try:
            args_in = tool.Input.model_validate(kwargs)
        except ValidationError as e:
            raise ToolException(
                f"Invalid arguments for {tool.name}: {format_validation_error(e)}"
            ) from e
        try:
            res = await tool.run(args_in, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a tool bug must not end the run (as in the classic engine)
            raise ToolException(f"{tool.name} failed: {type(e).__name__}: {e}") from e
        content = truncate_middle(res.content, ctx.max_output_chars)
        if res.is_error:
            raise ToolException(content)
        return content, {"summary": res.summary}


def langchain_tools(tools: list[Tool], ctx: ToolContext | None = None) -> list[CmcoderTool]:
    return [CmcoderTool(t, ctx) for t in tools]
