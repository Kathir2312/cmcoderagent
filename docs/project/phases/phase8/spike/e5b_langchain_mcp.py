"""E5b (16 October, decision 8 revisited): LangChain's own MCP support,
`langchain.mcp`, on FastMCP 4's client and the `mcp` library 2.x, with a
real stdio MCP server: server processes per call, error results, the stdio
transport's log file, and what importing FastMCP reads from the current
folder. The server counts its own starts in a file next to it.

It needs FastMCP, which cmcoder itself now depends on, so it runs in the
project's environment (not the spike's requirements.txt):

    uv run python docs/project/phases/phase8/spike/e5b_langchain_mcp.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import warnings
from pathlib import Path

from common import check, finding

work = Path(tempfile.mkdtemp())
starts = work / "starts.txt"
server = work / "server.py"
server.write_text(
    f"""
from mcp.server.mcpserver import MCPServer

with open({str(starts)!r}, "a") as f:
    f.write("start\\n")
mcp = MCPServer("tickets")

@mcp.tool()
def find_ticket(key: str) -> str:
    \"\"\"Find a ticket by its key.\"\"\"
    return f"{{key}}: login fails on Windows"

@mcp.tool()
def close_ticket(key: str) -> str:
    \"\"\"Close a ticket.\"\"\"
    raise ValueError("closed tickets can't be closed again")

mcp.run("stdio")
"""
)
(work / ".env").write_text("FASTMCP_LOG_LEVEL=DEBUG\n")
os.chdir(work)  # FastMCP reads `.env` from the current folder when it's imported

import fastmcp  # noqa: E402  (after the chdir, on purpose)

finding("FastMCP's settings, imported in a folder with a .env", f"log_level={fastmcp.settings.log_level}")

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from langchain.mcp import as_langchain_tool
finding("importing langchain.mcp", "; ".join(str(w.message) for w in caught) or "no warning")


def started() -> int:
    return starts.read_text().count("start") if starts.exists() else 0


def transport(**kw: object) -> StdioTransport:
    return StdioTransport(sys.executable, [str(server)], log_file=work / "server.log", **kw)  # type: ignore[arg-type]


async def main() -> None:
    client = Client(transport(keep_alive=False))
    async with client:  # held open for the session, as cmcoder's McpManager does
        tools = {t.name: await as_langchain_tool(t, client) for t in await client.list_tools()}
        finding("tool names", ", ".join(tools))
        before = started()
        results = [await tools["find_ticket"].ainvoke({"key": "BUG-1"}) for _ in range(3)]
        check("held open: no new process for 3 calls", started() == before, f"result: {results[0]!r}")
        bad = await tools["close_ticket"].ainvoke(
            {"name": "close_ticket", "args": {"key": "BUG-1"}, "id": "c1", "type": "tool_call"}
        )
        check("a tool's failure is an error result", bad.status == "error", repr(bad.content)[:80])

    loose = Client(transport(keep_alive=False))
    async with loose:
        tool = await as_langchain_tool((await loose.list_tools())[0], loose)
    before = started()
    for _ in range(3):  # not held open, and not kept alive: each call connects
        await tool.ainvoke({"key": "BUG-1"})
    finding("not held open, keep_alive=False: processes for 3 calls", str(started() - before))
    check("the stdio transport writes the server's stderr to its log file", (work / "server.log").exists())


asyncio.run(main())
