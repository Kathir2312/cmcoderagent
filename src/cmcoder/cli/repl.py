"""Interactive terminal mode (basic TUI: prompt_toolkit input + rich rendering).

A full Textual UI replaces this in Phase 1; the agent and protocol stay the same.
"""

from __future__ import annotations

import asyncio
import difflib
from contextlib import suppress
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.status import Status
from rich.syntax import Syntax
from rich.text import Text

from .. import __version__
from ..compat import InterruptHandler
from ..config.settings import Settings, config_dir
from ..core.agent import Agent, PermissionAnswer, PermissionRequest
from ..core.permissions import MODES
from ..protocol import events as ev
from ..providers.messages import Usage
from ..providers.openai_compat import OpenAICompatProvider
from .factory import AgentOptions, build_agent, resolve_model_profile

HELP = """\
[bold]Commands[/bold]
  /help              show this help
  /clear             start a new conversation
  /model [name]      show or switch the model (e.g. /model qwen3-27b)
  /mode [mode]       show or set the permission mode: default, acceptEdits, plan, bypassPermissions
  /cost              token usage for this session
  /exit              quit

[bold]Keys[/bold]
  Enter              send            Alt+Enter / Esc Enter   new line
  Shift+Tab          cycle permission mode
  Ctrl+C             interrupt the current turn (twice at the prompt to quit)
"""


