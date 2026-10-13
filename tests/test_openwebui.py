"""Open WebUI as a gateway (Phase 4 item 1), against the mock server's Open
WebUI mode, which copies what Open WebUI 0.11 does (see providers/openwebui.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from cmcoder.cli.doctor import Doctor
from cmcoder.cli.factory import build_provider, resolve_model_profile
from cmcoder.config.settings import Settings, env_layer
from cmcoder.core.agent import Agent
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.protocol import events as ev
from cmcoder.providers.auth import ApiKeyAuth
from cmcoder.providers.openai_compat import BadRequest, ProviderError, _call_index
from cmcoder.providers.openwebui import OpenWebUIProvider, api_base
from cmcoder.providers.profiles import resolve_profile
from cmcoder.providers.transport import TransportOptions, build_client
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import API_KEY

MODELS = {"qwen3:32b": "ollama", "qwen3:8b": "ollama", "qwen3-vllm": "openai"}


def webui(mock_server: Any, script: list[dict[str, Any]], **kw: Any) -> Any:
    return mock_server(script, openwebui=MODELS, **kw)


def provider(server: Any) -> OpenWebUIProvider:
    return OpenWebUIProvider(
        "webui",
        server.root_url,
        ApiKeyAuth("webui", explicit_key=API_KEY),
        build_client(TransportOptions(read_timeout=10)),
    )


def settings(server: Any, **extra: Any) -> Settings:
    return Settings.model_validate(
        {
            "providers": {"webui": {"type": "openwebui", "baseUrl": server.root_url}},
            "model": "webui:qwen3:32b",
            **extra,
        }
    )


def call(*calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {"tool_calls": [{"name": n, "arguments": a} for n, a in calls]}


def test_server_address_forms() -> None:
    for url in (
        "https://webui.corp",
        "https://webui.corp/",
        "https://webui.corp/api",
        "https://webui.corp/api/v1/",
    ):
        assert api_base(url) == "https://webui.corp/api"
    assert env_layer({"CMCODER_BASE_URL": "https://w", "CMCODER_PROVIDER_TYPE": "openwebui"})[
        "providers"
    ]["default"] == {"baseUrl": "https://w", "type": "openwebui"}


async def test_models_and_backends(mock_server: Any) -> None:
    p = provider(webui(mock_server, []))
    try:
        assert await p.list_models() == ["qwen3-vllm", "qwen3:32b", "qwen3:8b"]  # no arena
        info = await p.model_info()
        assert (
            info["qwen3:32b"]["backend"] == "ollama" and info["qwen3-vllm"]["backend"] == "openai"
        )
        assert await p.ollama_context_length("qwen3:32b") == 40960
        assert await p.ollama_context_length("qwen3-vllm") is None  # not an Ollama model
    finally:
        await p.aclose()


async def test_an_ollama_model_gets_num_ctx_and_whole_tool_calls(
    mock_server: Any, project: Path
) -> None:
    (project / "a.txt").write_text("A\n")
    (project / "b.txt").write_text("B\n")
    server = webui(
        mock_server,
        [
            call(("Read", {"file_path": "a.txt"}), ("Read", {"file_path": "b.txt"})),
            {"content": "Both read.", "reasoning": "Done."},
        ],
    )
    s = settings(server)
    p = provider(server)
    profile = await resolve_model_profile(s, p, "qwen3:32b")
    assert profile.backend == "ollama" and profile.context_window == 32768
    agent = Agent(
        p,
        "qwen3:32b",
        profile,
        default_tools(),
        PermissionPolicy("default"),
        ToolContext(cwd=project, project_root=project),
        "test",
    )
    try:
        events = [e async for e in agent.run("read both")]
    finally:
        await agent.close()
    results = [e for e in events if isinstance(e, ev.ToolResult)]
    # Two calls, both numbered 0 by Open WebUI, kept apart by their ids.
    assert [r.content.strip().splitlines()[-1].split("\t")[-1] for r in results] == ["A", "B"]
    assert events[-1].result == "Both read." and not events[-1].is_error
    first = server.requests[0]
    assert first["options"] == {"num_ctx": 32768}
    assert "chat_template_kwargs" not in first
    assert server.state.truncated == []
    assert agent.usage.prompt_tokens > 0  # usage from the last content chunk


async def test_window_capped_at_the_trained_length(mock_server: Any) -> None:
    server = webui(mock_server, [], ollama_context={"qwen3:8b": 8192})
    p = provider(server)
    try:
        profile = await resolve_model_profile(settings(server), p, "qwen3:8b")
        assert profile.context_window == 8192
        assert "trained length" in profile.context_window_source
        body = p.build_request("qwen3:8b", [], [], profile, thinking=False)
        assert body["options"] == {"num_ctx": 8192, "think": False}
        # Your own contextWindow still wins (and is what is sent).
        s = settings(server, modelProfiles=[{"match": "qwen3:8b", "contextWindow": 16384}])
        assert (await resolve_model_profile(s, p, "qwen3:8b")).context_window == 16384
    finally:
        await p.aclose()


async def test_without_num_ctx_ollama_would_truncate(mock_server: Any) -> None:
    """Why num_ctx is sent: the mock, like Ollama, drops what doesn't fit."""
    server = webui(mock_server, [{"content": "ok"}])
    p = provider(server)
    plain = resolve_profile("qwen3:32b")  # not marked as Ollama: no options sent
    from cmcoder.providers.messages import Message

    try:
        [e async for e in p.stream_chat("qwen3:32b", [Message.user("x " * 8000)], [], plain)]
    finally:
        await p.aclose()
    assert server.state.truncated == ["qwen3:32b"]


