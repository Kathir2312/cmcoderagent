"""Bash tool: runs commands in a persistent shell."""

from __future__ import annotations

from pydantic import Field

from ..compat import SHELL_HELP, find_shell
from .base import Tool, ToolContext, ToolInput, ToolResult, truncate_middle
from .shell import PersistentShell

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600


class BashInput(ToolInput):
    command: str = Field(description="The shell command to run.")
    timeout_seconds: int | None = Field(
        None,
        ge=1,
        le=MAX_TIMEOUT,
        description=f"Timeout in seconds (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}).",
    )
    description: str | None = Field(
        None, description="Short description of what the command does (5-10 words)."
    )


class BashTool(Tool):
    name = "Bash"
    description = (
        "Run a command in a persistent bash shell (the working directory and environment carry "
        "over between calls). stdin is closed, so interactive commands fail; pass non-interactive "
        "flags instead. Prefer Read/Edit/Glob/Grep over cat/sed/find/grep. Output is truncated "
        "if very long."
    )
    Input = BashInput

    def permission_target(self, args: BashInput, ctx: ToolContext) -> str:
        return args.command.strip()

    def describe(self, args: BashInput, ctx: ToolContext) -> str:
        cmd = args.command.strip().replace("\n", " ")
        return f"Bash({cmd if len(cmd) <= 80 else cmd[:77] + '...'})"

    async def run(self, args: BashInput, ctx: ToolContext) -> ToolResult:
        if ctx.shell is None:
            shell = find_shell()
            if shell is None:
                return ToolResult(f"No shell available. {SHELL_HELP}", is_error=True)
            ctx.shell = PersistentShell(ctx.cwd, shell)
        res = await ctx.shell.run(args.command, float(args.timeout_seconds or DEFAULT_TIMEOUT))
        out = truncate_middle(res.output.rstrip("\n"))
        if res.exit_code not in (0, None):
            out = f"{out}\n\nExit code {res.exit_code}" if out else f"Exit code {res.exit_code}"
        if not out:
            out = "(no output)"
        failed = res.exit_code != 0
        if res.timed_out:
            summary = "timed out"
        elif res.exit_code is None:
            summary = "shell exited"
        else:
            summary = f"exit {res.exit_code}"
        return ToolResult(out, is_error=failed, summary=summary)
