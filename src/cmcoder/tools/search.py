"""Glob and Grep tools (ripgrep-backed)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Literal

from pydantic import Field

from .base import Tool, ToolContext, ToolInput, ToolResult, truncate_middle

MAX_GLOB_RESULTS = 100
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache"}


def ripgrep() -> str | None:
    return shutil.which("rg")


async def _run(argv: list[str], cwd: Path, timeout: float = 60.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


class GlobInput(ToolInput):
    pattern: str = Field(description='Glob pattern, e.g. "**/*.py" or "src/**/test_*.ts".')
    path: str | None = Field(
        None, description="Directory to search in. Defaults to the working directory."
    )


class GlobTool(Tool):
    name = "Glob"
    description = (
        "Find files by name pattern. Respects .gitignore. Returns matching paths, most recently "
        f"modified first (up to {MAX_GLOB_RESULTS})."
    )
    Input = GlobInput
    read_only = True

    def permission_target(self, args: GlobInput, ctx: ToolContext) -> Path:
        return ctx.resolve(args.path or ".")

    def describe(self, args: GlobInput, ctx: ToolContext) -> str:
        return f'Glob("{args.pattern}")'

    async def run(self, args: GlobInput, ctx: ToolContext) -> ToolResult:
        root = ctx.resolve(args.path or ".")
        if not root.is_dir():
            return ToolResult(f"Not a directory: {root}", is_error=True)
        rg = ripgrep()
        paths: list[Path] = []
        if rg:
            code, out, err = await _run([rg, "--files", "--glob", args.pattern], root)
            if code not in (0, 1):
                return ToolResult(f"Glob failed: {err.strip()}", is_error=True)
            paths = [root / line for line in out.splitlines() if line]
        else:
            paths = [
                p
                for p in root.glob(args.pattern)
                if p.is_file() and not SKIP_DIRS.intersection(p.relative_to(root).parts)
            ]

        def mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        paths.sort(key=mtime, reverse=True)
        if not paths:
            return ToolResult("No files found.", summary="0 files")
        shown = paths[:MAX_GLOB_RESULTS]
        body = "\n".join(str(p) for p in shown)
        if len(paths) > len(shown):
            body += f"\n\n({len(paths) - len(shown)} more files not shown; narrow the pattern)"
        return ToolResult(body, summary=f"{len(paths)} files")


class GrepInput(ToolInput):
    pattern: str = Field(description="Regular expression (ripgrep syntax).")
    path: str | None = Field(
        None, description="File or directory to search. Defaults to the working directory."
    )
    glob: str | None = Field(None, description='Only search files matching this glob, e.g. "*.py".')
    type: str | None = Field(None, description='ripgrep file type, e.g. "py", "js", "rust".')
    output_mode: Literal["files_with_matches", "content", "count"] = Field(
        "files_with_matches",
        description="files_with_matches (default): file paths; content: matching lines; count: matches per file.",
    )
    case_insensitive: bool = Field(False, description="Case-insensitive search.")
    context_lines: int | None = Field(
        None, ge=0, le=20, description="Lines of context around matches (content mode)."
    )
    multiline: bool = Field(False, description="Let patterns span lines.")
    head_limit: int = Field(250, ge=1, description="Maximum number of output lines.")


class GrepTool(Tool):
    name = "Grep"
    description = (
        "Search file contents with a regular expression (ripgrep). Respects .gitignore. "
        "Use output_mode=content to see matching lines with line numbers."
    )
    Input = GrepInput
    read_only = True

    def permission_target(self, args: GrepInput, ctx: ToolContext) -> Path:
        return ctx.resolve(args.path or ".")

    def describe(self, args: GrepInput, ctx: ToolContext) -> str:
        return f'Grep("{args.pattern}")'

    async def run(self, args: GrepInput, ctx: ToolContext) -> ToolResult:
        rg = ripgrep()
        if not rg:
            return ToolResult(
                "ripgrep (rg) is not installed. Use Bash with grep instead, and ask the user to "
                "install ripgrep.",
                is_error=True,
            )
        target = ctx.resolve(args.path or ".")
        argv = [rg, "--color=never", "--no-heading"]
        if args.output_mode == "files_with_matches":
            argv.append("--files-with-matches")
        elif args.output_mode == "count":
            argv.append("--count")
        else:
            argv.append("--line-number")
            if args.context_lines:
                argv += ["-C", str(args.context_lines)]
        if args.case_insensitive:
            argv.append("-i")
        if args.multiline:
            argv += ["-U", "--multiline-dotall"]
        if args.glob:
            argv += ["--glob", args.glob]
        if args.type:
            argv += ["--type", args.type]
        argv += ["--regexp", args.pattern, "--", str(target)]
        try:
            code, out, err = await _run(argv, ctx.cwd)
        except TimeoutError:
            return ToolResult(
                "Grep timed out after 60s; narrow the path or pattern.", is_error=True
            )
        if code == 1:
            return ToolResult("No matches found.", summary="0 matches")
        if code != 0:
            return ToolResult(f"Grep failed: {err.strip()}", is_error=True)
        prefix = f"{ctx.cwd}/"
        lines = [line.removeprefix(prefix) for line in out.splitlines()]
        shown = lines[: args.head_limit]
        body = "\n".join(shown)
        if len(lines) > len(shown):
            body += f"\n\n({len(lines) - len(shown)} more lines not shown; raise head_limit or narrow the search)"
        unit = "files" if args.output_mode == "files_with_matches" else "lines"
        return ToolResult(truncate_middle(body), summary=f"{len(lines)} {unit}")
