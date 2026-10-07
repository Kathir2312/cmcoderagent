"""OpenAI Chat Completions adapter (LiteLLM, vLLM, Ollama /v1, OpenAI, ...).

Built directly on httpx so we control streaming, non-standard fields and error
handling across servers.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import ssl
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .auth import AuthError, AuthProvider
from .content import user_content
from .messages import (
    Message,
    ReasoningDelta,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallStarted,
    ToolSpec,
    Usage,
)
from .profiles import ModelProfile
from .text_tools import to_prompted_wire
from .thinking import CLOSE as CLOSE_TAG
from .thinking import ThinkSplitter

# --- Errors ------------------------------------------------------------------


class ProviderError(Exception):
    kind = "provider"
    retryable = False

    def __init__(self, message: str, *, status: int | None = None, hint: str | None = None):
        super().__init__(message)
        self.status = status
        self.hint = hint
        self.retry_after: float | None = None

    def __str__(self) -> str:
        msg = super().__str__()
        return f"{msg}\n  hint: {self.hint}" if self.hint else msg


class ConnectionFailed(ProviderError):
    kind = "connection"
    retryable = True


class TLSFailed(ProviderError):
    kind = "tls"


class AuthFailed(ProviderError):
    kind = "auth"


class BudgetExceeded(ProviderError):
    kind = "budget"


class RateLimited(ProviderError):
    kind = "rate_limit"
    retryable = True


class ServerError(ProviderError):
    kind = "server"
    retryable = True


class ContextTooLong(ProviderError):
    kind = "context_length"
    # The limit the server stated in its error, when it said one.
    context_window: int | None = None


# How servers state their limit:
#   vLLM / OpenAI: "This model's maximum context length is 32768 tokens. ..."
#   LiteLLM wraps those: "ContextWindowExceededError: ... maximum context length is 32768 ..."
#   llama.cpp / others: "... exceeds the available context size (8192 tokens)"
#   TGI: "... must have less than 4096 tokens" / max_model_len=4096
_WINDOW_PATTERNS = [
    re.compile(r"maximum context length is (\d+)"),
    re.compile(r"max_model_len\W{0,4}(\d+)"),
    re.compile(r"context (?:length|window|size) (?:is |of )?(?:only )?\(?(\d+) tokens"),
    re.compile(r"must have less than (\d+) (?:input )?tokens"),
]


def parse_context_window(text: str) -> int | None:
    """The context window a server states in an error message, if any."""
    low = text.lower().replace(",", "")
    for pattern in _WINDOW_PATTERNS:
        if m := pattern.search(low):
            n = int(m.group(1))
            if 512 <= n <= 10_000_000:  # ignore unrelated small numbers
                return n
    return None


class BadRequest(ProviderError):
    kind = "bad_request"


class Timeout(ProviderError):
    kind = "timeout"
    retryable = True


def _error_message(body: bytes | str) -> str:
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:500] or "(empty response body)"
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        return str(err.get("message") or err.get("detail") or err)[:500]
    return str(err)[:500]


def classify_http_error(
    status: int, body: bytes | str, headers: httpx.Headers | None = None
) -> ProviderError:
    msg = _error_message(body)
    low = msg.lower()
    raw = (body.decode("utf-8", "replace") if isinstance(body, bytes) else body).lower()
    # LiteLLM reports some auth failures with status 400 and type "auth_error".
    auth_like = '"auth_error"' in raw or "authentication error" in low or "invalid api key" in low
    err: ProviderError
    if "budget" in low:
        err = BudgetExceeded(
            f"API key budget exceeded: {msg}",
            status=status,
            hint="Contact the LiteLLM admin to raise the key's budget.",
        )
    elif status in (401, 403) or auth_like:
        err = AuthFailed(
            f"Authentication failed ({status}): {msg}",
            status=status,
            hint="Check your API key with `cmcoder login`.",
        )
    elif status == 429:
        err = RateLimited(f"Rate limited: {msg}", status=status)
    elif (
        "context length" in low
        or "maximum context" in low
        or "too many tokens" in low
        or "context size" in low
        or "max_model_len" in low
    ):
        err = ContextTooLong(f"Request too long for the model: {msg}", status=status)
        err.context_window = parse_context_window(raw)
    elif status == 404:
        err = BadRequest(
            f"Not found ({status}): {msg}",
            status=status,
            hint="Check the base URL and model name (`cmcoder models`).",
        )
    elif status in (408, 409) or status >= 500:
        err = ServerError(f"Server error ({status}): {msg}", status=status)
    else:
        err = BadRequest(f"Request rejected ({status}): {msg}", status=status)
    if headers is not None and (ra := headers.get("retry-after")):
        try:
            err.retry_after = float(ra)
        except ValueError:
            pass
    return err


def classify_transport_error(exc: Exception, base_url: str) -> ProviderError:
    text = str(exc)
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(cause):
            return TLSFailed(
                f"TLS certificate verification failed for {base_url}: {cause}",
                hint="The server likely uses an internal CA. Install the company root CA "
                "in the OS trust store, or set `caCertPath` / CMCODER_CA_CERT. "
                "Run `cmcoder doctor` for details.",
            )
        cause = cause.__cause__ or cause.__context__
    if isinstance(exc, httpx.TimeoutException):
        return Timeout(f"Timed out talking to {base_url}: {type(exc).__name__}")
    low = text.lower()
    if any(
        s in low
        for s in (
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo",
            "name resolution",
        )
    ):
        err = ConnectionFailed(
            f"Cannot resolve host for {base_url}",
            hint="The server name only resolves on the company network. Connect to the VPN.",
        )
        err.retryable = False
        return err
    return ConnectionFailed(f"Cannot connect to {base_url}: {text or type(exc).__name__}")


# --- Wire conversion -----------------------------------------------------------

# A request with tools rejected because the backend has no tool calling
# (e.g. vLLM without --enable-auto-tool-choice).
_NO_TOOL_SUPPORT = re.compile(
    r"enable-auto-tool-choice|tool.?choice.{0,40}(not supported|requires)|"
    r"does not support (tools|function calling|tool)|tools? (are|is) not supported",
    re.I,
)


def no_tool_support(err: ProviderError) -> bool:
    return isinstance(err, BadRequest) and bool(_NO_TOOL_SUPPORT.search(str(err)))


def to_wire_messages(messages: list[Message], vision: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments or "{}"},
                    }
                    for tc in m.tool_calls
                ]
            elif not m.content:
                item["content"] = ""
            out.append(item)
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "user":
            out.append({"role": "user", "content": user_content(m, vision)})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def to_wire_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# --- Provider ----------------------------------------------------------------


class OpenAICompatProvider:
    kind = "openai"  # see providers/openwebui.py for "openwebui"

    def __init__(
        self,
        name: str,
        base_url: str,
        auth: AuthProvider,
        client: httpx.AsyncClient,
        *,
        max_retries: int = 2,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.client = client
        self.max_retries = max_retries
        # Models seen to omit the opening <think> (detected automatically).
        self.open_think_models: set[str] = set()

    async def aclose(self) -> None:
        await self.client.aclose()

    def _with_key_hint(self, err: ProviderError) -> ProviderError:
        """For auth failures, say which key was sent (masked), so a key from
        the wrong place (e.g. an environment variable) is easy to spot."""
        describe = getattr(self.auth, "describe", None)
        if isinstance(err, AuthFailed) and describe is not None:
            err.hint = (
                f"cmcoder sent the {describe()}. If that's not the key you expect, check "
                "CMCODER_API_KEY, then run `cmcoder login`; if it is, ask the gateway admin "
                "whether the key is valid there."
            )
        return err

    # -- simple GET endpoints --

    async def _get_json(self, url: str) -> Any:
        try:
            headers = await self.auth.get_headers()
        except AuthError as e:
            raise AuthFailed(str(e)) from e
        try:
            resp = await self.client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise classify_transport_error(e, self.base_url) from e
        if resp.status_code != 200:
            raise self._with_key_hint(
                classify_http_error(resp.status_code, resp.content, resp.headers)
            )
        return resp.json()

    async def list_models(self) -> list[str]:
        data = await self._get_json(f"{self.base_url}/models")
        return sorted(str(m.get("id")) for m in data.get("data", []) if m.get("id"))

    async def model_info(self) -> dict[str, dict[str, Any]]:
        """LiteLLM `/model/info`: context window, max output, tool support per alias.

        Returns {} when the server doesn't expose it.
        """
        candidates = [f"{self.base_url}/model/info"]
        if self.base_url.endswith("/v1"):
            candidates.append(f"{self.base_url[:-3]}/model/info")
        for url in candidates:
            try:
                data = await self._get_json(url)
            except (BadRequest, AuthFailed):
                continue
            out: dict[str, dict[str, Any]] = {}
            for item in data.get("data", []):
                name = item.get("model_name")
                if name:
                    out[str(name)] = dict(item.get("model_info") or {})
            return out
        return {}

    # -- embeddings --

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """One vector per text, from `<base>/embeddings` (OpenAI format).

        LiteLLM serves it at /v1/embeddings; Open WebUI at /api/embeddings,
        passing Ollama models on to Ollama's /api/embed (openwebui.py)."""
        if not texts:
            return []
        url = f"{self.base_url}/embeddings"
        body = {"model": model, "input": texts}
        attempt = 0
        while True:
            try:
                try:
                    headers = await self.auth.get_headers()
                except AuthError as e:
                    raise AuthFailed(str(e), hint="Run `cmcoder login`.") from e
                try:
                    resp = await self.client.post(url, json=body, headers=headers)
                except httpx.HTTPError as e:
                    raise classify_transport_error(e, self.base_url) from e
                if resp.status_code != 200:
                    raise self._with_key_hint(
                        classify_http_error(resp.status_code, resp.content, resp.headers)
                    )
                return _parse_embeddings(resp, model, len(texts))
            except ProviderError as err:
                if not err.retryable or attempt >= self.max_retries:
                    raise
                await asyncio.sleep(_backoff(attempt, err.retry_after))
                attempt += 1

    # -- context window --

    async def probe_context_window(
        self, model: str, profile: ModelProfile, timeout: float = 8.0
    ) -> int | None:
        """Ask the server for the model's real context window.

        Sends a one-word prompt with an impossibly large max_tokens. vLLM and
        similar servers reject it before generating anything, stating their
        limit ("maximum context length is N tokens"). A server that accepts
        the request instead is disconnected immediately. Returns None when the
        server doesn't say.
        """
        body = self.build_request(
            model, [Message.user("hi")], [], profile, thinking=False, max_tokens=10_000_000
        )
        url = f"{self.base_url}/chat/completions"

        async def go() -> int | None:
            try:
                headers = await self.auth.get_headers()
            except AuthError:
                return None
            async with self.client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code == 200:
                    return None  # accepted: closing the stream stops generation
                err = classify_http_error(resp.status_code, await resp.aread(), resp.headers)
                return err.context_window if isinstance(err, ContextTooLong) else None

        try:
            return await asyncio.wait_for(go(), timeout)
        except (TimeoutError, httpx.HTTPError):
            return None

    # -- chat --

    def build_request(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec],
        profile: ModelProfile,
        *,
        thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        prompted = profile.tool_calling == "prompted" and bool(tools)
        # Prompted: tools described in the system prompt, calls and results as text.
        vision = bool(profile.vision)
        wire = (
            to_prompted_wire(messages, tools, vision)
            if prompted
            else to_wire_messages(messages, vision)
        )
        body: dict[str, Any] = {"model": model, "messages": wire, "stream": True}
        if tools and not prompted:
            body["tools"] = to_wire_tools(tools)
            body["tool_choice"] = "auto"
            if not profile.parallel_tool_calls:
                body["parallel_tool_calls"] = False
        if profile.stream_usage:
            body["stream_options"] = {"include_usage": True}
        if profile.temperature is not None:
            body["temperature"] = profile.temperature
        if profile.top_p is not None:
            body["top_p"] = profile.top_p
        body[profile.max_tokens_param] = max_tokens or profile.max_output
        if profile.backend == "ollama":
            # Open WebUI passes `options` to Ollama (and moves `think` to the top).
            options: dict[str, Any] = {"num_ctx": profile.context_window}
            if thinking is False and profile.thinking_switch != "none":
                options["think"] = False  # only for models that think (Ollama rejects it otherwise)
            body["options"] = options
        elif thinking is False:
            if profile.thinking_switch == "chat_template_kwargs":
                body["chat_template_kwargs"] = {"enable_thinking": False}
            elif profile.thinking_switch == "prompt":
                for item in reversed(wire):
                    if item["role"] == "user":
                        content = item["content"]
                        if isinstance(content, list):  # with images: the text part, or a new one
                            texts = [p for p in content if p.get("type") == "text"]
                            if texts:
                                texts[0]["text"] += " /no_think"
                            else:
                                content.insert(0, {"type": "text", "text": "/no_think"})
                        else:
                            item["content"] = f"{content} /no_think"
                        break
        body.update(profile.extra_body)
        return body

    async def stream_chat(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec],
        profile: ModelProfile,
        *,
        thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        body = self.build_request(
            model, messages, tools, profile, thinking=thinking, max_tokens=max_tokens
        )
        url = f"{self.base_url}/chat/completions"
        attempt = 0
        refreshed = False
        while True:
            started = False
            try:
                try:
                    headers = await self.auth.get_headers()
                except AuthError as e:
                    raise AuthFailed(str(e), hint="Run `cmcoder login`.") from e
                async with self.client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        err = self._with_key_hint(
                            classify_http_error(resp.status_code, await resp.aread(), resp.headers)
                        )
                        if (
                            resp.status_code == 401
                            and not refreshed
                            and await self.auth.on_unauthorized()
                        ):
                            refreshed = True
                            continue
                        raise err
                    started = True
                    cost = _parse_cost(resp.headers)
                    async for ev in self._parse_stream(resp, profile, body, cost, thinking):
                        yield ev
                    return
            except ProviderError as err:
                if started or not err.retryable or attempt >= self.max_retries:
                    raise
                await asyncio.sleep(_backoff(attempt, err.retry_after))
                attempt += 1
            except httpx.HTTPError as e:
                err = classify_transport_error(e, self.base_url)
                if started or not err.retryable or attempt >= self.max_retries:
                    raise err from e
                await asyncio.sleep(_backoff(attempt, None))
                attempt += 1

    async def _parse_stream(
        self,
        resp: httpx.Response,
        profile: ModelProfile,
        body: dict[str, Any],
        cost: float | None,
        thinking: bool | None = None,
    ) -> AsyncIterator[StreamEvent]:
        requested = str(body.get("model", ""))
        starts_open = thinking is not False and (
            profile.reasoning == "think-open"
            or (profile.reasoning == "auto" and requested in self.open_think_models)
        )
        splitter = (
            ThinkSplitter(starts_open=starts_open)
            if profile.reasoning in ("auto", "think-tags", "think-open")
            else None
        )
        text: list[str] = []
        field_reasoning: list[str] = []  # from reasoning_content / reasoning fields
        tag_reasoning: list[str] = []  # from <think> tags inside content
        calls: dict[int, dict[str, str]] = {}
        usage: Usage | None = None
        finish: str | None = None
        model: str | None = None

        def handle_content(piece: str) -> list[StreamEvent]:
            events: list[StreamEvent] = []
            parts = splitter.feed(piece) if splitter else [("text", piece)]
            for kind, p in parts:
                if kind == "text":
                    text.append(p)
                    events.append(TextDelta(p))
                else:
                    tag_reasoning.append(p)
                    events.append(ReasoningDelta(p))
            return events

        async for raw in resp.aiter_lines():
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                raise classify_http_error(500, json.dumps(chunk))
            model = chunk.get("model") or model
            if chunk.get("usage"):
                u = chunk["usage"]
                usage = Usage(
                    prompt_tokens=int(u.get("prompt_tokens") or 0),
                    completion_tokens=int(u.get("completion_tokens") or 0),
                )
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or choice.get("message") or {}
                r = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(r, str) and r:
                    field_reasoning.append(r)
                    yield ReasoningDelta(r)
                c = delta.get("content")
                if isinstance(c, str) and c:
                    for ev in handle_content(c):
                        yield ev
                for tc in delta.get("tool_calls") or []:
                    idx = _call_index(tc, calls)
                    entry = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        entry["id"] = str(tc["id"])
                    fn = tc.get("function") or {}
                    if fn.get("name") and not entry["name"]:
                        entry["name"] = str(fn["name"])
                        yield ToolCallStarted(idx, entry["name"])
                    args = fn.get("arguments")
                    if isinstance(args, dict):
                        entry["arguments"] += json.dumps(args)
                    elif isinstance(args, str):
                        entry["arguments"] += args
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]

        if splitter:
            for kind, p in splitter.flush():
                if kind == "text":
                    text.append(p)
                    yield TextDelta(p)
                else:
                    tag_reasoning.append(p)
                    yield ReasoningDelta(p)
        content = "".join(text)
        if splitter and starts_open and not splitter.saw_close:
            # Expected </think> never came: the model didn't think this time,
            # so what looked like reasoning is the answer.
            content = "".join(tag_reasoning) + content
            tag_reasoning = []
        elif splitter and not splitter.saw_open and CLOSE_TAG in content:
            # The template opened <think> for the model (Qwen3 Thinking-2507):
            # move the text before </think> to reasoning, and start "open"
            # for this model from now on.
            before, _, after = content.partition(CLOSE_TAG)
            tag_reasoning.insert(0, before)
            content = after.lstrip()
            self.open_think_models.add(requested)

        tool_calls: list[ToolCall] = []
        seen_ids: set[str] = set()
        for idx in sorted(calls):
            entry = calls[idx]
            if not entry["name"]:
                continue
            call_id = entry["id"]
            if not call_id or call_id in seen_ids:
                call_id = f"call_{uuid.uuid4().hex[:16]}"
            seen_ids.add(call_id)
            tool_calls.append(ToolCall(call_id, entry["name"], entry["arguments"]))

        reasoning = "".join(field_reasoning) + "".join(tag_reasoning)
        if usage is None:
            usage = Usage(
                prompt_tokens=estimate_tokens(json.dumps(body.get("messages", []))),
                completion_tokens=estimate_tokens(content + reasoning)
                + sum(estimate_tokens(tc.arguments) for tc in tool_calls),
                estimated=True,
            )
        usage.cost = cost
        msg = Message(role="assistant", content=content, tool_calls=tool_calls, reasoning=reasoning)
        yield StreamDone(message=msg, usage=usage, finish_reason=finish, model=model)


