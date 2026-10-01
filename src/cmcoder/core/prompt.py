"""System prompt and project memory (CMCODER.md / AGENTS.md)."""

from __future__ import annotations

import datetime as _dt
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config.settings import config_dir

MEMORY_FILENAMES = ("CMCODER.md", "AGENTS.md")
LOCAL_MEMORY_FILENAME = "CMCODER.local.md"
MAX_MEMORY_CHARS = 40_000

BASE_PROMPT = """\
You are cmcoder, an agentic coding assistant working in the user's repository on their machine.
You help with software engineering tasks: fixing bugs, adding features, refactoring, explaining code and running commands.

# How to work
- Use the tools to look at the code before answering or changing it. Never guess file contents, APIs or paths.
- Use Glob and Grep to find things, and Read to look at files. Read a file before editing it.
- Make changes with Edit (exact string replacement) or Write (new files). Keep edits minimal and in the style of the surrounding code.
- After changing code, run the relevant tests, linter or build with Bash when the project has them, and fix what you broke.
- Do not create files the task does not need (no new docs or READMEs unless asked).
- If a tool call fails, read the error and fix your call; don't repeat the same failing call.
- If the request is ambiguous or risky (deleting data, force-pushing, changing many files), ask the user first.
- Never print, copy or send secrets (API keys, passwords, .env contents).
- Do not commit or push unless the user asks.

# How to reply
- Be concise and direct. Use GitHub-flavored Markdown; it is shown in a terminal.
- When you finish a task, say briefly what you changed and how you verified it. If something is not done or a check failed, say so plainly.
- Refer to code as `path/to/file.py:42`.
"""

COMPACT_PROMPT = """\
You are cmcoder, a coding assistant working in the user's repository.
Use tools to inspect code before answering. Read a file before editing it. Keep changes minimal.
Run tests with Bash after changes when possible. Be concise. Never reveal secrets.
"""


@dataclass
class MemoryFile:
    path: Path
    content: str


def _git_info(cwd: Path) -> str | None:
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if branch.returncode != 0:
            return None
        status = subprocess.run(
            ["git", "status", "--short"], cwd=cwd, capture_output=True, text=True, timeout=5
        )
        changed = status.stdout.strip().splitlines()
        summary = f"{len(changed)} changed files" if changed else "clean"
        return f"branch {branch.stdout.strip()}, working tree {summary}"
    except (OSError, subprocess.SubprocessError):
        return None


def load_memory_files(cwd: Path, project_root: Path) -> list[MemoryFile]:
    """User memory, then each folder from the project root down to cwd, then local memory."""
    candidates: list[Path] = [config_dir() / "CMCODER.md"]
    dirs = [project_root]
    try:
        rel = cwd.relative_to(project_root)
        for part in rel.parts:
            dirs.append(dirs[-1] / part)
    except ValueError:
        pass
    for d in dirs:
        candidates += [d / name for name in MEMORY_FILENAMES]
    candidates.append(project_root / LOCAL_MEMORY_FILENAME)

    out: list[MemoryFile] = []
    seen: set[Path] = set()
    total = 0
    for path in candidates:
        try:
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        if total + len(text) > MAX_MEMORY_CHARS:
            text = text[: max(0, MAX_MEMORY_CHARS - total)] + "\n... [memory truncated]"
        total += len(text)
        out.append(MemoryFile(path, text))
        if total >= MAX_MEMORY_CHARS:
            break
    return out


def build_system_prompt(
    cwd: Path,
    project_root: Path,
    *,
    tier: str = "full",
    model: str | None = None,
    memory: list[MemoryFile] | None = None,
    append: str | None = None,
) -> str:
    """Stable content first (so server-side prefix caches are reused), dynamic last."""
    parts = [COMPACT_PROMPT if tier == "compact" else BASE_PROMPT]
    if memory:
        mem = "\n\n".join(f"## From {m.path}\n{m.content}" for m in memory)
        parts.append(
            "# Project instructions\nFollow these instructions from the user's memory files. "
            f"They override the defaults above.\n\n{mem}"
        )
    if append:
        parts.append(append)
    env = [
        f"Working directory: {cwd}",
        f"Project root: {project_root}",
        f"Platform: {platform.system()} {platform.release()}",
        f"Date: {_dt.date.today().isoformat()}",
    ]
    if model:
        env.append(f"Model: {model}")
    if git := _git_info(cwd):
        env.append(f"Git: {git}")
    parts.append("# Environment\n" + "\n".join(env))
    return "\n\n".join(parts)
