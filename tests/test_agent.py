from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from cmcoder.core.agent import Agent, PermissionAnswer, PermissionRequest, parse_tool_arguments
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.protocol import events as ev
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider


def make_agent(
    server: Any, project: Path, mode: str = "default", ask: Any = None, **kw: Any
) -> Agent:
    return Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy(mode, allow=kw.pop("allow", None)),
        ToolContext(cwd=project, project_root=project),
        "You are a test agent.",
        ask=ask,
        **kw,
    )


async def run(agent: Agent, prompt: str) -> list[ev.Event]:
    try:
        return [e async for e in agent.run(prompt)]
    finally:
        await agent.close()


def result(events: list[ev.Event]) -> ev.Result:
    r = events[-1]
    assert isinstance(r, ev.Result)
    return r


def test_parse_tool_arguments_repairs() -> None:
    assert parse_tool_arguments('{"a": 1}') == ({"a": 1}, None)
    assert parse_tool_arguments('```json\n{"a": 1,}\n```') == ({"a": 1}, None)
    assert parse_tool_arguments('"{\\"a\\": 1}"') == ({"a": 1}, None)
    assert parse_tool_arguments('{"s": "line1\nline2"}') == ({"s": "line1\nline2"}, None)
    assert parse_tool_arguments("") == ({}, None)
    args, err = parse_tool_arguments("{not json")
    assert args is None and err and "not valid JSON" in err
    assert parse_tool_arguments("[1]")[1] == "arguments must be a JSON object"


async def test_full_loop_edits_file(mock_server: Any, project: Path) -> None:
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    server = mock_server(
        [
            {"tool_calls": [{"name": "Read", "arguments": {"file_path": "calc.py"}}]},
            {
                "tool_calls": [
                    {
                        "name": "Edit",
                        "arguments": {
                            "file_path": "calc.py",
                            "old_string": "a - b",
                            "new_string": "a + b",
                        },
                    }
                ]
            },
            {"content": "Fixed the bug in `calc.py:2`."},
        ]
    )
    events = await run(make_agent(server, project, mode="acceptEdits"), "fix add")
    assert (project / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    r = result(events)
    assert (
        r.subtype == "success" and r.result == "Fixed the bug in `calc.py:2`." and r.num_turns == 3
    )
    # The model saw the tool results.
    tool_msgs = [m for m in server.requests[2]["messages"] if m["role"] == "tool"]
    assert "return a - b" in tool_msgs[0]["content"]
    assert "Edited calc.py" in tool_msgs[1]["content"]
    labels = [e.label for e in events if isinstance(e, ev.ToolUse)]
    assert labels == ["Read(calc.py)", "Edit(calc.py)"]


async def test_bad_tool_calls_become_errors_for_the_model(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {
                "tool_calls": [
                    {"name": "Nope", "arguments": {}},
                    {"name": "Read", "arguments": "{broken"},
                    {"name": "Read", "arguments": {"path": "x"}},
                ]
            },
            {"content": "ok"},
        ]
    )
    events = await run(make_agent(server, project), "go")
    errors = [e.content for e in events if isinstance(e, ev.ToolResult) and e.is_error]
    assert "Unknown tool 'Nope'" in errors[0]
    assert "not valid JSON" in errors[1]
    assert "file_path: Field required" in errors[2]
    assert result(events).subtype == "success"


async def test_headless_denies_with_hint(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "rm build/out.o"}}]},
            {"content": "I could not run it."},
        ]
    )
    events = await run(make_agent(server, project), "clean")
    denied = [e for e in events if isinstance(e, ev.PermissionDenied)]
    assert denied and "--allowedTools" in denied[0].reason
    assert "Bash(rm:*)" in denied[0].reason


async def test_headless_never_runs_high_risk_even_in_bypass(
    mock_server: Any, project: Path
) -> None:
    (project / "build").mkdir()
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "rm -rf build"}}]},
            {"content": "I could not run it."},
        ]
    )
    events = await run(make_agent(server, project, mode="bypassPermissions"), "clean")
    denied = [e for e in events if isinstance(e, ev.PermissionDenied)]
    assert denied and "high-risk" in denied[0].reason
    assert "--allowedTools" not in denied[0].reason
    assert (project / "build").is_dir()


