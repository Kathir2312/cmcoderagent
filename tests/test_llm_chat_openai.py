"""llm/: the Phase 8 chat model (LangChain's ChatOpenAI with cmcoder's fixes)
against the mock gateway, which checks the history the way vLLM does. The
spike's E1 found ChatOpenAI itself dropping the reasoning, resending broken
tool calls (a 400 from vLLM) and keeping empty replies."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from cmcoder.config.settings import Settings
from cmcoder.llm.chat_openai import REASONING, CmcoderChatOpenAI
from cmcoder.llm.factory import chat_model, chat_model_for
from cmcoder.providers.auth import AuthError
from cmcoder.providers.profiles import ModelProfile, resolve_profile
from cmcoder.providers.transport import TransportOptions

from .conftest import API_KEY

READ = {
    "type": "function",
    "function": {
        "name": "Read",
        "description": "Read a file.",
        "parameters": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    },
}


def profile(**changes: Any) -> ModelProfile:
    return resolve_profile("qwen3-27b").model_copy(update=changes)


def model(server: Any, **changes: Any) -> CmcoderChatOpenAI:
    return chat_model(
        "qwen3-27b",
        profile(**changes),
        base_url=server.base_url,
        api_key=API_KEY,
        transport=TransportOptions(read_timeout=10),
        max_retries=0,
    )


def test_text_tool_calls_and_usage(mock_server: Any) -> None:
    server = mock_server(
        [
            {"content": "Hello."},
            {"tool_calls": [{"id": "call_1", "name": "Read", "arguments": {"file_path": "a.py"}}]},
        ]
    )
    m = model(server)
    hello = m.invoke("hi")
    assert hello.content == "Hello."
    assert hello.usage_metadata and hello.usage_metadata["input_tokens"] > 0
    call = m.bind_tools([READ]).invoke("read a.py")
    assert isinstance(call, AIMessage)
    assert call.tool_calls == [
        {"name": "Read", "args": {"file_path": "a.py"}, "id": "call_1", "type": "tool_call"}
    ]


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
def test_reasoning_is_kept(mock_server: Any, field: str) -> None:
    """vLLM's reasoning parser: `reasoning_content`, `reasoning` in newer vLLM."""
    server = mock_server([{"content": "Blue.", "reasoning": "Rayleigh."}], reasoning_field=field)
    reply = model(server).invoke("why is the sky blue?")
    assert reply.content == "Blue."
    assert reply.additional_kwargs[REASONING] == "Rayleigh."


def test_think_tags_are_split_as_they_stream(mock_server: Any) -> None:
    """No reasoning parser on the server: <think> in the text, split even
    when a tag is cut across chunks, and never streamed as the answer."""
    server = mock_server([{"content": "Blue.", "reasoning": "Rayleigh."}] * 2, think_tags=True)
    m = model(server)
    reply = m.invoke("why?")
    assert reply.content == "Blue."
    assert reply.additional_kwargs[REASONING] == "Rayleigh."
    chunks = list(m.stream("why?"))
    assert "".join(str(c.content) for c in chunks) == "Blue."
    assert "think" not in "".join(str(c.content) for c in chunks)


def test_a_template_that_opens_think_is_learned(mock_server: Any) -> None:
    """Qwen3 Thinking-2507: only </think> comes; the text before it is
    thinking, and the model's later replies start inside the thinking."""
    server = mock_server([{"content": "Blue.", "reasoning": "Rayleigh."}] * 2, think_tags="open")
    m = model(server)
    first = m.invoke("why?")
    assert (first.content, first.additional_kwargs[REASONING]) == ("Blue.", "Rayleigh.")
    second = "".join(str(c.content) for c in m.stream("why?"))
    assert second == "Blue."  # split while streaming now


