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
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text

from .. import __version__, brand
from ..compat import InterruptHandler
from ..config.settings import Settings, config_dir, ignored_settings_message
from ..core.agent import Agent, PermissionAnswer, PermissionRequest
from ..core.commands import BUILT_IN, help_lines
from ..core.critic import critic_command, critique_note
from ..core.permissions import MODES, ModeNotAllowed
from ..core.sessions import SessionLog, age, list_sessions, load
from ..core.subagents import agents_command
from ..images import Image, ImageError
from ..mcp_client import status_lines
from ..protocol import events as ev
from ..providers.messages import Usage
from ..providers.openai_compat import OpenAICompatProvider
from ..rag.index import Progress
from .agent_map import AgentMap, ParallelTasks, review_lines
from .factory import AgentOptions, build_agent, index_command, resolve_model_profile
from .pasted_images import PendingImages, clipboard_image, image_path
from .symbols import sym as S

# Lines the permission prompt needs besides the preview: panel border and
# title, reason, three options, the input line and the bottom toolbar.
PROMPT_CHROME_LINES = 12
MAX_PREVIEW_LINES = 30
MAX_PREVIEW_LINE_CHARS = 400


def output_preview(event: ev.ToolResult, keep: int) -> str | None:
    """The start of a successful command's output, indented under its summary.

    Like Claude Code, so what the model saw is visible (e.g. a pip error that a
    pipe hid behind exit 0)."""
    lines = event.content.strip().splitlines()
    if event.name != "Bash" or not lines or lines == ["(no output)"]:
        return None
    more = f"\n    … {len(lines) - keep} more lines" if len(lines) > keep else ""
    return "    " + "\n    ".join(lines[:keep]) + more


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


def subagent_line(event: ev.Event, tag: str = "") -> tuple[str, str] | None:
    """A subagent's tool call or problem as one indented line: (text, style)."""
    if getattr(event, "parent_tool_use_id", None) is None:
        return None
    if isinstance(event, ev.ToolUse):
        return f"  {S().pipe} {tag}{S().tool} {event.label}", "dim"
    if isinstance(event, ev.ToolResult) and event.is_error:
        first = (event.content.strip().splitlines() or [""])[0]
        return f"  {S().pipe} {tag}  {S().end} {first[:200]}", "red"
    if isinstance(event, ev.PermissionDenied):
        return f"  {S().pipe} {tag}  {S().end} denied: {event.reason}", "yellow"
    return "", ""


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
  /todos             show the current todo list
  /compact [focus]   summarise the conversation so far to free context
                     (e.g. /compact keep the failing test names)
  /model [name]      show or switch the model (e.g. /model qwen3-27b)
  /mode [mode]       show or set the permission mode: default, acceptEdits, plan, bypassPermissions
  /cost              token usage for this session
  /mcp               MCP servers: status and tools
  /index [status]    build or update the code index (code search), or show it
  /agents [n|stop n] agent types and this session's subagents: status, steps, reports
  /agents map        the last turn's agents as a mind map
  /critic [on|off]   a critic agent reviews each answer before you see it
  /exit              quit

[bold]Keys[/bold]
  Enter              send            Alt+Enter / Esc Enter   new line
  Shift+Tab          cycle permission mode
  Ctrl+C             interrupt the current turn (twice at the prompt to quit);
                     with subagents running: stop one of them, or everything
"""


class SlashCompleter(Completer):
    """Completes `/name` at the start of the input: built-in and your commands."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent

    def get_completions(self, document: Any, complete_event: Any) -> Any:
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text or "\n" in text:
            return
        typed = text[1:]
        custom = self.agent.command_list()
        for name in sorted({*BUILT_IN, *custom}):
            if name.startswith(typed):
                c = custom.get(name)
                meta = c.description if c else "built-in"
                yield Completion("/" + name, start_position=-len(text), display_meta=meta)


class StatusView:
    """The spinner line, with the agent map under it while subagents run."""

    def __init__(self, agent_map: AgentMap, text: str) -> None:
        self.map = agent_map
        self.spinner = Spinner(S().spinner, text=text)

    def __rich_console__(self, console: Console, options: Any) -> Any:
        yield self.spinner
        if not self.map.busy:  # e.g. /compact: the last turn's map isn't live
            return
        for line in self.map.mind_map(max(40, options.max_width - 2)):
            yield Text("  ") + line
        if self.map.active:
            yield Text("  Ctrl+C: stop one subagent, or everything", style="dim")


