"""The Bash sandbox (Phase 4 item 3): bubblewrap on Linux, sandbox-exec on
macOS. Skipped where neither can run (e.g. native Windows)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from cmcoder.compat import find_shell
from cmcoder.config.settings import SandboxConfig, load_settings, set_project_trust
from cmcoder.core.agent import Agent, PermissionAnswer, PermissionRequest
from cmcoder.core.permissions import Decision, PermissionPolicy
from cmcoder.core.prompt import build_system_prompt
from cmcoder.providers.profiles import resolve_profile
from cmcoder.sandbox import Sandbox, detect, make_sandbox
from cmcoder.sandbox.proxy import FilteringProxy, host_allowed
from cmcoder.tools.base import ToolContext
from cmcoder.tools.bash import BashInput, BashTool
from cmcoder.tools.registry import default_tools
from cmcoder.tools.shell import PersistentShell

from .conftest import make_provider

AVAILABLE = detect()
needs_sandbox = pytest.mark.skipif(not AVAILABLE.ok, reason=f"no sandbox: {AVAILABLE.reason}")


def config(**kw: Any) -> SandboxConfig:
    return SandboxConfig.model_validate(kw)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    (h / ".ssh").mkdir(parents=True)
    (h / ".ssh" / "id_rsa").write_text("PRIVATE KEY")
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def web() -> Iterator[str]:
    """A plain HTTP server outside the sandbox."""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def do_GET(self) -> None:
            body = b"hello from outside"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


async def run_in_sandbox(project: Path, cfg: SandboxConfig, *commands: str) -> list[Any]:
    sbx, _ = make_sandbox(cfg, project)
    assert sbx is not None
    shell = find_shell()
    assert shell
    sh = PersistentShell(project, shell, sandbox=sbx)
    try:
        return [await sh.run(c, 30) for c in commands]
    finally:
        await sh.close()
        await sbx.close()


def test_host_patterns() -> None:
    allowed = ["pypi.org", "*.pythonhosted.org", "registry.npmjs.org"]
    assert host_allowed("pypi.org", allowed) and host_allowed("PyPI.org.", allowed)
    assert host_allowed("files.pythonhosted.org", allowed)
    assert not host_allowed("pythonhosted.org", allowed)  # "*." means subdomains
    assert not host_allowed("evilpypi.org", allowed) and not host_allowed("example.com", [])


@needs_sandbox
async def test_files(project: Path, home: Path) -> None:
    (project / ".git" / "hooks").mkdir(parents=True)
    (project / ".cmcoder").mkdir()
    (project / ".git" / "config").write_text("[core]\n")
    outside = project.parent / "outside.txt"
    r = await run_in_sandbox(
        project,
        config(),
        "echo ok > made.txt && cat made.txt",
        f"echo x > {outside}",
        "echo x >> .git/hooks/pre-commit",
        "echo x >> .git/config",
        "echo '{}' > .cmcoder/settings.json",
        "cat ~/.ssh/id_rsa",
        "echo x > /etc/cmcoder-test",
        "cd sub 2>/dev/null || mkdir sub && cd sub && pwd",
        "pwd",  # the shell keeps its state
    )
    assert r[0].output.strip() == "ok" and (project / "made.txt").read_text() == "ok\n"
    assert not outside.exists()  # outside the project: never reaches the disk
    for i in (2, 3, 4):
        # Linux: "Read-only file system"; macOS: "Operation not permitted".
        assert r[i].exit_code != 0 and (
            "Read-only file system" in r[i].output or "Operation not permitted" in r[i].output
        )
    assert not (project / ".git" / "hooks" / "pre-commit").exists()
    assert (project / ".git" / "config").read_text() == "[core]\n"
    assert not (project / ".cmcoder" / "settings.json").exists()
    assert r[5].exit_code != 0 and "PRIVATE KEY" not in r[5].output
    assert r[6].exit_code != 0
    assert r[8].output.strip().endswith("/sub")


@needs_sandbox
async def test_network(project: Path, web: str) -> None:
    if shutil.which("curl") is None:
        pytest.skip("needs curl")
    cfg = config(network={"allowedHosts": ["127.0.0.1"]})
    allowed = await run_in_sandbox(
        project,
        cfg,
        f"NO_PROXY= no_proxy= curl -sS -m 10 http://{web}/",
        'curl -sS -m 5 --noproxy "*" http://1.1.1.1/ -o /dev/null; echo rc=$?',  # direct
    )
    assert allowed[0].output.strip() == "hello from outside"
    assert "rc=0" not in allowed[1].output  # no network except the proxy
    blocked = await run_in_sandbox(
        project, config(), f"NO_PROXY= no_proxy= curl -sS -m 10 http://{web}/"
    )
    assert "network access to 127.0.0.1 is blocked" in blocked[0].output
    assert "allowedHosts" in blocked[0].output


@needs_sandbox
async def test_a_timeout_kills_the_sandboxed_command(project: Path) -> None:
    sbx, _ = make_sandbox(config(), project)
    assert sbx is not None
    sh = PersistentShell(project, find_shell() or "bash", sandbox=sbx)
    try:
        r = await sh.run("sleep 30", 1)
        assert r.timed_out
        assert (await sh.run("echo again", 10)).output.strip() == "again"
    finally:
        await sh.close()
        await sbx.close()


class Asker:
    def __init__(self) -> None:
        self.asked: list[PermissionRequest] = []

    async def __call__(self, req: PermissionRequest) -> PermissionAnswer:
        self.asked.append(req)
        return PermissionAnswer(allow=True)


def bash(command: str, **kw: Any) -> dict[str, Any]:
    return {"tool_calls": [{"name": "Bash", "arguments": {"command": command, **kw}}]}


@needs_sandbox
async def test_auto_allow_and_leaving_the_sandbox(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            bash("mkdir made-inside"),
            bash("mkdir made-outside", dangerously_disable_sandbox=True),
            {"content": "done"},
        ]
    )
    sbx, _ = make_sandbox(config(), project)
    asker = Asker()
    agent = Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("default"),
        ToolContext(cwd=project, project_root=project, sandbox=sbx),
        "test",
        ask=asker,
    )
    try:
        [e async for e in agent.run("go")]
    finally:
        await agent.close()
    # Only leaving the sandbox asked, and it said so.
    assert [r.label for r in asker.asked] == ["Bash(mkdir made-outside) [outside the sandbox]"]
    assert not asker.asked[0].can_remember
    assert (project / "made-inside").is_dir() and (project / "made-outside").is_dir()
    assert sbx is not None and sbx.tmp is None  # closed with the agent


@needs_sandbox
def test_policy(project: Path) -> None:
    sbx = Sandbox(config(), project, AVAILABLE.kind or "bwrap")
    ctx = ToolContext(cwd=project, project_root=project, sandbox=sbx)
    tool = BashTool()
    plain = BashInput(command="npm test")
    out = BashInput(command="npm test", dangerously_disable_sandbox=True)
    p = PermissionPolicy("default", allow=["Bash(npm test:*)"])
    assert p.check(tool, out, ctx).decision == Decision.ASK  # an allow rule doesn't skip it
    assert PermissionPolicy("default").check(tool, plain, ctx).decision == Decision.ALLOW
    risky = BashInput(command="rm -rf build")
    assert PermissionPolicy("default").check(tool, risky, ctx).decision == Decision.ASK
    assert PermissionPolicy("plan").check(tool, plain, ctx).decision == Decision.DENY
    assert PermissionPolicy("default", deny=["Bash(npm:*)"]).check(tool, plain, ctx).decision == (
        Decision.DENY
    )
    strict = Sandbox(config(allowUnsandboxedCommands=False), project, "bwrap")
    ctx2 = ToolContext(cwd=project, project_root=project, sandbox=strict)
    assert PermissionPolicy("bypassPermissions").check(tool, out, ctx2).decision == Decision.DENY
    no_auto = Sandbox(config(autoAllow=False), project, "bwrap")
    ctx3 = ToolContext(cwd=project, project_root=project, sandbox=no_auto)
    assert PermissionPolicy("default").check(tool, plain, ctx3).decision == Decision.ASK


def test_the_model_is_told(project: Path) -> None:
    sbx = Sandbox(config(network={"allowedHosts": ["pypi.org"]}), project, "bwrap")
    prompt = build_system_prompt(project, project, sandbox=sbx.prompt_note())
    assert "Sandbox: Bash commands run in a sandbox" in prompt and "pypi.org" in prompt


def test_a_repository_cannot_loosen_it(project: Path) -> None:
    (project / ".cmcoder").mkdir()
    (project / ".cmcoder" / "settings.json").write_text(
        '{"sandbox": {"enabled": false, "network": {"allowedHosts": ["*"]}}}'
    )
    s = load_settings(project, environ={})
    assert s.sandbox.enabled == "auto" and s.sandbox.network.allowed_hosts == []
    assert any("sandbox" in x for x in s.ignored_project_settings)
    set_project_trust(project, True)
    assert load_settings(project, environ={}).sandbox.enabled is False


def test_off_or_unavailable(project: Path) -> None:
    assert make_sandbox(config(enabled=False), project) == (None, None)
    if not AVAILABLE.ok:
        sbx, warning = make_sandbox(config(enabled=True), project)
        assert sbx is None and warning and "sandbox" in warning


@needs_sandbox
def test_subagents_share_it(mock_server: Any, project: Path) -> None:
    sbx = Sandbox(config(), project, AVAILABLE.kind or "bwrap")
    agent = Agent(
        make_provider(mock_server([])),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("default"),
        ToolContext(cwd=project, project_root=project, sandbox=sbx),
        "test",
    )
    from cmcoder.core.subagents import ModelChoice

    child = agent.spawn_subagent(ModelChoice(agent.provider, agent.model, agent.profile), {}, "x")
    assert child.ctx.sandbox is sbx


def git(project: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=project,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


@needs_sandbox
async def test_git_reads_work_and_writes_dont(project: Path) -> None:
    shutil.rmtree(project / ".git")
    git(project, "init", "-q")
    (project / "a.txt").write_text("a\n")
    git(project, "add", "a.txt")
    git(project, "commit", "-qm", "start")
    head = (project / ".git" / "HEAD").read_text()
    (project / "a.txt").write_text("b\n")
    r = await run_in_sandbox(
        project,
        config(),
        "git status --short && git diff --stat && git log --oneline | wc -l",
        "git -c user.name=t -c user.email=t@x commit -qam change; echo rc=$?",
        "mv .git .git-moved; echo rc=$?",
        "rm -rf .git/refs; echo rc=$?",
    )
    assert "M a.txt" in r[0].output and r[0].exit_code == 0
    for out in r[1:]:
        assert "rc=0" not in out.output
    assert (project / ".git" / "HEAD").read_text() == head
    assert (project / ".git" / "refs").is_dir() and not (project / ".git-moved").exists()


@needs_sandbox
async def test_missing_protected_folders_cant_be_created(tmp_path: Path) -> None:
    project = tmp_path / "bare"
    project.mkdir()
    r = await run_in_sandbox(
        project,
        config(),
        "mkdir -p .vscode/x; echo rc=$?",
        "echo '{}' > .mcp.json; echo rc=$?",
        "git init -q; echo rc=$?",
        "echo ok > notes.txt && cat notes.txt",
    )
    for out in r[:3]:
        assert "rc=0" not in out.output
    assert r[3].output.strip() == "ok"
    # The placeholders are gone again; the project's own file stays.
    assert sorted(p.name for p in project.iterdir()) == ["notes.txt"]


@needs_sandbox
async def test_local_services_are_out_of_reach(
    project: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if sys.platform == "win32":
        pytest.skip("Unix sockets")
    # Not under /tmp (private in the sandbox anyway), and short: macOS limits
    # socket paths to about 100 characters.
    runtime = Path(tempfile.mkdtemp(prefix="cmc-", dir=Path.home()))
    request.addfinalizer(lambda: shutil.rmtree(runtime, ignore_errors=True))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("CMCODER_API_KEY", "sk-not-for-the-sandbox")

    async def answer(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        w.write(b"reached\n")
        await w.drain()
        w.close()

    path = str(runtime / "service.sock")
    server = await asyncio.start_unix_server(answer, path=path)
    try:
        r = await run_in_sandbox(
            project,
            config(),
            f'{sys.executable} -I -c "import socket; s = socket.socket(socket.AF_UNIX); '
            f"s.connect('{path}'); print(s.recv(10))\" 2>&1 | tail -1",
            'echo "key=${CMCODER_API_KEY:-none}"',
        )
    finally:
        server.close()
        await server.wait_closed()
    assert "reached" not in r[0].output
    assert r[1].output.strip() == "key=none"


@needs_sandbox
async def test_direct_local_connections_are_blocked(project: Path, web: str) -> None:
    if shutil.which("curl") is None:
        pytest.skip("needs curl")
    r = await run_in_sandbox(
        project,
        config(network={"allowedHosts": ["127.0.0.1"]}),
        f'curl -sS -m 5 --noproxy "*" http://{web}/; echo rc=$?',
    )
    assert "hello from outside" not in r[0].output and "rc=0" not in r[0].output


class Recorder:
    """A plain HTTP server that records each request's first line and Host."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []
        rec = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_GET(self) -> None:
                rec.seen.append((self.requestline, self.headers.get("Host", "")))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]


async def test_the_proxy_forwards_one_checked_request(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    rec = Recorder()
    proxy = FilteringProxy(["127.0.0.1"])
    port = await proxy.start_tcp()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            f"GET http://127.0.0.1:{rec.port}/first HTTP/1.1\r\nHost: other.example\r\n\r\n"
            f"GET http://127.0.0.1:{rec.port}/second HTTP/1.1\r\nHost: x\r\n\r\n".encode()
        )
        await writer.drain()
        reply = await asyncio.wait_for(reader.read(), 10)
        writer.close()
        assert b"200" in reply.split(b"\r\n", 1)[0]
        assert rec.seen == [("GET /first HTTP/1.1", f"127.0.0.1:{rec.port}")]

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            f"POST http://127.0.0.1:{rec.port}/up HTTP/1.1\r\n"
            "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n".encode()
        )
        await writer.drain()
        assert b" 400 " in await asyncio.wait_for(reader.read(), 10)
        writer.close()
    finally:
        await proxy.close()
        rec.httpd.shutdown()
        rec.httpd.server_close()
