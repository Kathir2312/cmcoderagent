"""Tool base class and shared helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict

from ..providers.messages import ToolSpec

if TYPE_CHECKING:
    from .shell import PersistentShell

MAX_RESULT_CHARS = 30_000


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

    def resolve(self, file_path: str) -> Path:
        p = Path(file_path).expanduser()
        if not p.is_absolute():
            p = self.cwd / p
        return p.resolve()

    def display(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.cwd))
        except ValueError:
            return str(path)

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


def truncate_middle(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n\n... [{omitted} characters truncated] ...\n\n{tail}"
