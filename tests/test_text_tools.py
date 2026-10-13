from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cmcoder.core.agent import Agent, parse_tool_arguments
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.protocol import events as ev
from cmcoder.providers.messages import Message, ToolCall, ToolSpec
from cmcoder.providers.profiles import resolve_profile
from cmcoder.providers.text_tools import Holdback, extract, to_prompted_wire
from cmcoder.tools.base import ToolContext
from cmcoder.tools.files import EditInput, EditTool, ReadInput, ReadTool, closest_match
from cmcoder.tools.registry import default_tools

from .conftest import make_provider

TOOLS = {"Read", "Grep"}


def block(name: str, args: Any) -> str:
    return f"<tool_call>\n{json.dumps({'name': name, 'arguments': args})}\n</tool_call>"


# --- parsing ---------------------------------------------------------------------------


def test_extract_calls_from_text() -> None:
    text = (
        "I'll look.\n"
        + block("Read", {"file_path": "a.py"})
        + "\n"
        + block("Grep", {"pattern": "x"})
    )
    rest, calls = extract(text, TOOLS)
    assert rest == "I'll look."
    assert [(c.name, json.loads(c.arguments)) for c in calls] == [
        ("Read", {"file_path": "a.py"}),
        ("Grep", {"pattern": "x"}),
    ]
    assert calls[0].id != calls[1].id


def test_extract_leaves_other_text_alone() -> None:
    prose = "Qwen writes <tool_call> tags when the server has no parser."
    assert extract(prose, TOOLS) == (prose, [])
    unknown = block("Delete", {"all": True})
    assert extract(unknown, TOOLS) == (unknown, [])
    broken = "<tool_call>{not json</tool_call>"
    assert extract(broken, TOOLS) == (broken, [])


def test_extract_tolerates_model_quirks() -> None:
    # no closing tag (the model stopped at its stop token), "parameters", a trailing comma
    _, calls = extract('<tool_call>\n{"name": "Read", "parameters": {"file_path": "a",},}', TOOLS)
    assert calls and json.loads(calls[0].arguments) == {"file_path": "a"}
    # arguments as a JSON string
    _, calls = extract(block("Read", '{"file_path": "b"}'), TOOLS)
    assert json.loads(calls[0].arguments) == {"file_path": "b"}


def test_holdback_hides_tool_call_text() -> None:
    h = Holdback()
    shown = "".join(h.feed(c) for c in ["Look", "ing <to", 'ol_call>\n{"na', 'me": 1}'])
    assert shown == "Looking " and h.flush() == ""
    h = Holdback()
    shown = "".join(h.feed(c) for c in ["a < b", " and <t"]) + h.flush()
    assert shown == "a < b and <t"  # not a tag after all: nothing lost


def test_python_style_arguments_are_repaired() -> None:
    assert parse_tool_arguments("{'file_path': 'a.py', 'replace_all': True}") == (
        {"file_path": "a.py", "replace_all": True},
        None,
    )


# --- prompted mode ------------------------------------------------------------------------


def test_prompted_wire_format() -> None:
    spec = ToolSpec("Read", "Read a file.", {"type": "object", "properties": {}})
    msgs = [
        Message.system("You are cmcoder."),
        Message.user("read both"),
        Message(
            role="assistant",
            content="Reading.",
            tool_calls=[ToolCall("1", "Read", '{"file_path": "a"}'), ToolCall("2", "Read", "{}")],
        ),
        Message.tool_result("1", "Read", "AAA"),
        Message.tool_result("2", "Read", "BBB"),
    ]
    wire = to_prompted_wire(msgs, [spec])
    assert [m["role"] for m in wire] == ["system", "user", "assistant", "user"]
    assert "<tools>" in wire[0]["content"] and '"name": "Read"' in wire[0]["content"]
    assert wire[2]["content"].count("<tool_call>") == 2
    assert wire[3]["content"] == (
        "<tool_response>\nAAA\n</tool_response>\n<tool_response>\nBBB\n</tool_response>"
    )


