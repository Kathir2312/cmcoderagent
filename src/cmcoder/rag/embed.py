"""Embeddings for code search, from the gateway's embedding model.

The same call works with LiteLLM (`/v1/embeddings`) and Open WebUI
(`/api/embeddings`, which passes Ollama models on to Ollama): see
`OpenAICompatProvider.embed`.
"""

from __future__ import annotations

import numpy as np

from ..providers.openai_compat import OpenAICompatProvider

# Texts per request, and the longest text sent (characters): embedding
# models take a few thousand tokens at most; a chunk is far smaller anyway.
BATCH_SIZE = 32
MAX_CHARS = 6000


class Embedder:
    def __init__(self, provider: OpenAICompatProvider, model: str) -> None:
        self.provider = provider
        self.model = model
        self.dim: int | None = None

    @property
    def gateway(self) -> str:
        """For messages: which gateway answers ("LiteLLM-compatible" or "Open WebUI")."""
        return "Open WebUI" if self.provider.kind == "openwebui" else "OpenAI-compatible"

    async def embed(self, texts: list[str]) -> np.ndarray:
        """Unit-length float32 vectors, one row per text."""
        rows: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = [t[:MAX_CHARS] or " " for t in texts[i : i + BATCH_SIZE]]
            rows += await self.provider.embed(self.model, batch)
        if not rows:
            return np.zeros((0, self.dim or 0), dtype=np.float32)
        matrix = np.asarray(rows, dtype=np.float32)
        if self.dim is None:
            self.dim = int(matrix.shape[1])
        elif matrix.shape[1] != self.dim:
            raise ValueError(
                f"{self.model} returned vectors of size {matrix.shape[1]}, earlier {self.dim}"
            )
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    async def embed_one(self, text: str) -> np.ndarray:
        return (await self.embed([text]))[0]
