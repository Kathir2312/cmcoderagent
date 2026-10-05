"""The agent map: subagents of the running turn as a live tree, and tags that
tell apart the lines of subagents running at the same time."""

from __future__ import annotations

import time

from ..core.subagents import ACTIVE_STATES, STATE_MARKS, STATE_WORDS, summary_line
from ..protocol import events as ev

STATE_STYLES = {
    "queued": "dim",
    "running": "cyan",
    "waiting": "yellow",
    "stopping": "yellow",
    "done": "green",
    "limit": "green",
    "stopped": "yellow",
    "failed": "red",
}
TICKING = ("running", "waiting", "stopping")


class ParallelTasks:
    """The Task calls running, so the lines of subagents running at the same
    time can be told apart: "[Analyse TW.Core] ● Read(...)"."""

    def __init__(self) -> None:
        self.running: dict[str, str] = {}
        self.parallel = False

    def tag(self, event: ev.Event) -> str:
        """The prefix for this event's line (and keep track of running Tasks)."""
        parent = getattr(event, "parent_tool_use_id", None)
        if parent is not None:
            return self._tag(parent)
        if isinstance(event, ev.ToolUse) and event.name == "Task":
            self.running[event.id] = str(event.input.get("description") or "subagent")[:40]
            self.parallel = self.parallel or len(self.running) > 1
        elif isinstance(event, ev.ToolResult) and event.id in self.running:
            tag = self._tag(event.id)
            del self.running[event.id]
            self.parallel = bool(self.running) and self.parallel
            return tag
        return ""

    def _tag(self, task_id: str) -> str:
        name = self.running.get(task_id)
        return f"[{name}] " if self.parallel and name else ""


class AgentMap:
    """The latest `subagent_status` of each subagent in this turn, drawn as a
    tree (Claude Code style)::

        ├─ ◐ 1. Analyse TW.Core  explore · 14/100 steps · 22 tools · 31k tokens · 1m12s
        │     └ Read(Controllers/OrderController.cs)
        ├─ ⏸ 2. Analyse TW.Api  waiting for permission · Bash(dotnet build)
        └─ ○ 3. Analyse TW.Web  queued
    """

    def __init__(self) -> None:
        self.runs: dict[str, ev.SubagentStatus] = {}
        self._seen: dict[str, float] = {}

    def update(self, status: ev.SubagentStatus) -> None:
        self.runs[status.id] = status
        self._seen[status.id] = time.monotonic()

    def clear(self) -> None:
        self.runs.clear()
        self._seen.clear()

    @property
    def active(self) -> list[ev.SubagentStatus]:
        return [s for s in self.runs.values() if s.state in ACTIVE_STATES]

    def elapsed_ms(self, s: ev.SubagentStatus) -> int:
        """Its time so far, ticking between status events."""
        if s.state not in TICKING:
            return s.elapsed_ms
        return s.elapsed_ms + int((time.monotonic() - self._seen.get(s.id, 0.0)) * 1000)

    def detail(self, s: ev.SubagentStatus) -> str:
        if s.state == "queued":
            return f"{s.agent_type} · queued"
        live = s.model_copy(update={"elapsed_ms": self.elapsed_ms(s)})
        words = "" if s.state in ("running", "done") else f"{STATE_WORDS[s.state]} · "
        return words + summary_line(live)

    def lines(self) -> list[tuple[str, str]]:
        """The tree as (text, style) lines; empty when no subagent ran."""
        out: list[tuple[str, str]] = []
        items = list(self.runs.values())
        for i, s in enumerate(items):
            last = i == len(items) - 1
            out.append(
                (
                    f"{'└─' if last else '├─'} {STATE_MARKS[s.state]} {s.number}. "
                    f"{s.description}  {self.detail(s)}",
                    STATE_STYLES[s.state],
                )
            )
            if s.activity and s.state in TICKING:
                out.append((f"{'  ' if last else '│ '}    └ {s.activity}", "dim"))
        return out

    def final_line(self, task_id: str) -> str | None:
        """The closing line of a finished Task call: "✓ done · explore · …"."""
        s = self.runs.get(task_id)
        if s is None:
            return None
        return f"{STATE_MARKS[s.state]} {STATE_WORDS[s.state]} · {summary_line(s)}"