async def test_an_openai_backend_is_left_alone(mock_server: Any) -> None:
    server = webui(mock_server, [], context_window=40960, model_info=False)
    p = provider(server)
    try:
        profile = await resolve_model_profile(settings(server), p, "qwen3-vllm")
        assert profile.backend == "default" and profile.context_window == 40960  # probed
        assert "options" not in p.build_request("qwen3-vllm", [], [], profile)
    finally:
        await p.aclose()


async def test_errors_in_open_webui_format(mock_server: Any) -> None:
    server = webui(mock_server, [{"error": {"status": 400, "message": "Model not found"}}])
    p = provider(server)
    from cmcoder.providers.messages import Message

    try:
        with pytest.raises(BadRequest, match="Model not found"):
            [
                e
                async for e in p.stream_chat(
                    "qwen3:32b", [Message.user("hi")], [], resolve_profile("q")
                )
            ]
        with pytest.raises(ProviderError, match="Model not found"):
            [e async for e in p.stream_chat("nope", [Message.user("hi")], [], resolve_profile("q"))]
    finally:
        await p.aclose()


async def test_doctor_explains_open_webui(
    mock_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CMCODER_API_KEY", API_KEY)
    server = webui(
        mock_server,
        [{"content": "OK"}, {"content": "hi"}, call(("get_weather", {"city": "Paris"}))],
    )
    s = settings(server)
    assert isinstance(build_provider(s, "webui"), OpenWebUIProvider)
    console = Console(record=True, width=200)
    await Doctor(s, console).check_api("webui", ["qwen3:32b"], probe=True)
    out = console.export_text()
    assert "qwen3:32b: served by Ollama through Open WebUI" in out
    assert "context window 32,768 tokens, sent to Ollama as num_ctx" in out
    assert "thinking can be switched off (Ollama's think option)" in out
    assert "native tool calling works" in out


def test_same_index_new_id_is_a_new_call() -> None:
    calls: dict[int, dict[str, str]] = {}
    calls[_call_index({"index": 0, "id": "a"}, calls)] = {"id": "a", "name": "x", "arguments": ""}
    assert _call_index({"index": 0, "id": "b"}, calls) == 1
    assert _call_index({"index": 0}, calls) == 0  # an OpenAI-style continuation chunk
    assert _call_index({"index": 0, "id": "a"}, calls) == 0
