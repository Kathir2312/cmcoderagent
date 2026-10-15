"""MCP servers' tools for the Phase 8 engine, through langchain-mcp-adapters
(design decision D18).

cmcoder's `McpManager` keeps doing what the adapters don't (the Phase 8
spike, E5): one lasting session per server (the adapters' default starts a
server process for every call), approving project servers, a log file per
server, cmcoder's TLS and proxy, timeouts. The adapters turn each server's
tools into LangChain tools on that session (`load_mcp_tools(session)`), and
`McpServerTool` gives them cmcoder's face:

- names `mcp__<server>__<tool>`, which permission rules match (the adapters'
  own prefix is `<server>_<tool>`);
- the description and schema the classic engine sends (`McpTool.spec()`);
  LangChain drops the schema's `title` keys, as cmcoder's own tools do;
- results as text: the adapters give content blocks, the gateway takes a
  string (images and binary content are named, not sent);
- a stopped server, a timeout or an error from the tool: an error
  `ToolMessage` (the adapters would hand an error back as an ordinary
  result).
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import BaseTool, ToolException
from pydantic import ConfigDict

from ..mcp_client import ListMcpResourcesTool, McpManager, McpServer, McpTool, ReadMcpResourceTool
from ..tools.base import ToolContext
from .tools import CmcoderTool


class McpServerTool(BaseTool):
    """One MCP server tool, adapted by langchain-mcp-adapters (see the module)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    server: Any  # the McpServer whose session runs it
    adapted: Any  # the adapters' tool on that session
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
        try:
            content = await asyncio.wait_for(
                self.adapted.ainvoke(kwargs), server.config.tool_timeout
            )
        except TimeoutError as e:
            raise ToolException(
                f"{self.name} timed out after {server.config.tool_timeout:.0f}s."
            ) from e
        except ToolException as e:
            raise ToolException(blocks_text(e.args[0] if e.args else str(e))) from e
        except Exception as e:  # the server's problem, not the run's
            raise ToolException(f"{self.name} failed: {type(e).__name__}: {e}") from e
        return blocks_text(content)


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
    from langchain_mcp_adapters.tools import load_mcp_tools

    out: list[BaseTool] = []
    for server in manager.connected():
        adapted = {
            t.name: t
            for t in await load_mcp_tools(
                server.client.session, server_name=server.name, handle_tool_errors=False
            )
        }
        out += [
            McpServerTool(server, mcp_tool, adapted[mcp_tool.name])
            for mcp_tool in server.tools
            if mcp_tool.name in adapted
        ]
    if any(s.resources for s in manager.connected()):
        out += [
            CmcoderTool(ListMcpResourcesTool(manager), ctx),
            CmcoderTool(ReadMcpResourceTool(manager), ctx),
        ]
    return out
