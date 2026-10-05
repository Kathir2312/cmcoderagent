"""Critique: a critic agent checks the turn's final answer before the user
sees it (on with `/critic on`, `--critic` or `critic.enabled`).

The main agent's final reply is held. A read-only `critic` subagent gets the
user's request, the draft answer, what the agent did this turn and the
turn's diff, checks them against the files, and ends with a `Verdict` tool
call. Pass: the answer is shown. Fail: its findings go back to the main
agent, which fixes the work and answers again, up to `critic.maxRounds`;
after that the answer is shown marked "not validated". The critic runs like
any subagent, so it shows in the agent map and navigator.
"""

from __future__ import annotations

import difflib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, Field

from ..protocol import events as ev
from ..providers.messages import Message
from ..sensitive import is_secret
from ..tools.base import Tool, ToolContext, ToolInput, ToolResult
from .subagents import CRITIC_NAME, AgentDefinition, TaskDone, TaskTool

if TYPE_CHECKING:
    from .agent import Agent

CRITIC_MAX_TURNS = 30  # model calls the critic may make per review
CHARS_PER_TOKEN = 4  # rough, for the review prompt's budget
PROMPT_SHARE = 0.45  # of the window, for the review prompt (the critic reads more itself)

CRITIC_PROMPT = """\
You are the reviewer (critic) of cmcoder, a coding assistant. Before an answer
is shown to the user, you check it. You can read the project (Read, Grep,
Glob, CodeSearch) but not change anything.

Check:
- Does the answer do what the user asked, completely?
- Is what it says true? Verify claims against the files instead of trusting
  them: the code it says it changed, the functions it names, the results it
  reports.
- Are the changes correct: bugs, missing cases, broken callers, an edit that
  doesn't do what the answer says?
- Does it claim work it didn't do?

Report real problems the user would want fixed, not style preferences or
optional extras. If the answer is right, pass it.

End by calling the Verdict tool exactly once: verdict "pass" or "fail", a
one-sentence summary, and for "fail" each problem with what is wrong, where
(file:line when you can) and how to fix it."""

CRITIC_DEFINITION = AgentDefinition(
    CRITIC_NAME,
    "Reviews the answer before the user sees it (when critique is on).",
    CRITIC_PROMPT,
    "built-in",
    tools=["Read", "Glob", "Grep", "CodeSearch"],
    model=None,
)


# -- the Verdict tool ------------------------------------------------------------


class Issue(BaseModel):
    severity: Literal["high", "medium", "low"] = Field(
        "medium", description="high: wrong or broken; medium: incomplete; low: minor"
    )
    problem: str = Field(description="What is wrong")
    where: str = Field("", description="file:line, or where in the answer")
    fix: str = Field("", description="How to fix it")


class VerdictInput(ToolInput):
    verdict: Literal["pass", "fail"]
    summary: str = Field(description="One sentence: why it passes or fails")
    issues: list[Issue] = Field(default_factory=list, description="For fail: each problem to fix")


class VerdictTool(Tool):
    """The critic's last call: its verdict, kept for the main agent."""

    name: ClassVar[str] = "Verdict"
    description: ClassVar[str] = (
        "Give your verdict on the answer: pass, or fail with the problems to fix. "
        "Call it exactly once, at the end of your review."
    )
    Input = VerdictInput
    read_only: ClassVar[bool] = True

    def __init__(self) -> None:
        self.result: VerdictInput | None = None

    def describe(self, args: VerdictInput, ctx: ToolContext) -> str:
        return f"Verdict({args.verdict})"

    async def run(self, args: VerdictInput, ctx: ToolContext) -> ToolResult:
        self.result = args
        n = len(args.issues)
        summary = "pass" if args.verdict == "pass" else f"fail · {n} problem{'s' if n != 1 else ''}"
        return ToolResult(
            "Verdict recorded. Reply with one short line; you're done.", summary=summary
        )


@dataclass
class Review:
    """One round's outcome."""

    verdict: Literal[
        "pass", "fail", "none"
    ]  # none: no verdict (an error, or the critic didn't say)
    summary: str = ""
    issues: list[Issue] = field(default_factory=list)

    def event(self, round_: int, max_rounds: int, final: bool) -> ev.ReviewResult:
        return ev.ReviewResult(
            round=round_,
            max_rounds=max_rounds,
            verdict=self.verdict,
            final=final,
            summary=self.summary,
            issues=[ev.ReviewIssue(**i.model_dump()) for i in self.issues],
        )


# -- what the critic is given -------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 60)] + f"\n... [{len(text) - limit + 60} more characters]"


def turn_diff(agent: Agent) -> str:
    """This turn's changes to the project's files (from the checkpoints), as
    a unified diff. Secret files and files outside the project are left out."""
    root = agent.ctx.project_root
    seen: set[str] = set()
    parts: list[str] = []
    for entry in agent.checkpoints.entries:
        if entry.turn != agent.turn or entry.path in seen or entry.skipped:
            continue
        seen.add(entry.path)
        path = Path(entry.path)
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_secret(path, root):
            continue
        try:
            before = agent.checkpoints._get(entry.blob).decode() if entry.blob else ""
            after = path.read_text(encoding="utf-8") if path.is_file() else ""
        except (OSError, UnicodeDecodeError, KeyError):
            parts.append(f"(changed: {rel}, not shown)")
            continue
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}" if entry.blob else "/dev/null",
            tofile=f"b/{rel}" if path.exists() else "/dev/null",
        )
        parts.append("".join(diff))
    return "\n".join(p for p in parts if p)


