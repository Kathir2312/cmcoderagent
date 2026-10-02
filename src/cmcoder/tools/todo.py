"""TodoWrite: a visible task list for multi-step work.

The model sends the whole list every time (like Claude Code's TodoWrite).
The list lives in ToolContext.todos, is shown to the user as a checklist,
is carried across compaction, and is restored on --resume from the last
TodoWrite call in the conversation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .base import Tool, ToolContext, ToolInput, ToolResult

Status = Literal["pending", "in_progress", "completed"]
MARKS = {"pending": "☐", "in_progress": "◐", "completed": "☑"}


class TodoItem(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    content: str = Field(min_length=1, description="The task, e.g. 'Add a test for login'.")
    status: Status = Field(description="pending, in_progress or completed.")
    active_form: str | None = Field(
        None,
        alias="activeForm",
        description="The task as an action in progress, e.g. 'Adding a test for login'.",
    )


class TodoInput(ToolInput):
    todos: list[TodoItem] = Field(description="The complete, updated todo list.")


def format_todos(todos: list[dict[str, Any]]) -> str:
    """The list as plain text: one '☐/◐/☑ task' line per item."""
    return "\n".join(f"{MARKS.get(t.get('status', ''), '☐')} {t.get('content', '')}" for t in todos)


class TodoTool(Tool):
    name = "TodoWrite"
    description = (
        "Keep a todo list for tasks with 3 or more steps, so you and the user can track "
        "progress. Send the complete list every time. Mark exactly one item in_progress "
        "while you work on it, and mark it completed as soon as it is done. Don't use it "
        "for simple one-step requests."
    )
    Input = TodoInput
    read_only = True  # changes nothing on disk: no permission needed

    def describe(self, args: TodoInput, ctx: ToolContext) -> str:
        done = sum(t.status == "completed" for t in args.todos)
        return f"TodoWrite({done}/{len(args.todos)} done)"

    async def run(self, args: TodoInput, ctx: ToolContext) -> ToolResult:
        ctx.todos = [t.model_dump(by_alias=True, exclude_none=True) for t in args.todos]
        done = sum(t.status == "completed" for t in args.todos)
        active = [t for t in args.todos if t.status == "in_progress"]
        notes = []
        if len(active) > 1:
            notes.append("Note: keep only one item in_progress at a time.")
        if args.todos and done == len(args.todos):
            notes.append("All items are completed.")
        text = f"Todo list updated ({done}/{len(args.todos)} done):\n{format_todos(ctx.todos)}"
        return ToolResult(
            "\n".join([text, *notes]),
            summary=f"{done}/{len(args.todos)} done"
            + (f" · now: {active[0].active_form or active[0].content}" if active else ""),
        )
