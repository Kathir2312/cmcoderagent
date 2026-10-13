"""Hooks (Phase 3 item 2): shell commands on agent events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cmcoder.config.settings import Settings, load_settings, set_project_trust
from cmcoder.core.agent import Agent, PermissionAnswer, PermissionRequest
from cmcoder.core.hooks import HookRunner
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.protocol import events as ev
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider


def hooks(**events: list[dict[str, Any]]) -> Settings:
    return Settings.model_validate({"hooks": events})


def on(command: str, matcher: str | None = None, timeout: float = 30) -> dict[str, Any]:
    entry: dict[str, Any] = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
    if matcher:
        entry["matcher"] = matcher
    return entry


def call(name: str, /, **arguments: Any) -> dict[str, Any]:
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


def same_dir(a: str | Path, b: str | Path) -> bool:
    return Path(a).resolve() == Path(b).resolve()


class Asker:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.asked: list[PermissionRequest] = []

    async def __call__(self, req: PermissionRequest) -> PermissionAnswer:
        self.asked.append(req)
        return PermissionAnswer(allow=self.allow)


def make_agent(
    server: Any,
    project: Path,
    settings: Settings,
    mode: str = "default",
    ask: Any = None,
    deny: list[str] | None = None,
) -> Agent:
    return Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy(mode, deny=deny or []),
        ToolContext(cwd=project, project_root=project),
        "test",
        ask=ask,
        hooks=HookRunner(settings, project),
    )


async def run(agent: Agent, prompt: str = "go") -> list[Any]:
    try:
        return [e async for e in agent.run(prompt)]
    finally:
        await agent.close()


def results(events: list[Any]) -> dict[str, ev.ToolResult]:
    return {e.name: e for e in events if isinstance(e, ev.ToolResult)}


def warnings(events: list[Any]) -> list[str]:
    return [e.message for e in events if isinstance(e, ev.Warning)]


BLOCK_RM = 'if grep -q "rm -rf"; then echo "no rm -rf in this repo" >&2; exit 2; fi'


async def test_pre_tool_use_blocks_with_a_reason(mock_server: Any, project: Path) -> None:
    (project / "a.txt").write_text("x\n")
    server = mock_server(
        [call("Bash", command="rm -rf build"), call("Read", file_path="a.txt"), {"content": "ok"}]
    )
    agent = make_agent(
        server, project, hooks(PreToolUse=[on(BLOCK_RM, "Bash")]), "bypassPermissions"
    )
    events = await run(agent)
    assert "no rm -rf in this repo" in results(events)["Bash"].content
    assert results(events)["Bash"].is_error and not results(events)["Read"].is_error
    sent = [m for m in server.requests[1]["messages"] if m["role"] == "tool"]
    assert "no rm -rf in this repo" in sent[-1]["content"]  # the model is told why


async def test_pre_tool_use_permission_decisions(mock_server: Any, project: Path) -> None:
    allow = 'echo \'{"hookSpecificOutput": {"permissionDecision": "allow"}}\''
    ask = 'echo \'{"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "check reads"}}\''
    (project / "a.txt").write_text("x\n")
    server = mock_server(
        [
            call("Write", file_path="new.txt", content="hi\n"),
            call("Read", file_path="a.txt"),
            {"content": "ok"},
        ]
    )
    asker = Asker()
    settings = hooks(PreToolUse=[on(allow, "Write"), on(ask, "Read")])
    events = await run(make_agent(server, project, settings, ask=asker))
    # Write normally asks; the hook allowed it. Read never asks; the hook made it ask.
    assert [r.tool_name for r in asker.asked] == ["Read"]
    assert asker.asked[0].reason == "check reads"
    assert (project / "new.txt").read_text() == "hi\n" and not results(events)["Read"].is_error


async def test_a_hook_never_overrides_deny_rules_or_high_risk(
    mock_server: Any, project: Path
) -> None:
    allow = 'echo \'{"hookSpecificOutput": {"permissionDecision": "allow"}}\''
    server = mock_server(
        [
            call("Write", file_path="x.txt", content="x"),
            call("Bash", command="git reset --hard"),
            {"content": "ok"},
        ]
    )
    asker = Asker(allow=False)
    agent = make_agent(server, project, hooks(PreToolUse=[on(allow)]), ask=asker, deny=["Write"])
    events = await run(agent)
    assert results(events)["Write"].is_error and not (project / "x.txt").exists()
    assert [r.tool_name for r in asker.asked] == ["Bash"]  # high-risk still asks


async def test_post_tool_use_feedback_reaches_the_model(mock_server: Any, project: Path) -> None:
    lint = 'echo "line 1: missing docstring" >&2; exit 2'
    server = mock_server([call("Write", file_path="m.py", content="x = 1\n"), {"content": "ok"}])
    events = await run(
        make_agent(server, project, hooks(PostToolUse=[on(lint, "Write|Edit")]), "acceptEdits")
    )
    content = results(events)["Write"].content
    assert content.startswith("Created m.py") and "missing docstring" in content


async def test_prompt_hooks_add_context_or_block(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "ok"}, {"content": "ok"}])
    settings = hooks(
        SessionStart=[on('echo "Today is a release freeze."')],
        UserPromptSubmit=[
            on(
                'if grep -q "password"; then echo "No secrets in prompts." >&2; exit 2; fi; echo "Ticket: ABC-1"'
            )
        ],
    )
    agent = make_agent(server, project, settings)
    try:
        first = [e async for e in agent.run("fix the bug")]
        blocked = [e async for e in agent.run("my password is hunter2")]
        [e async for e in agent.run("again")]
    finally:
        await agent.close()
    sent = server.requests[0]["messages"][1]["content"]
    assert "release freeze" in sent and "Ticket: ABC-1" in sent and sent.endswith("fix the bug")
    assert first[-1].result == "ok"
    assert blocked[-1].is_error and "No secrets in prompts." in blocked[-1].result
    assert len(server.requests) == 2  # the blocked prompt never reached the model
    assert all("hunter2" not in json.dumps(r) for r in server.requests)
    assert (
        "release freeze" not in server.requests[1]["messages"][-1]["content"]
    )  # SessionStart: once


async def test_stop_hook_sends_the_model_back_to_work(mock_server: Any, project: Path) -> None:
    once = 'if [ ! -f .stopped ]; then touch .stopped; echo "tests still fail" >&2; exit 2; fi'
    server = mock_server([{"content": "Done!"}, {"content": "Really done."}])
    events = await run(make_agent(server, project, hooks(Stop=[on(once)])))
    assert events[-1].result == "Really done."
    assert "tests still fail" in server.requests[1]["messages"][-1]["content"]


async def test_stop_hook_cannot_loop_forever(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": f"done {i}"} for i in range(6)])
    events = await run(make_agent(server, project, hooks(Stop=[on("exit 2")])))
    assert events[-1].result == "done 3" and len(server.requests) == 4


async def test_hook_input_and_failures(mock_server: Any, project: Path) -> None:
    server = mock_server([call("Read", file_path="nope.txt"), {"content": "ok"}])
    settings = hooks(
        PreToolUse=[
            on('cat > input.json; echo "$CMCODER_PROJECT_DIR" > dir.txt'),
            on("exit 1"),
            on("sleep 5", timeout=0.5),
        ]
    )
    events = await run(make_agent(server, project, settings, "bypassPermissions"))
    data = json.loads((project / "input.json").read_text())
    assert data["hook_event_name"] == "PreToolUse" and data["tool_name"] == "Read"
    assert data["tool_input"]["file_path"] == "nope.txt" and data["session_id"]
    assert same_dir((project / "dir.txt").read_text().strip(), project)
    assert any("failed (exit 1)" in w for w in warnings(events))
    assert any("timed out" in w for w in warnings(events))
    assert events[-1].result == "ok"  # failures don't stop the turn


async def test_notification_and_pre_compact(mock_server: Any, project: Path) -> None:
    server = mock_server([call("Write", file_path="x.txt", content="x"), {"content": "ok"}])
    settings = hooks(Notification=[on("cat > note.json")], PreCompact=[on("cat > compact.json")])
    agent = make_agent(server, project, settings, ask=Asker())
    try:
        [e async for e in agent.run("write")]
        [e async for e in agent.compact()]
    finally:
        await agent.close()
    assert "needs your permission to use Write(x.txt)" in (project / "note.json").read_text()
    assert json.loads((project / "compact.json").read_text())["trigger"] == "manual"


async def test_project_hooks_need_trust_and_approval(mock_server: Any, project: Path) -> None:
    (project / ".cmcoder").mkdir()
    (project / ".cmcoder" / "settings.json").write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [on("touch ran.txt")]}})
    )
    s = load_settings(project, environ={})
    assert s.project_hooks == {} and any(
        "hooks (UserPromptSubmit)" in x for x in s.ignored_project_settings
    )
    set_project_trust(project, True)
    s = load_settings(project, environ={})
    server = mock_server([{"content": "ok"}] * 3)

    asker = Asker(allow=False)
    await run(make_agent(server, project, s, ask=asker))
    assert asker.asked[0].tool_name == "Hook" and asker.asked[0].input["command"] == "touch ran.txt"
    assert not (project / "ran.txt").exists()

    asker = Asker(allow=True)
    await run(make_agent(server, project, s, ask=asker))
    assert (project / "ran.txt").exists()
    asker = Asker(allow=True)
    await run(make_agent(server, project, s, ask=asker))
    assert asker.asked == []  # remembered


def test_managed_hooks_only(project: Path) -> None:
    s = hooks(Stop=[on("echo user")])
    s.managed_hooks = hooks(Stop=[on("echo managed")]).hooks
    assert [h.origin for h in HookRunner(s, project).hooks] == ["managed", "user"]
    s.allow_managed_hooks_only = True
    assert [h.command.command for h in HookRunner(s, project).hooks] == ["echo managed"]


@pytest.mark.parametrize(
    ("matcher", "tool", "hit"),
    [
        ("Bash", "Bash", True),
        ("Edit|Write", "Write", True),
        ("Edit|Write", "Read", False),
        ("mcp__.*", "mcp__gh__x", True),
        (None, "Read", True),
    ],
)
def test_matchers(project: Path, matcher: str | None, tool: str, hit: bool) -> None:
    runner = HookRunner(hooks(PreToolUse=[on("true", matcher)]), project)
    assert runner.hooks[0].matches(tool) is hit
