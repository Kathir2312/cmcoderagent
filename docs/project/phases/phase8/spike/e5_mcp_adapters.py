"""E5: langchain-mcp-adapters with a real stdio MCP server: tool names, a
new server process per call (the default) or one lasting session.

The server counts its own starts in a file next to it."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from common import check, finding
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

work = Path(tempfile.mkdtemp())
starts = work / "starts.txt"
server = work / "server.py"
server.write_text(
    f"""
from pathlib import Path
from mcp.server.fastmcp import FastMCP

with open({str(starts)!r}, "a") as f:
    f.write("start\\n")
mcp = FastMCP("tickets")

@mcp.tool()
def find_ticket(key: str) -> str:
    \"\"\"Find a ticket by its key.\"\"\"
    return f"{{key}}: login fails on Windows"

mcp.run()
"""
)
conn = {"tickets": {"transport": "stdio", "command": sys.executable, "args": [str(server)]}}


async def main() -> None:
    client = MultiServerMCPClient(conn, tool_name_prefix=True)
    tools = await client.get_tools()
    finding("tool names with tool_name_prefix=True", ", ".join(t.name for t in tools))
    before = starts.read_text().count("start") if starts.exists() else 0
    for _ in range(3):
        await tools[0].ainvoke({"key": "BUG-1"})
    per_call = starts.read_text().count("start") - before
    finding("default: server processes started for 3 calls", str(per_call))

    async with client.session("tickets") as session:
        lasting = await load_mcp_tools(session)
        before = starts.read_text().count("start")
        results = [await lasting[0].ainvoke({"key": "BUG-1"}) for _ in range(3)]
        started = starts.read_text().count("start") - before
    check("lasting session: no new process for 3 calls", started == 0, f"result: {results[0]!r}")


asyncio.run(main())
