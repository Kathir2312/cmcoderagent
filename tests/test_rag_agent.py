"""Phase 5 item 4: the CodeSearch tool and automatic code context in a session."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from cmcoder.cli.factory import AgentOptions, build_agent, build_provider
from cmcoder.config.settings import load_settings
from cmcoder.core.agent import Agent, visible_text
from cmcoder.protocol import events as ev
from cmcoder.rag.index import open_index

from .conftest import API_KEY
from .test_rag_index import AUTH


@pytest.fixture
def env(mock_server: Any, project: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A project with code, a gateway with an embedding model, and settings for both."""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    (project / "auth.py").write_text(AUTH)
    (project / "notes.md").write_text("# Deploy\n\nDeploys run from the release branch.\n")
    server = mock_server([])
    monkeypatch.setenv("CMCODER_BASE_URL", server.base_url)
    monkeypatch.setenv("CMCODER_API_KEY", API_KEY)
    monkeypatch.setenv("CMCODER_EMBEDDING_MODEL", "text-embedding-3-small")
    return server


async def build_index(project: Path) -> None:
    index = open_index(load_settings(project), project, build_provider)
    assert index is not None
    try:
        await index.update()
    finally:
        await index.close()


async def session(project: Path, **kw: Any) -> Agent:
    return await build_agent(
        load_settings(project), AgentOptions(cwd=project, model="qwen3-27b", **kw)
    )


def sent_to_model(server: Any, i: int = -1) -> str:
    return "\n".join(str(m.get("content")) for m in server.state.requests[i]["messages"])


async def test_code_search_tool(env: Any, project: Path) -> None:
    await build_index(project)
    env.state.script = [
        {
            "tool_calls": [
                {"name": "CodeSearch", "arguments": {"query": "refresh expired auth token"}}
            ]
        },
        {"content": "It's refresh_auth_token in auth.py."},
    ]
    agent = await session(project)
    try:
        assert "CodeSearch" in agent.tools
        assert "Code search: this project is indexed" in agent.system_prompt
        events = [e async for e in agent.run("ok")]  # too short for automatic context
    finally:
        await agent.close()
    result = next(e for e in events if isinstance(e, ev.ToolResult))
    assert "auth.py:" in result.content and "refresh_auth_token" in result.content
    assert not result.is_error and not any(isinstance(e, ev.PermissionRequest) for e in events)


async def test_automatic_context(env: Any, project: Path) -> None:
    await build_index(project)
    env.state.script = [{"content": "a"}, {"content": "b"}, {"content": "c"}]
    agent = await session(project)
    try:
        first = [e async for e in agent.run("how is the expired auth token refreshed?")]
        context = next(e for e in first if isinstance(e, ev.CodeContext))
        assert context.items[0].path == "auth.py" and context.tokens > 0
        sent = sent_to_model(env)
        assert "<system-reminder>" in sent and "def refresh_auth_token" in sent
        # The same code isn't sent twice in one conversation; short messages get none.
        second = [e async for e in agent.run("how is the expired auth token refreshed?")]
        assert not any(isinstance(e, ev.CodeContext) for e in second)
        third = [e async for e in agent.run("thanks")]
        assert not any(isinstance(e, ev.CodeContext) for e in third)
        # Rewinding gives back what the user wrote, without the added code.
        _, prompt = agent.rewind(1, code=False)
        assert prompt == "how is the expired auth token refreshed?"
        assert visible_text(sent) != sent
    finally:
        await agent.close()


