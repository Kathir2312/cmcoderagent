"""Subagents: the `Task` tool hands a self-contained job to a fresh agent.

A subagent has its own conversation (so the main one stays small), its own
tools and possibly a smaller model, and returns one final report. It uses the
main agent's permission policy, prompts and hooks (PreToolUse, PostToolUse,
and SubagentStop instead of Stop), and can't start subagents itself.

Built in: `general-purpose` (every tool, the main model) and `explore`
(read-only search: Read, Glob, Grep; `subagentModel`, else `smallFastModel`).
A subagent may make `subagentMaxTurns` model calls; at the limit it writes its
report without tools. Task calls in one reply run in parallel
(`maxParallelSubagents`).
Your own are Markdown files in Claude Code's format:

    ~/.cmcoder/agents/reviewer.md     (yours, every project)
    .cmcoder/agents/reviewer.md       (this project's: only when it's trusted)

    ---
    name: reviewer
    description: Reviews a diff for bugs. Use after making changes.
    tools: Read, Grep, Glob, Bash
    model: small
    ---
    You are a careful code reviewer...

`tools` (optional) limits the tools; `mcp__server` means all of a server's
tools. `model`: `inherit` (default), `small` (smallFastModel), or a model name.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import Field

from ..config.settings import config_dir
from ..protocol import events as ev
from ..sensitive import safe_project_file
from ..tools.base import Tool, ToolContext, ToolInput, ToolResult
from .commands import MAX_FILE_CHARS, parse_file, split_tools
from .compaction import Summarizer

if TYPE_CHECKING:
    from .agent import Agent

# A model a subagent runs on: (provider, model, profile), like the summariser's.
ModelChoice = Summarizer
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
NOT_FOR_SUBAGENTS = {"Task", "TodoWrite"}  # no nesting; the todo list is the main agent's
MAX_REPORT_CHARS = 30_000

GENERAL_PURPOSE_PROMPT = """\
You are a subagent of cmcoder, a coding assistant. You were given one task by
the main agent. Do it completely with your tools, without asking questions:
nobody can answer them. Search broadly when you don't know where something is,
read the relevant files, and make changes only if the task asks for them.
Your model calls are limited: read what the task needs, not everything.

When you are done, reply with a concise report for the main agent: what you
found or did, with file paths (and line numbers when useful). That report is
the only thing the main agent sees, so include everything it needs."""

EXPLORE_PROMPT = """\
You are a read-only search subagent of cmcoder, a coding assistant. Find what
the main agent asked about, quickly: use Glob for file names, Grep for
contents, and Read for the relevant parts. You cannot change anything.

Reply with a concise report: the answer, the relevant file paths with line
numbers, and short excerpts where they help. That report is the only thing the
main agent sees."""


@dataclass
class AgentDefinition:
    name: str
    description: str
    prompt: str
    origin: Literal["built-in", "user", "project"]
    tools: list[str] | None = None  # None: every tool
    model: str | None = None  # None / "inherit": the main model
    path: Path | None = None


BUILT_IN_AGENTS = {
    "general-purpose": AgentDefinition(
        "general-purpose",
        "For multi-step tasks: research a question, find code across many files, or carry "
        "out a self-contained change, when doing it here would fill the conversation.",
        GENERAL_PURPOSE_PROMPT,
        "built-in",
    ),
    "explore": AgentDefinition(
        "explore",
        "Fast, read-only codebase search: find files, definitions and usages, and answer "
        "questions about how the code works. Say how thorough to be.",
        EXPLORE_PROMPT,
        "built-in",
        tools=["Read", "Glob", "Grep", "CodeSearch"],  # CodeSearch when the project is indexed
        model="subagent",
    ),
}


def _load_dir(
    folder: Path, origin: Literal["user", "project"], root: Path | None = None
) -> dict[str, AgentDefinition]:
    found: dict[str, AgentDefinition] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.md")):
        if root is not None and not safe_project_file(path, root):
            continue  # a project's file must really be inside it
        try:
            meta, body = parse_file(path.read_text(encoding="utf-8")[:MAX_FILE_CHARS])
        except (OSError, UnicodeDecodeError):
            continue
        name = meta.get("name") or path.stem
        if not NAME_RE.match(name) or not body or name in BUILT_IN_AGENTS:
            continue
        found[name] = AgentDefinition(
            name=name,
            description=meta.get("description", "") or f"Custom agent {name}",
            prompt=body,
            origin=origin,
            tools=split_tools(meta["tools"]) if meta.get("tools") else None,
            model=meta.get("model") or None,
            path=path,
        )
    return found


def load_agents(project_root: Path, project_trusted: bool) -> dict[str, AgentDefinition]:
    """Built-in agents, a trusted project's, then yours (yours win on a name clash)."""
    agents = dict(BUILT_IN_AGENTS)
    if project_trusted:
        agents.update(_load_dir(project_root / ".cmcoder" / "agents", "project", project_root))
    agents.update(_load_dir(config_dir() / "agents", "user"))
    return agents


