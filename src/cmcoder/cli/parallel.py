"""Telling apart the lines of subagents that run at the same time."""

from __future__ import annotations

from ..protocol import events as ev


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
