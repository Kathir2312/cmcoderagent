"""llm/: the rest of the Phase 8 model layer against the mock gateway: tool
calls written as text and prompted tool calling, the fast model falling
back to the main one, structured output, and embeddings (without tiktoken)."""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from cmcoder.config.settings import Settings
from cmcoder.llm.factory import (
    chat_model,
    embeddings,
    embeddings_for,
    structured,
    with_fallback,
)
from cmcoder.providers.profiles import resolve_profile
from cmcoder.providers.transport import TransportOptions

from .conftest import API_KEY
from .test_llm_chat_openai import READ


def model(server: Any, name: str = "qwen3-27b", **changes: Any) -> Any:
    return chat_model(
        name,
        resolve_profile("qwen3-27b").model_copy(update=changes),
        base_url=server.base_url,
        api_key=API_KEY,
        transport=TransportOptions(read_timeout=10),
        max_retries=0,
    )


def text_call(name: str, args: dict[str, Any]) -> str:
    return f"<tool_call>\n{json.dumps({'name': name, 'arguments': args})}\n</tool_call>"


def test_tool_calls_written_as_text_become_tool_calls(mock_server: Any) -> None:
    """A server without a tool-call parser: Qwen's <tool_call> text arrives
    as content; known tools become tool calls, anything else stays text."""
    server = mock_server(
        [
            {"content": "Reading it.\n" + text_call("Read", {"file_path": "a.py"})},
            {"content": text_call("Nope", {})},
        ]
    )
    m = model(server).bind_tools([READ])
    reply = m.invoke("read a.py")
    assert reply.content == "Reading it."
    assert [(c["name"], c["args"]) for c in reply.tool_calls] == [("Read", {"file_path": "a.py"})]
    unknown = m.invoke("call nope")
    assert unknown.tool_calls == [] and "Nope" in str(unknown.content)


def test_prompted_tool_calling(mock_server: Any) -> None:
    """toolCalling "prompted": no `tools` parameter; the tools in the system
    prompt, past calls and results as text, the reply's call parsed."""
    server = mock_server([{"content": text_call("Read", {"file_path": "b.py"})}])
    history = [
        SystemMessage("You are cmcoder."),
        HumanMessage("read a.py and b.py"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "Read", "args": {"file_path": "a.py"}, "id": "c1", "type": "tool_call"}
            ],
        ),
        ToolMessage("print('a')", tool_call_id="c1"),
    ]
    reply = model(server, tool_calling="prompted").bind_tools([READ]).invoke(history)
    assert [(c["name"], c["args"]) for c in reply.tool_calls] == [("Read", {"file_path": "b.py"})]
    body = server.requests[-1]
    assert "tools" not in body and "tool_choice" not in body
    sent = body["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert "# Tools" in sent[0]["content"] and '"name": "Read"' in sent[0]["content"]
    assert sent[2]["content"] == text_call("Read", {"file_path": "a.py"})
    assert sent[3]["content"] == "<tool_response>\nprint('a')\n</tool_response>"


def test_the_fast_model_falls_back_to_the_main_one(mock_server: Any) -> None:
    server = mock_server(
        [{"error": {"status": 500, "message": "fast model unavailable"}}, {"content": "from main"}]
    )
    fast, main = model(server, "qwen3-7b"), model(server, "qwen3-27b")
    assert with_fallback(fast, main).invoke("title?").content == "from main"
    assert [r["model"] for r in server.requests] == ["qwen3-7b", "qwen3-27b"]


class Title(BaseModel):
    title: str


@pytest.mark.parametrize("calling", ["native", "prompted"])
def test_structured_output(mock_server: Any, calling: str) -> None:
    reply = (
        {"tool_calls": [{"name": "Title", "arguments": {"title": "Fix the login bug"}}]}
        if calling == "native"
        else {"content": text_call("Title", {"title": "Fix the login bug"})}
    )
    server = mock_server([reply])
    out = structured(model(server, tool_calling=calling), Title).invoke("name this chat")
    assert out == Title(title="Fix the login bug")


def test_embeddings_send_texts_and_need_no_internet(
    mock_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_embedding_ctx_length=False: the texts go as they are, so tiktoken
    (OpenAI's models only, tables downloaded on first use) is never needed."""
    real = socket.getaddrinfo

    def local_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        if str(host) not in ("127.0.0.1", "localhost", "::1"):
            raise OSError(f"network blocked by the test: {host}")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", local_only)
    server = mock_server([], embedding_dim=16)
    e = embeddings(
        "nomic-embed-text",
        base_url=server.base_url,
        api_key=API_KEY,
        transport=TransportOptions(read_timeout=10),
        max_retries=0,
    )
    vectors = e.embed_documents(["def add(a, b):", "class User:"])
    assert len(vectors) == 2 and all(len(v) == 16 for v in vectors)
    assert len(e.embed_query("where is login handled?")) == 16
    assert server.state.embedding_requests[0]["input"] == ["def add(a, b):", "class User:"]


def test_embeddings_from_settings(mock_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server = mock_server([], embedding_dim=8)
    settings = Settings.model_validate({"providers": {"corp": {"baseUrl": server.base_url}}})
    monkeypatch.setenv("CMCODER_API_KEY", API_KEY)
    assert len(embeddings_for(settings, "corp", "nomic-embed-text").embed_query("x")) == 8
