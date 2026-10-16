"""cmcoder's tools as LangChain tools.

Each tool (Read, Edit, Bash, …) keeps its own code; `CmcoderTool` wraps it as
a LangChain `BaseTool`, so LangGraph can run it in its tool node:

- the model sees the schema it sees from the classic engine (`Tool.spec()`,
  a JSON schema), not one LangChain derives; LangChain only inlines shared
  definitions (`$defs`/`$ref`, as in TodoWrite's items): the same schema,
  and simpler for small models' tool parsers;
- the arguments are checked with the tool's own pydantic `Input`, with the
  same error text as before;
- a failed tool becomes a `ToolMessage` with `status="error"`; its output
  is cut to the run's limit as before. A failure the tool reports keeps its
  one-line summary (the artifact); one it raises becomes LangChain's
  `ToolException`;
- the tool runs where the run is: in the graph's runtime context
  (`EngineContext`), or the `ToolContext` it was made with;
- a tool that streams (the Task tool, whose subagent's tool calls show as
  they happen) sends what it streams to the front end (engine/events.py).
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, ToolException
from pydantic import ConfigDict, ValidationError

from ..providers.messages import ToolSpec
from ..tools.base import Tool, ToolContext, ToolResult, truncate_middle
from .context import EngineContext

# The id of the tool call being run (for a tool that needs it, as Task does).
_CALL_ID: ContextVar[str] = ContextVar("cmcoder_tool_call_id", default="")


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

    async def arun(self, tool_input: Any, *args: Any, **kwargs: Any) -> Any:
        token = _CALL_ID.set(kwargs.get("tool_call_id") or "")
        try:
            out = await super().arun(tool_input, *args, **kwargs)
        finally:
            _CALL_ID.reset(token)
        if isinstance(out, ToolMessage) and isinstance(out.artifact, dict):
            if out.artifact.get("is_error"):
                out.status = "error"
        return out

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
        stop_turn = False
        try:
            if (run_stream := getattr(tool, "run_stream", None)) is not None:
                res, stop_turn = await self._streamed(run_stream(args_in, ctx, _CALL_ID.get()))
            else:
                res = await tool.run(args_in, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a tool bug must not end the run (as in the classic engine)
            raise ToolException(f"{tool.name} failed: {type(e).__name__}: {e}") from e
        content = truncate_middle(res.content, ctx.max_output_chars)
        artifact: dict[str, Any] = {"summary": res.summary}
        if res.is_error:
            artifact["is_error"] = True  # status "error" (arun), with the summary kept
        if stop_turn:
            artifact["stop_turn"] = True  # the user denied a subagent's tool call
        return content, artifact

    @staticmethod
    async def _streamed(items: Any) -> tuple[ToolResult, bool]:
        """Passes a streaming tool's events on, each delivered before the
        next (a subagent's permission question must not overtake them)."""
        from ..core.subagents import TaskDone
        from .events import delivered, emit

        res = ToolResult("The tool didn't finish.", is_error=True)
        stop_turn = False
        async for item in items:
            if isinstance(item, TaskDone):
                res, stop_turn = item.result, item.stop_turn
            else:
                emit(item)
                await delivered()
        return res, stop_turn


def langchain_tools(tools: list[Tool], ctx: ToolContext | None = None) -> list[CmcoderTool]:
    return [CmcoderTool(t, ctx) for t in tools]
