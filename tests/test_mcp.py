"""MCP client (Phase 3 item 1) against real MCP servers (tests/mcp_servers)."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cmcoder.config.settings import Settings, load_settings, set_project_trust
from cmcoder.core.agent import Agent, PermissionAnswer, PermissionRequest
from cmcoder.core.permissions import Decision, PermissionPolicy
from cmcoder.mcp_client import McpManager, expand, tool_name
from cmcoder.protocol import events as ev
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider

SERVER = str(Path(__file__).parent / "mcp_servers" / "demo_server.py")


def stdio(**extra: Any) -> dict[str, Any]:
    return {"command": sys.executable, "args": [SERVER], **extra}


def call(name: str, /, **arguments: Any) -> dict[str, Any]:
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


def agent_with(
    server: Any,
    project: Path,
    settings: Settings,
    mode: str = "bypassPermissions",
    ask: Any = None,
    allow: list[str] | None = None,
) -> Agent:
    return Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy(mode, allow=allow or []),
        ToolContext(cwd=project, project_root=project),
        "test",
        ask=ask,
        mcp=McpManager(settings, project),
    )


async def run(agent: Agent, prompt: str) -> list[Any]:
    try:
        return [e async for e in agent.run(prompt)]
    finally:
        await agent.close()


def tool_results(events: list[Any]) -> dict[str, ev.ToolResult]:
    return {e.name: e for e in events if isinstance(e, ev.ToolResult)}


async def test_stdio_server_tools_reach_the_model(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            call("mcp__demo__add", a=2, b=3),
            call("mcp__demo__fail", reason="test"),
            call("ListMcpResources"),
            call("ReadMcpResource", server="demo", uri="demo://greeting"),
            {"content": "Done."},
        ]
    )
    settings = Settings.model_validate({"mcpServers": {"demo": stdio()}})
    events = await run(agent_with(server, project, settings), "use the demo tools")
    results = tool_results(events)
    assert results["mcp__demo__add"].content == "5" and not results["mcp__demo__add"].is_error
    assert results["mcp__demo__fail"].is_error
    assert "demo://greeting" in results["ListMcpResources"].content
    assert results["ReadMcpResource"].content == "Hello from the demo server."
    # The model was offered the server's tools with their own JSON schemas.
    offered = {t["function"]["name"]: t["function"] for t in server.requests[0]["tools"]}
    assert offered["mcp__demo__add"]["parameters"]["properties"].keys() == {"a", "b"}
    assert offered["mcp__demo__shout"]["description"].startswith("[MCP server demo]")


async def test_mcp_tools_ask_unless_allowed(mock_server: Any, project: Path) -> None:
    server = mock_server([call("mcp__demo__shout", text="hi"), {"content": "ok"}] * 2)
    settings = Settings.model_validate({"mcpServers": {"demo": stdio()}})
    asked: list[PermissionRequest] = []

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=True)

    events = await run(agent_with(server, project, settings, "default", ask), "shout")
    assert [r.tool_name for r in asked] == ["mcp__demo__shout"]
    assert tool_results(events)["mcp__demo__shout"].content == "HI"
    # A whole-server allow rule: no question.
    asked.clear()
    events = await run(
        agent_with(server, project, settings, "default", ask, ["mcp__demo"]), "shout"
    )
    assert asked == [] and tool_results(events)["mcp__demo__shout"].content == "HI"


async def test_a_broken_server_is_reported_and_skipped(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "Still working."}])
    settings = Settings.model_validate(
        {
            "mcpServers": {
                "missing": {"command": "definitely-not-a-program-xyz"},
                "crash": {"command": sys.executable, "args": ["-c", "import sys; sys.exit(3)"]},
                "off": {**stdio(), "disabled": True},
                "demo": stdio(),
            }
        }
    )
    agent = agent_with(server, project, settings)
    events = await run(agent, "hello")
    warnings = [e.message for e in events if isinstance(e, ev.Warning)]
    assert any("missing" in w and "not found on PATH" in w for w in warnings)
    assert any("crash" in w for w in warnings)
    assert events[-1].result == "Still working."
    status = {s.name: s.status for s in agent.mcp.servers}  # type: ignore[union-attr]
    assert status == {"missing": "failed", "crash": "failed", "off": "disabled", "demo": "disabled"}
    # (demo was connected; "disabled" after the agent closed it)


async def test_environment_values_expand(
    mock_server: Any, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_TOKEN", "s3cret")
    server = mock_server([call("mcp__demo__env", name="TOKEN"), {"content": "ok"}])
    settings = Settings.model_validate(
        {"mcpServers": {"demo": stdio(env={"TOKEN": "${DEMO_TOKEN}"})}}
    )
    events = await run(agent_with(server, project, settings), "token?")
    assert tool_results(events)["mcp__demo__env"].content == "s3cret"
    assert expand("${NOPE:-fallback}") == "fallback"
    with pytest.raises(ValueError, match="NOPE is not set"):
        expand("${NOPE}")


@pytest.fixture
def http_server() -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, SERVER, "streamable-http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_http_server(
    mock_server: Any, project: Path, http_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    server = mock_server([call("mcp__web__add", a=40, b=2), {"content": "ok"}])
    settings = Settings.model_validate(
        {"mcpServers": {"web": {"url": http_server, "headers": {"X-Team": "${USER:-dev}"}}}}
    )
    events = await run(agent_with(server, project, settings), "add")
    assert tool_results(events)["mcp__web__add"].content == "42"


async def test_project_servers_need_trust_and_approval(mock_server: Any, project: Path) -> None:
    import json

    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"proj": stdio()}}))
    # Not trusted: ignored, with the reason.
    s = load_settings(project, environ={})
    assert s.project_mcp_servers == {}
    assert any(".mcp.json" in x for x in s.ignored_project_settings)
    # Trusted: offered, but each server is approved before it first starts.
    set_project_trust(project, True)
    s = load_settings(project, environ={})
    assert set(s.project_mcp_servers) == {"proj"}

    server = mock_server([{"content": "ok"}] * 3)
    asked: list[PermissionRequest] = []

    async def deny(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=False)

    async def allow(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=True)

    agent = agent_with(server, project, s, ask=deny)
    await run(agent, "hi")
    assert asked[0].tool_name == "McpServer" and "demo_server.py" in asked[0].input["runs"]
    assert "reads your environment variables" not in asked[0].input
    assert agent.mcp.servers[0].status == "not approved"  # type: ignore[union-attr]

    asked.clear()
    agent = agent_with(server, project, s, ask=allow)
    await run(agent, "hi")
    assert len(asked) == 1 and "mcp__proj__add" in agent.tools

    asked.clear()  # remembered: no question the next time
    agent = agent_with(server, project, s, ask=allow)
    await run(agent, "hi")
    assert asked == []

    # A changed command is a different server: asked again.
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"proj": stdio(args=[SERVER, "stdio"])}})
    )
    s = load_settings(project, environ={})
    agent = agent_with(server, project, s, ask=allow)
    await run(agent, "hi")
    assert len(asked) == 1


async def test_without_anyone_to_ask_a_project_server_stays_off(
    mock_server: Any, project: Path
) -> None:
    s = Settings.model_validate({})
    s.project_mcp_servers = Settings.model_validate({"mcpServers": {"proj": stdio()}}).mcp_servers
    server = mock_server([{"content": "ok"}])
    agent = agent_with(server, project, s, ask=None)  # e.g. cmcoder -p
    await run(agent, "hi")
    assert agent.mcp.servers[0].status == "not approved"  # type: ignore[union-attr]
    assert "mcp__proj__add" not in agent.tools


def test_managed_allow_and_deny_lists(project: Path) -> None:
    s = Settings.model_validate({"mcpServers": {"a": stdio(), "b": stdio(), "c": stdio()}})
    s.allowed_mcp_servers = ["a", "b"]
    s.denied_mcp_servers = ["b"]
    m = McpManager(s, project)
    assert {x.name: x.status for x in m.servers} == {
        "a": "starting",
        "b": "blocked",
        "c": "blocked",
    }


def test_tool_names_are_safe_for_model_apis() -> None:
    assert tool_name("my server", "do.thing/now") == "mcp__my_server__do_thing_now"
    assert len(tool_name("s" * 50, "t" * 50)) == 64


def test_server_rule_matches_only_its_own_tools(project: Path) -> None:
    from cmcoder.mcp_client import McpArgs, McpServer, McpTool

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name, self.description, self.title, self.input_schema = name, "x", None, {}

    s = Settings.model_validate({"mcpServers": {"git": stdio(), "github": stdio()}})
    git = McpTool(McpServer("git", s.mcp_servers["git"], "user"), FakeTool("log"))
    github = McpTool(McpServer("github", s.mcp_servers["github"], "user"), FakeTool("log"))
    policy = PermissionPolicy("default", allow=["mcp__git"])
    ctx = ToolContext(cwd=project, project_root=project)
    assert policy.check(git, McpArgs(), ctx).decision == Decision.ALLOW
    assert policy.check(github, McpArgs(), ctx).decision == Decision.ASK  # not a prefix match


def test_mcp_config_file(tmp_path: Path) -> None:
    import json

    from cmcoder.config.settings import SettingsError, add_mcp_config

    s = Settings.model_validate({"mcpServers": {"mine": stdio()}})
    path = tmp_path / "extra.json"
    path.write_text(json.dumps({"mcpServers": {"extra": {"url": "https://mcp.example.com"}}}))
    add_mcp_config(s, path)
    assert set(s.mcp_servers) == {"mine", "extra"}
    assert McpManager(s, tmp_path).servers[1].origin == "user"  # you named the file
    with pytest.raises(SettingsError, match="not found"):
        add_mcp_config(s, tmp_path / "missing.json")
    path.write_text(json.dumps({"mcpServers": {"bad": {"startupTimeout": "soon"}}}))
    with pytest.raises(SettingsError, match="extra.json"):
        add_mcp_config(s, path)


def test_approval_shows_env_headers_and_variables() -> None:
    s = Settings.model_validate(
        {
            "mcpServers": {
                "web": {
                    "url": "https://mcp.example.com/${REGION:-eu}",
                    "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
                },
                "local": {**stdio(), "env": {"KEY": "${AWS_SECRET_ACCESS_KEY}"}, "cwd": "tools"},
            }
        }
    )
    web = s.mcp_servers["web"].approval_details()
    assert web["headers"] == "Authorization: Bearer ${GITHUB_TOKEN}"  # as written, not expanded
    assert web["reads your environment variables"] == "GITHUB_TOKEN, REGION"
    local = s.mcp_servers["local"].approval_details()
    assert local["env"] == "KEY=${AWS_SECRET_ACCESS_KEY}" and local["cwd"] == "tools"
    assert local["reads your environment variables"] == "AWS_SECRET_ACCESS_KEY"
