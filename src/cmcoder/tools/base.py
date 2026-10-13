"""Tool base class and shared helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict

from ..compat import from_shell_path
from ..providers.messages import ToolSpec

if TYPE_CHECKING:
    from ..rag.index import CodeIndex
    from ..sandbox import Sandbox
    from .shell import PersistentShell

MAX_RESULT_CHARS = 30_000


def output_budget_chars(context_window: int) -> int:
    """Per-tool-output size: about a fifth of the context window, capped at 30k chars."""
    return max(4_000, min(MAX_RESULT_CHARS, int(context_window * 3 * 0.2)))


class ToolInput(BaseModel):
    # Models sometimes add stray keys; ignore them rather than failing the call.
    model_config = ConfigDict(extra="ignore")


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    # One-line summary for the UI, e.g. "Read 120 lines".
    summary: str | None = None


@dataclass
class ToolContext:
    cwd: Path
    project_root: Path
    # Resolved path -> mtime_ns when the agent last read (or wrote) it.
    read_files: dict[Path, int] = field(default_factory=dict)
    shell: PersistentShell | None = None
    # The Bash sandbox for this session (None: commands run unsandboxed), and
    # the shell for commands the user let run outside it.
    sandbox: Sandbox | None = None
    unsandboxed_shell: PersistentShell | None = None
    # Largest tool output kept in the conversation; set from the model's context
    # window (smaller windows get smaller outputs).
    max_output_chars: int = MAX_RESULT_CHARS
    # The TodoWrite list: [{"content", "status", "activeForm"?}, ...]
    todos: list[dict[str, Any]] = field(default_factory=list)
    # The project's code index (Phase 5; None: code search is off).
    code_index: CodeIndex | None = None

    async def close_shells(self) -> None:
        for shell in (self.shell, self.unsandboxed_shell):
            if shell is not None:
                await shell.close()
        self.shell = self.unsandboxed_shell = None

    def __post_init__(self) -> None:
        # Compare like with like: tool paths are resolved, so the roots must be
        # too (Windows 8.3 short names, macOS /tmp -> /private/tmp, symlinks).
        self.cwd = self.cwd.resolve()
        self.project_root = self.project_root.resolve()

    def resolve(self, file_path: str) -> Path:
        p = Path(from_shell_path(file_path)).expanduser()
        if not p.is_absolute():
            p = self.cwd / p
        return p.resolve()

    def display(self, path: Path) -> str:
        """Paths as the model sees them: relative when possible, always with
        "/" (Windows accepts it, and answers stay the same on every OS)."""
        try:
            return path.relative_to(self.cwd).as_posix()
        except ValueError:
            return path.as_posix()

    def mark_read(self, path: Path) -> None:
        try:
            self.read_files[path] = path.stat().st_mtime_ns
        except OSError:
            pass

    def check_fresh(self, path: Path) -> str | None:
        """Error message if `path` exists but wasn't read, or changed since it was."""
        if not path.exists():
            return None
        seen = self.read_files.get(path)
        if seen is None:
            return f"File {self.display(path)} has not been read yet. Read it first before writing to it."
        if path.stat().st_mtime_ns != seen:
            return (
                f"File {self.display(path)} has been modified since it was last read "
                "(by the user or another process). Read it again before writing to it."
            )
        return None


def _strip_titles(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip_titles(v) for v in schema]
    return schema


# Bigger files get no diff preview (the edit still works).
MAX_CHANGE_PREVIEW_CHARS = 1_000_000


@dataclass
class FileChange:
    """What a file-changing tool call would do, for diff review before approval."""

    path: Path
    before: str | None  # None: the file doesn't exist yet
    after: str


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Input: ClassVar[type[ToolInput]]
    read_only: ClassVar[bool] = False

    def spec(self) -> ToolSpec:
        schema = _strip_titles(self.Input.model_json_schema())
        schema.setdefault("properties", {})
        return ToolSpec(name=self.name, description=self.description, parameters=schema)

    @abstractmethod
    async def run(self, args: Any, ctx: ToolContext) -> ToolResult: ...

    def permission_target(self, args: Any, ctx: ToolContext) -> str | Path | None:
        """What permission rules match against: a path for file tools, the command for Bash."""
        return None

    def describe(self, args: Any, ctx: ToolContext) -> str:
        """Short label for the UI, e.g. `Read(src/app.py)`."""
        return self.name

    def proposed_change(self, args: Any, ctx: ToolContext) -> FileChange | None:
        """The file change this call would make (Write, Edit), or None."""
        return None


def truncate_middle(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n\n... [{omitted} characters truncated] ...\n\n{tail}"
