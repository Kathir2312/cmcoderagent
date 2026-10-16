"""The OpenAI SDK's errors (what ChatOpenAI raises) as cmcoder's own
(providers/openai_compat.py), so both engines tell the user the same thing
and react the same way: a "too long" request teaches the real context
window, a server without tool calling switches to prompted tools, a bad key
says to run `cmcoder login`."""

from __future__ import annotations

import openai

from ..providers.openai_compat import (
    ConnectionFailed,
    ProviderError,
    Timeout,
    classify_http_error,
    classify_transport_error,
)


def provider_error(exc: BaseException, base_url: str) -> ProviderError | None:
    """`exc` as a ProviderError, or None when it isn't the gateway's (a bug)."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, openai.APIStatusError):
        response = exc.response
        body = getattr(response, "content", None) or str(exc.body or exc.message)
        return classify_http_error(exc.status_code, body, getattr(response, "headers", None))
    if isinstance(exc, openai.APITimeoutError):
        return Timeout(f"Timed out talking to {base_url}")
    if isinstance(exc, openai.APIConnectionError):
        cause = exc.__cause__ or exc.__context__
        if isinstance(cause, Exception):
            return classify_transport_error(cause, base_url)
        return ConnectionFailed(f"Cannot connect to {base_url}: {exc}")
    if isinstance(exc, openai.APIError):  # e.g. an error event inside the stream
        return classify_http_error(500, str(exc.body or exc.message))
    return None
