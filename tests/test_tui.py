"""The Textual UI, driven headlessly with Textual's test pilot."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Input, Markdown, Static

from cmcoder.cli.tui import CmcoderApp, PermissionScreen
from cmcoder.config.settings import Settings
from cmcoder.core.agent import Agent
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider


def make_app(server: Any, project: Path, mode: str = "default") -> CmcoderApp:
    settings = Settings.model_validate(
        {"providers": {"mock": {"baseUrl": server.base_url}}, "model": "mock:qwen3-27b"}
    )
    app = CmcoderApp(settings)
    app.agent = Agent(
        make_provider(server),
        "qwen3-27b",
        resolve_profile("qwen3-27b"),
        default_tools(),
        PermissionPolicy(mode),
        ToolContext(cwd=project, project_root=project),
        "test",
        ask=app.ask,
    )
    return app


def log_text(app: CmcoderApp) -> str:
    parts = []
    for w in app.query_one("#log", VerticalScroll).children:
        if isinstance(w, Markdown):
            parts.append(w.source)
        elif isinstance(w, Static):
            c = w.content
            parts.append(c.plain if isinstance(c, Text) else str(c))
    return "\n".join(parts)


async def until(pilot: Any, cond: Callable[[], bool], timeout: float = 10) -> None:
    for _ in range(int(timeout / 0.05)):
        if cond():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not reached")


async def send(pilot: Any, app: CmcoderApp, text: str) -> None:
    app.query_one("#prompt", Input).value = text
    await pilot.press("enter")


async def test_reply_is_shown(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "Hello **there**."}])
    app = make_app(server, project)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await send(pilot, app, "hi")
            await until(pilot, lambda: "Hello **there**." in log_text(app))
            assert "> hi" in log_text(app)
            await until(
                pilot, lambda: "mode: default" in str(app.query_one("#status", Static).content)
            )
    finally:
        await app.agent.close()  # type: ignore[union-attr]


async def test_permission_dialog_keeps_options_visible(mock_server: Any, project: Path) -> None:
    """Item 2, completed: a 500-line command in a fixed-size dialog with a
    scrollable preview; the buttons stay on screen."""
    body = "\n".join(f"# row {i}" for i in range(1, 501))
    server = mock_server(
        [
            {
                "tool_calls": [
                    {"name": "Bash", "arguments": {"command": f"python3 - << 'EOF'\n{body}\nEOF"}}
                ]
            },
            {"content": "Stopped."},
        ]
    )
    app = make_app(server, project)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await send(pilot, app, "run it")
            await until(pilot, lambda: isinstance(app.screen, PermissionScreen))
            screen = app.screen
            region = screen.query_one("#buttons").region
            assert region.y + region.height <= 24  # the options are on screen
            preview = screen.query_one("#preview")
            assert preview.max_scroll_y > 400  # the whole command, scrollable
            await pilot.press("3")  # No → feedback box
            await pilot.press(*"use pytest")
            await pilot.press("enter")
            await until(pilot, lambda: "denied by user" in log_text(app))
            await until(pilot, lambda: "Stopped." in log_text(app))
    finally:
        await app.agent.close()  # type: ignore[union-attr]
    assert "use pytest" in server.requests[1]["messages"][-1]["content"]


async def test_allow_runs_the_tool(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {
                "tool_calls": [
                    {"name": "Write", "arguments": {"file_path": "n.md", "content": "# n\n"}}
                ]
            },
            {"content": "Created."},
        ]
    )
    app = make_app(server, project)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await send(pilot, app, "make notes")
            await until(pilot, lambda: isinstance(app.screen, PermissionScreen))
            await pilot.press("1")
            await until(pilot, lambda: "Created." in log_text(app))
    finally:
        await app.agent.close()  # type: ignore[union-attr]
    assert (project / "n.md").read_text() == "# n\n"


async def test_ctrl_c_interrupts_and_shift_tab_cycles_mode(mock_server: Any, project: Path) -> None:
    server = mock_server([{"tool_calls": [{"name": "Bash", "arguments": {"command": "sleep 30"}}]}])
    app = make_app(server, project, mode="bypassPermissions")
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("shift+tab")
            assert app.agent.policy.mode == "default"  # type: ignore[union-attr]
            app.agent.policy.mode = "bypassPermissions"  # type: ignore[union-attr]
            await send(pilot, app, "wait")
            await until(pilot, lambda: "Bash(sleep 30)" in log_text(app))
            await asyncio.sleep(0.5)
            await pilot.press("ctrl+c")
            await until(pilot, lambda: "Interrupted" in log_text(app), timeout=5)
            await send(pilot, app, "/help")
            await until(pilot, lambda: "/compact [focus]" in log_text(app))
            await send(pilot, app, "/mode plan")
            await until(pilot, lambda: "Permission mode: plan" in log_text(app))
    finally:
        await app.agent.close()  # type: ignore[union-attr]


async def test_todo_checklist(mock_server: Any, project: Path) -> None:
    todos = [
        {"content": "Fix it", "status": "in_progress"},
        {"content": "Test it", "status": "pending"},
    ]
    server = mock_server(
        [{"tool_calls": [{"name": "TodoWrite", "arguments": {"todos": todos}}]}, {"content": "ok"}]
    )
    app = make_app(server, project)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await send(pilot, app, "go")
            await until(pilot, lambda: "► Fix it" in log_text(app) and "☐ Test it" in log_text(app))
    finally:
        await app.agent.close()  # type: ignore[union-attr]


async def test_map_and_status_updates_after_the_app_closed(mock_server: Any, project: Path) -> None:
    # A turn that ends while the app quits updates the map and the status bar
    # after its widgets are gone; that must not fail the turn (seen on Windows CI).
    server = mock_server([{"content": "ok"}])
    app = make_app(server, project)
    try:
        async with app.run_test(size=(100, 30)):
            pass
        app.map.start_turn("go")
        app.refresh_map()
        app.update_status(busy=True)
    finally:
        await app.agent.close()  # type: ignore[union-attr]


async def test_text_in_brackets_is_shown_not_read_as_markup(
    mock_server: Any, project: Path
) -> None:
    # The critic's "[high]" and a tool label like Grep("[Http(Get|Post)]") are
    # plain text; Textual would read them as style markup and drop them.
    from cmcoder.protocol import events as ev

    app = make_app(mock_server([{"content": "ok"}]), project)
    try:
        async with app.run_test(size=(120, 30)) as pilot:
            await app.render_event(
                ev.ToolUse(id="g", name="Grep", input={}, label='Grep("[Http(Get|Post)]")')
            )
            await app.render_event(
                ev.ReviewResult(
                    round=1,
                    max_rounds=2,
                    verdict="fail",
                    final=False,
                    summary="s",
                    issues=[ev.ReviewIssue(severity="high", problem="wrong tax", where="p.py:8")],
                )
            )
            await pilot.pause()
            log = app.query_one("#log", VerticalScroll)
            text = "\n".join(  # what is displayed, not what was passed in
                w.render().plain for w in log.children if isinstance(w, Static)
            )
            assert "[Http(Get|Post)]" in text
            assert "- [high] wrong tax (p.py:8)" in text
    finally:
        await app.agent.close()  # type: ignore[union-attr]
