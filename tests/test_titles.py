from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from cmcoder.core.agent import Agent
from cmcoder.core.compaction import Summarizer
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.core.sessions import SessionLog, list_sessions
from cmcoder.core.titles import clean_title
from cmcoder.providers.openai_compat import ServerError
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider


@pytest.mark.parametrize(
    ("raw", "title"),
    [
        ("Fix the login bug", "Fix the login bug"),
        ('"Add retry logic to client."', "Add retry logic to client"),
        ("Title: Refactor settings loader\n\nThis title describes…", "Refactor settings loader"),
        ("**Rename get_usr everywhere**", "Rename get_usr everywhere"),
        ("\n\n  ", None),
        ("x" * 200, "x" * 60),
    ],
)
def test_clean_title(raw: str, title: str | None) -> None:
    assert clean_title(raw) == title


class FailingProvider:
    name = "broken"

    async def stream_chat(self, *a: Any, **kw: Any) -> AsyncIterator[Any]:
        raise ServerError("down")
        yield  # pragma: no cover


def make_agent(
    server: Any, project: Path, summarizer: Summarizer | None, session: bool = True
) -> Agent:
    return Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy(),
        ToolContext(cwd=project, project_root=project),
        "test",
        session=SessionLog(project) if session else None,
        summarizer=summarizer,
    )


async def test_small_model_titles_the_session_once(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "Looking."}, {"content": "Done."}])
    small = Summarizer(make_provider(server), "qwen3-7b", resolve_profile("qwen3-7b"))
    agent = make_agent(server, project, small)
    [e async for e in agent.run("the login page rejects valid passwords, please fix it")]
    [e async for e in agent.run("also add a test")]
    await agent.close()  # waits briefly for the background title
    requests = server.state.title_requests
    assert len(requests) == 1  # only after the first turn
    assert requests[0]["model"] == "qwen3-7b"
    assert requests[0]["max_tokens"] == 40
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert list_sessions(project)[0].title == "Fix the login bug"


async def test_no_small_model_no_title_request(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "ok"}])
    agent = make_agent(server, project, None)
    [e async for e in agent.run("rename get_usr to get_user")]
    await agent.close()
    assert not server.state.title_requests
    assert list_sessions(project)[0].title == "rename get_usr to get_user"  # fallback


async def test_a_failing_title_never_disturbs_the_session(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "ok"}])
    broken = Summarizer(FailingProvider(), "qwen3-7b", resolve_profile("qwen3-7b"))
    agent = make_agent(server, project, broken)
    events = [e async for e in agent.run("hello")]
    await agent.close()
    assert events[-1].subtype == "success"  # type: ignore[union-attr]
    assert list_sessions(project)[0].title == "hello"


async def test_no_session_no_title(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "ok"}])
    small = Summarizer(make_provider(server), "qwen3-7b", resolve_profile("qwen3-7b"))
    agent = make_agent(server, project, small, session=False)
    [e async for e in agent.run("hello")]
    await agent.close()
    assert not server.state.title_requests
