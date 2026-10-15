"""Lab M1: LangChain's ChatOpenAI and cmcoder's CmcoderChatOpenAI, side by
side, against cmcoder's mock gateway (which checks the history as vLLM does).

    uv run python docs/learn/phase8/labs/m1_model.py

Read the output next to docs/learn/phase8/m1-model-layer.md."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from cmcoder.llm.chat_openai import REASONING
from cmcoder.llm.factory import chat_model, with_fallback
from cmcoder.providers.profiles import resolve_profile
from cmcoder.testing.mock_server import MockServer, MockState

PROFILE = resolve_profile("qwen3-27b")
BROKEN = '{"sql": "SELECT "Name" FROM t"}'  # a tool call the model got wrong
HISTORY = [
    HumanMessage("query it"),
    AIMessage(
        content="",
        invalid_tool_calls=[
            {
                "type": "invalid_tool_call",
                "id": "c1",
                "name": "Query",
                "args": BROKEN,
                "error": None,
            }
        ],
    ),
    ToolMessage("arguments are not valid JSON", tool_call_id="c1"),
    AIMessage(content=""),  # an empty reply
    HumanMessage("ping"),
]


def models(server: MockServer) -> dict[str, object]:
    return {
        "ChatOpenAI": ChatOpenAI(
            model="qwen3-27b",
            base_url=server.base_url,
            api_key="sk-lab",
            streaming=True,
            max_retries=0,
        ),
        "CmcoderChatOpenAI": chat_model(
            "qwen3-27b", PROFILE, base_url=server.base_url, api_key="sk-lab", max_retries=0
        ),
    }


def section(title: str) -> None:
    print(f"\n== {title}")


with MockServer(
    MockState([{"content": "Blue.", "reasoning": "Rayleigh scattering."}] * 2)
) as server:
    section("1. The model's reasoning (vLLM's reasoning_content field)")
    for name, m in models(server).items():
        reply = m.invoke("why is the sky blue?")  # type: ignore[attr-defined]
        print(
            f"{name:18} answer={reply.content!r}  reasoning={reply.additional_kwargs.get(REASONING)!r}"
        )

with MockServer(
    MockState([{"content": "Blue.", "reasoning": "Rayleigh."}] * 2, think_tags=True)
) as server:
    section("2. A server without a reasoning parser: <think> in the text")
    for name, m in models(server).items():
        streamed = "".join(str(c.content) for c in m.stream("why?"))  # type: ignore[attr-defined]
        print(f"{name:18} streamed to the user: {streamed!r}")

with MockServer(MockState([{"content": "pong"}] * 2)) as server:
    section("3. A history with a broken tool call and an empty reply")
    for name, m in models(server).items():
        try:
            print(f"{name:18} reply={m.invoke(HISTORY).content!r}")  # type: ignore[attr-defined]
            print(f"{'':18} roles sent: {[x['role'] for x in server.requests[-1]['messages']]}")
        except Exception as e:  # what vLLM does with a broken past tool call
            print(f"{name:18} {type(e).__name__}: {str(e)[:90]}")

with MockServer(
    MockState([{"error": {"status": 500, "message": "fast model down"}}, {"content": "from main"}])
) as server:
    section("4. with_fallbacks: the fast model fails, the main one answers")
    fast = chat_model(
        "qwen3-7b", PROFILE, base_url=server.base_url, api_key="sk-lab", max_retries=0
    )
    main = chat_model(
        "qwen3-27b", PROFILE, base_url=server.base_url, api_key="sk-lab", max_retries=0
    )
    print(
        "reply:",
        with_fallback(fast, main).invoke("a title?").content,
        "| models asked:",
        [r["model"] for r in server.requests],
    )