def _call_index(tc: dict[str, Any], calls: dict[int, dict[str, str]]) -> int:
    idx = tc.get("index")
    if isinstance(idx, int):
        entry = calls.get(idx)
        if entry is not None and tc.get("id") and entry["id"] and entry["id"] != tc["id"]:
            # Same index, another id: a new call (Ollama through Open WebUI
            # numbers every call 0 and gives each its own id).
            return max(calls) + 1
        return idx
    # Some servers omit `index`; match by id, else treat as a new call.
    if tc.get("id"):
        for i, entry in calls.items():
            if entry["id"] == tc["id"]:
                return i
        return len(calls)
    return max(calls) if calls else 0


def _parse_cost(headers: httpx.Headers) -> float | None:
    raw = headers.get("x-litellm-response-cost")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_embeddings(resp: httpx.Response, model: str, count: int) -> list[list[float]]:
    try:
        data = resp.json()
        items = sorted(data["data"], key=lambda d: int(d.get("index", 0)))
        vectors = [[float(x) for x in item["embedding"]] for item in items]
    except (ValueError, KeyError, TypeError) as e:
        raise BadRequest(
            f"Unexpected embeddings response for {model!r}: {e}",
            hint="Is it an embedding model? `cmcoder doctor` checks it.",
        ) from e
    if len(vectors) != count or not all(vectors):
        raise BadRequest(
            f"The gateway returned {len(vectors)} embeddings for {count} texts ({model!r}).",
            hint="Is it an embedding model? `cmcoder doctor` checks it.",
        )
    return vectors


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(max(retry_after, 0.0), 60.0)
    return min(2**attempt + random.uniform(0, 1), 20.0)
