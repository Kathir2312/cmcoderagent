"""A model call's usage as the classic engine counts it: the server's token
counts (estimated from the text when it sends none, marked as such) and the
cost LiteLLM puts in a response header (`x-litellm-response-cost`).

ChatOpenAI gives the token counts (`usage_metadata`) but never sees the
header, so cmcoder's HTTP client reads it (`record_cost`, an httpx response
hook) into a holder that the model call opened (`counting_cost`)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai.chat_models.base import _convert_message_to_dict

from ..providers.messages import Usage
from ..providers.openai_compat import _parse_cost, estimate_tokens

_COST: ContextVar[dict[str, float] | None] = ContextVar("cmcoder_cost", default=None)


async def record_cost(response: httpx.Response) -> None:
    """httpx response hook: LiteLLM's cost of this call, for the open holder."""
    holder = _COST.get()
    if holder is not None and (cost := _parse_cost(response.headers)) is not None:
        holder["cost"] = cost


@contextmanager
def counting_cost() -> Iterator[dict[str, float]]:
    """Around one model call: the holder `record_cost` fills (shared by
    reference, so it also works where LangChain copies the context)."""
    holder: dict[str, float] = {}
    token = _COST.set(holder)
    try:
        yield holder
    finally:
        _COST.reset(token)


def usage_of(message: AIMessage, request: list[BaseMessage], cost: float | None = None) -> Usage:
    """The reply's usage; estimated (~4 characters a token) without the server's."""
    meta = message.usage_metadata
    if meta:
        return Usage(
            prompt_tokens=int(meta.get("input_tokens") or 0),
            completion_tokens=int(meta.get("output_tokens") or 0),
            cost=cost,
        )
    sent: list[dict[str, Any]] = [_convert_message_to_dict(m) for m in request]
    text = message.text + str(message.additional_kwargs.get("reasoning_content", ""))
    calls = sum(estimate_tokens(json.dumps(c["args"])) for c in message.tool_calls)
    return Usage(
        prompt_tokens=estimate_tokens(json.dumps(sent)),
        completion_tokens=estimate_tokens(text) + calls,
        estimated=True,
        cost=cost,
    )
