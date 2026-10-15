"""Lab M2: cmcoder's tools and an MCP server's tools as LangChain tools,
run by LangGraph's ToolNode (no model needed).

    uv run python docs/learn/phase8/labs/m2_tools.py

Read the output next to docs/learn/phase8/m2-tools-and-mcp.md."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from cmcoder.config.settings import Settings
from cmcoder.engine.context import EngineContext
from cmcoder.engine.mcp import mcp_tools
from cmcoder.engine.tools import CmcoderTool, langchain_tools
from cmcoder.mcp_client import McpManager
from cmcoder.tools.base import ToolContext
from cmcoder.tools.files import ReadTool

DEMO_SERVER = Path(__file__).resolve().parents[4] / "tests" / "mcp_servers" / "demo_server.py"


def section(title: str) -> None:
    print(f"\n== {title}")


def tool_call(name: str, **args: object) -> dict[str, object]:
    return {"name": name, "args": args, "id": "call_1", "type": "tool_call"}


async def main() -> None:
    a, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    (a / "app.py").write_text("print('project A')\n")
    (b / "app.py").write_text("print('project B')\n")

    section("1. cmcoder's Read tool as a LangChain tool: what the model is sent")
    read = CmcoderTool(ReadTool(), ToolContext(cwd=a, project_root=a))
    print(json.dumps(convert_to_openai_tool(read), indent=1)[:500], "...")

    section("2. Running it with a tool call: a ToolMessage")
    for args in ({"file_path": "app.py"}, {}, {"file_path": "missing.py"}):
        msg = await read.ainvoke(tool_call("Read", **args))
        print(
            f"args={args!s:24} status={msg.status:7} artifact={msg.artifact}  content={msg.content[:50]!r}"
        )

    section("3. In a graph: the same tool objects, each run in its own folder (runtime context)")
    graph = StateGraph(MessagesState, context_schema=EngineContext)
    graph.add_node("tools", ToolNode(langchain_tools([ReadTool()])))
    graph.add_edge(START, "tools")
    app = graph.compile()
    ask = {"messages": [AIMessage(content="", tool_calls=[tool_call("Read", file_path="app.py")])]}
    for folder in (a, b):
        out = await app.ainvoke(
            ask, context=EngineContext(ToolContext(cwd=folder, project_root=folder))
        )
        print(f"{folder.name}: {out['messages'][-1].content.strip()!r}")

    section("4. An MCP server's tools through langchain-mcp-adapters, on cmcoder's session")
    settings = Settings.model_validate(
        {"mcpServers": {"demo": {"command": sys.executable, "args": [str(DEMO_SERVER)]}}}
    )
    manager = McpManager(settings, a)
    [w async for w in manager.start(None)]
    try:
        tools = await mcp_tools(manager, ToolContext(cwd=a, project_root=a))
        print("tools:", ", ".join(t.name for t in tools))
        by_name = {t.name: t for t in tools}
        for name, args in (
            ("mcp__demo__shout", {"text": "hello"}),
            ("mcp__demo__fail", {"reason": "a demo"}),
        ):
            msg = await by_name[name].ainvoke(tool_call(name, **args))
            print(f"{name}: status={msg.status} content={msg.content!r}")
    finally:
        await manager.close()


asyncio.run(main())
