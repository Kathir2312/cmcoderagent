from __future__ import annotations

from typing import Any

import pytest

from cmcoder.providers.messages import (
    Message,
    ReasoningDelta,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from cmcoder.providers.openai_compat import (
    AuthFailed,
    BudgetExceeded,
    ServerError,
    to_wire_messages,
)
from cmcoder.providers.profiles import resolve_profile

from .conftest import make_provider

PROFILE = resolve_profile("qwen3-27b")
TOOL = ToolSpec("Read", "read", {"type": "object", "properties": {"file_path": {"type": "string"}}})


async def collect(provider: Any, **kw: Any) -> tuple[list[Any], StreamDone]:
    events = [
        e
        async for e in provider.stream_chat(
            "qwen3-27b", [Message.user("hi")], [TOOL], PROFILE, **kw
        )
    ]
    done = events[-1]
    assert isinstance(done, StreamDone)
    return events, done


async def test_streams_text_reasoning_and_usage(mock_server: Any) -> None:
    server = mock_server([{"content": "Hello there, friend!", "reasoning": "hmm", "cost": 0.0012}])
    provider = make_provider(server)
    events, done = await collect(provider)
    await provider.aclose()
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hello there, friend!"
    assert "".join(e.text for e in events if isinstance(e, ReasoningDelta)) == "hmm"
    assert done.message.content == "Hello there, friend!"
    assert done.message.reasoning == "hmm"
    assert done.usage.prompt_tokens > 0 and not done.usage.estimated
    assert done.usage.cost == pytest.approx(0.0012)
    assert done.model == "qwen3-27b"


async def test_think_tags_in_content_are_split_out(mock_server: Any) -> None:
    server = mock_server([{"content": "Answer", "reasoning": "secret thoughts"}], think_tags=True)
    provider = make_provider(server)
    _, done = await collect(provider)
    await provider.aclose()
    assert done.message.content == "Answer"
    assert done.message.reasoning == "secret thoughts"


async def test_assembles_streamed_tool_calls(mock_server: Any) -> None:
    server = mock_server(
        [
            {
                "tool_calls": [
                    {"name": "Read", "arguments": {"file_path": "a.py"}},
                    {"name": "Read", "arguments": {"file_path": "b.py"}},
                ]
            }
        ]
    )
    provider = make_provider(server)
    _, done = await collect(provider)
    await provider.aclose()
    calls = done.message.tool_calls
    assert [c.name for c in calls] == ["Read", "Read"]
    assert calls[0].arguments == '{"file_path": "a.py"}'
    assert calls[1].arguments == '{"file_path": "b.py"}'
    assert calls[0].id != calls[1].id
    assert done.finish_reason == "tool_calls"


async def test_request_body(mock_server: Any) -> None:
    server = mock_server([{"content": "ok"}])
    provider = make_provider(server)
    await collect(provider, thinking=False)
    await provider.aclose()
    body = server.requests[0]
    assert body["stream"] is True
    assert body["tools"][0]["function"]["name"] == "Read"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["stream_options"] == {"include_usage": True}
    assert body["parallel_tool_calls"] is False


def test_prompt_thinking_switch() -> None:
    from cmcoder.providers.openai_compat import OpenAICompatProvider

    profile = resolve_profile("qwen3-27b", overrides=[{"thinkingSwitch": "prompt"}])
    body = OpenAICompatProvider.build_request(
        None,  # type: ignore[arg-type]
        "m",
        [Message.user("hello")],
        [],
        profile,
        thinking=False,
    )
    assert body["messages"][-1]["content"] == "hello /no_think"
    assert "chat_template_kwargs" not in body


async def test_bad_key_is_auth_error(mock_server: Any) -> None:
    server = mock_server([{"content": "never"}])
    provider = make_provider(server, key="wrong")
    with pytest.raises(AuthFailed) as e:
        await collect(provider)
    await provider.aclose()
    assert e.value.hint and "cmcoder login" in e.value.hint


async def test_budget_error_is_not_retried(mock_server: Any) -> None:
    server = mock_server(
        [
            {"error": {"status": 400, "message": "Budget has been exceeded! Current cost: 10"}},
            {"content": "x"},
        ]
    )
    provider = make_provider(server)
    with pytest.raises(BudgetExceeded):
        await collect(provider)
    await provider.aclose()
    assert len(server.requests) == 1


async def test_retries_server_errors_then_succeeds(
    mock_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cmcoder.providers.openai_compat._backoff", lambda attempt, ra: 0)
    server = mock_server(
        [{"error": {"status": 503, "message": "overloaded"}}, {"content": "recovered"}]
    )
    provider = make_provider(server)
    _, done = await collect(provider)
    await provider.aclose()
    assert done.message.content == "recovered"
    assert len(server.requests) == 2


async def test_gives_up_after_max_retries(
    mock_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cmcoder.providers.openai_compat._backoff", lambda attempt, ra: 0)
    server = mock_server([{"error": {"status": 500, "message": "boom"}}] * 3)
    provider = make_provider(server, max_retries=1)
    with pytest.raises(ServerError):
        await collect(provider)
    await provider.aclose()
    assert len(server.requests) == 2


def test_wire_messages() -> None:
    msgs = [
        Message.system("sys"),
        Message(
            role="assistant", content="", tool_calls=[ToolCall("c1", "Read", "")], reasoning="r"
        ),
        Message.tool_result("c1", "Read", "data"),
    ]
    wire = to_wire_messages(msgs)
    assert wire[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}
        ],
    }
    assert "reasoning" not in str(wire)
    assert wire[2] == {"role": "tool", "tool_call_id": "c1", "content": "data"}


async def test_list_models_and_model_info(mock_server: Any) -> None:
    server = mock_server([], context_window=65536)
    provider = make_provider(server)
    assert await provider.list_models() == ["qwen3-27b", "qwen3-7b"]
    info = await provider.model_info()
    await provider.aclose()
    assert info["qwen3-27b"]["max_input_tokens"] == 65536


async def test_qwen3_thinking_2507_output_without_opening_tag(mock_server: Any) -> None:
    """The chat template adds <think>, so only </think> appears in the output."""
    server = mock_server(
        [
            {"reasoning": "first thoughts", "content": "Answer one"},
            {"reasoning": "more", "content": "Answer two"},
        ],
        think_tags="open",
    )
    provider = make_provider(server)
    _, first = await collect(provider)
    assert first.message.content == "Answer one"
    assert first.message.reasoning == "first thoughts"
    # Learned: later replies stream the reasoning as reasoning, not as text.
    events, second = await collect(provider)
    await provider.aclose()
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Answer two"
    assert "".join(e.text for e in events if isinstance(e, ReasoningDelta)) == "more"
    assert second.message.content == "Answer two"


async def test_open_think_model_that_skips_thinking_keeps_its_answer(mock_server: Any) -> None:
    server = mock_server([{"content": "Plain answer"}])
    provider = make_provider(server)
    provider.open_think_models.add("qwen3-27b")
    _, done = await collect(provider)
    await provider.aclose()
    assert done.message.content == "Plain answer"
    assert done.message.reasoning == ""


def test_litellm_auth_error_with_status_400() -> None:
    from cmcoder.providers.openai_compat import classify_http_error

    body = b'{"error": {"message": "Authentication Error, Invalid proxy server token passed", "type": "auth_error"}}'
    assert isinstance(classify_http_error(400, body), AuthFailed)
