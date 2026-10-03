"""`cmcoder --protocol stdio`: the Agent Protocol over stdin/stdout.

A long-lived process for one conversation, used by the VS Code extension.
The client writes one JSON message per line (`protocol/messages.py`); the
agent writes one JSON event per line (`protocol/events.py`). Nothing else
ever goes to stdout: stray prints are sent to stderr.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, BinaryIO

from pydantic import ValidationError

from ..config.settings import Settings, SettingsError, ignored_settings_message
from ..core.agent import Agent, PermissionAnswer, PermissionRequest, parse_tool_arguments
from ..core.commands import BUILT_IN, split_line
from ..core.ide import IDE_TOOLS, format_ide_context
from ..core.permissions import MODES, Decision, ModeNotAllowed
from ..core.sessions import list_sessions
from ..protocol import events as ev
from ..protocol import messages as msg
from ..providers.openai_compat import OpenAICompatProvider, ProviderError
from ..tools.base import ToolResult
from ..tools.files import ReadInput, ReadTool
from .factory import AgentOptions, build_agent, resolve_model_profile

IDE_TOOL_TIMEOUT = 30.0  # seconds
_READ = ReadTool()


class StdioServer:
    def __init__(self, settings: Settings, opts: AgentOptions, out: BinaryIO) -> None:
        self.settings = settings
        self.opts = opts
        self.out = out
        self.agent: Agent | None = None
        self.turn: asyncio.Task[None] | None = None
        self.pending: dict[str, asyncio.Future[PermissionAnswer]] = {}
        self.ide_pending: dict[str, asyncio.Future[ToolResult]] = {}
        self.inbox: asyncio.Queue[bytes] = asyncio.Queue()

    def emit(self, event: ev.Event) -> None:
        self.out.write(event.model_dump_json().encode() + b"\n")
        self.out.flush()

    def error(self, kind: str, message: str, hint: str | None = None) -> None:
        self.emit(ev.Error(kind=kind, message=message, hint=hint))

    # --- input ---------------------------------------------------------------

    def _start_reader(self, stdin_fd: int) -> None:
        """Read stdin in a thread: portable (Windows pipes don't work with
        asyncio's stdin readers) and a daemon, so a blocked read never holds
        up exit.

        It reads the raw file descriptor with `os.read`, not `sys.stdin`: a
        daemon thread blocked inside a *buffered* read holds the buffer's lock,
        and Python aborts (SIGABRT) if it is still held at interpreter exit."""
        loop = asyncio.get_running_loop()

        def put(line: bytes) -> None:
            with suppress(RuntimeError):  # the loop already closed: we're exiting
                loop.call_soon_threadsafe(self.inbox.put_nowait, line)

        def read() -> None:
            buffer = b""
            while True:
                try:
                    chunk = os.read(stdin_fd, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    put(line)
            if buffer:
                put(buffer)
            put(b"")  # EOF

        threading.Thread(target=read, name="cmcoder-stdin", daemon=True).start()

    # --- main loop -----------------------------------------------------------

    async def serve(self, stdin_fd: int) -> int:
        self.opts.ask = self.ask
        try:
            self.agent = await build_agent(self.settings, self.opts)
        except (SettingsError, ProviderError, ValueError) as e:
            self.error("startup", str(e).split("\n  hint:")[0], getattr(e, "hint", None))
            return 2
        self._start_reader(stdin_fd)
        self.emit(self.agent.init_event())
        if warning := ignored_settings_message(self.settings):
            self.emit(ev.Warning(message=warning))
        if len(self.agent.messages) > 1:  # a resumed conversation
            self.emit(ev.History(messages=history(self.agent)))
        if self.agent.ctx.todos:
            self.emit(ev.TodoUpdate(todos=self.agent.ctx.todos))
        try:
            while True:
                line = await self.inbox.get()
                if not line:
                    break  # EOF: the client went away
                if not line.strip():
                    continue
                try:
                    message = msg.parse_message(line)
                except ValidationError as e:
                    self.error("protocol", f"Invalid message: {_first_error(e)}")
                    continue
                if isinstance(message, msg.Shutdown):
                    break
                await self.handle(message)
        finally:
            await self._stop_turn()
            await self.agent.close()
        return 0

    async def handle(self, message: msg.ClientMessage) -> None:
        agent = self.agent
        assert agent is not None
        if isinstance(message, msg.UserMessage):
            if self.busy:
                self.error("busy", "A turn is already running; interrupt it first.")
                return
            note = (
                format_ide_context(message.context, agent.ctx.project_root, self._withheld)
                if message.context
                else None
            )
            self.turn = asyncio.create_task(self._run_turn(message.text, note))
        elif isinstance(message, msg.Interrupt):
            await self._stop_turn()
        elif isinstance(message, msg.PermissionResponse):
            future = self.pending.pop(message.request_id, None)
            if future is None or future.done():
                self.error("protocol", f"No pending permission request {message.request_id!r}.")
                return
            future.set_result(
                PermissionAnswer(
                    allow=message.allow, remember=message.remember, feedback=message.feedback
                )
            )
        elif isinstance(message, msg.SetMode):
            if message.mode not in MODES:
                modes = ", ".join(agent.policy.available_modes())
                self.error(
                    "invalid_mode", f"Unknown mode {message.mode!r}. Choose one of: {modes}."
                )
                return
            try:
                agent.policy.mode = message.mode
            except ModeNotAllowed as e:
                self.error("invalid_mode", str(e))
                return
            self.emit(ev.ModeChanged(mode=agent.policy.mode))
        elif isinstance(message, msg.SetModel):
            await self._set_model(message.model)
        elif isinstance(message, msg.IdeCapabilities):
            for name in message.tools:
                if tool := IDE_TOOLS.get(name):
                    agent.tools[name] = tool(self.ide_call)
        elif isinstance(message, msg.IdeToolResult):
            pending = self.ide_pending.pop(message.request_id, None)
            if pending is None or pending.done():
                self.error("protocol", f"No pending IDE tool request {message.request_id!r}.")
                return
            pending.set_result(ToolResult(message.content, is_error=message.is_error))
        elif isinstance(message, msg.ListSessions):
            sessions = [
                ev.SessionSummary(
                    id=s.session_id, title=s.title, updated=s.updated, messages=s.messages
                )
                for s in list_sessions(agent.ctx.project_root)
                if s.session_id != agent.session_id
            ]
            self.emit(ev.SessionList(sessions=sessions))
        elif isinstance(message, msg.ListCommands):
            self.emit(ev.CommandList(commands=self.command_list()))

    def _withheld(self, path: str) -> bool:
        """Files whose contents Read would refuse (secrets, deny rules)."""
        agent = self.agent
        assert agent is not None
        try:
            args = ReadInput.model_validate({"file_path": path})
            return agent.policy.check(_READ, args, agent.ctx).decision == Decision.DENY
        except (ValueError, OSError):
            return True

    @property
    def busy(self) -> bool:
        return self.turn is not None and not self.turn.done()

    def _result(self, subtype: str, text: str, is_error: bool, started: float) -> ev.Result:
        agent = self.agent
        assert agent is not None
        return ev.Result(
            subtype=subtype,  # type: ignore[arg-type]
            is_error=is_error,
            result=text,
            num_turns=0,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage={},
            session_id=agent.session_id,
        )

    async def _turn_events(
        self, text: str, context: str | None, started: float
    ) -> AsyncIterator[ev.Event]:
        """A prompt, or a slash command: `/compact`, a custom command or an MCP prompt."""
        agent = self.agent
        assert agent is not None
        if not text.startswith("/"):
            async for event in agent.run(text, context):
                yield event
            return
        name, arguments = split_line(text)
        if name == "compact":
            async for event in agent.compact(arguments or None):
                yield event
            yield self._result("success", "", False, started)
            return
        try:
            expansion, warnings = await agent.expand_command(text)
        except ValueError as e:
            yield ev.Warning(message=str(e))
            yield self._result("error", str(e), True, started)
            return
        for w in warnings:
            yield ev.Warning(message=w)
        if expansion is not None:
            async for event in agent.run(expansion.prompt, context, allow=expansion.allowed_tools):
                yield event
        elif name in BUILT_IN:
            message = f"/{name} isn't available here; use the panel's buttons or the terminal."
            yield ev.Warning(message=message)
            yield self._result("error", message, True, started)
        else:  # not a command (e.g. a path): an ordinary prompt
            async for event in agent.run(text, context):
                yield event

    async def _run_turn(self, text: str, context: str | None = None) -> None:
        agent = self.agent
        assert agent is not None
        started = time.monotonic()
        try:
            async for event in self._turn_events(text, context, started):
                self.emit(event)
                if isinstance(event, ev.ToolResult) and event.name == "TodoWrite":
                    self.emit(ev.TodoUpdate(todos=agent.ctx.todos))
        except asyncio.CancelledError:
            self.emit(self._result("interrupted", "Interrupted by the user.", False, started))
            raise
        except Exception as e:  # a bug: report it instead of dying silently
            self.error("internal", f"{type(e).__name__}: {e}")
            self.emit(self._result("error", str(e), True, started))

    def command_list(self) -> list[ev.CommandInfo]:
        agent = self.agent
        assert agent is not None
        out = [
            ev.CommandInfo(
                name="compact",
                description="Summarise the conversation so far to free context",
                argument_hint="[focus]",
                origin="built-in",
            )
        ]
        for c in sorted(agent.command_list().values(), key=lambda c: c.name):
            out.append(
                ev.CommandInfo(
                    name=c.name,
                    description=c.description,
                    argument_hint=c.argument_hint,
                    origin=c.origin,
                )
            )
        return out

    async def _stop_turn(self) -> None:
        for future in [*self.pending.values(), *self.ide_pending.values()]:
            future.cancel()
        self.pending.clear()
        self.ide_pending.clear()
        if self.busy:
            assert self.turn is not None
            self.turn.cancel()
            try:
                await self.turn
            except asyncio.CancelledError:
                pass

    async def ask(self, req: PermissionRequest) -> PermissionAnswer:
        request_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[PermissionAnswer] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.emit(
            ev.PermissionRequest(
                request_id=request_id,
                tool_use_id=req.call_id,
                name=req.tool_name,
                label=req.label,
                input=req.input,
                suggested_rule=req.suggested_rule,
                reason=req.reason,
                can_remember=req.can_remember,
                change=ev.FileChange(
                    path=str(req.change.path), before=req.change.before, after=req.change.after
                )
                if req.change
                else None,
            )
        )
        try:
            answer = await future
        finally:
            self.pending.pop(request_id, None)
        if answer.remember and not req.can_remember:
            answer.remember = False
        return answer

    async def ide_call(self, name: str, data: dict[str, Any]) -> ToolResult:
        """Run an IDE tool in the client and wait for its answer."""
        request_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[ToolResult] = asyncio.get_running_loop().create_future()
        self.ide_pending[request_id] = future
        self.emit(ev.IdeToolRequest(request_id=request_id, name=name, input=data))
        try:
            return await asyncio.wait_for(future, IDE_TOOL_TIMEOUT)
        except TimeoutError:
            return ToolResult(f"The editor did not answer {name} in time.", is_error=True)
        finally:
            self.ide_pending.pop(request_id, None)

    async def _set_model(self, ref: str) -> None:
        agent = self.agent
        assert agent is not None
        if self.busy:
            self.error("busy", "Can't switch models during a turn.")
            return
        try:
            provider_name, model = self.settings.resolve_model(ref)
        except (SettingsError, ValueError) as e:
            self.error("invalid_model", str(e))
            return
        if provider_name != agent.provider.name:
            self.error(
                "invalid_model",
                f"{ref!r} is on provider {provider_name!r}; switching providers needs a restart.",
            )
            return
        provider = agent.provider
        assert isinstance(provider, OpenAICompatProvider)
        agent.model = model
        agent.profile = await resolve_model_profile(self.settings, provider, model)
        self.emit(ev.ModelChanged(model=model, context_window=agent.profile.context_window))


REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.DOTALL)


def history(agent: Agent) -> list[ev.HistoryItem]:
    """The conversation so far, as a client shows it: prompts, replies and tool labels."""
    items: list[ev.HistoryItem] = []
    for m in agent.messages[1:]:
        if m.role == "user":
            text = REMINDER.sub("", m.content).strip()
            if text:
                items.append(ev.HistoryItem(role="user", text=text))
        elif m.role == "assistant":
            if m.content.strip():
                items.append(ev.HistoryItem(role="assistant", text=m.content.strip()))
            for call in m.tool_calls or []:
                items.append(
                    ev.HistoryItem(role="tool", text=_label(agent, call.name, call.arguments))
                )
    return items


def _label(agent: Agent, name: str, arguments: str) -> str:
    tool = agent.tools.get(name)
    data, _ = parse_tool_arguments(arguments)
    if tool is None or data is None:
        return name
    try:
        return tool.describe(tool.Input.model_validate(data), agent.ctx)
    except ValidationError:
        return name


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    where = ".".join(str(x) for x in err.get("loc", ()))
    return f"{where}: {err['msg']}" if where else err["msg"]


async def run_stdio(settings: Settings, opts: AgentOptions) -> int:
    out = sys.stdout.buffer
    # Only protocol events may reach stdout; anything else printed goes to stderr.
    sys.stdout = sys.stderr
    return await StdioServer(settings, opts, out).serve(sys.stdin.fileno())