def make_agent(server: Any, project: Path, prompted: bool = False) -> Agent:
    profile = resolve_profile(
        "qwen3-27b", overrides=[{"toolCalling": "prompted"}] if prompted else None
    )
    return Agent(
        make_provider(server),
        "qwen3-27b",
        profile,
        default_tools(),
        PermissionPolicy("bypassPermissions"),
        ToolContext(cwd=project, project_root=project),
        "test",
    )


async def run(agent: Agent, prompt: str) -> list[ev.Event]:
    try:
        return [e async for e in agent.run(prompt)]
    finally:
        await agent.close()


async def test_text_tool_calls_run_in_native_mode(mock_server: Any, project: Path) -> None:
    """A backend without a tool parser: Qwen's <tool_call> text still works."""
    (project / "a.txt").write_text("hello\n")
    server = mock_server(
        [
            {"content": "Let me read it.\n" + block("Read", {"file_path": "a.txt"})},
            {"content": "It says hello."},
        ]
    )
    agent = make_agent(server, project)
    events = await run(agent, "what's in a.txt?")
    shown = "".join(e.text for e in events if isinstance(e, ev.AssistantDelta))
    assert "<tool_call>" not in shown and "Let me read it." in shown
    results = [e for e in events if isinstance(e, ev.ToolResult)]
    assert results and "hello" in results[0].content
    assert agent.messages[2].tool_calls[0].name == "Read"
    assert isinstance(events[-1], ev.Result) and events[-1].result == "It says hello."


async def test_prompted_mode_end_to_end(mock_server: Any, project: Path) -> None:
    (project / "a.txt").write_text("hello\n")
    server = mock_server(
        [
            {"content": block("Read", {"file_path": "a.txt"})},
            {"content": "It says hello."},
        ]
    )
    events = await run(make_agent(server, project, prompted=True), "what's in a.txt?")
    first, second = server.requests
    assert "tools" not in first and "<tools>" in first["messages"][0]["content"]
    assert "<tool_response>" in second["messages"][-1]["content"]
    assert "hello" in second["messages"][-1]["content"]
    assert isinstance(events[-1], ev.Result) and events[-1].subtype == "success"


async def test_switches_to_prompted_when_tools_are_rejected(
    mock_server: Any, project: Path
) -> None:
    """vLLM without --enable-auto-tool-choice rejects the tools parameter."""
    server = mock_server(
        [
            {
                "error": {
                    "status": 400,
                    "message": '"auto" tool choice requires --enable-auto-tool-choice and '
                    "--tool-call-parser to be set",
                }
            },
            {"content": "Hello!"},
        ]
    )
    agent = make_agent(server, project)
    events = await run(agent, "hi")
    warnings = [e.message for e in events if isinstance(e, ev.Warning)]
    assert any("switching to prompted tool calls" in w for w in warnings)
    assert "tools" in server.requests[0] and "tools" not in server.requests[1]
    assert agent.profile.tool_calling == "prompted"
    assert isinstance(events[-1], ev.Result) and events[-1].result == "Hello!"


# --- Edit hints ---------------------------------------------------------------------------


def test_closest_match() -> None:
    text = "def a():\n    return 1\n\n\ndef total(items):\n    return sum(i.price for i in items)\n"
    assert closest_match(text, "def total(item):\n  return sum(i.price for i in item)") == (
        5,
        "def total(items):\n    return sum(i.price for i in items)",
    )
    assert closest_match(text, "something else entirely, nothing alike") is None


async def test_edit_not_found_shows_the_closest_lines(ctx: ToolContext, project: Path) -> None:
    (project / "m.py").write_text("x = 1\n\ndef total(items):\n    return sum(items)\n")
    await ReadTool().run(ReadInput(file_path="m.py"), ctx)
    res = await EditTool().run(
        EditInput(file_path="m.py", old_string="def total(item):", new_string="def total(xs):"),
        ctx,
    )
    assert res.is_error and "closest text is at line 3" in res.content
    assert "def total(items):" in res.content
