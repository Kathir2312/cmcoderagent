"""After each model call (`after_model`), in the classic engine's order.

`create_agent` runs the `after_model` hooks from the last middleware in its
list to the first, and a hook that jumps (`jump_to`) skips the rest; so these
are listed as `[Finish, Replies, InvalidCalls]` and run in the other order.

`InvalidCalls`: a tool call whose arguments aren't valid JSON (even after
cmcoder's repairs) gets an error result the model sees, and the model goes
again. `create_agent` would only answer it before the *next* model call, and a
reply with nothing but such calls would end the turn instead.

`Replies`: a reply cut at the output limit is reported; an empty reply (no
text, no tool call) is asked about once, and stays out of the history.

`Finish` (a reply without tool calls, i.e. the answer): the Stop hook, which
may send the model back to work, then the critic's review of the answer when
critique is on (core/critic.py), which may too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.runtime import Runtime

from ...core import critic
from ...core.agent import EMPTY_REPLY_RETRY, MAX_STOP_HOOK_BLOCKS
from ...protocol import events as ev
from ...providers.messages import parse_tool_arguments
from ..context import EngineContext
from ..convert import is_empty_reply
from ..events import Release, delivered, emit
from ..state import CmcoderState

if TYPE_CHECKING:
    from ..agent import GraphAgent


def last_reply(messages: list[AnyMessage]) -> AIMessage | None:
    last = messages[-1] if messages else None
    return last if isinstance(last, AIMessage) else None


class InvalidCalls(AgentMiddleware[CmcoderState, EngineContext]):
    state_schema = CmcoderState

    def __init__(self, agent: GraphAgent) -> None:
        super().__init__()
        self.agent = agent

    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self, state: CmcoderState, runtime: Runtime[EngineContext]
    ) -> dict[str, Any] | None:
        ai = last_reply(state["messages"])
        if ai is None or not ai.invalid_tool_calls:
            return None
        agent = self.agent
        results: list[ToolMessage] = []
        for bad in ai.invalid_tool_calls:
            call_id, name, raw = bad.get("id") or "", bad.get("name") or "", str(bad.get("args") or "")
            agent.current.recent_calls.append(f"{name}:{raw.strip()}")
            emit(ev.ToolUse(id=call_id, name=name, input={"_raw": raw}, label=name))
            if name not in agent.tools:
                text = f"Unknown tool {name!r}. Available tools: {', '.join(agent.tools)}."
            else:
                _, error = parse_tool_arguments(raw)
                text = f"Invalid tool call: {error}. Retry with valid JSON."
            emit(ev.ToolResult(id=call_id, name=name, content=text, is_error=True))
            results.append(ToolMessage(text, tool_call_id=call_id, name=name, status="error"))
        update: dict[str, Any] = {"messages": results}
        if not ai.tool_calls:  # nothing else to run: straight back to the model
            update["jump_to"] = "model"
        return update


class Replies(AgentMiddleware[CmcoderState, EngineContext]):
    state_schema = CmcoderState

    def __init__(self, agent: GraphAgent) -> None:
        super().__init__()
        self.agent = agent

    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self, state: CmcoderState, runtime: Runtime[EngineContext]
    ) -> dict[str, Any] | None:
        messages = state["messages"]
        ai = last_reply(messages)
        if ai is None:
            return None
        empty = is_empty_reply(ai)
        if ai.tool_calls or ai.invalid_tool_calls:
            return {"empty_retry": False} if state.get("empty_retry") else None
        if ai.response_metadata.get("finish_reason") == "length":
            emit(ev.Warning(message="The reply was cut off at the max output length."))
        if not empty:
            return {"empty_retry": False} if state.get("empty_retry") else None
        if not state.get("empty_retry") and not state.get("wrap_up"):
            emit(ev.Warning(message="The model returned an empty reply; asking again."))
            return {
                "messages": [HumanMessage(EMPTY_REPLY_RETRY)],
                "empty_retry": True,
                "jump_to": "model",
            }
        emit(ev.Warning(message="The model returned an empty reply."))
        asked = messages[-2] if len(messages) > 1 else None
        if isinstance(asked, HumanMessage) and asked.text == EMPTY_REPLY_RETRY and asked.id:
            return {"messages": [RemoveMessage(id=asked.id)]}  # it didn't help: as it was
        return None


class Finish(AgentMiddleware[CmcoderState, EngineContext]):
    state_schema = CmcoderState

    def __init__(self, agent: GraphAgent) -> None:
        super().__init__()
        self.agent = agent

    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self, state: CmcoderState, runtime: Runtime[EngineContext]
    ) -> dict[str, Any] | None:
        ai = last_reply(state["messages"])
        if ai is None or ai.tool_calls or ai.invalid_tool_calls:
            return None
        agent = self.agent
        turn = agent.current
        if agent.hooks is not None and turn.stop_blocks < MAX_STOP_HOOK_BLOCKS:
            outcome = await agent._hook(
                "SubagentStop" if agent.is_subagent else "Stop",
                {"stop_hook_active": turn.stop_blocks > 0},
            )
            for w in outcome.warnings:
                emit(ev.Warning(message=w))
            if outcome.block:
                # Not done yet, says a hook (e.g. "the tests fail"): continue.
                turn.stop_blocks += 1
                emit(ev.Warning(message=f"A Stop hook asked to continue: {outcome.reason}"))
                note = (
                    "<system-reminder>A Stop hook says the task isn't finished: "
                    f"{outcome.reason}</system-reminder>"
                )
                return {"messages": [HumanMessage(note)], "jump_to": "model"}
        if turn.deferred:
            return await self._review(ai)
        return None

    async def _review(self, ai: AIMessage) -> dict[str, Any] | None:
        """Critique: the critic reviews the answer before it's shown."""
        agent = self.agent
        turn = agent.current
        turn.review_round += 1
        reviewed: critic.Review | None = None
        async for item in critic.review(
            agent,
            turn.prompt,
            ai.text,
            turn.start,
            turn.review_round,
            agent.critique_rounds,
            turn.review_issues,
        ):
            if isinstance(item, critic.Review):
                reviewed = item
            else:  # the critic's own tool calls, as they happen
                emit(item)
                await delivered()
        assert reviewed is not None
        if reviewed.verdict == "fail" and turn.review_round < agent.critique_rounds:
            emit(reviewed.event(turn.review_round, agent.critique_rounds, final=False))
            turn.review_issues = reviewed.issues
            turn.deferred = False
            ask = critic.fix_request(reviewed, turn.review_round, agent.critique_rounds)
            return {"messages": [HumanMessage(ask)], "jump_to": "model"}
        turn.review_info = {
            "verdict": reviewed.verdict,
            "rounds": turn.review_round,
            "summary": reviewed.summary,
            "issues": [i.model_dump() for i in reviewed.issues],
        }
        emit(Release())
        emit(reviewed.event(turn.review_round, agent.critique_rounds, final=True))
        return None
