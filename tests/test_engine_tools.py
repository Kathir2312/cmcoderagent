"""engine/tools.py: cmcoder's tools as LangChain tools, the same to the model
as in the classic engine (schema, error texts), run by LangGraph's tool node
in the run's working folder (its runtime context)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.utils.json_schema import dereference_refs
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from cmcoder.engine.context import EngineContext
from cmcoder.engine.tools import CmcoderTool, langchain_tools
from cmcoder.llm.factory import chat_model
from cmcoder.providers.profiles import resolve_profile
from cmcoder.providers.transport import TransportOptions
from cmcoder.tools.base import Tool, ToolContext, ToolInput, ToolResult
from cmcoder.tools.files import ReadTool
from cmcoder.tools.registry import default_tools

from .conftest import API_KEY


def call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args, "id": "call_1", "type": "tool_call"}


def without_titles(schema: Any, in_properties: bool = False) -> Any:
    """`title` keywords removed (a property *named* title stays)."""
    if isinstance(schema, dict):
        return {
            k: without_titles(v, k == "properties" and not in_properties)
            for k, v in schema.items()
            if in_properties or not (k == "title" and isinstance(v, str))
        }
    if isinstance(schema, list):
        return [without_titles(v) for v in schema]
    return schema


def inlined(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema as LangChain sends it: $defs/$ref inlined, no titles."""
    out = dereference_refs(schema)
    out.pop("$defs", None)
    return without_titles(out)


def test_the_model_sees_the_classic_schema(mock_server: Any) -> None:
    for tool in default_tools():
        spec = tool.spec()
        sent = convert_to_openai_tool(CmcoderTool(tool))["function"]
        assert (sent["name"], sent["description"], sent["parameters"]) == (
            spec.name,
            spec.description,
            inlined(spec.parameters),
        )
    server = mock_server([{"content": "ok"}])
    model = chat_model(
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        base_url=server.base_url,
        api_key=API_KEY,
        transport=TransportOptions(read_timeout=10),
        max_retries=0,
    )
    model.bind_tools(langchain_tools(default_tools())).invoke("hi")
    sent = {
        t["function"]["name"]: t["function"]["parameters"] for t in server.requests[-1]["tools"]
    }
    assert sent == {t.name: inlined(t.spec().parameters) for t in default_tools()}


async def test_results_errors_and_bad_arguments(project: Path) -> None:
    (project / "a.py").write_text("x = 1\n")
    read = CmcoderTool(ReadTool(), ToolContext(cwd=project, project_root=project))
    ok = await read.ainvoke(call("Read", file_path="a.py"))
    assert isinstance(ok, ToolMessage) and ok.status == "success"
    assert "x = 1" in ok.content and ok.tool_call_id == "call_1"
    assert ok.artifact == {"summary": "Read 1 lines"}
    bad = await read.ainvoke(call("Read"))
    assert bad.status == "error"
    assert bad.content == "Invalid arguments for Read: file_path: Field required"
    missing = await read.ainvoke(call("Read", file_path="nope.py"))
    assert missing.status == "error" and "nope.py" in str(missing.content)


class Echo(Tool):
    """A tool whose output isn't paged (Read pages its own)."""

    name = "Echo"
    description = "Repeat some text."

    class Input(ToolInput):
        text: str

    async def run(self, args: Any, ctx: ToolContext) -> ToolResult:
        return ToolResult(args.text * 1000)


async def test_output_is_cut_to_the_runs_limit(project: Path) -> None:
    ctx = ToolContext(cwd=project, project_root=project, max_output_chars=500)
    out = await CmcoderTool(Echo(), ctx).ainvoke(call("Echo", text="abc "))
    assert len(str(out.content)) < 700 and "characters truncated" in str(out.content)


async def test_in_a_graph_the_tool_works_in_the_runs_folder(project: Path) -> None:
    """LangGraph's runtime context: the same tools serve every run, each in
    its own folder; a tool without one says so."""
    (project / "a.py").write_text("x = 1\n")
    graph = StateGraph(MessagesState, context_schema=EngineContext)
    graph.add_node("tools", ToolNode(langchain_tools([ReadTool()])))  # no ToolContext of their own
    graph.add_edge(START, "tools")
    app = graph.compile()
    ask = {"messages": [AIMessage(content="", tool_calls=[call("Read", file_path="a.py")])]}
    out = await app.ainvoke(
        ask, context=EngineContext(ToolContext(cwd=project, project_root=project))
    )
    assert "x = 1" in out["messages"][-1].content
    lost = await app.ainvoke(ask)
    assert (
        lost["messages"][-1].status == "error"
        and "No working folder" in lost["messages"][-1].content
    )
