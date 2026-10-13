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

Shell commands are judged part by part (`a | b && c`, see core/readonly.py):
a command line runs without asking when every part is read-only (in every
mode) or allowed by a rule, and the files it writes through redirections are
allowed too. A deny rule that matches any part denies the whole line. Modes:

  default            ask before changes (file edits, commands that aren't read-only)
  acceptEdits        file edits in the project without asking; commands still ask
  auto               ask only for risky actions: high-risk and outward commands
                     (pushes, publishing, deploys, uploads, package installs),
                     protected files, and files outside the project or secret
  plan               read only: no edits, no commands that change anything
  bypassPermissions  everything but high-risk commands
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pathspec

from ..config.settings import config_dir
from ..sensitive import is_secret
from ..tools.base import Tool, ToolContext
from .readonly import READERS, Segment, Verdict, classify, parse
from .risk import high_risk_reason, outward_reason

MODES = ("default", "acceptEdits", "auto", "plan", "bypassPermissions")
BYPASS_DISABLED_MESSAGE = "bypassPermissions is disabled by your organisation's managed settings."
AUTO_DISABLED_MESSAGE = "The auto mode is disabled by your organisation's managed settings."


class ModeNotAllowed(ValueError):
    pass


HIGH_RISK_MODES = ("ask", "deny")
FILE_EDIT_TOOLS = {"Edit", "Write"}
_RULE_RE = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*(?:\((.*)\))?\s*$", re.S)

# Characters that chain or redirect commands. A prefix rule like Bash(npm test:*)
# never approves a command containing them ("npm test; rm -rf ~").
_SHELL_OPERATORS = (";", "&", "|", "`", "$(", ">", "<", "\n", "\r")

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


def _words(spec: str) -> list[str]:
    try:
        return shlex.split(spec)
    except ValueError:
        return spec.split()


def words_match(spec: str, words: list[str]) -> bool:
    """A Bash rule's spec against one part of a command line (its words)."""
    if spec.endswith(":*"):
        prefix = _words(spec[:-2])
        return bool(prefix) and words[: len(prefix)] == prefix
    return words == _words(spec)


def _all_parts(command: str, depth: int = 0) -> list[list[str]]:
    """The words of every part of a command line, those inside $(...) too."""
    segments = parse(command)
    if segments is None or depth > 3:
        return []
    out: list[list[str]] = []
    for seg in segments:
        out.append(seg.words)
        for inner in seg.inner:
            out.extend(_all_parts(inner, depth + 1))
    return out


def shell_cwd(args: object, ctx: ToolContext) -> Path:
    """Where the command will run: the persistent shell keeps `cd` between calls."""
    shell = (
        ctx.unsandboxed_shell if getattr(args, "dangerously_disable_sandbox", False) else ctx.shell
    )
    cwd = getattr(shell, "current_cwd", None)
    return cwd if isinstance(cwd, Path) else ctx.cwd


class PermissionPolicy:
    def __init__(
        self,
        mode: str = "default",
        allow: list[str] | None = None,
        deny: list[str] | None = None,
        high_risk: str = "ask",
        *,
        bypass_disabled: bool = False,
        auto_disabled: bool = False,
        allow_rules_locked: bool = False,
    ) -> None:
        if high_risk not in HIGH_RISK_MODES:
            raise ValueError(f"highRiskCommands must be one of {', '.join(HIGH_RISK_MODES)}")
        # Managed settings: bypassPermissions can't be chosen in any way
        # (flag, settings, /mode, Shift+Tab), and only the managed allow rules
        # apply ("always allow" answers are not offered).
        self.bypass_disabled = bypass_disabled
        self.auto_disabled = auto_disabled
        self.allow_rules_locked = allow_rules_locked
        self._mode = "default"
        self.mode = mode
        self.high_risk = high_risk
        self.allow = [Rule.parse(r) for r in allow or []]
        self.deny = [Rule.parse(r) for r in deny or []]
        # A slash command's `allowed-tools`: allow rules for one turn only.
        self.turn_allow: list[Rule] = []

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
        if value == "auto" and self.auto_disabled:
            raise ModeNotAllowed(AUTO_DISABLED_MESSAGE)
        self._mode = value

    def available_modes(self) -> list[str]:
        return [
            m
            for m in MODES
            if not (m == "bypassPermissions" and self.bypass_disabled)
            and not (m == "auto" and self.auto_disabled)
        ]

    def add_allow(self, rule: str) -> None:
        if self.allow_rules_locked:
            return
        parsed = Rule.parse(rule)
        if all(str(r) != str(parsed) for r in self.allow):
            self.allow.append(parsed)

    def set_turn_allow(self, rules: list[str]) -> None:
        """Allow rules for the current turn (none with managed-only rules)."""
        self.turn_allow = [] if self.allow_rules_locked else [Rule.parse(r) for r in rules]

    def _rule_matches(
        self, rule: Rule, tool: Tool, target: str | Path | None, ctx: ToolContext
    ) -> bool:
        whole_server = (
            rule.tool.startswith("mcp__")
            and rule.spec is None
            and tool.name.startswith(rule.tool + "__")
        )  # `mcp__github` covers every tool of that MCP server
        if whole_server:
            return True
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
        if tool.name == "Bash" and isinstance(target, str):
            # `cd x && git push`, `echo $(curl ...)`: a deny rule for any part denies all.
            for words in _all_parts(target):
                for rule in self.deny:
                    if rule.tool == "Bash" and rule.spec and words_match(rule.spec, words):
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
            tool.name == "Bash"
            and ctx.sandbox is not None
            and getattr(args, "dangerously_disable_sandbox", False)
        ):
            # Before allow rules: leaving the sandbox is the user's call each time.
            if not ctx.sandbox.allow_unsandboxed:
                return PermissionCheck(
                    Decision.DENY,
                    "running commands outside the sandbox is turned off by settings; "
                    "tell the user what the sandbox blocks",
                )
            if self.mode == "plan":
                return PermissionCheck(
                    Decision.DENY, "plan mode is read-only; propose a plan instead"
                )
            if self.mode != "bypassPermissions":
                return PermissionCheck(Decision.ASK, "runs outside the sandbox", high_risk=True)
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
        for rule in self.turn_allow:
            if self._rule_matches(rule, tool, target, ctx):
                return PermissionCheck(Decision.ALLOW, f"allowed by the command ({rule})")

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

        if tool.name == "Bash":
            return self._check_bash(str(target or ""), args, ctx)

        if self.mode == "bypassPermissions":
            return PermissionCheck(Decision.ALLOW)

        if tool.name in FILE_EDIT_TOOLS:
            if self.mode == "plan":
                return PermissionCheck(
                    Decision.DENY, "plan mode is read-only; propose a plan instead"
                )
            if self.mode in ("acceptEdits", "auto") and inside:
                return PermissionCheck(Decision.ALLOW)
            return PermissionCheck(Decision.ASK)

        return PermissionCheck(Decision.ASK)

    def _part_allowed(self, seg: Segment) -> bool:
        """One part of a command line, by the allow rules (never a part with $(...))."""
        if seg.dynamic or not seg.words:
            return False
        for rule in [*self.allow, *self.turn_allow]:
            if rule.tool != "Bash":
                continue
            if rule.spec is None or rule.spec in ("", "*") or words_match(rule.spec, seg.words):
                return True
        return False

    def write_allowed(self, path: Path, ctx: ToolContext) -> bool:
        """A file a command writes through a redirection (`> notes.txt`): like an edit."""
        root = ctx.project_root
        if self.mode == "bypassPermissions":
            return True
        if is_protected(path, root) or is_secret(path, root):
            return False
        for rule in [*self.allow, *self.turn_allow]:
            if rule.tool == "Edit" and (
                rule.spec is None or rule.spec in ("", "*") or path_matches(rule.spec, path, root)
            ):
                return True
        return self.mode in ("acceptEdits", "auto") and _rel(path, root) is not None

    def _check_bash(self, command: str, args: object, ctx: ToolContext) -> PermissionCheck:
        segments = parse(command)
        verdicts: list[Verdict] = (
            classify(segments, shell_cwd(args, ctx), ctx.project_root) if segments else []
        )
        writes = [w for v in verdicts for w in v.writes]
        unknown_write = any(v.unknown_write for v in verdicts)
        reads_ok = all(v.reads_ok for v in verdicts)
        if segments and reads_ok and not unknown_write:
            if not writes and all(v.read_only for v in verdicts):
                return PermissionCheck(Decision.ALLOW, "read-only")
            if all(
                v.read_only or self._part_allowed(seg)
                for seg, v in zip(segments, verdicts, strict=True)
            ) and all(self.write_allowed(w, ctx) for w in writes):
                return PermissionCheck(Decision.ALLOW, "allowed by rules")
        if self.mode == "bypassPermissions":
            return PermissionCheck(Decision.ALLOW)
        if self.mode == "plan":
            return PermissionCheck(Decision.DENY, "plan mode is read-only; propose a plan instead")
        if ctx.sandbox is not None and ctx.sandbox.auto_allow:
            return PermissionCheck(Decision.ALLOW, "runs in the sandbox")
        if self.mode != "auto":
            return PermissionCheck(Decision.ASK)
        # auto: everything but what's risky.
        if reason := outward_reason(command):
            return PermissionCheck(Decision.ASK, reason)
        if segments is None:
            return PermissionCheck(Decision.ASK, "the command doesn't parse")
        for w in writes:
            if not self.write_allowed(w, ctx):
                return PermissionCheck(
                    Decision.ASK, f"writes {ctx.display(w)} (outside the project, or protected)"
                )
        if unknown_write:
            return PermissionCheck(Decision.ASK, "writes to a file named by a variable")
        if not reads_ok or any(
            not v.read_only and seg.words[:1] and seg.words[0] in READERS
            for seg, v in zip(segments, verdicts, strict=True)
        ):
            return PermissionCheck(Decision.ASK, "reads files outside the project or secret files")
        return PermissionCheck(Decision.ALLOW, "auto mode")


