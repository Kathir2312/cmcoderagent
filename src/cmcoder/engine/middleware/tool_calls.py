"""Around each tool call (`wrap_tool_call`): what the classic engine does in
`Agent._run_call`, before and after the tool runs.

In order: the tool use shown (with the tool's own label); an unknown tool or
invalid arguments answered with an error; the same call a third time in a
row refused; a Bash command that is plainly file work sent to the file tool
(Qwen's habit, core/steer.py); the permission rules and the PreToolUse hook;
the user asked when the rules say so (after the tool use has reached the
front end); a checkpoint of a file before it is edited (/rewind); the tool;
the PostToolUse hook's notes; the result shown.

`create_agent` runs every tool call of a reply at the same time (one `Send`
each). The classic engine runs them one after another in the reply's order,
except Task calls next to each other, which run together (subagents in
parallel); so does this middleware, by making each call wait for the earlier
ones. A call the user denies, without saying what to do instead, stops the
turn: the calls after it are skipped, Task calls running alongside are
stopped, and the turn ends (`outcome`).

Asking here, inside the tool call, is M4's way; M5 asks with LangGraph's
`interrupt()`, so a turn can wait for an answer across a restart.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import ValidationError

from ...core.agent import (
    MAX_IDENTICAL_CALLS,
    STOPPED_SUBAGENT,
    STOPPED_WITH_OTHERS,
    PermissionAnswer,
    PermissionRequest,
)
from ...core.permissions import FILE_EDIT_TOOLS, Decision, PermissionCheck, suggest_rules
from ...core.steer import file_work_redirect
from ...protocol import events as ev
from ...tools.base import ToolResult, truncate_middle
from ..context import EngineContext
from ..events import delivered, emit
from ..state import CmcoderState
from ..tools import format_validation_error

if TYPE_CHECKING:
    from ..agent import GraphAgent

STOPPED_TURN = "Skipped: the user stopped this turn."
DENIED = "Stopped: the user denied a tool call."


@dataclass
class _Reply:
    """One reply's tool calls: which must finish before each may start."""

    after: dict[str, list[str]]
    done: dict[str, asyncio.Event] = field(default_factory=dict)
    running: dict[str, asyncio.Task[Any]] = field(default_factory=dict)  # Task calls
    stopped: set[str] = field(default_factory=set)  # stopped because another was denied


def call_groups(calls: list[dict[str, Any]], parallel_tasks: bool) -> list[list[str]]:
    """The calls in order; Task calls next to each other form one group that
    runs together (as the classic engine's `_call_groups`)."""
    groups: list[list[dict[str, Any]]] = []
    for call in calls:
        if parallel_tasks and call["name"] == "Task" and groups and groups[-1][-1]["name"] == "Task":
            groups[-1].append(call)
        else:
            groups.append([call])
    return [[c["id"] for c in g] for g in groups]


