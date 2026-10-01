"""Permission modes and allow/deny rules, mirroring Claude Code.

Rule syntax:
  Tool                 any use of the tool, e.g. "Read"
  Bash(npm test:*)     commands starting with "npm test" (prefix match)
  Bash(git status)     exactly this command
  Edit(src/**)         file tools on paths matching a gitignore-style pattern,
                       relative to the project root. Edit rules also cover Write.

Order of evaluation: deny rules > allow rules > built-in secret-file protection
> the permission mode's defaults.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pathspec

from ..tools.base import Tool, ToolContext

MODES = ("default", "acceptEdits", "plan", "bypassPermissions")
FILE_EDIT_TOOLS = {"Edit", "Write"}
_RULE_RE = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*(?:\((.*)\))?\s*$", re.S)

# Characters that chain or redirect commands. A prefix rule like Bash(npm test:*)
# never approves a command containing them ("npm test; rm -rf ~").
_SHELL_OPERATORS = (";", "&", "|", "`", "$(", ">", "<", "\n", "\r")

# Read-only commands allowed without asking (when they contain no operators).
SAFE_COMMANDS = (
    "ls",
    "pwd",
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "which",
    "whoami",
    "date",
    "uname",
)

# Files never sent to the model unless an explicit allow rule names them.
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "secrets/**",
    ".cmcoder/credentials.json",
)

_SECRET_SPEC = pathspec.GitIgnoreSpec.from_lines(SECRET_PATTERNS)


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionCheck:
    decision: Decision
    reason: str = ""


@dataclass
class Rule:
    tool: str
    spec: str | None

    @classmethod
    def parse(cls, text: str) -> Rule:
        m = _RULE_RE.match(text)
        if not m:
            raise ValueError(f"Invalid permission rule: {text!r}")
        spec = m.group(2)
        return cls(m.group(1), spec.strip() if spec is not None else None)

    def __str__(self) -> str:
        return self.tool if self.spec is None else f"{self.tool}({self.spec})"


def has_shell_operators(command: str) -> bool:
    return any(op in command for op in _SHELL_OPERATORS)


def command_matches(spec: str, command: str) -> bool:
    command = command.strip()
    if spec.endswith(":*"):
        prefix = spec[:-2].strip()
        if has_shell_operators(command):
            return False
        return command == prefix or command.startswith(prefix + " ")
    return command == spec.strip()


def _rel(path: Path, root: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def path_matches(spec: str, path: Path, root: Path) -> bool:
    rel = _rel(path, root)
    target = rel if rel is not None else path.as_posix()
    spec_ = pathspec.GitIgnoreSpec.from_lines([spec])
    return spec_.match_file(target)


def is_secret(path: Path, root: Path) -> bool:
    rel = _rel(path, root) or path.name
    return _SECRET_SPEC.match_file(rel)


class PermissionPolicy:
    def __init__(
        self,
        mode: str = "default",
        allow: list[str] | None = None,
        deny: list[str] | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(
                f"Unknown permission mode {mode!r}; expected one of {', '.join(MODES)}"
            )
        self.mode = mode
        self.allow = [Rule.parse(r) for r in allow or []]
        self.deny = [Rule.parse(r) for r in deny or []]

    def add_allow(self, rule: str) -> None:
        parsed = Rule.parse(rule)
        if all(str(r) != str(parsed) for r in self.allow):
            self.allow.append(parsed)

    def _rule_matches(
        self, rule: Rule, tool: Tool, target: str | Path | None, ctx: ToolContext
    ) -> bool:
        if rule.tool != tool.name and not (rule.tool == "Edit" and tool.name in FILE_EDIT_TOOLS):
            return False
        if rule.spec is None or rule.spec in ("", "*"):
            return True
        if isinstance(target, str):
            return command_matches(rule.spec, target)
        if isinstance(target, Path):
            return path_matches(rule.spec, target, ctx.project_root)
        return False

    def check(self, tool: Tool, args: object, ctx: ToolContext) -> PermissionCheck:
        target = tool.permission_target(args, ctx)
        for rule in self.deny:
            if self._rule_matches(rule, tool, target, ctx):
                return PermissionCheck(Decision.DENY, f"denied by rule {rule}")
        for rule in self.allow:
            if self._rule_matches(rule, tool, target, ctx):
                return PermissionCheck(Decision.ALLOW, f"allowed by rule {rule}")

        if isinstance(target, Path) and is_secret(target, ctx.project_root):
            return PermissionCheck(
                Decision.DENY,
                f"{ctx.display(target)} looks like a secrets file and is never sent to the model. "
                "Add an allow rule (e.g. Read(path)) if this is intended.",
            )

        inside = isinstance(target, Path) and _rel(target, ctx.project_root) is not None

        if tool.read_only:
            if inside or self.mode == "bypassPermissions":
                return PermissionCheck(Decision.ALLOW)
            return PermissionCheck(Decision.ASK, "reads outside the project directory")

        if self.mode == "bypassPermissions":
            return PermissionCheck(Decision.ALLOW)

        if tool.name == "Bash":
            command = str(target or "")
            safe = any(command_matches(f"{c}:*", command) for c in SAFE_COMMANDS)
            if safe:
                return PermissionCheck(Decision.ALLOW)
            if self.mode == "plan":
                return PermissionCheck(
                    Decision.DENY, "plan mode is read-only; propose a plan instead"
                )
            return PermissionCheck(Decision.ASK)

        if tool.name in FILE_EDIT_TOOLS:
            if self.mode == "plan":
                return PermissionCheck(
                    Decision.DENY, "plan mode is read-only; propose a plan instead"
                )
            if self.mode == "acceptEdits" and inside:
                return PermissionCheck(Decision.ALLOW)
            return PermissionCheck(Decision.ASK)

        return PermissionCheck(Decision.ASK)


def suggest_rule(tool: Tool, args: object, ctx: ToolContext) -> str:
    """The rule saved when the user answers "always allow"."""
    target = tool.permission_target(args, ctx)
    if tool.name == "Bash" and isinstance(target, str):
        if has_shell_operators(target):
            return f"Bash({target})"
        words = target.split()
        if len(words) >= 2 and re.fullmatch(r"[A-Za-z][\w.-]*", words[1]):
            return f"Bash({words[0]} {words[1]}:*)"
        return f"Bash({words[0]}:*)" if words else "Bash"
    if isinstance(target, Path):
        name = "Edit" if tool.name in FILE_EDIT_TOOLS else tool.name
        rel = _rel(target, ctx.project_root)
        if rel is None:
            return f"{name}({target.as_posix()})"
        parent = Path(rel).parent.as_posix()
        return f"{name}({parent}/**)" if parent != "." else f"{name}(/{rel})"
    return tool.name