def _part_rule(seg: Segment, read_program: bool) -> str:
    """The rule "always allow" saves for one part of a command line."""
    words = seg.words
    if read_program or words[0] == "cd":
        # `cat /etc/hosts`: only this one (a rule for all of cat would cover secrets).
        return f"Bash({shlex.join(words)})"
    if len(words) >= 2 and re.fullmatch(r"[A-Za-z][\w.-]*", words[1]):
        return f"Bash({words[0]} {words[1]}:*)"
    return f"Bash({words[0]}:*)"


def _path_rule(name: str, target: Path, ctx: ToolContext) -> str:
    rel = _rel(target, ctx.project_root)
    if rel is None:
        return f"{name}({target.as_posix()})"
    parent = Path(rel).parent.as_posix()
    return f"{name}({parent}/**)" if parent != "." else f"{name}(/{rel})"


def suggest_rules(tool: Tool, args: object, ctx: ToolContext) -> list[str]:
    """The rules saved when the user answers "always allow": for a command line,
    one per part that isn't read-only, and one per file it writes."""
    target = tool.permission_target(args, ctx)
    if tool.name == "Bash" and isinstance(target, str):
        segments = parse(target)
        if not segments or any(seg.dynamic for seg in segments):
            return [f"Bash({target})"]
        verdicts = classify(segments, shell_cwd(args, ctx), ctx.project_root)
        if any(v.unknown_write for v in verdicts):
            return [f"Bash({target})"]
        rules = [
            _part_rule(seg, seg.words[0] in READERS)
            for seg, v in zip(segments, verdicts, strict=True)
            if seg.words and not v.read_only
        ]
        rules += [_path_rule("Edit", w, ctx) for v in verdicts for w in v.writes]
        return list(dict.fromkeys(rules)) or [f"Bash({target})"]
    if isinstance(target, Path):
        return [_path_rule("Edit" if tool.name in FILE_EDIT_TOOLS else tool.name, target, ctx)]
    return [tool.name]


def suggest_rule(tool: Tool, args: object, ctx: ToolContext) -> str:
    """The rules "always allow" saves, as the user sees them."""
    return ", ".join(suggest_rules(tool, args, ctx))
