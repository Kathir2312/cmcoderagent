"""Subagents running in parallel, and a subagent's report at its step limit."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from cmcoder.cli.agent_map import ParallelTasks
from cmcoder.core.agent import (
    STEPS_LEFT_REMINDER,
    STOPPED_WITH_OTHERS,
    Agent,
    PermissionAnswer,
    PermissionRequest,
    visible_text,
)
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.core.subagents import SubagentRuntime
from cmcoder.protocol import events as ev
from cmcoder.providers.messages import Message, StreamDone, ToolCall, Usage
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

Reply = dict[str, Any] | Callable[[], Any]


class Scripted:
    """A model that answers each conversation from its own script, keyed by the
    conversation's first user message, so parallel subagents get the right replies
    whatever order their requests come in."""

    name = "fake"
    base_url = "http://fake"

    def __init__(self, scripts: dict[str, list[Reply]]) -> None:
        self.scripts = scripts
        self.requests: list[tuple[str, list[str], list[Message]]] = []
        self._ids = 0

    async def stream_chat(
        self, model: str, messages: list[Message], tools: list[Any], profile: Any, **kw: Any
    ) -> AsyncIterator[Any]:
        key = next(m.content for m in messages if m.role == "user")
        if key not in self.scripts:  # a long first message: scripted by its start
            key = next(k for k in self.scripts if key.startswith(k))
        self.requests.append((key, [t.name for t in tools], [*messages]))
        reply = self.scripts[key].pop(0)
        if callable(reply):
            reply = await reply()
        calls = []
        for name, args in reply.get("calls", []):
            self._ids += 1
            calls.append(ToolCall(f"c{self._ids}", name, json.dumps(args)))
        message = Message(role="assistant", content=reply.get("content", ""), tool_calls=calls)
        yield StreamDone(message, Usage(prompt_tokens=10, completion_tokens=5), "stop")


def task(prompt: str, description: str = "job") -> tuple[str, dict[str, Any]]:
    return ("Task", {"description": description, "prompt": prompt})


def make_agent(provider: Scripted, project: Path, **kw: Any) -> Agent:
    return Agent(
        provider,  # type: ignore[arg-type]
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy("default"),
        ToolContext(cwd=project, project_root=project),
        "main prompt",
        subagents=SubagentRuntime(project, False),
        **kw,
    )


async def run(agent: Agent, prompt: str = "go") -> list[Any]:
    try:
        return [e async for e in asyncio_timeout(agent.run(prompt))]
    finally:
        await agent.close()


async def asyncio_timeout(gen: AsyncIterator[Any], seconds: float = 10) -> AsyncIterator[Any]:
    while True:
        try:
            yield await asyncio.wait_for(gen.__anext__(), seconds)
        except StopAsyncIteration:
            return


def task_results(events: list[Any]) -> list[ev.ToolResult]:
    return [e for e in events if isinstance(e, ev.ToolResult) and e.name == "Task"]


async def test_task_calls_in_one_reply_run_at_the_same_time(project: Path) -> None:
    both_started = asyncio.Event()
    started: list[str] = []

    def waits_for_the_other(name: str) -> Callable[[], Any]:
        async def reply() -> dict[str, Any]:
            started.append(name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 5)  # one at a time: never set
            return {"calls": [("Glob", {"pattern": f"{name}/*"})]}

        return reply

    provider = Scripted(
        {
            "go": [
                {"calls": [task("look at A", "A"), task("look at B", "B")]},
                {"content": "both"},
            ],
            "look at A": [waits_for_the_other("A"), {"content": "A report"}],
            "look at B": [waits_for_the_other("B"), {"content": "B report"}],
        }
    )
    events = await run(make_agent(provider, project))

    assert sorted(started) == ["A", "B"]
    assert {r.content for r in task_results(events)} == {"A report", "B report"}
    # The main agent gets the results in the order of its calls.
    last = provider.requests[-1][2]
    assert [visible_text(m.content) for m in last if m.role == "tool"] == ["A report", "B report"]
    assert events[-1].result == "both"
    # Each subagent's steps carry its own Task call's id.
    uses = {e.id: e for e in events if isinstance(e, ev.ToolUse) and e.name == "Task"}
    steps = [e for e in events if isinstance(e, ev.ToolUse) and e.parent_tool_use_id]
    assert {uses[s.parent_tool_use_id].input["description"] for s in steps} == {"A", "B"}
    assert {s.label for s in steps} == {'Glob("A/*")', 'Glob("B/*")'}


async def test_one_at_a_time_when_parallel_is_off(project: Path) -> None:
    provider = Scripted(
        {
            "go": [{"calls": [task("look at A"), task("look at B")]}, {"content": "done"}],
            "look at A": [{"calls": [("Glob", {"pattern": "a"})]}, {"content": "A"}],
            "look at B": [{"calls": [("Glob", {"pattern": "b"})]}, {"content": "B"}],
        }
    )
    await run(make_agent(provider, project, max_parallel_subagents=1))
    order = [key for key, _, _ in provider.requests]
    assert order == ["go", "look at A", "look at A", "look at B", "look at B", "go"]


async def test_at_most_max_parallel_subagents_at_once(project: Path) -> None:
    running = 0
    most = 0

    def busy(name: str) -> Callable[[], Any]:
        async def reply() -> dict[str, Any]:
            nonlocal running, most
            running += 1
            most = max(most, running)
            await asyncio.sleep(0.05)
            running -= 1
            return {"content": name}

        return reply

    names = [f"job {i}" for i in range(5)]
    provider = Scripted(
        {"go": [{"calls": [task(n) for n in names]}, {"content": "done"}]}
        | {n: [busy(n)] for n in names}
    )
    await run(make_agent(provider, project, max_parallel_subagents=2))
    assert most == 2
    last = provider.requests[-1][2]
    assert [visible_text(m.content) for m in last if m.role == "tool"] == names


async def test_other_tools_still_run_one_at_a_time(project: Path) -> None:
    agent = make_agent(Scripted({}), project)
    calls = [
        ToolCall("1", "Task", "{}"),
        ToolCall("2", "Task", "{}"),
        ToolCall("3", "Read", "{}"),
        ToolCall("4", "Task", "{}"),
        ToolCall("5", "Glob", "{}"),
        ToolCall("6", "Glob", "{}"),
    ]
    groups = [[c.id for c in g] for g in agent._call_groups(calls)]
    assert groups == [["1", "2"], ["3"], ["4"], ["5"], ["6"]]
    agent.max_parallel_subagents = 1
    assert len(agent._call_groups(calls)) == 6


async def test_a_denial_in_one_subagent_stops_the_others(project: Path) -> None:
    asked: list[PermissionRequest] = []

    async def deny(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=False)

    async def never() -> dict[str, Any]:
        await asyncio.Event().wait()
        return {}

    provider = Scripted(
        {
            "go": [{"calls": [task("write it"), task("think long")]}],
            "write it": [{"calls": [("Write", {"file_path": "a.txt", "content": "x"})]}],
            "think long": [never],
        }
    )
    agent = make_agent(provider, project, ask=deny)
    events = await run(agent)

    assert len(asked) == 1 and not (project / "a.txt").exists()
    results = task_results(events)
    assert any(r.content == STOPPED_WITH_OTHERS for r in results)
    assert events[-1].subtype == "interrupted"
    # Every call has a result, in order: the conversation stays valid.
    tools = [m for m in agent.messages if m.role == "tool"]
    assert [m.content for m in tools][-1] == STOPPED_WITH_OTHERS


async def test_parallel_subagents_ask_one_at_a_time(project: Path) -> None:
    asking = 0
    most = 0

    async def allow(req: PermissionRequest) -> PermissionAnswer:
        nonlocal asking, most
        asking += 1
        most = max(most, asking)
        await asyncio.sleep(0.05)
        asking -= 1
        return PermissionAnswer(allow=True)

    provider = Scripted(
        {
            "go": [{"calls": [task("write a"), task("write b")]}, {"content": "done"}],
            "write a": [
                {"calls": [("Write", {"file_path": "a.txt", "content": "a"})]},
                {"content": "wrote a"},
            ],
            "write b": [
                {"calls": [("Write", {"file_path": "b.txt", "content": "b"})]},
                {"content": "wrote b"},
            ],
        }
    )
    await run(make_agent(provider, project, ask=allow))
    assert most == 1
    assert (project / "a.txt").read_text() == "a" and (project / "b.txt").read_text() == "b"


async def test_an_answer_while_waiting_can_cover_the_next_question(project: Path) -> None:
    asked: list[PermissionRequest] = []

    async def always(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        await asyncio.sleep(0.05)
        return PermissionAnswer(allow=True, remember=True)

    command = "echo same"
    provider = Scripted(
        {
            "go": [{"calls": [task("run 1"), task("run 2")]}, {"content": "done"}],
            "run 1": [{"calls": [("Bash", {"command": command})]}, {"content": "1"}],
            "run 2": [{"calls": [("Bash", {"command": command})]}, {"content": "2"}],
        }
    )
    await run(make_agent(provider, project, ask=always))
    assert len(asked) == 1  # the second subagent's call was allowed by the first answer


async def test_a_subagent_at_its_step_limit_still_reports(project: Path) -> None:
    searches: list[Reply] = [{"calls": [("Glob", {"pattern": f"*{i}"})]} for i in range(3)]
    provider = Scripted(
        {
            "go": [{"calls": [task("survey")]}, {"content": "ok"}],
            "survey": [*searches, {"content": "Found X in a.py; didn't get to b/."}],
        }
    )
    agent = make_agent(provider, project, max_turns=50, subagent_max_turns=3)
    events = await run(agent)

    survey = [r for r in provider.requests if r[0] == "survey"]
    assert len(survey) == 4
    assert survey[2][1] != [] and survey[3][1] == []  # the last call has no tools
    assert "used all 3 model calls" in survey[3][2][-1].content
    report = task_results(events)[0]
    assert not report.is_error
    assert report.content.startswith("(The subagent used all 3 model calls")
    assert report.content.endswith("Found X in a.py; didn't get to b/.")
    assert "step limit reached" in (report.summary or "")
    assert any(isinstance(e, ev.Warning) and "asking for its report" in e.message for e in events)


async def test_tool_calls_in_the_report_call_are_ignored(project: Path) -> None:
    provider = Scripted(
        {
            "go": [{"calls": [task("survey")]}, {"content": "ok"}],
            "survey": [
                {"calls": [("Glob", {"pattern": "*"})]},
                {"content": "partial", "calls": [("Glob", {"pattern": "**"})]},
            ],
        }
    )
    events = await run(make_agent(provider, project, subagent_max_turns=1))
    report = task_results(events)[0]
    assert report.content.endswith("partial") and not report.is_error
    assert [k for k, _, _ in provider.requests].count("survey") == 2


async def test_the_main_agent_does_not_get_a_report_call(project: Path) -> None:
    provider = Scripted({"go": [{"calls": [("Glob", {"pattern": "*"})]}]})
    events = await run(make_agent(provider, project, max_turns=1))
    assert events[-1].subtype == "max_turns" and len(provider.requests) == 1


async def test_a_subagent_is_told_when_few_calls_are_left(project: Path) -> None:
    searches: list[Reply] = [{"calls": [("Glob", {"pattern": f"*{i}"})]} for i in range(12)]
    provider = Scripted(
        {"go": [{"calls": [task("survey")]}, {"content": "ok"}], "survey": searches}
    )
    await run(make_agent(provider, project, subagent_max_turns=12))
    survey = [msgs for key, _, msgs in provider.requests if key == "survey"]
    reminder = STEPS_LEFT_REMINDER.format(left=5)
    told = [i for i, msgs in enumerate(survey) if msgs[-1].content.endswith(reminder)]
    assert told == [7]  # after 7 of its 12 calls, once


def test_subagent_limits_from_settings() -> None:
    from cmcoder.config.settings import Settings

    s = Settings.model_validate({})
    assert (s.subagent_max_turns, s.max_parallel_subagents) == (100, 4)
    s = Settings.model_validate({"subagentMaxTurns": 30, "maxParallelSubagents": 1})
    assert (s.subagent_max_turns, s.max_parallel_subagents) == (30, 1)
    with pytest.raises(ValueError):
        Settings.model_validate({"maxParallelSubagents": 0})


def test_the_task_tool_mentions_parallel_runs_when_the_model_can(project: Path) -> None:
    agent = make_agent(Scripted({}), project)
    assert "run at the same time (up to 4)" in agent.tools["Task"].spec().description
    agent.profile = resolve_profile("qwen3-27b", overrides=[{"parallelToolCalls": False}])
    assert "at the same time" not in agent.tools["Task"].spec().description


def test_terminal_lines_of_parallel_subagents_are_tagged() -> None:
    tasks = ParallelTasks()

    def use(id: str, description: str) -> ev.ToolUse:
        return ev.ToolUse(id=id, name="Task", input={"description": description}, label="Task")

    step = ev.ToolUse(id="s1", name="Read", input={}, label="Read(x)", parent_tool_use_id="t1")
    # One subagent: no tags.
    assert tasks.tag(use("t1", "Analyse A")) == "" and tasks.tag(step) == ""
    # Two at once: each line says whose it is, until both are done.
    assert tasks.tag(use("t2", "Analyse B")) == ""
    assert tasks.tag(step) == "[Analyse A] "
    done = ev.ToolResult(id="t1", name="Task", content="r", is_error=False)
    assert tasks.tag(done) == "[Analyse A] "
    later = step.model_copy(update={"parent_tool_use_id": "t2"})
    assert tasks.tag(later) == "[Analyse B] "
    assert tasks.tag(done.model_copy(update={"id": "t2"})) == "[Analyse B] "
    assert tasks.tag(use("t3", "C")) == "" and not tasks.parallel


async def test_interrupting_parallel_subagents_keeps_the_conversation_valid(
    project: Path,
) -> None:
    started = asyncio.Event()
    count = 0

    async def hang() -> dict[str, Any]:
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        await asyncio.Event().wait()
        return {}

    provider = Scripted({"go": [{"calls": [task("a"), task("b")]}], "a": [hang], "b": [hang]})
    agent = make_agent(provider, project)

    async def consume() -> None:
        async for _ in agent.run("go"):
            pass

    turn = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 5)
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    await agent.close()
    calls = [c.id for m in agent.messages for c in m.tool_calls]
    answered = [m.tool_call_id for m in agent.messages if m.role == "tool"]
    assert len(calls) == 2 and sorted(answered) == sorted(calls)
