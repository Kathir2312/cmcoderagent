"""cmcoder through a real Open WebUI, in front of the mock model server (an
OpenAI-compatible backend) and a stand-in Ollama (tests/fake_ollama.py).

Open WebUI is a large install, so this runs only when CMCODER_TEST_OPENWEBUI
points at its `open-webui` executable (the "Open WebUI" CI workflow, or by hand:
`uv venv /tmp/owui && uv pip install --python /tmp/owui/bin/python open-webui`,
then `CMCODER_TEST_OPENWEBUI=/tmp/owui/bin/open-webui uv run pytest tests/test_openwebui_real.py`).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cmcoder.cli.factory import resolve_model_profile
from cmcoder.config.settings import Settings
from cmcoder.core.agent import Agent
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.protocol import events as ev
from cmcoder.providers.auth import ApiKeyAuth
from cmcoder.providers.openwebui import OpenWebUIProvider
from cmcoder.providers.transport import TransportOptions, build_client
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import API_KEY, start_mock
from .fake_ollama import FakeOllama

OPENWEBUI = os.environ.get("CMCODER_TEST_OPENWEBUI")
pytestmark = pytest.mark.skipif(not OPENWEBUI, reason="set CMCODER_TEST_OPENWEBUI to run")

TWO_READS = {
    "tool_calls": [
        {"name": "Read", "arguments": {"file_path": "a.txt"}},
        {"name": "Read", "arguments": {"file_path": "b.txt"}},
    ]
}
DONE = {"content": "Read both: A and B.", "thinking": "Short files."}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _post(url: str, data: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    req = urllib.request.Request(url, json.dumps(data).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=30) as resp:
        return json.loads(resp.read())


@pytest.fixture(scope="module")
def openwebui(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """A running Open WebUI with an admin's API key; its backends' recorders."""
    assert OPENWEBUI
    backend = start_mock(
        [dict(TWO_READS), {"content": DONE["content"]}],
        models=["qwen3-27b", "qwen3-7b", "text-embedding-3-small"],
    )
    ollama = FakeOllama("qwen3:8b", [dict(TWO_READS), dict(DONE)]).__enter__()
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update(
        {
            "DATA_DIR": str(tmp_path_factory.mktemp("owui-data")),
            "WEBUI_SECRET_KEY": "test-secret",
            "ENABLE_OLLAMA_API": "True",
            "OLLAMA_BASE_URL": ollama.url,
            "OPENAI_API_BASE_URL": backend.base_url,
            "OPENAI_API_KEY": API_KEY,
            "ENABLE_API_KEYS": "True",
            "OFFLINE_MODE": "True",
            "BYPASS_EMBEDDING_AND_RETRIEVAL": "True",
            "HF_HUB_OFFLINE": "1",
        }
    )
    log = tmp_path_factory.mktemp("owui-log") / "open-webui.log"
    with open(log, "w") as out:
        proc = subprocess.Popen(
            [OPENWEBUI, "serve", "--host", "127.0.0.1", "--port", str(port)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 240
        while True:
            try:
                with opener.open(f"{url}/health", timeout=5) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() > deadline or proc.poll() is not None:
                pytest.fail(f"Open WebUI didn't start:\n{log.read_text()[-3000:]}")
            time.sleep(2)
        account = {"name": "Admin", "email": "admin@example.invalid", "password": "Passw0rd-test!"}
        token = _post(f"{url}/api/v1/auths/signup", account)["token"]
        key = _post(f"{url}/api/v1/auths/api_key", {}, token)["api_key"]
        yield {"url": url, "key": key, "backend": backend, "ollama": ollama}
    finally:
        proc.terminate()
        proc.wait(timeout=30)
        ollama.__exit__(None, None, None)
        backend.__exit__(None, None, None)


async def _two_reads(openwebui: dict[str, Any], project: Path, model: str) -> list[Any]:
    (project / "a.txt").write_text("A\n")
    (project / "b.txt").write_text("B\n")
    provider = OpenWebUIProvider(
        "webui",
        openwebui["url"],
        ApiKeyAuth("webui", explicit_key=openwebui["key"]),
        build_client(TransportOptions(read_timeout=60)),
    )
    settings = Settings.model_validate(
        {"providers": {"webui": {"type": "openwebui", "baseUrl": openwebui["url"]}}}
    )
    assert model in await provider.list_models()
    profile = await resolve_model_profile(settings, provider, model, probe=False)
    agent = Agent(
        provider,
        model,
        profile,
        default_tools(),
        PermissionPolicy("default"),
        ToolContext(cwd=project, project_root=project),
        "test",
    )
    try:
        return [e async for e in agent.run("read a.txt and b.txt")]
    finally:
        await agent.close()


def _contents(events: list[Any]) -> list[str]:
    return [e.content.split("\t")[-1].strip() for e in events if isinstance(e, ev.ToolResult)]


async def test_an_ollama_model(openwebui: dict[str, Any], project: Path) -> None:
    events = await _two_reads(openwebui, project, "qwen3:8b")
    assert _contents(events) == ["A", "B"]  # two calls, though Ollama numbers neither
    assert events[-1].result == "Read both: A and B." and not events[-1].is_error
    received = openwebui["ollama"].requests
    assert [r["options"]["num_ctx"] for r in received] == [32768, 32768]
    assert len(received[0]["tools"]) == len(default_tools())


async def test_an_openai_compatible_backend(openwebui: dict[str, Any], project: Path) -> None:
    events = await _two_reads(openwebui, project, "qwen3-27b")
    assert _contents(events) == ["A", "B"]
    assert events[-1].result == "Read both: A and B."
    received = openwebui["backend"].requests
    assert "options" not in received[0]
    assert {t["function"]["name"] for t in received[0]["tools"]} >= {"Read", "Edit", "Bash"}


@pytest.mark.parametrize(
    ("model", "backend"), [("nomic-embed-text", "ollama"), ("text-embedding-3-small", "backend")]
)
async def test_embeddings_for_code_search(
    openwebui: dict[str, Any], model: str, backend: str
) -> None:
    """Phase 5: /api/embeddings, for an Ollama model and an OpenAI-compatible one."""
    from cmcoder.rag.embed import Embedder
    from cmcoder.testing.mock_server import fake_embedding

    provider = OpenWebUIProvider(
        "webui",
        openwebui["url"],
        ApiKeyAuth("webui", explicit_key=openwebui["key"]),
        build_client(TransportOptions(read_timeout=60)),
    )
    try:
        vectors = await Embedder(provider, model).embed(["def refresh_token(): ...", "class X"])
    finally:
        await provider.aclose()
    assert vectors.shape == (2, 64)
    assert abs(float(vectors[0][0]) - fake_embedding("def refresh_token(): ...")[0]) < 1e-4
    if backend == "ollama":
        assert openwebui["ollama"].embed_requests[-1]["model"] == model
    else:
        assert openwebui["backend"].state.embedding_requests[-1]["model"] == model
