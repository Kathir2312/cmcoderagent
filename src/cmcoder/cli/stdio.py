"""`cmcoder --protocol stdio`: the Agent Protocol over stdin/stdout.

A long-lived process for one conversation, used by the VS Code extension.
The client writes one JSON message per line (`protocol/messages.py`); the
agent writes one JSON event per line (`protocol/events.py`). Nothing else
ever goes to stdout: stray prints are sent to stderr.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import uuid
from contextlib import suppress
from typing import BinaryIO

from pydantic import ValidationError

from ..config.settings import Settings, SettingsError
from ..core.agent import Agent, PermissionAnswer, PermissionRequest
from ..core.permissions import MODES, ModeNotAllowed
from ..protocol import events as ev
from ..protocol import messages as msg
from ..providers.openai_compat import OpenAICompatProvider, ProviderError
from .factory import AgentOptions, build_agent, resolve_model_profile


class StdioServer:
    def __init__(self, settings: Settings, opts: AgentOptions, out: BinaryIO) -> None:
        self.settings = settings
        self.opts = opts
        self.out = out
        self.agent: Agent | None = None
        self.turn: asyncio.Task[None] | None = None
        self.pending: dict[str, asyncio.Future[PermissionAnswer]] = {}
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
        if self.agent.ctx.todos:  # a resumed conversation
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
            self.turn = asyncio.create_task(self._run_turn(message.text))
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

    @property
    def busy(self) -> bool:
        return self.turn is not None and not self.turn.done()

    async def _run_turn(self, text: str) -> None:
        agent = self.agent
        assert agent is not None
        started = time.monotonic()
        try:
            async for event in agent.run(text):
                self.emit(event)
                if isinstance(event, ev.ToolResult) and event.name == "TodoWrite":
                    self.emit(ev.TodoUpdate(todos=agent.ctx.todos))
        except asyncio.CancelledError:
            self.emit(
                ev.Result(
                    subtype="interrupted",
                    is_error=False,
                    result="Interrupted by the user.",
                    num_turns=0,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    usage={},
                    session_id=agent.session_id,
                )
            )
            raise
        except Exception as e:  # a bug: report it instead of dying silently
            self.error("internal", f"{type(e).__name__}: {e}")
            self.emit(
                ev.Result(
                    subtype="error",
                    is_error=True,
                    result=str(e),
                    num_turns=0,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    usage={},
                    session_id=agent.session_id,
                )
            )

    async def _stop_turn(self) -> None:
        for future in self.pending.values():
            future.cancel()
        self.pending.clear()
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
            )
        )
        try:
            answer = await future
        finally:
            self.pending.pop(request_id, None)
        if answer.remember and not req.can_remember:
            answer.remember = False
        return answer

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


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    where = ".".join(str(x) for x in err.get("loc", ()))
    return f"{where}: {err['msg']}" if where else err["msg"]


async def run_stdio(settings: Settings, opts: AgentOptions) -> int:
    out = sys.stdout.buffer
    # Only protocol events may reach stdout; anything else printed goes to stderr.
    sys.stdout = sys.stderr
    return await StdioServer(settings, opts, out).serve(sys.stdin.fileno())
