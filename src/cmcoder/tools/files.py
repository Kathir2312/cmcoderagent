"""Read, Write and Edit tools."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from .base import (
    MAX_CHANGE_PREVIEW_CHARS,
    FileChange,
    Tool,
    ToolContext,
    ToolInput,
    ToolResult,
)

DEFAULT_READ_LINES = 2000
MAX_READ_BYTES = 20 * 1024 * 1024
MAX_LINE_CHARS = 2000


def _read_text(path: Path) -> tuple[str | None, str | None]:
    """Return (text, error)."""
    # Check first: on Windows, opening a directory raises PermissionError.
    if path.is_dir():
        return None, f"{path} is a directory. Use Glob or `ls` via Bash to list it."
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return None, (
                f"{path} is larger than {MAX_READ_BYTES // (1024 * 1024)} MB. Use Grep to find the "
                "relevant part, or Bash with head/tail/sed -n to read a slice."
            )
        data = path.read_bytes()
    except FileNotFoundError:
        return None, f"File does not exist: {path}"
    except IsADirectoryError:
        return None, f"{path} is a directory. Use Glob or `ls` via Bash to list it."
    except OSError as e:
        return None, f"Cannot read {path}: {e}"
    if b"\x00" in data[:8192]:
        return None, f"{path} looks like a binary file and cannot be read as text."
    return data.decode("utf-8", errors="replace"), None


class ReadInput(ToolInput):
    file_path: str = Field(
        description="Path to the file (absolute, or relative to the working directory)."
    )
    offset: int | None = Field(None, description="1-based line number to start reading from.", ge=1)
    limit: int | None = Field(None, description="Number of lines to read.", ge=1)


class ReadTool(Tool):
    name = "Read"
    description = (
        "Read a text file. Returns lines prefixed with line numbers (cat -n style). "
        f"Reads up to {DEFAULT_READ_LINES} lines by default; use offset/limit for large files. "
        "Always read a file before editing it."
    )
    Input = ReadInput
    read_only = True

    def permission_target(self, args: ReadInput, ctx: ToolContext) -> Path:
        return ctx.resolve(args.file_path)

    def describe(self, args: ReadInput, ctx: ToolContext) -> str:
        return f"Read({ctx.display(ctx.resolve(args.file_path))})"

    async def run(self, args: ReadInput, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args.file_path)
        text, err = _read_text(path)
        if err or text is None:
            return ToolResult(err or "read failed", is_error=True)
        ctx.mark_read(path)
        lines = text.splitlines()
        if not lines:
            return ToolResult("(file exists but is empty)", summary="Read 0 lines")
        start = (args.offset or 1) - 1
        if start >= len(lines):
            return ToolResult(
                f"offset {args.offset} is past the end of the file ({len(lines)} lines).",
                is_error=True,
            )
        end = min(len(lines), start + (args.limit or DEFAULT_READ_LINES))
        budget = ctx.max_output_chars - 200  # room for the footer
        out: list[str] = []
        size = 0
        for n in range(start, end):
            line = lines[n]
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + " ... [line truncated]"
            numbered = f"{n + 1:>6}\t{line}"
            if out and size + len(numbered) + 1 > budget:
                end = n  # stop at a line boundary rather than cutting out the middle
                break
            out.append(numbered)
            size += len(numbered) + 1
        body = "\n".join(out)
        if end < len(lines):
            body += (
                f"\n\n(showing lines {start + 1}-{end} of {len(lines)}; call Read with "
                f"offset={end + 1} to continue, or use Grep to find what you need)"
            )
        return ToolResult(body, summary=f"Read {end - start} lines")


class WriteInput(ToolInput):
    file_path: str = Field(description="Path of the file to write.")
    content: str = Field(description="The full content to write.")


class WriteTool(Tool):
    name = "Write"
    description = (
        "Create a new file or overwrite an existing one with the given content. "
        "An existing file must be Read first. Prefer Edit for changing existing files. "
        "Always use this to write files, never shell commands like cat > file << EOF."
    )
    Input = WriteInput

    def permission_target(self, args: WriteInput, ctx: ToolContext) -> Path:
        return ctx.resolve(args.file_path)

    def describe(self, args: WriteInput, ctx: ToolContext) -> str:
        return f"Write({ctx.display(ctx.resolve(args.file_path))})"

    def proposed_change(self, args: WriteInput, ctx: ToolContext) -> FileChange | None:
        path = ctx.resolve(args.file_path)
        if path.is_dir() or len(args.content) > MAX_CHANGE_PREVIEW_CHARS or ctx.check_fresh(path):
            return None  # nothing to review: too big, or the write would be refused
        before = None
        if path.exists():
            before, err = _read_text(path)
            if err or before is None or len(before) > MAX_CHANGE_PREVIEW_CHARS:
                return None
        return FileChange(path, before, args.content)

    async def run(self, args: WriteInput, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args.file_path)
        if err := ctx.check_fresh(path):
            return ToolResult(err, is_error=True)
        if path.is_dir():
            return ToolResult(f"{path} is a directory.", is_error=True)
        existed = path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # newline="": write exactly what was given (no LF -> CRLF translation on Windows).
            path.write_text(args.content, encoding="utf-8", newline="")
        except OSError as e:
            return ToolResult(f"Cannot write {path}: {e}", is_error=True)
        ctx.mark_read(path)
        if ctx.code_index is not None:
            ctx.code_index.note_changed(path)  # re-indexed before the next search
        n = len(args.content.splitlines())
        verb = "Updated" if existed else "Created"
        return ToolResult(f"{verb} {ctx.display(path)} ({n} lines).", summary=f"{verb} {n} lines")


def closest_match(text: str, old: str, threshold: float = 0.6) -> tuple[int, str] | None:
    """Where in `text` something like `old` is: (1-based line, those lines).

    Helps the model fix an Edit whose old_string is slightly off (whitespace,
    a changed word, a stale copy). Skipped for very large files."""
    lines = text.splitlines()
    want = old.strip("\n").splitlines()
    n = len(want)
    if not n or len(lines) > 5000 or n > 200:
        return None
    target = "\n".join(w.strip() for w in want)
    best, best_at = 0.0, -1
    for i in range(0, max(1, len(lines) - n + 1)):
        window = "\n".join(x.strip() for x in lines[i : i + n])
        m = difflib.SequenceMatcher(None, target, window, autojunk=False)
        if m.real_quick_ratio() < best or m.quick_ratio() < best:
            continue
        r = m.ratio()
        if r > best:
            best, best_at = r, i
    if best < threshold or best_at < 0:
        return None
    return best_at + 1, "\n".join(lines[best_at : best_at + n])


class EditInput(ToolInput):
    file_path: str = Field(description="Path of the file to edit.")
    old_string: str = Field(
        description="Exact text to replace, including whitespace and indentation."
    )
    new_string: str = Field(description="Replacement text.")
    replace_all: bool = Field(False, description="Replace every occurrence instead of exactly one.")


class EditTool(Tool):
    name = "Edit"
    description = (
        "Replace an exact string in a file. The file must be Read first. old_string must match "
        "the file exactly (copy it from the Read output without the line-number prefix) and must "
        "be unique unless replace_all is true; include surrounding lines to make it unique."
    )
    Input = EditInput

    def permission_target(self, args: EditInput, ctx: ToolContext) -> Path:
        return ctx.resolve(args.file_path)

    def describe(self, args: EditInput, ctx: ToolContext) -> str:
        return f"Edit({ctx.display(ctx.resolve(args.file_path))})"

    def proposed_change(self, args: EditInput, ctx: ToolContext) -> FileChange | None:
        path = ctx.resolve(args.file_path)
        if not path.exists():
            if args.old_string == "" and len(args.new_string) <= MAX_CHANGE_PREVIEW_CHARS:
                return FileChange(path, None, args.new_string)
            return None
        if ctx.check_fresh(path):
            return None  # the edit would be refused (file not read, or changed since)
        text, err = _read_text(path)
        if err or text is None or len(text) > MAX_CHANGE_PREVIEW_CHARS:
            return None
        done = _replace(text, args)
        return FileChange(path, text, done.updated) if isinstance(done, _Replaced) else None

    async def run(self, args: EditInput, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args.file_path)
        if not path.exists():
            if args.old_string == "":
                # Allow creating a new file through Edit, as Claude Code does.
                write = await WriteTool().run(
                    WriteInput(file_path=str(path), content=args.new_string), ctx
                )
                return write
            return ToolResult(f"File does not exist: {path}", is_error=True)
        if err := ctx.check_fresh(path):
            return ToolResult(err, is_error=True)
        if args.old_string == args.new_string:
            return ToolResult(
                "old_string and new_string are identical; nothing to change.", is_error=True
            )
        text, err = _read_text(path)
        if err or text is None:
            return ToolResult(err or "read failed", is_error=True)

        done = _replace(text, args)
        if isinstance(done, str):
            return ToolResult(done.format(path=ctx.display(path)), is_error=True)
        updated, count, first, new, old = done.updated, done.count, done.first, done.new, done.old
        try:
            path.write_text(updated, encoding="utf-8", newline="")
        except OSError as e:
            return ToolResult(f"Cannot write {path}: {e}", is_error=True)
        ctx.mark_read(path)
        if ctx.code_index is not None:
            ctx.code_index.note_changed(path)  # re-indexed before the next search

        # Show the edited region so the model can confirm the result.
        start_line = text[:first].count("\n")
        new_lines = updated.splitlines()
        lo = max(0, start_line - 3)
        hi = min(len(new_lines), start_line + new.count("\n") + 4)
        snippet = "\n".join(f"{n + 1:>6}\t{new_lines[n]}" for n in range(lo, hi))
        removed = old.count("\n") + 1
        added = new.count("\n") + 1 if new else 0
        times = f" ({count} occurrences)" if args.replace_all and count > 1 else ""
        return ToolResult(
            f"Edited {ctx.display(path)}{times}. Result:\n{snippet}",
            summary=f"+{added} -{removed} lines{times}",
        )


@dataclass
class _Replaced:
    updated: str
    count: int
    first: int  # offset of the first replacement in the original text
    old: str  # the strings as applied (line endings matched to the file)
    new: str


def _replace(text: str, args: EditInput) -> _Replaced | str:
    """Apply an Edit to `text`: the result, or an error message with a `{path}`
    placeholder. Shared by the edit itself and its diff preview, so the two
    can never disagree."""
    old, new = args.old_string, args.new_string
    if "\r\n" in text and "\r\n" not in old:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    count = text.count(old) if old else 0
    if count == 0:
        hint = ""
        if old.strip() and old.strip() in text:
            hint = " The text exists with different leading/trailing whitespace; copy it exactly."
        elif near := closest_match(text, old):
            start, block = near
            hint = (
                f" The closest text is at line {start}; check indentation, quotes and "
                f"spelling against it:\n{block}\n"
            )
        hint = hint.replace("{", "{{").replace("}", "}}")
        return f"old_string was not found in {{path}}.{hint} Re-read the file and try again."
    if count > 1 and not args.replace_all:
        return (
            f"old_string occurs {count} times in {{path}}. Add more surrounding "
            "context to make it unique, or set replace_all to true."
        )
    updated = text.replace(old, new) if args.replace_all else text.replace(old, new, 1)
    return _Replaced(updated, count, text.index(old), old, new)
