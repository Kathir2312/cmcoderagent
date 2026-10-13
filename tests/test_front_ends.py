"""Phase 3 item 6: what the front ends show about commands, agents, skills and MCP."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from cmcoder.cli.doctor import Doctor
from cmcoder.config.settings import config_dir, load_settings, set_project_trust

from .test_stdio import Agent as StdioAgent


def extensions(project: Path) -> None:
    c = project / ".cmcoder"
    (c / "commands").mkdir(parents=True)
    (c / "commands" / "review.md").write_text(
        "---\ndescription: Review\nallowed-tools: Read, Bash(git log:*)\n---\nReview $1."
    )
    (c / "agents").mkdir()
    (c / "agents" / "reviewer.md").write_text(
        "---\ndescription: Reviews code\ntools: Read\n---\nGo."
    )
    (c / "skills" / "release").mkdir(parents=True)
    (c / "skills" / "release" / "SKILL.md").write_text("---\ndescription: Release notes\n---\nDo.")


def doctor_text(project: Path) -> str:
    console = Console(record=True, width=200)
    Doctor(load_settings(project, environ={}), console).check_extensions()
    return console.export_text()


def test_doctor_lists_commands_agents_and_skills(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extensions(project)
    (config_dir() / "agents").mkdir(parents=True)
    (config_dir() / "agents" / "notes.md").write_text(
        "---\ndescription: Notes\nmodel: small\n---\nGo."
    )
    monkeypatch.chdir(project)

    out = doctor_text(project)
    assert "/review: Review" in out and "allowed-tools apply once the project is trusted" in out
    assert "agent notes: Notes" in out and "model: small" in out
    assert "Project agents not used: reviewer" in out
    assert "skill release: Release notes" in out

    set_project_trust(project, True)
    out = doctor_text(project)
    assert "allows Read, Bash(git log:*) for its turn" in out
    assert "agent reviewer: Reviews code" in out and "tools: Read" in out
    assert "not used" not in out


def test_trust_lists_agents_and_command_tools(project: Path) -> None:
    extensions(project)
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    r = subprocess.run(
        [sys.executable, "-m", "cmcoder", "trust"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        stdin=subprocess.DEVNULL,
    )
    out = " ".join(r.stdout.split())  # Rich wraps long lines
    assert ".cmcoder/agents: subagents (reviewer) for the Task tool" in out
    assert "/review allows Read, Bash(git log:*) for its turn" in out


async def test_mcp_status_over_stdio(mock_server: Any, project: Path) -> None:
    server = mock_server([])
    agent = await StdioAgent.start(project, server)
    try:
        await agent.next()
        await agent.send(type="list_commands")
        names = [c["name"] for c in (await agent.until("command_list"))["commands"]]
        assert {"compact", "mcp"} <= set(names)
        await agent.send(type="user_message", text="/mcp")
        reply = await agent.until("assistant_message")
        assert reply["text"].startswith("**MCP servers**") and "No MCP servers" in reply["text"]
        assert (await agent.next())["type"] == "result"
        assert server.requests == []  # nothing went to the model
    finally:
        await agent.close()
