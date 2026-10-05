"""Full-screen terminal UI built with Textual: `cmcoder --tui` (or "ui": "textual").

Layout: a scrollable conversation, an input line, and a status bar. Permission
requests open a fixed-size dialog whose preview scrolls on its own, so the
options can never be pushed off screen (Phase 1 item 2, completed).

The classic prompt_toolkit REPL (cli/repl.py) stays the default until this UI
has been proven on Windows terminals. Both drive the same Agent and events.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from rich.markdown import Markdown as RichMarkdown
from rich.syntax import Syntax
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.suggester import Suggester
from textual.widgets import Button, Footer, Input, Label, Markdown, Static, Tree
from textual.widgets.tree import TreeNode
from textual.worker import Worker

from .. import __version__, brand
from ..config.settings import Settings, ignored_settings_message
from ..core.agent import Agent, PermissionAnswer, PermissionRequest
from ..core.commands import BUILT_IN, help_lines
from ..core.permissions import MODES, ModeNotAllowed
from ..core.subagents import agent_run_details, agents_command
from ..mcp_client import status_lines
from ..protocol import events as ev
from ..rag.index import Progress
from .agent_map import STATE_STYLES, AgentMap, ParallelTasks
from .factory import AgentOptions, build_agent, index_command
from .repl import Repl, output_preview, short_rule, subagent_line
from .symbols import sym as S


class SlashSuggester(Suggester):
    """Suggests the rest of a `/name` (built-in or yours); Right arrow accepts."""

    def __init__(self, app: Any) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self.app = app

    async def get_suggestion(self, value: str) -> str | None:
        agent = self.app.agent
        if agent is None or not value.startswith("/") or " " in value:
            return None
        names = sorted({*BUILT_IN, *agent.command_list()})
        return next(("/" + n for n in names if n.startswith(value[1:]) and n != value[1:]), None)


HELP = """\
/help              show this help
/clear             start a new conversation
/compact [focus]   summarise the conversation so far to free context
/mode [mode]       show or set the permission mode
/model             show the model
/cost              token usage for this session
/mcp               MCP servers: status and tools
/index [status]    build or update the code index (code search), or show it
/agents [n|stop n] agent types and this session's subagents (works while cmcoder works)
/agents map        the agent navigator (also Ctrl+G): the turn's agents as a tree
/todos             show the todo list
/exit              quit
Keys: Enter send · Ctrl+C interrupt (twice when idle: quit) · Shift+Tab cycle mode · Ctrl+G agent navigator
/resume and /rewind: use the classic UI (cmcoder without --tui) for now."""

UPDATE_EVERY = 0.08  # seconds between Markdown re-renders while streaming


class PermissionScreen(ModalScreen[PermissionAnswer]):
    """The permission dialog: fixed size, scrollable preview, options always visible."""

    DEFAULT_CSS = """
    PermissionScreen { align: center middle; }
    #dialog { width: 92%; height: 85%; border: thick $warning; background: $surface; padding: 0 1; }
    #title { text-style: bold; color: $warning; margin-bottom: 1; }
    #preview { height: 1fr; border: round $panel; }
    #reason { color: $text-muted; }
    #buttons { height: auto; margin-top: 1; }
    #buttons Button { margin-right: 1; }
    #feedback { display: none; margin-top: 1; }
    """
    BINDINGS = [
        Binding("1", "allow", "Yes"),
        Binding("2", "always", "Always"),
        Binding("3", "deny", "No"),
        Binding("escape", "deny", "No"),
    ]

    def __init__(self, req: PermissionRequest, source: str, lexer: str) -> None:
        super().__init__()
        self.req, self.source, self.lexer = req, source, lexer

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Allow {self.req.label}?", id="title")
            with VerticalScroll(id="preview"):
                yield Static(Syntax(self.source, self.lexer, word_wrap=True, line_numbers=True))
            if self.req.reason:
                yield Label(f"({self.req.reason})", id="reason")
            with Horizontal(id="buttons"):
                yield Button("1 Yes", id="allow", variant="success")
                if self.req.can_remember:
                    yield Button(f"2 Always: {short_rule(self.req.suggested_rule)}", id="always")
                yield Button("3 No", id="deny", variant="error")
            yield Input(
                placeholder="What should cmcoder do instead? (Enter to just stop)", id="feedback"
            )

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        {"allow": self.action_allow, "always": self.action_always, "deny": self.action_deny}[
            str(event.button.id)
        ]()

    def action_allow(self) -> None:
        self.dismiss(PermissionAnswer(allow=True))

    def action_always(self) -> None:
        if self.req.can_remember:
            self.dismiss(PermissionAnswer(allow=True, remember=True))

    def action_deny(self) -> None:
        feedback = self.query_one("#feedback", Input)
        if feedback.display:  # second Escape/3: stop without feedback
            self.dismiss(PermissionAnswer(allow=False))
            return
        feedback.display = True
        feedback.focus()

    @on(Input.Submitted, "#feedback")
    def _feedback(self, event: Input.Submitted) -> None:
        self.dismiss(PermissionAnswer(allow=False, feedback=event.value.strip() or None))


class AgentNavigatorScreen(Screen[None]):
    """The turn's agents as a tree you can move through: the main agent,
    its subagents, every tool call of each. The selected subagent's steps and
    report on the right; S stops it."""

    DEFAULT_CSS = """
    AgentNavigatorScreen #body { height: 1fr; }
    AgentNavigatorScreen Tree { width: 3fr; border-right: solid $panel; }
    AgentNavigatorScreen #side { width: 2fr; }
    AgentNavigatorScreen #details { width: 100%; padding: 0 1; }
    AgentNavigatorScreen #title { height: 1; padding: 0 1; background: $panel; }
    """
    BINDINGS = [
        Binding("escape", "close", "Back to the chat"),
        Binding("ctrl+g", "close", "Back", show=False),
        Binding("s", "stop", "Stop subagent"),
    ]

    def __init__(self, chat: CmcoderApp) -> None:
        super().__init__()
        self.chat = chat
        self.nodes: dict[str, TreeNode[str]] = {}
        self.leaves: dict[str, list[TreeNode[str]]] = {}

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal(id="body"):
            yield Tree(Text(f"{S().tool} main agent"), data="", id="tree")
            with VerticalScroll(id="side"):
                yield Static(id="details")
        yield Footer()

    def on_mount(self) -> None:
        tree = self.query_one(Tree)
        tree.root.expand()
        self.sync()
        tree.focus()

    def reset(self) -> None:
        """A new turn: start the tree again."""
        self.query_one(Tree).root.remove_children()
        self.nodes.clear()
        self.leaves.clear()
        self.sync()

    def sync(self) -> None:
        m = self.chat.map
        prompt = m.prompt if len(m.prompt) <= 90 else m.prompt[:87] + "..."
        self.query_one("#title", Static).update(
            Text(f"Agent navigator · {'this turn' if m.busy else 'last turn'}: “{prompt}”", "bold")
        )
        tree = self.query_one(Tree)
        tree.root.set_label(
            Text.assemble(
                (f"{S().tool} main agent", "bold"), ("  " + "  ".join(m.root_lines()), "dim")
            )
        )
        for s in m.runs.values():
            label = Text.assemble(
                (
                    f"{S().states[s.state]} {s.number}. {s.description}",
                    f"bold {STATE_STYLES[s.state]}",
                ),
                ("  " + m.stats(s), "dim"),
            )
            node = self.nodes.get(s.id)
            if node is None:
                node = self.nodes[s.id] = tree.root.add(label, data=s.id, expand=True)
                self.leaves[s.id] = []
            else:
                node.set_label(label)
            steps = m.steps.get(s.id, [])
            shown = self.leaves[s.id]
            for step in steps[len(shown) :]:
                shown.append(node.add_leaf(Text(step.label, "dim"), data=s.id))
            for leaf, step in zip(shown, steps, strict=False):
                if step.problem:
                    leaf.set_label(Text(f"{step.label} - {step.problem}", "red"))
        if not m.runs and not tree.root.children:
            tree.root.add_leaf(Text("No subagents in this turn yet.", "dim"), data="")
        elif m.runs:
            for child in list(tree.root.children):
                if not child.data:
                    child.remove()
        self.show_details()

    def selected(self) -> str:
        node = self.query_one(Tree).cursor_node
        return str(node.data) if node is not None and node.data else ""

    def show_details(self) -> None:
        details = self.query_one("#details", Static)
        agent = self.chat.agent
        run = (
            agent.find_subagent_run(self.selected())
            if agent is not None and self.selected()
            else None
        )
        if run is None:
            details.update(
                Text(
                    "Select a subagent (arrow keys) to see its steps and report; S stops it.", "dim"
                )
            )
            return
        details.update(Text("\n".join(agent_run_details(run, S().states))))

    @on(Tree.NodeHighlighted)
    def _highlighted(self) -> None:
        self.show_details()

    def action_stop(self) -> None:
        agent = self.chat.agent
        run = (
            agent.find_subagent_run(self.selected())
            if agent is not None and self.selected()
            else None
        )
        if agent is None or run is None:
            self.notify("Select a subagent first.", severity="warning")
            return
        self.notify("\n".join(agents_command(agent, f"stop {run.number}", S().states)))

    def action_close(self) -> None:
        self.app.pop_screen()


class CmcoderApp(App[int]):
    TITLE = "cmcoder"  # replaced by the brand's name in __init__
    CSS = """
    #log { height: 1fr; padding: 0 1; }
    #log > Static { margin-bottom: 0; }
    #prompt { border: tall $accent; }
    #status { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    #agents { height: auto; max-height: 24; padding: 0 1; border-top: solid $panel; display: none; }
    .user { color: $text; text-style: bold; margin-top: 1; }
    .tool { color: $accent; }
    .dim { color: $text-muted; }
    .warn { color: $warning; }
    .err { color: $error; text-style: bold; }
    """
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt", priority=True),
        Binding("shift+tab", "cycle_mode", "Mode", priority=True),
        Binding("ctrl+g", "navigator", "Agent navigator"),
        Binding("ctrl+d", "quit", "Quit"),
    ]

    def __init__(self, settings: Settings, initial_prompt: str | None = None) -> None:
        super().__init__()
        self.brand = brand.load()
        self.title = self.brand.product_name
        self.settings = settings
        self.initial_prompt = initial_prompt
        self.agent: Agent | None = None
        self.turn: Worker[None] | None = None
        self._md: Markdown | None = None
        self._text = ""
        self._last_update = 0.0
        self._prompt_tokens = 0
        self._quit_armed = False
        self.tasks = ParallelTasks()
        self.map = AgentMap()  # this turn's subagents, shown above the input

    # -- layout -------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="log")
        yield Static(id="agents")
        yield Input(
            placeholder=f"Ask {self.brand.product_name}…  (/help for commands)",
            id="prompt",
            suggester=SlashSuggester(self),
        )
        yield Static(id="status")

    async def on_mount(self) -> None:
        assert self.agent is not None
        a = self.agent
        cfg = self.settings.providers[a.provider.name]
        if self.brand.logo:
            self.write(Text(self.brand.logo, style=self.brand.accent_color))
        self.write(
            Text.assemble(
                (f"{self.brand.product_name} ", f"bold {self.brand.accent_color}"),
                f"{__version__}  ·  {a.model} ({a.provider.name}: {cfg.base_url})\n",
                (f"cwd {a.ctx.cwd}", "dim"),
                (
                    f"\nproject {a.ctx.project_root}" if a.ctx.project_root != a.ctx.cwd else "",
                    "dim",
                ),
                (
                    "\npolicy: managed settings in effect" if self.settings.managed_path else "",
                    "dim",
                ),
            )
        )
        if warning := ignored_settings_message(self.settings):
            self.write(f"{S().warn} {warning}", "warn")
        if len(a.messages) > 1:
            self.write(
                f"Resumed conversation {a.session_id[:8]} ({len(a.messages) - 1} messages).", "tool"
            )
        self.update_status()
        self.set_interval(1.0, self.refresh_map)  # running subagents' times tick
        self.query_one("#prompt", Input).focus()
        if self.initial_prompt:
            self.start_turn(self.initial_prompt)

    def write(self, renderable: Any, classes: str = "") -> None:
        log = self.query_one("#log", VerticalScroll)
        log.mount(Static(renderable, classes=classes))
        log.scroll_end(animate=False)

    def refresh_map(self) -> None:
        """The mind map above the input while the turn runs; the navigator
        screen too when it's open."""
        if isinstance(self.screen, AgentNavigatorScreen):
            self.screen.sync()
        panel = self.query_one("#agents", Static)
        lines = self.map.mind_map(max(40, self.size.width - 4)) if self.map.busy else []
        panel.display = bool(lines)
        if not lines:
            return
        text = Text("Subagents", style="bold")
        if self.map.active:
            text.append(
                "  ·  Ctrl+G navigator · /agents stop <n> stops one · Ctrl+C everything",
                style="dim",
            )
        for line in lines:
            text.append("\n")
            text.append_text(line)
        panel.update(text)

    def new_map(self, prompt: str) -> None:
        assert self.agent is not None
        self.map.start_turn(prompt, self.agent.model)
        if isinstance(self.screen, AgentNavigatorScreen):
            self.screen.reset()

    def action_navigator(self) -> None:
        if isinstance(self.screen, AgentNavigatorScreen):
            self.pop_screen()
        else:
            self.push_screen(AgentNavigatorScreen(self))

    def update_status(self, busy: bool = False) -> None:
        if self.agent is None:
            return
        a = self.agent
        ctx = (
            f" · context {100 * self._prompt_tokens // max(1, a.profile.context_window)}%"
            if self._prompt_tokens
            else ""
        )
        state = "working… (Ctrl+C to interrupt)" if busy else "Shift+Tab: mode"
        self.query_one("#status", Static).update(
            f"{a.model} · mode: {a.policy.mode}{ctx} · {state}"
        )

    # -- turns ----------------------------------------------------------------------

    @on(Input.Submitted, "#prompt")
    async def _submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        if self.turn is not None and self.turn.is_running:
            if line == "/agents" or line.startswith("/agents "):  # e.g. stop one subagent
                await self.command(line)
                return
            self.notify("cmcoder is still working; Ctrl+C interrupts.", severity="warning")
            return
        if line.startswith("/"):
            await self.command(line)
        else:
            self.start_turn(line)

    def start_turn(self, prompt: str) -> None:
        assert self.agent is not None
        self.write(Text(f"> {prompt}"), "user")
        self.new_map(prompt)
        self.turn = self.run_worker(
            self.stream(self.agent.run(prompt)), exclusive=True, group="turn"
        )

    async def stream(
        self, events: Any, interrupted: str = "Interrupted. What should cmcoder do instead?"
    ) -> None:
        self.update_status(busy=True)
        try:
            async for event in events:
                await self.render_event(event)
        except asyncio.CancelledError:
            await self.finish_reply()
            self.write(f"└ {interrupted}", "warn")
        finally:
            self.map.busy = False  # the map stays for /agents map until the next turn
            self.refresh_map()
            self.update_status()

    async def finish_reply(self) -> None:
        if self._md is not None:
            await self._md.update(self._text)
        self._md, self._text = None, ""

    async def render_event(self, event: ev.Event) -> None:
        tag = self.tasks.tag(event)
        self.map.observe(event)
        if isinstance(event, ev.SubagentStatus | ev.Result) or getattr(
            event, "parent_tool_use_id", None
        ):
            self.refresh_map()
        if (line := subagent_line(event, tag)) is not None:  # a step inside a Task call
            if line[0]:
                self.write(line[0], "err" if line[1] == "red" else "dim")
        elif isinstance(event, ev.AssistantDelta):
            if self._md is None:
                self._md = Markdown()
                log = self.query_one("#log", VerticalScroll)
                await log.mount(self._md)
            self._text += event.text
            if time.monotonic() - self._last_update > UPDATE_EVERY:
                self._last_update = time.monotonic()
                await self._md.update(self._text)
                self.query_one("#log", VerticalScroll).scroll_end(animate=False)
        elif isinstance(event, ev.AssistantMessage):
            streamed = self._md is not None
            await self.finish_reply()
            if event.text and not streamed:
                self.write(RichMarkdown(event.text))
        elif isinstance(event, ev.ToolUse):
            await self.finish_reply()
            todos = event.input.get("todos") if event.name == "TodoWrite" else None
            if isinstance(todos, list):
                self.write(self.todo_text(todos), "tool")
            else:
                self.write(f"{S().tool} {event.label}", "tool")
        elif isinstance(event, ev.ToolResult) and (final := self.map.final_line(event.id)):
            state = self.map.runs[event.id].state
            self.write(f"  └ {tag}{final}", "err" if state == "failed" else "dim")
        elif isinstance(event, ev.ToolResult):
            if event.is_error:
                lines = event.content.strip().splitlines()
                self.write(
                    f"  └ {tag}" + "\n    ".join(lines[:4]) + ("\n    …" if len(lines) > 4 else ""),
                    "err",
                )
            else:
                self.write(f"  └ {tag}{event.summary or 'done'}", "dim")
                if preview := output_preview(event, 3):
                    self.write(preview, "dim")
        elif isinstance(event, ev.PermissionDenied):
            self.write(f"  └ denied: {event.reason}", "warn")
        elif isinstance(event, ev.UsageUpdate):
            self._prompt_tokens = event.prompt_tokens
            self.update_status(busy=True)
        elif isinstance(event, ev.Warning):
            self.write(f"{S().warn} {event.message}", "warn")
        elif isinstance(event, ev.Error):
            await self.finish_reply()
            self.write(
                f"{S().error} {event.message}" + (f"\n  {event.hint}" if event.hint else ""), "err"
            )
        elif isinstance(event, ev.CodeContext):
            self.write(f"{S().note} {event.summary()}", "dim")
        elif isinstance(event, ev.Compacted):
            self._prompt_tokens = 0
            self.write(
                f"{S().compacted} Compacted the conversation: summarised {event.summarized_messages} earlier "
                f"messages with {event.model} (≈{event.tokens_before:,} → {event.tokens_after:,} tokens).",
                "tool",
            )
        elif isinstance(event, ev.Result):
            await self.finish_reply()

    @staticmethod
    def todo_text(todos: list[Any]) -> Text:
        out = Text(f"{S().tool} Todo list")
        for t in todos:
            if isinstance(t, dict):
                status = str(t.get("status", "pending"))
                style = {"completed": "strike dim", "in_progress": "bold"}.get(status, "")
                out.append(
                    f"\n  {S().todo.get(status, S().todo['pending'])} {t.get('content', '')}",
                    style=style,
                )
        return out

    async def ask(self, req: PermissionRequest) -> PermissionAnswer:
        source, lexer = Repl._preview_source(req)
        answer = await self.push_screen_wait(PermissionScreen(req, source, lexer))
        self.query_one("#prompt", Input).focus()
        return answer

    # -- commands and keys --------------------------------------------------------------

    async def command(self, line: str) -> None:
        assert self.agent is not None
        a = self.agent
        name, _, arg = line[1:].partition(" ")
        arg = arg.strip()
        self.write(Text(f"> {line}"), "user")
        if name in ("exit", "quit"):
            self.exit(0)
        elif name == "help":
            self.write(HELP, "dim")
            if custom := help_lines(a.command_list()):
                self.write(Text("Your commands\n" + "\n".join(custom)), "dim")
        elif name == "clear":
            a.clear()
            self._prompt_tokens = 0
            self.query_one("#log", VerticalScroll).remove_children()
            self.write("Started a new conversation.", "dim")
        elif name == "compact":
            self.turn = self.run_worker(
                self.stream(
                    a.compact(arg or None), "Compaction cancelled; the conversation is unchanged."
                ),
                exclusive=True,
                group="turn",
            )
        elif name == "mode":
            if arg:
                try:
                    a.policy.mode = arg
                except ModeNotAllowed as e:
                    self.write(str(e), "err")
                except ValueError:
                    self.write(
                        f"Unknown mode. Choose one of: {', '.join(a.policy.available_modes())}",
                        "err",
                    )
            self.write(f"Permission mode: {a.policy.mode}")
        elif name == "model":
            self.write(
                f"Model: {a.model} (context {a.profile.context_window:,} tokens, {a.profile.context_window_source})"
            )
        elif name == "mcp":
            self.write("\n".join(status_lines(a.mcp)))
        elif name == "agents" and arg == "map":
            self.action_navigator()
        elif name == "agents":
            self.write("\n".join(agents_command(a, arg, S().states)))
            self.refresh_map()
        elif name == "index":
            status = self.query_one("#status", Static)

            def show(p: Progress) -> None:
                status.update(f"Indexing: {p.done}/{p.total} files, {p.chunks} pieces…")

            lines = await index_command(a, self.settings, arg, show)
            self.update_status()
            self.write("\n".join(lines))
        elif name == "cost":
            u = a.usage
            cost = f", cost {u.cost:.4f}" if u.cost is not None else ""
            self.write(f"Tokens: {u.prompt_tokens} in, {u.completion_tokens} out{cost}")
        elif name == "todos":
            self.write(
                self.todo_text(a.ctx.todos)
                if a.ctx.todos
                else "No todo list in this conversation.",
                "tool",
            )
        elif name in ("resume", "rewind"):
            self.write(f"/{name} is in the classic UI for now: run cmcoder without --tui.", "warn")
        else:
            try:
                expansion, warnings = await a.expand_command(line)
            except ValueError as e:
                self.write(str(e), "err")
                expansion, warnings = None, []
                name = ""
            for w in warnings:
                self.write(f"{S().warn} {w}", "warn")
            if expansion is not None:
                self.new_map(line)
                self.turn = self.run_worker(
                    self.stream(a.run(expansion.prompt, allow=expansion.allowed_tools)),
                    exclusive=True,
                    group="turn",
                )
            elif name:
                self.write(f"Unknown command /{name}. Type /help.", "err")
        self.update_status()

    def action_interrupt(self) -> None:
        if self.turn is not None and self.turn.is_running:
            self.turn.cancel()
            return
        if self._quit_armed:
            self.exit(0)
            return
        self._quit_armed = True
        self.notify("Press Ctrl+C again to quit (or type /exit).")
        self.set_timer(3, lambda: setattr(self, "_quit_armed", False))

    def action_cycle_mode(self) -> None:
        assert self.agent is not None
        policy = self.agent.policy
        modes = policy.available_modes()
        policy.mode = (
            modes[(modes.index(policy.mode) + 1) % len(modes)] if policy.mode in modes else MODES[0]
        )
        self.update_status(busy=self.turn is not None and self.turn.is_running)


async def run_tui(settings: Settings, opts: AgentOptions, initial_prompt: str | None) -> int:
    app = CmcoderApp(settings, initial_prompt)
    opts.ask = app.ask
    opts.frontend = "tui"
    app.agent = await build_agent(settings, opts)
    try:
        code = await app.run_async()
    finally:
        await app.agent.close()
    return int(code or 0)
