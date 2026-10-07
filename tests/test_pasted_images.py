"""Images in the terminal: the clipboard (Ctrl+V / Alt+V), a pasted or dropped
image file's path, the [Image #n] placeholders, and the TUI end to end."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

import pytest
from PIL import Image as Pil
from textual import events
from textual.widgets import Input

from cmcoder.cli import pasted_images
from cmcoder.cli.pasted_images import PendingImages, clipboard_image, image_path
from cmcoder.cli.tui import CmcoderApp
from cmcoder.config.settings import Settings
from cmcoder.core.agent import Agent
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.images import ImageError
from cmcoder.providers.profiles import resolve_profile
from cmcoder.tools.base import ToolContext
from cmcoder.tools.registry import default_tools

from .conftest import make_provider
from .test_tui import until


def png(path: Path | None = None) -> bytes:
    out = io.BytesIO()
    Pil.new("RGB", (16, 8), (0, 160, 0)).save(out, "PNG")
    if path is not None:
        path.write_bytes(out.getvalue())
    return out.getvalue()


# --- a pasted path ----------------------------------------------------------------------


def test_a_pasted_or_dropped_image_path_is_recognised(tmp_path: Path) -> None:
    shot = tmp_path / "my shot.png"
    png(shot)
    for pasted in (
        str(shot),
        f'"{shot}"',  # Windows Terminal quotes a dropped path
        f"'{shot}'",
        shot.as_uri(),  # file:// URL
        f"  {shot}\n",
    ):
        assert image_path(pasted) == shot, pasted
    if os.name != "nt":  # macOS Terminal escapes spaces (on Windows \ separates folders)
        assert image_path(str(shot).replace(" ", "\\ ")) == shot


def test_other_pastes_are_text(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    assert image_path(str(notes)) is None  # not an image type
    assert image_path(str(tmp_path / "missing.png")) is None
    assert image_path(f"{tmp_path / 'a.png'}\n{tmp_path / 'b.png'}") is None  # several lines
    assert image_path("look at screenshot.png please") is None
    assert image_path("") is None


# --- the clipboard -------------------------------------------------------------------------


def test_the_clipboards_image_as_png(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from PIL import ImageGrab

    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: Pil.new("RGB", (4, 4)))
    raw = clipboard_image()
    assert raw is not None and raw.startswith(b"\x89PNG")
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: None)
    assert clipboard_image() is None
    # Windows: files copied in Explorer come as their paths.
    shot = tmp_path / "copied.png"
    raw = png(shot)
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: [str(tmp_path / "x.txt"), str(shot)])
    assert clipboard_image() == raw

    def broken() -> None:
        raise OSError("wl-paste or xclip is required")

    monkeypatch.setattr(ImageGrab, "grabclipboard", broken)
    assert clipboard_image() is None


# --- placeholders --------------------------------------------------------------------------


def test_placeholders_decide_which_images_are_sent() -> None:
    pending = PendingImages()
    assert pending.add(png(), "a.png") == "[Image #1]"
    assert pending.add(png()) == "[Image #2]"
    assert pending.add(png()) == "[Image #3]"
    # The user deleted #2 before sending; #3 is mentioned first.
    taken = pending.take("compare [Image #3] with [Image #1]")
    assert [i.name for i in taken] == ["", "a.png"]
    assert pending.images == {}  # a new message starts with none
    with pytest.raises(ImageError, match="isn't a PNG"):
        pending.add(b"not an image")


def test_at_most_five_per_message() -> None:
    pending = PendingImages()
    for _ in range(5):
        pending.add(png())
    with pytest.raises(ImageError, match="At most 5"):
        pending.add(png())


# --- the TUI, end to end -------------------------------------------------------------------


def make_app(server: Any, project: Path) -> CmcoderApp:
    settings = Settings.model_validate(
        {"providers": {"mock": {"baseUrl": server.base_url}}, "model": "mock:qwen2.5-vl-7b"}
    )
    app = CmcoderApp(settings)
    app.agent = Agent(
        make_provider(server),
        "qwen2.5-vl-7b",
        resolve_profile("qwen2.5-vl-7b"),
        default_tools(),
        PermissionPolicy("default"),
        ToolContext(cwd=project, project_root=project),
        "test",
        ask=app.ask,
    )
    return app


def with_images(server: Any) -> list[dict[str, Any]]:
    """Requests whose last message has image parts."""
    return [
        r for r in server.requests if isinstance(r.get("messages", [{}])[-1].get("content"), list)
    ]


async def test_tui_a_dropped_image_and_alt_v_go_with_the_message(
    mock_server: Any, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = mock_server([{"content": "Two green boxes."}])
    shot = tmp_path / "shot.png"
    png(shot)
    monkeypatch.setattr("cmcoder.cli.tui.clipboard_image", lambda: png())
    app = make_app(server, project)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            prompt = app.query_one("#prompt", Input)
            prompt.focus()
            prompt.post_message(events.Paste(str(shot)))  # a dropped file arrives as its path
            await until(pilot, lambda: "[Image #1]" in prompt.value)
            await pilot.press("alt+v")
            await until(pilot, lambda: "[Image #2]" in prompt.value)
            prompt.insert_text_at_cursor("what are these?")
            await pilot.press("enter")
            await until(pilot, lambda: bool(with_images(server)))
            await until(pilot, lambda: not (app.turn and app.turn.is_running))
    finally:
        await app.agent.close()  # type: ignore[union-attr]
    content = with_images(server)[-1]["messages"][-1]["content"]
    assert content[0]["text"] == "[Image #1] [Image #2] what are these?"
    assert [p["type"] for p in content[1:]] == ["image_url", "image_url"]


def test_pasted_images_module_has_no_ide_dependencies() -> None:
    # Imported by the REPL and the TUI only; cheap to load.
    assert {".png", ".jpg", ".jpeg", ".gif", ".webp"} == pasted_images.IMAGE_SUFFIXES
