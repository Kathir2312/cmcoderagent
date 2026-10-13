from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cmcoder.core.agent import Agent, PermissionAnswer, PermissionRequest, last_todos
from cmcoder.core.permissions import Decision, PermissionPolicy
from cmcoder.core.sessions import SessionLog, load
from cmcoder.protocol import events as ev
from cmcoder.providers.messages import Message, ToolCall
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools
from cmcoder.tools.todo import TodoInput, TodoTool, format_todos

from .conftest import make_provider

PLAN = [
    {"content": "Read the code", "status": "completed"},
    {"content": "Fix the bug", "status": "in_progress", "activeForm": "Fixing the bug"},
    {"content": "Run the tests", "status": "pending"},
]


async def test_todo_tool(ctx: ToolContext) -> None:
    res = await TodoTool().run(TodoInput.model_validate({"todos": PLAN}), ctx)
    assert ctx.todos[1] == PLAN[1]
    assert "1/3 done" in res.content and "☑ Read the code" in res.content
    assert res.summary == "1/3 done · now: Fixing the bug"
    two_active = [dict(PLAN[1]), dict(PLAN[1], content="Other")]
    res = await TodoTool().run(TodoInput.model_validate({"todos": two_active}), ctx)
    assert "only one item in_progress" in res.content


def test_todo_validation() -> None:
    with pytest.raises(ValidationError):
        TodoInput.model_validate({"todos": [{"content": "x", "status": "done"}]})
    with pytest.raises(ValidationError):
        TodoInput.model_validate({"todos": [{"content": "", "status": "pending"}]})


def test_todo_needs_no_permission(ctx: ToolContext) -> None:
    args = TodoInput.model_validate({"todos": PLAN})
    for mode in ("default", "plan"):
        assert PermissionPolicy(mode).check(TodoTool(), args, ctx).decision == Decision.ALLOW


def test_format() -> None:
    assert format_todos(PLAN) == "☑ Read the code\n► Fix the bug\n☐ Run the tests"


async def test_todos_survive_compaction_and_resume(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {"tool_calls": [{"name": "TodoWrite", "arguments": {"todos": PLAN}}]},
            {"content": "Planned."},
        ]
    )
    asked: list[PermissionRequest] = []

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=False)

    agent = Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("default"),
        ToolContext(cwd=project, project_root=project),
        "test",
        ask=ask,
        session=SessionLog(project),
    )
    try:
        events = [e async for e in agent.run("fix the bug")]
        assert not asked  # no permission prompt for TodoWrite
        uses = [e for e in events if isinstance(e, ev.ToolUse)]
        assert uses[0].label == "TodoWrite(1/3 done)"
        assert agent.ctx.todos == PLAN

        [e async for e in agent.compact()]
        summary = agent.messages[1].content
        assert "current todo list" in summary and "► Fix the bug" in summary

        messages, _ = load(agent.session.path)  # type: ignore[union-attr]
    finally:
        await agent.close()
    # The TodoWrite call itself was summarised away; the summary carries the list.
    assert last_todos(messages) == []


def test_resume_restores_the_last_todo_list(project: Path) -> None:
    call = ToolCall("t1", "TodoWrite", json.dumps({"todos": PLAN}))
    later = ToolCall("t2", "TodoWrite", json.dumps({"todos": PLAN[:1]}))
    messages = [
        Message.user("fix it"),
        Message(role="assistant", tool_calls=[call]),
        Message.tool_result("t1", "TodoWrite", "ok"),
        Message(role="assistant", tool_calls=[later]),
        Message.tool_result("t2", "TodoWrite", "ok"),
    ]
    agent = Agent(
        None,  # type: ignore[arg-type]
        "m",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy(),
        ToolContext(cwd=project, project_root=project),
        "test",
    )
    agent.resume(messages)
    assert agent.ctx.todos == PLAN[:1]  # the latest list wins


def reads(n: int) -> list[dict[str, Any]]:
    return [
        {"tool_calls": [{"name": "Glob", "arguments": {"pattern": f"*{i}.py"}}]} for i in range(n)
    ]


def make_agent(server: Any, project: Path) -> Agent:
    return Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("bypassPermissions"),
        ToolContext(cwd=project, project_root=project),
        "test",
    )


def reminders(request: dict[str, Any]) -> int:
    return sum(
        "Use the TodoWrite tool now" in (m.get("content") or "")
        for m in request["messages"]
        if m["role"] == "tool"
    )


async def test_reminder_after_several_calls_without_todos(mock_server: Any, project: Path) -> None:
    """Hands-on test on Windows: Qwen never called TodoWrite on its own, so
    the checklist never appeared. After 3 tool calls it is reminded once."""
    server = mock_server([*reads(5), {"content": "Done."}])
    agent = make_agent(server, project)
    try:
        [e async for e in agent.run("refactor everything")]
    finally:
        await agent.close()
    assert [reminders(r) for r in server.requests] == [0, 0, 0, 1, 1, 1]


async def test_no_reminder_when_todos_are_used(mock_server: Any, project: Path) -> None:
    todo = {"tool_calls": [{"name": "TodoWrite", "arguments": {"todos": PLAN}}]}
    server = mock_server([todo, *reads(4), {"content": "Done."}])
    agent = make_agent(server, project)
    try:
        [e async for e in agent.run("fix the bug")]
    finally:
        await agent.close()
    assert all(reminders(r) == 0 for r in server.requests)


async def test_no_reminder_for_short_tasks(mock_server: Any, project: Path) -> None:
    server = mock_server([*reads(2), {"content": "Done."}, *reads(2), {"content": "Done."}])
    agent = make_agent(server, project)
    try:
        [e async for e in agent.run("look")]
        [e async for e in agent.run("look again")]  # the count starts over each turn
    finally:
        await agent.close()
    assert all(reminders(r) == 0 for r in server.requests)