class Repl:
    def __init__(self, settings: Settings, opts: AgentOptions, verbose: bool = False) -> None:
        self.settings = settings
        self.opts = opts
        self.verbose = verbose
        self.console = Console(highlight=False)
        self.tasks = ParallelTasks()
        self.map = AgentMap()  # this turn's subagents, under the spinner
        self.agent: Agent | None = None
        self.session: PromptSession[str] | None = None
        self._status: Live | None = None
        self._status_view: StatusView | None = None
        # One prompt at a time (permission questions, "stop which subagent?");
        # while one is open, events are held and shown after it.
        self._prompt_lock = asyncio.Lock()
        self._prompting = False
        self._held: list[ev.Event] = []
        # Images attached to the message being typed (Ctrl+V / Alt+V, a pasted path).
        self.pending_images = PendingImages()
        self._image_note = ""  # shown in the toolbar until the next message
        self._chooser: asyncio.Task[None] | None = None
        self._live: Live | None = None
        self._buffer = ""
        self._last_prompt_tokens = 0
        self._turn_task: asyncio.Task[None] | None = None
        self._interrupt: InterruptHandler | None = None
        self._next_input = ""  # pre-filled prompt text (after /rewind)

    # -- rendering helpers ------------------------------------------------

    def _start_status(self, text: str = "Waiting for model…") -> None:
        if self._prompting:
            return
        if self._status is None or self._status_view is None:
            self._status_view = StatusView(self.map, text)
            self._status = Live(
                self._status_view, console=self.console, refresh_per_second=8, transient=True
            )
            self._status.start()
        else:
            self._status_view.spinner.update(text=text)

    def _stop_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
            self._status_view = None

    def _hold(self) -> None:
        """A prompt opens: keep events until it closes."""
        self._stop_status()
        self._stop_live()
        self._prompting = True

    def _release(self) -> None:
        self._prompting = False
        held, self._held = self._held, []
        for event in held:
            self._draw(event)
        if self._turn_task is not None and not self._turn_task.done():
            self._start_status()

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.update(Markdown(self._buffer))
            self._live.stop()
            self._live = None
        self._buffer = ""

    def _render(self, event: ev.Event) -> None:
        if self._prompting:
            self._held.append(event)
        else:
            self._draw(event)

    def _draw(self, event: ev.Event) -> None:
        c = self.console
        tag = self.tasks.tag(event)
        self.map.observe(event)
        if isinstance(event, ev.SubagentStatus):
            self._start_status("Subagents working…")
        elif (line := subagent_line(event, tag)) is not None:  # a step inside a Task call
            # The agent map shows what each subagent is doing; every step only with -v.
            if line[0] and self.verbose:
                self._stop_status()
                c.print(Text(line[0], style=line[1]))
                self._start_status("Subagents working…")
        elif isinstance(event, ev.ReasoningDelta):
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
            if event.name == "TodoWrite" and isinstance(event.input.get("todos"), list):
                self._print_todos(event.input["todos"])
                return
            c.print(Text(f"{S().tool} ", style="cyan") + Text(event.label, style="bold"))
            if event.input.get("subagent_type") == "critic":
                self._start_status("Reviewing the answer…")
        elif isinstance(event, ev.CritiqueChanged):
            self._stop_status()
            c.print(Text(critique_note(event), style="dim"))
            self._start_status()
        elif isinstance(event, ev.ReviewResult):
            self._stop_status()
            for line, style in review_lines(event):
                c.print(Text(line, style=style))
            if not event.final:
                self._start_status("Fixing what the reviewer found…")
        elif isinstance(event, ev.ToolResult) and (final := self.map.final_line(event.id)):
            state = self.map.runs[event.id].state
            style = {"done": "green", "limit": "green", "failed": "red"}.get(state, "yellow")
            self._stop_status()
            c.print(Text(f"  └ {tag}{final}", style=style))
            self._start_status()
        elif isinstance(event, ev.ToolResult):
            if event.is_error:
                lines = event.content.strip().splitlines()
                preview = "\n    ".join(lines[:4]) + ("\n    …" if len(lines) > 4 else "")
                c.print(Text(f"  └ {tag}{preview}", style="red"))
            else:
                c.print(Text(f"  └ {tag}{event.summary or 'done'}", style="dim"))
                if preview := output_preview(event, 20 if self.verbose else 3):
                    c.print(Text(preview, style="dim"))
            self._start_status()
        elif isinstance(event, ev.PermissionDenied):
            c.print(Text(f"  └ denied: {event.reason}", style="yellow"))
        elif isinstance(event, ev.UsageUpdate):
            self._last_prompt_tokens = event.prompt_tokens
        elif isinstance(event, ev.Warning):
            self._stop_status()
            c.print(Text(f"{S().warn} {event.message}", style="yellow"))
        elif isinstance(event, ev.Error):
            self._stop_status()
            self._stop_live()
            c.print(Text(f"{S().error} {event.message}", style="bold red"))
            if event.hint:
                c.print(Text(f"  {event.hint}", style="red"))
        elif isinstance(event, ev.CodeContext | ev.ImagesDescribed):
            c.print(Text(f"{S().note} {event.summary()}", style="dim"))
        elif isinstance(event, ev.Compacted):
            self._stop_status()
            how = "Compacted" if event.trigger == "manual" else "Context nearly full: compacted"
            c.print(
                Text(
                    f"{S().compacted} {how} the conversation: summarised {event.summarized_messages} earlier "
                    f"messages with {event.model} (≈{event.tokens_before:,} → "
                    f"{event.tokens_after:,} tokens).",
                    style="cyan",
                )
            )
            self._last_prompt_tokens = 0
        elif isinstance(event, ev.Result):
            self._stop_status()
            self._stop_live()

    def _print_agent_map(self) -> None:
        """/agents map: the last turn's agents as a mind map."""
        lines = self.map.mind_map(self.console.width)
        if not lines:
            self.console.print(
                Text("No subagents in the last turn. /agents lists this session's.", style="dim")
            )
            return
        prompt = self.map.prompt if len(self.map.prompt) <= 80 else self.map.prompt[:77] + "..."
        self.console.print(Text(f"Agent map · last turn: “{prompt}”", style="bold"))
        for line in lines:
            self.console.print(line)
        self.console.print(Text("/agents <n> shows a subagent's steps and report.", style="dim"))

    def _print_todos(self, todos: list[Any]) -> None:
        styles = {"completed": "dim strike", "in_progress": "bold cyan", "pending": ""}
        self.console.print(Text(f"{S().tool} Todo list", style="cyan"))
        for t in todos:
            if isinstance(t, dict):
                status = str(t.get("status", "pending"))
                mark = S().todo.get(status, S().todo["pending"])
                self.console.print(
                    Text(f"  {mark} {t.get('content', '')}", style=styles.get(status, ""))
                )

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
        c.print(
            "  [bold]3[/bold] No, and tell "
            + escape(brand.load().product_name)
            + " what to do differently"
        )

    async def ask(self, req: PermissionRequest) -> PermissionAnswer:
        async with self._prompt_lock:
            self._hold()
            try:
                return await self._ask(req)
            finally:
                self._release()

    async def _ask(self, req: PermissionRequest) -> PermissionAnswer:
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
            if custom := help_lines(self.agent.command_list()):
                c.print("[bold]Your commands[/bold]")
                c.print(Text("\n".join(custom)))
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
        elif name == "todos":
            if self.agent.ctx.todos:
                self._print_todos(self.agent.ctx.todos)
            else:
                c.print("[dim]No todo list in this conversation.[/dim]")
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
        elif name == "mcp":
            for line in status_lines(self.agent.mcp):
                c.print(Text(line))
        elif name == "critic":
            for line in critic_command(self.agent, arg):
                c.print(Text(line))
        elif name == "agents" and arg == "map":
            self._print_agent_map()
        elif name == "agents":
            for line in agents_command(self.agent, arg, S().states):
                c.print(Text(line))
        elif name == "index":
            with c.status("Code index…", spinner=S().spinner) as spinner:

                def show(p: Progress) -> None:
                    spinner.update(f"Indexing: {p.done}/{p.total} files, {p.chunks} pieces…")

                lines = await index_command(self.agent, self.settings, arg, show)
            for out in lines:
                c.print(Text(out))
        elif name == "cost":
            u: Usage = self.agent.usage
            est = " (estimated)" if u.estimated else ""
            cost = f", cost {u.cost:.4f}" if u.cost is not None else ""
            c.print(f"Tokens: {u.prompt_tokens} in, {u.completion_tokens} out{est}{cost}")
        else:
            try:
                expansion, warnings = await self.agent.expand_command(line)
            except ValueError as e:
                c.print(Text(str(e), style="red"))
                return True
            for w in warnings:
                c.print(Text(f"{S().warn} {w}", style="yellow"))
            if expansion is None:
                c.print(f"[red]Unknown command /{name}. Type /help.[/red]")
            else:
                await self._run_turn(expansion.prompt, expansion.allowed_tools)
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
        n = len(self.pending_images.images)
        images = f" · {n} image{'s' if n > 1 else ''} attached" if n else ""
        note = f" · {self._image_note}" if self._image_note else ""
        mode = self.agent.policy.mode
        return f" {self.agent.model} · mode: {mode} (shift+tab){pct}{images}{note}"

    def _arm_interrupt(self) -> None:
        task = self._turn_task
        if task is not None and not task.done() and self._interrupt is not None:
            self._interrupt.arm(self._on_interrupt)

    def _on_interrupt(self) -> None:
        """Ctrl+C: interrupt the turn; with subagents running, first ask
        whether to stop just one of them."""
        task = self._turn_task
        if task is None or task.done():
            return
        if self.map.busy and self.map.active and self._chooser is None and self.agent is not None:
            self._chooser = asyncio.get_running_loop().create_task(self._choose_stop())
        else:
            task.cancel()

    async def _choose_stop(self) -> None:
        assert self.agent is not None and self.session is not None
        c = self.console
        try:
            async with self._prompt_lock:
                active = self.map.active
                task = self._turn_task
                if not active or task is None or task.done():
                    return
                self._hold()
                c.print(Text("Stop a subagent? The others and the turn go on.", style="yellow"))
                for st in active:
                    c.print(Text(f"  {st.number}. {st.description} · {self.map.detail(st)}"))
                try:
                    choice = await self.session.prompt_async(
                        "  number · a = interrupt everything · Enter = keep going: "
                    )
                except (KeyboardInterrupt, EOFError):
                    choice = "a"
                choice = choice.strip().lower()
                if choice in ("a", "all"):
                    task.cancel()
                elif choice:
                    for line in agents_command(self.agent, f"stop {choice}", S().states):
                        c.print(Text(f"  {line}", style="yellow"))
        finally:
            self._chooser = None
            self._arm_interrupt()  # prompt_toolkit dropped our Ctrl+C handler
            if self._prompting:
                self._release()

    async def _run_turn(
        self, prompt: str, allow: list[str] | None = None, images: list[Image] | None = None
    ) -> None:
        assert self.agent is not None
        self.map.start_turn(prompt, self.agent.model)
        await self._run_stream(self.agent.run(prompt, allow=allow, images=images))

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
            if self._chooser is not None:  # the turn ended while choosing
                self._chooser.cancel()
                await asyncio.gather(self._chooser, return_exceptions=True)
            self._turn_task = None
            self.map.busy = False
            self._interrupt.disarm()
            self._interrupt = None
            if self._prompting:
                self._release()
            self._stop_status()
            self._stop_live()

    async def main(self, initial_prompt: str | None = None) -> int:
        self.opts.ask = self.ask
        self.agent = await build_agent(self.settings, self.opts)
        agent = self.agent
        cfg = self.settings.providers[agent.provider.name]
        b = brand.load()
        self.console.set_window_title(f"{b.product_name} · {agent.ctx.project_root.name}")
        if b.logo:
            self.console.print(Text(b.logo, style=b.accent_color))
        self.console.print(
            Panel.fit(
                f"[bold]{escape(b.product_name)}[/bold] {__version__}\n"
                f"model    {agent.model}  [dim]({agent.provider.name}: {cfg.base_url})[/dim]\n"
                f"cwd      {agent.ctx.cwd}\n"
                + (
                    f"project  {agent.ctx.project_root}\n"
                    if agent.ctx.project_root != agent.ctx.cwd
                    else ""
                )
                + f"mode     {agent.policy.mode}\n"
                + ("policy   managed settings in effect\n" if self.settings.managed_path else "")
                + "[dim]/help for commands · Ctrl+C interrupts · /exit quits[/dim]",
                border_style=b.accent_color,
            )
        )
        if warning := ignored_settings_message(self.settings):
            self.console.print(Text(f"{S().warn} {warning}", style="yellow"))
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

        def attach(event: Any, raw: bytes, name: str) -> None:
            try:
                placeholder = self.pending_images.add(raw, name)
            except ImageError as e:
                self._image_note = str(e)
            else:
                self._image_note = ""
                buffer = event.current_buffer
                space = "" if not buffer.text or buffer.text[-1:].isspace() else " "
                buffer.insert_text(f"{space}{placeholder} ")
            event.app.invalidate()

        # Windows Terminal keeps Ctrl+V for pasting text: Alt+V works everywhere.
        @bindings.add("c-v")
        @bindings.add("escape", "v")
        def _paste_image(event: Any) -> None:
            raw = clipboard_image()
            if raw is None:
                self._image_note = "no image in the clipboard"
                event.app.invalidate()
            else:
                attach(event, raw, "")

        @bindings.add(Keys.BracketedPaste)
        def _paste(event: Any) -> None:
            path = image_path(event.data)  # a dropped image file arrives as its path
            if path is not None:
                attach(event, path.read_bytes(), path.name)
                return
            event.current_buffer.insert_text(event.data.replace("\r\n", "\n").replace("\r", "\n"))

        hist = config_dir() / "history"
        hist.parent.mkdir(parents=True, exist_ok=True)
        self.session = PromptSession(
            history=FileHistory(str(hist)),
            key_bindings=bindings,
            bottom_toolbar=self._toolbar,
            completer=SlashCompleter(agent),
            complete_while_typing=True,
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
                images = self.pending_images.take(line)
                self._image_note = ""
                if not line:
                    continue
                if line.startswith("/"):
                    if not await self._command(line):
                        break
                    continue
                await self._run_turn(line, images=images)
        finally:
            with suppress(Exception):
                await agent.close()
        return 0