def untrusted_project_agents(project_root: Path) -> list[str]:
    """A project's agents that aren't used because the project isn't trusted."""
    return sorted(_load_dir(project_root / ".cmcoder" / "agents", "project", project_root))


@dataclass
class SubagentRuntime:
    """What the main agent needs to start subagents (built by the CLI factory)."""

    project_root: Path
    project_trusted: bool
    # A model reference ("small", "subagent", or a model name) -> the model to use.
    resolve_model: Callable[[str], Awaitable[ModelChoice | None]] | None = None
    # (definition, model name) -> the subagent's system prompt.
    system_prompt: Callable[[AgentDefinition, str], str] = field(default=lambda d, model: d.prompt)

    def definitions(self) -> dict[str, AgentDefinition]:
        return load_agents(self.project_root, self.project_trusted)


def select_tools(tools: dict[str, Tool], allowed: list[str] | None) -> dict[str, Tool]:
    usable = {n: t for n, t in tools.items() if n not in NOT_FOR_SUBAGENTS}
    if allowed is None:
        return usable
    return {
        n: t
        for n, t in usable.items()
        if n in allowed or any(a.startswith("mcp__") and n.startswith(a + "__") for a in allowed)
    }


class TaskInput(ToolInput):
    description: str = Field(description="A short (3-5 word) description of the task")
    prompt: str = Field(
        description="The task for the subagent: everything it needs to know, since it "
        "doesn't see this conversation, and what to put in its report"
    )
    subagent_type: str = Field(
        "general-purpose", description="Which agent to use (see the list in the description)"
    )


@dataclass
class TaskDone:
    """The last item of `TaskTool.run_stream`."""

    result: ToolResult
    stop_turn: bool = False  # the user denied a tool call: the main turn stops too


# Subagent events the front ends show (inside the Task call's card).
FORWARDED = (ev.ToolUse, ev.ToolResult, ev.PermissionDenied)
MAX_RUN_LOG = 200  # tool calls kept per run for `/agents <n>`
ACTIVE_STATES = ("queued", "running", "waiting", "stopping")