async def test_high_risk_approval_is_never_remembered(mock_server: Any, project: Path) -> None:
    for d in ("a", "b"):
        (project / d).mkdir()
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "rm -rf a"}}]},
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "rm -rf b"}}]},
            {"content": "done"},
        ]
    )
    asked: list[PermissionRequest] = []
    saved: list[str] = []

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        # Even if a client sends "always", nothing is saved.
        return PermissionAnswer(allow=True, remember=True)

    agent = make_agent(server, project, ask=ask, on_rule_saved=saved.append)
    await run(agent, "clean")
    assert len(asked) == 2
    assert all(not r.can_remember and "high-risk" in r.reason for r in asked)
    assert saved == []
    assert not (project / "a").exists() and not (project / "b").exists()


async def test_ask_allow_always_saves_rule(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "git stash list"}}]},
            {
                "tool_calls": [
                    {"name": "Bash", "arguments": {"command": "git stash list --oneline"}}
                ]
            },
            {"content": "done"},
        ]
    )
    asked: list[PermissionRequest] = []
    saved: list[str] = []

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        asked.append(req)
        return PermissionAnswer(allow=True, remember=True)

    agent = make_agent(server, project, ask=ask, on_rule_saved=saved.append)
    events = await run(agent, "say hi")
    assert len(asked) == 1  # second command matched the saved rule
    assert saved == ["Bash(git stash:*)"]
    outputs = [e for e in events if isinstance(e, ev.ToolResult)]
    assert len(outputs) == 2
    assert not [e for e in events if isinstance(e, ev.PermissionDenied)]


async def test_user_denial_stops_turn(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {
                "tool_calls": [
                    {"name": "Write", "arguments": {"file_path": "a.txt", "content": "x"}},
                    {"name": "Write", "arguments": {"file_path": "b.txt", "content": "y"}},
                ]
            },
            {"content": "should not be requested"},
        ]
    )

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        return PermissionAnswer(allow=False)

    agent = make_agent(server, project, ask=ask)
    events = await run(agent, "write")
    assert result(events).subtype == "interrupted"
    assert len(server.requests) == 1
    assert not (project / "a.txt").exists()
    # Every tool call still has a result, so the next turn is valid.
    tool_results = [m for m in agent.messages if m.role == "tool"]
    assert len(tool_results) == 2


async def test_user_denial_with_feedback_continues(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "npm test"}}]},
            {"content": "ok, using pytest"},
        ]
    )

    async def ask(req: PermissionRequest) -> PermissionAnswer:
        return PermissionAnswer(allow=False, feedback="use pytest")

    events = await run(make_agent(server, project, ask=ask), "test")
    assert result(events).result == "ok, using pytest"
    assert "use pytest" in server.requests[1]["messages"][-1]["content"]


async def test_repeated_identical_calls_are_stopped(mock_server: Any, project: Path) -> None:
    (project / "a.txt").write_text("x")
    call = {"tool_calls": [{"name": "Read", "arguments": {"file_path": "a.txt"}}]}
    server = mock_server([call, call, call, {"content": "giving up"}])
    events = await run(make_agent(server, project), "loop")
    results = [e for e in events if isinstance(e, ev.ToolResult)]
    assert not results[0].is_error and not results[1].is_error
    assert results[2].is_error and "identical arguments" in results[2].content


async def test_max_turns(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [{"tool_calls": [{"name": "Glob", "arguments": {"pattern": f"*{i}"}}]} for i in range(5)]
    )
    events = await run(make_agent(server, project, max_turns=2), "x")
    assert result(events).subtype == "max_turns"


async def test_provider_error_reported_and_history_kept_clean(
    mock_server: Any, project: Path
) -> None:
    server = mock_server([{"error": {"status": 400, "message": "Budget has been exceeded"}}])
    agent = make_agent(server, project)
    events = await run(agent, "hi")
    err = next(e for e in events if isinstance(e, ev.Error))
    assert err.kind == "budget"
    assert result(events).is_error
    assert [m.role for m in agent.messages] == ["system"]


