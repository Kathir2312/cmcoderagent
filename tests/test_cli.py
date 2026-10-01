"""End-to-end: the real `cmcoder` CLI as a subprocess against the mock server."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from .conftest import API_KEY


def cli(
    args: list[str], cwd: Path, server: Any, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update(
        {
            "CMCODER_BASE_URL": server.base_url,
            "CMCODER_API_KEY": API_KEY,
            "CMCODER_MODEL": "qwen3-27b",
            "NO_COLOR": "1",
        }
    )
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "cmcoder", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


SCRIPT = [
    {
        "tool_calls": [
            {"name": "Write", "arguments": {"file_path": "hello.py", "content": "print('hi')\n"}}
        ]
    },
    {"tool_calls": [{"name": "Bash", "arguments": {"command": "python3 hello.py"}}]},
    {"content": "Created hello.py; it prints hi."},
]


def test_print_mode_text(mock_server: Any, project: Path) -> None:
    server = mock_server(list(SCRIPT))
    r = cli(
        [
            "-p",
            "make hello",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "Bash(python3:*)",
        ],
        project,
        server,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Created hello.py; it prints hi."
    assert (project / "hello.py").read_text() == "print('hi')\n"
    tool_results = [m for m in server.requests[2]["messages"] if m["role"] == "tool"]
    assert tool_results[1]["content"] == "hi"


def test_print_mode_stream_json(mock_server: Any, project: Path) -> None:
    server = mock_server(list(SCRIPT))
    r = cli(
        [
            "-p",
            "make hello",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "bypassPermissions",
        ],
        project,
        server,
    )
    assert r.returncode == 0, r.stderr
    events = [json.loads(line) for line in r.stdout.splitlines()]
    types = [e["type"] for e in events]
    assert types[0] == "system_init" and types[-1] == "result"
    assert events[0]["model"] == "qwen3-27b"
    assert "tool_use" in types and "tool_result" in types and "usage" in types
    assert events[-1]["subtype"] == "success"


def test_print_mode_json_and_permission_denial(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "make deploy"}}]},
            {"content": "I need permission to run make deploy."},
        ]
    )
    r = cli(["-p", "deploy", "--output-format", "json"], project, server)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["type"] == "result" and out["result"] == "I need permission to run make deploy."
    tool_msg = [m for m in server.requests[1]["messages"] if m["role"] == "tool"][0]
    assert (
        "Permission denied" in tool_msg["content"] and "Bash(make deploy:*)" in tool_msg["content"]
    )


def test_auth_failure_exit_code(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "never"}])
    r = cli(["-p", "hi"], project, server, {"CMCODER_API_KEY": "wrong"})
    assert r.returncode == 1
    assert "Authentication failed" in r.stderr and "cmcoder login" in r.stderr


def test_project_settings_and_memory_are_used(mock_server: Any, project: Path) -> None:
    (project / "CMCODER.md").write_text("Always answer in French.")
    (project / ".cmcoder").mkdir()
    (project / ".cmcoder/settings.json").write_text(json.dumps({"permissions": {"deny": ["Read"]}}))
    (project / "a.txt").write_text("x")
    server = mock_server(
        [
            {"tool_calls": [{"name": "Read", "arguments": {"file_path": "a.txt"}}]},
            {"content": "Non."},
        ]
    )
    r = cli(["-p", "read a.txt"], project, server)
    assert r.returncode == 0, r.stderr
    first = server.requests[0]["messages"]
    assert "Always answer in French." in first[0]["content"]
    tool_msg = [m for m in server.requests[1]["messages"] if m["role"] == "tool"][0]
    assert "denied by rule Read" in tool_msg["content"]


def test_missing_model_config(project: Path) -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("CMCODER_") or k == "CMCODER_CONFIG_DIR"
    }
    r = subprocess.run(
        [sys.executable, "-m", "cmcoder", "-p", "hi"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 2
    assert "No model configured" in r.stderr


def test_protocol_schema_command(project: Path) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "cmcoder", "protocol-schema"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0
    schema = json.loads(r.stdout)
    assert "result" in json.dumps(schema)


@pytest.mark.parametrize("flag", ["--version"])
def test_version(project: Path, flag: str) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "cmcoder", flag],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0 and r.stdout.strip()
