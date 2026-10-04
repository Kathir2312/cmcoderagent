"""Splitting files into pieces ("chunks") for the index, along code structure.

Python files are split with Python's own parser: each top-level function and
class (a large class: its methods one by one), plus the code between them.
Other files are split at lines that start a definition in most languages
(`function`, `class`, `def`, `func`, `fn`, `interface`, ...) when they're not
indented, or after blank lines, keeping pieces under a size limit with a
small overlap so nothing falls between two pieces.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_LINES = 80
MAX_CHARS = 2400
MIN_LINES = 4
OVERLAP = 5
# Neighbouring pieces shorter than this are joined (small functions together).
SMALL = 15

LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".m": "objc",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".md": "markdown",
    ".rst": "text",
    ".txt": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".vue": "vue",
    ".svelte": "svelte",
    ".tf": "terraform",
    ".gradle": "gradle",
    ".dart": "dart",
    ".lua": "lua",
    ".r": "r",
}
# Files known by name, without a useful extension.
NAMED = {"Dockerfile": "docker", "Makefile": "make", "Jenkinsfile": "groovy"}

# A line that starts a definition (when not indented), and the name it defines.
_DEFINITION = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+|internal\s+)?"
    r"(?:static\s+|final\s+|abstract\s+|async\s+|pub(?:\(\w+\))?\s+|unsafe\s+|override\s+)*"
    r"(?:def|class|function|func|fn|interface|struct|enum|trait|impl|type|module|object|"
    r"record|namespace|const|let|var|val|CREATE\s+(?:OR\s+REPLACE\s+)?\w+)\s+"
    r"(?:\([^)]*\)\s*)?([A-Za-z_$][\w$.]*)",
)
# Markdown headings split documents into sections.
_HEADING = re.compile(r"^#{1,4}\s+(.+)")


def language_of(path: str) -> str | None:
    p = PurePosixPath(path)
    return NAMED.get(p.name) or LANGUAGES.get(p.suffix.lower())


@dataclass
class Chunk:
    path: str  # relative to the project, with "/"
    start_line: int  # 1-based, inclusive
    end_line: int
    text: str
    language: str = "text"
    symbol: str | None = None

    @property
    def id(self) -> str:
        h = hashlib.sha1(f"{self.path}\0{self.start_line}\0{self.text}".encode())
        return h.hexdigest()[:24]

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def embedding_text(self) -> str:
        """What is embedded: the path and name help find the piece by meaning."""
        head = f"File: {self.path}"
        if self.symbol:
            head += f"\nDefines: {self.symbol}"
        return f"{head}\n\n{self.text}"


def chunk_file(path: str, text: str) -> list[Chunk]:
    language = language_of(path) or "text"
    lines = text.splitlines()
    if not any(line.strip() for line in lines):
        return []
    if language == "python":
        try:
            spans = _python_spans(text, len(lines))
        except (SyntaxError, ValueError, RecursionError):
            spans = _generic_spans(lines, markdown=False)
    else:
        spans = _generic_spans(lines, markdown=language == "markdown")
    chunks: list[Chunk] = []
    for start, end, symbol in _merge_small(spans):
        for s, e in _limit(lines, start, end):
            body = "\n".join(lines[s - 1 : e]).strip("\n")
            if body.strip():
                chunks.append(Chunk(path, s, e, body, language, symbol))
    return chunks


def _merge_small(spans: list[tuple[int, int, str | None]]) -> list[tuple[int, int, str | None]]:
    out: list[tuple[int, int, str | None]] = []
    for start, end, symbol in spans:
        if out:
            p_start, p_end, p_symbol = out[-1]
            small = p_end - p_start + 1 < SMALL or end - start + 1 < MIN_LINES
            if small and end - p_start + 1 <= MAX_LINES:
                names = [n for n in (p_symbol, symbol) if n]
                joined = ", ".join(dict.fromkeys(", ".join(names).split(", "))) or None
                out[-1] = (p_start, end, joined if joined and joined.count(",") < 4 else p_symbol)
                continue
        out.append((start, end, symbol))
    return out


def _limit(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """A span cut into windows of at most MAX_LINES/MAX_CHARS, overlapping a little."""
    out: list[tuple[int, int]] = []
    s = start
    while s <= end:
        e = s
        size = 0
        while e <= end and e - s < MAX_LINES:
            size += len(lines[e - 1]) + 1
            if size > MAX_CHARS and e > s:
                break
            e += 1
        e -= 1
        out.append((s, e))
        if e >= end:
            break
        s = max(e + 1 - OVERLAP, s + 1)
    return out


def _python_spans(text: str, n_lines: int) -> list[tuple[int, int, str | None]]:
    tree = ast.parse(text)
    spans: list[tuple[int, int, str | None]] = []
    covered = 0

    def add_gap(until: int) -> None:
        nonlocal covered
        if until > covered + 1:
            spans.append((covered + 1, until - 1, None))
        covered = max(covered, until - 1)

    for node in tree.body:
        start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        end = node.end_lineno or node.lineno
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        add_gap(start)
        if isinstance(node, ast.ClassDef) and end - start + 1 > MAX_LINES:
            # A large class: its head, then each method on its own.
            methods = [
                n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            pos = start
            for i, m in enumerate(methods):
                m_start = min([m.lineno, *(d.lineno for d in m.decorator_list)])
                if i == 0 and m_start > pos:
                    spans.append((pos, m_start - 1, node.name))  # the class's head
                    pos = m_start
                # A method with the blank lines and comments before it.
                spans.append((pos, m.end_lineno or m.lineno, f"{node.name}.{m.name}"))
                pos = (m.end_lineno or m.lineno) + 1
            if pos <= end:
                spans.append((pos, end, node.name))
        else:
            spans.append((start, end, node.name))
        covered = end
    add_gap(n_lines + 1)
    return sorted(spans)


def _generic_spans(lines: list[str], markdown: bool) -> list[tuple[int, int, str | None]]:
    spans: list[tuple[int, int, str | None]] = []
    start = 1
    symbol: str | None = None
    for i, line in enumerate(lines, start=1):
        if i == start:
            symbol = _symbol(line, markdown)
            continue
        new = _symbol(line, markdown)
        blank_break = not line.strip() and i - start >= MAX_LINES // 2
        if (new and i - start >= MIN_LINES) or blank_break:
            spans.append((start, i - 1, symbol))
            start = i
            symbol = new
    spans.append((start, len(lines), symbol))
    return spans


def _symbol(line: str, markdown: bool) -> str | None:
    if markdown:
        m = _HEADING.match(line)
        return m.group(1).strip() if m else None
    if not line or line[0].isspace():
        return None
    m = _DEFINITION.match(line)
    return m.group(1) if m else None
