"""E3: what reaches the network besides the gateway. Every DNS lookup and
connection to anything but this PC is recorded and refused. Each case runs
in its own process:

  plain    an agent turn with summarising, no tracing variables
  traced   the same with LANGSMITH_TRACING=true set (a developer's shell)
  guarded  the same, with the variables cleared before LangChain is imported
  late     the variables cleared only after LangChain was imported and used"""

from __future__ import annotations

import os
import socket
import subprocess
import sys

LOCAL = {"127.0.0.1", "localhost", "::1"}
TRACING = ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGCHAIN_TRACING_V2", "LANGCHAIN_API_KEY")


def guard() -> None:
    """What cmcoder would do first thing: no tracing unless allowed."""
    for name in list(os.environ):
        if name.startswith(("LANGSMITH_", "LANGCHAIN_TRACING", "LANGCHAIN_API_KEY", "LANGFUSE_")):
            del os.environ[name]


def block_network(seen: list[str]) -> None:
    real_getaddrinfo, real_connect = socket.getaddrinfo, socket.socket.connect

    def getaddrinfo(host, *a, **kw):  # type: ignore[no-untyped-def]
        if str(host) not in LOCAL:
            seen.append(f"lookup {host}")
            raise OSError("network blocked by the spike")
        return real_getaddrinfo(host, *a, **kw)

    def connect(self, address):  # type: ignore[no-untyped-def]
        if isinstance(address, tuple) and str(address[0]) not in LOCAL:
            seen.append(f"connect {address[0]}")
            raise OSError("network blocked by the spike")
        return real_connect(self, address)

    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    socket.socket.connect = connect  # type: ignore[method-assign]


def turn() -> None:
    from common import chat_model, mock
    from langchain.agents import create_agent
    from langchain.agents.middleware import SummarizationMiddleware
    from langchain_core.tools import tool
    from langchain_core.tracers.langchain import wait_for_all_tracers

    @tool("Read")
    def read(file_path: str) -> str:
        """Read a file."""
        return "x = 1\n" * 50

    server = mock(
        [{"tool_calls": [{"name": "Read", "arguments": {"file_path": "a.py"}}]}]
        + [{"content": "Summary or answer."}] * 4
    )
    model = chat_model(server)
    summarise = SummarizationMiddleware(model, trigger=("messages", 3), keep=("messages", 2))
    create_agent(model, [read], middleware=[summarise]).invoke(
        {"messages": [("user", "read a.py")]}
    )
    wait_for_all_tracers()
    server.__exit__(None, None, None)


def case(mode: str) -> None:
    seen: list[str] = []
    if mode == "guarded":
        guard()
    block_network(seen)
    turn()
    if mode == "late":
        guard()
        seen.append("--- variables cleared ---")
        turn()
    print(repr(seen))


if __name__ == "__main__" and len(sys.argv) > 1:
    case(sys.argv[1])
elif __name__ == "__main__":
    from common import check, finding

    for mode in ("plain", "traced", "guarded", "late"):
        # No proxy: a local proxy would hide where a request is going.
        env = {k: v for k, v in os.environ.items() if k not in TRACING and "proxy" not in k.lower()}
        if mode != "plain":
            env.update(LANGSMITH_TRACING="true", LANGSMITH_API_KEY="spike-not-a-key")
        out = subprocess.run(
            [sys.executable, __file__, mode], capture_output=True, text=True, env=env, timeout=120
        )
        lines = out.stdout.strip().splitlines()
        seen = (
            eval(lines[-1])
            if lines and lines[-1].startswith("[")
            else [f"crashed: {out.stderr[-300:]}"]
        )  # noqa: S307
        if mode in ("plain", "guarded"):
            check(f"{mode}: nothing but the gateway", not seen, ", ".join(seen) or "no lookups")
        else:
            from collections import Counter

            finding(
                mode, "; ".join(f"{n}x {what}" for what, n in Counter(seen).items()) or "no lookups"
            )
