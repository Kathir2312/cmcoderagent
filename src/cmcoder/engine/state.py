"""What the engine keeps during a turn.

`CmcoderState` is the graph's state (`create_agent` merges it with its own
`AgentState`: `messages` and the private `jump_to`). It holds what decides
where the graph goes next. `Turn` holds the rest of a turn's bookkeeping, as
the classic engine's local variables did: the usage, the last answer, the
calls seen, the critic's rounds. (M5 moves what a resumed run needs into the
state, when the checkpointer keeps it.)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain.agents.middleware import AgentState

from ..providers.messages import Usage


class Outcome(TypedDict):
    """How a turn ended when it didn't end with an answer."""

    subtype: Literal["max_turns", "interrupted", "blocked"]
    text: str


def _first(old: Outcome | None, new: Outcome | None) -> Outcome | None:
    """The first way a turn ended wins (two tool calls may end it in one step)."""
    return old or new


class CmcoderState(AgentState):
    steps: NotRequired[int]
    """Model calls in this turn (the classic engine's `steps`; `maxTurns` caps it)."""
    wrap_up: NotRequired[bool]
    """A subagent's last call, for its report: no tools offered, none run."""
    empty_retry: NotRequired[bool]
    """The model was asked once more after an empty reply."""
    outcome: NotRequired[Annotated[Outcome | None, _first]]
    """Set to end the turn without an answer (the next step goes to the end)."""


@dataclass
class Turn:
    """One user turn's bookkeeping (see the module's docstring)."""

    prompt: str
    user_msg_id: str  # the user's message in the graph (hooks and code context change it)
    start: int  # its index in agent.messages (the critic reviews what came after)
    started: float = field(default_factory=time.monotonic)
    usage: Usage = field(default_factory=Usage)
    last_text: str = ""
    recent_calls: list[str] = field(default_factory=list)  # name:arguments, for repeats
    todo_reminded: bool = False
    stop_blocks: int = 0  # times a Stop hook sent the model back to work
    warned_context: bool = False
    prompted_retried: bool = False
    deferred: bool = False  # the last reply is an answer waiting for the critic
    review_round: int = 0
    review_issues: list[Any] = field(default_factory=list)
    review_info: dict[str, Any] | None = None
    stopped: bool = False  # the user denied a tool call: the rest are skipped
