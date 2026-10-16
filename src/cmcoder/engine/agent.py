"""The Phase 8 engine's agent: `create_agent` with cmcoder's middleware.

`GraphAgent` is the classic `Agent` (core/agent.py) with its turn loop
replaced. What the front ends use around a turn is the classic agent's
(sessions, /rewind, /compact, slash commands, MCP servers, subagent runs,
closing), so they only choose which class to make (the `engine` setting).
A turn:

1. as before: the user's message, images described for a model that can't
   see them, startup warnings, MCP servers started;
2. the graph (`create_agent(model, tools, middleware=[...])`, built again
   only when the tools change) runs from the conversation so far, and the
   event bridge (events.py) turns its streams into protocol events;
3. after every step the classic message list follows the graph's
   (convert.Mirror), and sessions are saved from it as before;
4. the final state says how the turn ended (`outcome`), and the turn ends
   with the same `Result` as the classic engine's.

Until M5, the classic message list stays the conversation's record and each
turn's graph starts from it (no checkpointer yet).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ..core.agent import Agent
from ..images import Image
from ..llm.factory import chat_model_from_provider
from ..mcp_client import McpTool
from ..protocol import events as ev
from ..providers.messages import Message
from ..providers.openai_compat import ContextTooLong, ProviderError
from ..core import critic
from .context import EngineContext
from .convert import Mirror
from .events import Bridge
from .mcp import mcp_tools
from .middleware import cmcoder_middleware
from .middleware.tool_calls import DENIED
from .state import Turn
from .tools import CmcoderTool

# LangGraph's step limit, well above what maxTurns allows: each model call
# takes several steps (the hooks around it, the tools).
STEPS_PER_MODEL_CALL = 12


class GraphAgent(Agent):
    """The classic agent with the turn run as a LangGraph graph (see the module)."""

    engine = "langgraph"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.mirror = Mirror()
        self.current = Turn(prompt="", user_msg_id="", start=0)
        self._chat: tuple[Any, BaseChatModel] | None = None
        self._graph: tuple[Any, Any] | None = None
        self._mcp_tools: dict[str, BaseTool] = {}

    @property
    def sees_images(self) -> bool:
        return bool(self.profile.vision)

    @property
    def holds_answers(self) -> bool:
        """Critique is on: the final answer waits for the critic's review."""
        return self.critique and not self.is_subagent

    def chat(self) -> BaseChatModel:
        """The chat model for the model and profile as they are now (`/model`,
        or prompted tool calls switched on, give a new one)."""
        key = (id(self.provider), self.model, self.profile.model_dump_json())
        if self._chat is None or self._chat[0] != key:
            self._chat = (key, chat_model_from_provider(self.provider, self.model, self.profile))
        return self._chat[1]

    async def graph(self) -> Any:
        """`create_agent` with cmcoder's tools and middleware, built again
        when the tools change (MCP servers started, a skill added)."""
        key = tuple((name, id(tool)) for name, tool in self.tools.items())
        if self._graph is None or self._graph[0] != key:
            if self.mcp is not None and self.mcp.connected() and not self._mcp_tools:
                self._mcp_tools = {t.name: t for t in await mcp_tools(self.mcp)}
            tools: list[BaseTool] = []
            for name, tool in self.tools.items():
                adapted = self._mcp_tools.get(name) if isinstance(tool, McpTool) else None
                tools.append(adapted or CmcoderTool(tool))
            built = create_agent(
                self.chat(),
                tools,
                middleware=cmcoder_middleware(self),
                context_schema=EngineContext,
                name="cmcoder",
            )
            self._graph = (key, built)
        return self._graph[1]

    async def _run(
        self, prompt: str, context: str | None, images: list[Image]
    ) -> AsyncIterator[ev.Event]:
        started = time.monotonic()
        self.turn += 1
        user_message = Message.user(f"{context}\n\n{prompt}" if context else prompt)
        user_message.turn = self.turn
        user_message.images = list(images)
        self.messages.append(user_message)

        def ended(subtype: str, text: str, *, is_error: bool, steps: int = 0, usage: bool = True) -> ev.Result:
            turn_usage = self.current.usage
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
                }
                if usage
                else {},
                session_id=self.session_id,
                review=self.current.review_info if usage else None,
            )

        self.current = Turn(prompt=prompt, user_msg_id="", start=len(self.messages) - 1, started=started)
        if images and not self.profile.vision:
            problem = None
            async for event in self._describe_images(user_message, prompt):
                if isinstance(event, ev.Error):
                    problem = event.message
                yield event
            if problem is not None:
                self.messages.pop()
                self.turn -= 1
                yield ended("error", problem, is_error=True, usage=False)
                return
        for message in self.startup_warnings:
            yield ev.Warning(message=message)
        self.startup_warnings = []
        if switched := critic.follow_saved(self):  # switched in another window
            yield switched
        async for warning in self._start_mcp():
            yield warning

        graph = await self.graph()
        system = self.messages[:1] if self.messages and self.messages[0].role == "system" else []
        self.mirror = Mirror()
        history = self.mirror.to_graph(self.messages[len(system) :], vision=self.sees_images)
        self.current.user_msg_id = history[-1].id or ""
        final: dict[str, Any] = {}

        def follow(values: dict[str, Any]) -> None:
            final.clear()
            final.update(values)
            self.messages = [*system, *self.mirror.from_graph(values["messages"])]

        bridge = Bridge(hold=self.holds_answers, on_values=follow)
        config = {"recursion_limit": (self.max_turns + 5) * STEPS_PER_MODEL_CALL}
        try:
            try:
                async for event in bridge.run(
                    graph, {"messages": history}, config, EngineContext(tool_ctx=self.ctx)
                ):
                    yield event
            except ProviderError as e:
                steps = final.get("steps", 0)
                hint = "Use /clear to start a new conversation." if isinstance(e, ContextTooLong) else e.hint
                yield ev.Error(kind=e.kind, message=str(e).split("\n  hint:")[0], hint=hint)
                # Drop the user message if the model never answered, so retrying works cleanly.
                if self.messages[-1].role == "user" and steps <= 1:
                    self.messages.pop()
                    self.turn -= 1
                yield ended("error", str(e), is_error=True, steps=steps)
                return
            steps = final.get("steps", 0)
            outcome = final.get("outcome")
            if outcome is None:
                yield ended("success", self.current.last_text, is_error=False, steps=steps)
            elif outcome["subtype"] == "blocked":
                self.messages.pop()
                self.turn -= 1
                yield ended("error", outcome["text"], is_error=True, usage=False)
            elif outcome["subtype"] == "max_turns":
                yield ended("max_turns", outcome["text"], is_error=True, steps=steps)
            else:
                yield ended("interrupted", outcome["text"] or DENIED, is_error=False, steps=steps)
        except asyncio.CancelledError:
            self._repair_after_interrupt()
            raise
        finally:
            self.save_session()
            if self.turn == 1 and not self._titled and self._session_started:
                self._start_title(prompt)
