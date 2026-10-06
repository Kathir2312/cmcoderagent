"""Vector stores for the code index: one interface, two places.

- `LocalStore` (default): built in, a SQLite file under ~/.cmcoder/index/
  with the vectors; search is a matrix product in memory. Nothing to install,
  so it works in the standalone build. Fine up to about 100k pieces.
- `ChromaServerStore`: Chroma at a URL, running on this PC
  (http://localhost:8000) or a server shared by a team, through its REST API
  (v2) with cmcoder's own HTTP client: TLS, proxy and company CA as for the
  gateway, no extra package. (Chroma inside cmcoder, through the `chromadb`
  package, isn't offered for now: the standalone program can't include it.)

Scores are cosine similarities (vectors are unit length): 1 is identical.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np

from ..config.settings import config_dir
from ..mcp_client import expand
from ..providers.auth import load_stored_api_key
from ..providers.transport import TransportOptions, build_client
from .chunker import Chunk

BATCH = 100  # records per request to a Chroma server


class StoreError(Exception):
    """The store can't be used (unreachable, refused, not installed): say why."""


@dataclass
class Hit:
    chunk: Chunk
    score: float


class VectorStore(Protocol):
    def describe(self) -> str: ...
    async def add(self, chunks: list[Chunk], vectors: np.ndarray) -> None: ...
    async def delete_paths(self, paths: list[str]) -> None: ...
    async def search(
        self, vector: np.ndarray, k: int, path_prefix: str | None = None
    ) -> list[Hit]: ...
    async def count(self) -> int: ...
    async def clear(self) -> None: ...
    async def close(self) -> None: ...


def private_folder(path: Path) -> None:
    """Create `path` readable by the user only, like the folders above it up to
    ~/.cmcoder/index (an index holds copies of the code)."""
    path.mkdir(parents=True, exist_ok=True)
    top = config_dir() / "index"
    for folder in [path, *path.parents]:
        try:
            folder.chmod(0o700)
        except OSError:
            pass
        if folder == top or top not in folder.parents:
            break


def _prefix_ok(path: str, prefix: str | None) -> bool:
    if not prefix:
        return True
    prefix = prefix.strip("/")
    return path == prefix or path.startswith(prefix + "/")


def _metadata(c: Chunk) -> dict[str, Any]:
    return {
        "path": c.path,
        "start": c.start_line,
        "end": c.end_line,
        "language": c.language,
        "symbol": c.symbol or "",
    }


def _chunk(doc: str | None, meta: dict[str, Any] | None) -> Chunk:
    m = meta or {}
    return Chunk(
        path=str(m.get("path", "?")),
        start_line=int(m.get("start", 0)),
        end_line=int(m.get("end", 0)),
        text=doc or "",
        language=str(m.get("language", "text")),
        symbol=str(m.get("symbol") or "") or None,
    )


# --- built in ------------------------------------------------------------------


class LocalStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        private_folder(folder)
        self._db = sqlite3.connect(folder / "index.db", check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, path TEXT, "
                "start INTEGER, end_ INTEGER, language TEXT, symbol TEXT, text TEXT, vector BLOB)"
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS by_path ON chunks(path)")
            self._db.commit()
        self._matrix: np.ndarray | None = None  # loaded on first search
        self._rows: list[tuple[str, str, int, int, str, str, str]] = []

    def describe(self) -> str:
        return f"local index ({self.folder})"

    async def add(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        rows = [
            (
                c.id,
                c.path,
                c.start_line,
                c.end_line,
                c.language,
                c.symbol or "",
                c.text,
                np.asarray(v, dtype=np.float32).tobytes(),
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?)", rows)
            self._db.commit()
            self._matrix = None

    async def delete_paths(self, paths: list[str]) -> None:
        with self._lock:
            self._db.executemany("DELETE FROM chunks WHERE path = ?", [(p,) for p in paths])
            self._db.commit()
            self._matrix = None

    def _load(self) -> np.ndarray:
        with self._lock:
            if self._matrix is None:
                cur = self._db.execute(
                    "SELECT id, path, start, end_, language, symbol, text, vector FROM chunks"
                )
                rows, vectors = [], []
                for row in cur:
                    rows.append(row[:7])
                    vectors.append(np.frombuffer(row[7], dtype=np.float32))
                self._rows = rows
                self._matrix = np.vstack(vectors) if vectors else np.zeros((0, 1), np.float32)
            return self._matrix

    async def search(self, vector: np.ndarray, k: int, path_prefix: str | None = None) -> list[Hit]:
        matrix = await asyncio.to_thread(self._load)
        if not len(self._rows) or matrix.shape[1] != vector.shape[0]:
            return []
        scores = matrix @ vector.astype(np.float32)
        order = np.argsort(-scores)
        hits: list[Hit] = []
        for i in order:
            _, path, start, end, language, symbol, text = self._rows[int(i)]
            if not _prefix_ok(path, path_prefix):
                continue
            chunk = Chunk(path, start, end, text, language, symbol or None)
            hits.append(Hit(chunk, float(scores[int(i)])))
            if len(hits) >= k:
                break
        return hits

    async def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    async def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM chunks")
            self._db.commit()
            self._matrix = None

    async def close(self) -> None:
        with self._lock:
            self._db.close()


# --- a Chroma server -----------------------------------------------------------


def chroma_key_name(url: str) -> str:
    """Where `cmcoder rag setup` keeps a Chroma server's API key (keychain account)."""
    return "chroma:" + url.rstrip("/")


class ChromaServerStore:
    def __init__(
        self,
        url: str,
        collection: str,
        *,
        headers: dict[str, str] | None = None,
        ca_cert_path: str | None = None,
        tenant: str = "default_tenant",
        database: str = "default_database",
        client: httpx.AsyncClient | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.collection = collection
        self.metadata = metadata or {}
        sent = {k: expand(v) for k, v in (headers or {}).items()}
        key = os.environ.get("CMCODER_CHROMA_API_KEY") or load_stored_api_key(
            chroma_key_name(self.url)
        )
        if key and not any(h.lower() in ("authorization", "x-chroma-token") for h in sent):
            sent["Authorization"] = f"Bearer {key}"
        self.client = client or build_client(
            TransportOptions(ca_cert_path=ca_cert_path, read_timeout=60), headers=sent
        )
        self.base = f"{self.url}/api/v2/tenants/{tenant}/databases/{database}/collections"
        self._id: str | None = None

    def describe(self) -> str:
        return f"Chroma server {self.url} (collection {self.collection})"

    async def _call(self, method: str, url: str, body: Any = None) -> Any:
        try:
            resp = await self.client.request(method, url, json=body)
        except httpx.HTTPError as e:
            hint = ""
            if httpx.URL(self.url).host in ("localhost", "127.0.0.1", "::1"):
                hint = (
                    " Is Chroma running on this PC? Start it first, e.g. "
                    "`docker run -d -p 127.0.0.1:8000:8000 -v chroma-data:/data chromadb/chroma`."
                )
            raise StoreError(f"Can't reach the Chroma server {self.url}: {e}.{hint}") from e
        if resp.status_code in (401, 403):
            raise StoreError(
                f"The Chroma server {self.url} refused the request ({resp.status_code}). "
                "Check its API key: `cmcoder rag setup`, or CMCODER_CHROMA_API_KEY."
            )
        if resp.status_code >= 400:
            raise StoreError(f"Chroma server error {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else None

    async def _collection(self) -> str:
        if self._id is None:
            body = {
                "name": self.collection,
                "get_or_create": True,
                "configuration": {"hnsw": {"space": "cosine"}},
                "metadata": self.metadata or None,
            }
            data = await self._call("POST", self.base, body)
            self._id = str(data["id"])
        return self._id

    async def check(self) -> None:
        """Reachable, and the key is accepted (listing needs it; the heartbeat doesn't)."""
        await self._call("GET", f"{self.url}/api/v2/heartbeat")
        await self._call("GET", f"{self.base}?limit=1")

    async def add(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        cid = await self._collection()
        for i in range(0, len(chunks), BATCH):
            part = chunks[i : i + BATCH]
            body = {
                "ids": [c.id for c in part],
                "embeddings": [[float(x) for x in v] for v in vectors[i : i + BATCH]],
                "documents": [c.text for c in part],
                "metadatas": [_metadata(c) for c in part],
            }
            await self._call("POST", f"{self.base}/{cid}/upsert", body)

    async def delete_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        cid = await self._collection()
        for i in range(0, len(paths), BATCH):
            where = {"path": {"$in": paths[i : i + BATCH]}}
            await self._call("POST", f"{self.base}/{cid}/delete", {"where": where})

    async def search(self, vector: np.ndarray, k: int, path_prefix: str | None = None) -> list[Hit]:
        cid = await self._collection()
        body = {
            "query_embeddings": [[float(x) for x in vector]],
            "n_results": k * 4 if path_prefix else k,
            "include": ["documents", "metadatas", "distances"],
        }
        data = await self._call("POST", f"{self.base}/{cid}/query", body)
        hits: list[Hit] = []
        ids = (data or {}).get("ids") or [[]]
        docs = (data.get("documents") or [[None] * len(ids[0])])[0]
        metas = (data.get("metadatas") or [[None] * len(ids[0])])[0]
        dists = (data.get("distances") or [[1.0] * len(ids[0])])[0]
        for doc, meta, dist in zip(docs, metas, dists, strict=False):
            chunk = _chunk(doc, meta)
            if _prefix_ok(chunk.path, path_prefix):
                hits.append(Hit(chunk, 1.0 - float(dist if dist is not None else 1.0)))
        return hits[:k]

    async def count(self) -> int:
        cid = await self._collection()
        return int(await self._call("GET", f"{self.base}/{cid}/count") or 0)

    async def clear(self) -> None:
        try:
            await self._call("DELETE", f"{self.base}/{self.collection}")
        except StoreError as e:
            if "404" not in str(e):  # no collection yet: nothing to clear
                raise
        self._id = None

    async def close(self) -> None:
        await self.client.aclose()


# --- Chroma in cmcoder itself: not offered for now ----------------------------


def chroma_needs_url() -> str:
    """Chroma is always used through its URL (for now); what to set instead.

    Chroma inside cmcoder (the `chromadb` package) isn't part of the
    standalone program, which developers install without Python, so it isn't
    offered. A Chroma on the same PC works through its URL like any server.
    """
    return (
        'Chroma needs its URL: "store": {"type": "chroma", "url": "http://localhost:8000"} '
        "for a Chroma running on this PC, or your Chroma server's address. "
        'Or use the built-in index ("store": {"type": "local"}). `cmcoder rag setup` sets either.'
    )
