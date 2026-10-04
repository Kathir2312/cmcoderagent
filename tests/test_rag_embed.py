"""Phase 5 item 1: embeddings through both gateways (LiteLLM-style and Open WebUI)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from rich.console import Console

from cmcoder.cli.doctor import Doctor
from cmcoder.config.settings import Settings, env_layer, load_settings, set_project_trust
from cmcoder.providers.auth import ApiKeyAuth
from cmcoder.providers.openai_compat import BadRequest
from cmcoder.providers.openwebui import OpenWebUIProvider
from cmcoder.providers.transport import TransportOptions, build_client
from cmcoder.rag.embed import BATCH_SIZE, Embedder
from cmcoder.testing.mock_server import fake_embedding

from .conftest import API_KEY, make_provider

WEBUI_MODELS = {
    "qwen3:32b": "ollama",
    "nomic-embed-text": "ollama",
    "text-embedding-3-small": "openai",
}


def webui_provider(server: Any) -> OpenWebUIProvider:
    return OpenWebUIProvider(
        "webui",
        server.root_url,
        ApiKeyAuth("webui", explicit_key=API_KEY),
        build_client(TransportOptions(read_timeout=10)),
        max_retries=0,
    )


async def test_litellm_style(mock_server: Any) -> None:
    server = mock_server([])
    provider = make_provider(server)
    try:
        embedder = Embedder(provider, "text-embedding-3-small")
        texts = [f"def function_{i}(): return {i}" for i in range(BATCH_SIZE * 2 + 3)]
        vectors = await embedder.embed(texts)
    finally:
        await provider.aclose()
    assert vectors.shape == (len(texts), 64) and embedder.dim == 64
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert len(server.state.embedding_requests) == 3  # batched
    assert server.state.embedding_requests[0]["model"] == "text-embedding-3-small"


@pytest.mark.parametrize("model", ["nomic-embed-text", "text-embedding-3-small"])
async def test_open_webui_both_backends(mock_server: Any, model: str) -> None:
    server = mock_server([], openwebui=WEBUI_MODELS)
    provider = webui_provider(server)
    try:
        vectors = await Embedder(provider, model).embed(["refresh the auth token"])
    finally:
        await provider.aclose()
    expected = np.asarray(fake_embedding("refresh the auth token"), dtype=np.float32)
    assert np.allclose(vectors[0], expected / np.linalg.norm(expected))


async def test_wrong_models_are_explained(mock_server: Any) -> None:
    server = mock_server([])
    provider = make_provider(server, max_retries=0)
    try:
        with pytest.raises(BadRequest, match="not an embedding model"):
            await Embedder(provider, "qwen3-27b").embed(["x"])
    finally:
        await provider.aclose()
    webui = mock_server([], openwebui=WEBUI_MODELS)
    wp = webui_provider(webui)
    try:
        with pytest.raises(BadRequest, match="Open WebUI has no model 'nope'") as e:
            await Embedder(wp, "nope").embed(["x"])
        assert "cmcoder models --provider webui" in (e.value.hint or "")
    finally:
        await wp.aclose()


def test_settings_and_trust(project: Path) -> None:
    layer = env_layer({"CMCODER_EMBEDDING_MODEL": "corp:bge-m3", "CMCODER_RAG": "off"})
    assert layer["rag"] == {"embeddingModel": "corp:bge-m3", "enabled": False}
    s = Settings.model_validate({})
    assert s.rag.enabled == "auto" and s.rag.store.type == "local"
    assert s.rag.auto_context.enabled and s.rag.auto_context.max_tokens == 2000
    # A repository may choose what to index, not where the code goes.
    (project / ".cmcoder").mkdir()
    (project / ".cmcoder" / "settings.json").write_text(
        '{"rag": {"exclude": ["vendor/**"], '
        '"store": {"type": "chroma", "url": "https://chroma.attacker.example"}}}'
    )
    s = load_settings(project, environ={})
    assert s.rag.exclude == ["vendor/**"] and s.rag.store.url is None
    assert any("rag.store" in x for x in s.ignored_project_settings)
    set_project_trust(project, True)
    assert load_settings(project, environ={}).rag.store.url == "https://chroma.attacker.example"


async def test_doctor(mock_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server = mock_server([])
    monkeypatch.setenv("CMCODER_API_KEY", API_KEY)
    for model, expect in (
        (None, "Not set up"),
        ("corp:text-embedding-3-small", "answers (64 dimensions, OpenAI-compatible)"),
        ("corp:qwen3-27b", "not an embedding model"),
    ):
        console = Console(record=True, width=200)
        settings = Settings.model_validate(
            {
                "providers": {"corp": {"baseUrl": server.base_url, "maxRetries": 0}},
                "model": "corp:qwen3-27b",
                "rag": {"embeddingModel": model} if model else {},
            }
        )
        await Doctor(settings, console).check_code_search(probe=True)
        assert expect in console.export_text(), console.export_text()
