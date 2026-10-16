"""Around each model call (`wrap_model_call`): what the classic engine does
around its provider's stream.

- The request: the system prompt (with CMCODER.md and skills: the
  conversation's first message), the tools (none when a subagent writes its
  report), the model as it is now (after `/model`, or a profile change) and
  `max_tokens` sized to what is left of the context window.
- A server that rejects the `tools` parameter: the tools are described in the
  prompt instead (prompted tool calls) for the rest of the session, and the
  call is made again.
- Any other error from the gateway becomes cmcoder's own (llm/errors.py),
  which ends the turn with the same message as the classic engine.
- The reply: its usage (with LiteLLM's cost), its `AssistantMessage` (held
  for the critic when it's the final answer), the context warning; a
  subagent's report keeps no tool calls.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage

from ...core.context import WARN_RATIO
from ...llm.chat_openai import REASONING
from ...llm.errors import provider_error
from ...llm.usage import counting_cost, usage_of
from ...protocol import events as ev
from ...providers.openai_compat import ProviderError, no_tool_support
from ..context import EngineContext
from ..events import Reply, emit
from ..state import CmcoderState

if TYPE_CHECKING:
    from ..agent import GraphAgent

PROMPTED_SWITCH = (
    "The server rejected native tool calling for {model}; switching to prompted tool calls "
    'for this session. Set "toolCalling": "prompted" in modelProfiles to skip this, or enable '
    "tool calling on the backend (vLLM --enable-auto-tool-choice --tool-call-parser hermes)."
)


def call_list(ai: AIMessage) -> list[dict[str, Any]]:
    """The reply's tool calls as the protocol shows them (arguments as JSON text)."""
    return [
        {"id": c["id"], "name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}
        for c in ai.tool_calls
    ] + [
        {"id": c.get("id"), "name": c.get("name"), "arguments": c.get("args") or ""}
        for c in ai.invalid_tool_calls
    ]


class ModelCall(AgentMiddleware[CmcoderState, EngineContext]):
    state_schema = CmcoderState

    def __init__(self, agent: GraphAgent) -> None:
        super().__init__()
        self.agent = agent

    async def awrap_model_call(
        self,
        request: ModelRequest[EngineContext],
        handler: Callable[[ModelRequest[EngineContext]], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        agent = self.agent
        turn = agent.current
        agent.save_session()  # before every model call, so a crash loses little
        wrap_up = bool(request.state.get("wrap_up"))
        budget = agent.budget()
        request_chars = budget.request_chars(agent.messages)
        first = agent.messages[0] if agent.messages else None
        system = first.content if first is not None and first.role == "system" else agent.system_prompt
        req = request.override(
            model=agent.chat(),
            system_message=SystemMessage(system),
            tools=[] if wrap_up else request.tools,
            model_settings={
                **request.model_settings,
                "max_completion_tokens": budget.max_tokens(agent.messages),
            },
        )
        while True:
            try:
                with counting_cost() as cost:
                    response = await handler(req)
            except Exception as e:
                err = provider_error(e, agent.provider.base_url)
                if err is None:
                    raise
                if (
                    no_tool_support(err)
                    and not turn.prompted_retried
                    and agent.profile.tool_calling != "prompted"
                ):
                    turn.prompted_retried = True
                    agent.profile = agent.profile.model_copy(update={"tool_calling": "prompted"})
                    emit(ev.Warning(message=PROMPTED_SWITCH.format(model=agent.model)))
                    req = req.override(model=agent.chat())
                    continue
                raise self._with_key_hint(err) from e
            break

        *before, ai = response.result
        assert isinstance(ai, AIMessage)
        if wrap_up and (ai.tool_calls or ai.invalid_tool_calls):
            ai = ai.model_copy(update={"tool_calls": [], "invalid_tool_calls": []})
        sent = [req.system_message, *req.messages] if req.system_message else list(req.messages)
        usage = usage_of(ai, sent, cost.get("cost"))
        agent.usage.add(usage)
        turn.usage.add(usage)
        calls = call_list(ai)
        event = ev.AssistantMessage(
            text=ai.text,
            reasoning=str(ai.additional_kwargs.get(REASONING, "")),
            tool_calls=calls,
            model=ai.response_metadata.get("model_name"),
        )
        # The final answer under critique waits for its review (middleware/finish.py).
        turn.deferred = agent.holds_answers and not calls and bool(ai.text.strip())
        emit(Reply(event, deferred=turn.deferred))
        emit(
            ev.UsageUpdate(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                estimated=usage.estimated,
                cost=usage.cost,
                context_window=agent.profile.context_window,
            )
        )
        if not usage.estimated:
            budget.calibrate(request_chars, usage.prompt_tokens)
        if not turn.warned_context and budget.usage_ratio(usage.prompt_tokens) > WARN_RATIO:
            turn.warned_context = True
            emit(
                ev.Warning(
                    message=f"Context is {usage.prompt_tokens}/{budget.window} tokens. Older tool "
                    "output is dropped automatically as it fills; /clear starts fresh."
                )
            )
        if ai.text:
            turn.last_text = ai.text
        return ModelResponse(result=[*before, ai], structured_response=response.structured_response)

    def _with_key_hint(self, err: ProviderError) -> ProviderError:
        """For a refused key, which key was sent (masked), as the classic provider says."""
        hint = getattr(self.agent.provider, "_with_key_hint", None)
        return hint(err) if hint is not None else err
