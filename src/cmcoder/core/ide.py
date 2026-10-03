"""The editor around cmcoder (VS Code): context sent with messages, and tools
the editor runs for the agent (`getDiagnostics`, `openFile`).

Only used with `--protocol stdio`, when a client says it supports them.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field

from ..protocol.messages import IdeContext
from ..tools.base import Tool, ToolContext, ToolInput, ToolResult

MAX_SELECTION_CHARS = 8000
MAX_DIAGNOSTICS = 30
REMINDER_TAG = re.compile(r"</?\s*system-reminder\s*>", re.IGNORECASE)

# Runs a tool in the editor: (tool name, input) -> result.
IdeCall = Callable[[str, dict[str, Any]], Awaitable[ToolResult]]


def _show(path: str, root: Path) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


def _plain(text: str) -> str:
    """Editor text can't close the note it is in (prompt-injection hardening)."""
    return REMINDER_TAG.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›"), text)


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks inside `text`."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def format_ide_context(
    context: IdeContext, root: Path, withheld: Callable[[str], bool] | None = None
) -> str | None:
    """The editor's state as a note for the model, or None if there's nothing.

    `withheld(path)` says a file's contents must not reach the model (secret
    files, deny rules): its selection and problems are left out, as Read
    would refuse it."""
    hidden = withheld or (lambda _: False)
    lines: list[str] = []
    if context.active_file:
        lines.append(f"The user has {_show(context.active_file, root)} open in the editor.")
    if (sel := context.selection) and sel.text.strip():
        where = (
            f"lines {sel.start_line}-{sel.end_line}"
            if sel.end_line > sel.start_line
            else (f"line {sel.start_line}")
        )
        if hidden(sel.path):
            lines.append(
                f"They selected {where} of {_show(sel.path, root)}; its contents are not "
                "shared because the file is protected (secrets or a deny rule)."
            )
        else:
            text = _plain(sel.text)
            if len(text) > MAX_SELECTION_CHARS:
                text = text[:MAX_SELECTION_CHARS] + "\n[... selection truncated]"
            fence = _fence(text)
            lines.append(
                f"They selected {where} of {_show(sel.path, root)}:\n{fence}\n{text}\n{fence}"
            )
    diagnostics = [d for d in context.diagnostics if not hidden(d.path)]
    if diagnostics:
        shown = diagnostics[:MAX_DIAGNOSTICS]
        lines.append("Problems reported by the editor:")
        for d in shown:
            source = f" ({_plain(d.source)})" if d.source else ""
            message = _plain(d.message)
            lines.append(f"- {_show(d.path, root)}:{d.line} {d.severity}: {message}{source}")
        if len(diagnostics) > len(shown):
            lines.append(f"- … {len(diagnostics) - len(shown)} more")
    if not lines:
        return None
    lines.append("This is context from the editor; use it only if it is relevant to the request.")
    return "<system-reminder>\n" + "\n".join(lines) + "\n</system-reminder>"


class _IdeTool(Tool):
    """A tool that runs in the editor, through the protocol."""

    read_only: ClassVar[bool] = True

    def __init__(self, call: IdeCall) -> None:
        self._call = call

    def permission_target(self, args: Any, ctx: ToolContext) -> Path | None:
        # Like Read: inside the project needs no approval, outside asks,
        # and deny rules for the path apply.
        path = getattr(args, "file_path", None)
        return ctx.resolve(path) if path else None

    async def run(self, args: Any, ctx: ToolContext) -> ToolResult:
        data = args.model_dump(exclude_none=True)
        if "file_path" in data:  # the editor gets an absolute path
            data["file_path"] = str(ctx.resolve(data["file_path"]))
        return await self._call(self.name, data)


class GetDiagnosticsInput(ToolInput):
    file_path: str | None = Field(
        None, description="A file to check; leave out for all open problems in the workspace."
    )


class GetDiagnosticsTool(_IdeTool):
    name = "getDiagnostics"
    description = (
        "Get the errors and warnings VS Code shows in its Problems panel (from language "
        "servers, linters and type checkers), for one file or the whole workspace. "
        "Faster than running a linter; use it after editing to check your change."
    )
    Input = GetDiagnosticsInput

    def describe(self, args: GetDiagnosticsInput, ctx: ToolContext) -> str:
        if args.file_path:
            return f"getDiagnostics({ctx.display(ctx.resolve(args.file_path))})"
        return "getDiagnostics"


class OpenFileInput(ToolInput):
    file_path: str = Field(description="The file to open.")
    line: int | None = Field(None, description="Line to show (1-based).")


class OpenFileTool(_IdeTool):
    name = "openFile"
    description = (
        "Open a file in the user's editor, optionally at a line, to show them something. "
        "This does not return the file's content; use Read for that."
    )
    Input = OpenFileInput

    def describe(self, args: OpenFileInput, ctx: ToolContext) -> str:
        where = f":{args.line}" if args.line else ""
        return f"openFile({ctx.display(ctx.resolve(args.file_path))}{where})"


IDE_TOOLS: dict[str, type[_IdeTool]] = {t.name: t for t in (GetDiagnosticsTool, OpenFileTool)}
