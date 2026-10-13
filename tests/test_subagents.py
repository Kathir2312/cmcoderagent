"""Subagents (Phase 3 item 4): the Task tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cmcoder.config.settings import Settings, config_dir
from cmcoder.core.agent import Agent, PermissionAnswer, PermissionRequest
from cmcoder.core.hooks import HookRunner
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.core.subagents import EXPLORE_PROMPT, ModelChoice, SubagentRuntime, load_agents
from cmcoder.protocol import events as ev
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider


def call(name: str, /, **arguments: Any) -> dict[str, Any]:
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


def task(prompt: str, subagent_type: str = "general-purpose") -> dict[str, Any]:
    return call("Task", description="do a job", prompt=prompt, subagent_type=subagent_type)


class Asker:
    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.asked: list[PermissionRequest] = []

    async def __call__(self, req: PermissionRequest) -> PermissionAnswer:
        self.asked.append(req)
        return PermissionAnswer(allow=self.allow)


def make_agent(
    server: Any,
    project: Path,
    mode: str = "default",
    *,
    ask: Any = None,
    trusted: bool = False,
    hooks: Settings | None = None,
    small: bool = False,
) -> Agent:
    provider = make_provider(server)

    async def resolve(ref: str) -> ModelChoice | None:
        return ModelChoice(provider, "qwen-small", resolve_profile("qwen-small")) if small else None

    return Agent(
        provider,
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy(mode),
        ToolContext(cwd=project, project_root=project),
        "main prompt",
        ask=ask,
        hooks=HookRunner(hooks, project) if hooks else None,
        subagents=SubagentRuntime(project, trusted, resolve_model=resolve),
    )


async def run(agent: Agent, prompt: str = "go") -> list[Any]:
    try:
        return [e async for e in agent.run(prompt)]
    finally:
        await agent.close()


def tool_names(request: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in request.get("tools", [])}


async def test_explore_runs_in_its_own_conversation(mock_server: Any, project: Path) -> None:
    (project / "parser.py").write_text("def parse(): ...\n")
    server = mock_server(
        [
            task("Where is the parser?", "explore"),  # main
            call("Grep", pattern="def parse"),  # subagent
            {"content": "parse() is in parser.py:1."},  # subagent's report
            {"content": "It's in parser.py."},  # main
        ]
    )
    agent = make_agent(server, project, small=True)
    events = await run(agent, "find the parser")

    main, child, child2, main2 = server.requests
    assert "Task" in tool_names(main)
    assert tool_names(child) == {"Read", "Glob", "Grep"}  # read-only, no nesting
    assert child["model"] == "qwen-small" and main2["model"] == "qwen3-27b"
    assert child["messages"][0]["content"].startswith(EXPLORE_PROMPT[:40])
    assert [m["role"] for m in child["messages"]] == ["system", "user"]  # not the main chat
    assert child["messages"][1]["content"] == "Where is the parser?"

    task_id = next(e.id for e in events if isinstance(e, ev.ToolUse) and e.name == "Task")
    steps = [e for e in events if getattr(e, "parent_tool_use_id", None) == task_id]
    assert [type(e).__name__ for e in steps] == ["ToolUse", "ToolResult"]
    assert steps[0].label.startswith("Grep(")
    report = next(e for e in events if isinstance(e, ev.ToolResult) and e.name == "Task")
    assert (
        report.content == "parse() is in parser.py:1."
        and report.summary == "1 tool use · qwen-small"
    )
    assert main2["messages"][-1]["content"] == "parse() is in parser.py:1."
    assert events[-1].result == "It's in parser.py."
    assert agent.usage.prompt_tokens > 0


async def test_general_purpose_uses_the_main_permissions(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [task("Write a.txt"), call("Write", file_path="a.txt", content="x"), {"content": "never"}]
    )
    asker = Asker(allow=False)
    agent = make_agent(server, project, ask=asker)
    events = await run(agent)
    child = server.requests[1]
    assert "Bash" in tool_names(child) and not {"Task", "TodoWrite"} & tool_names(child)
    assert [r.tool_name for r in asker.asked] == ["Write"]  # asked through the main agent
    assert not (project / "a.txt").exists()
    report = next(e for e in events if isinstance(e, ev.ToolResult) and e.name == "Task")
    assert report.is_error and "denied" in report.content
    assert events[-1].subtype == "interrupted" and len(server.requests) == 2  # the turn stops


async def test_subagent_edits_can_be_rewound(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            task("Write a.txt"),
            call("Write", file_path="a.txt", content="x"),
            {"content": "Wrote it."},
            {"content": "ok"},
        ]
    )
    agent = make_agent(server, project, "acceptEdits")
    await run(agent)
    assert (project / "a.txt").read_text() == "x"
    assert [Path(e.path).name for e in agent.checkpoints.entries if e.turn == 1] == ["a.txt"]


async def test_custom_agents_and_trust(mock_server: Any, project: Path) -> None:
    agents = project / ".cmcoder" / "agents"
    agents.mkdir(parents=True)
    (agents / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews code\ntools: Read\n---\nYou review code."
    )
    mine = config_dir() / "agents"
    mine.mkdir(parents=True)
    (mine / "notes.md").write_text("---\ndescription: Takes notes\nmodel: small\n---\nTake notes.")
    (mine / "explore.md").write_text("---\ndescription: can't replace a built-in\n---\nNo.")

    assert set(load_agents(project, project_trusted=False)) == {
        "general-purpose",
        "explore",
        "notes",
    }
    found = load_agents(project, project_trusted=True)
    assert found["reviewer"].tools == ["Read"] and found["reviewer"].origin == "project"
    assert found["explore"].origin == "built-in"

    server = mock_server([task("Review x", "reviewer"), {"content": "LGTM"}, {"content": "ok"}])
    events = await run(make_agent(server, project, trusted=True))
    assert tool_names(server.requests[1]) == {"Read"}
    assert server.requests[1]["messages"][0]["content"] == "You review code."
    assert "reviewer: Reviews code" in next(
        t["function"]["description"]
        for t in server.requests[0]["tools"]
        if t["function"]["name"] == "Task"
    )
    assert events[-1].result == "ok"

    server = mock_server([task("Review x", "reviewer"), {"content": "ok"}])
    events = await run(make_agent(server, project, trusted=False))
    report = next(e for e in events if isinstance(e, ev.ToolResult) and e.name == "Task")
    assert report.is_error and "Unknown subagent_type 'reviewer'" in report.content


async def test_subagent_stop_hook(mock_server: Any, project: Path) -> None:
    hooks = Settings.model_validate(
        {
            "hooks": {
                "SubagentStop": [
                    {"hooks": [{"type": "command", "command": "echo sub >> hooks.log"}]}
                ],
                "Stop": [{"hooks": [{"type": "command", "command": "echo main >> hooks.log"}]}],
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "echo prompt >> hooks.log"}]}
                ],
            }
        }
    )
    server = mock_server([task("Say hi"), {"content": "hi"}, {"content": "ok"}])
    await run(make_agent(server, project, hooks=hooks))
    assert (project / "hooks.log").read_text().split() == ["prompt", "sub", "main"]