def test_history_goes_out_as_vllm_accepts_it(mock_server: Any) -> None:
    """A broken tool call, an empty reply and earlier reasoning in the history:
    the request passes the mock's vLLM check (E1c gave a 400 here)."""
    broken = '{"sql": "SELECT "Name" FROM t", "max_rows": 5}'
    server = mock_server([{"content": "pong"}])
    history = [
        HumanMessage("query it"),
        AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "type": "invalid_tool_call",
                    "id": "call_1",
                    "name": "Read",
                    "args": broken,
                    "error": None,
                }
            ],
        ),
        ToolMessage("arguments are not valid JSON", tool_call_id="call_1"),
        AIMessage(content="", additional_kwargs={REASONING: "private thoughts"}),
        AIMessage(content="Noted.", additional_kwargs={REASONING: "private thoughts"}),
        HumanMessage("ping"),
    ]
    assert model(server).invoke(history).content == "pong"
    sent = server.requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "tool", "assistant", "user"]
    args = sent[1]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(args) == {"_raw": broken}
    assert "private thoughts" not in json.dumps(sent)


def test_request_options_follow_the_profile(mock_server: Any) -> None:
    server = mock_server([{"content": "ok"}] * 5)
    serial = model(
        server, max_tokens_param="max_tokens", max_output=1234, parallel_tool_calls=False
    )
    serial.bind_tools([READ]).invoke("hi")
    body = server.requests[-1]
    assert body["max_tokens"] == 1234 and "max_completion_tokens" not in body
    assert body["parallel_tool_calls"] is False
    model(server, parallel_tool_calls=True).bind_tools([READ]).invoke("hi")
    assert "parallel_tool_calls" not in server.requests[-1]  # the server's default
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}

    model(server, thinking_switch="chat_template_kwargs").invoke("hi", thinking=False)
    assert server.requests[-1]["chat_template_kwargs"] == {"enable_thinking": False}

    model(server, thinking_switch="prompt").invoke("hi", thinking=False)
    assert server.requests[-1]["messages"][-1]["content"] == "hi /no_think"

    model(server, backend="ollama", context_window=40960).invoke("hi", thinking=False)
    assert server.requests[-1]["options"] == {"num_ctx": 40960, "think": False}


def test_langchain_learns_the_window_from_the_profile(mock_server: Any) -> None:
    m = model(mock_server([]), context_window=65536, max_output=4096)
    assert m.profile is not None
    assert (m.profile.get("max_input_tokens"), m.profile.get("max_output_tokens")) == (65536, 4096)


async def test_streamed_chunks_reach_callbacks_split(mock_server: Any) -> None:
    """What the engine streams to the user comes from the callbacks."""
    from langchain_core.callbacks import AsyncCallbackHandler

    seen: list[str] = []

    class Tokens(AsyncCallbackHandler):
        async def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
            chunk = kwargs.get("chunk")
            if chunk is not None and isinstance(chunk.message, AIMessageChunk):
                seen.append(str(chunk.message.content))

    server = mock_server([{"content": "Blue.", "reasoning": "Rayleigh."}], think_tags=True)
    reply = await model(server).ainvoke("why?", config={"callbacks": [Tokens()]})
    assert reply.content == "Blue."
    assert "".join(seen) == "Blue."


def test_the_key_is_never_shown(mock_server: Any) -> None:
    m = model(mock_server([]))
    for text in (repr(m), str(m), json.dumps(m.to_json(), default=str)):
        assert API_KEY not in text


def test_built_from_settings(mock_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server = mock_server([{"content": "ok"}])
    settings = Settings.model_validate({"providers": {"corp": {"baseUrl": server.base_url}}})
    monkeypatch.setenv("CMCODER_API_KEY", API_KEY)
    m = chat_model_for(settings, "corp", "qwen3-27b", profile())
    assert m.invoke("hi").content == "ok"
    monkeypatch.delenv("CMCODER_API_KEY")
    monkeypatch.setattr("cmcoder.llm.factory.load_stored_api_key", lambda provider: None)
    with pytest.raises(AuthError, match="cmcoder login"):
        chat_model_for(settings, "corp", "qwen3-27b", profile())


def test_company_proxies_still_apply(
    mock_server: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """cmcoder's own clients (OS certificate store, HTTPS_PROXY) are used as
    they are: langchain-openai's own transport would turn the proxy off."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    m = model(mock_server([]))
    assert m.root_client._client is m.http_client
    assert m.root_async_client._client is m.http_async_client
    assert m.http_client._trust_env and m.http_async_client._trust_env  # type: ignore[union-attr]
    assert "proxy" not in caplog.text
