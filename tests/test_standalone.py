"""The standalone build (packaging/build.py), exercised as users run it.

Runs only when CMCODER_TEST_BINARY points at a built `cmcoder` executable
(the Release workflow, or by hand after `uv run --with pyinstaller python
packaging/build.py`: CMCODER_TEST_BINARY=dist/cmcoder/cmcoder).

With CMCODER_TEST_NO_PYTHON=1 the program runs with no Python in reach
(packaging/no_python.py), as on developers' PCs, and a test fails if anything
it did started Python, uv or pip."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cmcoder.sandbox import detect

from .conftest import API_KEY

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import no_python  # noqa: E402

BINARY = os.environ.get("CMCODER_TEST_BINARY")
NO_PYTHON = os.environ.get("CMCODER_TEST_NO_PYTHON") == "1"
BRAND_JSON = Path(__file__).resolve().parent.parent / "branding" / "brand.json"
pytestmark = pytest.mark.skipif(not BINARY, reason="set CMCODER_TEST_BINARY to run")
_TRAP: dict[str, Path] = {}


@pytest.fixture(autouse=True)
def _nothing_started_python(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """With CMCODER_TEST_NO_PYTHON=1: each test fails if the program started Python."""
    if not NO_PYTHON:
        yield
        return
    if not _TRAP:
        tmp = tmp_path_factory.mktemp("no-python")
        _TRAP["folder"] = no_python.trap(tmp / "trap")
        _TRAP["log"] = tmp / "started.log"
        no_python.check(program_env(), _TRAP["folder"])
    _TRAP["log"].unlink(missing_ok=True)
    yield
    assert no_python.started(_TRAP["log"]) == [], "the standalone program started Python"


def program_env(server: Any = None, **env: str) -> dict[str, str]:
    """The environment the program runs in (no Python in reach when asked)."""
    environ = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    environ.update(
        {
            "CMCODER_BASE_URL": server.base_url if server else "http://127.0.0.1:9/v1",
            "CMCODER_API_KEY": API_KEY,
            "CMCODER_MODEL": "qwen3-27b",
            **env,
        }
    )
    if NO_PYTHON:
        environ = no_python.environment(environ, _TRAP["folder"], _TRAP["log"])
    return environ


def run(project: Path, server: Any, *args: str, stdin: str | None = None, **env: str) -> Any:
    assert BINARY
    environ = program_env(server, **env)
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


def test_without_git_bash_it_answers_and_says_what_to_install(
    mock_server: Any, project: Path, tmp_path: Path
) -> None:
    """A developer without Git for Windows: cmcoder still works; a shell
    command explains what to install instead of failing the turn."""
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "echo hi"}}]},
            {"content": "No shell here."},
        ]
    )
    missing = str(tmp_path / "no-git" / "bash.exe")
    r = run(
        project,
        server,
        "-p",
        "go",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "bypassPermissions",
        CMCODER_GIT_BASH_PATH=missing,
    )
    assert r.returncode == 0, r.stderr
    events = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    [bash] = [e for e in events if e["type"] == "tool_result" and e["name"] == "Bash"]
    assert bash["is_error"] and "Git for Windows" in bash["content"]
    assert events[-1]["result"] == "No shell here."


def test_the_vscode_protocol(mock_server: Any, project: Path) -> None:
    """Driven like the extension: a message, the reply, then shutdown."""
    assert BINARY
    server = mock_server([{"content": "Hi there."}])
    env = program_env(server)
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


def test_branding_is_inside(project: Path) -> None:
    """The brand's files are packaged; Windows Terminal gets the .ico."""
    r = run(project, None, "terminal-profile", "--print")
    assert r.returncode == 0, r.stderr
    p = json.loads(r.stdout)["profiles"][0]
    assert p["name"] == json.loads(BRAND_JSON.read_text("utf-8"))["productName"]
    assert p["icon"].endswith("cmcoder.ico") and Path(p["icon"]).is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file details")
def test_the_windows_exe_has_the_icon_and_details() -> None:
    assert sys.platform == "win32"  # (for the type checker)
    import ctypes
    from ctypes import wintypes

    exe = str(Path(BINARY or "").resolve())
    shell32 = ctypes.WinDLL("shell32")
    shell32.ExtractIconExW.restype = wintypes.UINT
    assert shell32.ExtractIconExW(exe, -1, None, None, 0) >= 1  # icons in the exe

    version = ctypes.WinDLL("version")
    size = version.GetFileVersionInfoSizeW(exe, None)
    assert size
    buf = ctypes.create_string_buffer(size)
    assert version.GetFileVersionInfoW(exe, 0, size, buf)
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    key = "\\StringFileInfo\\040904B0\\ProductName"
    assert version.VerQueryValueW(buf, key, ctypes.byref(ptr), ctypes.byref(length))
    product = ctypes.wstring_at(ptr.value or 0, length.value).rstrip("\0")
    brand = json.loads(BRAND_JSON.read_text("utf-8"))
    assert product == brand["productName"]


def test_code_search(mock_server: Any, project: Path) -> None:
    """numpy and the code index work in the standalone build."""
    (project / "auth.py").write_text("def refresh_auth_token(session):\n    return session\n")
    server = mock_server([])
    r = run(
        project,
        server,
        "rag",
        "setup",
        "-m",
        "default:text-embedding-3-small",
        "--store",
        "local",
        "--index",
        "--yes",
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert "Indexed 1 files" in r.stdout
    r = run(project, server, "index", "--status")
    assert "1 files" in r.stdout, r.stderr
