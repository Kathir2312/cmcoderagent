"""The event bridge: a graph run's streams as cmcoder's protocol events
(protocol/events.py), the events the front ends and IDE plugins already show.

A run streams in three modes at once (`graph.astream(stream_mode=[...])`):

- `"messages"`: the model's tokens as they arrive, which become
  `AssistantDelta` and `ReasoningDelta`. A tool call the model writes as
  text (`<tool_call>…`) is held back, as the classic engine does (Holdback),
  and so is the whole answer while the critic reviews it.
- `"custom"`: what middleware and tools send with `emit()`: tool use and
  results, warnings, permission denials, a subagent's events. Middleware
  also sends the bridge two signals: `Reply` (a model call is done: show
  its text and its `AssistantMessage`) and `Release` (the reviewed answer
  may be shown).
- `"values"`: the whole state after each step, which keeps the classic
  message list (`agent.messages`: sessions, /rewind, /compact) in step.

Before asking the user something (a permission), a tool waits with
`delivered()` until everything it emitted has reached the front end, so the
question never overtakes the tool use it is about: LangGraph runs a step's
tasks while the stream is read, not in turn with it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessageChunk
from langgraph.config import get_stream_writer

from ..llm.chat_openai import REASONING
from ..protocol import events as ev
from ..providers.text_tools import Holdback
from .context import EngineContext

# The key a protocol event travels under in the "custom" stream.
EVENT = "cmcoder_event"
# The graph's node whose model calls the user sees (create_agent's name for it).
MODEL_NODE = "model"


@dataclass
class Reply:
    """A model call is done: show the text held back so far and its
    `AssistantMessage`, unless the critic must review the answer first."""

    event: ev.AssistantMessage
    deferred: bool = False


@dataclass
class Release:
    """The critic's review is in: show the answer that waited for it."""


@dataclass
class _Barrier:
    reached: asyncio.Event = field(default_factory=asyncio.Event)


def _bridged() -> bool:
    from langgraph.runtime import get_runtime

    try:
        runtime = get_runtime()
    except (RuntimeError, LookupError):
        return False
    context = runtime.context if runtime is not None else None
    return isinstance(context, EngineContext) and context.bridged


def emit(item: ev.Event | Reply | Release) -> None:
    """From a node, middleware or tool: send `item` to the front end."""
    get_stream_writer()({EVENT: item})


async def delivered() -> None:
    """Wait until everything emitted so far has reached the front end."""
    if not _bridged():
        return
    barrier = _Barrier()
    emit(barrier)  # type: ignore[arg-type]
    await barrier.reached.wait()


class Bridge:
    """Runs a graph and yields its protocol events (see the module's docstring)."""

    def __init__(self, *, hold: bool, on_values: Callable[[dict[str, Any]], None]) -> None:
        self.hold = hold  # the critic reviews the final answer before it's shown
        self.on_values = on_values
        self._holdback = Holdback()
        self._held = ""  # this call's text, held for the critic
        self._waiting: tuple[ev.AssistantMessage, str] | None = None  # the answer under review

    async def run(
        self, graph: Any, state: dict[str, Any], config: dict[str, Any], context: EngineContext
    ) -> AsyncIterator[ev.Event]:
        context.bridged = True
        stream = graph.astream(
            state, config, context=context, stream_mode=["messages", "custom", "values"]
        )
        async for mode, data in stream:
            if mode == "values":
                self.on_values(data)
            elif mode == "messages":
                chunk, meta = data
                if meta.get("langgraph_node") == MODEL_NODE and isinstance(chunk, AIMessageChunk):
                    for out in self._chunk(chunk):
                        yield out
            elif mode == "custom" and isinstance(data, dict) and EVENT in data:
                for out in self._custom(data[EVENT]):
                    yield out

    def _chunk(self, chunk: AIMessageChunk) -> list[ev.Event]:
        out: list[ev.Event] = []
        if thought := chunk.additional_kwargs.get(REASONING):
            out.append(ev.ReasoningDelta(text=str(thought)))
        text = chunk.content if isinstance(chunk.content, str) else chunk.text
        if text and (shown := self._holdback.feed(text)):
            if self.hold:
                self._held += shown
            else:
                out.append(ev.AssistantDelta(text=shown))
        return out

    def _custom(self, item: Any) -> list[ev.Event]:
        if isinstance(item, _Barrier):
            item.reached.set()
            return []
        if isinstance(item, Reply):
            return self._reply(item)
        if isinstance(item, Release):
            waiting, self._waiting = self._waiting, None
            return self._show(*waiting) if waiting is not None else []
        return [item]

    def _reply(self, reply: Reply) -> list[ev.Event]:
        out: list[ev.Event] = []
        if rest := self._holdback.flush():  # the reply's text after the last token
            if self.hold:
                self._held += rest
            else:
                out.append(ev.AssistantDelta(text=rest))
        self._holdback = Holdback()  # the next call starts afresh
        held, self._held = self._held, ""
        # An answer that waited for review and was sent back for more work is never shown.
        self._waiting = (reply.event, held) if reply.deferred else None
        return out if reply.deferred else out + self._show(reply.event, held)

    @staticmethod
    def _show(event: ev.AssistantMessage, held: str) -> list[ev.Event]:
        return [*([ev.AssistantDelta(text=held)] if held else []), event]
