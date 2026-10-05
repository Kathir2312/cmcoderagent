"""Phase 4 item 2: /help, /cost, /model, /todos and /rewind in the VS Code
panel, i.e. over `cmcoder --protocol stdio`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .test_stdio import Agent

TODOS = [{"content": "Write it", "status": "completed", "activeForm": "Writing it"}]


def write(path: str, content: str) -> dict[str, Any]:
    return {"tool_calls": [{"name": "Write", "arguments": {"file_path": path, "content": content}}]}


async def command(agent: Agent, text: str) -> list[dict[str, Any]]:
    """Send a command; the events up to its result."""
    await agent.send(type="user_message", text=text)
    events = []
    while (e := await agent.next())["type"] != "result":
        events.append(e)
    assert not e["is_error"], e
    return events


async def test_panel_commands(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            write("new.txt", "hello\n"),
            {"tool_calls": [{"name": "TodoWrite", "arguments": {"todos": TODOS}}]},
            {"content": "Created new.txt."},
        ]
    )
    agent = await Agent.start(project, server, "--permission-mode", "acceptEdits")
    try:
        await agent.next()  # system_init
        await agent.send(type="list_commands")
        names = [c["name"] for c in (await agent.until("command_list"))["commands"]]
        assert names[:10] == [
            "compact",
            "rewind",
            "model",
            "cost",
            "todos",
            "mcp",
            "index",
            "agents",
            "critic",
            "help",
        ]
        critique = (await command(agent, "/critic on"))[0]["text"]
        assert critique.startswith("Critique is on")
        assert (await command(agent, "/critic off"))[0]["text"].startswith("Critique is off")

        help_text = (await command(agent, "/help"))[0]["text"]
        assert "/rewind" in help_text and "/model [name]" in help_text

        await agent.send(type="user_message", text="create new.txt")
        await agent.until("result")
        assert (project / "new.txt").exists()
        requests = len(server.requests)

        cost = (await command(agent, "/cost"))[0]["text"]
        assert "tokens in" in cost

        events = await command(agent, "/todos")
        assert events[0] == {"type": "todo_update", "todos": TODOS}
        assert "1/1 done" in events[1]["text"]

        events = await command(agent, "/model qwen3-7b")
        assert events[0]["type"] == "model_changed" and events[0]["model"] == "qwen3-7b"
        assert "qwen3-7b (provider default)" in events[1]["text"]

        # /rewind: the points, then the client's choice.
        points = (await command(agent, "/rewind"))[0]
        assert points["type"] == "rewind_points"
        [point] = points["points"]
        assert point["text"] == "create new.txt" and point["files_changed"] == 1
        await agent.send(type="rewind", turn=point["turn"], code=True, conversation=True)
        rewound = await agent.next()
        assert rewound["type"] == "rewound" and rewound["prompt"] == "create new.txt"
        assert [a["action"] for a in rewound["actions"]] == ["deleted (created after that point)"]
        assert not (project / "new.txt").exists()
        assert (await agent.next()) == {"type": "history", "messages": []}
        assert (await agent.next()) == {"type": "todo_update", "todos": []}

        assert len(server.requests) == requests  # no command went to the model
    finally:
        await agent.close()


async def test_rewind_refuses_an_unknown_message(mock_server: Any, project: Path) -> None:
    agent = await Agent.start(project, mock_server([]))
    try:
        await agent.next()
        assert (await command(agent, "/rewind"))[0]["text"] == "Nothing to rewind to yet."
        await agent.send(type="rewind", turn=7)
        error = await agent.next()
        assert error["type"] == "error" and "No message 7" in error["message"]
    finally:
        await agent.close()
