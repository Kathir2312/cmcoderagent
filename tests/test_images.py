"""Images the user attaches: checks, sizes, the gateway format, sessions, and the
vision helper for a main model that can't see images."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image as Pil

from cmcoder.config.settings import Settings
from cmcoder.core.agent import Agent
from cmcoder.core.compaction import Summarizer, render
from cmcoder.core.context import message_chars
from cmcoder.core.permissions import PermissionPolicy
from cmcoder.core.sessions import message_from_dict, message_to_dict
from cmcoder.images import (
    MAX_SIDE,
    TOKENS_PER_IMAGE,
    Image,
    ImageError,
    as_text,
    from_base64,
    prepare,
    sniff,
)
from cmcoder.protocol import events as ev
from cmcoder.providers.messages import Message, StreamDone, Usage
from cmcoder.providers.openai_compat import to_wire_messages
from cmcoder.providers.profiles import ModelProfile, looks_like_vision, resolve_profile
from cmcoder.providers.text_tools import to_prompted_wire
from cmcoder.tools.base import ToolContext

from .conftest import API_KEY, make_provider


def png(width: int = 40, height: int = 20, mode: str = "RGB") -> bytes:
    out = io.BytesIO()
    Pil.new(mode, (width, height), (200, 30, 30, 255)[: len(mode)]).save(out, "PNG")
    return out.getvalue()


def small_image() -> Image:
    return prepare(png(), "shot.png")


# --- checks and sizes ------------------------------------------------------------


def test_the_type_comes_from_the_bytes_not_the_name() -> None:
    assert sniff(png()) == "image/png"
    jpeg = io.BytesIO()
    Pil.new("RGB", (8, 8)).save(jpeg, "JPEG")
    assert sniff(jpeg.getvalue()) == "image/jpeg"
    assert sniff(b"GIF89a....") == "image/gif"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    with pytest.raises(ImageError, match="isn't a PNG, JPEG, GIF or WebP"):
        prepare(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "evil.png")
    with pytest.raises(ImageError, match="isn't a PNG"):
        prepare(b"MZ\x90\x00 an exe", "photo.jpg")


def test_a_small_image_is_sent_as_it_is() -> None:
    raw = png()
    img = prepare(raw, "shot.png")
    assert (img.media_type, img.width, img.height, img.name) == ("image/png", 40, 20, "shot.png")
    assert base64.b64decode(img.data) == raw
    assert img.data_url.startswith("data:image/png;base64,")


def test_a_large_screenshot_is_made_smaller() -> None:
    img = prepare(png(4000, 2000), "big.png")
    assert max(img.width, img.height) == MAX_SIDE
    assert img.media_type == "image/jpeg"  # no transparency: JPEG, much smaller
    with Pil.open(io.BytesIO(base64.b64decode(img.data))) as im:
        assert im.size == (MAX_SIDE, MAX_SIDE // 2)
    transparent = prepare(png(3000, 100, "RGBA"))
    assert transparent.media_type == "image/png"  # transparency kept


def test_broken_and_huge_images_are_refused() -> None:
    with pytest.raises(ImageError, match="couldn't be read"):
        prepare(png()[:40] + b"\x00" * 100)
    # A "decompression bomb": tiny file, enormous picture.
    header = io.BytesIO()
    Pil.new("1", (10_000, 10_000)).save(header, "PNG")
    with pytest.raises(ImageError, match="too large"):
        prepare(header.getvalue())
    with pytest.raises(ImageError, match="valid base64"):
        from_base64("not base64!!")


def test_base64_and_data_urls() -> None:
    raw = png()
    assert from_base64(base64.b64encode(raw).decode()).width == 40
    assert from_base64("data:image/png;base64," + base64.b64encode(raw).decode()).height == 20


# --- what the gateway gets -------------------------------------------------------------


def user_with_image(text: str = "what's wrong here?") -> Message:
    m = Message.user(text)
    m.images = [small_image()]
    return m


def test_a_model_that_sees_images_gets_image_parts() -> None:
    wire = to_wire_messages([Message.system("s"), user_with_image()], vision=True)
    content = wire[1]["content"]
    assert content[0] == {"type": "text", "text": "what's wrong here?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    # Messages without images keep plain text content.
    assert to_wire_messages([Message.user("hi")], vision=True)[0]["content"] == "hi"


def test_a_text_only_model_gets_the_description() -> None:
    m = user_with_image()
    m.images[0].description = "A Python traceback:\n```\nKeyError: 'x'\n```"
    m.images[0].described_by = "qwen2.5-vl-7b"
    content = to_wire_messages([m], vision=False)[0]["content"]
    assert isinstance(content, str)
    assert content.startswith('what\'s wrong here?\n\n<image n="1" described_by="qwen2.5-vl-7b">')
    assert "KeyError: 'x'" in content
    assert "base64" not in content
    # Without a description, the model is told an image was there.
    assert "can't see images" in as_text(small_image(), 2)


def test_prompted_tools_and_no_think_handle_image_parts() -> None:
    from cmcoder.providers.openai_compat import OpenAICompatProvider

    wire = to_prompted_wire([user_with_image()], [], vision=True)
    assert wire[-1]["content"][1]["type"] == "image_url"
    provider = OpenAICompatProvider.__new__(OpenAICompatProvider)
    profile = ModelProfile(match="*", thinkingSwitch="prompt", vision=True)
    body = provider.build_request("m", [user_with_image()], [], profile, thinking=False)
    assert body["messages"][-1]["content"][0]["text"] == "what's wrong here? /no_think"


# --- sessions, context, compaction ----------------------------------------------------


def test_images_are_saved_with_the_session() -> None:
    m = user_with_image()
    m.images[0].description = "a red box"
    back = message_from_dict(json.loads(json.dumps(message_to_dict(m))))
    assert back.images == m.images
    # Old session files (no images) still load.
    assert message_from_dict({"role": "user", "content": "x"}).images == []


def test_images_count_toward_the_context_and_reach_summaries() -> None:
    m = user_with_image()
    assert message_chars(m) >= len(m.content) + TOKENS_PER_IMAGE * 4
    m.images[0].description = "a red box"
    assert "a red box" in render(m)


# --- which models see images ------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "sees"),
    [
        ("qwen2.5-vl-7b", True),
        ("qwen3-vl-32b-instruct", True),
        ("llava:13b", True),
        ("gemma3:27b", True),
        ("corp-vision-model", True),
        ("qwen3-coder-30b-a3b", False),
        ("qwen3-27b", False),
        ("devl-coder", False),
    ],
)
def test_vision_from_the_name(model: str, sees: bool) -> None:
    assert looks_like_vision(model) is sees
    assert resolve_profile(model).vision is sees


def test_settings_beat_the_gateway_which_beats_the_name() -> None:
    assert resolve_profile("qwen2.5-vl-7b", server_info={"supports_vision": False}).vision is False
    assert resolve_profile("qwen3-27b", server_info={"supports_vision": True}).vision is True
    override = [{"match": "qwen3*", "vision": False}]
    assert (
        resolve_profile("qwen3-x", override, server_info={"supports_vision": True}).vision is False
    )


# --- the agent and the vision helper ------------------------------------------------------


class Recording:
    """A model that answers with fixed text and records what it was sent."""

    name = "fake"
    base_url = "http://fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[str, list[Message], ModelProfile]] = []

    async def stream_chat(
        self, model: str, messages: list[Message], tools: list[Any], profile: Any, **kw: Any
    ) -> AsyncIterator[Any]:
        self.calls.append((model, [*messages], profile))
        yield StreamDone(Message(role="assistant", content=self.reply), Usage(1, 1), "stop")


def agent(project: Path, model: str, main: Recording, vision: Recording | None) -> Agent:
    helper = (
        Summarizer(vision, "qwen2.5-vl-7b", resolve_profile("qwen2.5-vl-7b"))  # type: ignore[arg-type]
        if vision
        else None
    )
    return Agent(
        main,  # type: ignore[arg-type]
        model,
        resolve_profile(model),
        [],
        PermissionPolicy("default"),
        ToolContext(cwd=project, project_root=project),
        "system",
        vision=helper,
    )


async def run(a: Agent, text: str, images: list[Image]) -> list[Any]:
    try:
        return [e async for e in a.run(text, images=images)]
    finally:
        await a.close()


async def test_a_text_only_model_gets_the_vision_models_description(tmp_path: Path) -> None:
    main, vision = Recording("It's a KeyError."), Recording("A traceback: KeyError: 'x'")
    a = agent(tmp_path, "qwen3-27b", main, vision)
    events = await run(a, "what's this error?", [small_image()])
    described = [e for e in events if isinstance(e, ev.ImagesDescribed)]
    assert described and described[0].model == "qwen2.5-vl-7b" and described[0].count == 1
    # The vision model saw the image, with the user's message for context.
    _, sent, profile = vision.calls[0]
    assert profile.vision is True and sent[-1].images
    assert "what's this error?" in sent[-1].content
    # The main model got the message with the description, not the image.
    message = a.messages[1]
    assert message.images[0].description == "A traceback: KeyError: 'x'"
    assert message.images[0].described_by == "qwen2.5-vl-7b"
    assert to_wire_messages([message], vision=False)[0]["content"].count("KeyError") == 1
    assert isinstance(events[-1], ev.Result) and events[-1].subtype == "success"


async def test_a_model_that_sees_images_gets_them_directly(tmp_path: Path) -> None:
    main, vision = Recording("A red box."), Recording("unused")
    a = agent(tmp_path, "qwen2.5-vl-72b", main, vision)
    events = await run(a, "what is it?", [small_image()])
    assert not vision.calls
    assert not any(isinstance(e, ev.ImagesDescribed) for e in events)
    assert main.calls[0][1][-1].images  # the image reached the main model
    assert main.calls[0][2].vision is True


async def test_without_a_vision_model_the_turn_says_why_and_doesnt_start(tmp_path: Path) -> None:
    main = Recording("unused")
    a = agent(tmp_path, "qwen3-27b", main, None)
    events = await run(a, "what's this?", [small_image()])
    errors = [e for e in events if isinstance(e, ev.Error)]
    assert errors and "visionModel" in errors[0].message and "can't see images" in errors[0].message
    assert isinstance(events[-1], ev.Result) and events[-1].is_error
    assert not main.calls
    assert len(a.messages) == 1 and a.turn == 0  # the message wasn't kept


# --- cmcoder doctor: does the model see images? --------------------------------------------


async def doctor_images(server: Any, model: str, **extra: Any) -> str:
    from rich.console import Console

    from cmcoder.cli.doctor import Doctor

    settings = Settings.model_validate(
        {"providers": {"mock": {"baseUrl": server.base_url}}, "model": f"mock:{model}", **extra}
    )
    console = Console(record=True, width=300)
    doctor = Doctor(settings, console)
    provider = make_provider(server)
    try:
        await doctor.check_images(provider, model)
    finally:
        await provider.aclose()
    await doctor.check_vision_model()
    return console.export_text()


def image_requests(server: Any) -> list[dict[str, Any]]:
    return [
        r for r in server.requests if isinstance(r.get("messages", [{}])[-1].get("content"), list)
    ]


async def test_doctor_finds_a_model_that_sees_images_unknown_to_cmcoder(mock_server: Any) -> None:
    server = mock_server([{"content": "Green."}])
    out = await doctor_images(server, "qwen3-27b")
    assert "qwen3-27b: sees images, but cmcoder doesn't know it" in out
    assert 'Add {"match": "qwen3-27b", "vision": true} to modelProfiles' in out
    content = image_requests(server)[0]["messages"][-1]["content"]
    assert content[-1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_doctor_a_model_that_sees_images(mock_server: Any) -> None:
    out = await doctor_images(mock_server([{"content": "green"}]), "qwen2.5-vl-7b")
    assert "qwen2.5-vl-7b: sees images" in out and "doesn't know" not in out


async def test_doctor_a_text_only_model_without_a_vision_model(mock_server: Any) -> None:
    out = await doctor_images(mock_server([{"content": "I can't see images."}]), "qwen3-27b")
    assert "qwen3-27b: can't see images, and no visionModel is set" in out
    assert 'It said: "I can\'t see images."' in out


async def test_doctor_checks_the_vision_model_too(
    mock_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CMCODER_API_KEY", API_KEY)
    server = mock_server([{"content": "Text only here."}, {"content": "Blue."}])
    out = await doctor_images(server, "qwen3-27b", visionModel="mock:qwen2.5-vl-7b")
    assert "qwen3-27b: can't see images; mock:qwen2.5-vl-7b describes them for it" in out
    assert "visionModel qwen2.5-vl-7b: didn't see the test image" in out  # it said blue
    assert "It said: 'Blue.'" in out
    server = mock_server([{"content": "Nothing."}, {"content": "It is green."}])
    out = await doctor_images(server, "qwen3-27b", visionModel="mock:qwen2.5-vl-7b")
    assert "visionModel qwen2.5-vl-7b: sees images" in out
