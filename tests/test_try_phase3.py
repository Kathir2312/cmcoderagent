"""docs/phase3/try-phase3.ps1, the hands-on test project, made with Windows
PowerShell 5.1 (the one every Windows machine has) and read back by cmcoder."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cmcoder.config.settings import Settings, config_dir, load_settings
from cmcoder.core.commands import load_commands
from cmcoder.core.skills import load_skills
from cmcoder.core.subagents import load_agents
from cmcoder.mcp_client import McpManager

SCRIPT = Path(__file__).parent.parent / "docs" / "phase3" / "try-phase3.ps1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None,
    reason="Windows PowerShell only",
)


def run_script(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)]
        + ["-Path", str(target)],
        capture_output=True,
        text=True,
        timeout=180,
        stdin=subprocess.DEVNULL,
    )


async def test_the_script_makes_a_project_cmcoder_reads(tmp_path: Path) -> None:
    target = tmp_path / "p3"
    r = run_script(target)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Test project ready" in r.stdout

    assert load_commands(target)["explain"].description == "Explain a file to a new team member"
    assert load_skills(target)["haiku"].description == "Use when asked for a poem about code."
    assert load_agents(target, project_trusted=False)["reviewer"].tools == ["Read", "Grep", "Glob"]
    assert (config_dir() / "agents" / "reviewer.md").exists()  # yours, not the project's

    s = load_settings(target, environ={}, trust_project=True)
    assert "PostToolUse" in s.project_hooks
    assert set(s.project_mcp_servers) == {"tickets"}

    # The MCP server it set up starts and offers its tool.
    mine = Settings.model_validate({})
    mine.mcp_servers = s.project_mcp_servers
    manager = McpManager(mine, target)
    try:
        warnings = [w.message async for w in manager.start(None)]
        assert warnings == []
        assert "mcp__tickets__get_ticket" in [t.name for t in manager.tools()]
    finally:
        await manager.close()

    # Running it again starts over; a folder it didn't make is left alone.
    assert run_script(target).returncode == 0
    other = tmp_path / "mine"
    other.mkdir()
    (other / "keep.txt").write_text("x")
    r = run_script(other)
    assert r.returncode != 0 and (other / "keep.txt").exists()
