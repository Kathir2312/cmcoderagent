"""Lab M3: cmcoder's loop built by hand as a LangGraph StateGraph, from
cmcoder's real parts (the M1 chat model, the M2 tools), against the mock
gateway. M4 builds the engine with `create_agent`, which makes this graph
for you; here you see every piece.

    uv run python docs/learn/phase8/labs/m3_graph.py

Read the output next to docs/learn/phase8/m3-graph-by-hand.md. The graph
has no permission checks, so it gets read-only tools only."""

from __future__ import annotations

import asyncio
import operator
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from cmcoder.engine.context import EngineContext
from cmcoder.engine.tools import langchain_tools
from cmcoder.llm.factory import chat_model
from cmcoder.providers.profiles import resolve_profile
from cmcoder.testing.mock_server import MockServer, MockState
from cmcoder.tools.base import ToolContext
from cmcoder.tools.files import ReadTool
from cmcoder.tools.search import GlobTool, GrepTool

SYSTEM = "You are cmcoder, a coding assistant. Use the tools to look at the project."


class State(MessagesState):
    """The graph's state: the conversation (MessagesState's `messages`, whose
    reducer appends), plus two keys of our own."""

    steps: int  # model calls so far: no reducer, so an update replaces it
    files_read: Annotated[list[str], operator.add]  # a reducer: each update is appended


def build_graph(
    model: Any, tools: list[BaseTool], max_steps: int = 10, checkpointer: Any = None
) -> Any:
    bound = model.bind_tools(tools)

    async def call_model(state: State) -> dict[str, Any]:
        reply = await bound.ainvoke([SystemMessage(SYSTEM), *state["messages"]])
        asked = [c["args"].get("file_path", "") for c in reply.tool_calls if c["name"] == "Read"]
        return {"messages": [reply], "steps": state.get("steps", 0) + 1, "files_read": asked}

    def route(state: State) -> Literal["tools", "__end__"]:
        """The conditional edge: what `tools_condition` does, plus cmcoder's
        own limit on model calls (maxTurns)."""
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return END
        if state["steps"] >= max_steps:
            return END
        return "tools"

    graph = StateGraph(State, context_schema=EngineContext)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route, ["tools", END])
    graph.add_edge("tools", "model")
    return graph.compile(checkpointer=checkpointer)


def read_tools() -> list[BaseTool]:
    return list(langchain_tools([ReadTool(), GlobTool(), GrepTool()]))


def section(title: str) -> None:
    print(f"\n== {title}")


def read(path: str) -> dict[str, Any]:
    return {"tool_calls": [{"name": "Read", "arguments": {"file_path": path}}]}


async def main() -> None:
    project = Path(tempfile.mkdtemp())
    (project / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (project / "test_app.py").write_text("from app import add\nassert add(2, 2) == 4\n")
    context = EngineContext(ToolContext(cwd=project, project_root=project))
    script = [
        read("app.py"),
        read("test_app.py"),
        {"content": "add() subtracts, so test_app.py fails: it expects 4."},
        {"content": "Change `a - b` to `a + b` in app.py."},
    ]

    with MockServer(MockState(script)) as server:
        model = chat_model(
            "qwen3-27b",
            resolve_profile("qwen3-27b"),
            base_url=server.base_url,
            api_key="sk-lab",
            max_retries=0,
        )
        app = build_graph(model, read_tools(), checkpointer=InMemorySaver())
        thread = {"configurable": {"thread_id": "lab"}}

        section("1. The graph (Mermaid; paste it into https://mermaid.live)")
        print(app.get_graph().draw_mermaid())

        section('2. stream_mode="updates": what each node wrote, step by step')
        question = {"messages": [("user", "Why does the test fail?")]}
        async for update in app.astream(question, thread, context=context, stream_mode="updates"):
            for node, wrote in update.items():
                last = wrote["messages"][-1]
                said = (
                    f"tool calls {[c['name'] + '(' + c['args'].get('file_path', '') + ')' for c in last.tool_calls]}"
                    if getattr(last, "tool_calls", None)
                    else repr(str(last.content)[:60])
                )
                print(f"{node:6} -> {said}")

        section("3. The state after the turn: reducers at work")
        state = (await app.aget_state(thread)).values
        print("steps:", state["steps"], "(replaced each time)")
        print("files_read:", state["files_read"], "(appended by the operator.add reducer)")
        print("messages:", [type(m).__name__ for m in state["messages"]])

        section(
            '4. A second turn in the same thread (the checkpointer\'s memory); stream_mode="messages"'
        )
        print("tokens: ", end="")
        follow_up = {"messages": [("user", "How do I fix it?")]}
        async for chunk, meta in app.astream(
            follow_up, thread, context=context, stream_mode="messages"
        ):
            if meta.get("langgraph_node") == "model" and chunk.content:
                print(f"[{chunk.content}]", end="")
        print()
        state = (await app.aget_state(thread)).values
        print("messages now:", len(state["messages"]), "- the first turn's are still there")

    section(
        "5. A model that never stops calling tools: the recursion limit, and cmcoder's own limit"
    )
    with MockServer(MockState([read("app.py")] * 50)) as server:
        model = chat_model(
            "qwen3-27b",
            resolve_profile("qwen3-27b"),
            base_url=server.base_url,
            api_key="sk-lab",
            max_retries=0,
        )
        try:
            await build_graph(model, read_tools(), max_steps=100).ainvoke(
                {"messages": [("user", "go")]}, {"recursion_limit": 6}, context=context
            )
        except GraphRecursionError as e:
            print("recursion_limit=6:", type(e).__name__, "-", str(e).split(".")[0])
        out = await build_graph(model, read_tools(), max_steps=3).ainvoke(
            {"messages": [("user", "go")]}, context=context
        )
        print("max_steps=3: stopped cleanly after", out["steps"], "model calls")


if __name__ == "__main__":
    asyncio.run(main())
