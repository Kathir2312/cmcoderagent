"""Open WebUI as a gateway (provider `"type": "openwebui"`).

Open WebUI serves an OpenAI-compatible API under `/api`: `/api/models` and
`/api/chat/completions`, with an API key from the user's account (Settings →
Account; an admin enables API keys). The models behind it come from Ollama or
from OpenAI-compatible servers (vLLM, LiteLLM, ...). What differs from a
plain OpenAI-compatible server, read from Open WebUI's own code (0.11):

- `/api/models` lists every model with `owned_by` ("ollama", "openai",
  "arena", ...); a model's settings (`params`) are deliberately left out.
- Tools in a request are passed to the model unchanged (cmcoder never uses
  Open WebUI's own tools), for both kinds of backend.
- For an Ollama model, Open WebUI converts the request: `options` go to Ollama
  (`num_ctx`, and `think` moved to the top level). Ollama silently drops the
  start of a prompt longer than its `num_ctx`, so cmcoder always sends its own
  window (capped at the model's trained maximum from `/ollama/api/show`).
- The stream of an Ollama model is converted to OpenAI chunks: tool calls
  arrive whole, each numbered 0 with its own id; usage comes with the last
  content chunk; an empty chunk mid-stream says `finish_reason: "stop"`.
  `openai_compat` handles all of that.
- Errors are `{"detail": "..."}`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .auth import AuthProvider
from .openai_compat import BadRequest, OpenAICompatProvider, ProviderError, ServerError
from .profiles import ModelProfile

# Model kinds in /api/models that aren't one model to talk to.
SKIPPED_OWNERS = {"arena"}


def api_base(server_url: str) -> str:
    """`https://webui`, `.../api` or `.../api/v1` -> `https://webui/api`."""
    url = server_url.rstrip("/")
    for suffix in ("/api/v1", "/api"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return f"{url}/api"


class OpenWebUIProvider(OpenAICompatProvider):
    kind = "openwebui"

    def __init__(
        self,
        name: str,
        base_url: str,
        auth: AuthProvider,
        client: httpx.AsyncClient,
        *,
        max_retries: int = 2,
    ) -> None:
        super().__init__(name, api_base(base_url), auth, client, max_retries=max_retries)
        self.server_url = self.base_url[: -len("/api")]

    async def _models(self) -> list[dict[str, Any]]:
        data = await self._get_json(f"{self.base_url}/models")
        return [
            m
            for m in data.get("data", [])
            if isinstance(m, dict) and m.get("id") and m.get("owned_by") not in SKIPPED_OWNERS
        ]

    async def list_models(self) -> list[str]:
        return sorted(str(m["id"]) for m in await self._models())

    async def model_info(self) -> dict[str, dict[str, Any]]:
        """Per model: which backend serves it ("ollama" or "openai"), and for a
        custom model (a preset in Open WebUI) the Ollama model it is based on."""
        out: dict[str, dict[str, Any]] = {}
        for m in await self._models():
            entry: dict[str, Any] = {"backend": str(m.get("owned_by") or "openai")}
            info = m.get("info")
            if isinstance(info, dict) and info.get("base_model_id"):
                entry["base_model"] = str(info["base_model_id"])
            out[str(m["id"])] = entry
        return out

    async def ollama_context_length(self, model: str, timeout: float = 3.0) -> int | None:
        """The Ollama model's trained context length (`/ollama/api/show`), or
        None if Open WebUI doesn't say (no access, Ollama disabled, ...)."""

        async def go() -> int | None:
            try:
                resp = await self.client.post(
                    f"{self.server_url}/ollama/api/show",
                    json={"model": model},
                    headers=await self.auth.get_headers(),
                )
            except Exception:  # best effort: a missing answer only means "unknown"
                return None
            if resp.status_code != 200:
                return None
            try:
                data = resp.json()
            except ValueError:
                return None
            info = data.get("model_info") if isinstance(data, dict) else None
            if not isinstance(info, dict):
                return None
            for key, value in info.items():
                if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                    return value
            return None

        try:
            return await asyncio.wait_for(go(), timeout)
        except TimeoutError:
            return None

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        # Open WebUI answers a bare 500 for a model it doesn't know: say so.
        try:
            return await super().embed(model, texts)
        except ServerError as err:
            try:
                known = model in await self.list_models()
            except ProviderError:
                raise err from None
            if known:
                raise
            raise BadRequest(
                f"Open WebUI has no model {model!r} (it answered: {err})",
                hint=f"Embedding models it offers are among `cmcoder models --provider {self.name}`.",
            ) from None

    async def probe_context_window(
        self, model: str, profile: ModelProfile, timeout: float = 8.0
    ) -> int | None:
        # Ollama never refuses a long prompt (it drops the start), so a probe
        # learns nothing there; for an OpenAI-compatible backend it works as usual.
        if profile.backend == "ollama":
            return None
        return await super().probe_context_window(model, profile, timeout)
