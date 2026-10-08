"""Custom slash commands: Markdown files that expand to a prompt.

    ~/.cmcoder/commands/review.md          -> /review          (yours, every project)
    .cmcoder/commands/review.md            -> /review          (this project's; yours win)
    .cmcoder/commands/db/migrate.md        -> /db:migrate      (subfolders are namespaces)

A file is the prompt, with optional frontmatter (Claude Code's format):

    ---
    description: Review a file for bugs
    argument-hint: <file>
    allowed-tools: Read, Grep, Bash(git diff:*)
    ---
    Review $ARGUMENTS for bugs. Start with $1.

`$ARGUMENTS` is everything after the command; `$1`..`$9` are its words
(quotes group words). `allowed-tools` are allow rules for that turn only; a
repository's commands get them only in a trusted project. Commands never run
shell code by themselves.

MCP servers' prompts are commands too: `/mcp__<server>__<prompt> args...`.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..config.settings import config_dir
from ..sensitive import safe_project_file

# Commands the front ends handle themselves; a file can't replace them.
BUILT_IN = {
    "help", "clear", "resume", "rewind", "todos", "compact", "model", "mode",
    "cost", "mcp", "index", "agents", "critic", "image", "exit", "quit",
}  # fmt: skip
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
MAX_FILE_CHARS = 50_000


@dataclass
class SlashCommand:
    name: str
    body: str
    origin: Literal["user", "project", "mcp"]
    description: str = ""
    argument_hint: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    path: Path | None = None
    mcp: Any = None  # (server, prompt) for MCP prompts


@dataclass
class Expansion:
    """A command turned into a prompt, with the allow rules for that turn."""

    name: str
    prompt: str
    allowed_tools: list[str]


def parse_file(text: str) -> tuple[dict[str, str], str]:
    """Simple frontmatter (`key: value` lines between `---`) and the body."""
    meta: dict[str, str] = {}
    text = text.removeprefix("\ufeff").replace("\r\n", "\n")  # Windows editors: BOM, CRLF
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                key, sep, value = line.partition(":")
                if sep and key.strip():
                    meta[key.strip().lower()] = value.strip().strip("\"'")
            text = text[end + 4 :].lstrip("\n")
    return meta, text.strip()


def split_tools(value: str) -> list[str]:
    """`Read, Bash(git diff:*), Grep` -> rules (commas inside parentheses kept)."""
    out, depth, cur = [], 0, ""
    for ch in value:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return [t for t in out if t]


def _load_dir(
    folder: Path, origin: Literal["user", "project"], root: Path | None = None
) -> dict[str, SlashCommand]:
    """`root`: for a project's files, which must really be inside it."""
    found: dict[str, SlashCommand] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.rglob("*.md")):
        if root is not None and not safe_project_file(path, root):
            continue
        rel = path.relative_to(folder).with_suffix("")
        name = ":".join(rel.parts)
        if not NAME_RE.match(name) or name in BUILT_IN:
            continue
        try:
            text = path.read_text(encoding="utf-8")[:MAX_FILE_CHARS]
        except (OSError, UnicodeDecodeError):
            continue
        meta, body = parse_file(text)
        found[name] = SlashCommand(
            name=name,
            body=body,
            origin=origin,
            description=meta.get("description", ""),
            argument_hint=meta.get("argument-hint", ""),
            allowed_tools=split_tools(meta.get("allowed-tools", "")),
            path=path,
        )
    return found


def load_commands(project_root: Path) -> dict[str, SlashCommand]:
    """This project's commands, then yours on top (yours win on a name clash)."""
    commands = _load_dir(project_root / ".cmcoder" / "commands", "project", project_root)
    commands.update(_load_dir(config_dir() / "commands", "user"))
    return commands


class CommandSource:
    """Where an agent finds its commands; read again on each use, so a new or
    edited file works without restarting."""

    def __init__(self, project_root: Path, project_trusted: bool) -> None:
        self.root = project_root
        self.trusted = project_trusted

    def load(self) -> dict[str, SlashCommand]:
        commands = load_commands(self.root)
        if not self.trusted:
            for c in commands.values():
                if c.origin == "project":
                    c.allowed_tools = []  # a repository can't grant itself tools
        return commands


def mcp_prompt_commands(servers: list[Any]) -> dict[str, SlashCommand]:
    """Connected MCP servers' prompts, as `mcp__<server>__<prompt>` commands."""
    from ..mcp_client import tool_name

    found: dict[str, SlashCommand] = {}
    for s in servers:
        for p in s.prompts:
            args = [a.name for a in (p.arguments or [])]
            found[tool_name(s.name, p.name)] = SlashCommand(
                name=tool_name(s.name, p.name),
                body="",
                origin="mcp",
                description=(p.description or p.title or "").strip()
                or f"Prompt {p.name} from MCP server {s.name}",
                argument_hint=" ".join(f"<{a}>" for a in args),
                mcp=(s, p),
            )
    return found


def prompt_arguments(names: list[str], arguments: str) -> dict[str, str]:
    """Words to an MCP prompt's arguments, in order; the last one takes the rest."""
    try:
        words = shlex.split(arguments)
    except ValueError:
        words = arguments.split()
    out: dict[str, str] = {}
    for i, name in enumerate(names):
        if i >= len(words):
            break
        out[name] = " ".join(words[i:]) if i == len(names) - 1 else words[i]
    return out


def prompt_text(result: Any) -> str:
    """An MCP GetPromptResult's messages as one prompt."""
    parts: list[str] = []
    for m in result.messages:
        content = m.content
        text = getattr(content, "text", None)
        if text is None and (resource := getattr(content, "resource", None)) is not None:
            text = getattr(resource, "text", None)
        if text:
            parts.append(text if m.role == "user" else f"[{m.role}]\n{text}")
    return "\n\n".join(parts)


def substitute(body: str, arguments: str) -> str:
    try:
        words = shlex.split(arguments)
    except ValueError:  # an unbalanced quote: plain split
        words = arguments.split()
    text = body.replace("$ARGUMENTS", arguments)
    text = re.sub(
        r"\$([1-9])",
        lambda m: words[int(m.group(1)) - 1] if int(m.group(1)) <= len(words) else "",
        text,
    )
    if "$ARGUMENTS" not in body and not re.search(r"\$[1-9]", body) and arguments:
        text = f"{text}\n\n{arguments}"  # no placeholder: the arguments go at the end
    return text


def split_line(line: str) -> tuple[str, str]:
    """`/name the args` -> ("name", "the args")."""
    name, _, arguments = line.strip()[1:].partition(" ")
    return name, arguments.strip()


def help_lines(commands: dict[str, SlashCommand]) -> list[str]:
    """`/help` lines for custom commands and MCP prompts."""
    lines = []
    for c in sorted(commands.values(), key=lambda c: c.name):
        usage = f"/{c.name} {c.argument_hint}".strip()
        where = {"user": "", "project": " (project)", "mcp": " (MCP)"}[c.origin]
        text = c.description or "custom command"
        lines.append(
            f"  {usage:<18} {text}{where}"
            if len(usage) <= 18
            else f"  {usage}\n  {'':<18} {text}{where}"
        )
    return lines
