"""A scripted OpenAI-compatible server for tests, evals and CI (no GPU or API key needed).

The script is a list of replies, served in order to successive chat requests:

    [
      {"tool_calls": [{"name": "Read", "arguments": {"file_path": "app.py"}}]},
      {"content": "Done.", "reasoning": "optional <think> text"}
    ]

It also serves GET /v1/models and LiteLLM-style GET /v1/model/info, and can
require a bearer key. Every request body is recorded in `server.requests`.

Summary requests from auto-compaction and session-title requests
(recognised by their system prompts) get canned replies and don't use up the
script; they are recorded in `state.summary_requests` / `state.title_requests`. With `enforce_context=True`, a request whose
prompt plus max_tokens exceeds the model's window is rejected with vLLM's
"maximum context length" error, as a real server would.

Run standalone:  python -m cmcoder.testing.mock_server --script s.json --port 8765
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import ssl
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockState:
    def __init__(
        self,
        script: list[dict[str, Any]],
        models: list[str] | None = None,
        api_key: str | None = None,
        context_window: int = 32768,
        think_tags: bool | str = False,
        context_windows: dict[str, int] | None = None,
        enforce_context: bool = False,
        summary: str = "Summary (mock): the user asked for work on the project; files were read.",
        model_info: bool = True,
        title: str = "Fix the login bug",
        openwebui: dict[str, str] | None = None,
        ollama_context: dict[str, int] | None = None,
        embedding_models: list[str] | None = None,
        embedding_dim: int = 64,
    ) -> None:
        self.script = list(script)
        self.models = models or ["qwen3-27b", "qwen3-7b"]
        self.api_key = api_key
        self.context_window = context_window
        self.context_windows = context_windows or {}
        self.enforce_context = enforce_context
        self.summary = summary
        self.model_info = model_info  # False: no /model/info, like many gateways
        self.title = title
        # Open WebUI mode: model -> owned_by ("ollama" or "openai"); see _openwebui.
        self.openwebui = openwebui
        self.ollama_context = ollama_context or {}
        # Models /embeddings answers for (Open WebUI mode: also those in `openwebui`).
        self.embedding_models = (
            embedding_models
            if embedding_models is not None
            else ["text-embedding-3-small", "nomic-embed-text"]
        )
        self.embedding_dim = embedding_dim
        self.embedding_requests: list[dict[str, Any]] = []
        # Ollama models: requests whose prompt didn't fit num_ctx (Ollama drops
        # the start silently; the default num_ctx is small).
        self.truncated: list[str] = []
        self.title_requests: list[dict[str, Any]] = []
        self.think_tags = think_tags
        self.requests: list[dict[str, Any]] = []
        self.summary_requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def window(self, model: str) -> int:
        return self.context_windows.get(model, self.context_window)

    def next_reply(self) -> dict[str, Any]:
        with self.lock:
            if self.script:
                return self.script.pop(0)
        return {"content": "(mock script exhausted)"}


def _chunks(
    reply: dict[str, Any], model: str, think_tags: bool | str, prompt_tokens: int = 100
) -> list[dict[str, Any]]:
    """think_tags: False = reasoning_content field; True = <think>..</think> in content;
    "open" = like Qwen3 Thinking-2507, only the closing </think> appears."""
    cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": cid,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    out = [chunk({"role": "assistant"})]
    reasoning = reply.get("reasoning")
    if reasoning:
        if think_tags == "open":
            out.append(chunk({"content": f"{reasoning}</th"}))
            out.append(chunk({"content": "ink>\n\n"}))
        elif think_tags:
            out.append(chunk({"content": "<thi"}))
            out.append(chunk({"content": f"nk>{reasoning}</th"}))
            out.append(chunk({"content": "ink>\n\n"}))
        else:
            out.append(chunk({"reasoning_content": reasoning}))
    content = reply.get("content") or ""
    for i in range(0, len(content), 12):
        out.append(chunk({"content": content[i : i + 12]}))
    for idx, call in enumerate(reply.get("tool_calls") or []):
        args = call.get("arguments", {})
        args_text = args if isinstance(args, str) else json.dumps(args)
        out.append(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": idx,
                            "id": call.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {"name": call["name"], "arguments": ""},
                        }
                    ]
                }
            )
        )
        half = len(args_text) // 2
        for piece in (args_text[:half], args_text[half:]):
            out.append(chunk({"tool_calls": [{"index": idx, "function": {"arguments": piece}}]}))
    finish = "tool_calls" if reply.get("tool_calls") else reply.get("finish_reason", "stop")
    out.append(chunk({}, finish))
    completion = len(reply.get("content") or "") + len(json.dumps(reply.get("tool_calls") or []))
    usage = reply.get(
        "usage", {"prompt_tokens": prompt_tokens, "completion_tokens": max(1, completion // 4)}
    )
    out.append(
        {
            "id": cid,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [],
            "usage": usage,
        }
    )
    return out


OLLAMA_DEFAULT_NUM_CTX = 2048


def _ollama_chunks(reply: dict[str, Any], model: str, prompt_tokens: int) -> list[dict[str, Any]]:
    """An Ollama reply as Open WebUI converts it (utils/response.py): tool calls
    whole and all numbered 0, each with its own id; an empty chunk mid-stream
    saying finish_reason "stop"; usage with the last chunk."""
    cid = f"chatcmpl-{uuid.uuid4()}"

    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": cid,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    out = []
    if reply.get("reasoning"):
        out.append(chunk({"role": "assistant", "reasoning_content": reply["reasoning"]}))
    content = reply.get("content") or ""
    for i in range(0, len(content), 12):
        out.append(chunk({"content": content[i : i + 12]}))
    out.append(chunk({}, "stop"))  # Ollama's empty mid-stream chunk
    calls = reply.get("tool_calls") or []
    if calls:
        out.append(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call_{uuid.uuid4()}",
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c.get("arguments", {})),
                            },
                        }
                        for c in calls
                    ]
                }
            )
        )
    completion = max(1, len(content) // 4)
    last = chunk({}, "tool_calls" if calls else "stop")
    last["usage"] = {
        "input_tokens": prompt_tokens,
        "output_tokens": completion,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion,
        "total_tokens": prompt_tokens + completion,
    }
    out.append(last)
    if out[0]["choices"][0]["delta"] is not None:
        out[0]["choices"][0]["delta"]["role"] = "assistant"
    return out


def make_handler(state: MockState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _authorized(self) -> bool:
            if state.api_key is None:
                return True
            if self.headers.get("Authorization") == f"Bearer {state.api_key}":
                return True
            self._json(401, {"error": {"message": "Invalid API key", "type": "auth_error"}})
            return False

        def _json(self, status: int, data: Any) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not self._authorized():
                return
            path = self.path.rstrip("/")
            if state.openwebui is not None:
                if path in ("/api/models", "/api/v1/models"):
                    data = [
                        {"id": m, "name": m, "object": "model", "owned_by": owner}
                        for m, owner in state.openwebui.items()
                    ]
                    data.append({"id": "arena-model", "name": "Arena", "owned_by": "arena"})
                    self._json(200, {"data": data})
                else:
                    self._json(404, {"detail": "Not Found"})
                return
            if path.endswith("/models"):
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [{"id": m, "object": "model"} for m in state.models],
                    },
                )
            elif path.endswith("/model/info") and state.model_info:
                self._json(
                    200,
                    {
                        "data": [
                            {
                                "model_name": m,
                                "model_info": {
                                    "max_input_tokens": state.window(m),
                                    "max_output_tokens": 8192,
                                    "supports_function_calling": True,
                                },
                            }
                            for m in state.models
                        ]
                    },
                )
            else:
                self._json(404, {"error": {"message": f"not found: {self.path}"}})

        def _embeddings(self, raw: bytes) -> None:
            body = json.loads(raw or b"{}")
            model = body.get("model")
            texts = body.get("input")
            texts = [texts] if isinstance(texts, str) else texts
            with state.lock:
                state.embedding_requests.append(body)
            if state.openwebui is not None:
                # Open WebUI raises a plain exception for an unknown model: a bare 500.
                if model not in state.openwebui or model not in state.embedding_models:
                    body_text = b"Internal Server Error"
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body_text)))
                    self.end_headers()
                    self.wfile.write(body_text)
                    return
            elif model not in state.embedding_models:
                self._json(
                    400,
                    {"error": {"message": f"{model} is not an embedding model", "code": "400"}},
                )
                return
            data = [
                {
                    "object": "embedding",
                    "index": i,
                    "embedding": fake_embedding(t, state.embedding_dim),
                }
                for i, t in enumerate(texts or [])
            ]
            self._json(200, {"object": "list", "data": data, "model": model})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if not self._authorized():
                return
            if self.path.rstrip("/").endswith("/embeddings"):
                self._embeddings(raw)
                return
            ollama = False
            if state.openwebui is not None:
                path = self.path.rstrip("/")
                if path == "/ollama/api/show":
                    model = json.loads(raw or b"{}").get("model", "")
                    if state.openwebui.get(model) != "ollama":
                        self._json(400, {"detail": f"Model '{model}' was not found"})
                    else:
                        ctx = state.ollama_context.get(model, 40960)
                        self._json(
                            200,
                            {"model_info": {"general.architecture": "x", "x.context_length": ctx}},
                        )
                    return
                if path not in ("/api/chat/completions", "/api/v1/chat/completions"):
                    self._json(404, {"detail": "Not Found"})
                    return
                body = json.loads(raw or b"{}")
                if body.get("model") not in state.openwebui:
                    self._json(400, {"detail": "Model not found"})
                    return
                ollama = state.openwebui[body["model"]] == "ollama"
            elif not self.path.rstrip("/").endswith("/chat/completions"):
                self._json(404, {"error": {"message": f"not found: {self.path}"}})
                return
            body = json.loads(raw or b"{}")
            state.requests.append(body)
            model = body.get("model", "mock")
            # Realistic usage (~3.5 chars per token), so context handling is exercised.
            prompt_tokens = int(
                len(json.dumps(body.get("messages", [])) + json.dumps(body.get("tools", []))) / 3.5
            )
            max_tokens = int(body.get("max_tokens") or 0)
            requested = prompt_tokens + max_tokens
            if ollama:
                # Ollama never refuses: it drops what doesn't fit num_ctx.
                num_ctx = int((body.get("options") or {}).get("num_ctx") or OLLAMA_DEFAULT_NUM_CTX)
                if prompt_tokens > num_ctx:
                    state.truncated.append(str(model))
                max_tokens = 0
                requested = 0
            # Like vLLM, always reject a max_tokens larger than the window itself
            # (that is how cmcoder probes the limit); anything else only when enforcing.
            if requested > state.window(model) and (
                state.enforce_context or max_tokens >= state.window(model)
            ):
                message = (
                    f"This model's maximum context length is {state.window(model)} tokens. "
                    f"However, you requested {requested} tokens. Please reduce the length of "
                    "the messages."
                )
                self._json(
                    400,
                    {"detail": message}
                    if state.openwebui is not None
                    else {"error": {"message": message}},
                )
                return
            messages = body.get("messages") or [{}]
            if "<cmcoder-compaction>" in str(messages[0].get("content", "")):
                state.summary_requests.append(body)
                reply: dict[str, Any] = {"content": state.summary}
            elif "<cmcoder-title>" in str(messages[0].get("content", "")):
                state.title_requests.append(body)
                reply = {"content": state.title}
            else:
                reply = state.next_reply()
                # "expect": text the last message must contain (e.g. an expanded
                # slash command); otherwise the scripted step is not played.
                expected = reply.get("expect")
                if expected and expected not in str(messages[-1].get("content", "")):
                    reply = {"content": f"(mock expectation failed: {expected!r} not sent)"}
            if "error" in reply:
                err = reply["error"]
                message = err.get("message", "error")
                self._json(
                    int(err.get("status", 500)),
                    {"detail": message}
                    if state.openwebui is not None
                    else {"error": {"message": message}},
                )
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            if "cost" in reply:
                self.send_header("x-litellm-response-cost", str(reply["cost"]))
            self.end_headers()
            self.close_connection = True
            try:
                chunks = (
                    _ollama_chunks(reply, model, prompt_tokens)
                    if ollama
                    else _chunks(reply, model, state.think_tags, prompt_tokens)
                )
                for c in chunks:
                    self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                    self.wfile.flush()
                    if reply.get("delay"):
                        time.sleep(float(reply["delay"]))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client hung up (e.g. Ctrl+C, or a context-window probe)

    return Handler


_WORD = re.compile(r"[A-Za-z][a-z]*|[A-Z]+(?![a-z])|\d+")


def fake_embedding(text: str, dim: int = 64) -> list[float]:
    """A deterministic stand-in for an embedding model: words (split at
    snake_case and camelCase) hashed into `dim` signed buckets, normalised.
    Texts sharing words come out similar, which is all tests need."""
    vec = [0.0] * dim
    for word in _WORD.findall(text):
        w = word.lower()
        if len(w) < 2:
            continue
        h = hashlib.sha256(w.encode()).digest()
        vec[int.from_bytes(h[:4], "big") % dim] += 1.0 if h[4] & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class MockServer:
    def __init__(
        self,
        state: MockState,
        port: int = 0,
        tls: ssl.SSLContext | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        self.state = state
        self.host = host
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
        self.scheme = "http"
        if tls is not None:
            self.httpd.socket = tls.wrap_socket(self.httpd.socket, server_side=True)
            self.scheme = "https"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.httpd.server_address[1]}/v1"

    @property
    def root_url(self) -> str:
        """The server's address without /v1 (e.g. for Open WebUI mode)."""
        return f"{self.scheme}://{self.host}:{self.httpd.server_address[1]}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.state.requests

    def __enter__(self) -> MockServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--script", required=True, help="JSON file with the list of replies")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()
    with open(args.script, encoding="utf-8") as f:
        script = json.load(f)
    server = MockServer(MockState(script, api_key=args.api_key), port=args.port)
    print(f"mock server on {server.base_url}", flush=True)
    server.httpd.serve_forever()


if __name__ == "__main__":
    main()
