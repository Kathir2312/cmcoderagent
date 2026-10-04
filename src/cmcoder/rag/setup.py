"""Setting up code search, shared by `cmcoder rag setup` and the VS Code
extension (through the protocol), so both write the same settings.

The steps: pick an embedding model from the gateway (checked with a test
request), pick where the index lives (this machine, Chroma here, or a Chroma
server, checked with a heartbeat; its API key goes to the keychain, never a
file), and pick whose settings to write: yours, or the project's (shared
with the team; a server named there needs each person's project trust).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..config.settings import (
    Settings,
    update_project_settings,
    update_user_settings,
)
from ..providers.auth import store_api_key
from ..providers.openai_compat import OpenAICompatProvider, ProviderError
from .embed import Embedder
from .index import CodeIndex
from .stores import ChromaServerStore, StoreError, chroma_key_name

StoreKind = Literal["local", "chroma", "chroma-server"]
Scope = Literal["user", "project"]

# Names that usually mean an embedding model (listed first).
_EMBEDDING_NAME = re.compile(
    r"embed|bge|e5-|gte|minilm|mxbai|nomic|jina|snowflake-arctic|text-embedding|voyage",
    re.IGNORECASE,
)


@dataclass
class SetupChoice:
    embedding_model: str  # "provider:model"
    store: StoreKind = "local"
    url: str | None = None  # chroma-server
    api_key: str | None = None  # chroma-server; goes to the keychain
    scope: Scope = "user"
    read_only: bool = False


def looks_like_embedding_model(name: str) -> bool:
    return bool(_EMBEDDING_NAME.search(name))


async def embedding_candidates(
    settings: Settings, build_provider: Callable[[Settings, str], OpenAICompatProvider]
) -> tuple[list[str], list[str], dict[str, str]]:
    """(likely embedding models, other models, errors by provider), as
    "provider:model", from every configured gateway."""
    likely: list[str] = []
    other: list[str] = []
    errors: dict[str, str] = {}

    async def one(name: str) -> None:
        provider = build_provider(settings, name)
        try:
            models = await provider.list_models()
        except ProviderError as e:
            errors[name] = str(e)
            return
        finally:
            await provider.aclose()
        for m in models:
            (likely if looks_like_embedding_model(m) else other).append(f"{name}:{m}")

    await asyncio.gather(*(one(name) for name in settings.providers))
    return sorted(likely), sorted(other), errors


async def check_embedding_model(
    settings: Settings,
    ref: str,
    build_provider: Callable[[Settings, str], OpenAICompatProvider],
) -> tuple[str, int]:
    """("provider:model", vector size) after a test request; raises
    ProviderError (or SettingsError for an unknown provider)."""
    name, model = settings.resolve_model(ref)
    provider = build_provider(settings, name)
    try:
        embedder = Embedder(provider, model)
        await embedder.embed(["def check(): return 'cmcoder code search'"])
    finally:
        await provider.aclose()
    assert embedder.dim is not None
    return f"{name}:{model}", embedder.dim


async def check_store(choice: SetupChoice) -> str:
    """A short description of the store after checking it can be used; raises StoreError."""
    if choice.store == "local":
        return "the built-in index on this machine"
    if choice.store == "chroma":
        try:
            import chromadb  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as e:
            raise StoreError(
                "Chroma on this machine needs the chromadb package: "
                "`uv tool install --force --reinstall cmcoder[chroma]`. Or choose the built-in "
                "index or a Chroma server."
            ) from e
        return "Chroma on this machine"
    if not choice.url:
        raise StoreError(
            "A Chroma server needs its address (e.g. https://chroma.example.com:8000)."
        )
    headers = {"Authorization": f"Bearer {choice.api_key}"} if choice.api_key else {}
    store = ChromaServerStore(choice.url, "cmcoder-check", headers=headers)
    try:
        await store.check()  # writes nothing
    finally:
        await store.close()
    return f"the Chroma server {choice.url}"


def rag_block(choice: SetupChoice) -> dict[str, Any]:
    store: dict[str, Any] = {"type": "local" if choice.store == "local" else "chroma"}
    if choice.store == "chroma-server":
        store["url"] = choice.url
        if choice.read_only:
            store["readOnly"] = True
    return {"enabled": True, "embeddingModel": choice.embedding_model, "store": store}


def apply_setup(choice: SetupChoice, project_root: Path) -> Path:
    """Write the settings (and the server's key to the keychain). Returns the file written."""
    if choice.store == "chroma-server" and choice.url and choice.api_key:
        store_api_key(chroma_key_name(choice.url), choice.api_key)
    block = rag_block(choice)

    def change(data: dict[str, Any]) -> None:
        old = data.get("rag")
        rag: dict[str, Any] = old if isinstance(old, dict) else {}
        data["rag"] = {**rag, **block}

    if choice.scope == "project":
        return update_project_settings(project_root, change)
    return update_user_settings(change)


def set_enabled(enabled: bool) -> Path:
    """`cmcoder rag on|off`: in your own settings."""

    def change(data: dict[str, Any]) -> None:
        old = data.get("rag")
        rag: dict[str, Any] = old if isinstance(old, dict) else {}
        data["rag"] = {**rag, "enabled": enabled}

    return update_user_settings(change)


def age(ts: float | None) -> str:
    if not ts:
        return "never"
    seconds = int(time.time() - ts)
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds // 60} minutes ago"
    if seconds < 2 * 86400:
        return f"{seconds // 3600} hours ago"
    return f"{seconds // 86400} days ago"


async def status_lines(index: CodeIndex) -> list[str]:
    """The index's state, for /index status, `cmcoder index --status` and VS Code."""
    st = await index.status()
    cfg = index.cfg.auto_context
    auto = f"on (top {cfg.top_k}, up to {cfg.max_tokens:,} tokens)" if cfg.enabled else "off"
    lines = [
        f"Embedding model    {st.model}",
        f"Index              {st.store}{' (read-only)' if st.read_only else ''}",
        f"Files, pieces      {st.files:,} files, {st.chunks:,} pieces",
        f"Updated            {age(st.updated)}" + (" (updating now)" if index.updating else ""),
        f"Automatic context  {auto}",
    ]
    if index.last_error:
        lines.append(f"Last update failed: {index.last_error}")
    return lines
