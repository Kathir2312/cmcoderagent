"""E7, for your PC: the same checks as E1 against your real gateway and model
(Qwen3.6 on vLLM behind LiteLLM or Open WebUI). Nothing is sent anywhere else.

    $env:CMCODER_BASE_URL = "https://<your gateway>/v1"
    $env:CMCODER_API_KEY  = "<your key>"          # read here, never printed
    $env:CMCODER_MODEL    = "<your model>"
    uv run --no-project --with-requirements docs/project/phases/phase8/spike/requirements.txt `
        python docs/project/phases/phase8/spike/e7_real_gateway.py

TLS goes through the OS certificate store (truststore), as in cmcoder:
ChatOpenAI's own default uses certifi's list, which lacks a company CA."""

from __future__ import annotations

import os
import ssl

import httpx
import truststore
from common import check, finding
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

for name in list(os.environ):  # the tracing guard, before LangChain runs
    if name.startswith(("LANGSMITH_", "LANGCHAIN_TRACING", "LANGCHAIN_API_KEY")):
        del os.environ[name]

ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # the OS store; never verify=False
model = ChatOpenAI(
    model=os.environ["CMCODER_MODEL"],
    base_url=os.environ["CMCODER_BASE_URL"],
    api_key=os.environ["CMCODER_API_KEY"],
    streaming=True,
    http_client=httpx.Client(verify=ctx, timeout=120),
    http_async_client=httpx.AsyncClient(verify=ctx, timeout=120),
)


@tool("Read")
def read(file_path: str) -> str:
    """Read a text file. Returns its lines."""
    return "def add(a, b):\n    return a - b\n"


msg = model.invoke("In one sentence: why is the sky blue? Think first.")
finding(
    "reasoning returned by the gateway",
    "kept" if msg.additional_kwargs else "not kept by ChatOpenAI",
)
check("plain answer", bool(msg.content.strip()), msg.content[:80])

out = create_agent(model, [read]).invoke(
    {"messages": [("user", "Read app.py and say what add() does.")]}
)
calls = [c["name"] for m in out["messages"] for c in getattr(m, "tool_calls", [])]
check(
    "a tool call and an answer",
    "Read" in calls and bool(out["messages"][-1].content),
    f"calls: {calls}",
)
