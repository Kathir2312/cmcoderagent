"""Hooks: shell commands that run on agent events (Claude Code's format).

    "hooks": {
      "PreToolUse": [{"matcher": "Bash|Edit", "hooks": [{"type": "command", "command": "..."}]}],
      "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "..."}]}]
    }

A hook gets the event as JSON on stdin and runs in the project folder, with
the same shell as the Bash tool (Git Bash on Windows), so hook commands use
the same syntax on every OS. It answers with its exit code and output:

- exit 0: success. Plain stdout from UserPromptSubmit and SessionStart is
  added to the model's context; JSON stdout can decide (below).
- exit 2: block. stderr is the reason, given to the model (PreToolUse: the
  tool doesn't run; UserPromptSubmit: the prompt isn't sent; PostToolUse:
  the model is told; Stop: the model continues with the reason).
- anything else: a non-blocking error, shown to the user as a warning.

JSON on stdout: {"decision": "block", "reason": "..."}, or
{"hookSpecificOutput": {"permissionDecision": "allow"|"deny"|"ask",
"permissionDecisionReason": "...", "additionalContext": "..."}}.
A PreToolUse "allow" skips a normal permission prompt only: deny rules and
high-risk commands are never overridden by a hook.

Hooks from your user settings and the managed settings always run (unless
the managed `allowManagedHooksOnly`); a trusted project's hooks are approved
one by one before they first run.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..compat import find_shell, kill_process_tree, new_process_group_kwargs
from ..config.settings import (
    HookCommand,
    HookConfig,
    Settings,
    approve,
    config_fingerprint,
    is_approved,
)
from ..tools.shell import child_env

HookEvent = Literal[
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "SessionStart",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "Notification",
]
HookOrigin = Literal["user", "project", "managed"]
MAX_OUTPUT_CHARS = 10_000


@dataclass
class Hook:
    event: HookEvent
    matcher: str | None
    command: HookCommand
    origin: HookOrigin
    approved: bool | None = None  # project hooks: None until asked this session

    @property
    def key(self) -> str:
        return f"hook:{self.event}:{self.command.command}"

    def matches(self, value: str | None) -> bool:
        if not self.matcher or self.matcher == "*":
            return True
        if value is None:
            return False
        try:
            return re.fullmatch(self.matcher, value) is not None
        except re.error:
            return self.matcher == value


@dataclass
class HookOutcome:
    """What the hooks of one event decided, combined."""

    block: bool = False
    reason: str = ""
    # PreToolUse: "allow" / "deny" / "ask" (None: no opinion).
    permission: Literal["allow", "deny", "ask"] | None = None
    context: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# Asks the user to approve a project's hook before it first runs.
ApproveHookFn = Callable[[Hook], Awaitable[bool]]


class HookRunner:
    def __init__(self, settings: Settings, project_root: Path) -> None:
        self.root = project_root
        self.hooks: list[Hook] = []
        sources: list[tuple[HookOrigin, HookConfig]] = [("managed", settings.managed_hooks)]
        if not settings.allow_managed_hooks_only:
            user: tuple[HookOrigin, HookConfig] = ("user", settings.hooks)
            project: tuple[HookOrigin, HookConfig] = ("project", settings.project_hooks)
            sources += [user, project]
        for origin, config in sources:
            for event, matchers in config.items():
                for m in matchers:
                    for command in m.hooks:
                        self.hooks.append(Hook(event, m.matcher, command, origin))

    def has(self, event: HookEvent) -> bool:
        return any(h.event == event for h in self.hooks)

    async def run(
        self,
        event: HookEvent,
        payload: dict[str, Any],
        *,
        match: str | None = None,
        approve_fn: ApproveHookFn | None = None,
    ) -> HookOutcome:
        out = HookOutcome()
        for hook in [h for h in self.hooks if h.event == event and h.matches(match)]:
            if hook.origin == "project" and not await self._approved(hook, approve_fn):
                continue
            await self._run_one(hook, {**payload, "hook_event_name": event}, out)
            if out.block:
                break  # the first hook to block decides
        return out

    async def _approved(self, hook: Hook, approve_fn: ApproveHookFn | None) -> bool:
        if hook.approved is None:
            fp = config_fingerprint(hook.command)
            key = hook.key
            if is_approved(self.root, key, fp):
                hook.approved = True
            elif approve_fn is not None and await approve_fn(hook):
                approve(self.root, key, fp)
                hook.approved = True
            else:
                hook.approved = False
        return hook.approved

    async def _run_one(self, hook: Hook, payload: dict[str, Any], out: HookOutcome) -> None:
        shell = find_shell()
        if shell is None:
            out.warnings.append(f"{hook.event} hook skipped: no bash to run it with")
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                shell,
                "--noprofile",
                "--norc",
                "-c",
                hook.command.command,
                cwd=self.root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_hook_env(self.root),
                **new_process_group_kwargs(),
            )
        except OSError as e:
            out.warnings.append(f"{hook.event} hook could not start: {e}")
            return
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(json.dumps(payload).encode()), hook.command.timeout
            )
        except TimeoutError:
            kill_process_tree(proc.pid)
            await proc.wait()
            out.warnings.append(
                f"{hook.event} hook timed out after {hook.command.timeout:.0f}s: {hook.command.command}"
            )
            return
        except asyncio.CancelledError:
            kill_process_tree(proc.pid)
            raise
        stdout = stdout_b.decode(errors="replace").strip()[:MAX_OUTPUT_CHARS]
        stderr = stderr_b.decode(errors="replace").strip()[:MAX_OUTPUT_CHARS]
        if proc.returncode == 2:
            out.block = True
            out.permission = "deny"
            out.reason = stderr or stdout or f"blocked by a {hook.event} hook"
            return
        if proc.returncode != 0:
            detail = f": {stderr.splitlines()[0]}" if stderr else ""
            out.warnings.append(f"{hook.event} hook failed (exit {proc.returncode}){detail}")
            return
        data = _json_object(stdout)
        if data is None:
            if stdout and hook.event in ("UserPromptSubmit", "SessionStart"):
                out.context.append(stdout)
            return
        specific = data.get("hookSpecificOutput") or {}
        if isinstance(specific, dict):
            decision = specific.get("permissionDecision")
            if decision in ("allow", "deny", "ask"):
                # The strictest answer wins across hooks.
                order = {"allow": 0, "ask": 1, "deny": 2}
                if out.permission is None or order[decision] > order[out.permission]:
                    out.permission = decision
                    out.reason = str(specific.get("permissionDecisionReason") or out.reason)
                if decision == "deny":
                    out.block = True
            if ctx := specific.get("additionalContext"):
                out.context.append(str(ctx))
        if data.get("decision") == "block" or data.get("continue") is False:
            out.block = True
            out.reason = str(data.get("reason") or data.get("stopReason") or "blocked by a hook")


def _json_object(text: str) -> dict[str, Any] | None:
    if not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _hook_env(root: Path) -> dict[str, str]:
    """Like the Bash tool's environment, plus where the project is."""
    env = child_env(root)
    env["CMCODER_PROJECT_DIR"] = str(root)
    env.setdefault("CMCODER", "1")
    return env
