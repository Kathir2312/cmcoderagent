"""A stand-in Ollama server for testing cmcoder through a real Open WebUI.

Speaks just enough of Ollama's API for Open WebUI (`/api/tags`, `/api/version`,
`/api/ps`, `/api/show`, `/api/chat` as NDJSON, `/api/embed` for an embedding
model) and records every chat and embed request,
so a test can check what reached "Ollama" (e.g. `options.num_ctx`). Tool calls
are sent like older Ollama versions do: all in one message, without an index.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cmcoder.testing.mock_server import fake_embedding


class FakeOllama:
    def __init__(
        self,
        model: str,
        script: list[dict[str, Any]],
        context_length: int = 40960,
        embed_model: str = "nomic-embed-text",
    ):
        self.model = model
        self.embed_model = embed_model
        self.embed_requests: list[dict[str, Any]] = []
        self.script = list(script)
        self.context_length = context_length
        self.requests: list[dict[str, Any]] = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self) -> FakeOllama:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _reply_lines(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        reply = self.script.pop(0) if self.script else {"content": "(script exhausted)"}
        model = body.get("model", self.model)
        lines: list[dict[str, Any]] = []
        if reply.get("tool_calls"):
            calls = [
                {"function": {"name": c["name"], "arguments": c.get("arguments", {})}}
                for c in reply["tool_calls"]
            ]
            message = {"role": "assistant", "content": "", "tool_calls": calls}
            lines.append({"model": model, "message": message, "done": False})
        if reply.get("thinking"):
            message = {"role": "assistant", "content": "", "thinking": reply["thinking"]}
            lines.append({"model": model, "message": message, "done": False})
        if reply.get("content"):
            message = {"role": "assistant", "content": reply["content"]}
            lines.append({"model": model, "message": message, "done": False})
        lines.append(
            {
                "model": model,
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 1234,
                "eval_count": 56,
            }
        )
        return lines

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, data: bytes, content_type: str, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _json(self, data: Any, status: int = 200) -> None:
                self._send(json.dumps(data).encode(), "application/json", status)

            def do_HEAD(self) -> None:
                self._send(b"", "text/plain")

            def do_GET(self) -> None:
                if self.path.startswith("/api/tags"):
                    details = {"family": "qwen3", "parameter_size": "8B"}
                    models = [
                        {
                            "name": name,
                            "model": name,
                            "modified_at": "2026-01-01T00:00:00Z",
                            "size": 1,
                            "digest": "d",
                            "details": details,
                        }
                        for name in (fake.model, fake.embed_model)
                    ]
                    self._json({"models": models})
                elif self.path.startswith("/api/version"):
                    self._json({"version": "0.12.0"})
                elif self.path.startswith("/api/ps"):
                    self._json({"models": []})
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw or b"{}")
                if self.path.startswith("/api/show"):
                    info = {
                        "general.architecture": "qwen3",
                        "qwen3.context_length": fake.context_length,
                    }
                    self._json(
                        {
                            "model_info": info,
                            "parameters": "",
                            "template": "",
                            "details": {"family": "qwen3"},
                            "capabilities": ["completion", "tools", "thinking"],
                        }
                    )
                elif self.path.startswith("/api/embed"):
                    fake.embed_requests.append(body)
                    texts = body.get("input")
                    texts = [texts] if isinstance(texts, str) else texts or []
                    self._json(
                        {
                            "model": body.get("model"),
                            "embeddings": [fake_embedding(t) for t in texts],
                        }
                    )
                elif self.path.startswith("/api/chat"):
                    fake.requests.append(body)
                    lines = fake._reply_lines(body)
                    data = "".join(json.dumps(x) + "\n" for x in lines).encode()
                    self._send(data, "application/x-ndjson")
                else:
                    self._json({"error": "not found"}, 404)

        return Handler
