"""tracing_guard.py: LangChain would send cmcoder's runs (with the code they
read) to LangSmith's cloud by itself when a developer's shell has
LANGSMITH_TRACING set, and it reads the variable once, when first imported
(Phase 8 spike, E3). cmcoder holds the variables back before anything can
import LangChain, and gives them back to the commands it runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cmcoder import tracing_guard

TRACING = {
    "LANGSMITH_TRACING": "true",
    "LANGSMITH_API_KEY": "spike-not-a-key",
    "LANGCHAIN_TRACING_V2": "true",
    "LANGFUSE_SECRET_KEY": "spike-not-a-key",
}


def test_hold_moves_the_variables_aside(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing_guard, "_held", {})
    environ = {
        "PATH": "/usr/bin",
        "langsmith_tracing": "true",
        "LANGFUSE_HOST": "https://example.com",
    }
    tracing_guard.hold(environ)
    assert environ == {"PATH": "/usr/bin"}
    assert tracing_guard.held() == {
        "langsmith_tracing": "true",
        "LANGFUSE_HOST": "https://example.com",
    }


def run_python(code: str, env: dict[str, str]) -> str:
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1]


def clean_env(**extra: str) -> dict[str, str]:
    # No proxy: a local proxy would hide where a request is going.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(tracing_guard.PREFIXES) and "proxy" not in k.lower()
    }
    return {**env, **extra}


def test_cmcoder_starts_without_them_and_its_commands_get_them_back(tmp_path: Path) -> None:
    code = (
        "import json, os, pathlib, cmcoder\n"
        "from cmcoder.tools.shell import child_env\n"
        f"names = {list(TRACING)!r}\n"
        "child = child_env(pathlib.Path('.'))\n"
        "print(json.dumps([[os.environ.get(n) for n in names], [child.get(n) for n in names]]))"
    )
    own, child = json.loads(run_python(code, clean_env(**TRACING)))
    assert own == [None] * len(TRACING)
    assert child == list(TRACING.values())


# A turn through LangChain with the network blocked: every lookup of a host
# that isn't this PC is recorded and refused.
TURN = """
import json, socket, sys
seen = []
real = socket.getaddrinfo
def getaddrinfo(host, *a, **kw):
    if str(host) not in ("127.0.0.1", "localhost", "::1"):
        seen.append(str(host))
        raise OSError("network blocked by the test")
    return real(host, *a, **kw)
socket.getaddrinfo = getaddrinfo
if sys.argv[2] == "cmcoder":
    import cmcoder  # the guard
from langchain_core.tracers.langchain import wait_for_all_tracers
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="qwen3-27b", base_url=sys.argv[1], api_key="sk-test-key", max_retries=0, streaming=True)
model.invoke("hi")
wait_for_all_tracers()
print(json.dumps(sorted(set(seen))))
"""


@pytest.mark.parametrize("first", ["cmcoder", "langchain"])
def test_no_trace_leaves_the_pc(mock_server: Any, first: str) -> None:
    server = mock_server([{"content": "Hello."}, {"content": "Hello."}])
    env = clean_env(**TRACING)
    out = subprocess.run(
        [sys.executable, "-c", TURN, server.base_url, first],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    if first == "cmcoder":
        assert seen == []
    else:  # the control: without the guard LangChain does reach for LangSmith
        assert seen == ["api.smith.langchain.com"]
