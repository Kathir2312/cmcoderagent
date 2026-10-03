"""The standalone build (packaging/build.py), exercised as users run it.

Runs only when CMCODER_TEST_BINARY points at a built `cmcoder` executable
(the Release workflow, or by hand after `uv run --with pyinstaller python
packaging/build.py`: CMCODER_TEST_BINARY=dist/cmcoder/cmcoder)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cmcoder.sandbox import detect

from .conftest import API_KEY

BINARY = os.environ.get("CMCODER_TEST_BINARY")
pytestmark = pytest.mark.skipif(not BINARY, reason="set CMCODER_TEST_BINARY to run")


def run(project: Path, server: Any, *args: str, stdin: str | None = None, **env: str) -> Any:
    assert BINARY
    environ = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    environ.update(
        {
            "CMCODER_BASE_URL": server.base_url if server else "http://127.0.0.1:9/v1",
            "CMCODER_API_KEY": API_KEY,
            "CMCODER_MODEL": "qwen3-27b",
            **env,
        }
    )
    return subprocess.run(
        [str(Path(BINARY).resolve()), *args],
        cwd=project,
        env=environ,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_version_and_doctor(project: Path) -> None:
    r = run(project, None, "--version")
    assert r.returncode == 0 and r.stdout.strip()
    r = run(project, None, "doctor", "--no-probe")
    assert "Bash sandbox" in r.stdout and "Traceback" not in r.stdout + r.stderr


def test_a_turn_with_tools(mock_server: Any, project: Path) -> None:
    (project / "a.txt").write_text("hello\n")
    server = mock_server(
        [
            {"tool_calls": [{"name": "Read", "arguments": {"file_path": "a.txt"}}]},
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "echo from-bash"}}]},
            {"content": "Done."},
        ]
    )
    r = run(
        project,
        server,
        "-p",
        "go",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "bypassPermissions",
    )
    assert r.returncode == 0, r.stderr
    events = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    results = {e["name"]: e["content"] for e in events if e["type"] == "tool_result"}
    assert "hello" in results["Read"] and "from-bash" in results["Bash"]
    assert events[-1]["result"] == "Done."


def test_the_vscode_protocol(mock_server: Any, project: Path) -> None:
    """Driven like the extension: a message, the reply, then shutdown."""
    assert BINARY
    server = mock_server([{"content": "Hi there."}])
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update(CMCODER_BASE_URL=server.base_url, CMCODER_API_KEY=API_KEY, CMCODER_MODEL="qwen3-27b")
    proc = subprocess.Popen(
        [str(Path(BINARY).resolve()), "--protocol", "stdio"],
        cwd=project,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdin and proc.stdout
    try:
        assert json.loads(proc.stdout.readline())["type"] == "system_init"
        proc.stdin.write(json.dumps({"type": "user_message", "text": "hello"}) + "\n")
        proc.stdin.flush()
        while (event := json.loads(proc.stdout.readline()))["type"] != "result":
            pass
        assert event["result"] == "Hi there."
        proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(detect().kind != "bwrap", reason="the bridge is for the Linux sandbox")
def test_the_sandbox_bridge(mock_server: Any, project: Path) -> None:
    """In the standalone build the bridge is cmcoder itself (--sandbox-bridge)."""
    server = mock_server(
        [
            {
                "tool_calls": [
                    {
                        "name": "Bash",
                        "arguments": {
                            "command": "curl -sS -m 10 https://example.invalid/ ; echo; "
                            "echo proxy=$HTTP_PROXY"
                        },
                    }
                ]
            },
            {"content": "ok"},
        ]
    )
    r = run(
        project,
        server,
        "-p",
        "go",
        "--output-format",
        "stream-json",
        CMCODER_SANDBOX="on",
    )
    events = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    out = next(e["content"] for e in events if e["type"] == "tool_result")
    # The request reached cmcoder's proxy through the bridge, which refused it.
    assert "CONNECT tunnel failed, response 403" in out, out
