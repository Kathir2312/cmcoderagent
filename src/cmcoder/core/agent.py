"""The agent loop: model -> tool calls -> results -> repeat."""

from __future__ import annotations

import ast
import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from ..mcp_client import McpManager, McpServer
from ..protocol import events as ev
from ..providers.messages import (
    Message,
    ReasoningDelta,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)
from ..providers.openai_compat import ContextTooLong, ProviderError, no_tool_support
from ..providers.profiles import ModelProfile
from ..providers.text_tools import Holdback, extract
from ..tools.base import FileChange, Tool, ToolContext, ToolResult, truncate_middle
from .checkpoints import Checkpoints, RestoreAction
from .commands import (
    CommandSource,
    Expansion,
    SlashCommand,
    mcp_prompt_commands,
    prompt_arguments,
    prompt_text,
    split_line,
    substitute,
)
from .compaction import CompactionError, Summarizer, compact
from .context import WARN_RATIO, ContextBudget
from .hooks import Hook, HookEvent, HookOutcome, HookRunner
from .permissions import (
    FILE_EDIT_TOOLS,
    Decision,
    PermissionCheck,
    PermissionPolicy,
    suggest_rule,
)
from .sessions import SessionLog
from .steer import file_work_redirect
from .titles import make_title

TITLE_WAIT_ON_CLOSE = 2.0  # seconds a pending title may still take when closing

MAX_IDENTICAL_CALLS = 3
MAX_STOP_HOOK_BLOCKS = 3  # a Stop hook can't keep a turn going forever

# Models often skip the optional TodoWrite tool. Like Claude Code, remind them
# once per turn when a task turns out to have several steps.
TODO_REMINDER_AFTER = 3  # tool calls in one turn without a todo list
TODO_REMINDER = (
    "\n\n<system-reminder>This task has several steps. Use the TodoWrite tool now to "
    "list them, mark the current one in_progress, and mark each completed as soon as "
    "it is done. If the task is really finished, ignore this. Don't mention this "
    "reminder to the user.</system-reminder>"
)


def last_todos(messages: list[Message]) -> list[dict[str, Any]]:
    """The todo list from the last TodoWrite call (to restore it on resume)."""
    for m in reversed(messages):
        for c in reversed(m.tool_calls):
            if c.name == "TodoWrite":
                args, _ = parse_tool_arguments(c.arguments)
                todos = (args or {}).get("todos")
                if isinstance(todos, list):
                    return [t for t in todos if isinstance(t, dict)]
    return []


class ChatProvider(Protocol):
    name: str

    def stream_chat(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec],
        profile: ModelProfile,
        *,
        thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]: ...


@dataclass
class PermissionRequest:
    call_id: str
    tool_name: str
    label: str
    input: dict[str, Any]
    suggested_rule: str
    reason: str = ""
    # False for high-risk commands: approve this once only, never "always".
    can_remember: bool = True
    # For Write/Edit: the file before and after, for a diff review.
    change: FileChange | None = None


@dataclass
class PermissionAnswer:
    allow: bool
    remember: bool = False
    # Optional message for the model when denying ("use pytest instead").
    feedback: str | None = None


AskFn = Callable[[PermissionRequest], Awaitable[PermissionAnswer]]


