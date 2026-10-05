"""The agent map: subagents of the running turn as a live tree, and tags that
tell apart the lines of subagents running at the same time."""

from __future__ import annotations

import time
from dataclasses import dataclass

from rich.text import Text

from ..core.subagents import ACTIVE_STATES, STATE_WORDS, summary_line
from ..protocol import events as ev
from .symbols import sym as S

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
LEAVES = 3  # tool calls shown per subagent in the mind map; the rest fold
MIND_MAP_MIN_WIDTH = 96  # narrower: the vertical tree


@dataclass
class Step:
    id: str
    label: str
    problem: str = ""  # an error or a denial


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
        self.steps: dict[str, list[Step]] = {}  # each subagent's tool calls
        self.reports: dict[str, str] = {}
        self.prompt = ""
        self.model = ""
        self.busy = False
        self.outcome = ""  # the turn's result: success, interrupted, ...
        self.main_tools = 0  # the main agent's own tool calls

    def update(self, status: ev.SubagentStatus) -> None:
        self.runs[status.id] = status
        self._seen[status.id] = time.monotonic()

    def clear(self) -> None:
        self.runs.clear()
        self._seen.clear()
        self.steps.clear()
        self.reports.clear()
        self.outcome = ""
        self.main_tools = 0

    def start_turn(self, prompt: str, model: str = "") -> None:
        self.clear()
        self.prompt = prompt
        self.model = model or self.model
        self.busy = True

    def observe(self, event: ev.Event) -> None:
        """Every event of the turn: statuses, the subagents' steps, reports."""
        parent = getattr(event, "parent_tool_use_id", None)
        if isinstance(event, ev.SubagentStatus):
            self.update(event)
        elif isinstance(event, ev.ToolUse):
            if parent:
                self.steps.setdefault(parent, []).append(Step(event.id, event.label))
            elif event.name != "Task":
                self.main_tools += 1
        elif isinstance(event, ev.ToolResult):
            if parent and event.is_error:
                self._problem(parent, event.id, (event.content.strip().splitlines() or [""])[0])
            elif not parent and event.id in self.runs:
                self.reports[event.id] = event.content
        elif isinstance(event, ev.PermissionDenied) and parent:
            self._problem(parent, event.id, f"denied: {event.reason}")
        elif isinstance(event, ev.Result):
            self.busy = False
            self.outcome = event.subtype

    def _problem(self, parent: str, step_id: str, text: str) -> None:
        for step in self.steps.get(parent, []):
            if step.id == step_id:
                step.problem = text[:200]

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
                    f"{S().last if last else S().branch} {S().states[s.state]} {s.number}. "
                    f"{s.description}  {self.detail(s)}",
                    STATE_STYLES[s.state],
                )
            )
            if s.activity and s.state in TICKING:
                pad = "  " if last else S().pipe + " "
                out.append((f"{pad}    {S().end} {s.activity}", "dim"))
        return out

    def final_line(self, task_id: str) -> str | None:
        """The closing line of a finished Task call: "✓ done · explore · …"."""
        s = self.runs.get(task_id)
        if s is None:
            return None
        return f"{S().states[s.state]} {STATE_WORDS[s.state]} · {summary_line(s)}"

    # -- the mind map ------------------------------------------------------------

    def leaves(self, s: ev.SubagentStatus) -> list[tuple[str, str]]:
        """Its last few tool calls as (text, style); "... N earlier steps" first."""
        steps = self.steps.get(s.id, [])
        shown = steps[-LEAVES:]
        out: list[tuple[str, str]] = []
        if len(steps) > len(shown):
            n = len(steps) - len(shown)
            out.append((f"... {n} earlier step{'s' if n != 1 else ''}", "dim italic"))
        for i, step in enumerate(shown):
            if step.problem:
                out.append((f"{step.label} - {step.problem}", "red"))
            elif i == len(shown) - 1 and s.state in TICKING and s.activity == step.label:
                out.append((step.label, STATE_STYLES[s.state]))  # what it's doing now
            else:
                out.append((step.label, "dim"))
        return out

    def stat_lines(self, s: ev.SubagentStatus) -> tuple[str, str]:
        """Its numbers on two lines: "explore · 14/100 steps", "22 tools · 31.4k tokens · 1m12s"."""
        if s.state == "queued":
            return f"{s.agent_type} · queued", ""
        words = "" if s.state in ("running", "done") else f"{STATE_WORDS[s.state]} · "
        tools = f"{s.tool_uses} tool{'s' if s.tool_uses != 1 else ''}"
        return (
            f"{words}{s.agent_type} · {s.steps}/{s.max_steps} steps",
            f"{tools} · {_short(s.tokens)} tokens · {_duration(self.elapsed_ms(s))}",
        )

    def stats(self, s: ev.SubagentStatus) -> str:
        return " · ".join(x for x in self.stat_lines(s) if x)

    def root_lines(self) -> list[str]:
        """Under "main agent": the model, how the turn goes, its own tool calls."""
        how = "working..." if self.busy else self.outcome.replace("success", "done")
        own = f"{self.main_tools} own tool call{'s' if self.main_tools != 1 else ''}"
        return [x for x in (self.model, how, own if self.main_tools else "") if x]

    def mind_map(self, width: int) -> list[Text]:
        """The turn as a mind map, left to right: the main agent, its subagents,
        their last tool calls. Narrower than MIND_MAP_MIN_WIDTH: a tree."""
        runs = list(self.runs.values())
        if not runs:
            return []
        if width < MIND_MAP_MIN_WIDTH:
            return self._tree(runs, width)
        sym = S()
        box = sym.branch.startswith("├")
        h = "─" if box else "-"
        root_w = 22
        agent_w = min(52, max(34, (width - root_w - 8) // 2))
        leaf_w = max(16, width - root_w - 1 - 2 - agent_w - 1 - 2)

        # Rows: each subagent's block (its name, its numbers, its leaves), a
        # blank row between blocks.
        rows: list[dict[str, object]] = []
        anchors: list[int] = []
        for k, s in enumerate(runs):
            if k:
                rows.append({})
            top = len(rows)
            leaves = self.leaves(s)
            for _ in range(max(3, len(leaves))):
                rows.append({})
            rows[top]["agent"] = s
            first, second = self.stat_lines(s)
            rows[top + 1]["stats"] = (first, "yellow" if s.state == "waiting" else "dim")
            if second:
                rows[top + 2]["stats"] = (second, "dim")
            for j, leaf in enumerate(leaves):
                rows[top + j]["leaf"] = leaf
                rows[top + j]["joint"] = _joint(j, len(leaves), box)
            if leaves:
                rows[top]["fill"] = True
            anchors.append(top)
        r_root = anchors[len(anchors) // 2]
        root = [(f"{sym.tool} main agent ", "bold")] + [
            (f"  {x}", "dim") for x in self.root_lines()
        ]
        while len(rows) < r_root + len(root):
            rows.append({})

        out: list[Text] = []
        for r, row in enumerate(rows):
            t = Text()
            k = r - r_root
            if k == 0:
                label, style = root[0]
                t.append(label, style)
                t.append(h * (root_w - len(label)), "dim")
            elif 0 < k < len(root):
                label, style = root[k]
                t.append(_fit(label, root_w - 1).ljust(root_w), style)
            else:
                t.append(" " * root_w)
            t.append(_trunk(r, anchors, r_root, box), "dim")
            s = row.get("agent")
            stats = row.get("stats")
            if isinstance(s, ev.SubagentStatus):
                name = _fit(f"{sym.states[s.state]} {s.number}. {s.description}", agent_w - 1)
                t.append(f"{h} ", "dim")
                t.append(name, f"bold {STATE_STYLES[s.state]}")
                fill = agent_w - len(name)
                t.append((" " + h * (fill - 1)) if row.get("fill") else " " * fill, "dim")
            elif isinstance(stats, tuple):
                t.append("  " + _fit(stats[0], agent_w - 1).ljust(agent_w), stats[1])
            else:
                t.append(" " * (agent_w + 2))
            leaf = row.get("leaf")
            if isinstance(leaf, tuple):
                t.append(str(row["joint"]), "dim")
                t.append(f"{h} ", "dim")
                t.append(_fit(leaf[0], leaf_w), leaf[1])
            t.rstrip()
            out.append(t)
        while out and not out[-1].plain:
            out.pop()
        return out

    def _tree(self, runs: list[ev.SubagentStatus], width: int) -> list[Text]:
        """The narrow form: a tree, each subagent's leaves under it."""
        sym = S()
        out = [Text(_fit(f"{sym.tool} main agent  " + "  ".join(self.root_lines()), width), "bold")]
        for i, s in enumerate(runs):
            last = i == len(runs) - 1
            t = Text(f"{sym.last if last else sym.branch} ", "dim")
            name = f"{sym.states[s.state]} {s.number}. {s.description}"
            t.append(_fit(name, width - 3), f"bold {STATE_STYLES[s.state]}")
            out.append(t)
            pad = "   " if last else sym.pipe + "  "
            for line in self.stat_lines(s):
                if line:
                    out.append(Text(_fit(f"{pad}  {line}", width), "dim"))
            leaves = self.leaves(s)
            for j, (text, style) in enumerate(leaves):
                t = Text(f"{pad}  {sym.last if j == len(leaves) - 1 else sym.branch} ", "dim")
                t.append(_fit(text, max(8, width - len(t.plain))), style)
                out.append(t)
        return out


def _short(n: int) -> str:
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


def _duration(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 3)] + "..."


def _joint(j: int, n: int, box: bool) -> str:
    """The connector before leaf j of n (the parent sits on row 0)."""
    if not box:
        return "-" if n == 1 else "+"
    if n == 1:
        return "─"
    return "┬" if j == 0 else "└" if j == n - 1 else "├"


def _trunk(r: int, anchors: list[int], r_root: int, box: bool) -> str:
    """The main agent's trunk at row r: joins it to every subagent row."""
    a0, an = anchors[0], anchors[-1]
    if r < a0 or r > an:
        return " "
    if not box:
        return "+" if r in anchors else "|"
    if a0 == an:
        return "─"
    if r == a0:
        return "┬" if r == r_root else "┌"
    if r == an:
        return "┴" if r == r_root else "└"
    if r in anchors:
        return "┼" if r == r_root else "├"
    return "┤" if r == r_root else "│"


def review_lines(event: ev.ReviewResult) -> list[tuple[str, str]]:
    """Critique's outcome as (text, style) lines, for the terminal front ends."""
    sym = S()
    n = len(event.issues)
    problems = f"{n} problem{'s' if n != 1 else ''}"
    if not event.final:
        head = (
            f"{sym.compacted} The reviewer found {problems}; fixing "
            f"{'them' if n != 1 else 'it'} (review {event.round}/{event.max_rounds}):",
            "yellow",
        )
    elif event.verdict == "pass":
        return [(f"{sym.ok} Reviewed: {event.summary}".rstrip(": "), "green")]
    elif event.verdict == "fail":
        head = (
            f"{sym.warn} Not validated: after {event.round} review"
            f"{'s' if event.round != 1 else ''} the reviewer still found {problems}:",
            "yellow",
        )
    else:
        return [(f"{sym.warn} Not reviewed: {event.summary}", "yellow")]
    lines: list[tuple[str, str]] = [head]
    for issue in event.issues:
        where = f" ({issue.where})" if issue.where else ""
        lines.append((f"  - [{issue.severity}] {issue.problem}{where}", "dim"))
    if not event.issues and event.summary:
        lines.append((f"  - {event.summary}", "dim"))
    return lines
