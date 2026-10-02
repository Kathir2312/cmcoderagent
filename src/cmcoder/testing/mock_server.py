"""A scripted OpenAI-compatible server for tests, evals and CI (no GPU or API key needed).

The script is a list of replies, served in order to successive chat requests:

    [
      {"tool_calls": [{"name": "Read", "arguments": {"file_path": "app.py"}}]},
      {"content": "Done.", "reasoning": "optional <think> text"}
    ]

It also serves GET /v1/models and LiteLLM-style GET /v1/model/info, and can
require a bearer key. Every request body is recorded in `server.requests`.

Summary requests from auto-compaction (recognised by their system prompt) get
a canned summary and don't use up the script; they are also recorded in
`server.summary_requests`. With `enforce_context=True`, a request whose
prompt plus max_tokens exceeds the model's window is rejected with vLLM's
"maximum context length" error, as a real server would.

Run standalone:  python -m cmcoder.testing.mock_server --script s.json --port 8765
"""

from __future__ import annotations

import argparse
import json
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
    ) -> None:
        self.script = list(script)
        self.models = models or ["qwen3-27b", "qwen3-7b"]
        self.api_key = api_key
        self.context_window = context_window
        self.context_windows = context_windows or {}
        self.enforce_context = enforce_context
        self.summary = summary
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
            if path.endswith("/models"):
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [{"id": m, "object": "model"} for m in state.models],
                    },
                )
            elif path.endswith("/model/info"):
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

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if not self._authorized():
                return
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._json(404, {"error": {"message": f"not found: {self.path}"}})
                return
            body = json.loads(raw or b"{}")
            state.requests.append(body)
            model = body.get("model", "mock")
            # Realistic usage (~3.5 chars per token), so context handling is exercised.
            prompt_tokens = int(
                len(json.dumps(body.get("messages", [])) + json.dumps(body.get("tools", []))) / 3.5
            )
            requested = prompt_tokens + int(body.get("max_tokens") or 0)
            if state.enforce_context and requested > state.window(model):
                self._json(
                    400,
                    {
                        "error": {
                            "message": f"This model's maximum context length is "
                            f"{state.window(model)} tokens. However, you requested "
                            f"{requested} tokens. Please reduce the length of the messages."
                        }
                    },
                )
                return
            messages = body.get("messages") or [{}]
            if "<cmcoder-compaction>" in str(messages[0].get("content", "")):
                state.summary_requests.append(body)
                reply: dict[str, Any] = {"content": state.summary}
            else:
                reply = state.next_reply()
            if "error" in reply:
                err = reply["error"]
                self._json(
                    int(err.get("status", 500)), {"error": {"message": err.get("message", "error")}}
                )
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            if "cost" in reply:
                self.send_header("x-litellm-response-cost", str(reply["cost"]))
            self.end_headers()
            for c in _chunks(reply, model, state.think_tags, prompt_tokens):
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.flush()
                if reply.get("delay"):
                    time.sleep(float(reply["delay"]))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

    return Handler


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
