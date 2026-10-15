"""Shared by the Phase 8 spike experiments: cmcoder's scripted mock gateway
(stdlib only, imported straight from src/) and a ChatOpenAI pointed at it.

Run an experiment with the pinned stack, from the repository root:

    uv run --no-project --with-requirements docs/project/phases/phase8/spike/requirements.txt \
        python docs/project/phases/phase8/spike/e1_chat_openai.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "src"))

from cmcoder.testing.mock_server import MockServer, MockState  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def mock(script: list[dict[str, Any]], **kw: Any) -> MockServer:
    """A running mock gateway that plays `script` and checks history like vLLM."""
    server = MockServer(MockState(list(script), **kw))
    server.__enter__()
    return server


def chat_model(server: MockServer, **kw: Any) -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model="qwen3-27b",
        base_url=server.base_url,
        api_key="sk-spike",  # the mock takes any key
        streaming=True,
        max_retries=0,
        **kw,
    )


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def finding(name: str, detail: str) -> None:
    """An observation, not a pass/fail: what the library does."""
    RESULTS.append((name, True, detail))
    print(f"NOTE  {name}: {detail}")
