"""The turn around the model calls.

`TurnStart` (`before_agent`, once a turn): the user's hooks (SessionStart,
UserPromptSubmit) and the code that code search finds, added to the user's
message; a hook may refuse the prompt, which ends the turn.

`TurnLimits` (`before_model`, before every model call): the classic engine's
loop head. It counts model calls and stops at `maxTurns`; a subagent gets one
more call for its report (`wrap_up`: no tools). It adds the reminders that go
after a tool step (use TodoWrite; a subagent's calls left), and ends the turn
when something set an outcome (`jump_to: "end"`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from ...core.agent import (
    STEPS_LEFT_REMINDER,
    STOP_REQUEST,
    SUBAGENT_STEPS_WARNING,
    TODO_REMINDER,
    WRAP_UP_REQUEST,
)
from ...protocol import events as ev
from ..context import EngineContext
from ..events import emit
from ..state import CmcoderState

if TYPE_CHECKING:
    from ..agent import GraphAgent


class TurnStart(AgentMiddleware[CmcoderState, EngineContext]):
    state_schema = CmcoderState

    def __init__(self, agent: GraphAgent) -> None:
        super().__init__()
        self.agent = agent

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self, state: CmcoderState, runtime: Runtime[EngineContext]
    ) -> dict[str, Any] | None:
        agent = self.agent
        turn = agent.current
        user = agent.mirror.message_for(turn.user_msg_id)
        assert user is not None
        before = user.content
        if agent.hooks is not None and not agent.is_subagent:
            notes: list[str] = []
            if not agent._session_hooks_done:
                agent._session_hooks_done = True
                source = "resume" if len(agent.messages) > 2 else "startup"
                outcome = await agent._hook("SessionStart", {"source": source})
                for w in outcome.warnings:
                    emit(ev.Warning(message=w))
                notes += outcome.context
            outcome = await agent._hook("UserPromptSubmit", {"prompt": turn.prompt})
            for w in outcome.warnings:
                emit(ev.Warning(message=w))
            if outcome.block:
                emit(ev.Warning(message=f"A UserPromptSubmit hook blocked this prompt: {outcome.reason}"))
                return {
                    "outcome": {"subtype": "blocked", "text": f"Blocked by a hook: {outcome.reason}"},
                    "jump_to": "end",
                }
            notes += outcome.context
            if notes:
                hook_note = "\n".join(notes)
                user.content = (
                    f"<system-reminder>\nFrom the user's hooks:\n{hook_note}\n</system-reminder>"
                    f"\n\n{user.content}"
                )
        if (added := await agent._auto_context(turn.prompt)) is not None:
            text, event = added
            user.content = f"{text}\n\n{user.content}"
            emit(event)
        elif agent._context_failed is True:
            agent._context_failed = None  # said once
            emit(
                ev.Warning(
                    message="Code search isn't answering, so no code context was added. "
                    "`cmcoder doctor` checks it."
                )
            )
        if user.content == before:
            return None
        return {"messages": [agent.mirror.replace(user, turn.user_msg_id, vision=agent.sees_images)]}


class TurnLimits(AgentMiddleware[CmcoderState, EngineContext]):
    state_schema = CmcoderState

    def __init__(self, agent: GraphAgent) -> None:
        super().__init__()
        self.agent = agent

    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self, state: CmcoderState, runtime: Runtime[EngineContext]
    ) -> dict[str, Any] | None:
        agent = self.agent
        turn = agent.current
        if state.get("outcome"):
            return {"jump_to": "end"}
        messages = state["messages"]
        steps = state.get("steps", 0)
        wrap_up = state.get("wrap_up", False)
        update: dict[str, Any] = {}
        last = messages[-1]
        if isinstance(last, ToolMessage):  # after a tool step
            extra = ""
            if not turn.todo_reminded and self._needs_todo_reminder(len(turn.recent_calls)):
                turn.todo_reminded = True
                extra += TODO_REMINDER
            left = agent.max_turns - steps
            if agent.is_subagent and left == SUBAGENT_STEPS_WARNING < agent.max_turns // 2:
                extra += STEPS_LEFT_REMINDER.format(left=left)
            if extra:
                last = agent.mirror.appended(last, extra, vision=agent.sees_images)
                update["messages"] = [last]
        stop = agent._stop_requested
        if (steps >= agent.max_turns or stop) and (
            agent.is_subagent and not wrap_up and not isinstance(last, AIMessage)
        ):
            # A subagent at its limit, or stopped by the user: one more call,
            # without tools, for its report.
            request = STOP_REQUEST if stop else WRAP_UP_REQUEST.format(limit=steps)
            if isinstance(last, HumanMessage):  # added to the user's message, as a reminder
                asked = agent.mirror.appended(last, "\n\n" + request, vision=agent.sees_images)
            else:
                asked = HumanMessage(request)
            update["messages"] = [*update.get("messages", []), asked]
            update["wrap_up"] = True
            if not stop:
                emit(
                    ev.Warning(
                        message=f"Reached {agent.max_turns} model calls (max turns); "
                        "asking for its report."
                    )
                )
        elif steps >= agent.max_turns:
            emit(ev.Warning(message=f"Stopped after {agent.max_turns} model calls (max turns)."))
            return {"outcome": {"subtype": "max_turns", "text": turn.last_text}, "jump_to": "end"}
        update["steps"] = steps + 1
        return update

    def _needs_todo_reminder(self, calls_this_turn: int) -> bool:
        """Several tool calls this turn and no open todo list (as the classic engine)."""
        agent = self.agent
        if calls_this_turn < 3 or "TodoWrite" not in agent.tools:
            return False
        return not any(t.get("status") != "completed" for t in agent.ctx.todos)
