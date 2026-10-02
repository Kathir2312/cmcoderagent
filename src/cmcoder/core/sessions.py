"""Saved conversations: `--continue`, `--resume <id>` and `/resume`.

Each session is a JSON Lines file (one JSON object per line):

    ~/.cmcoder/projects/<project-hash>/<session-id>.jsonl

Records:
    {"type": "meta", "session_id", "cwd", "model", "created"}
    {"type": "message", "message": {...}}          one conversation message
    {"type": "reset", "messages": [...]}           the whole conversation, after
                                                   /clear, compaction or a rewind
    {"type": "title", "title": "..."}              a short title for /resume

The system prompt isn't saved (it's rebuilt on resume, so memory files and
settings are current). Nothing else is written: no API keys, no settings.
Files are created owner-only (0600) in an owner-only folder (0700).

A session cut off mid-tool (crash, killed terminal) is repaired on load:
tool calls without a result get "Interrupted" results, so the transcript is
valid for the server again.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..config.settings import config_dir
from ..providers.messages import Message, ToolCall

INTERRUPTED_RESULT = "Interrupted: the session ended before this tool finished."


def project_key(project_root: Path) -> str:
    """Folder name for a project: readable name + hash of its normalised path.

    os.path.normcase lower-cases Windows paths, so C:\\Repo and c:\\repo are
    the same project there."""
    norm = os.path.normcase(os.path.abspath(str(project_root)))
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in project_root.name)[:40]
    return f"{name or 'project'}-{digest}"


def sessions_dir(project_root: Path) -> Path:
    return config_dir() / "projects" / project_key(project_root)


def message_to_dict(m: Message) -> dict[str, Any]:
    d = asdict(m)
    return {k: v for k, v in d.items() if v not in (None, "", [])} | {"role": m.role}


def message_from_dict(d: dict[str, Any]) -> Message:
    calls = [ToolCall(**c) for c in d.get("tool_calls") or []]
    return Message(
        role=d["role"],
        content=d.get("content", ""),
        tool_calls=calls,
        tool_call_id=d.get("tool_call_id"),
        name=d.get("name"),
        reasoning=d.get("reasoning", ""),
        turn=d.get("turn"),
    )


def repair(messages: list[Message]) -> list[Message]:
    """Give every tool call a result, so the server accepts the transcript."""
    out: list[Message] = []
    pending: list[ToolCall] = []
    for m in messages:
        if m.role == "tool":
            pending = [c for c in pending if c.id != m.tool_call_id]
        else:
            out += [Message.tool_result(c.id, c.name, INTERRUPTED_RESULT) for c in pending]
            pending = list(m.tool_calls)
        out.append(m)
    out += [Message.tool_result(c.id, c.name, INTERRUPTED_RESULT) for c in pending]
    return out


def _secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(stat.S_IRWXU)
    except OSError:
        pass


class SessionLog:
    """Appends one session's records to its file."""

    def __init__(self, project_root: Path, session_id: str | None = None) -> None:
        self.session_id = session_id or str(uuid.uuid4())
        self.dir = sessions_dir(project_root)
        self.path = self.dir / f"{self.session_id}.jsonl"
        self._saved: list[int] = []  # id() of the messages already written, in order

    def _write(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        _secure_dir(self.dir)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def start(self, cwd: Path, model: str) -> None:
        if not self.path.exists():
            self._write(
                [
                    {
                        "type": "meta",
                        "session_id": self.session_id,
                        "cwd": str(cwd),
                        "model": model,
                        "created": time.time(),
                    }
                ]
            )

    def mark_saved(self, messages: list[Message]) -> None:
        """After loading: these messages are already in the file."""
        self._saved = [id(m) for m in messages[1:]]

    def save(self, messages: list[Message]) -> None:
        """Write what changed since the last save (the system prompt excluded)."""
        body = messages[1:] if messages and messages[0].role == "system" else messages
        ids = [id(m) for m in body]
        n = len(self._saved)
        if ids[:n] == self._saved:
            new = body[n:]
            self._write([{"type": "message", "message": message_to_dict(m)} for m in new])
        else:  # replaced or shortened: /clear, compaction, rewind, error rollback
            self._write([{"type": "reset", "messages": [message_to_dict(m) for m in body]}])
        self._saved = ids

    def set_title(self, title: str) -> None:
        self._write([{"type": "title", "title": title}])


@dataclass
class SessionInfo:
    session_id: str
    path: Path
    updated: float
    title: str
    messages: int
    model: str


def load(path: Path) -> tuple[list[Message], dict[str, Any]]:
    """Replay a session file: (messages without the system prompt, meta)."""
    messages: list[Message] = []
    meta: dict[str, Any] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue  # a line cut off by a crash
            kind = r.get("type")
            if kind == "meta":
                meta.update(r)
            elif kind == "title":
                meta["title"] = r.get("title", "")
            elif kind == "message":
                messages.append(message_from_dict(r["message"]))
            elif kind == "reset":
                messages = [message_from_dict(d) for d in r.get("messages", [])]
    return repair(messages), meta


def _default_title(messages: list[Message]) -> str:
    for m in messages:
        if m.role == "user" and not m.content.startswith("["):
            text = " ".join(m.content.split())
            return text[:60] + ("…" if len(text) > 60 else "")
    return "(empty session)"


def list_sessions(project_root: Path) -> list[SessionInfo]:
    """This project's sessions, newest first."""
    out: list[SessionInfo] = []
    d = sessions_dir(project_root)
    if not d.is_dir():
        return out
    for path in d.glob("*.jsonl"):
        try:
            messages, meta = load(path)
        except OSError:
            continue
        if not messages:
            continue
        out.append(
            SessionInfo(
                session_id=path.stem,
                path=path,
                updated=path.stat().st_mtime,
                title=meta.get("title") or _default_title(messages),
                messages=len(messages),
                model=str(meta.get("model", "")),
            )
        )
    return sorted(out, key=lambda s: s.updated, reverse=True)


def find_session(project_root: Path, ref: str) -> SessionInfo | None:
    """By full id or a unique prefix of it."""
    matches = [s for s in list_sessions(project_root) if s.session_id.startswith(ref)]
    return matches[0] if len(matches) == 1 else None


def cleanup(days: int) -> int:
    """Delete session files (and their checkpoints) not touched for `days` days."""
    root = config_dir() / "projects"
    if not root.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for path in root.glob("*/*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
                cp = path.with_suffix(".checkpoints")
                if cp.is_dir():
                    for f in sorted(cp.rglob("*"), reverse=True):
                        f.unlink() if f.is_file() else f.rmdir()
                    cp.rmdir()
        except OSError:
            pass
    return removed


def age(ts: float) -> str:
    s = int(time.time() - ts)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"
