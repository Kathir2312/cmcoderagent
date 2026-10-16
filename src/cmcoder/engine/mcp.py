"""MCP servers' tools for the Phase 8 engine, through LangChain's own MCP
support, `langchain.mcp` (design decision D18).

`langchain.mcp` runs on FastMCP's client, and so does cmcoder's `McpManager`
(mcp_client.py), which keeps doing what an agent library leaves to the
application: approving project servers, a log file per server, cmcoder's TLS
and proxy, timeouts, and one lasting connection per server.
`as_langchain_tool(tool, client)` turns each tool into a LangChain tool on
that connection, and `McpServerTool` gives it cmcoder's face:

- names `mcp__<server>__<tool>`, which permission rules match;
- the description and schema the classic engine sends (`McpTool.spec()`);
  LangChain drops the schema's `title` keys, as for cmcoder's own tools;
- results as text: `langchain.mcp` gives content blocks, the gateway takes
  a string (images and files are named, not sent);
- a tool's own failure (`isError`) stays an error result, as `langchain.mcp`
  makes it; a stopped server, a timeout or a broken connection become one too.

Not used yet: elicitation (a server asking the user for input in the middle
of a call, answered through a LangGraph `interrupt`) needs the checkpointer,
which comes in M5; until then cmcoder's clients don't offer it, so servers
don't ask. Tool-list caching (SEP-2549) is FastMCP's, on the client, for
servers on the 2026 protocol.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

from langchain_core._api import LangChainBetaWarning
from langchain_core.tools import BaseTool, ToolException
from pydantic import ConfigDict

from ..mcp_client import (
    ListMcpResourcesTool,
    McpManager,
    McpServer,
    McpTool,
    ReadMcpResourceTool,
    fastmcp_client,
)
from ..tools.base import ToolContext
from .tools import CmcoderTool


def _as_langchain_tool() -> Any:
    """`langchain.mcp.as_langchain_tool`, with FastMCP imported cmcoder's way
    first (mcp_client.fastmcp_client) and the module's beta warning quiet."""
    fastmcp_client()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LangChainBetaWarning)
        from langchain.mcp import as_langchain_tool
    return as_langchain_tool


class McpServerTool(BaseTool):
    """One MCP server tool, adapted by `langchain.mcp` (see the module)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    server: Any  # the McpServer whose connection runs it
    adapted: Any  # langchain.mcp's tool on that connection
    response_format: Any = "content"

    def __init__(self, server: McpServer, mcp_tool: Any, adapted: BaseTool) -> None:
        spec = McpTool(server, mcp_tool).spec()
        super().__init__(
            name=spec.name,
            description=spec.description,
            args_schema=spec.parameters,
            server=server,  # type: ignore[call-arg]
            adapted=adapted,  # type: ignore[call-arg]
            handle_tool_error=True,
        )

    def _run(self, *args: Any, **kwargs: Any) -> str:
        return asyncio.run(self._arun(*args, **kwargs))

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        kwargs.pop("run_manager", None)
        kwargs.pop("config", None)
        server: McpServer = self.server
        if server.status != "connected":
            raise ToolException(f"MCP server {server.name} is {server.status}.")
        # As a tool call, so the result says whether the tool failed (`status`).
        call = {"name": self.adapted.name, "args": kwargs, "id": "mcp", "type": "tool_call"}
        try:
            message = await asyncio.wait_for(self.adapted.ainvoke(call), server.config.tool_timeout)
        except TimeoutError as e:
            raise ToolException(
                f"{self.name} timed out after {server.config.tool_timeout:.0f}s."
            ) from e
        except Exception as e:  # the server's or the connection's problem, not the run's
            raise ToolException(f"{self.name} failed: {type(e).__name__}: {e}") from e
        text = blocks_text(message.content)
        if message.status == "error":
            raise ToolException(text)
        return text


def blocks_text(content: Any) -> str:
    """LangChain content blocks (or a string) as the text the model gets."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else [content]:
        if isinstance(block, str):
            parts.append(block)
            continue
        kind = block.get("type", "") if isinstance(block, dict) else ""
        if kind == "text":
            parts.append(str(block.get("text", "")))
        elif kind in ("image", "audio", "file"):
            mime = block.get("mime_type") or block.get("mimeType") or "?"
            parts.append(f"[{kind}: {mime}; not shown to the model]")
        else:
            parts.append(str(block))
    return "\n".join(parts)


async def mcp_tools(manager: McpManager, ctx: ToolContext | None = None) -> list[BaseTool]:
    """The connected servers' tools, and the resource tools when a server
    has resources. Start the manager first (`McpManager.start`); `ctx` as
    for `CmcoderTool` (inside the engine, the run's context serves)."""
    as_langchain_tool = _as_langchain_tool()
    out: list[BaseTool] = []
    for server in manager.connected():
        for mcp_tool in server.tools:
            adapted = await as_langchain_tool(mcp_tool, server.client)
            out.append(McpServerTool(server, mcp_tool, adapted))
    if any(s.resources for s in manager.connected()):
        out += [
            CmcoderTool(ListMcpResourcesTool(manager), ctx),
            CmcoderTool(ReadMcpResourceTool(manager), ctx),
        ]
    return out
