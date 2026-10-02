"""`cmcoder --protocol stdio`: the real CLI as a long-lived subprocess, driven
like the VS Code extension drives it."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from cmcoder.protocol.events import parse_event
from cmcoder.protocol.messages import parse_message

from .conftest import API_KEY

TIMEOUT = 30


class Agent:
    """A `cmcoder --protocol stdio` process."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.events: list[dict[str, Any]] = []

    @classmethod
    async def start(cls, cwd: Path, server: Any, *args: str) -> Agent:
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        env.update(
            {
                "CMCODER_BASE_URL": server.base_url,
                "CMCODER_API_KEY": API_KEY,
                "CMCODER_MODEL": "qwen3-27b",
            }
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "cmcoder",
            "--protocol",
            "stdio",
            *args,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return cls(proc)

    async def send(self, **message: Any) -> None:
        parse_message(json.dumps(message))  # the tests only send valid messages
        await self.send_raw(json.dumps(message))

    async def send_raw(self, line: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(line.encode() + b"\n")
        await self.proc.stdin.drain()

    async def next(self) -> dict[str, Any]:
        assert self.proc.stdout
        line = await asyncio.wait_for(self.proc.stdout.readline(), TIMEOUT)
        assert line, f"agent exited: {await self.stderr()}"
        event = json.loads(line)  # every stdout line is a JSON event...
        parse_event(event)  # ...that matches the protocol
        self.events.append(event)
        return event

    async def until(self, type_: str) -> dict[str, Any]:
        while (event := await self.next())["type"] != type_:
            pass
        return event

    async def stderr(self) -> str:
        assert self.proc.stderr
        return (await self.proc.stderr.read()).decode(errors="replace")

    async def close(self) -> int:
        if self.proc.returncode is None:
            assert self.proc.stdin
            self.proc.stdin.close()
            try:
                return await asyncio.wait_for(self.proc.wait(), TIMEOUT)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        return self.proc.returncode or 0


def write(path: str, content: str) -> dict[str, Any]:
    return {"tool_calls": [{"name": "Write", "arguments": {"file_path": path, "content": content}}]}


async def test_a_conversation_over_stdio(mock_server: Any, project: Path) -> None:
    plan = [{"content": "Write it", "status": "in_progress", "activeForm": "Writing it"}]
    server = mock_server(
        [
            # turn 1: a write denied with feedback, then allowed with "always"
            write("a.py", "x = 1\n"),
            write("a.py", "x = 2\n"),
            {"content": "Wrote a.py."},
            # turn 2: a todo list, no permission needed
            {"tool_calls": [{"name": "TodoWrite", "arguments": {"todos": plan}}]},
            {"content": "Planned."},
            # turn 3: interrupted while waiting for permission
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "touch b.txt"}}]},
            # turn 4: still works after the interrupt
            {"content": "Still here."},
        ]
    )
    agent = await Agent.start(project, server)
    try:
        init = await agent.next()
        assert init["type"] == "system_init" and init["protocol_version"] == 1
        assert init["permission_mode"] == "default"

        await agent.send(type="user_message", text="create a.py")
        ask = await agent.until("permission_request")
        assert ask["name"] == "Write" and ask["input"]["file_path"] == "a.py"
        await agent.send(
            type="permission_response",
            request_id=ask["request_id"],
            allow=False,
            feedback="use x = 2",
        )
        denied = await agent.until("permission_denied")
        assert denied["name"] == "Write"

        ask = await agent.until("permission_request")
        await agent.send(type="user_message", text="busy?")  # one turn at a time
        assert (await agent.until("error"))["kind"] == "busy"
        await agent.send(
            type="permission_response", request_id=ask["request_id"], allow=True, remember=True
        )
        result = await agent.until("result")
        assert result["subtype"] == "success" and result["result"] == "Wrote a.py."
        assert (project / "a.py").read_text() == "x = 2\n"
        rules = json.loads((project / ".cmcoder/settings.local.json").read_text())
        assert rules["permissions"]["allow"] == [ask["suggested_rule"]]

        await agent.send(type="user_message", text="plan it")
        todos = await agent.until("todo_update")
        assert todos["todos"] == plan
        assert (await agent.until("result"))["result"] == "Planned."

        await agent.send(type="user_message", text="touch b")
        ask = await agent.until("permission_request")
        assert ask["name"] == "Bash"
        await agent.send(type="interrupt")
        result = await agent.until("result")
        assert result["subtype"] == "interrupted"
        assert not (project / "b.txt").exists()
        await agent.send(
            type="permission_response", request_id=ask["request_id"], allow=True
        )  # too late: the request was cancelled
        assert (await agent.until("error"))["kind"] == "protocol"

        await agent.send(type="user_message", text="still there?")
        assert (await agent.until("result"))["result"] == "Still here."

        await agent.send(type="shutdown")
        assert await agent.close() == 0
    finally:
        await agent.close()
    # The model saw the denial feedback and an interrupted, repaired history.
    sent = json.dumps(server.requests[1]["messages"])
    assert "use x = 2" in sent


async def test_modes_models_and_bad_messages(mock_server: Any, project: Path) -> None:
    server = mock_server([])
    agent = await Agent.start(project, server, "--permission-mode", "plan")
    try:
        assert (await agent.next())["permission_mode"] == "plan"
        await agent.send(type="set_mode", mode="acceptEdits")
        assert (await agent.next()) == {"type": "mode_changed", "mode": "acceptEdits"}
        await agent.send(type="set_mode", mode="yolo")
        assert (await agent.next())["kind"] == "invalid_mode"

        await agent.send(type="set_model", model="qwen3-8b")
        changed = await agent.next()
        assert changed["type"] == "model_changed" and changed["model"] == "qwen3-8b"
        await agent.send_raw('{"type": "set_model", "model": ""}')
        assert (await agent.next())["kind"] == "protocol"

        await agent.send_raw("not json")
        assert (await agent.next())["kind"] == "protocol"
        await agent.send_raw('{"type": "dance"}')
        assert (await agent.next())["kind"] == "protocol"
        await agent.send_raw('{"type": "user_message", "text": ""}')
        assert (await agent.next())["kind"] == "protocol"
        await agent.send_raw("")  # blank lines are ignored
    finally:
        assert await agent.close() == 0  # EOF ends the process cleanly


async def test_startup_errors_are_events(project: Path) -> None:
    class NoServer:
        base_url = "http://127.0.0.1:9/v1"

    agent = await Agent.start(project, NoServer(), "--permission-mode", "nonsense")
    try:
        assert await agent.close() == 2  # rejected before starting: usage error on stderr
        assert "--permission-mode" in await agent.stderr()
    finally:
        await agent.close()


async def test_resumed_conversation_sends_its_todos(mock_server: Any, project: Path) -> None:
    plan = [{"content": "Fix it", "status": "pending"}]
    server = mock_server(
        [
            {"tool_calls": [{"name": "TodoWrite", "arguments": {"todos": plan}}]},
            {"content": "Planned."},
        ]
    )
    first = await Agent.start(project, server)
    try:
        await first.next()
        await first.send(type="user_message", text="plan")
        await first.until("result")
    finally:
        await first.close()
    second = await Agent.start(project, server, "--continue")
    try:
        await second.next()
        assert (await second.next()) == {"type": "todo_update", "todos": plan}
    finally:
        await second.close()
