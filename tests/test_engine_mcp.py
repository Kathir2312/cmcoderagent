"""engine/mcp.py: MCP servers' tools through langchain-mcp-adapters, on
cmcoder's own lasting session per server, with cmcoder's names, schema and
error handling (the Phase 8 spike, E5)."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from cmcoder.config.settings import Settings
from cmcoder.engine.mcp import blocks_text, mcp_tools
from cmcoder.mcp_client import McpManager, McpTool
from cmcoder.tools.base import ToolContext

from .test_engine_tools import inlined

SERVER = str(Path(__file__).parent / "mcp_servers" / "demo_server.py")


@pytest.fixture
async def manager(project: Path) -> AsyncIterator[McpManager]:
    settings = Settings.model_validate(
        {"mcpServers": {"demo": {"command": sys.executable, "args": [SERVER]}}}
    )
    m = McpManager(settings, project)
    warnings = [w async for w in m.start(None)]
    assert not warnings and m.connected()
    yield m
    await m.close()


def call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args, "id": "call_1", "type": "tool_call"}


async def test_names_and_schemas_are_cmcoders(manager: McpManager, project: Path) -> None:
    tools = {
        t.name: t for t in await mcp_tools(manager, ToolContext(cwd=project, project_root=project))
    }
    assert sorted(tools) == [
        "ListMcpResources",
        "ReadMcpResource",
        "mcp__demo__add",
        "mcp__demo__env",
        "mcp__demo__fail",
        "mcp__demo__shout",
    ]
    server = manager.connected()[0]
    for mcp_tool in server.tools:
        classic = McpTool(server, mcp_tool).spec()
        sent = convert_to_openai_tool(tools[classic.name])["function"]
        assert sent["description"] == classic.description
        assert sent["parameters"] == inlined(classic.parameters)


async def test_results_and_errors(manager: McpManager) -> None:
    tools = {t.name: t for t in await mcp_tools(manager)}
    ok = await tools["mcp__demo__add"].ainvoke(call("mcp__demo__add", a=2, b=3))
    assert (ok.content, ok.status) == ("5", "success")
    failed = await tools["mcp__demo__fail"].ainvoke(call("mcp__demo__fail", reason="test"))
    assert failed.status == "error" and "failed on purpose: test" in failed.content


async def test_calls_go_through_cmcoders_lasting_session(
    manager: McpManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapters' default would start the server again for every call."""
    session = manager.connected()[0].client.session
    real, calls = session.call_tool, []

    async def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[0])
        return await real(*args, **kwargs)

    monkeypatch.setattr(session, "call_tool", counted)
    tools = {t.name: t for t in await mcp_tools(manager)}
    for _ in range(3):
        await tools["mcp__demo__shout"].ainvoke({"text": "hi"})
    assert calls == ["shout"] * 3


async def test_a_stopped_server_is_an_error_result(manager: McpManager) -> None:
    tools = {t.name: t for t in await mcp_tools(manager)}
    manager.connected()[0].status = "disabled"
    out = await tools["mcp__demo__add"].ainvoke(call("mcp__demo__add", a=1, b=1))
    assert out.status == "error" and out.content == "MCP server demo is disabled."


async def test_in_a_graph(manager: McpManager, project: Path) -> None:
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode(await mcp_tools(manager)))
    graph.add_edge(START, "tools")
    out = await graph.compile().ainvoke(
        {"messages": [AIMessage(content="", tool_calls=[call("mcp__demo__shout", text="hello")])]}
    )
    assert out["messages"][-1].content == "HELLO"


def test_content_blocks_become_text() -> None:
    blocks = [
        {"type": "text", "text": "a chart:"},
        {"type": "image", "mime_type": "image/png", "base64": "..."},
    ]
    assert blocks_text(blocks) == "a chart:\n[image: image/png; not shown to the model]"
    assert blocks_text("plain") == "plain"