def parse_tool_arguments(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse model-produced JSON arguments, repairing common mistakes."""
    text = (raw or "").strip()
    if not text:
        return {}, None
    attempts = [text]
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    attempts.append(re.sub(r",\s*([}\]])", r"\1", fenced))
    value: Any = None
    error: str | None = None
    for candidate in attempts:
        try:
            value = json.loads(candidate, strict=False)
            error = None
            break
        except ValueError as e:
            error = str(e)
    if error is not None:
        # Python-style dicts ({'path': 'a', 'all': True}) from some small models.
        try:
            value = ast.literal_eval(fenced)
            error = None
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass
    if error is not None:
        return None, f"arguments are not valid JSON ({error})"
    if isinstance(value, str):  # double-encoded
        try:
            value = json.loads(value, strict=False)
        except ValueError:
            pass
    if not isinstance(value, dict):
        return None, "arguments must be a JSON object"
    return value, None


def format_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


class Agent:
    def __init__(
        self,
        provider: ChatProvider,
        model: str,
        profile: ModelProfile,
        tools: list[Tool],
        policy: PermissionPolicy,
        ctx: ToolContext,
        system_prompt: str,
        *,
        max_turns: int = 50,
        ask: AskFn | None = None,
        on_rule_saved: Callable[[str], None] | None = None,
        summarizer: Summarizer | None = None,
        on_context_window: Callable[[str, int], None] | None = None,
        session: SessionLog | None = None,
        auto_compact: bool = True,
        compact_threshold: float = 0.8,
        mcp: McpManager | None = None,
        hooks: HookRunner | None = None,
        commands: CommandSource | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.profile = profile
        self.tools = {t.name: t for t in tools}
        self.policy = policy
        self.ctx = ctx
        self.max_turns = max_turns
        self.ask = ask
        self.on_rule_saved = on_rule_saved
        # Where the conversation is saved (None: not saved).
        self.session = session
        self.session_id = session.session_id if session else str(uuid.uuid4())
        self._session_started = False
        self.turn = 0  # user turns so far in this session
        self.checkpoints = self._new_checkpoints()
        # Background jobs for the small model (session titles).
        self._background: set[asyncio.Task[None]] = set()
        self._titled = False
        self.system_prompt = system_prompt
        self.messages: list[Message] = [Message.system(system_prompt)]
        self.usage = Usage()
        self._budget: ContextBudget | None = None
        # The last Bash command redirected to a file tool; sending it again runs it.
        self._redirected: str | None = None
        # The small/fast model for summaries; the main model is the fallback.
        self.summarizer = summarizer
        # MCP servers: started at the beginning of the first turn.
        self.mcp = mcp
        # Hooks (core/hooks.py); SessionStart runs with the first turn.
        self.hooks = hooks
        self._session_hooks_done = False
        # Custom slash commands (core/commands.py); MCP prompts are added to them.
        self.commands = commands
        # Called with (model, tokens) when the server states a smaller window.
        self.on_context_window = on_context_window
        self.auto_compact = auto_compact
        self.compact_threshold = compact_threshold

    def budget(self) -> ContextBudget:
        """Context budget for the current model profile (rebuilt after /model)."""
        b = self._budget
        p = self.profile
        if b is None or b.window != p.context_window or b.max_output != p.max_output:
            nb = ContextBudget(p.context_window, p.max_output, self.tool_specs())
            if b is not None:
                nb.chars_per_token = b.chars_per_token
                nb.max_chars_per_token = b.max_chars_per_token
            self._budget = nb
        assert self._budget is not None
        return self._budget

    def init_event(self) -> ev.SystemInit:
        return ev.SystemInit(
            session_id=self.session_id,
            cwd=str(self.ctx.cwd),
            model=self.model,
            provider=self.provider.name,
            tools=list(self.tools),
            permission_mode=self.policy.mode,
        )

    def _new_checkpoints(self) -> Checkpoints:
        folder = self.session.path.with_suffix(".checkpoints") if self.session else None
        return Checkpoints(folder)

    def clear(self) -> None:
        """Start a new conversation (the old one stays resumable)."""
        self.messages = [Message.system(self.system_prompt)]
        self.turn = 0
        self.ctx.todos = []
        self._titled = False
        if self.session is not None:
            self.session = SessionLog(self.ctx.project_root)
            self.session_id = self.session.session_id
            self._session_started = False
        self.checkpoints = self._new_checkpoints()

    def rewind_points(self) -> list[tuple[int, str, int]]:
        """Earlier user messages to rewind to: (turn, text, files changed since)."""
        out = []
        for m in self.messages:
            if m.role == "user" and m.turn:
                out.append((m.turn, m.content, len(self.checkpoints.changes_since(m.turn))))
        return out

    def rewind(
        self, turn: int, *, code: bool = True, conversation: bool = True, outside: bool = False
    ) -> tuple[list[RestoreAction], str | None]:
        """Go back to just before user message `turn`. Returns the file actions
        and that message's text (to edit and send again), if the conversation
        was rewound. Files outside the project are only restored if `outside`."""
        actions: list[RestoreAction] = []
        if code:
            actions = self.checkpoints.restore(
                turn, include_outside=None if outside else self.ctx.project_root.resolve()
            )
        prompt = None
        if conversation:
            idx = next(
                (i for i, m in enumerate(self.messages) if m.role == "user" and m.turn == turn),
                None,
            )
            if idx is not None:
                prompt = self.messages[idx].content
                self._keep_before_rewind(prompt)
                self.messages = self.messages[:idx]
                self.turn = turn - 1
                self.save_session()
        return actions, prompt

    def _keep_before_rewind(self, prompt: str) -> None:
        """A rewind never loses history: the whole conversation as it was is
        kept as a separate resumable session ("Before rewind: …")."""
        if self.session is None or len(self.messages) <= 1:
            return
        try:
            backup = SessionLog(self.ctx.project_root)
            backup.start(self.ctx.cwd, self.model)
            backup.save(self.messages)
            first = next((m.content for m in self.messages if m.role == "user"), prompt)
            backup.set_title("Before rewind: " + " ".join(first.split())[:45])
        except OSError:
            pass

    def resume(self, messages: list[Message], session: SessionLog | None = None) -> None:
        """Continue a saved conversation (`messages` without the system prompt)."""
        self.messages = [Message.system(self.system_prompt), *messages]
        self.turn = max((m.turn or 0 for m in messages), default=0)
        self.ctx.todos = last_todos(messages)
        self._titled = True  # a resumed session keeps its title
        if session is not None:
            self.session = session
            self.session_id = session.session_id
            self.checkpoints = self._new_checkpoints()
        if self.session is not None:
            self.session.mark_saved(self.messages)
            self._session_started = True

    def save_session(self) -> None:
        """Write new messages to the session file. Never fails the turn."""
        if self.session is None:
            return
        try:
            if not self._session_started:
                if len(self.messages) <= 1:
                    return  # nothing to save yet
                self.session.start(self.ctx.cwd, self.model)
                self._session_started = True
            self.session.save(self.messages)
        except OSError:
            pass

    def tool_specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.tools.values()]

    def _start_title(self, first_message: str) -> None:
        """After the first turn: ask the small model for a session title,
        in the background (nothing waits for it)."""
        self._titled = True
        summarizer, session = self.summarizer, self.session
        if summarizer is None or session is None:
            return

        async def job() -> None:
            try:
                title, usage = await make_title(summarizer, first_message)
                self.usage.add(usage)
                if title:
                    session.set_title(title)
            except Exception:  # a title is a nicety: never disturb the session
                pass

        task = asyncio.get_running_loop().create_task(job())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _hook(
        self, event: HookEvent, payload: dict[str, Any], match: str | None = None
    ) -> HookOutcome:
        """Runs one event's hooks with the common fields every hook gets."""
        assert self.hooks is not None
        base = {
            "session_id": self.session_id,
            "transcript_path": str(self.session.path) if self.session else None,
            "cwd": str(self.ctx.cwd),
            "permission_mode": self.policy.mode,
        }
        return await self.hooks.run(
            event, {**base, **payload}, match=match, approve_fn=self._approve_hook
        )

    def _notify(self, message: str) -> None:
        """Notification hooks, in the background (a desktop popup must not delay the prompt)."""
        if self.hooks is None or not self.hooks.has("Notification"):
            return

        async def notify() -> None:
            await self._hook("Notification", {"message": message})

        task = asyncio.create_task(notify())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _approve_hook(self, hook: Hook) -> bool:
        """Asks once before a project's hook first runs (it runs as you)."""
        if self.ask is None:
            return False
        answer = await self.ask(
            PermissionRequest(
                call_id=f"hook-{hook.event}",
                tool_name="Hook",
                label=f"Run this project's {hook.event} hook",
                input={
                    "event": hook.event,
                    "matcher": hook.matcher or "*",
                    "command": hook.command.command,
                },
                suggested_rule="",
                reason="This project's settings want to run a command on every "
                f"{hook.event} event; it runs with your permissions. Allowing is "
                "remembered until the command changes.",
                can_remember=False,
            )
        )
        return answer.allow

    async def _approve_mcp_server(self, server: McpServer) -> bool:
        """Asks once before a project's MCP server first starts (it runs as you)."""
        if self.ask is None:
            return False
        answer = await self.ask(
            PermissionRequest(
                call_id=f"mcp-{server.name}",
                tool_name="McpServer",
                label=f"Start MCP server {server.name}",
                input={"server": server.name, "runs": server.config.describe()},
                suggested_rule="",
                reason="This project's settings (.mcp.json) want to start an MCP server; it "
                "runs with your permissions. Allowing is remembered until its settings change.",
                can_remember=False,
            )
        )
        return answer.allow

    async def close(self) -> None:
        if self.mcp is not None:
            await self.mcp.close()
        if self._background:  # let a title finish briefly, then stop waiting
            _done, pending = await asyncio.wait(self._background, timeout=TITLE_WAIT_ON_CLOSE)
            for t in pending:
                t.cancel()
        if self.ctx.shell:
            await self.ctx.shell.close()
        providers = [self.provider]
        if self.summarizer is not None and self.summarizer.provider is not self.provider:
            providers.append(self.summarizer.provider)
        for p in providers:
            aclose = getattr(p, "aclose", None)
            if aclose is not None:
                await aclose()

    def _summarizers(self) -> list[Summarizer]:
        main = Summarizer(self.provider, self.model, self.profile)
        if self.summarizer is None or self.summarizer.model == self.model:
            return [main]
        return [self.summarizer, main]

    async def compact(
        self, focus: str | None = None, *, trigger: str = "manual"
    ) -> AsyncIterator[ev.Event]:
        """Summarise the older part of the conversation (`/compact`, or
        automatically when the window fills). The conversation is only
        replaced once the summary is ready, so cancelling leaves it intact."""
        if self.hooks is not None:
            outcome = await self._hook(
                "PreCompact", {"trigger": trigger, "custom_instructions": focus or ""}
            )
            for w in outcome.warnings:
                yield ev.Warning(message=w)
        budget = self.budget()
        before = budget.estimate(self.messages)
        try:
            res = await compact(
                self.messages,
                self._summarizers(),
                window=budget.window,
                chars_per_token=budget.chars_per_token,
                focus=focus,
                force=trigger == "manual",
                todos=self.ctx.todos,
            )
        except CompactionError as e:
            yield ev.Warning(message=f"Could not summarise the conversation: {e}")
            return
        if res is None:
            if trigger == "manual":
                yield ev.Warning(message="Nothing to compact yet.")
            return
        self.messages = res.messages
        self.usage.add(res.usage)
        self.save_session()
        yield ev.Compacted(
            trigger=trigger,  # type: ignore[arg-type]
            summarized_messages=res.summarized,
            tokens_before=before,
            tokens_after=budget.estimate(self.messages),
            model=res.model,
        )

    # ------------------------------------------------------------------

    async def _start_mcp(self) -> AsyncIterator[ev.Warning]:
        if self.mcp is not None and not self.mcp.started:
            async for warning in self.mcp.start(self._approve_mcp_server):
                yield warning
            for tool in self.mcp.tools():
                self.tools.setdefault(tool.name, tool)

    def command_list(self) -> dict[str, SlashCommand]:
        """Custom commands and the prompts of connected MCP servers."""
        found = self.commands.load() if self.commands is not None else {}
        if self.mcp is not None:
            found.update(mcp_prompt_commands(self.mcp.connected()))
        return found

    async def expand_command(self, line: str) -> tuple[Expansion | None, list[str]]:
        """`/name args` as a prompt: (None, ...) when no such command exists.
        The list is warnings to show (e.g. an MCP server that didn't start).
        Raises ValueError when the command exists but can't be expanded."""
        name, arguments = split_line(line)
        warnings: list[str] = []
        if name.startswith("mcp__"):
            async for w in self._start_mcp():  # prompts are known once servers run
                warnings.append(w.message)
        command = self.command_list().get(name)
        if command is None:
            return None, warnings
        if command.mcp is not None:
            server, p = command.mcp
            names = [a.name for a in (p.arguments or [])]
            values = prompt_arguments(names, arguments)
            missing = [a.name for a in (p.arguments or []) if a.required and a.name not in values]
            if missing:
                raise ValueError(f"/{name} needs: {' '.join(f'<{m}>' for m in missing)}")
            try:
                result = await asyncio.wait_for(
                    server.client.get_prompt(p.name, values), server.config.tool_timeout
                )
            except Exception as e:  # the server's problem
                raise ValueError(f"MCP server {server.name} could not give /{name}: {e}") from e
            return Expansion(name, prompt_text(result), []), warnings
        return Expansion(name, substitute(command.body, arguments), command.allowed_tools), warnings

    async def run(
        self, prompt: str, context: str | None = None, *, allow: list[str] | None = None
    ) -> AsyncIterator[ev.Event]:
        """Run one user turn. Yields protocol events, ending with a Result.

        `context` (e.g. the editor's open file and selection) goes into the
        user message ahead of the prompt; titles and history use the prompt.
        `allow`: extra allow rules for this turn only (a command's allowed-tools)."""
        self.policy.set_turn_allow(allow or [])
        try:
            async for event in self._run(prompt, context):
                yield event
        finally:
            self.policy.set_turn_allow([])

    async def _run(self, prompt: str, context: str | None) -> AsyncIterator[ev.Event]:
        started = time.monotonic()
        self.turn += 1
        user_message = Message.user(f"{context}\n\n{prompt}" if context else prompt)
        user_message.turn = self.turn
        self.messages.append(user_message)
        async for warning in self._start_mcp():
            yield warning
        if self.hooks is not None:
            notes: list[str] = []
            if not self._session_hooks_done:
                self._session_hooks_done = True
                source = "resume" if len(self.messages) > 2 else "startup"
                outcome = await self._hook("SessionStart", {"source": source})
                for w in outcome.warnings:
                    yield ev.Warning(message=w)
                notes += outcome.context
            outcome = await self._hook("UserPromptSubmit", {"prompt": prompt})
            for w in outcome.warnings:
                yield ev.Warning(message=w)
            if outcome.block:
                self.messages.pop()
                self.turn -= 1
                yield ev.Warning(
                    message=f"A UserPromptSubmit hook blocked this prompt: {outcome.reason}"
                )
                yield ev.Result(
                    subtype="error",
                    is_error=True,
                    result=f"Blocked by a hook: {outcome.reason}",
                    num_turns=0,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    usage={},
                    session_id=self.session_id,
                )
                return
            notes += outcome.context
            if notes:
                hook_note = "\n".join(notes)
                user_message.content = (
                    f"<system-reminder>\nFrom the user's hooks:\n{hook_note}\n</system-reminder>"
                    f"\n\n{user_message.content}"
                )
        turn_usage = Usage()
        last_text = ""
        steps = 0
        recent_calls: list[str] = []
        stop_blocks = 0  # times a Stop hook sent the model back to work this turn
        todo_reminded = False  # sent at most once, and not after TodoWrite was used
        warned_context = False
        overflow_retried = False
        prompted_retried = False
        # Turned off for the rest of the turn if summarising fails, so a
        # broken summariser isn't retried before every model call.
        can_compact = self.auto_compact

        def result(subtype: str, text: str, is_error: bool = False) -> ev.Result:
            return ev.Result(
                subtype=subtype,  # type: ignore[arg-type]
                is_error=is_error,
                result=text,
                num_turns=steps,
                duration_ms=int((time.monotonic() - started) * 1000),
                usage={
                    "prompt_tokens": turn_usage.prompt_tokens,
                    "completion_tokens": turn_usage.completion_tokens,
                    "estimated": turn_usage.estimated,
                    "cost": turn_usage.cost,
                },
                session_id=self.session_id,
            )

        try:
            while True:
                self.save_session()  # after every step, so a crash loses little
                if steps >= self.max_turns:
                    yield ev.Warning(
                        message=f"Stopped after {self.max_turns} model calls (max turns)."
                    )
                    yield result("max_turns", last_text, is_error=True)
                    return
                steps += 1

                budget = self.budget()
                if (
                    can_compact
                    and budget.estimate(self.messages) >= budget.window * self.compact_threshold
                ):
                    async for e in self.compact(trigger="auto"):
                        if isinstance(e, ev.Warning):
                            can_compact = False
                        yield e
                    warned_context = False
                if not budget.fits(self.messages):
                    removed = budget.free_space(self.messages)
                    if removed:
                        yield ev.Warning(
                            message=f"Context window nearly full: removed {removed} older tool "
                            "output(s). /compact summarises the conversation; /clear starts fresh."
                        )
                    if not budget.fits(self.messages):
                        yield ev.Error(
                            kind="context_length",
                            message="The conversation no longer fits in the model's context window "
                            f"({budget.window} tokens).",
                            hint="Use /clear to start a new conversation, or ask the admin to serve "
                            "the model with a longer context.",
                        )
                        yield result("error", "context window full", is_error=True)
                        return
                request_chars = budget.request_chars(self.messages)

                done: StreamDone | None = None
                holdback = Holdback()  # keeps <tool_call> text off the screen
                try:
                    async for sev in self.provider.stream_chat(
                        self.model,
                        self.messages,
                        self.tool_specs(),
                        self.profile,
                        max_tokens=budget.max_tokens(self.messages),
                    ):
                        if isinstance(sev, TextDelta):
                            if shown := holdback.feed(sev.text):
                                yield ev.AssistantDelta(text=shown)
                        elif isinstance(sev, ReasoningDelta):
                            yield ev.ReasoningDelta(text=sev.text)
                        elif isinstance(sev, StreamDone):
                            done = sev
                except ContextTooLong as e:
                    if e.context_window and e.context_window < self.profile.context_window:
                        # The server told us its real limit: use it from now on.
                        old = self.profile.context_window
                        self.profile = self.profile.model_copy(
                            update={
                                "context_window": e.context_window,
                                "context_window_source": "server limit (from its error)",
                            }
                        )
                        if self.on_context_window:
                            self.on_context_window(self.model, e.context_window)
                        yield ev.Warning(
                            message=f"The server says {self.model}'s context window is "
                            f"{e.context_window:,} tokens (cmcoder assumed {old:,}); "
                            "using that from now on."
                        )
                        budget = self.budget()
                    # Our estimate was too low: be more conservative, free more, retry once.
                    if not overflow_retried:
                        overflow_retried = True
                        steps -= 1
                        budget.distrust()
                        compacted = False
                        if can_compact:
                            async for e in self.compact(trigger="auto"):
                                compacted = compacted or isinstance(e, ev.Compacted)
                                if isinstance(e, ev.Warning):
                                    can_compact = False
                                yield e
                        if not compacted or not budget.fits(self.messages):
                            removed = budget.free_space(self.messages, keep_recent=1)
                            yield ev.Warning(
                                message=f"The server reported the context window was exceeded; "
                                f"removed {removed} older tool output(s) and retrying."
                            )
                        continue
                    yield ev.Error(
                        kind=e.kind,
                        message=str(e).split("\n  hint:")[0],
                        hint="Use /clear to start a new conversation.",
                    )
                    if self.messages[-1].role == "user" and steps == 1:
                        self.messages.pop()
                        self.turn -= 1
                    yield result("error", str(e), is_error=True)
                    return
                except ProviderError as e:
                    if (
                        no_tool_support(e)
                        and not prompted_retried
                        and self.profile.tool_calling != "prompted"
                    ):
                        # The backend rejects the `tools` parameter: describe the
                        # tools in the prompt instead, for the rest of the session.
                        prompted_retried = True
                        steps -= 1
                        self.profile = self.profile.model_copy(update={"tool_calling": "prompted"})
                        yield ev.Warning(
                            message=f"The server rejected native tool calling for {self.model}; "
                            "switching to prompted tool calls for this session. Set "
                            '"toolCalling": "prompted" in modelProfiles to skip this, or enable '
                            "tool calling on the backend (vLLM --enable-auto-tool-choice "
                            "--tool-call-parser hermes)."
                        )
                        continue
                    yield ev.Error(kind=e.kind, message=str(e).split("\n  hint:")[0], hint=e.hint)
                    # Drop the user message if the model never answered, so retrying works cleanly.
                    if self.messages[-1].role == "user" and steps == 1:
                        self.messages.pop()
                        self.turn -= 1
                    yield result("error", str(e), is_error=True)
                    return
                if done is None:
                    yield ev.Error(kind="provider", message="Stream ended without a final message.")
                    yield result("error", "stream ended unexpectedly", is_error=True)
                    return

                overflow_retried = False  # one retry per model call, not per turn
                if rest := holdback.flush():
                    yield ev.AssistantDelta(text=rest)
                msg = done.message
                if not msg.tool_calls and "<tool_call>" in msg.content:
                    # Tool calls written as text (no tool parser on the backend).
                    msg.content, msg.tool_calls = extract(msg.content, set(self.tools))
                self.messages.append(msg)
                self.usage.add(done.usage)
                turn_usage.add(done.usage)
                yield ev.AssistantMessage(
                    text=msg.content,
                    reasoning=msg.reasoning,
                    tool_calls=[
                        {"id": c.id, "name": c.name, "arguments": c.arguments}
                        for c in msg.tool_calls
                    ],
                    model=done.model,
                )
                yield ev.UsageUpdate(
                    prompt_tokens=done.usage.prompt_tokens,
                    completion_tokens=done.usage.completion_tokens,
                    estimated=done.usage.estimated,
                    cost=done.usage.cost,
                    context_window=self.profile.context_window,
                )
                if not done.usage.estimated:
                    budget.calibrate(request_chars, done.usage.prompt_tokens)
                if not warned_context and budget.usage_ratio(done.usage.prompt_tokens) > WARN_RATIO:
                    warned_context = True
                    yield ev.Warning(
                        message=f"Context is {done.usage.prompt_tokens}/{budget.window} tokens. "
                        "Older tool output is dropped automatically as it fills; /clear starts fresh."
                    )
                if msg.content:
                    last_text = msg.content

                if not msg.tool_calls:
                    if done.finish_reason == "length":
                        yield ev.Warning(message="The reply was cut off at the max output length.")
                    if not msg.content and not msg.tool_calls:
                        yield ev.Warning(message="The model returned an empty reply.")
                    if self.hooks is not None and stop_blocks < MAX_STOP_HOOK_BLOCKS:
                        outcome = await self._hook("Stop", {"stop_hook_active": stop_blocks > 0})
                        for w in outcome.warnings:
                            yield ev.Warning(message=w)
                        if outcome.block:
                            # Not done yet, says a hook (e.g. "the tests fail"): continue.
                            stop_blocks += 1
                            yield ev.Warning(
                                message=f"A Stop hook asked to continue: {outcome.reason}"
                            )
                            self.messages.append(
                                Message.user(
                                    "<system-reminder>A Stop hook says the task isn't finished: "
                                    f"{outcome.reason}</system-reminder>"
                                )
                            )
                            continue
                    yield result("success", last_text)
                    return

                stop_turn = False
                for call in msg.tool_calls:
                    if stop_turn:
                        self.messages.append(
                            Message.tool_result(
                                call.id, call.name, "Skipped: the user stopped this turn."
                            )
                        )
                        continue
                    if call.name == "TodoWrite":
                        todo_reminded = True
                    signature = f"{call.name}:{call.arguments.strip()}"
                    recent_calls.append(signature)
                    repeated = len(recent_calls) >= MAX_IDENTICAL_CALLS and all(
                        s == signature for s in recent_calls[-MAX_IDENTICAL_CALLS:]
                    )
                    async for out in self._run_call(call, repeated):
                        if isinstance(out, _StopTurn):
                            stop_turn = True
                        else:
                            yield out
                if stop_turn:
                    yield result("interrupted", "Stopped: the user denied a tool call.")
                    return
                if not todo_reminded and self._needs_todo_reminder(len(recent_calls)):
                    todo_reminded = True
                    self.messages[-1].content += TODO_REMINDER
        except asyncio.CancelledError:
            self._repair_after_interrupt()
            raise
        finally:
            self.save_session()
            if self.turn == 1 and not self._titled and self._session_started:
                self._start_title(prompt)

    def _needs_todo_reminder(self, calls_this_turn: int) -> bool:
        """True when this turn has made several tool calls and there is no open todo list."""
        if calls_this_turn < TODO_REMINDER_AFTER or "TodoWrite" not in self.tools:
            return False
        if any(t.get("status") != "completed" for t in self.ctx.todos):
            return False  # a list is open and being kept
        return self.messages[-1].role == "tool"

    async def _run_call(
        self, call: ToolCall, repeated: bool
    ) -> AsyncIterator[ev.Event | _StopTurn]:
        tool = self.tools.get(call.name)
        args_dict, parse_error = parse_tool_arguments(call.arguments)
        args: Any = None
        validation_error: str | None = None
        label = call.name
        if tool is not None and args_dict is not None:
            try:
                args = tool.Input.model_validate(args_dict)
                label = tool.describe(args, self.ctx)
            except ValidationError as e:
                validation_error = format_validation_error(e)
        yield ev.ToolUse(
            id=call.id,
            name=call.name,
            input=args_dict if args_dict is not None else {"_raw": call.arguments},
            label=label,
        )

        def finish(res: ToolResult) -> ev.ToolResult:
            content = truncate_middle(res.content, self.ctx.max_output_chars)
            self.messages.append(Message.tool_result(call.id, call.name, content))
            return ev.ToolResult(
                id=call.id,
                name=call.name,
                content=content,
                is_error=res.is_error,
                summary=res.summary,
            )

        if tool is None:
            yield finish(
                ToolResult(
                    f"Unknown tool {call.name!r}. Available tools: {', '.join(self.tools)}.",
                    is_error=True,
                )
            )
            return
        if parse_error or args_dict is None:
            yield finish(
                ToolResult(
                    f"Invalid tool call: {parse_error}. Retry with valid JSON.", is_error=True
                )
            )
            return
        if validation_error or args is None:
            yield finish(
                ToolResult(f"Invalid arguments for {tool.name}: {validation_error}", is_error=True)
            )
            return
        if repeated:
            yield finish(
                ToolResult(
                    f"You have called {tool.name} with identical arguments {MAX_IDENTICAL_CALLS} times "
                    "in a row. Stop repeating it and try a different approach.",
                    is_error=True,
                )
            )
            return

        if tool.name == "Bash" and self.profile.steer_bash_file_work:
            command = str(args.command)
            hint = file_work_redirect(command)
            if hint and command != self._redirected:
                self._redirected = command
                yield finish(
                    ToolResult(
                        f"{hint} If the shell is really needed here, say why and send the "
                        "same command again.",
                        is_error=True,
                        summary="use the file tool instead",
                    )
                )
                return
            self._redirected = None

        check = self.policy.check(tool, args, self.ctx)
        if self.hooks is not None and check.decision != Decision.DENY:
            outcome = await self._hook(
                "PreToolUse",
                {"tool_name": tool.name, "tool_input": args.model_dump()},
                match=tool.name,
            )
            for w in outcome.warnings:
                yield ev.Warning(message=w)
            if outcome.block:
                reason = f"blocked by a PreToolUse hook: {outcome.reason}"
                yield ev.PermissionDenied(id=call.id, name=tool.name, reason=reason)
                yield finish(ToolResult(f"Not run: {reason}", is_error=True))
                return
            if (
                outcome.permission == "allow"
                and check.decision == Decision.ASK
                and not check.high_risk
            ):
                check = PermissionCheck(Decision.ALLOW, "allowed by a PreToolUse hook")
            elif outcome.permission == "ask" and check.decision == Decision.ALLOW:
                check = PermissionCheck(
                    Decision.ASK, outcome.reason or "a PreToolUse hook asks to confirm"
                )
        if check.decision == Decision.DENY:
            yield ev.PermissionDenied(id=call.id, name=tool.name, reason=check.reason)
            yield finish(ToolResult(f"Permission denied: {check.reason}", is_error=True))
            return
        if check.decision == Decision.ASK:
            rule = suggest_rule(tool, args, self.ctx)
            if self.ask is None:
                if check.high_risk:
                    reason = (
                        f"{label} is a high-risk command ({check.reason}) and needs a person's "
                        "approval each time; it cannot run in non-interactive mode."
                    )
                else:
                    reason = (
                        f"{label} needs approval, which is not available in non-interactive mode. "
                        f'Allow it with --allowedTools "{rule}" or a less strict --permission-mode.'
                    )
                yield ev.PermissionDenied(id=call.id, name=tool.name, reason=reason)
                yield finish(ToolResult(f"Permission denied: {reason}", is_error=True))
                return
            self._notify(f"cmcoder needs your permission to use {label}")
            answer = await self.ask(
                PermissionRequest(
                    call_id=call.id,
                    tool_name=tool.name,
                    label=label,
                    input=args.model_dump(),
                    suggested_rule=rule,
                    reason=check.reason,
                    can_remember=not check.high_risk and not self.policy.allow_rules_locked,
                    change=tool.proposed_change(args, self.ctx),
                )
            )
            if not answer.allow:
                feedback = (answer.feedback or "").strip()
                yield ev.PermissionDenied(id=call.id, name=tool.name, reason="denied by user")
                msg = "The user denied this action."
                if feedback:
                    msg += f" Their feedback: {feedback}"
                else:
                    msg += " Stop and wait for the user's instructions."
                yield finish(ToolResult(msg, is_error=True))
                if not feedback:
                    yield _StopTurn()
                return
            if answer.remember and not check.high_risk and not self.policy.allow_rules_locked:
                self.policy.add_allow(rule)
                if self.on_rule_saved:
                    self.on_rule_saved(rule)

        if tool.name in FILE_EDIT_TOOLS:
            target = tool.permission_target(args, self.ctx)
            if isinstance(target, Path):
                try:
                    self.checkpoints.capture(self.turn, target)  # for /rewind
                except OSError:
                    pass
        try:
            res = await tool.run(args, self.ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a tool bug must not kill the session
            res = ToolResult(f"{tool.name} failed: {type(e).__name__}: {e}", is_error=True)
        if self.hooks is not None:
            outcome = await self._hook(
                "PostToolUse",
                {
                    "tool_name": tool.name,
                    "tool_input": args.model_dump(),
                    "tool_response": {"content": res.content, "is_error": res.is_error},
                },
                match=tool.name,
            )
            for w in outcome.warnings:
                yield ev.Warning(message=w)
            notes = (
                [f"A PostToolUse hook objected: {outcome.reason}"] if outcome.block else []
            ) + outcome.context
            if notes:
                res = ToolResult(res.content + "\n\n" + "\n".join(notes), res.is_error, res.summary)
        yield finish(res)

    def _repair_after_interrupt(self) -> None:
        """Keep the transcript valid: every tool call needs a result."""
        answered = {m.tool_call_id for m in self.messages if m.role == "tool"}
        for m in reversed(self.messages):
            if m.role == "assistant":
                for call in m.tool_calls:
                    if call.id not in answered:
                        self.messages.append(
                            Message.tool_result(call.id, call.name, "Interrupted by the user.")
                        )
                break
        self.messages.append(Message.user("[Request interrupted by the user]"))


class _StopTurn:
    pass