class ToolCalls(AgentMiddleware[CmcoderState, EngineContext]):
    state_schema = CmcoderState

    def __init__(self, agent: GraphAgent) -> None:
        super().__init__()
        self.agent = agent
        self._reply_id: str | None = None
        self._reply: _Reply | None = None

    # -- the order of a reply's calls -------------------------------------

    def _schedule(self, ai: AIMessage) -> _Reply:
        if self._reply is None or self._reply_id != ai.id:
            groups = call_groups(ai.tool_calls, self.agent.max_parallel_subagents > 1)
            after: dict[str, list[str]] = {}
            earlier: list[str] = []
            for group in groups:
                for call_id in group:
                    after[call_id] = list(earlier)
                earlier += group
            self._reply_id, self._reply = ai.id, _Reply(after)
            self._reply.done = {c["id"]: asyncio.Event() for c in ai.tool_calls}
        return self._reply

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        call_id = call["id"] or ""
        ai = next(
            (m for m in reversed(request.state["messages"]) if isinstance(m, AIMessage)), None
        )
        reply = self._schedule(ai) if ai is not None else _Reply({call_id: []})
        for earlier in reply.after.get(call_id, []):
            if (event := reply.done.get(earlier)) is not None:
                await event.wait()
        task = asyncio.current_task()
        if call["name"] == "Task" and task is not None:
            reply.running[call_id] = task
        try:
            return await self._call(request, handler, reply)
        except asyncio.CancelledError:
            if call_id not in reply.stopped or task is None:
                raise
            task.uncancel()  # stopped because the user denied another subagent's call
            return self._finish(call, ToolResult(STOPPED_WITH_OTHERS, is_error=True))
        finally:
            reply.running.pop(call_id, None)
            if (event := reply.done.get(call_id)) is not None:
                event.set()

    def _stop_others(self, reply: _Reply, call_id: str) -> None:
        """The user denied a subagent's tool call: the Task calls running
        alongside stop too (their results say so)."""
        for other, task in reply.running.items():
            if other != call_id:
                reply.stopped.add(other)
                task.cancel()

    # -- one call -----------------------------------------------------------

    async def _call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
        reply: _Reply,
    ) -> ToolMessage | Command[Any]:
        agent = self.agent
        turn = agent.current
        call = request.tool_call
        call_id, name, args_dict = call["id"] or "", call["name"], call["args"]
        if turn.stopped or agent._stop_requested:  # no events, as in the classic engine
            text = STOPPED_SUBAGENT if agent._stop_requested else STOPPED_TURN
            return ToolMessage(text, tool_call_id=call_id, name=name, status="error")
        if name == "TodoWrite":
            turn.todo_reminded = True
        signature = f"{name}:{json.dumps(args_dict, ensure_ascii=False)}"
        turn.recent_calls.append(signature)
        repeated = len(turn.recent_calls) >= MAX_IDENTICAL_CALLS and all(
            s == signature for s in turn.recent_calls[-MAX_IDENTICAL_CALLS:]
        )
        tool = agent.tools.get(name)
        args: Any = None
        validation_error: str | None = None
        label = name
        if tool is not None:
            try:
                args = tool.Input.model_validate(args_dict)
                label = tool.describe(args, agent.ctx)
            except ValidationError as e:
                validation_error = format_validation_error(e)
        emit(ev.ToolUse(id=call_id, name=name, input=args_dict, label=label))

        if tool is None:
            known = ", ".join(agent.tools)
            return self._finish(
                call, ToolResult(f"Unknown tool {name!r}. Available tools: {known}.", is_error=True)
            )
        if validation_error or args is None:
            return self._finish(
                call,
                ToolResult(f"Invalid arguments for {tool.name}: {validation_error}", is_error=True),
            )
        if repeated:
            return self._finish(
                call,
                ToolResult(
                    f"You have called {tool.name} with identical arguments {MAX_IDENTICAL_CALLS} "
                    "times in a row. Stop repeating it and try a different approach.",
                    is_error=True,
                ),
            )
        if tool.name == "Bash" and agent.profile.steer_bash_file_work:
            command = str(args.command)
            hint = file_work_redirect(command)
            if hint and command != agent._redirected:
                agent._redirected = command
                return self._finish(
                    call,
                    ToolResult(
                        f"{hint} If the shell is really needed here, say why and send the "
                        "same command again.",
                        is_error=True,
                        summary="use the file tool instead",
                    ),
                )
            agent._redirected = None

        refused = await self._permission(call, tool, args, label, reply)
        if refused is not None:
            return refused

        if tool.name in FILE_EDIT_TOOLS:
            target = tool.permission_target(args, agent.ctx)
            if isinstance(target, Path):
                try:
                    agent.checkpoints.capture(agent.turn, target)  # for /rewind
                except OSError:
                    pass
        out = await handler(request)
        message = out if isinstance(out, ToolMessage) else None
        if message is None:  # a Command from a tool: passed on as it is
            return out
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        res = ToolResult(
            message.text, is_error=message.status == "error", summary=artifact.get("summary")
        )
        if agent.hooks is not None:
            outcome = await agent._hook(
                "PostToolUse",
                {
                    "tool_name": tool.name,
                    "tool_input": args.model_dump(),
                    "tool_response": {"content": res.content, "is_error": res.is_error},
                },
                match=tool.name,
            )
            for w in outcome.warnings:
                emit(ev.Warning(message=w))
            notes = (
                [f"A PostToolUse hook objected: {outcome.reason}"] if outcome.block else []
            ) + outcome.context
            if notes:
                res = ToolResult(res.content + "\n\n" + "\n".join(notes), res.is_error, res.summary)
        result = self._finish(call, res, artifact)
        if artifact.get("stop_turn"):  # the user denied one of the subagent's calls
            turn.stopped = True
            self._stop_others(reply, call_id)
            return Command(
                update={"messages": [result], "outcome": {"subtype": "interrupted", "text": DENIED}}
            )
        return result

    async def _permission(
        self, call: Any, tool: Any, args: Any, label: str, reply: _Reply
    ) -> ToolMessage | Command[Any] | None:
        """The rules, the PreToolUse hook and the user; None when the call may run."""
        agent = self.agent
        turn = agent.current
        call_id = call["id"] or ""
        check = agent.policy.check(tool, args, agent.ctx)
        hook_asks = False  # a PreToolUse hook wants the user asked, whatever the rules say
        if agent.hooks is not None and check.decision != Decision.DENY:
            outcome = await agent._hook(
                "PreToolUse",
                {"tool_name": tool.name, "tool_input": args.model_dump()},
                match=tool.name,
            )
            for w in outcome.warnings:
                emit(ev.Warning(message=w))
            if outcome.block:
                reason = f"blocked by a PreToolUse hook: {outcome.reason}"
                emit(ev.PermissionDenied(id=call_id, name=tool.name, reason=reason))
                return self._finish(call, ToolResult(f"Not run: {reason}", is_error=True))
            if outcome.permission == "allow" and check.decision == Decision.ASK and not check.high_risk:
                check = PermissionCheck(Decision.ALLOW, "allowed by a PreToolUse hook")
            elif outcome.permission == "ask" and check.decision == Decision.ALLOW:
                hook_asks = True
                check = PermissionCheck(Decision.ASK, outcome.reason or "a PreToolUse hook asks to confirm")
        if check.decision == Decision.DENY:
            emit(ev.PermissionDenied(id=call_id, name=tool.name, reason=check.reason))
            return self._finish(call, ToolResult(f"Permission denied: {check.reason}", is_error=True))
        if check.decision != Decision.ASK:
            return None
        rules = suggest_rules(tool, args, agent.ctx)
        rule = ", ".join(rules)  # what the user sees
        if agent.ask is None:
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
            emit(ev.PermissionDenied(id=call_id, name=tool.name, reason=reason))
            return self._finish(call, ToolResult(f"Permission denied: {reason}", is_error=True))
        # Parallel subagents ask one at a time; an answer meanwhile ("always
        # allow") may already cover this call.
        async with agent._ask_lock:
            if agent._stop_requested:  # stopped while it waited to ask
                return self._finish(call, ToolResult(STOPPED_SUBAGENT, is_error=True))
            if not hook_asks and agent.policy.check(tool, args, agent.ctx).decision == Decision.ALLOW:
                return None
            agent._notify(f"cmcoder needs your permission to use {label}")
            await delivered()  # the tool use reaches the front end before the question
            answer: PermissionAnswer = await agent.ask(
                PermissionRequest(
                    call_id=call_id,
                    tool_name=tool.name,
                    label=label,
                    input=args.model_dump(),
                    suggested_rule=rule,
                    reason=check.reason,
                    can_remember=not check.high_risk and not agent.policy.allow_rules_locked,
                    change=tool.proposed_change(args, agent.ctx),
                )
            )
            if not answer.allow:
                feedback = (answer.feedback or "").strip()
                emit(ev.PermissionDenied(id=call_id, name=tool.name, reason="denied by user"))
                text = "The user denied this action."
                text += f" Their feedback: {feedback}" if feedback else " Stop and wait for the user's instructions."
                result = self._finish(call, ToolResult(text, is_error=True))
                if feedback:
                    return result
                turn.stopped = True
                self._stop_others(reply, call_id)
                return Command(
                    update={"messages": [result], "outcome": {"subtype": "interrupted", "text": DENIED}}
                )
            if answer.remember and not check.high_risk and not agent.policy.allow_rules_locked:
                for r in rules:
                    agent.policy.add_allow(r)
                    if agent.on_rule_saved:
                        agent.on_rule_saved(r)
        return None

    def _finish(self, call: Any, res: ToolResult, artifact: dict[str, Any] | None = None) -> ToolMessage:
        """The result: shown to the user, and the ToolMessage the model gets."""
        content = truncate_middle(res.content, self.agent.ctx.max_output_chars)
        emit(
            ev.ToolResult(
                id=call["id"] or "",
                name=call["name"],
                content=content,
                is_error=res.is_error,
                summary=res.summary,
            )
        )
        return ToolMessage(
            content,
            tool_call_id=call["id"] or "",
            name=call["name"],
            status="error" if res.is_error else "success",
            artifact={**(artifact or {}), "summary": res.summary},
        )
