"""File checkpoints for `/rewind`.

Before Write or Edit changes a file for the first time in a turn, its current
contents are saved (or the fact that it didn't exist yet). `/rewind` to turn
N puts every file changed since then back as it was before turn N: changed
files get their old contents, files the agent created are deleted.

Stored next to the session file:

    <session-id>.checkpoints/index.json     which file was captured in which turn
    <session-id>.checkpoints/blobs/<sha256>  the contents, stored once per version

Without a session file (persistSessions off) checkpoints live in memory for
the session. Changes made by Bash commands are not tracked: only Write and
Edit go through here.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 20 * 1024 * 1024  # bigger files aren't checkpointed


@dataclass
class Entry:
    turn: int
    path: str  # absolute
    blob: str | None  # sha256 of the old contents; None: the file didn't exist
    skipped: bool = False  # too big to keep


@dataclass
class RestoreAction:
    path: Path
    action: str  # "restored", "deleted", "skipped (too big)", "failed: …"


class Checkpoints:
    def __init__(self, folder: Path | None) -> None:
        self.folder = folder
        self.entries: list[Entry] = []
        self._memory: dict[str, bytes] = {}
        if folder is not None and (folder / "index.json").is_file():
            try:
                data = json.loads((folder / "index.json").read_text(encoding="utf-8"))
                self.entries = [Entry(**e) for e in data.get("entries", [])]
            except (OSError, ValueError, TypeError):
                self.entries = []

    # -- storage ---------------------------------------------------------------

    def _put(self, content: bytes) -> str:
        sha = hashlib.sha256(content).hexdigest()
        if self.folder is None:
            self._memory[sha] = content
            return sha
        blobs = self.folder / "blobs"
        blobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = blobs / sha
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(content)
        return sha

    def _get(self, sha: str) -> bytes:
        if self.folder is None:
            return self._memory[sha]
        return (self.folder / "blobs" / sha).read_bytes()

    def _save_index(self) -> None:
        if self.folder is None:
            return
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.folder / "index.json.tmp"
        tmp.write_text(
            json.dumps({"entries": [e.__dict__ for e in self.entries]}), encoding="utf-8"
        )
        os.replace(tmp, self.folder / "index.json")  # atomic: never a half-written index

    # -- capture and restore -----------------------------------------------------

    def capture(self, turn: int, path: Path) -> None:
        """Remember `path` as it is now, unless it was already captured this turn."""
        key = str(path.resolve())
        if any(e.turn == turn and e.path == key for e in self.entries):
            return
        p = Path(key)
        if p.is_file():
            if p.stat().st_size > MAX_FILE_BYTES:
                entry = Entry(turn, key, None, skipped=True)
            else:
                entry = Entry(turn, key, self._put(p.read_bytes()))
        else:
            entry = Entry(turn, key, None)
        self.entries.append(entry)
        try:
            self._save_index()
        except OSError:
            pass

    def changes_since(self, turn: int) -> list[Entry]:
        """For each file changed in turn `turn` or later: its state before that."""
        first: dict[str, Entry] = {}
        for e in sorted(self.entries, key=lambda e: e.turn):
            if e.turn >= turn and e.path not in first:
                first[e.path] = e
        return list(first.values())

    def files_changed_in(self, turn: int) -> int:
        return len({e.path for e in self.entries if e.turn == turn})

    def restore(self, turn: int, include_outside: Path | None = None) -> list[RestoreAction]:
        """Put files back as they were before `turn`. Files outside
        `include_outside` (the project root), if given, are left alone."""
        actions: list[RestoreAction] = []
        for e in self.changes_since(turn):
            path = Path(e.path)
            if include_outside is not None and not path.is_relative_to(include_outside):
                actions.append(RestoreAction(path, "left as is (outside the project)"))
                continue
            try:
                if e.skipped:
                    actions.append(RestoreAction(path, "skipped (too big to checkpoint)"))
                    continue
                if e.blob is None:
                    if path.exists():
                        path.unlink()
                    actions.append(RestoreAction(path, "deleted (created after that point)"))
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(self._get(e.blob))
                    actions.append(RestoreAction(path, "restored"))
            except (OSError, KeyError) as err:
                actions.append(RestoreAction(path, f"failed: {err}"))
        # Those turns are undone; later edits will be captured again.
        self.entries = [e for e in self.entries if e.turn < turn]
        try:
            self._save_index()
        except OSError:
            pass
        return actions
