"""Which files of the project go into the index.

The project's text files, as Grep sees them (hidden folders, node_modules and
.gitignore'd files skipped), limited by `rag.include` / `rag.exclude`. Never a
secret file (`.env`, keys, `secrets/`: the same rules as the Read tool), a
link to outside the project, a binary or generated file, or one larger than
`rag.maxFileBytes`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pathspec

from ..config.settings import RagConfig
from ..sensitive import safe_project_file
from ..tools.search import walk_files
from .chunker import language_of

# Generated, minified or lock files: large, and nothing to find by meaning.
SKIPPED = pathspec.GitIgnoreSpec.from_lines(
    [
        "*.min.js",
        "*.min.css",
        "*.map",
        "*.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "*.pb.go",
        "*_pb2.py",
        "*.generated.*",
        "dist/",
        "build/",
        "out/",
        "target/",
        "coverage/",
        "*.svg",
        "*.ipynb",
    ]
)
LONG_LINE = 1000  # an average line this long: minified or data, not code


@dataclass(frozen=True)
class FileState:
    """What the index remembers about a file, to see whether it changed."""

    mtime_ns: int
    size: int
    sha1: str


def _spec(patterns: list[str]) -> pathspec.PathSpec | None:
    return pathspec.GitIgnoreSpec.from_lines(patterns) if patterns else None


class FileSelector:
    def __init__(self, root: Path, cfg: RagConfig) -> None:
        self.root = root.resolve()
        self.cfg = cfg
        self.include = _spec(cfg.include)
        self.exclude = _spec(cfg.exclude)

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def wanted(self, path: Path) -> bool:
        """By name and place only (no reading): would this file be indexed?"""
        try:
            rel = self.rel(path)
        except (ValueError, OSError):
            return False
        if any(part.startswith(".") for part in rel.split("/")):
            return False
        if self.include is not None and not self.include.match_file(rel):
            return False
        if self.exclude is not None and self.exclude.match_file(rel):
            return False
        if SKIPPED.match_file(rel) or language_of(rel) is None:
            return False
        return safe_project_file(path, self.root)

    def files(self) -> Iterator[Path]:
        for path in walk_files(self.root):
            if self.wanted(path):
                yield path

    def read(self, path: Path) -> tuple[str, FileState] | None:
        """The file's text and state, or None if it isn't indexable text."""
        try:
            st = path.stat()
            if st.st_size > self.cfg.max_file_bytes or st.st_size == 0:
                return None
            data = path.read_bytes()
        except OSError:
            return None
        if b"\x00" in data[:8192]:
            return None
        text = data.decode("utf-8", "replace")
        lines = text.count("\n") + 1
        if len(text) / lines > LONG_LINE:
            return None
        return text, FileState(st.st_mtime_ns, st.st_size, hashlib.sha1(data).hexdigest())
