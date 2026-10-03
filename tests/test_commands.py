"""Custom slash commands (Phase 3 item 3): Markdown files and MCP prompts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from cmcoder.config.settings import Settings, config_dir
from cmcoder.core.agent import Agent, PermissionAnswer, PermissionRequest
from cmcoder.core.commands import CommandSource, load_commands, parse_file, split_tools, substitute
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.mcp_client import McpManager
from cmcoder.protocol import events as ev
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider
from .test_stdio import Agent as StdioAgent

SERVER = str(Path(__file__).parent / "mcp_servers" / "demo_server.py")

REVIEW = """\
---
description: Review a file for bugs
argument-hint: <file>
allowed-tools: Read, Bash(echo checked:*)
---
Review $1 for bugs. Notes: $ARGUMENTS
"""


def command(folder: Path, name: str, text: str) -> None:
    path = folder / (name.replace(":", "/") + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def project_commands(project: Path) -> Path:
    return project / ".cmcoder" / "commands"


def user_commands() -> Path:
    return config_dir() / "commands"


class Asker:
    def __init__(self) -> None:
        self.asked: list[PermissionRequest] = []

    async def __call__(self, req: PermissionRequest) -> PermissionAnswer:
        self.asked.append(req)
        return PermissionAnswer(allow=False)


def make_agent(
    server: Any,
    project: Path,
    *,
    trusted: bool = True,
    ask: Any = None,
    mcp: McpManager | None = None,
    locked: bool = False,
) -> Agent:
    return Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("default", allow_rules_locked=locked),
        ToolContext(cwd=project, project_root=project),
        "test",
        ask=ask,
        mcp=mcp,
        commands=CommandSource(project, trusted),
    )


def bash(command: str) -> dict[str, Any]:
    return {"tool_calls": [{"name": "Bash", "arguments": {"command": command}}]}


def test_files_frontmatter_and_arguments() -> None:
    meta, body = parse_file(REVIEW)
    assert meta["description"] == "Review a file for bugs" and meta["argument-hint"] == "<file>"
    assert split_tools(meta["allowed-tools"]) == ["Read", "Bash(echo checked:*)"]
    assert split_tools("Bash(a, b), Read") == ["Bash(a, b)", "Read"]
    assert substitute(body, '"my file.py" carefully') == (
        'Review my file.py for bugs. Notes: "my file.py" carefully'
    )
    assert substitute("Fix the tests.", "in api/") == "Fix the tests.\n\nin api/"
    assert substitute("Use $2.", "one") == "Use ."
    assert parse_file("No frontmatter.") == ({}, "No frontmatter.")
    # Windows PowerShell 5.1 writes a BOM; Notepad may write CRLF.
    assert parse_file("\ufeff---\r\ndescription: d\r\n---\r\nBody\r\n") == (
        {"description": "d"},
        "Body",
    )


def test_loading_namespaces_and_precedence(project: Path) -> None:
    command(project_commands(project), "review", "project review")
    command(project_commands(project), "db:migrate", "migrate")
    command(project_commands(project), "help", "can't replace a built-in")
    command(user_commands(), "review", "my review")
    found = load_commands(project)
    assert set(found) == {"review", "db:migrate"}
    assert found["review"].body == "my review" and found["review"].origin == "user"
    assert found["db:migrate"].origin == "project"


async def test_a_command_expands_and_allows_its_tools_for_one_turn(
    mock_server: Any, project: Path
) -> None:
    command(user_commands(), "review", REVIEW)
    server = mock_server(
        [
            bash("echo checked first"),
            {"content": "Looks fine."},
            bash("echo checked"),
            {"content": "ok"},
        ]
    )
    asker = Asker()
    agent = make_agent(server, project, ask=asker)
    try:
        expansion, warnings = await agent.expand_command("/review app.py now")
        assert expansion is not None and warnings == []
        assert expansion.prompt == "Review app.py for bugs. Notes: app.py now"
        events = [e async for e in agent.run(expansion.prompt, allow=expansion.allowed_tools)]
        assert asker.asked == []  # Bash(echo checked:*) came with the command
        assert events[-1].result == "Looks fine."
        assert server.requests[0]["messages"][-1]["content"] == expansion.prompt
        # The next turn has no command: the same call asks again.
        [e async for e in agent.run("again")]
        assert [r.tool_name for r in asker.asked] == ["Bash"]
        assert await agent.expand_command("/nope") == (None, [])
    finally:
        await agent.close()


@pytest.mark.parametrize(
    ("trusted", "locked", "asks"), [(False, False, 1), (True, False, 0), (True, True, 1)]
)
async def test_a_repository_command_grants_tools_only_when_trusted(
    mock_server: Any, project: Path, trusted: bool, locked: bool, asks: int
) -> None:
    command(project_commands(project), "review", REVIEW)
    server = mock_server([bash("echo checked"), {"content": "ok"}])
    asker = Asker()
    agent = make_agent(server, project, trusted=trusted, ask=asker, locked=locked)
    try:
        expansion, _ = await agent.expand_command("/review a.py")
        assert expansion is not None
        [e async for e in agent.run(expansion.prompt, allow=expansion.allowed_tools)]
    finally:
        await agent.close()
    assert len(asker.asked) == asks


async def test_mcp_prompts_are_commands(mock_server: Any, project: Path) -> None:
    settings = Settings.model_validate(
        {"mcpServers": {"demo": {"command": sys.executable, "args": [SERVER]}}}
    )
    server = mock_server([{"content": "ok"}])
    agent = make_agent(server, project, mcp=McpManager(settings, project))
    try:
        expansion, warnings = await agent.expand_command("/mcp__demo__review src/app.py")
        assert warnings == [] and expansion is not None
        assert expansion.prompt == "Please review src/app.py for bugs."
        listed = agent.command_list()["mcp__demo__review"]
        assert listed.origin == "mcp" and listed.argument_hint == "<file>"
        with pytest.raises(ValueError, match="needs: <file>"):
            await agent.expand_command("/mcp__demo__review")
        events = [e async for e in agent.run(expansion.prompt)]
        assert events[-1].result == "ok"
    finally:
        await agent.close()


async def test_commands_over_stdio(mock_server: Any, project: Path) -> None:
    command(user_commands(), "hello", "---\ndescription: Say hello\n---\nSay hello to $ARGUMENTS.")
    server = mock_server([{"content": "Hello!"}, {"content": "A path."}])
    agent = await StdioAgent.start(project, server)
    try:
        await agent.next()
        await agent.send(type="list_commands")
        listed = (await agent.until("command_list"))["commands"]
        assert {
            "name": "hello",
            "description": "Say hello",
            "argument_hint": "",
            "origin": "user",
        } in listed
        assert listed[0]["name"] == "compact"

        await agent.send(type="user_message", text="/hello the team")
        assert (await agent.until("result"))["result"] == "Hello!"
        assert server.requests[-1]["messages"][-1]["content"] == "Say hello to the team."

        await agent.send(type="user_message", text="/usr/bin/env is missing")
        assert (await agent.until("result"))["result"] == "A path."  # not a command
        assert server.requests[-1]["messages"][-1]["content"] == "/usr/bin/env is missing"

        await agent.send(type="user_message", text="/clear")
        assert "isn't available here" in (await agent.until("warning"))["message"]
        assert (await agent.until("result"))["is_error"] is True
        assert len(server.requests) == 2
    finally:
        await agent.close()


def test_events_match_the_protocol() -> None:
    ev.parse_event({"type": "command_list", "commands": []})
