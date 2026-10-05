"""The agent map: subagent status events, stopping one subagent, /agents."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from cmcoder.core.agent import STOP_REQUEST, PermissionAnswer, PermissionRequest
from cmcoder.core.subagents import agent_run_details, agents_overview, summary_line
from cmcoder.protocol import events as ev

from .test_subagents_parallel import Scripted, make_agent, run, task, task_results


def statuses(events: list[Any], task_id: str | None = None) -> list[ev.SubagentStatus]:
    return [
        e
        for e in events
        if isinstance(e, ev.SubagentStatus) and (task_id is None or e.id == task_id)
    ]


async def test_status_events_follow_a_subagent(project: Path) -> None:
    provider = Scripted(
        {
            "go": [{"calls": [task("survey", "Survey A")]}, {"content": "ok"}],
            "survey": [{"calls": [("Glob", {"pattern": "*"})]}, {"content": "found it"}],
        }
    )
    agent = make_agent(provider, project)
    events = await run(agent)

    seen = statuses(events)
    assert [s.state for s in seen][:2] == ["queued", "running"]
    assert seen[-1].state == "done"
    assert {s.id for s in seen} == {seen[0].id} and seen[0].number == 1
    assert any(s.activity == 'Glob("*")' for s in seen)
    last = seen[-1]
    assert (last.steps, last.tool_uses, last.max_steps) == (2, 1, 50)
    assert last.tokens == 30 and last.agent_type == "general-purpose"
    assert last.description == "Survey A" and last.model == "qwen3-27b"
    # The status comes before the Task's result, so the map is final when the card closes.
    order = [type(e).__name__ for e in events]
    assert order.index("SubagentStatus", len(order) - 6) < max(
        i for i, e in enumerate(events) if isinstance(e, ev.ToolResult) and e.name == "Task"
    )
    record = agent.subagent_runs[0]
    assert record.state == "done" and record.report == "found it"
    assert record.log == ['● Glob("*")']


async def test_stopping_one_subagent_keeps_the_others_going(project: Path) -> None:
    agent_box: dict[str, Any] = {}
    b_started = asyncio.Event()

    async def a_first() -> dict[str, Any]:
        await asyncio.wait_for(b_started.wait(), 5)
        stopped = agent_box["agent"].stop_subagent("2")  # stop B by its number
        assert stopped is not None and stopped.description == "B"
        return {"calls": [("Glob", {"pattern": "a*"})]}

    async def b_first() -> dict[str, Any]:
        b_started.set()
        await asyncio.sleep(0.05)  # still working when it is stopped
        return {"calls": [("Glob", {"pattern": "b*"}), ("Glob", {"pattern": "c*"})]}

    provider = Scripted(
        {
            "go": [{"calls": [task("look at A", "A"), task("look at B", "B")]}, {"content": "ok"}],
            "look at A": [a_first, {"content": "A finished"}],
            "look at B": [b_first, {"content": "B so far"}],
        }
    )
    agent = make_agent(provider, project)
    agent_box["agent"] = agent
    events = await run(agent)

    results = {r.content.splitlines()[-1]: r for r in task_results(events)}
    assert results["A finished"].content == "A finished"
    b = results["B so far"]
    assert b.content.startswith("(The user stopped this subagent") and not b.is_error
    assert "stopped by the user" in (b.summary or "")
    # B's tool calls after the stop weren't run, and its last call had no tools.
    b_requests = [(tools, msgs) for key, tools, msgs in provider.requests if key == "look at B"]
    assert len(b_requests) == 2 and b_requests[1][0] == []
    assert b_requests[1][1][-1].content == STOP_REQUEST
    skipped = [m.content for m in b_requests[1][1] if m.role == "tool"]
    assert skipped == ["Not run: the user stopped this subagent."] * 2
    states = [s.state for s in statuses(events) if s.description == "B"]
    assert "stopping" in states and states[-1] == "stopped"
    assert [r.state for r in agent.subagent_runs] == ["done", "stopped"]
    assert events[-1].subtype == "success"  # the turn went on


async def test_stopping_a_queued_subagent(project: Path) -> None:
    agent_box: dict[str, Any] = {}
    release = asyncio.Event()

    async def first() -> dict[str, Any]:
        agent = agent_box["agent"]
        await asyncio.sleep(0.05)  # let the others queue up
        queued = [r for r in agent.subagent_runs if r.state == "queued"]
        assert [r.description for r in queued] == ["third"]
        assert agent.stop_subagent(queued[0].id) is queued[0]
        assert agent.stop_subagent(queued[0].id) is None  # already stopped
        release.set()
        return {"content": "first done"}

    async def second() -> dict[str, Any]:
        await asyncio.wait_for(release.wait(), 5)
        return {"content": "second done"}

    provider = Scripted(
        {
            "go": [
                {"calls": [task("one", "first"), task("two", "second"), task("three", "third")]},
                {"content": "ok"},
            ],
            "one": [first],
            "two": [second],
            "three": [{"content": "never asked"}],
        }
    )
    agent = make_agent(provider, project, max_parallel_subagents=2)
    agent_box["agent"] = agent
    events = await run(agent)
    assert [k for k, _, _ in provider.requests].count("three") == 0
    contents = [r.content for r in task_results(events)]
    assert sorted(contents) == sorted(
        ["first done", "second done", "The user stopped this subagent before it started."]
    )
    assert [r.state for r in agent.subagent_runs] == ["done", "done", "stopped"]
    third = [s for s in statuses(events) if s.description == "third"]
    assert [s.state for s in third] == ["queued", "stopped"]


async def test_waiting_for_permission_shows_in_the_map(project: Path) -> None:
    seen: list[str] = []
    agent_box: dict[str, Any] = {}

    async def allow(req: PermissionRequest) -> PermissionAnswer:
        seen.append(agent_box["agent"].subagent_runs[0].state)
        return PermissionAnswer(allow=True)

    provider = Scripted(
        {
            "go": [{"calls": [task("write")]}, {"content": "ok"}],
            "write": [
                {"calls": [("Write", {"file_path": "a.txt", "content": "a"})]},
                {"content": "wrote"},
            ],
        }
    )
    agent = make_agent(provider, project, ask=allow)
    agent_box["agent"] = agent
    events = await run(agent)
    assert seen == ["waiting"]
    waiting = [s for s in statuses(events) if s.state == "waiting"]
    assert len(waiting) == 1 and waiting[0].activity.startswith("Write(")
    assert statuses(events)[-1].state == "done"


async def test_an_interrupted_turn_leaves_its_subagents_stopped(project: Path) -> None:
    started = asyncio.Event()

    async def hang() -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return {}

    provider = Scripted({"go": [{"calls": [task("a")]}], "a": [hang]})
    agent = make_agent(provider, project)

    async def consume() -> None:
        async for _ in agent.run("go"):
            pass

    turn = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 5)
    turn.cancel()
    await asyncio.gather(turn, return_exceptions=True)
    await agent.close()
    assert [r.state for r in agent.subagent_runs] == ["stopped"]
    assert agent.subagent_slots._value == 4  # its slot was given back


async def test_agents_overview_and_details(project: Path) -> None:
    provider = Scripted(
        {
            "go": [{"calls": [task("survey", "Survey A")]}, {"content": "ok"}],
            "survey": [{"calls": [("Glob", {"pattern": "*"})]}, {"content": "found it"}],
        }
    )
    agent = make_agent(provider, project)
    definitions = agent.tools["Task"].runtime.definitions()  # type: ignore[attr-defined]
    assert "No subagents have run" in "\n".join(agents_overview(definitions, []))
    await run(agent)

    text = "\n".join(agents_overview(definitions, agent.subagent_runs))
    assert "general-purpose (built-in) · main model · all tools" in text
    assert "explore (built-in) · subagent · Read, Glob, Grep, CodeSearch" in text
    assert "1. ✓ Survey A · done" in text and "This session: 1 run, 0 active" in text
    details = "\n".join(agent_run_details(agent.find_subagent_run("1")))  # type: ignore[arg-type]
    assert 'Steps:\n  ● Glob("*")' in details and details.endswith("Report:\nfound it")
    assert agent.find_subagent_run("#1") is agent.subagent_runs[0]
    assert agent.find_subagent_run("9") is None and agent.stop_subagent("1") is None


def test_summary_line() -> None:
    status = ev.SubagentStatus(
        id="t",
        number=1,
        description="d",
        agent_type="explore",
        model="m",
        state="running",
        steps=14,
        max_steps=100,
        tool_uses=22,
        tokens=31_400,
        elapsed_ms=72_000,
    )
    assert summary_line(status) == "explore · 14/100 steps · 22 tools · 31.4k tokens · 1m12s"
    status.tool_uses, status.tokens, status.elapsed_ms = 1, 900, 41_500
    assert summary_line(status).endswith("1 tool · 900 tokens · 41s")


async def test_tui_shows_the_map_and_stops_one_with_agents_stop(project: Path) -> None:
    from textual.widgets import Input, Static

    from cmcoder.cli.tui import CmcoderApp
    from cmcoder.config.settings import Settings

    from .test_tui import log_text, until

    box: dict[str, Any] = {}

    async def slow() -> dict[str, Any]:
        await box["agent"].subagent_runs[0]._stopped.wait()
        return {"calls": [("Glob", {"pattern": "*"})]}

    provider = Scripted(
        {
            "go": [{"calls": [task("survey", "Survey A")]}, {"content": "main done"}],
            "survey": [slow, {"content": "partial findings"}],
        }
    )
    settings = Settings.model_validate(
        {"providers": {"fake": {"baseUrl": "http://fake"}}, "model": "fake:qwen3-27b"}
    )
    app = CmcoderApp(settings)
    app.agent = box["agent"] = make_agent(provider, project)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            app.start_turn("go")
            panel = app.query_one("#agents", Static)

            def panel_text() -> str:
                return str(panel.content) if panel.display else ""

            await until(pilot, lambda: "1. Survey A" in panel_text())
            assert "/agents stop <n>" in panel_text() and "◐" in panel_text()
            app.query_one("#prompt", Input).value = "/agents stop 1"
            await pilot.press("enter")  # accepted while the turn runs
            await until(pilot, lambda: app.turn is not None and not app.turn.is_running)
            text = log_text(app)
            assert "Stopping 1. Survey A" in text
            assert "■ stopped · general-purpose" in text
            assert not panel.display  # hidden again after the turn
    finally:
        await app.agent.close()  # type: ignore[union-attr]


async def test_stop_subagent_over_stdio_and_agents_in_the_panel(
    mock_server: Any, project: Path
) -> None:
    from .test_panel_commands import command
    from .test_stdio import Agent as StdioAgent

    def call(name: str, **arguments: Any) -> dict[str, Any]:
        return {"tool_calls": [{"name": name, "arguments": arguments}]}

    server = mock_server(
        [
            call("Task", description="Survey A", prompt="survey"),  # main
            call("Write", file_path="a.txt", content="x"),  # subagent: asks permission
            {"content": "partial report"},  # subagent, stopped: its report
            {"content": "main done"},  # main
        ]
    )
    agent = await StdioAgent.start(project, server)
    try:
        await agent.next()  # system_init
        await agent.send(type="user_message", text="go")
        events: list[dict[str, Any]] = []
        while (e := await agent.next())["type"] != "permission_request":
            events.append(e)
        running = [s for s in events if s["type"] == "subagent_status"]
        assert [s["state"] for s in running][:2] == ["queued", "running"]
        task_id = running[0]["id"]
        await agent.send(type="stop_subagent", id=task_id)
        await agent.send(type="permission_response", request_id=e["request_id"], allow=True)
        while (e := await agent.next())["type"] != "result":
            events.append(e)
        states = [s["state"] for s in events if s["type"] == "subagent_status"]
        assert "waiting" in states and "stopping" in states and states[-1] == "stopped"
        report = next(x for x in events if x["type"] == "tool_result" and x["name"] == "Task")
        assert report["content"].startswith("(The user stopped this subagent")
        assert report["content"].endswith("partial report")
        assert e["result"] == "main done"
        # The stopped subagent's last request had no tools.
        assert "tools" not in server.requests[2] or not server.requests[2]["tools"]

        await agent.send(type="stop_subagent", id=task_id)  # not running any more
        assert (await agent.next())["type"] == "error"
        text = (await command(agent, "/agents"))[0]["text"]
        assert "1. ■ Survey A · stopped" in text
        details = (await command(agent, "/agents 1"))[0]["text"]
        assert "● Write(a.txt)" in details and "partial report" in details
    finally:
        await agent.close()


def status_event(id: str, number: int, state: str, **extra: Any) -> ev.SubagentStatus:
    fields: dict[str, Any] = {
        "id": id,
        "number": number,
        "description": f"Analyse {id}",
        "agent_type": "explore",
        "model": "m",
        "state": state,
        "steps": 3,
        "max_steps": 100,
        "tool_uses": 4,
        "tokens": 1200,
        "elapsed_ms": 5000,
    }
    return ev.SubagentStatus(**(fields | extra))


def test_terminal_agent_map_and_status_view() -> None:
    from rich.console import Console

    from cmcoder.cli.agent_map import AgentMap
    from cmcoder.cli.repl import StatusView

    m = AgentMap()
    m.update(status_event("core", 1, "running", activity="Read(Program.cs)"))
    m.update(status_event("api", 2, "waiting", activity="Bash(dotnet build)"))
    m.update(status_event("web", 3, "queued"))
    console = Console(record=True, width=120, color_system=None)
    console.print(StatusView(m, "Subagents working…"))
    text = console.export_text()
    assert "├─ ◐ 1. Analyse core  explore · 3/100 steps · 4 tools · 1.2k tokens · 5s" in text
    assert "│     └ Read(Program.cs)" in text
    assert "├─ ⏸ 2. Analyse api  waiting for permission · explore" in text
    assert "└─ ○ 3. Analyse web  explore · queued" in text
    assert "Ctrl+C: stop one subagent, or everything" in text
    m.update(status_event("core", 1, "done"))
    assert m.final_line("core") == "✓ done · explore · 3/100 steps · 4 tools · 1.2k tokens · 5s"
    assert m.final_line("nope") is None and len(m.active) == 2


async def test_terminal_ctrl_c_offers_to_stop_one_subagent(project: Path) -> None:
    from cmcoder.cli.factory import AgentOptions
    from cmcoder.cli.repl import Repl
    from cmcoder.config.settings import Settings

    class Answers:
        def __init__(self, *answers: str) -> None:
            self.answers = list(answers)

        async def prompt_async(self, *a: Any, **kw: Any) -> str:
            return self.answers.pop(0)

    repl = Repl(Settings.model_validate({}), AgentOptions(cwd=project))
    repl.agent = make_agent(Scripted({}), project)
    run_ = repl.agent.new_subagent_run("t1", "Analyse core", "explore", "m")
    run_.state = "running"
    repl.map.update(run_.status())
    turn = asyncio.create_task(asyncio.Event().wait())
    repl._turn_task = turn
    try:
        repl.session = Answers("1")  # type: ignore[assignment]
        repl._on_interrupt()  # subagents running: ask which one, don't cancel
        assert repl._chooser is not None
        await repl._chooser
        assert run_.stop_requested and not turn.cancelled() and repl._chooser is None

        repl.map.update(status_event("t2", 2, "running"))
        repl.session = Answers("a")  # type: ignore[assignment]
        repl._on_interrupt()
        await repl._chooser  # type: ignore[misc]
        await asyncio.sleep(0)
        assert turn.cancelled()  # "a": interrupt everything
    finally:
        turn.cancel()
        await repl.agent.close()


async def test_terminal_ctrl_c_without_subagents_interrupts(project: Path) -> None:
    from cmcoder.cli.factory import AgentOptions
    from cmcoder.cli.repl import Repl
    from cmcoder.config.settings import Settings

    repl = Repl(Settings.model_validate({}), AgentOptions(cwd=project))
    turn = asyncio.create_task(asyncio.Event().wait())
    repl._turn_task = turn
    repl._on_interrupt()
    await asyncio.sleep(0)
    assert turn.cancelled() and repl._chooser is None
