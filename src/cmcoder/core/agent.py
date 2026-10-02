"""The agent loop: model -> tool calls -> results -> repeat."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

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
from ..providers.openai_compat import ContextTooLong, ProviderError
from ..providers.profiles import ModelProfile
from ..tools.base import Tool, ToolContext, ToolResult, truncate_middle
from .compaction import CompactionError, Summarizer, compact
from .context import WARN_RATIO, ContextBudget
from .permissions import Decision, PermissionPolicy, suggest_rule
from .steer import file_work_redirect

MAX_IDENTICAL_CALLS = 3


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
        auto_compact: bool = True,
        compact_threshold: float = 0.8,
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
        self.session_id = str(uuid.uuid4())
        self.system_prompt = system_prompt
        self.messages: list[Message] = [Message.system(system_prompt)]
        self.usage = Usage()
        self._budget: ContextBudget | None = None
        # The last Bash command redirected to a file tool; sending it again runs it.
        self._redirected: str | None = None
        # The small/fast model for summaries; the main model is the fallback.
        self.summarizer = summarizer
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

    def clear(self) -> None:
        self.messages = [Message.system(self.system_prompt)]

    def tool_specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.tools.values()]

    async def close(self) -> None:
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
        yield ev.Compacted(
            trigger=trigger,  # type: ignore[arg-type]
            summarized_messages=res.summarized,
            tokens_before=before,
            tokens_after=budget.estimate(self.messages),
            model=res.model,
        )

    # ------------------------------------------------------------------

    async def run(self, prompt: str) -> AsyncIterator[ev.Event]:
        """Run one user turn. Yields protocol events, ending with a Result."""
        started = time.monotonic()
        self.messages.append(Message.user(prompt))
        turn_usage = Usage()
        last_text = ""
        steps = 0
        recent_calls: list[str] = []
        warned_context = False
        overflow_retried = False
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
                try:
                    async for sev in self.provider.stream_chat(
                        self.model,
                        self.messages,
                        self.tool_specs(),
                        self.profile,
                        max_tokens=budget.max_tokens(self.messages),
                    ):
                        if isinstance(sev, TextDelta):
                            yield ev.AssistantDelta(text=sev.text)
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
                    yield result("error", str(e), is_error=True)
                    return
                except ProviderError as e:
                    yield ev.Error(kind=e.kind, message=str(e).split("\n  hint:")[0], hint=e.hint)
                    # Drop the user message if the model never answered, so retrying works cleanly.
                    if self.messages[-1].role == "user" and steps == 1:
                        self.messages.pop()
                    yield result("error", str(e), is_error=True)
                    return
                if done is None:
                    yield ev.Error(kind="provider", message="Stream ended without a final message.")
                    yield result("error", "stream ended unexpectedly", is_error=True)
                    return

                overflow_retried = False  # one retry per model call, not per turn
                msg = done.message
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
        except asyncio.CancelledError:
            self._repair_after_interrupt()
            raise

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
            answer = await self.ask(
                PermissionRequest(
                    call_id=call.id,
                    tool_name=tool.name,
                    label=label,
                    input=args.model_dump(),
                    suggested_rule=rule,
                    reason=check.reason,
                    can_remember=not check.high_risk,
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
            if answer.remember and not check.high_risk:
                self.policy.add_allow(rule)
                if self.on_rule_saved:
                    self.on_rule_saved(rule)

        try:
            res = await tool.run(args, self.ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a tool bug must not kill the session
            res = ToolResult(f"{tool.name} failed: {type(e).__name__}: {e}", is_error=True)
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