class Repl:
    def __init__(self, settings: Settings, opts: AgentOptions, verbose: bool = False) -> None:
        self.settings = settings
        self.opts = opts
        self.verbose = verbose
        self.console = Console(highlight=False)
        self.agent: Agent | None = None
        self.session: PromptSession[str] | None = None
        self._status: Status | None = None
        self._live: Live | None = None
        self._buffer = ""
        self._last_prompt_tokens = 0
        self._turn_task: asyncio.Task[None] | None = None
        self._interrupt: InterruptHandler | None = None

    # -- rendering helpers ------------------------------------------------

    def _start_status(self, text: str = "Waiting for model…") -> None:
        if self._status is None:
            self._status = self.console.status(text, spinner="dots")
            self._status.start()
        else:
            self._status.update(text)

    def _stop_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.update(Markdown(self._buffer))
            self._live.stop()
            self._live = None
        self._buffer = ""

    def _render(self, event: ev.Event) -> None:
        c = self.console
        if isinstance(event, ev.ReasoningDelta):
            self._start_status("Thinking…")
            if self.verbose:
                self._stop_status()
                c.print(Text(event.text, style="dim italic"), end="")
        elif isinstance(event, ev.AssistantDelta):
            self._stop_status()
            if self._live is None:
                self._live = Live(
                    Markdown(""), console=c, refresh_per_second=8, vertical_overflow="visible"
                )
                self._live.start()
            self._buffer += event.text
            self._live.update(Markdown(self._buffer))
        elif isinstance(event, ev.AssistantMessage):
            self._stop_status()
            streamed = bool(self._buffer)
            self._stop_live()
            if event.text and not streamed:
                # Text that only arrived in the final message (e.g. a reply first
                # streamed as reasoning, then reclassified) still gets shown.
                c.print(Markdown(event.text))
        elif isinstance(event, ev.ToolUse):
            self._stop_status()
            c.print(Text("● ", style="cyan") + Text(event.label, style="bold"))
        elif isinstance(event, ev.ToolResult):
            if event.is_error:
                lines = event.content.strip().splitlines()
                preview = "\n    ".join(lines[:4]) + ("\n    …" if len(lines) > 4 else "")
                c.print(Text(f"  └ {preview}", style="red"))
            else:
                c.print(Text(f"  └ {event.summary or 'done'}", style="dim"))
            self._start_status()
        elif isinstance(event, ev.PermissionDenied):
            c.print(Text(f"  └ denied: {event.reason}", style="yellow"))
        elif isinstance(event, ev.UsageUpdate):
            self._last_prompt_tokens = event.prompt_tokens
        elif isinstance(event, ev.Warning):
            self._stop_status()
            c.print(Text(f"⚠ {event.message}", style="yellow"))
        elif isinstance(event, ev.Error):
            self._stop_status()
            self._stop_live()
            c.print(Text(f"✗ {event.message}", style="bold red"))
            if event.hint:
                c.print(Text(f"  {event.hint}", style="red"))
        elif isinstance(event, ev.Result):
            self._stop_status()
            self._stop_live()

    # -- permission prompt ---------------------------------------------------

    def _preview(self, req: PermissionRequest) -> Any:
        inp = req.input
        if req.tool_name == "Bash":
            return Syntax(str(inp.get("command", "")), "bash", word_wrap=True)
        if req.tool_name == "Edit":
            diff = difflib.unified_diff(
                str(inp.get("old_string", "")).splitlines(),
                str(inp.get("new_string", "")).splitlines(),
                lineterm="",
                n=2,
            )
            return Syntax("\n".join(list(diff)[2:]) or "(no change)", "diff")
        if req.tool_name == "Write":
            content = str(inp.get("content", ""))
            lines = content.splitlines()
            shown = "\n".join(lines[:30]) + (
                f"\n… ({len(lines) - 30} more lines)" if len(lines) > 30 else ""
            )
            return Syntax(shown, "text")
        return Text(str(inp))

    async def ask(self, req: PermissionRequest) -> PermissionAnswer:
        self._stop_status()
        self._stop_live()
        c = self.console
        c.print(
            Panel(
                self._preview(req), title=f"Allow {req.label}?", border_style="yellow", expand=False
            )
        )
        if req.reason:
            c.print(Text(f"  ({req.reason})", style="dim"))
        c.print("  [bold]1[/bold] Yes")
        if req.can_remember:
            c.print(
                f"  [bold]2[/bold] Yes, and don't ask again for [cyan]{req.suggested_rule}[/cyan] in this project"
            )
        else:
            c.print("  [dim]2 (not offered: high-risk commands are approved one at a time)[/dim]")
        c.print("  [bold]3[/bold] No, and tell cmcoder what to do differently")
        assert self.session is not None
        while True:
            try:
                choice = (await self.session.prompt_async("  choose [1/2/3]: ")).strip().lower()
            except (KeyboardInterrupt, EOFError):
                self._arm_interrupt()
                return PermissionAnswer(allow=False)
            if choice in ("1", "y", "yes"):
                answer = PermissionAnswer(allow=True)
                break
            if choice in ("2", "a", "always") and req.can_remember:
                answer = PermissionAnswer(allow=True, remember=True)
                break
            if choice in ("3", "n", "no"):
                try:
                    feedback = await self.session.prompt_async(
                        "  what should cmcoder do instead? (Enter to just stop): "
                    )
                except (KeyboardInterrupt, EOFError):
                    feedback = ""
                answer = PermissionAnswer(allow=False, feedback=feedback.strip() or None)
                break
        # prompt_toolkit installs and then removes its own SIGINT handler, which
        # also drops ours; re-arm Ctrl+C for the rest of the turn.
        self._arm_interrupt()
        self._start_status()
        return answer

    # -- slash commands --------------------------------------------------------

    async def _command(self, line: str) -> bool:
        """Handle a slash command. Returns False to exit."""
        assert self.agent is not None
        name, _, arg = line[1:].partition(" ")
        arg = arg.strip()
        c = self.console
        if name in ("exit", "quit"):
            return False
        if name == "help":
            c.print(HELP)
        elif name == "clear":
            self.agent.clear()
            self._last_prompt_tokens = 0
            c.print("[dim]Started a new conversation.[/dim]")
        elif name == "model":
            if not arg:
                c.print(
                    f"Model: [bold]{self.agent.model}[/bold] (provider {self.agent.provider.name}, "
                    f"context {self.agent.profile.context_window} tokens)"
                )
            else:
                provider_name, model = self.settings.resolve_model(arg)
                if provider_name != self.agent.provider.name:
                    c.print(
                        "[yellow]Switching providers mid-session arrives in Phase 1; restart with --model.[/yellow]"
                    )
                else:
                    provider = self.agent.provider
                    assert isinstance(provider, OpenAICompatProvider)
                    self.agent.model = model
                    self.agent.profile = await resolve_model_profile(self.settings, provider, model)
                    c.print(f"Model set to [bold]{model}[/bold]")
        elif name == "mode":
            if arg:
                if arg not in MODES:
                    c.print(f"[red]Unknown mode. Choose one of: {', '.join(MODES)}[/red]")
                else:
                    self.agent.policy.mode = arg
            c.print(f"Permission mode: [bold]{self.agent.policy.mode}[/bold]")
        elif name == "cost":
            u: Usage = self.agent.usage
            est = " (estimated)" if u.estimated else ""
            cost = f", cost {u.cost:.4f}" if u.cost is not None else ""
            c.print(f"Tokens: {u.prompt_tokens} in, {u.completion_tokens} out{est}{cost}")
        else:
            c.print(f"[red]Unknown command /{name}. Type /help.[/red]")
        return True

    # -- main loop -----------------------------------------------------------

    def _toolbar(self) -> Any:
        if not self.agent:
            return ""
        ctx = self._last_prompt_tokens
        pct = f" · context {100 * ctx // max(1, self.agent.profile.context_window)}%" if ctx else ""
        return f" {self.agent.model} · mode: {self.agent.policy.mode} (shift+tab){pct}"

    def _arm_interrupt(self) -> None:
        task = self._turn_task
        if task is not None and not task.done() and self._interrupt is not None:
            self._interrupt.arm(task.cancel)

    async def _run_turn(self, prompt: str) -> None:
        assert self.agent is not None
        agent = self.agent

        async def consume() -> None:
            self._start_status()
            async for event in agent.run(prompt):
                self._render(event)

        task = asyncio.create_task(consume())
        self._turn_task = task
        self._interrupt = InterruptHandler(asyncio.get_running_loop())
        self._arm_interrupt()
        try:
            await task
        except asyncio.CancelledError:
            self._stop_status()
            self._stop_live()
            self.console.print(
                Text("└ Interrupted. What should cmcoder do instead?", style="yellow")
            )
        finally:
            self._turn_task = None
            self._interrupt.disarm()
            self._interrupt = None
            self._stop_status()
            self._stop_live()

    async def main(self, initial_prompt: str | None = None) -> int:
        self.opts.ask = self.ask
        self.agent = await build_agent(self.settings, self.opts)
        agent = self.agent
        cfg = self.settings.providers[agent.provider.name]
        self.console.print(
            Panel.fit(
                f"[bold]cmcoder[/bold] {__version__}\n"
                f"model    {agent.model}  [dim]({agent.provider.name}: {cfg.base_url})[/dim]\n"
                f"cwd      {agent.ctx.cwd}\n"
                f"mode     {agent.policy.mode}\n"
                "[dim]/help for commands · Ctrl+C interrupts · /exit quits[/dim]",
                border_style="cyan",
            )
        )
        bindings = KeyBindings()

        @bindings.add("s-tab")
        def _cycle_mode(event: Any) -> None:
            policy = agent.policy
            policy.mode = MODES[(MODES.index(policy.mode) + 1) % len(MODES)]
            event.app.invalidate()

        @bindings.add("escape", "enter")
        def _newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        hist = config_dir() / "history"
        hist.parent.mkdir(parents=True, exist_ok=True)
        self.session = PromptSession(
            history=FileHistory(str(hist)), key_bindings=bindings, bottom_toolbar=self._toolbar
        )
        pending = initial_prompt
        ctrl_c = False
        try:
            while True:
                if pending is None:
                    try:
                        line = await self.session.prompt_async("> ")
                        ctrl_c = False
                    except KeyboardInterrupt:
                        if ctrl_c:
                            break
                        ctrl_c = True
                        self.console.print("[dim](press Ctrl+C again or type /exit to quit)[/dim]")
                        continue
                    except EOFError:
                        break
                else:
                    line, pending = pending, None
                line = line.strip()
                if not line:
                    continue
                if line.startswith("/"):
                    if not await self._command(line):
                        break
                    continue
                await self._run_turn(line)
        finally:
            with suppress(Exception):
                await agent.close()
        return 0
