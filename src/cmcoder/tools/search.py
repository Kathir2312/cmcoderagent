"""Glob and Grep tools.

Uses ripgrep when installed (fast), with a built-in Python fallback so search
works out of the box, e.g. on Windows machines without `rg`.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pathspec
from pydantic import Field

from ..compat import find_program
from ..sensitive import is_secret, ripgrep_exclude_globs
from .base import Tool, ToolContext, ToolInput, ToolResult, truncate_middle

MAX_GLOB_RESULTS = 100
SEARCH_TIMEOUT = 60.0
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache"}

# ripgrep type names -> extensions, for the fallback.
TYPE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "py": (".py", ".pyi"),
    "js": (".js", ".mjs", ".cjs", ".jsx"),
    "ts": (".ts", ".tsx", ".mts", ".cts"),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "go": (".go",),
    "rust": (".rs",),
    "c": (".c", ".h"),
    "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h"),
    "cs": (".cs",),
    "php": (".php",),
    "ruby": (".rb",),
    "swift": (".swift",),
    "sh": (".sh", ".bash"),
    "ps": (".ps1", ".psm1"),
    "sql": (".sql",),
    "md": (".md", ".markdown"),
    "json": (".json",),
    "yaml": (".yaml", ".yml"),
    "toml": (".toml",),
    "xml": (".xml",),
    "html": (".html", ".htm"),
    "css": (".css", ".scss", ".sass", ".less"),
}


def ripgrep() -> str | None:
    return find_program("rg")


async def _run(argv: list[str], cwd: Path, timeout: float = SEARCH_TIMEOUT) -> tuple[int, str, str]:
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


def _strip_prefix(line: str, base: Path) -> str:
    """Show paths relative to the working directory (ripgrep prints them as given)."""
    # ripgrep may echo the root with either separator (--path-separator applies
    # to what it appends), so try every combination.
    for prefix in (f"{base}{os.sep}", f"{base.as_posix()}/", f"{base}/"):
        if line.startswith(prefix):
            return line[len(prefix) :]
    return line


# --- Python fallback ---------------------------------------------------------


def walk_files(
    root: Path, deadline: float | None = None, include: pathspec.PathSpec | None = None
) -> Iterator[Path]:
    """Files under `root` like ripgrep: skips hidden entries, SKIP_DIRS and .gitignore matches.

    As with ripgrep's --glob, files matching `include` are returned even if
    .gitignore lists them, but ignored directories are still skipped.
    """
    specs: list[tuple[Path, pathspec.PathSpec]] = []

    def ignored(path: Path, is_dir: bool) -> bool:
        for base, spec in specs:
            try:
                rel = path.relative_to(base).as_posix()
            except ValueError:
                continue
            if spec.match_file(rel + "/" if is_dir else rel):
                return True
        return False

    for dirpath, dirnames, filenames in os.walk(root):
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError
        here = Path(dirpath)
        gitignore = here / ".gitignore"
        if gitignore.is_file():
            try:
                lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
                specs.append((here, pathspec.GitIgnoreSpec.from_lines(lines)))
            except OSError:
                pass
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(".") and d not in SKIP_DIRS and not ignored(here / d, True)
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = here / name
            if not ignored(p, False) or (
                include is not None and include.match_file(p.relative_to(root).as_posix())
            ):
                yield p


def _glob_matcher(pattern: str) -> pathspec.PathSpec:
    # ripgrep's --glob uses gitignore glob syntax: "*.py" matches at any depth.
    return pathspec.GitIgnoreSpec.from_lines([pattern])


def python_glob(root: Path, pattern: str) -> list[Path]:
    deadline = time.monotonic() + SEARCH_TIMEOUT
    spec = _glob_matcher(pattern)
    return [
        p
        for p in walk_files(root, deadline, include=spec)
        if spec.match_file(p.relative_to(root).as_posix())
    ]


MAX_FALLBACK_FILE_BYTES = 10 * 1024 * 1024


def _read_text_file(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FALLBACK_FILE_BYTES:
            return None  # like a binary file: too large for the built-in search
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", "replace")


def python_grep(
    args: GrepInput, target: Path, cwd: Path, project_root: Path | None = None
) -> tuple[list[str], str | None]:
    """Returns (output lines, error). Secret files under `project_root` are skipped."""
    flags = re.IGNORECASE if args.case_insensitive else 0
    if args.multiline:
        flags |= re.DOTALL | re.MULTILINE
    try:
        regex = re.compile(args.pattern, flags)
    except re.error as e:
        return [], f"Invalid regular expression: {e}"
    deadline = time.monotonic() + SEARCH_TIMEOUT

    glob_spec = _glob_matcher(args.glob) if args.glob else None
    if target.is_file():
        files: list[Path] = [target]
        base = target.parent
    else:
        base = target
        files = list(walk_files(target, deadline, include=glob_spec))
    if glob_spec is not None:
        files = [f for f in files if glob_spec.match_file(f.relative_to(base).as_posix())]
    if project_root is not None:
        files = [f for f in files if not is_secret(f, project_root)]
    if args.type:
        exts = TYPE_EXTENSIONS.get(args.type)
        if exts is None:
            return (
                [],
                f"Unknown file type {args.type!r}. Known: {', '.join(sorted(TYPE_EXTENSIONS))}",
            )
        files = [f for f in files if f.suffix.lower() in exts]

    def show(p: Path) -> str:
        try:
            return p.relative_to(cwd).as_posix()
        except ValueError:
            return p.as_posix()

    out: list[str] = []
    for f in files:
        if time.monotonic() > deadline:
            raise TimeoutError
        text = _read_text_file(f)
        if text is None:
            continue
        name = show(f)
        if args.output_mode == "files_with_matches":
            if regex.search(text):
                out.append(name)
            continue
        lines = text.splitlines()
        matched_lines: set[int] = set()
        if args.multiline:
            for m in regex.finditer(text):
                first = text.count("\n", 0, m.start())
                last = text.count("\n", 0, max(m.start(), m.end() - 1))
                matched_lines.update(range(first, min(last, len(lines) - 1) + 1))
        else:
            matched_lines = {i for i, line in enumerate(lines) if regex.search(line)}
        if not matched_lines:
            continue
        if args.output_mode == "count":
            out.append(f"{name}:{len(matched_lines)}")
            continue
        ctx_n = args.context_lines or 0
        shown: list[int] = []
        for i in sorted(matched_lines):
            shown.extend(range(max(0, i - ctx_n), min(len(lines), i + ctx_n + 1)))
        prev = -2
        for i in sorted(set(shown)):
            if ctx_n and prev >= 0 and i > prev + 1:
                out.append("--")
            sep = ":" if i in matched_lines else "-"
            out.append(f"{name}{sep}{i + 1}{sep}{lines[i]}")
            prev = i
    return out, None


# --- Tools ---------------------------------------------------------------------


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
        try:
            if rg:
                code, out, err = await _run(
                    [rg, "--files", "--path-separator", "/", "--glob", args.pattern], root
                )
                if code not in (0, 1):
                    return ToolResult(f"Glob failed: {err.strip()}", is_error=True)
                paths = [root / line for line in out.splitlines() if line]
            else:
                paths = await asyncio.to_thread(python_glob, root, args.pattern)
        except TimeoutError:
            return ToolResult("Glob timed out; narrow the path or pattern.", is_error=True)

        def mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        paths.sort(key=mtime, reverse=True)
        if not paths:
            return ToolResult("No files found.", summary="0 files")
        shown = paths[:MAX_GLOB_RESULTS]
        body = "\n".join(p.as_posix() for p in shown)  # "/" on every OS, like Grep
        if len(paths) > len(shown):
            body += f"\n\n({len(paths) - len(shown)} more files not shown; narrow the pattern)"
        return ToolResult(body, summary=f"{len(paths)} files")


class GrepInput(ToolInput):
    pattern: str = Field(description="Regular expression.")
    path: str | None = Field(
        None, description="File or directory to search. Defaults to the working directory."
    )
    glob: str | None = Field(None, description='Only search files matching this glob, e.g. "*.py".')
    type: str | None = Field(None, description='File type, e.g. "py", "js", "ts", "java", "go".')
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
        "Search file contents with a regular expression. Respects .gitignore. "
        "Use output_mode=content to see matching lines with line numbers."
    )
    Input = GrepInput
    read_only = True

    def permission_target(self, args: GrepInput, ctx: ToolContext) -> Path:
        return ctx.resolve(args.path or ".")

    def describe(self, args: GrepInput, ctx: ToolContext) -> str:
        return f'Grep("{args.pattern}")'

    async def run(self, args: GrepInput, ctx: ToolContext) -> ToolResult:
        target = ctx.resolve(args.path or ".")
        if not target.exists():
            return ToolResult(f"Path does not exist: {target}", is_error=True)
        rg = ripgrep()
        try:
            if rg:
                lines, error = await self._ripgrep(rg, args, target, ctx)
            else:
                lines, error = await asyncio.to_thread(
                    python_grep, args, target, ctx.cwd, ctx.project_root
                )
        except TimeoutError:
            return ToolResult("Grep timed out; narrow the path or pattern.", is_error=True)
        if error:
            return ToolResult(error, is_error=True)
        if not lines:
            return ToolResult("No matches found.", summary="0 matches")
        shown = lines[: args.head_limit]
        body = "\n".join(shown)
        if len(lines) > len(shown):
            body += f"\n\n({len(lines) - len(shown)} more lines not shown; raise head_limit or narrow the search)"
        unit = "files" if args.output_mode == "files_with_matches" else "lines"
        return ToolResult(
            truncate_middle(body, ctx.max_output_chars), summary=f"{len(lines)} {unit}"
        )

    async def _ripgrep(
        self, rg: str, args: GrepInput, target: Path, ctx: ToolContext
    ) -> tuple[list[str], str | None]:
        argv = [rg, "--color=never", "--no-heading", "--path-separator", "/"]
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
        # After the user's glob: in ripgrep the last matching glob wins. (Globs
        # don't filter an explicitly named file; the permission check covers that.)
        argv += ripgrep_exclude_globs(target, ctx.project_root)
        argv += ["--regexp", args.pattern, "--", str(target)]
        code, out, err = await _run(argv, ctx.cwd)
        if code == 1:
            return [], None
        if code != 0:
            return [], f"Grep failed: {err.strip()}"
        return [_strip_prefix(line, ctx.cwd) for line in out.splitlines()], None
