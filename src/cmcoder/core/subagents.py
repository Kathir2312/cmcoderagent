"""Subagents: the `Task` tool hands a self-contained job to a fresh agent.

A subagent has its own conversation (so the main one stays small), its own
tools and possibly a smaller model, and returns one final report. It uses the
main agent's permission policy, prompts and hooks (PreToolUse, PostToolUse,
and SubagentStop instead of Stop), and can't start subagents itself.

Built in: `general-purpose` (every tool, the main model) and `explore`
(read-only search: Read, Glob, Grep; `subagentModel`, else `smallFastModel`).
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

import re
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
        tools=["Read", "Glob", "Grep"],
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
        child = parent.spawn_subagent(
            choice,
            select_tools(parent.tools, definition.tools),
            self.runtime.system_prompt(definition, choice.model),
        )
        final: ev.Result | None = None
        tool_uses = 0
        try:
            async for event in child.run(args.prompt):
                if isinstance(event, ev.Result):
                    final = event
                elif isinstance(event, FORWARDED):
                    tool_uses += isinstance(event, ev.ToolUse)
                    yield event.model_copy(update={"parent_tool_use_id": call_id})
                elif isinstance(event, ev.Warning):
                    yield ev.Warning(message=f"[{definition.name}] {event.message}")
        finally:
            parent.usage.add(child.usage)
            await child.ctx.close_shells()
        summary = f"{tool_uses} tool use{'s' if tool_uses != 1 else ''} · {choice.model}"
        if final is None:
            yield TaskDone(
                ToolResult("The subagent didn't finish.", is_error=True, summary=summary)
            )
        elif final.subtype == "success":
            report = final.result.strip() or "(The subagent finished without a report.)"
            if len(report) > MAX_REPORT_CHARS:
                report = report[:MAX_REPORT_CHARS] + "\n... [report truncated]"
            yield TaskDone(ToolResult(report, summary=summary))
        elif final.subtype == "interrupted":
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
            yield TaskDone(
                ToolResult(f"The subagent stopped: {final.result}", is_error=True, summary=summary)
            )
