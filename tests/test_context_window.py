from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from cmcoder.cli.doctor import Doctor
from cmcoder.cli.factory import (
    ERROR_SOURCE,
    PROBE_SOURCE,
    load_learned_window,
    resolve_model_profile,
    save_learned_window,
)
from cmcoder.config.settings import Settings
from cmcoder.core.agent import Agent
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.protocol import events as ev
from cmcoder.providers.openai_compat import (
    ContextTooLong,
    classify_http_error,
    parse_context_window,
)
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        # vLLM
        (
            "This model's maximum context length is 32768 tokens. However, you requested "
            "10000010 tokens (10 in the messages, 10000000 in the completion).",
            32768,
        ),
        # LiteLLM wrapping vLLM, with a thousands separator
        (
            "litellm.ContextWindowExceededError: litellm.BadRequestError: OpenAIException - "
            "maximum context length is 40,960 tokens",
            40960,
        ),
        # newer vLLM wording
        ("'max_tokens' is too large: 10000000. max_model_len=65536", 65536),
        # llama.cpp
        ("the request exceeds the available context size (8192 tokens)", 8192),
        # TGI
        ("`inputs` tokens + `max_new_tokens` must have less than 4096 tokens", 4096),
        # no number stated
        ("Too many tokens in the request", None),
        # unrelated small numbers are ignored
        ("maximum context length is 12 tokens", None),
    ],
)
def test_parse_context_window(text: str, tokens: int | None) -> None:
    assert parse_context_window(text) == tokens


def test_context_errors_carry_the_window() -> None:
    body = json.dumps(
        {"error": {"message": "This model's maximum context length is 40960 tokens."}}
    )
    err = classify_http_error(400, body.encode())
    assert isinstance(err, ContextTooLong) and err.context_window == 40960


# --- probing -----------------------------------------------------------------------


async def test_probe_reads_the_limit(mock_server: Any) -> None:
    server = mock_server([], context_window=40960)
    p = make_provider(server)
    try:
        assert await p.probe_context_window("qwen3-27b", resolve_profile("qwen3-27b")) == 40960
    finally:
        await p.aclose()
    assert server.requests[0]["max_tokens"] == 10_000_000


async def test_probe_when_the_server_does_not_say(mock_server: Any) -> None:
    server = mock_server([{"content": "hi"}], context_window=100_000_000)
    p = make_provider(server)
    try:
        assert await p.probe_context_window("qwen3-27b", resolve_profile("qwen3-27b")) is None
    finally:
        await p.aclose()


def settings_for(server: Any, **extra: Any) -> Settings:
    return Settings.model_validate(
        {"providers": {"mock": {"baseUrl": server.base_url}}, "model": "mock:qwen3-27b", **extra}
    )


async def test_profile_learns_the_window_once(mock_server: Any) -> None:
    """Your gateway: no /model/info, so cmcoder asks the server once and caches it."""
    server = mock_server([], context_window=40960, model_info=False)
    settings = settings_for(server)
    p = make_provider(server)
    try:
        profile = await resolve_model_profile(settings, p, "qwen3-27b")
        assert (profile.context_window, profile.context_window_source) == (40960, PROBE_SOURCE)
        again = await resolve_model_profile(settings, p, "qwen3-27b")
        assert again.context_window == 40960
    finally:
        await p.aclose()
    probes = [r for r in server.requests if r.get("max_tokens") == 10_000_000]
    assert len(probes) == 1  # the second start used the cache
    assert load_learned_window(server.base_url, "qwen3-27b") == (40960, PROBE_SOURCE)


async def test_no_probe_when_the_window_is_known(mock_server: Any) -> None:
    server = mock_server([], context_window=40960, model_info=False)
    p = make_provider(server)
    try:
        # Set in settings: no probe, settings win.
        s = settings_for(server, modelProfiles=[{"match": "qwen3*", "contextWindow": 65536}])
        profile = await resolve_model_profile(s, p, "qwen3-27b")
        assert profile.context_window == 65536
        assert profile.context_window_source == "settings (modelProfiles)"
        assert not server.requests
        # A recent "server didn't say" is not probed again today.
        save_learned_window(server.base_url, "qwen3-7b", None, PROBE_SOURCE)
        profile = await resolve_model_profile(settings_for(server), p, "qwen3-7b")
        assert profile.context_window_source == "built-in default"
        assert not server.requests
    finally:
        await p.aclose()


async def test_model_info_server_is_not_probed(mock_server: Any) -> None:
    server = mock_server([], context_window=65536)  # serves /model/info
    p = make_provider(server)
    try:
        profile = await resolve_model_profile(settings_for(server), p, "qwen3-27b")
    finally:
        await p.aclose()
    assert (profile.context_window, profile.context_window_source) == (
        65536,
        "server (/model/info)",
    )
    assert not server.requests


# --- learning from an error during a session ---------------------------------------


async def test_session_learns_a_smaller_window_from_the_server(
    mock_server: Any, project: Path
) -> None:
    """cmcoder assumes 32K but the server only allows 20K: the first rejection
    teaches it the real limit, and the session carries on."""
    for i in range(12):
        (project / f"f{i}.txt").write_text("lorem ipsum dolor sit amet\n" * 250)
    script: list[dict[str, Any]] = [
        {"tool_calls": [{"name": "Read", "arguments": {"file_path": f"f{i}.txt"}}]}
        for i in range(12)
    ]
    server = mock_server(
        [*script, {"content": "done"}], context_window=20_000, enforce_context=True
    )
    learned: list[tuple[str, int]] = []
    agent = Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("bypassPermissions"),
        ToolContext(cwd=project, project_root=project),
        "You are a test agent.",
        on_context_window=lambda m, n: learned.append((m, n)),
    )
    try:
        events = [e async for e in agent.run("read them all")]
    finally:
        await agent.close()
    warnings = [e.message for e in events if isinstance(e, ev.Warning)]
    assert any("context window is 20,000 tokens (cmcoder assumed 32,768)" in w for w in warnings)
    assert learned == [("qwen3-27b", 20_000)]
    assert agent.profile.context_window == 20_000
    assert agent.profile.context_window_source == ERROR_SOURCE
    assert isinstance(events[-1], ev.Result) and events[-1].subtype == "success"


# --- doctor --------------------------------------------------------------------------


async def test_doctor_reports_the_window_and_its_source(mock_server: Any) -> None:
    server = mock_server([], context_window=40960, model_info=False)
    console = Console(record=True, width=200)
    doctor = Doctor(settings_for(server), console)
    p = make_provider(server)
    try:
        await doctor.check_context_window(p, "qwen3-27b")
        s = settings_for(server, modelProfiles=[{"match": "qwen3*", "contextWindow": 65536}])
        await Doctor(s, console).check_context_window(p, "qwen3-27b")
    finally:
        await p.aclose()
    out = console.export_text()
    assert "context window 40,960 tokens" in out
    assert f"server says 40,960 tokens; using 40,960 from {PROBE_SOURCE}" in out
    # A setting larger than what the server allows is flagged.
    assert "context window set to 65,536 tokens, but the server allows 40,960" in out
