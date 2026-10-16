"""cmcoder's behaviour as LangChain middleware for `create_agent`.

turn.py: `TurnStart` (the user's hooks and code context, once a turn) and
`TurnLimits` (maxTurns, a subagent's report, the reminders, before each model
call). model_call.py: `ModelCall` (the request, prompted tool calls, errors,
usage and the reply's events). replies.py: `InvalidCalls`, `Replies` (empty
replies) and `Finish` (the Stop hook, the critic). tool_calls.py: `ToolCalls`
(the order of calls, permissions, hooks, checkpoints).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.middleware import AgentMiddleware

from .model_call import ModelCall
from .replies import Finish, InvalidCalls, Replies
from .tool_calls import ToolCalls
from .turn import TurnLimits, TurnStart

if TYPE_CHECKING:
    from ..agent import GraphAgent


def cmcoder_middleware(agent: GraphAgent) -> list[AgentMiddleware]:
    """In `create_agent`'s order: `after_model` hooks run from last to first,
    so `InvalidCalls`, then `Replies`, then `Finish`; `ModelCall` and
    `ToolCalls` wrap the model and each tool."""
    return [
        TurnStart(agent),
        TurnLimits(agent),
        ModelCall(agent),
        Finish(agent),
        Replies(agent),
        InvalidCalls(agent),
        ToolCalls(agent),
    ]
