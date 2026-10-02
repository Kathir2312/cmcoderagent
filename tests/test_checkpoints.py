from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cmcoder.core import checkpoints as cp_mod
from cmcoder.core.agent import Agent
from cmcoder.core.checkpoints import Checkpoints
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.core.sessions import SessionLog, load
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider


@pytest.mark.parametrize("on_disk", [True, False])
def test_capture_and_restore(tmp_path: Path, on_disk: bool) -> None:
    cp = Checkpoints(tmp_path / "cp" if on_disk else None)
    a, b = tmp_path / "a.txt", tmp_path / "new.txt"
    a.write_text("v1")
    cp.capture(1, a)
    a.write_text("v2")
    cp.capture(1, a)  # same turn: the first capture (v1) is kept
    a.write_text("v3")
    cp.capture(2, a)
    cp.capture(2, b)
    b.write_text("created")
    a.write_text("v4")
    assert {e.path for e in cp.changes_since(2)} == {str(a.resolve()), str(b.resolve())}

    if on_disk:  # survives a restart
        cp = Checkpoints(tmp_path / "cp")
    actions = cp.restore(2)
    assert a.read_text() == "v3" and not b.exists()
    assert sorted(x.action.split()[0] for x in actions) == ["deleted", "restored"]
    cp.restore(1)
    assert a.read_text() == "v1"
    assert cp.entries == []


def test_big_files_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp_mod, "MAX_FILE_BYTES", 10)
    cp = Checkpoints(None)
    big = tmp_path / "big.bin"
    big.write_text("x" * 100)
    cp.capture(1, big)
    big.write_text("changed")
    actions = cp.restore(1)
    assert actions[0].action.startswith("skipped") and big.read_text() == "changed"


def make_agent(server: Any, project: Path, session: SessionLog | None = None) -> Agent:
    return Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("bypassPermissions"),
        ToolContext(cwd=project, project_root=project),
        "test",
        session=session,
    )


def two_turns(project: Path) -> list[dict[str, Any]]:
    (project / "app.py").write_text("x = 1\n")
    return [
        # turn 1: read and edit app.py
        {"tool_calls": [{"name": "Read", "arguments": {"file_path": "app.py"}}]},
        {
            "tool_calls": [
                {
                    "name": "Edit",
                    "arguments": {
                        "file_path": "app.py",
                        "old_string": "x = 1",
                        "new_string": "x = 2",
                    },
                }
            ]
        },
        {"content": "x is 2 now."},
        # turn 2: edit again and create a file
        {
            "tool_calls": [
                {
                    "name": "Edit",
                    "arguments": {
                        "file_path": "app.py",
                        "old_string": "x = 2",
                        "new_string": "x = 3",
                    },
                },
                {"name": "Write", "arguments": {"file_path": "notes.md", "content": "# notes\n"}},
            ]
        },
        {"content": "Done."},
    ]


async def test_rewind_code_and_conversation(mock_server: Any, project: Path) -> None:
    server = mock_server(two_turns(project))
    agent = make_agent(server, project, SessionLog(project))
    try:
        [e async for e in agent.run("set x to 2")]
        [e async for e in agent.run("set x to 3 and add notes")]
        assert (project / "app.py").read_text() == "x = 3\n" and (project / "notes.md").exists()
        points = agent.rewind_points()
        assert [(t, n) for t, _, n in points] == [(1, 2), (2, 2)]  # files changed since

        actions, prompt = agent.rewind(2)
        assert (project / "app.py").read_text() == "x = 2\n"
        assert not (project / "notes.md").exists()
        assert prompt == "set x to 3 and add notes"
        assert agent.turn == 1 and agent.messages[-1].content == "x is 2 now."
        assert len(actions) == 2
        saved, _ = load(agent.session.path)  # type: ignore[union-attr]
        assert saved[-1].content == "x is 2 now."  # the session file was rewound too

        agent.rewind(1, conversation=False)  # code only
        assert (project / "app.py").read_text() == "x = 1\n"
        assert agent.messages[-1].content == "x is 2 now."
    finally:
        await agent.close()


async def test_conversation_only_keeps_files(mock_server: Any, project: Path) -> None:
    server = mock_server(two_turns(project))
    agent = make_agent(server, project)
    try:
        [e async for e in agent.run("set x to 2")]
        actions, prompt = agent.rewind(1, code=False)
    finally:
        await agent.close()
    assert actions == [] and prompt == "set x to 2"
    assert (project / "app.py").read_text() == "x = 2\n"
    assert [m.role for m in agent.messages] == ["system"]


async def test_files_outside_the_project_need_consent(
    mock_server: Any, project: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.txt"
    server = mock_server(
        [
            {
                "tool_calls": [
                    {"name": "Write", "arguments": {"file_path": str(outside), "content": "x"}}
                ]
            },
            {"content": "ok"},
        ]
    )
    agent = make_agent(server, project)
    try:
        [e async for e in agent.run("write outside")]
        actions, _ = agent.rewind(1, conversation=False)
        assert outside.exists() and "outside the project" in actions[0].action
    finally:
        await agent.close()


async def test_checkpoints_survive_resume(mock_server: Any, project: Path) -> None:
    server = mock_server(two_turns(project))
    first = make_agent(server, project, SessionLog(project))
    [e async for e in first.run("set x to 2")]
    await first.close()
    second = make_agent(server, project)
    messages, _ = load(first.session.path)  # type: ignore[union-attr]
    second.resume(messages, SessionLog(project, first.session_id))
    try:
        second.rewind(1)
    finally:
        await second.close()
    assert (project / "app.py").read_text() == "x = 1\n"
