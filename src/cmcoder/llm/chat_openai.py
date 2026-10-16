"""LangChain's ChatOpenAI, with what cmcoder's gateways and Qwen on vLLM need.

ChatOpenAI talks to any OpenAI-compatible server, but the Phase 8 spike (E1)
found three things it gets wrong there, which cmcoder had fixed in its own
provider (providers/openai_compat.py):

- **Reasoning.** vLLM sends the model's thinking in `reasoning_content`
  (newer vLLM: `reasoning`), and a server without a reasoning parser leaves
  it in the text as `<think>...</think>`. ChatOpenAI drops the first and
  shows the second as the answer. Here both end up in
  `additional_kwargs["reasoning_content"]`, for display only: it is never
  sent back.
- **Past tool calls.** vLLM parses the arguments of every tool call in the
  history, and one the model wrote as broken JSON fails every later
  request (400 "Expecting ',' delimiter"). They go out as strict JSON
  objects (`wire_arguments`).
- **Empty replies.** A reply with nothing in it, kept in the history, is
  copied by the model: it's left out.

Per call, `thinking=False` turns Qwen's thinking off the way the model
profile says, and the request carries `max_tokens` under the name the
server takes and Ollama's options through Open WebUI.

Tool calls written as text (Qwen's `<tool_call>` format) become real tool
calls, for a server without a tool-call parser. With the profile's
`toolCalling: "prompted"` (a server that rejects the `tools` parameter) the
tools are described in the system prompt instead, and past calls and their
results are sent as text (providers/text_tools.py).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import Field, PrivateAttr

from ..providers.messages import ToolSpec, parse_tool_arguments, wire_arguments
from ..providers.profiles import ModelProfile
from ..providers.text_tools import CLOSE as CALL_CLOSE
from ..providers.text_tools import OPEN as CALL_OPEN
from ..providers.text_tools import extract, tools_prompt
from ..providers.thinking import CLOSE, ThinkSplitter

REASONING = "reasoning_content"  # where a message keeps the model's thinking
# The reasoning modes in which the text may hold <think> tags.
TAG_MODES = ("auto", "think-tags", "think-open")
# Runs whose <think> tags are being split, kept until the run's final message
# is put together (a bound, so stream()-only runs don't pile up).
MAX_OPEN_RUNS = 64


class CmcoderChatOpenAI(ChatOpenAI):
    """ChatOpenAI for cmcoder: see the module's docstring."""

    cm_profile: ModelProfile = Field(default_factory=lambda: ModelProfile.model_validate({}))
    """cmcoder's profile of the model (reasoning, thinking switch, backend, …)."""

    # This model's chat template opens <think> itself (Qwen3 Thinking-2507), so
    # its text starts inside the thinking. Learned from its first reply.
    _opens_think: bool = PrivateAttr(default=False)
    # Per run: (saw <think>, saw </think>) of the streamed text.
    _think_seen: dict[UUID, tuple[bool, bool]] = PrivateAttr(default_factory=dict)

    # --- The request -------------------------------------------------------

    def _get_request_payload(
        self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        thinking = kwargs.pop("thinking", None)
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload["messages"] = wire_history(payload.get("messages") or [])
        p = self.cm_profile
        if p.tool_calling == "prompted" and payload.get("tools"):
            specs = [_spec(tool) for tool in payload.pop("tools")]
            payload.pop("tool_choice", None)
            payload.pop("parallel_tool_calls", None)
            payload["messages"] = prompted_history(payload["messages"], specs)
        for name in ("max_tokens", "max_completion_tokens"):
            if name in payload and name != p.max_tokens_param:
                payload[p.max_tokens_param] = payload.pop(name)
        if payload.get("tools") and not p.parallel_tool_calls:
            payload.setdefault("parallel_tool_calls", False)
        extra: dict[str, Any] = dict(payload.get("extra_body") or {})
        if p.backend == "ollama":
            # Open WebUI passes `options` to Ollama (and moves `think` to the top).
            options: dict[str, Any] = {"num_ctx": p.context_window}
            if thinking is False and p.thinking_switch != "none":
                options["think"] = False  # only for models that think (Ollama rejects it otherwise)
            extra["options"] = options
        elif thinking is False:
            if p.thinking_switch == "chat_template_kwargs":
                extra["chat_template_kwargs"] = {"enable_thinking": False}
            elif p.thinking_switch == "prompt":
                _no_think(payload["messages"])
        extra.update(p.extra_body)
        if extra:
            payload["extra_body"] = extra
        return payload

    # --- The reply ---------------------------------------------------------

    def _convert_chunk_to_generation_chunk(
        self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None
    ) -> ChatGenerationChunk | None:
        out = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        delta = (choices[0].get("delta") or {}) if choices else {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if out is not None and isinstance(reasoning, str) and reasoning:
            out.message.additional_kwargs[REASONING] = reasoning
        return out

    def _create_chat_result(self, response: Any, generation_info: dict | None = None) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        data = response if isinstance(response, dict) else response.model_dump()
        for gen, choice in zip(result.generations, data.get("choices") or [], strict=False):
            message = choice.get("message") or {}
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                gen.message.additional_kwargs[REASONING] = reasoning
        return result

    def _splitter(self) -> ThinkSplitter | None:
        if self.cm_profile.reasoning not in TAG_MODES:
            return None
        return ThinkSplitter(starts_open=self._starts_open())

    def _starts_open(self) -> bool:
        return self.cm_profile.reasoning == "think-open" or self._opens_think

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        # The callbacks (what streams to the user) get the chunks after
        # splitting, so they're called here rather than by ChatOpenAI.
        splitter = self._splitter()
        for chunk in super()._stream(messages, stop=stop, run_manager=None, **kwargs):
            out = _split(chunk, splitter)
            if run_manager:
                run_manager.on_llm_new_token(out.text, chunk=out)
            yield out
        if splitter is not None:
            if (tail := _flush(splitter)) is not None:
                if run_manager:
                    run_manager.on_llm_new_token(tail.text, chunk=tail)
                yield tail
            self._remember(run_manager, splitter)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        splitter = self._splitter()
        async for chunk in super()._astream(messages, stop=stop, run_manager=None, **kwargs):
            out = _split(chunk, splitter)
            if run_manager:
                await run_manager.on_llm_new_token(out.text, chunk=out)
            yield out
        if splitter is not None:
            if (tail := _flush(splitter)) is not None:
                if run_manager:
                    await run_manager.on_llm_new_token(tail.text, chunk=tail)
                yield tail
            self._remember(run_manager, splitter)

    def _remember(self, run_manager: Any, splitter: ThinkSplitter) -> None:
        if run_manager is None:
            return
        if len(self._think_seen) >= MAX_OPEN_RUNS:
            self._think_seen.clear()
        self._think_seen[run_manager.run_id] = (splitter.saw_open, splitter.saw_close)

    # Every invoke goes through these, streamed or not (LangChain puts a
    # streamed reply together itself, without calling _generate).
    def _generate_with_cache(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        result = super()._generate_with_cache(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        return self._finish(result, run_manager, kwargs.get("tools"))

    async def _agenerate_with_cache(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        result = await super()._agenerate_with_cache(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        return self._finish(result, run_manager, kwargs.get("tools"))

    def _finish(self, result: ChatResult, run_manager: Any, tools: Any = None) -> ChatResult:
        """The whole reply's corrections, once its last chunk is in."""
        seen = self._think_seen.pop(run_manager.run_id, None) if run_manager else None
        known = {_spec(tool).name for tool in tools or []}
        generations: list[ChatGeneration] = []
        for gen in result.generations:
            message = gen.message
            if isinstance(message, AIMessage) and self.cm_profile.reasoning in TAG_MODES:
                message = self._finish_thinking(message, seen)
            if isinstance(message, AIMessage) and known:
                message = text_tool_calls(message, known)
            if isinstance(message, AIMessage):
                message = checked_tool_calls(message)
            generations.append(ChatGeneration(message=message, generation_info=gen.generation_info))
        return ChatResult(generations=generations, llm_output=result.llm_output)

    def _finish_thinking(self, message: AIMessage, seen: tuple[bool, bool] | None) -> AIMessage:
        content = message.content if isinstance(message.content, str) else None
        if content is None:
            return message
        reasoning = str(message.additional_kwargs.get(REASONING, ""))
        if seen is None:  # not streamed: split the whole text now
            splitter = ThinkSplitter(starts_open=self._starts_open())
            parts = splitter.feed(content) + splitter.flush()
            content = "".join(p for kind, p in parts if kind == "text")
            reasoning += "".join(p for kind, p in parts if kind != "text")
            seen = (splitter.saw_open, splitter.saw_close)
        saw_open, saw_close = seen
        if self._starts_open() and not saw_close:
            # The expected </think> never came: the model didn't think this
            # time, so what looked like thinking is the answer.
            content, reasoning = reasoning + content, ""
        elif not saw_open and CLOSE in content:
            # The chat template opened <think> for the model (Qwen3
            # Thinking-2507): the text before </think> is its thinking, and
            # from now on its replies start inside the thinking.
            before, _, after = content.partition(CLOSE)
            content, reasoning = after.lstrip(), before + reasoning
            self._opens_think = True
        kwargs = {k: v for k, v in message.additional_kwargs.items() if k != REASONING}
        if reasoning:
            kwargs[REASONING] = reasoning
        return message.model_copy(update={"content": content, "additional_kwargs": kwargs})


def wire_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The request's messages with past tool arguments as strict JSON
    objects, empty replies left out and no reasoning sent back."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "assistant":
            m = {k: v for k, v in m.items() if k not in (REASONING, "reasoning")}
            calls = m.get("tool_calls") or []
            if not calls and _empty(m.get("content")):
                continue  # an empty reply: the model would copy it
            if calls:
                m["tool_calls"] = [
                    {
                        **c,
                        "function": {
                            **c.get("function", {}),
                            "arguments": wire_arguments(
                                (c.get("function") or {}).get("arguments") or ""
                            ),
                        },
                    }
                    for c in calls
                ]
        out.append(m)
    return out


def _empty(content: Any) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        return not any(
            (p.get("text") or "").strip() if isinstance(p, dict) and p.get("type") == "text" else p
            for p in content
        )
    return False


def _no_think(messages: list[dict[str, Any]]) -> None:
    """Qwen's prompt switch: " /no_think" after the last user message."""
    for item in reversed(messages):
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, list):  # with images: the text part, or a new one
            texts = [p for p in content if isinstance(p, dict) and p.get("type") == "text"]
            if texts:
                texts[0]["text"] += " /no_think"
            else:
                content.insert(0, {"type": "text", "text": "/no_think"})
        else:
            item["content"] = f"{content} /no_think"
        return


def _split(chunk: ChatGenerationChunk, splitter: ThinkSplitter | None) -> ChatGenerationChunk:
    """A chunk with any <think> text moved from its content to its reasoning."""
    message = chunk.message
    if splitter is None or not isinstance(message.content, str) or not message.content:
        return chunk
    parts = splitter.feed(message.content)
    text = "".join(p for kind, p in parts if kind == "text")
    thought = "".join(p for kind, p in parts if kind != "text")
    return _with(chunk, text, thought)


def _flush(splitter: ThinkSplitter) -> ChatGenerationChunk | None:
    parts = splitter.flush()
    if not parts:
        return None
    text = "".join(p for kind, p in parts if kind == "text")
    thought = "".join(p for kind, p in parts if kind != "text")
    return _with(ChatGenerationChunk(message=AIMessageChunk(content="")), text, thought)


def _with(chunk: ChatGenerationChunk, text: str, thought: str) -> ChatGenerationChunk:
    message = chunk.message
    kwargs = dict(message.additional_kwargs)
    if thought:
        kwargs[REASONING] = str(kwargs.get(REASONING, "")) + thought
    new = message.model_copy(update={"content": text, "additional_kwargs": kwargs})
    return ChatGenerationChunk(message=new, generation_info=chunk.generation_info)


def _spec(tool: Any) -> ToolSpec:
    """A bound tool (OpenAI's format) as cmcoder's ToolSpec."""
    fn = tool.get("function", tool) if isinstance(tool, dict) else {}
    return ToolSpec(
        name=str(fn.get("name", "")),
        description=str(fn.get("description", "")),
        parameters=fn.get("parameters") or {},
    )


def prompted_history(messages: list[dict[str, Any]], tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """The request for a server that gets no `tools` parameter: the tools in
    the system prompt, calls and their results as text (several results in
    one user message)."""
    out: list[dict[str, Any]] = []
    described = False
    results: dict[str, Any] | None = None  # the user message collecting tool results
    for m in messages:
        role = m.get("role")
        if role in ("system", "developer") and not described:
            out.append({**m, "content": f"{m.get('content') or ''}\n\n{tools_prompt(tools)}"})
            described = True
        elif role == "assistant":
            parts = [m["content"]] if isinstance(m.get("content"), str) and m["content"] else []
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                args, _ = parse_tool_arguments(fn.get("arguments") or "")
                block = {"name": fn.get("name"), "arguments": args if args is not None else {}}
                parts.append(f"{CALL_OPEN}\n{json.dumps(block, ensure_ascii=False)}\n{CALL_CLOSE}")
            out.append({"role": "assistant", "content": "\n".join(parts)})
        elif role == "tool":
            block = f"<tool_response>\n{m.get('content') or ''}\n</tool_response>"
            if results is not None and out and out[-1] is results:
                results["content"] += "\n" + block
            else:
                results = {"role": "user", "content": block}
                out.append(results)
            continue
        else:
            out.append(m)
        results = None
    if not described:
        out.insert(0, {"role": "system", "content": tools_prompt(tools)})
    return out


def checked_tool_calls(message: AIMessage) -> AIMessage:
    """The reply's tool calls as cmcoder's own provider took them: arguments
    the model got slightly wrong (a trailing comma, a Python dict, a code
    fence) repaired, as LangChain doesn't; a call without a name dropped; and
    every call with an id of its own (some servers send none, or repeat one)."""
    if not message.tool_calls and not message.invalid_tool_calls:
        return message
    valid = [dict(c) for c in message.tool_calls]
    invalid: list[dict[str, Any]] = []
    for bad in message.invalid_tool_calls:
        if not bad.get("name"):
            continue
        raw = bad.get("args")
        args, error = parse_tool_arguments(raw) if isinstance(raw, str) else (None, None)
        if args is not None:
            valid.append({"type": "tool_call", "id": bad.get("id"), "name": bad["name"], "args": args})
        else:
            invalid.append({**bad, "error": bad.get("error") or error})
    seen: set[str] = set()
    for call in [*valid, *invalid]:
        if not call.get("id") or call["id"] in seen:
            call["id"] = f"call_{uuid.uuid4().hex[:16]}"
        seen.add(call["id"])
    return message.model_copy(update={"tool_calls": valid, "invalid_tool_calls": invalid})


def text_tool_calls(message: AIMessage, known: set[str]) -> AIMessage:
    """A reply's `<tool_call>` text for known tools, as its tool calls."""
    if (
        message.tool_calls
        or not isinstance(message.content, str)
        or CALL_OPEN not in message.content
    ):
        return message
    text, calls = extract(message.content, known)
    if not calls:
        return message
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for call in calls:
        call_id = call.id or f"call_{uuid.uuid4().hex[:12]}"
        args, error = parse_tool_arguments(call.arguments)
        if args is None:
            invalid.append(
                {
                    "type": "invalid_tool_call",
                    "id": call_id,
                    "name": call.name,
                    "args": call.arguments,
                    "error": error,
                }
            )
        else:
            valid.append({"type": "tool_call", "id": call_id, "name": call.name, "args": args})
    return message.model_copy(
        update={"content": text, "tool_calls": valid, "invalid_tool_calls": invalid}
    )