def turn_actions(messages: list[Message], start: int) -> str:
    """The turn's tool calls and the first lines of their results."""
    results = {m.tool_call_id: m.content for m in messages[start:] if m.role == "tool"}
    lines: list[str] = []
    for m in messages[start:]:
        for call in m.tool_calls:
            args = " ".join(call.arguments.split())
            out = (results.get(call.id) or "").strip().splitlines()
            first = f" -> {out[0][:160]}" if out else ""
            lines.append(f"- {call.name}({args[:200]}){first}")
    return "\n".join(lines) or "(no tool calls)"


def review_prompt(agent: Agent, request: str, draft: str, start: int, earlier: list[Issue]) -> str:
    budget = int(agent.profile.context_window * CHARS_PER_TOKEN * PROMPT_SHARE)
    request = _clip(request, budget // 8)
    draft = _clip(draft, budget // 4)
    actions = _clip(turn_actions(agent.messages, start), budget // 8)
    used = len(request) + len(draft) + len(actions) + 1500
    diff = turn_diff(agent)
    diff_text = (
        f"```diff\n{_clip(diff, max(2000, budget - used))}\n```"
        if diff
        else "(no files changed this turn)"
    )
    again = ""
    if earlier:
        listed = "\n".join(f"- [{i.severity}] {i.problem} ({i.where})" for i in earlier)
        again = (
            "\n\n## Problems found in the previous round\n"
            f"The agent was asked to fix these; check that it did:\n{listed}"
        )
    return (
        "Review this answer before it is shown to the user.\n\n"
        f"## The user's request\n{request}\n\n"
        f"## The answer (draft)\n{draft}\n\n"
        f"## What the agent did this turn\n{actions}\n\n"
        f"## Files changed this turn\n{diff_text}{again}\n\n"
        "Read the files you need to check it, then call Verdict."
    )


def fix_request(review: Review, round_: int, max_rounds: int) -> str:
    """The reminder that sends the main agent back to work."""
    listed = (
        "\n".join(
            f"- [{i.severity}] {i.problem}"
            + (f" ({i.where})" if i.where else "")
            + (f" Fix: {i.fix}" if i.fix else "")
            for i in review.issues
        )
        or f"- {review.summary}"
    )
    return (
        f"<system-reminder>A reviewer checked your answer before the user saw it and found "
        f"problems (review {round_} of {max_rounds}):\n{listed}\n\n"
        "Fix them with your tools, then give your complete final answer again (the user "
        "hasn't seen the previous one). Don't mention the review.</system-reminder>"
    )


async def review(
    agent: Agent,
    request: str,
    draft: str,
    start: int,
    round_: int,
    max_rounds: int,
    earlier: list[Issue],
) -> AsyncIterator[ev.Event | Review]:
    """Run the critic on the draft; its events, then the Review."""
    task = agent.tools.get("Task")
    if not isinstance(task, TaskTool):
        yield Review("none", "Critique needs subagents, which aren't available here.")
        return
    call_id = f"review-{agent.turn}-{round_}"
    description = f"Review the answer (round {round_}/{max_rounds})"
    yield ev.ToolUse(
        id=call_id,
        name="Task",
        input={"description": description, "subagent_type": CRITIC_NAME},
        label=f"Review(the answer, round {round_}/{max_rounds})",
    )
    verdict = VerdictTool()
    report = ToolResult("The reviewer didn't finish.", is_error=True)
    try:
        async for item in task.run_agent(
            task.runtime.critic(),
            description,
            review_prompt(agent, request, draft, start, earlier),
            call_id,
            extra_tools=[verdict],
            max_turns=CRITIC_MAX_TURNS,
        ):
            if isinstance(item, TaskDone):
                report = item.result
            else:
                yield item
    except Exception as e:  # a broken review never loses the answer
        report = ToolResult(f"The reviewer failed: {type(e).__name__}: {e}", is_error=True)
    got = verdict.result
    if got is None:
        why = report.content.strip().splitlines()[0] if report.is_error else "it gave no verdict"
        result = Review("none", f"The reviewer couldn't finish: {why}")
    else:
        result = Review(got.verdict, got.summary, list(got.issues))
    shown = {"pass": "✓ pass", "fail": f"✗ {len(result.issues)} problem(s)", "none": "no verdict"}
    yield ev.ToolResult(
        id=call_id,
        name="Task",
        content=result.summary,
        is_error=result.verdict != "pass",
        summary=shown[result.verdict],
    )
    yield result


def critic_command(agent: Agent, arg: str) -> list[str]:
    """`/critic [on|off]` for every front end."""
    word = arg.strip().lower()
    if word in ("on", "off"):
        agent.critique = word == "on"
    elif word:
        return ["Usage: /critic, /critic on, /critic off."]
    if not agent.critique:
        return [
            "Critique is off: answers are shown as they are written.",
            "/critic on: a critic agent reviews each answer before you see it.",
        ]
    rounds = agent.critique_rounds
    return [
        "Critique is on: a critic agent reviews each answer before you see it; if it finds "
        f"problems, cmcoder fixes them and it reviews again (up to {rounds} review"
        f"{'s' if rounds != 1 else ''} per answer).",
        "/critic off turns it off.",
    ]
