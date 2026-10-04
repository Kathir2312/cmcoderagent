"""The project's code index: which files are in it, keeping it current, search.

A small manifest (per project and embedding model, under
~/.cmcoder/index/) remembers each indexed file's size, time and fingerprint,
so only changed files are embedded again. Updates are saved batch by batch:
an interrupted update keeps what it did. Files the agent edits are
re-indexed before the next search, and a search result whose file changed
since it was indexed is refreshed before it's returned.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.settings import RagConfig, Settings, config_dir
from ..providers.openai_compat import OpenAICompatProvider
from .chunker import Chunk, chunk_file
from .embed import Embedder
from .files import FileSelector, FileState
from .stores import (
    ChromaLocalStore,
    ChromaServerStore,
    Hit,
    LocalStore,
    StoreError,
    VectorStore,
)

FILES_PER_BATCH = 20
MANIFEST_VERSION = 1


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")
    return (s or "project")[:limit]


def _origin_url(root: Path) -> str | None:
    """The git remote "origin", so a shared index has the same name for everyone."""
    try:
        text = (root / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'\[remote "origin"\][^\[]*?\burl\s*=\s*(\S+)', text)
    return m.group(1) if m else None


def project_name(root: Path) -> str:
    origin = _origin_url(root)
    if origin:
        return _slug(origin.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
    return _slug(root.name)


def collection_name(root: Path, model: str) -> str:
    """For Chroma: 3-63 characters, letters, digits, `.`, `_`, `-`."""
    identity = _origin_url(root) or str(root.resolve())
    digest = hashlib.sha1(f"{identity}\0{model}".encode()).hexdigest()[:10]
    return f"cmcoder-{project_name(root)[:40]}-{digest}"


def index_folder(root: Path, model: str) -> Path:
    key = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:10]
    return config_dir() / "index" / f"{_slug(root.name)}-{key}" / _slug(model, 60)


@dataclass
class Progress:
    done: int
    total: int
    chunks: int
    current: str | None = None


@dataclass
class UpdateResult:
    indexed: int = 0  # files (re)indexed
    removed: int = 0
    chunks: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)  # unreadable, binary, too large
    seconds: float = 0.0


@dataclass
class IndexStatus:
    model: str
    store: str
    files: int
    chunks: int
    updated: float | None
    read_only: bool


class CodeIndex:
    def __init__(
        self,
        root: Path,
        cfg: RagConfig,
        embedder: Embedder,
        store: VectorStore,
        folder: Path,
        model_ref: str,
        *,
        owns_provider: bool = False,
    ) -> None:
        self.root = root.resolve()
        self.cfg = cfg
        self.embedder = embedder
        self.store = store
        self.folder = folder
        self.model_ref = model_ref
        self.read_only = cfg.store.read_only
        self.selector = FileSelector(self.root, cfg)
        self.owns_provider = owns_provider
        self._files: dict[str, FileState] = {}
        self._updated: float | None = None
        self._chunks = 0
        self._dirty: set[str] = set()
        self._lock = asyncio.Lock()
        self._load_manifest()

    # -- manifest ---------------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.folder / "manifest.json"

    def _load_manifest(self) -> None:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("version") != MANIFEST_VERSION or data.get("store") != self.store.describe():
            return  # another store or format: start again
        self._files = {
            p: FileState(int(v[0]), int(v[1]), str(v[2])) for p, v in data.get("files", {}).items()
        }
        self._updated = data.get("updated")
        self._chunks = int(data.get("chunks", 0))

    def _save_manifest(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {
            "version": MANIFEST_VERSION,
            "project": str(self.root),
            "model": self.model_ref,
            "store": self.store.describe(),
            "updated": self._updated,
            "chunks": self._chunks,
            "files": {p: [s.mtime_ns, s.size, s.sha1] for p, s in sorted(self._files.items())},
        }
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.manifest_path)

    # -- state ------------------------------------------------------------------

    def has_local_index(self) -> bool:
        return bool(self._files)

    async def available(self) -> bool:
        """Whether there is anything to search: our own index, or (a shared,
        read-only server) a collection someone else filled."""
        if self._files:
            return True
        if not self.read_only:
            return False
        try:
            return await self.store.count() > 0
        except StoreError:
            return False

    async def status(self) -> IndexStatus:
        try:
            chunks = await self.store.count()
        except StoreError:
            chunks = self._chunks
        return IndexStatus(
            model=self.model_ref,
            store=self.store.describe(),
            files=len(self._files),
            chunks=chunks,
            updated=self._updated,
            read_only=self.read_only,
        )

    # -- updating ---------------------------------------------------------------

    def _changes(self) -> tuple[list[Path], list[str], int]:
        """(files to index, indexed files gone, unchanged count): by size and
        time first, then by content for files whose time changed."""
        seen: set[str] = set()
        todo: list[Path] = []
        unchanged = 0
        for path in self.selector.files():
            rel = self.selector.rel(path)
            seen.add(rel)
            old = self._files.get(rel)
            if old is not None:
                try:
                    st = path.stat()
                except OSError:
                    continue
                if st.st_mtime_ns == old.mtime_ns and st.st_size == old.size:
                    unchanged += 1
                    continue
                try:
                    if hashlib.sha1(path.read_bytes()).hexdigest() == old.sha1:
                        self._files[rel] = FileState(st.st_mtime_ns, st.st_size, old.sha1)
                        unchanged += 1
                        continue
                except OSError:
                    continue
            todo.append(path)
        removed = [rel for rel in self._files if rel not in seen]
        return todo, removed, unchanged

    async def update(
        self,
        progress: Callable[[Progress], None] | None = None,
        *,
        paths: list[Path] | None = None,
    ) -> UpdateResult:
        """Index what changed (or just `paths`). Raises StoreError / ProviderError."""
        if self.read_only:
            raise StoreError(
                "This index is read-only (rag.store.readOnly): it's kept up to date elsewhere."
            )
        started = time.monotonic()
        async with self._lock:
            result = UpdateResult()
            if paths is None:
                todo, removed, result.unchanged = await asyncio.to_thread(self._changes)
            else:
                todo = [p for p in paths if p.exists() and self.selector.wanted(p)]
                removed = [
                    self.selector.rel(p)
                    for p in paths
                    if not p.exists() and self.selector.rel(p) in self._files
                ]
            if removed:
                await self.store.delete_paths(removed)
                for rel in removed:
                    self._files.pop(rel, None)
                result.removed = len(removed)
            total = len(todo)
            for i in range(0, total, FILES_PER_BATCH):
                batch = todo[i : i + FILES_PER_BATCH]
                chunks: list[Chunk] = []
                states: dict[str, FileState] = {}
                for path in batch:
                    rel = self.selector.rel(path)
                    read = await asyncio.to_thread(self.selector.read, path)
                    if read is None:
                        result.skipped.append(rel)
                        states[rel] = FileState(0, -1, "")  # not indexable: forget it
                        continue
                    text, state = read
                    states[rel] = state
                    chunks += chunk_file(rel, text)
                if progress is not None:
                    progress(Progress(i, total, result.chunks, batch[0].name))
                vectors = await self.embedder.embed([c.embedding_text() for c in chunks])
                await self.store.delete_paths(list(states))
                if chunks:
                    await self.store.add(chunks, vectors)
                for rel, state in states.items():
                    if state.size < 0:
                        self._files.pop(rel, None)
                    else:
                        self._files[rel] = state
                result.indexed += sum(1 for s in states.values() if s.size >= 0)
                result.chunks += len(chunks)
                self._updated = time.time()
                self._chunks = await self._count()
                self._save_manifest()
            if removed and not todo:
                self._updated = time.time()
                self._chunks = await self._count()
                self._save_manifest()
            if paths is None and not todo and not removed and self._updated is None:
                self._updated = time.time()  # an empty project: indexed, nothing in it
                self._save_manifest()
            for path in todo:
                self._dirty.discard(self.selector.rel(path))
            if progress is not None:
                progress(Progress(total, total, result.chunks))
        result.seconds = time.monotonic() - started
        return result

    async def _count(self) -> int:
        try:
            return await self.store.count()
        except StoreError:
            return self._chunks

    def note_changed(self, path: Path) -> None:
        """A file the agent wrote or edited: re-indexed before the next search."""
        try:
            if self.selector.wanted(path):
                self._dirty.add(self.selector.rel(path))
        except (ValueError, OSError):
            pass

    async def _refresh(self, rels: set[str]) -> None:
        if rels and not self.read_only and self._files:
            await self.update(paths=[self.root / r for r in sorted(rels)])

    def _stale(self, hits: list[Hit]) -> set[str]:
        out: set[str] = set()
        for hit in hits:
            old = self._files.get(hit.chunk.path)
            if old is None:
                continue
            try:
                st = (self.root / hit.chunk.path).stat()
            except OSError:
                out.add(hit.chunk.path)
                continue
            if st.st_mtime_ns != old.mtime_ns or st.st_size != old.size:
                out.add(hit.chunk.path)
        return out

    # -- searching --------------------------------------------------------------

    async def search(self, query: str, k: int = 8, path: str | None = None) -> list[Hit]:
        await self._refresh(set(self._dirty))
        vector = await self.embedder.embed_one(query)
        hits = await self.store.search(vector, k, path)
        stale = self._stale(hits) if not self.read_only else set()
        if stale:
            await self._refresh(stale)
            hits = await self.store.search(vector, k, path)
        return hits

    async def clear(self) -> None:
        await self.store.clear()
        self._files = {}
        self._chunks = 0
        self._updated = None
        self.manifest_path.unlink(missing_ok=True)

    async def close(self) -> None:
        await self.store.close()
        if self.owns_provider:
            await self.embedder.provider.aclose()


def open_store(cfg: RagConfig, root: Path, model: str, folder: Path) -> VectorStore:
    store = cfg.store
    meta: dict[str, Any] = {"cmcoder": 1, "embedding_model": model}
    if store.type == "local":
        return LocalStore(folder / "local")
    name = store.collection or collection_name(root, model)
    if store.url:
        try:
            return ChromaServerStore(
                store.url,
                name,
                headers=store.headers,
                ca_cert_path=store.ca_cert_path,
                metadata=meta,
            )
        except ValueError as e:  # a ${VAR} in the headers isn't set
            raise StoreError(f"rag.store.headers: {e}") from e
    return ChromaLocalStore(folder / "chroma", name, metadata=meta)


def open_index(
    settings: Settings,
    root: Path,
    build_provider: Callable[[Settings, str], OpenAICompatProvider],
    shared: OpenAICompatProvider | None = None,
) -> CodeIndex | None:
    """The project's index as configured, or None when code search is off or
    has no embedding model. Raises SettingsError or StoreError."""
    cfg = settings.rag
    if cfg.enabled is False or not cfg.embedding_model:
        return None
    provider_name, model = settings.resolve_model(cfg.embedding_model)
    ref = f"{provider_name}:{model}"
    folder = index_folder(root, ref)
    store = open_store(cfg, root, ref, folder)  # first: nothing to close if it fails
    owns = shared is None or shared.name != provider_name
    provider = build_provider(settings, provider_name) if owns or shared is None else shared
    return CodeIndex(root, cfg, Embedder(provider, model), store, folder, ref, owns_provider=owns)
