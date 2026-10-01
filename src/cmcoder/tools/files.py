"""Read, Write and Edit tools."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from .base import Tool, ToolContext, ToolInput, ToolResult

DEFAULT_READ_LINES = 2000
MAX_LINE_CHARS = 2000


def _read_text(path: Path) -> tuple[str | None, str | None]:
    """Return (text, error)."""
    try:
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
        out = []
        for n in range(start, end):
            line = lines[n]
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + " ... [line truncated]"
            out.append(f"{n + 1:>6}\t{line}")
        body = "\n".join(out)
        if end < len(lines):
            body += (
                f"\n\n(showing lines {start + 1}-{end} of {len(lines)}; use offset to read more)"
            )
        return ToolResult(body, summary=f"Read {end - start} lines")


class WriteInput(ToolInput):
    file_path: str = Field(description="Path of the file to write.")
    content: str = Field(description="The full content to write.")


class WriteTool(Tool):
    name = "Write"
    description = (
        "Create a new file or overwrite an existing one with the given content. "
        "An existing file must be Read first. Prefer Edit for changing existing files."
    )
    Input = WriteInput

    def permission_target(self, args: WriteInput, ctx: ToolContext) -> Path:
        return ctx.resolve(args.file_path)

    def describe(self, args: WriteInput, ctx: ToolContext) -> str:
        return f"Write({ctx.display(ctx.resolve(args.file_path))})"

    async def run(self, args: WriteInput, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args.file_path)
        if err := ctx.check_fresh(path):
            return ToolResult(err, is_error=True)
        if path.is_dir():
            return ToolResult(f"{path} is a directory.", is_error=True)
        existed = path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.content, encoding="utf-8")
        except OSError as e:
            return ToolResult(f"Cannot write {path}: {e}", is_error=True)
        ctx.mark_read(path)
        n = len(args.content.splitlines())
        verb = "Updated" if existed else "Created"
        return ToolResult(f"{verb} {ctx.display(path)} ({n} lines).", summary=f"{verb} {n} lines")


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

        old, new = args.old_string, args.new_string
        if "\r\n" in text and "\r\n" not in old:
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        count = text.count(old) if old else 0
        if count == 0:
            hint = ""
            if old.strip() and old.strip() in text:
                hint = (
                    " The text exists with different leading/trailing whitespace; copy it exactly."
                )
            return ToolResult(
                f"old_string was not found in {ctx.display(path)}.{hint} Re-read the file and try again.",
                is_error=True,
            )
        if count > 1 and not args.replace_all:
            return ToolResult(
                f"old_string occurs {count} times in {ctx.display(path)}. Add more surrounding "
                "context to make it unique, or set replace_all to true.",
                is_error=True,
            )
        first = text.index(old)
        updated = text.replace(old, new) if args.replace_all else text.replace(old, new, 1)
        try:
            path.write_text(updated, encoding="utf-8", newline="")
        except OSError as e:
            return ToolResult(f"Cannot write {path}: {e}", is_error=True)
        ctx.mark_read(path)

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