async def test_interrupt_repairs_history(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": "sleep 10"}}]},
        ]
    )
    agent = make_agent(server, project, mode="bypassPermissions")

    async def consume() -> None:
        async for _ in agent.run("slow"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(1.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await agent.close()
    roles = [m.role for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assert agent.messages[-1].content == "[Request interrupted by the user]"


async def test_reasoning_never_sent_back(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "first", "reasoning": "private"}, {"content": "second"}])
    agent = make_agent(server, project)
    try:
        [e async for e in agent.run("one")]
        [e async for e in agent.run("two")]
    finally:
        await agent.close()
    assert "private" not in str(server.requests[1]["messages"])


# --- Context window management (found in validation: Qwen3 at 32K) ---


def small_window_agent(server: Any, project: Path, window: int, max_output: int = 2048) -> Agent:
    profile = resolve_profile(
        "qwen3-27b", overrides=[{"contextWindow": window, "maxOutput": max_output}]
    )
    agent = make_agent(server, project, mode="bypassPermissions")
    agent.profile = profile
    agent.ctx.max_output_chars = 6_000
    return agent


async def test_max_tokens_never_exceeds_remaining_context(mock_server: Any, project: Path) -> None:
    (project / "big.txt").write_text("".join(f"row {i:05d} " + "x" * 50 + "\n" for i in range(400)))
    reads = [
        {
            "tool_calls": [
                {
                    "name": "Read",
                    "arguments": {"file_path": "big.txt", "offset": 1 + 90 * i, "limit": 90},
                }
            ]
        }
        for i in range(6)
    ]
    server = mock_server([*reads, {"content": "done"}])
    agent = small_window_agent(server, project, window=12_000, max_output=4_000)
    events = await run(agent, "read it all")
    assert result(events).subtype == "success"
    for body in server.requests:
        # The mock counts prompt tokens the way a server would (from the JSON
        # request); prompt + requested output must fit the window.
        server_prompt_tokens = len(json.dumps(body["messages"]) + json.dumps(body["tools"])) / 3.5
        assert server_prompt_tokens + body["max_tokens"] <= 12_000
    assert min(b["max_tokens"] for b in server.requests) < 4_000  # it shrank as context filled


async def test_old_tool_output_is_dropped_when_window_fills(
    mock_server: Any, project: Path
) -> None:
    from cmcoder.core.context import ELIDED_RESULT

    (project / "big.txt").write_text("".join(f"row {i:05d} " + "y" * 60 + "\n" for i in range(600)))
    reads = [
        {
            "tool_calls": [
                {
                    "name": "Read",
                    "arguments": {"file_path": "big.txt", "offset": 1 + 80 * i, "limit": 80},
                }
            ]
        }
        for i in range(7)
    ]
    server = mock_server([*reads, {"content": "done"}])
    agent = small_window_agent(server, project, window=10_000)
    agent.auto_compact = False  # the Phase 0 fallback (also used when summarising fails)
    events = await run(agent, "read everything")
    assert result(events).subtype == "success"
    warnings = [e.message for e in events if isinstance(e, ev.Warning)]
    assert any("removed" in w and "older tool output" in w for w in warnings), warnings
    last_messages = server.requests[-1]["messages"]
    tool_contents = [m["content"] for m in last_messages if m["role"] == "tool"]
    assert ELIDED_RESULT in tool_contents  # old outputs dropped...
    assert tool_contents[-1] != ELIDED_RESULT  # ...recent ones kept


async def test_server_context_error_is_retried_after_freeing_space(
    mock_server: Any, project: Path
) -> None:
    (project / "a.txt").write_text("z" * 3000)
    server = mock_server(
        [
            {"tool_calls": [{"name": "Read", "arguments": {"file_path": "a.txt"}}]},
            {"tool_calls": [{"name": "Read", "arguments": {"file_path": "a.txt", "offset": 1}}]},
            {
                "error": {
                    "status": 400,
                    "message": "This model's maximum context length is 32768 tokens. However, you requested 40000 tokens.",
                }
            },
            {"content": "recovered"},
        ]
    )
    events = await run(make_agent(server, project, mode="bypassPermissions"), "go")
    assert result(events).result == "recovered"
    assert any("exceeded" in e.message for e in events if isinstance(e, ev.Warning))


async def test_prompt_too_big_for_window_fails_clearly(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "never"}])
    agent = small_window_agent(server, project, window=4_000)
    events = await run(agent, "x" * 50_000)
    err = next(e for e in events if isinstance(e, ev.Error))
    assert err.kind == "context_length" and err.hint and "/clear" in err.hint
    assert server.requests == []  # never sent a request that can't fit
