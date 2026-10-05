"""Critique: a critic agent reviews the answer before the user sees it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cmcoder.core.critic import CRITIC_PROMPT, critic_command, turn_diff
from cmcoder.protocol import events as ev

from .test_subagents_parallel import Scripted, make_agent, run

REVIEW = "Review this answer before it is shown to the user."


def verdict(
    v: str, summary: str = "ok", issues: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"calls": [("Verdict", {"verdict": v, "summary": summary, "issues": issues or []})]}


def shown_text(events: list[Any]) -> str:
    """What the user is shown of the answers (the scripted model doesn't
    stream, so it's the assistant messages' text)."""
    return "".join(e.text for e in events if isinstance(e, ev.AssistantMessage | ev.AssistantDelta))


def reviews(events: list[Any]) -> list[ev.ReviewResult]:
    return [e for e in events if isinstance(e, ev.ReviewResult)]


async def test_a_passing_answer_is_shown_after_the_review(project: Path) -> None:
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    provider = Scripted(
        {
            "fix add": [
                {"calls": [("Read", {"file_path": "calc.py"})]},
                {
                    "calls": [
                        (
                            "Edit",
                            {"file_path": "calc.py", "old_string": "a - b", "new_string": "a + b"},
                        )
                    ]
                },
                {"content": "Fixed add() in calc.py."},
            ],
            REVIEW: [
                {"calls": [("Read", {"file_path": "calc.py"})]},
                verdict("pass", "add() now adds."),
                {"content": "Passed."},
            ],
        }
    )
    agent = make_agent(provider, project, critique=True)
    agent.policy.mode = "acceptEdits"
    events = await run(agent, "fix add")

    assert shown_text(events) == "Fixed add() in calc.py."
    [review] = reviews(events)
    assert (review.verdict, review.final, review.round) == ("pass", True, 1)
    # The answer is released only after the review.
    order = [type(e).__name__ for e in events]
    answer = len(order) - 1 - order[::-1].index("AssistantMessage")
    assert answer > order.index("SubagentStatus")
    assert order.index("ReviewResult") > answer
    assert events[-1].review == {
        "verdict": "pass",
        "rounds": 1,
        "summary": "add() now adds.",
        "issues": [],
    }
    # The critic: read-only tools plus Verdict, the main model, the turn's diff.
    critic_requests = [r for r in provider.requests if r[0] == REVIEW]
    tools = set(critic_requests[0][1])
    assert tools == {"Read", "Glob", "Grep", "Verdict"}
    first = critic_requests[0][2]
    assert first[0].content.startswith(CRITIC_PROMPT[:40])
    prompt = first[1].content
    assert "## The user's request\nfix add" in prompt
    assert "## The answer (draft)\nFixed add() in calc.py." in prompt
    assert "-    return a - b\n+    return a + b" in prompt
    assert "- Edit(" in prompt
    # It shows in the agent map like a subagent.
    run_ = agent.subagent_runs[-1]
    assert run_.agent_type == "critic" and run_.state == "done"
    assert run_.description == "Review the answer (round 1/2)"


async def test_a_failing_answer_is_fixed_and_reviewed_again(project: Path) -> None:
    provider = Scripted(
        {
            "summarise": [
                {"content": "Draft with a mistake."},
                {"content": "Corrected answer."},
            ],
            REVIEW: [
                verdict(
                    "fail",
                    "It names the wrong file.",
                    [
                        {
                            "severity": "high",
                            "problem": "names a.py, the code is in b.py",
                            "where": "the answer",
                            "fix": "say b.py",
                        }
                    ],
                ),
                {"content": "Failed."},
                verdict("pass", "Now right."),
                {"content": "Passed."},
            ],
        }
    )
    agent = make_agent(provider, project, critique=True)
    events = await run(agent, "summarise")

    # The user never sees the failed draft.
    assert shown_text(events) == "Corrected answer."
    first, second = reviews(events)
    assert (first.verdict, first.final, first.round) == ("fail", False, 1)
    assert first.issues[0].problem == "names a.py, the code is in b.py"
    assert (second.verdict, second.final, second.round) == ("pass", True, 2)
    # The main agent got the findings to fix.
    main_requests = [r for r in provider.requests if r[0] == "summarise"]
    reminder = main_requests[1][2][-1].content
    assert "found problems (review 1 of 2)" in reminder and "[high] names a.py" in reminder
    assert "Fix: say b.py" in reminder
    # The second review is told what the first one found.
    second_review = [r for r in provider.requests if r[0] == REVIEW][2][2][1].content
    assert "## Problems found in the previous round" in second_review
    assert events[-1].review is not None and events[-1].review["rounds"] == 2


async def test_still_failing_after_the_last_round_is_shown_not_validated(project: Path) -> None:
    provider = Scripted(
        {
            "go": [{"content": "First."}, {"content": "Second."}],
            REVIEW: [
                verdict("fail", "Wrong.", [{"problem": "p1"}]),
                {"content": "x"},
                verdict("fail", "Still wrong.", [{"problem": "p2"}]),
                {"content": "x"},
            ],
        }
    )
    events = await run(make_agent(provider, project, critique=True), "go")
    assert shown_text(events) == "Second."
    last = reviews(events)[-1]
    assert (last.verdict, last.final, last.round, last.max_rounds) == ("fail", True, 2, 2)
    assert [i.problem for i in last.issues] == ["p2"]
    assert events[-1].subtype == "success" and events[-1].review["verdict"] == "fail"


async def test_no_verdict_shows_the_answer_unreviewed(project: Path) -> None:
    provider = Scripted(
        {"go": [{"content": "The answer."}], REVIEW: [{"content": "Looks fine to me."}]}
    )
    events = await run(make_agent(provider, project, critique=True), "go")
    assert shown_text(events) == "The answer."
    [review] = reviews(events)
    assert review.verdict == "none" and review.final
    assert "gave no verdict" in review.summary


async def test_critique_off_streams_as_before(project: Path) -> None:
    provider = Scripted({"go": [{"content": "Plain answer."}]})
    events = await run(make_agent(provider, project), "go")
    assert shown_text(events) == "Plain answer."
    assert not reviews(events) and events[-1].review is None
    assert [r[0] for r in provider.requests] == ["go"]


async def test_tool_step_text_is_shown_and_subagents_are_not_reviewed(project: Path) -> None:
    provider = Scripted(
        {
            "go": [
                {
                    "content": "Let me look.",
                    "calls": [("Task", {"description": "look", "prompt": "look around"})],
                },
                {"content": "Summary of the findings."},
            ],
            "look around": [{"content": "subagent report"}],
            REVIEW: [verdict("pass"), {"content": "ok"}],
        }
    )
    events = await run(make_agent(provider, project, critique=True), "go")
    assert shown_text(events) == "Let me look.Summary of the findings."
    # One review: the summary, not the subagent's report.
    assert [r[0] for r in provider.requests].count(
        REVIEW
    ) == 2  # the verdict call, then its last line
    assert len(reviews(events)) == 1


def test_the_turn_diff_leaves_out_secrets_and_outside_files(project: Path, tmp_path: Path) -> None:
    agent = make_agent(Scripted({}), project)
    agent.turn = 1
    (project / "a.py").write_text("x = 1\n")
    (project / ".env").write_text("KEY=old\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("o\n")
    for p in (project / "a.py", project / ".env", outside, project / "new.py"):
        agent.checkpoints.capture(1, p)
    (project / "a.py").write_text("x = 2\n")
    (project / ".env").write_text("KEY=new-secret\n")
    outside.write_text("changed\n")
    (project / "new.py").write_text("print('hi')\n")
    diff = turn_diff(agent)
    assert "-x = 1\n+x = 2" in diff
    assert "+++ b/new.py" in diff and "+print('hi')" in diff
    assert "secret" not in diff and ".env" not in diff and "outside" not in diff


def test_the_turn_diff_shows_only_changed_lines_of_a_crlf_file(project: Path) -> None:
    # A Windows file: the checkpoint and the file on disk both have CRLF.
    agent = make_agent(Scripted({}), project)
    agent.turn = 1
    (project / "w.py").write_bytes(b"x = 1\r\ny = 0\r\n")
    agent.checkpoints.capture(1, project / "w.py")
    (project / "w.py").write_bytes(b"x = 2\r\ny = 0\r\n")
    diff = turn_diff(agent)
    assert "-x = 1\n+x = 2\n y = 0" in diff
    assert "-y = 0" not in diff and "\r" not in diff


def test_critic_command_and_settings(project: Path) -> None:
    from cmcoder.config.settings import Settings

    agent = make_agent(Scripted({}), project)
    assert "Critique is off" in critic_command(agent, "")[0]
    assert "Critique is on" in critic_command(agent, "on")[0] and agent.critique
    assert "up to 2 reviews" in critic_command(agent, "")[0]
    critic_command(agent, "off")
    assert not agent.critique
    assert critic_command(agent, "maybe")[0].startswith("Usage")
    s = Settings.model_validate({})
    assert (s.critic.enabled, s.critic.max_rounds) == (False, 2)
    assert (
        Settings.model_validate({"critic": {"enabled": True, "maxRounds": 3}}).critic.max_rounds
        == 3
    )


async def test_the_critic_is_not_a_task_subagent_and_can_be_customised(project: Path) -> None:
    from cmcoder.config.settings import config_dir

    agent = make_agent(Scripted({}), project)
    task = agent.tools["Task"]
    assert "critic" not in task.spec().description  # type: ignore[attr-defined]
    folder = config_dir() / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "critic.md").write_text(
        "---\nname: critic\ndescription: ours\ntools: Read, Bash, Write\nmodel: small\n---\nCheck our standards.\n"
    )
    critic = task.runtime.critic()  # type: ignore[attr-defined]
    assert critic.prompt == "Check our standards." and critic.origin == "user"
    assert critic.tools == ["Read", "Glob", "Grep", "CodeSearch"] and critic.model is None
    assert "critic" not in task.runtime.definitions()  # type: ignore[attr-defined]
    provider = Scripted(
        {
            "go": [
                {
                    "calls": [
                        ("Task", {"description": "x", "prompt": "y", "subagent_type": "critic"})
                    ]
                },
                {"content": "done"},
            ]
        }
    )
    events = await run(make_agent(provider, project), "go")
    result = next(e for e in events if isinstance(e, ev.ToolResult) and e.name == "Task")
    assert result.is_error and "Unknown subagent_type 'critic'" in result.content


def test_review_lines_for_the_terminal() -> None:
    from cmcoder.cli.agent_map import review_lines

    fixing = ev.ReviewResult(
        round=1, max_rounds=2, verdict="fail", final=False, summary="s",
        issues=[ev.ReviewIssue(severity="high", problem="wrong file", where="a.py:3")],
    )  # fmt: skip
    assert review_lines(fixing) == [
        ("✻ The reviewer found 1 problem; fixing it (review 1/2):", "yellow"),
        ("  - [high] wrong file (a.py:3)", "dim"),
    ]
    passed = ev.ReviewResult(round=2, max_rounds=2, verdict="pass", final=True, summary="Right.")
    assert review_lines(passed) == [("✓ Reviewed: Right.", "green")]
    failed = fixing.model_copy(update={"final": True, "round": 2})
    assert review_lines(failed)[0][0] == (
        "⚠ Not validated: after 2 reviews the reviewer still found 1 problem:"
    )
    none = ev.ReviewResult(round=1, max_rounds=2, verdict="none", final=True, summary="no verdict")
    assert review_lines(none) == [("⚠ Not reviewed: no verdict", "yellow")]


def test_print_mode_with_the_critic(mock_server: Any, project: Path) -> None:
    import json

    from .test_cli import cli

    server = mock_server(
        [
            {"content": "The answer."},  # main
            {
                "tool_calls": [
                    {"name": "Verdict", "arguments": {"verdict": "pass", "summary": "Right."}}
                ]
            },
            {"content": "Done."},  # the critic's last line
        ]
    )
    r = cli(["-p", "question", "--critic", "--output-format", "json"], project, server)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["result"] == "The answer."
    assert out["review"]["verdict"] == "pass" and out["review"]["rounds"] == 1
    assert "✓ Reviewed: Right." in r.stderr
    # The critic's request: its own prompt, read-only tools and Verdict.
    critic_request = server.requests[1]
    names = {t["function"]["name"] for t in critic_request["tools"]}
    assert names == {"Read", "Glob", "Grep", "Verdict"}
    r = cli(["-p", "question", "--output-format", "json"], project, mock_server([{"content": "x"}]))
    assert json.loads(r.stdout)["review"] is None
