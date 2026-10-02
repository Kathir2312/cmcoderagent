from __future__ import annotations

import asyncio
import json
import ntpath
import os
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cmcoder.core import sessions
from cmcoder.core.agent import Agent
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.core.sessions import (
    INTERRUPTED_RESULT,
    SessionLog,
    cleanup,
    find_session,
    list_sessions,
    load,
    project_key,
)
from cmcoder.providers.messages import Message, ToolCall
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider
from .test_cli import cli


def make_agent(server: Any, project: Path, session: SessionLog | None) -> Agent:
    return Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("bypassPermissions"),
        ToolContext(cwd=project, project_root=project),
        "system prompt",
        session=session,
    )


def check_pairs(messages: list[Message]) -> None:
    pending: set[str] = set()
    for m in messages:
        if m.role == "tool":
            assert m.tool_call_id in pending
            pending.discard(m.tool_call_id or "")
        else:
            assert not pending
            pending = {c.id for c in m.tool_calls}
    assert not pending


READ_SCRIPT = [
    {"tool_calls": [{"name": "Read", "arguments": {"file_path": "a.txt"}}]},
    {"content": "It says hello."},
]


async def test_turns_are_saved_and_resumed(mock_server: Any, project: Path) -> None:
    (project / "a.txt").write_text("hello\n")
    server = mock_server([*READ_SCRIPT, {"content": "You asked about a.txt."}])
    first = make_agent(server, project, SessionLog(project))
    [e async for e in first.run("what is in a.txt?")]
    await first.close()

    info = list_sessions(project)[0]
    assert info.session_id == first.session_id
    assert info.title == "what is in a.txt?"
    if sys.platform != "win32":
        assert stat.S_IMODE(info.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(info.path.parent.stat().st_mode) == 0o700
    saved, meta = load(info.path)
    assert [m.role for m in saved] == ["user", "assistant", "tool", "assistant"]
    assert saved[0].turn == 1 and meta["model"] == "qwen3-27b"
    assert "system prompt" not in info.path.read_text()  # rebuilt on resume, not saved

    second = make_agent(server, project, None)
    second.resume(saved, SessionLog(project, info.session_id))
    [e async for e in second.run("what did I ask?")]
    await second.close()
    sent = server.requests[-1]["messages"]
    assert sent[0]["content"] == "system prompt"
    assert any(m["role"] == "user" and m["content"] == "what is in a.txt?" for m in sent)
    again, _ = load(info.path)
    assert len(again) == 6 and again[-2].turn == 2  # appended to the same file


def test_crash_mid_tool_is_repaired(project: Path) -> None:
    log = SessionLog(project)
    call = ToolCall("c1", "Bash", '{"command": "sleep 99"}')
    msgs = [
        Message.system("s"),
        Message.user("run it"),
        Message(role="assistant", tool_calls=[call]),
    ]
    log.start(project, "m")
    log.save(msgs)
    with log.path.open("a", encoding="utf-8") as f:
        f.write('{"type": "message", "message": {"role": "tool", "con')  # cut off by the crash
    loaded, _ = load(log.path)
    assert loaded[-1].role == "tool" and loaded[-1].content == INTERRUPTED_RESULT
    check_pairs(loaded)


async def test_ctrl_c_mid_tool_saves_a_valid_transcript(mock_server: Any, project: Path) -> None:
    server = mock_server([{"tool_calls": [{"name": "Bash", "arguments": {"command": "sleep 30"}}]}])
    agent = make_agent(server, project, SessionLog(project))

    async def consume() -> None:
        async for _ in agent.run("wait"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(1.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await agent.close()
    loaded, _ = load(list_sessions(project)[0].path)
    check_pairs(loaded)
    assert loaded[-1].content == "[Request interrupted by the user]"


async def test_compaction_and_clear(mock_server: Any, project: Path) -> None:
    (project / "a.txt").write_text("hello\n")
    server = mock_server(list(READ_SCRIPT))
    agent = make_agent(server, project, SessionLog(project))
    [e async for e in agent.run("what is in a.txt?")]
    [e async for e in agent.compact()]
    old_id = agent.session_id
    loaded, _ = load(SessionLog(project, old_id).path)
    assert len(loaded) == 2 and loaded[0].content.startswith("[Summary")  # reset record
    agent.clear()
    assert agent.session_id != old_id
    await agent.close()
    assert find_session(project, old_id[:8]) is not None  # the old one stays resumable


def test_windows_paths_map_to_one_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, C:\\Repo and c:\\repo\\ are the same folder."""
    monkeypatch.setattr(sessions, "os", SimpleNamespace(path=ntpath))
    a = project_key(Path("C:\\Users\\Dev\\Repo"))
    b = project_key(Path("c:\\users\\dev\\repo\\"))
    c = project_key(Path("C:\\Users\\Dev\\Other"))
    assert a.endswith(b.rsplit("-", 1)[1]) and a != c


def test_cleanup_removes_old_sessions(project: Path) -> None:
    old, new = SessionLog(project), SessionLog(project)
    for log in (old, new):
        log.start(project, "m")
        log.save([Message.system("s"), Message.user("hi")])
    checkpoints = old.path.with_suffix(".checkpoints")
    (checkpoints / "blobs").mkdir(parents=True)
    (checkpoints / "blobs" / "x").write_text("x")
    long_ago = time.time() - 40 * 86400
    os.utime(old.path, (long_ago, long_ago))
    assert cleanup(30) == 1
    assert not old.path.exists() and not checkpoints.exists() and new.path.exists()


def test_cli_continue_and_resume(mock_server: Any, project: Path, tmp_path: Path) -> None:
    server = mock_server([{"content": "First answer."}, {"content": "Second answer."}])
    env = {"CMCODER_CONFIG_DIR": str(tmp_path / "cfg")}
    r1 = cli(["-p", "remember the word apple"], project, server, env)
    assert r1.returncode == 0, r1.stderr
    r2 = cli(["-p", "--continue", "which word?"], project, server, env)
    assert r2.returncode == 0, r2.stderr
    contents = [m["content"] for m in server.requests[1]["messages"]]
    assert "remember the word apple" in contents and "First answer." in contents
    r3 = cli(["-p", "--resume", "nope", "x"], project, server, env)
    assert r3.returncode == 2 and "No single session" in r3.stderr


def test_sessions_can_be_turned_off(mock_server: Any, project: Path, tmp_path: Path) -> None:
    server = mock_server([{"content": "ok"}])
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "settings.json").write_text(json.dumps({"persistSessions": False}))
    r = cli(["-p", "hi"], project, server, {"CMCODER_CONFIG_DIR": str(cfg)})
    assert r.returncode == 0, r.stderr
    assert not (cfg / "projects").exists()
