"""Permission modes and allow/deny rules, mirroring Claude Code.

Rule syntax:
  Tool                 any use of the tool, e.g. "Read"
  Bash(npm test:*)     commands starting with "npm test" (prefix match)
  Bash(git status)     exactly this command
  Edit(src/**)         file tools on paths matching a gitignore-style pattern,
                       relative to the project root. Edit rules also cover Write.

Order of evaluation: deny rules > high-risk shell commands (always ask, in
every mode; see core/risk.py) > protected paths (always ask before editing
cmcoder settings or .git, except in bypassPermissions) > allow rules > built-in
secret-file protection > the permission mode's defaults.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pathspec

from ..config.settings import config_dir
from ..sensitive import is_secret
from ..tools.base import Tool, ToolContext
from .risk import high_risk_reason

MODES = ("default", "acceptEdits", "plan", "bypassPermissions")
BYPASS_DISABLED_MESSAGE = "bypassPermissions is disabled by your organisation's managed settings."


class ModeNotAllowed(ValueError):
    pass


HIGH_RISK_MODES = ("ask", "deny")
FILE_EDIT_TOOLS = {"Edit", "Write"}
_RULE_RE = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*(?:\((.*)\))?\s*$", re.S)

# Characters that chain or redirect commands. A prefix rule like Bash(npm test:*)
# never approves a command containing them ("npm test; rm -rf ~").
_SHELL_OPERATORS = (";", "&", "|", "`", "$(", ">", "<", "\n", "\r")

# Read-only commands allowed without asking. Each entry maps a command to a
# check on its arguments, because some read-only commands have writing options
# (`git branch -D`, `git diff --output=FILE`, `date -s`).
_GIT_BRANCH_LIST_FLAGS = {
    "-a",
    "--all",
    "-r",
    "--remotes",
    "-v",
    "-vv",
    "--verbose",
    "-l",
    "--list",
    "--show-current",
    "--no-color",
    "--color",
    "--merged",
    "--no-merged",
    "--contains",
}


def _git_read_args(args: list[str]) -> bool:
    return not any(a.startswith(("--output", "--ext-diff")) for a in args)


SAFE_COMMANDS: dict[str, Callable[[list[str]], bool]] = {
    "ls": lambda args: True,
    "pwd": lambda args: True,
    "whoami": lambda args: True,
    "uname": lambda args: True,
    "which": lambda args: True,
    "date": lambda args: all(a.startswith("+") for a in args),
    "git status": _git_read_args,
    "git diff": _git_read_args,
    "git log": _git_read_args,
    "git show": _git_read_args,
    "git branch": lambda args: all(a in _GIT_BRANCH_LIST_FLAGS for a in args),
}

# Paths the agent may not change without asking, even in acceptEdits mode:
# cmcoder's own settings (an agent could grant itself permissions) and git
# internals (hooks run code on the next commit).
PROTECTED_PATTERNS = (".cmcoder/**", ".git/**")

_PROTECTED_SPEC = pathspec.GitIgnoreSpec.from_lines(PROTECTED_PATTERNS)


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionCheck:
    decision: Decision
    reason: str = ""
    # High-risk commands need a fresh approval every time: no "always allow".
    high_risk: bool = False


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


def is_protected(path: Path, root: Path) -> bool:
    if path.is_relative_to(config_dir().resolve()):
        return True
    rel = _rel(path, root)
    return rel is not None and _PROTECTED_SPEC.match_file(rel)


def is_safe_command(command: str) -> bool:
    """Read-only commands that never need approval."""
    command = command.strip()
    if not command or has_shell_operators(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    for name, args_ok in SAFE_COMMANDS.items():
        n = len(name.split())
        # The command must start with the exact words, e.g. no `git -c x=y diff`.
        if words[:n] == name.split():
            return args_ok(words[n:])
    return False


class PermissionPolicy:
    def __init__(
        self,
        mode: str = "default",
        allow: list[str] | None = None,
        deny: list[str] | None = None,
        high_risk: str = "ask",
        *,
        bypass_disabled: bool = False,
        allow_rules_locked: bool = False,
    ) -> None:
        if high_risk not in HIGH_RISK_MODES:
            raise ValueError(f"highRiskCommands must be one of {', '.join(HIGH_RISK_MODES)}")
        # Managed settings: bypassPermissions can't be chosen in any way
        # (flag, settings, /mode, Shift+Tab), and only the managed allow rules
        # apply ("always allow" answers are not offered).
        self.bypass_disabled = bypass_disabled
        self.allow_rules_locked = allow_rules_locked
        self._mode = "default"
        self.mode = mode
        self.high_risk = high_risk
        self.allow = [Rule.parse(r) for r in allow or []]
        self.deny = [Rule.parse(r) for r in deny or []]

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in MODES:
            raise ValueError(
                f"Unknown permission mode {value!r}; expected one of {', '.join(MODES)}"
            )
        if value == "bypassPermissions" and self.bypass_disabled:
            raise ModeNotAllowed(BYPASS_DISABLED_MESSAGE)
        self._mode = value

    def available_modes(self) -> list[str]:
        return [m for m in MODES if not (m == "bypassPermissions" and self.bypass_disabled)]

    def add_allow(self, rule: str) -> None:
        if self.allow_rules_locked:
            return
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
        if tool.name == "Bash" and (risk := high_risk_reason(str(target or ""))):
            # Before allow rules and modes: no rule, acceptEdits or
            # bypassPermissions approves these on a person's behalf.
            if self.high_risk == "deny":
                return PermissionCheck(
                    Decision.DENY,
                    f"high-risk command ({risk}) is blocked by policy. "
                    "Ask the user to run it themselves if it is really needed.",
                    high_risk=True,
                )
            if self.mode == "plan":
                return PermissionCheck(
                    Decision.DENY, "plan mode is read-only; propose a plan instead", high_risk=True
                )
            return PermissionCheck(Decision.ASK, f"high-risk: {risk}", high_risk=True)
        if (
            tool.name in FILE_EDIT_TOOLS
            and isinstance(target, Path)
            and self.mode != "bypassPermissions"
            and is_protected(target, ctx.project_root)
        ):
            # Before allow rules: a broad rule like Edit(**) must not let the
            # agent rewrite its own permissions or git hooks.
            return PermissionCheck(
                Decision.ASK,
                f"{ctx.display(target)} is protected (cmcoder settings or git internals)",
            )
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
            # No target (e.g. TodoWrite): nothing on disk is touched.
            if target is None or inside or self.mode == "bypassPermissions":
                return PermissionCheck(Decision.ALLOW)
            return PermissionCheck(Decision.ASK, "reads outside the project directory")

        if self.mode == "bypassPermissions":
            return PermissionCheck(Decision.ALLOW)

        if tool.name == "Bash":
            if is_safe_command(str(target or "")):
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
