"""cmcoder -> real LiteLLM proxy -> scripted vLLM-style backend.

Checks the gateway assumptions in docs/DESIGN.md §4.7 against real LiteLLM.
Opt-in: runs when a LiteLLM proxy is installed, e.g.

    uv venv /tmp/litellm && uv pip install --python /tmp/litellm/bin/python 'litellm[proxy]'
    LITELLM_BIN=/tmp/litellm/bin/litellm uv run pytest tests/test_litellm_integration.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from cmcoder.providers.auth import ApiKeyAuth
from cmcoder.providers.messages import Message, StreamDone, ToolSpec
from cmcoder.providers.openai_compat import OpenAICompatProvider
from cmcoder.providers.profiles import resolve_profile
from cmcoder.providers.transport import TransportOptions, build_client
from cmcoder.testing.mock_server import MockServer, MockState

LITELLM = os.environ.get("LITELLM_BIN") or shutil.which("litellm")
pytestmark = pytest.mark.skipif(not LITELLM, reason="LiteLLM proxy not installed (set LITELLM_BIN)")

MASTER_KEY = "sk-master-test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, MockServer]]:
    # The backend allows 40960 tokens; LiteLLM's config claims 32768 (as configs drift).
    backend = MockServer(MockState([], context_window=40960))
    backend.__enter__()
    d = tmp_path_factory.mktemp("litellm")
    cfg = d / "config.yaml"
    cfg.write_text(
        f"""
model_list:
  - model_name: qwen3-27b
    litellm_params: {{model: hosted_vllm/Qwen/Qwen3-27B, api_base: "{backend.base_url}", api_key: sk-backend}}
    model_info: {{max_input_tokens: 32768, max_output_tokens: 8192, supports_function_calling: true}}
  - model_name: text-embedding-3-small
    litellm_params: {{model: openai/text-embedding-3-small, api_base: "{backend.base_url}", api_key: sk-backend}}
    model_info: {{mode: embedding}}
general_settings: {{master_key: {MASTER_KEY}}}
"""
    )
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    log = open(d / "proxy.log", "w")  # noqa: SIM115
    assert LITELLM
    proxy = subprocess.Popen(
        [LITELLM, "--config", str(cfg), "--port", str(port)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**env, "LITELLM_LOG": "ERROR"},
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):
            try:
                if httpx.get(f"{base}/health/liveliness", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if proxy.poll() is not None:
                break
            time.sleep(0.5)
        else:
            pytest.fail("LiteLLM proxy did not start: " + (d / "proxy.log").read_text()[-2000:])
        yield f"{base}/v1", backend
    finally:
        proxy.terminate()
        proxy.wait(15)
        backend.__exit__(None, None, None)
        log.close()


def provider(base_url: str) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        "corp",
        base_url,
        ApiKeyAuth("corp", explicit_key=MASTER_KEY),
        build_client(TransportOptions(read_timeout=60)),
        max_retries=0,
    )


async def test_models_and_model_info(gateway: tuple[str, MockServer]) -> None:
    base, _ = gateway
    p = provider(base)
    try:
        assert "qwen3-27b" in await p.list_models()
        info = await p.model_info()
    finally:
        await p.aclose()
    assert info["qwen3-27b"]["max_input_tokens"] == 32768
    assert info["qwen3-27b"]["supports_function_calling"] is True


async def test_streaming_reasoning_tools_and_thinking_switch(
    gateway: tuple[str, MockServer],
) -> None:
    base, backend = gateway
    backend.state.script = [
        {
            "reasoning": "think",
            "content": "Hello",
            "tool_calls": [{"name": "Read", "arguments": {"file_path": "a.py"}}],
        },
        {"content": "quick"},
    ]
    backend.state.requests.clear()
    p = provider(base)
    profile = resolve_profile("qwen3-27b", overrides=[{"parallelToolCalls": False}])
    tool = ToolSpec(
        "Read", "read", {"type": "object", "properties": {"file_path": {"type": "string"}}}
    )
    try:
        events = [
            e
            async for e in p.stream_chat(
                "qwen3-27b", [Message.user("hi")], [tool], profile, max_tokens=2000
            )
        ]
        quick = [
            e
            async for e in p.stream_chat(
                "qwen3-27b", [Message.user("hi")], [], profile, thinking=False
            )
        ]
    finally:
        await p.aclose()
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.message.reasoning == "think" and done.message.content == "Hello"
    assert done.message.tool_calls[0].name == "Read"
    assert json.loads(done.message.tool_calls[0].arguments) == {"file_path": "a.py"}
    assert not done.usage.estimated
    assert isinstance(quick[-1], StreamDone) and quick[-1].message.content == "quick"
    first, second = backend.requests
    # What LiteLLM forwarded to the backend:
    assert first["max_tokens"] == 2000
    assert first["parallel_tool_calls"] is False
    assert second["chat_template_kwargs"] == {"enable_thinking": False}


def test_cmcoder_print_mode_through_gateway(
    gateway: tuple[str, MockServer], tmp_path: Path
) -> None:
    base, backend = gateway
    backend.state.script = [
        {
            "tool_calls": [
                {
                    "name": "Write",
                    "arguments": {"file_path": "hello.py", "content": "print('hi')\n"},
                }
            ]
        },
        {"content": "Created hello.py."},
    ]
    (tmp_path / ".git").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update(CMCODER_BASE_URL=base, CMCODER_API_KEY=MASTER_KEY, CMCODER_MODEL="qwen3-27b")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "cmcoder",
            "-p",
            "create hello.py",
            "--permission-mode",
            "acceptEdits",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Created hello.py."
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"


async def test_context_window_probe_through_gateway(gateway: tuple[str, MockServer]) -> None:
    """LiteLLM passes the backend's "maximum context length" error through, so
    cmcoder learns the limit the backend really enforces."""
    from cmcoder.providers.profiles import resolve_profile

    base_url, _ = gateway
    p = provider(base_url)
    try:
        assert await p.probe_context_window("qwen3-27b", resolve_profile("qwen3-27b")) == 40960
    finally:
        await p.aclose()


async def test_embeddings_through_litellm(gateway: tuple[str, MockServer]) -> None:
    """Phase 5: code-search embeddings go through the gateway's /v1/embeddings."""
    from cmcoder.rag.embed import Embedder
    from cmcoder.testing.mock_server import fake_embedding

    base, backend = gateway
    p = provider(base)
    try:
        embedder = Embedder(p, "text-embedding-3-small")
        vectors = await embedder.embed(["def refresh_token(): ...", "class Parser: ..."])
    finally:
        await p.aclose()
    assert vectors.shape == (2, 64)
    expected = fake_embedding("def refresh_token(): ...")
    assert abs(float(vectors[0] @ vectors[0]) - 1.0) < 1e-5
    assert abs(float(vectors[0][0]) - expected[0]) < 1e-4
    assert backend.state.embedding_requests[-1]["model"] == "text-embedding-3-small"