async def test_off_or_limited(env: Any, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No index yet: no tool, and no warning unless code search was asked for.
    agent = await session(project)
    assert "CodeSearch" not in agent.tools and not agent.startup_warnings
    await agent.close()
    monkeypatch.setenv("CMCODER_RAG", "on")
    agent = await session(project)
    assert any("no index yet" in w for w in agent.startup_warnings)
    await agent.close()
    # Automatic context can be turned off, keeping the tool.
    await build_index(project)
    (project / ".cmcoder").mkdir()
    (project / ".cmcoder" / "settings.json").write_text(
        '{"rag": {"autoContext": {"enabled": false}}}'
    )
    env.state.script = [{"content": "a"}]
    agent = await session(project)
    try:
        events = [e async for e in agent.run("how is the expired auth token refreshed?")]
        assert "CodeSearch" in agent.tools
        assert not any(isinstance(e, ev.CodeContext) for e in events)
    finally:
        await agent.close()


async def test_a_failing_index_never_stops_the_turn(env: Any, project: Path) -> None:
    await build_index(project)
    env.state.embedding_models = []  # the embedding model is gone from the gateway
    env.state.script = [{"content": "answer"}, {"content": "again"}]
    agent = await session(project)
    try:
        events = [e async for e in agent.run("how is the expired auth token refreshed?")]
        assert events[-1].result == "answer"
        warnings = [e.message for e in events if isinstance(e, ev.Warning)]
        assert any("Code search isn't answering" in w for w in warnings)
        events = [e async for e in agent.run("and how are invoices parsed then?")]
        assert not any(isinstance(e, ev.Warning) for e in events)  # said once
    finally:
        await agent.close()


async def test_edits_and_new_files_are_found(env: Any, project: Path) -> None:
    await build_index(project)
    (project / "billing.py").write_text(
        "def compute_vat_for_invoice(total):\n    return total * 0.2\n"
    )
    env.state.script = [
        {"tool_calls": [{"name": "Read", "arguments": {"file_path": "auth.py"}}]},
        {
            "tool_calls": [
                {
                    "name": "Edit",
                    "arguments": {
                        "file_path": "auth.py",
                        "old_string": "def parse_invoice_total(lines):",
                        "new_string": "def parse_invoice_grand_total(lines):",
                    },
                }
            ]
        },
        {"tool_calls": [{"name": "CodeSearch", "arguments": {"query": "invoice grand total"}}]},
        {"tool_calls": [{"name": "CodeSearch", "arguments": {"query": "vat for invoice"}}]},
        {"content": "done"},
    ]
    agent = await session(project, permission_mode="acceptEdits")
    try:
        # The session brings the index up to date in the background (billing.py is new).
        index = agent.ctx.code_index
        assert index is not None
        for _ in range(100):
            if not index.updating:
                break
            await asyncio.sleep(0.05)
        events = [e async for e in agent.run("ok")]
    finally:
        await agent.close()
    results = [e.content for e in events if isinstance(e, ev.ToolResult)]
    assert "parse_invoice_grand_total" in results[2]  # the agent's own edit
    assert "compute_vat_for_invoice" in results[3]  # a file added before the session


async def test_subagents_search_too(env: Any, project: Path) -> None:
    await build_index(project)
    agent = await session(project)
    try:
        from cmcoder.core.subagents import BUILT_IN_AGENTS, select_tools

        explore = BUILT_IN_AGENTS["explore"]
        assert "CodeSearch" in select_tools(agent.tools, explore.tools)
        assert agent.ctx.code_index is not None
    finally:
        await agent.close()


async def test_index_command_turns_code_search_on_mid_session(env: Any, project: Path) -> None:
    from cmcoder.cli.factory import index_command

    agent = await session(project)
    try:
        settings = load_settings(project)
        assert "CodeSearch" not in agent.tools
        assert await index_command(agent, settings, "status") == [
            "No index for this project yet: /index builds it."
        ]
        lines = await index_command(agent, settings, "")
        assert lines[0].startswith("Indexed 2 files") and "on for the rest" in lines[1]
        assert "CodeSearch" in agent.tools and agent.ctx.code_index is not None
        assert "Code search: this project is indexed" in agent.messages[0].content
        status = await index_command(agent, settings, "status")
        assert any(line.startswith("Files, pieces      2 files") for line in status)
        assert (await index_command(agent, settings, "nonsense"))[0].startswith("Usage")
    finally:
        await agent.close()