@dataclass(eq=False)
class SubagentRun:
    """One Task call's subagent, for the agent map and `/agents`."""

    id: str  # the Task call's id
    number: int  # 1, 2, ... in this session
    description: str
    agent_type: str
    model: str
    max_steps: int
    state: ev.SubagentState = "queued"
    steps: int = 0
    tool_uses: int = 0
    tokens: int = 0
    activity: str = ""
    report: str = ""
    log: list[str] = field(default_factory=list)  # its tool calls, for `/agents <n>`
    queued_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    ended_at: float | None = None
    stop_requested: bool = False
    child: Agent | None = field(default=None, repr=False)
    # Called when the state changes outside the event stream (a stop, a permission wait).
    on_change: Callable[[], None] | None = field(default=None, repr=False)
    _stopped: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def elapsed_ms(self) -> int:
        if self.started_at is None:
            return 0
        return int(((self.ended_at or time.monotonic()) - self.started_at) * 1000)

    def status(self) -> ev.SubagentStatus:
        return ev.SubagentStatus(
            id=self.id,
            number=self.number,
            description=self.description,
            agent_type=self.agent_type,
            model=self.model,
            state=self.state,
            steps=self.steps,
            max_steps=self.max_steps,
            tool_uses=self.tool_uses,
            tokens=self.tokens,
            elapsed_ms=self.elapsed_ms(),
            activity=self.activity,
        )

    def note(self, line: str) -> None:
        self.log.append(line)
        del self.log[:-MAX_RUN_LOG]

    def changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def request_stop(self) -> bool:
        """Stop it: a queued one doesn't start; a running one writes its report
        after its current step. False when it isn't running."""
        if not self.active or self.stop_requested:
            return False
        self.stop_requested = True
        self._stopped.set()
        if self.child is not None:
            self.child.request_stop()
        if self.state != "queued":
            self.state = "stopping"
        self.changed()
        return True

    async def wait_for_slot(self, slots: asyncio.Semaphore) -> bool:
        """Wait for a free slot; False when it was stopped while it waited."""
        if self.stop_requested:
            return False
        acquire = asyncio.ensure_future(slots.acquire())
        stopped = asyncio.ensure_future(self._stopped.wait())
        try:
            await asyncio.wait({acquire, stopped}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:  # interrupted: give back a slot it just got
            if acquire.done() and not acquire.cancelled():
                slots.release()
            raise
        finally:
            stopped.cancel()
            if not acquire.done():
                acquire.cancel()
        if acquire.done() and not acquire.cancelled():
            if not self.stop_requested:
                return True
            slots.release()
        return False

    def start(self, child: Agent) -> None:
        self.child = child
        self.state = "running"
        self.started_at = time.monotonic()

    def finish(self, state: ev.SubagentState, report: str = "") -> None:
        self.state = state
        self.report = report
        self.activity = ""
        self.ended_at = time.monotonic()
        if self.started_at is None:
            self.started_at = self.ended_at
        self.child = None


def summary_line(run: SubagentRun | ev.SubagentStatus) -> str:
    """ "explore · 14/100 steps · 22 tools · 31k tokens · 1m12s" for status displays."""
    elapsed = run.elapsed_ms() if isinstance(run, SubagentRun) else run.elapsed_ms
    parts = [
        run.agent_type,
        f"{run.steps}/{run.max_steps} steps",
        f"{run.tool_uses} tool{'s' if run.tool_uses != 1 else ''}",
        f"{short_count(run.tokens)} tokens",
        duration(elapsed),
    ]
    return " · ".join(parts)


def short_count(n: int) -> str:
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


def duration(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


STATE_WORDS: dict[str, str] = {
    "queued": "queued",
    "running": "running",
    "waiting": "waiting for permission",
    "stopping": "stopping (writing its report)",
    "done": "done",
    "limit": "done (step limit reached)",
    "stopped": "stopped",
    "failed": "failed",
}
STATE_MARKS: dict[str, str] = {
    "queued": "○",
    "running": "◐",
    "waiting": "⏸",
    "stopping": "◑",
    "done": "✓",
    "limit": "✓",
    "stopped": "■",
    "failed": "✗",
}


def agents_overview(
    definitions: dict[str, AgentDefinition],
    runs: list[SubagentRun],
    limit: int = 20,
    marks: dict[str, str] = STATE_MARKS,
) -> list[str]:
    """`/agents`: the agent types, then this session's runs."""
    lines = ["Agent types (Task subagent_type):"]
    for d in definitions.values():
        tools = "all tools" if d.tools is None else ", ".join(d.tools)
        model = d.model or "main model"
        lines.append(f"  {d.name} ({d.origin}) · {model} · {tools}")
    if not runs:
        lines += ["", "No subagents have run in this session."]
        return lines
    active = sum(r.active for r in runs)
    lines += ["", f"This session: {len(runs)} run{'s' if len(runs) != 1 else ''}, {active} active"]
    for r in runs[-limit:]:
        lines.append(f"  {r.number:>2}. {marks[r.state]} {r.description} · {STATE_WORDS[r.state]}")
        lines.append(f"      {summary_line(r)}" + (f" · now: {r.activity}" if r.activity else ""))
    lines += ["", "/agents <n> shows a run's steps and report; /agents stop <n> stops one."]
    return lines


def agent_run_details(run: SubagentRun, marks: dict[str, str] = STATE_MARKS) -> list[str]:
    """`/agents <n>`: one run's state, steps and report."""
    lines = [
        f"{run.number}. {marks[run.state]} {run.description} · {STATE_WORDS[run.state]}",
        f"{summary_line(run)} · model {run.model}",
    ]
    if run.activity:
        lines.append(f"now: {run.activity}")
    lines += ["", "Steps:"] + ([f"  {line}" for line in run.log] or ["  (none yet)"])
    if run.report:
        lines += ["", "Report:", run.report]
    return lines


class TaskTool(Tool):
    name: ClassVar[str] = "Task"
    description: ClassVar[str] = ""
    Input = TaskInput
    # Starting a subagent needs no permission: each of its tool calls is checked.
    read_only: ClassVar[bool] = True

    def __init__(self, parent: Agent, runtime: SubagentRuntime) -> None:
        self.parent = parent
        self.runtime = runtime

    def spec(self) -> Any:
        spec = super().spec()
        agents = "\n".join(
            f"- {d.name}: {d.description}" for d in self.runtime.definitions().values()
        )
        spec.description = (
            "Hand a self-contained task to a subagent with its own fresh conversation; it "
            "works on its own and returns one report. Use it for broad searches and research "
            "that would fill this conversation with file contents, or for independent parts "
            "of a larger task. Give it a complete prompt: it can't see this conversation. "
            "Its report isn't shown to the user: summarise what matters.\n\n"
            f"Agents (subagent_type):\n{agents}"
        )
        parent = self.parent
        if parent.profile.parallel_tool_calls and parent.max_parallel_subagents > 1:
            spec.description += (
                "\n\nSeveral Task calls in one reply run at the same time (up to "
                f"{parent.max_parallel_subagents}): use that for independent parts, e.g. one "
                "subagent per project or folder. Don't give two of them the same files to change."
            )
        return spec

    def describe(self, args: TaskInput, ctx: ToolContext) -> str:
        return f"Task({args.subagent_type}: {args.description})"

    async def run(self, args: TaskInput, ctx: ToolContext) -> ToolResult:
        result = ToolResult("The subagent didn't finish.", is_error=True)
        async for item in self.run_stream(args, ctx, ""):
            if isinstance(item, TaskDone):
                result = item.result
        return result

    async def run_stream(
        self, args: TaskInput, ctx: ToolContext, call_id: str
    ) -> AsyncIterator[ev.Event | TaskDone]:
        definitions = self.runtime.definitions()
        definition = definitions.get(args.subagent_type)
        if definition is None:
            yield TaskDone(
                ToolResult(
                    f"Unknown subagent_type {args.subagent_type!r}. "
                    f"Available: {', '.join(definitions)}.",
                    is_error=True,
                )
            )
            return
        parent = self.parent
        choice = ModelChoice(parent.provider, parent.model, parent.profile)
        if definition.model and definition.model != "inherit":
            resolved = None
            if self.runtime.resolve_model is not None:
                try:
                    resolved = await self.runtime.resolve_model(definition.model)
                except Exception as e:  # a bad model name: say so, use the main model
                    yield ev.Warning(
                        message=f"Subagent {definition.name}: model {definition.model!r} "
                        f"can't be used ({e}); using {parent.model}."
                    )
            choice = resolved or choice
        run = parent.new_subagent_run(call_id, args.description, definition.name, choice.model)
        yield run.status()
        if not await run.wait_for_slot(parent.subagent_slots):
            run.finish("stopped")
            yield run.status()
            yield TaskDone(
                ToolResult(
                    "The user stopped this subagent before it started.",
                    is_error=True,
                    summary="stopped before it started",
                )
            )
            return
        try:
            async for item in self._run_child(run, definition, choice, args.prompt, call_id):
                yield item
        finally:
            parent.subagent_slots.release()
            if run.active:  # interrupted with the turn
                run.finish("stopped")

    async def _run_child(
        self,
        run: SubagentRun,
        definition: AgentDefinition,
        choice: ModelChoice,
        prompt: str,
        call_id: str,
    ) -> AsyncIterator[ev.Event | TaskDone]:
        parent = self.parent
        child = parent.spawn_subagent(
            choice,
            select_tools(parent.tools, definition.tools),
            self.runtime.system_prompt(definition, choice.model),
        )
        run.start(child)
        yield run.status()
        # The child's events and status changes from outside it (a stop, a
        # permission question) come through one queue, in order.
        updates: asyncio.Queue[Any] = asyncio.Queue()
        end = object()
        run.on_change = lambda: updates.put_nowait(run.status())
        if child.ask is not None:
            ask = child.ask

            async def asking(req: Any) -> Any:
                run.state, run.activity = "waiting", req.label
                run.changed()
                try:
                    return await ask(req)
                finally:
                    if run.state == "waiting":
                        run.state = "running"
                        run.changed()

            child.ask = asking

        async def pump() -> None:
            try:
                async for event in child.run(prompt):
                    updates.put_nowait(event)
            finally:
                updates.put_nowait(end)

        pumping = asyncio.create_task(pump())
        final: ev.Result | None = None
        try:
            while (item := await updates.get()) is not end:
                if isinstance(item, ev.SubagentStatus):
                    yield item
                elif isinstance(item, ev.Result):
                    final = item
                elif isinstance(item, FORWARDED):
                    if isinstance(item, ev.ToolUse):
                        run.tool_uses += 1
                        run.activity = item.label
                        run.note(f"● {item.label}")
                    else:
                        run.activity = ""
                        if isinstance(item, ev.PermissionDenied):
                            run.note(f"  └ denied: {item.reason}")
                        elif item.is_error:
                            first = (item.content.strip().splitlines() or [""])[0]
                            run.note(f"  └ {first[:200]}")
                    yield item.model_copy(update={"parent_tool_use_id": call_id})
                    yield run.status()
                elif isinstance(item, ev.AssistantMessage):
                    run.steps += 1
                    run.tokens = child.usage.prompt_tokens + child.usage.completion_tokens
                    yield run.status()
                elif isinstance(item, ev.Warning):
                    yield ev.Warning(message=f"[{definition.name}] {item.message}")
            await pumping  # a bug in the subagent fails the Task call, as before
        except asyncio.CancelledError:
            run.finish("stopped")  # the turn was interrupted
            raise
        except Exception:
            run.finish("failed")
            raise
        finally:
            run.on_change = None
            if not pumping.done():
                pumping.cancel()
            await asyncio.gather(pumping, return_exceptions=True)
            parent.usage.add(child.usage)
            await child.ctx.close_shells()
        summary = f"{run.tool_uses} tool use{'s' if run.tool_uses != 1 else ''} · {choice.model}"
        if final is None:
            run.finish("failed")
            yield run.status()
            yield TaskDone(
                ToolResult("The subagent didn't finish.", is_error=True, summary=summary)
            )
        elif final.subtype == "success":
            report = final.result.strip() or "(The subagent finished without a report.)"
            state: ev.SubagentState = "done"
            if run.stop_requested:
                state = "stopped"
                report = (
                    "(The user stopped this subagent, so this report may be incomplete.)\n\n"
                    + report
                )
                summary += " · stopped by the user"
            elif final.num_turns > child.max_turns:  # its report after reaching the limit
                state = "limit"
                report = (
                    f"(The subagent used all {child.max_turns} model calls it may make, so this "
                    "report may be incomplete.)\n\n" + report
                )
                summary += " · step limit reached"
            if len(report) > MAX_REPORT_CHARS:
                report = report[:MAX_REPORT_CHARS] + "\n... [report truncated]"
            run.finish(state, report)
            yield run.status()
            yield TaskDone(ToolResult(report, summary=summary))
        elif final.subtype == "interrupted":
            run.finish("stopped", "The user denied one of its tool calls.")
            yield run.status()
            yield TaskDone(
                ToolResult(
                    "The user denied a tool call of the subagent. Stop and wait for the "
                    "user's instructions.",
                    is_error=True,
                    summary="stopped",
                ),
                stop_turn=True,
            )
        else:
            run.finish("failed", final.result)
            yield run.status()
            yield TaskDone(
                ToolResult(f"The subagent stopped: {final.result}", is_error=True, summary=summary)
            )


def agents_command(parent: Agent, arg: str, marks: dict[str, str] = STATE_MARKS) -> list[str]:
    """`/agents [n | stop n]` for every front end (`marks`: the terminal's symbols)."""
    task = parent.tools.get("Task")
    if not isinstance(task, TaskTool):
        return ["Subagents aren't available in this session."]
    words = arg.split()
    if not words:
        return agents_overview(task.runtime.definitions(), parent.subagent_runs, marks=marks)
    if words[0] == "stop" and len(words) == 2:
        run = parent.stop_subagent(words[1])
        if run is None:
            return [f"No running subagent {words[1]} (see /agents)."]
        if run.state == "queued":
            return [f"Stopped {run.number}. {run.description} before it started."]
        return [
            f"Stopping {run.number}. {run.description}: it writes its report after its current step."
        ]
    if len(words) == 1 and (run := parent.find_subagent_run(words[0])) is not None:
        return agent_run_details(run, marks)
    return ["Usage: /agents, /agents <n> (a run's steps and report), /agents stop <n>."]
