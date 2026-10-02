"""Interactive terminal mode (basic TUI: prompt_toolkit input + rich rendering).

A full Textual UI replaces this in Phase 1; the agent and protocol stay the same.
"""

from __future__ import annotations

import asyncio
import difflib
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
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
from ..core.permissions import MODES, ModeNotAllowed
from ..core.sessions import SessionLog, age, list_sessions, load
from ..protocol import events as ev
from ..providers.messages import Usage
from ..providers.openai_compat import OpenAICompatProvider
from .factory import AgentOptions, build_agent, resolve_model_profile

# Lines the permission prompt needs besides the preview: panel border and
# title, reason, three options, the input line and the bottom toolbar.
PROMPT_CHROME_LINES = 12
MAX_PREVIEW_LINES = 30
MAX_PREVIEW_LINE_CHARS = 400


def clip_preview(text: str, max_lines: int) -> tuple[str, int]:
    """Keep the first and last lines of a long preview so the permission
    options always fit on screen. Returns (shown text, hidden line count)."""
    lines = [
        line if len(line) <= MAX_PREVIEW_LINE_CHARS else line[:MAX_PREVIEW_LINE_CHARS] + " …"
        for line in text.splitlines()
    ]
    max_lines = max(3, max_lines)
    if len(lines) <= max_lines:
        return "\n".join(lines), 0
    tail = max(1, max_lines // 3)
    head = max_lines - tail - 1  # one line for the "… N more lines" marker
    hidden = len(lines) - head - tail
    shown = [*lines[:head], f"… {hidden} more lines (v to view all) …", *lines[-tail:]]
    return "\n".join(shown), hidden


def short_rule(rule: str, limit: int = 60) -> str:
    """One-line form of a rule for display: a rule for a multi-line command
    would otherwise print the whole command again."""
    first = rule.splitlines()[0] if rule else rule
    if first != rule or len(first) > limit:
        return first[: limit - 3].rstrip() + " …)"
    return rule


HELP = """\
[bold]Commands[/bold]
  /help              show this help
  /clear             start a new conversation (the old one can be resumed)
  /resume [n|id]     list saved conversations in this project, or resume one
  /rewind            go back to an earlier message: undo file changes, the conversation, or both
  /compact [focus]   summarise the conversation so far to free context
                     (e.g. /compact keep the failing test names)
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
        self._next_input = ""  # pre-filled prompt text (after /rewind)

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
        elif isinstance(event, ev.Compacted):
            self._stop_status()
            how = "Compacted" if event.trigger == "manual" else "Context nearly full: compacted"
            c.print(
                Text(
                    f"✻ {how} the conversation: summarised {event.summarized_messages} earlier "
                    f"messages with {event.model} (≈{event.tokens_before:,} → "
                    f"{event.tokens_after:,} tokens).",
                    style="cyan",
                )
            )
            self._last_prompt_tokens = 0
        elif isinstance(event, ev.Result):
            self._stop_status()
            self._stop_live()

    # -- permission prompt ---------------------------------------------------

    @staticmethod
    def _preview_source(req: PermissionRequest) -> tuple[str, str]:
        """(text, syntax lexer) shown in the permission panel."""
        inp = req.input
        if req.tool_name == "Bash":
            return str(inp.get("command", "")), "bash"
        if req.tool_name == "Edit":
            diff = difflib.unified_diff(
                str(inp.get("old_string", "")).splitlines(),
                str(inp.get("new_string", "")).splitlines(),
                lineterm="",
                n=2,
            )
            return "\n".join(list(diff)[2:]) or "(no change)", "diff"
        if req.tool_name == "Write":
            return str(inp.get("content", "")), "text"
        return str(inp), "text"

    def _preview_lines(self) -> int:
        """How many preview lines fit so the options stay on screen."""
        return min(MAX_PREVIEW_LINES, self.console.size.height - PROMPT_CHROME_LINES)

    def _print_options(self, req: PermissionRequest) -> None:
        c = self.console
        if req.reason:
            c.print(Text(f"  ({req.reason})", style="dim"))
        c.print("  [bold]1[/bold] Yes")
        if req.can_remember:
            c.print(
                Text.assemble(
                    ("  2", "bold"),
                    " Yes, and don't ask again for ",
                    (short_rule(req.suggested_rule), "cyan"),
                    " in this project",
                )
            )
        else:
            c.print("  [dim]2 (not offered: high-risk commands are approved one at a time)[/dim]")
        c.print("  [bold]3[/bold] No, and tell cmcoder what to do differently")

    async def ask(self, req: PermissionRequest) -> PermissionAnswer:
        self._stop_status()
        self._stop_live()
        c = self.console
        source, lexer = self._preview_source(req)
        shown, hidden = clip_preview(source, self._preview_lines())
        c.print(
            Panel(
                Syntax(shown, lexer, word_wrap=True),
                title=f"Allow {req.label}?",
                border_style="yellow",
                expand=False,
            )
        )
        # The options always come after the preview, and the input line
        # repeats them, so they can't scroll out of view.
        self._print_options(req)
        keys = "1 yes · 2 always · 3 no" if req.can_remember else "1 yes · 3 no"
        if hidden:
            keys += " · v view all"
        assert self.session is not None
        while True:
            try:
                choice = (await self.session.prompt_async(f"  {keys}: ")).strip().lower()
            except (KeyboardInterrupt, EOFError):
                self._arm_interrupt()
                return PermissionAnswer(allow=False)
            if choice in ("1", "y", "yes"):
                answer = PermissionAnswer(allow=True)
                break
            if choice in ("2", "a", "always") and req.can_remember:
                answer = PermissionAnswer(allow=True, remember=True)
                break
            if choice in ("v", "view") and hidden:
                c.print(Syntax(source, lexer, word_wrap=True, line_numbers=True))
                self._print_options(req)
                continue
            if choice not in ("3", "n", "no"):
                c.print(Text(f"  Please answer {keys}.", style="yellow"))
                self._print_options(req)
                continue
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
        elif name == "compact":
            await self._run_stream(
                self.agent.compact(arg or None),
                status="Compacting…",
                interrupted="└ Compaction cancelled; the conversation is unchanged.",
            )
        elif name == "clear":
            self.agent.clear()
            self._last_prompt_tokens = 0
            c.print("[dim]Started a new conversation.[/dim]")
        elif name == "resume":
            await self._resume(arg)
        elif name == "rewind":
            await self._rewind()
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
                    modes = ", ".join(self.agent.policy.available_modes())
                    c.print(f"[red]Unknown mode. Choose one of: {modes}[/red]")
                else:
                    try:
                        self.agent.policy.mode = arg
                    except ModeNotAllowed as e:
                        c.print(Text(str(e), style="red"))
            c.print(f"Permission mode: [bold]{self.agent.policy.mode}[/bold]")
        elif name == "cost":
            u: Usage = self.agent.usage
            est = " (estimated)" if u.estimated else ""
            cost = f", cost {u.cost:.4f}" if u.cost is not None else ""
            c.print(f"Tokens: {u.prompt_tokens} in, {u.completion_tokens} out{est}{cost}")
        else:
            c.print(f"[red]Unknown command /{name}. Type /help.[/red]")
        return True

    async def _resume(self, arg: str) -> None:
        """/resume: list this project's sessions, or load one by number or id."""
        assert self.agent is not None
        c = self.console
        root = self.agent.ctx.project_root
        sessions = [s for s in list_sessions(root) if s.session_id != self.agent.session_id][:15]
        if not sessions:
            c.print("[dim]No other saved conversations in this project.[/dim]")
            return
        choice = arg
        if not choice:
            for i, s in enumerate(sessions, 1):
                c.print(
                    Text.assemble(
                        (f"  {i:>2} ", "bold"),
                        (f"{age(s.updated):>9}  ", "dim"),
                        s.title,
                        (f"  ({s.messages} messages, {s.session_id[:8]})", "dim"),
                    )
                )
            assert self.session is not None
            try:
                choice = (
                    await self.session.prompt_async("  resume which? (Enter to cancel): ")
                ).strip()
            except (KeyboardInterrupt, EOFError):
                return
            if not choice:
                return
        if choice.isdigit() and 1 <= int(choice) <= len(sessions):
            info = sessions[int(choice) - 1]
        else:
            matches = [s for s in sessions if s.session_id.startswith(choice)]
            if len(matches) != 1:
                c.print(Text(f"No single conversation matches {choice!r}.", style="red"))
                return
            info = matches[0]
        messages, _meta = load(info.path)
        self.agent.resume(messages, SessionLog(root, info.session_id))
        self._last_prompt_tokens = 0
        self._show_resumed()

    async def _ask_line(self, prompt: str) -> str:
        assert self.session is not None
        try:
            return (await self.session.prompt_async(prompt)).strip().lower()
        except (KeyboardInterrupt, EOFError):
            return ""

    async def _rewind(self) -> None:
        """/rewind: pick an earlier message, then what to undo."""
        assert self.agent is not None
        c = self.console
        points = self.agent.rewind_points()[-10:]
        if not points:
            c.print("[dim]Nothing to rewind to yet.[/dim]")
            return
        for i, (_turn, text, changed) in enumerate(points, 1):
            files = f"  ({changed} file(s) changed since)" if changed else ""
            c.print(
                Text.assemble(
                    (f"  {i:>2} ", "bold"),
                    clip_preview(" ".join(text.split()), 1)[0][:90],
                    (files, "dim"),
                )
            )
        pick = await self._ask_line("  rewind to before which message? (Enter to cancel): ")
        if not pick.isdigit() or not 1 <= int(pick) <= len(points):
            return
        turn = points[int(pick) - 1][0]
        what = await self._ask_line(
            "  1 code and conversation · 2 conversation only · 3 code only: "
        )
        if what not in ("1", "2", "3"):
            return
        code, conversation = what in ("1", "3"), what in ("1", "2")
        outside = False
        if code:
            root = self.agent.ctx.project_root.resolve()
            others = [
                e.path
                for e in self.agent.checkpoints.changes_since(turn)
                if not Path(e.path).is_relative_to(root)
            ]
            if others:
                c.print(
                    Text(
                        f"  {len(others)} changed file(s) are outside the project:", style="yellow"
                    )
                )
                for p in others[:10]:
                    c.print(Text(f"    {p}", style="yellow"))
                outside = await self._ask_line("  restore those too? [y/N]: ") in ("y", "yes")
        actions, prompt = self.agent.rewind(
            turn, code=code, conversation=conversation, outside=outside
        )
        for a in actions:
            c.print(Text(f"  {a.action}: {a.path}", style="dim"))
        if code:
            c.print(
                Text(
                    f"  Files: {sum(a.action == 'restored' for a in actions)} restored, "
                    f"{sum(a.action.startswith('deleted') for a in actions)} deleted. "
                    "Changes made by Bash commands are not undone.",
                    style="cyan",
                )
            )
        if prompt is not None:
            self._last_prompt_tokens = 0
            self._next_input = prompt
            c.print(
                Text(
                    "  Conversation rewound; your message is back in the input line.", style="cyan"
                )
            )

    def _show_resumed(self) -> None:
        """After resuming: which conversation, and where it left off."""
        assert self.agent is not None
        c = self.console
        msgs = self.agent.messages[1:]
        c.print(
            Text(
                f"Resumed conversation {self.agent.session_id[:8]} ({len(msgs)} messages).",
                style="cyan",
            )
        )
        last_user = next((m for m in reversed(msgs) if m.role == "user"), None)
        last_reply = next((m for m in reversed(msgs) if m.role == "assistant" and m.content), None)
        if last_user:
            c.print(Text("> " + clip_preview(last_user.content, 3)[0], style="dim"))
        if last_reply:
            c.print(Markdown(clip_preview(last_reply.content, 8)[0]))

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
        await self._run_stream(self.agent.run(prompt))

    async def _run_stream(
        self,
        events: AsyncIterator[ev.Event],
        status: str = "Waiting for model…",
        interrupted: str = "└ Interrupted. What should cmcoder do instead?",
    ) -> None:
        """Render an agent event stream, with Ctrl+C cancelling it."""

        async def consume() -> None:
            self._start_status(status)
            async for event in events:
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
            self.console.print(Text(interrupted, style="yellow"))
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
                + ("policy   managed settings in effect\n" if self.settings.managed_path else "")
                + "[dim]/help for commands · Ctrl+C interrupts · /exit quits[/dim]",
                border_style="cyan",
            )
        )
        if len(agent.messages) > 1:
            self._show_resumed()
        bindings = KeyBindings()

        @bindings.add("s-tab")
        def _cycle_mode(event: Any) -> None:
            policy = agent.policy
            modes = policy.available_modes()  # skips bypassPermissions when disabled
            policy.mode = modes[(modes.index(policy.mode) + 1) % len(modes)]
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
                        default, self._next_input = self._next_input, ""
                        line = await self.session.prompt_async("> ", default=default)
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
