"""Images over `cmcoder --protocol stdio`, as the IDEs send them: what reaches
the gateway for a model that sees images, and for one that doesn't."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image as Pil

from .conftest import API_KEY
from .test_stdio import Agent


def png_b64() -> str:
    out = io.BytesIO()
    Pil.new("RGB", (30, 20), (10, 120, 200)).save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode()


async def start(project: Path, server: Any, model: str, **env: str) -> Agent:
    environ = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    environ.update(
        {"CMCODER_BASE_URL": server.base_url, "CMCODER_API_KEY": API_KEY, "CMCODER_MODEL": model}
    )
    environ.update(env)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "cmcoder",
        "--protocol",
        "stdio",
        cwd=project,
        env=environ,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return Agent(proc)


def chat_requests(server: Any) -> list[dict[str, Any]]:
    """The conversation's requests (not cmcoder's context-window probe, a bare "hi")."""
    probe = [{"role": "user", "content": "hi"}]
    return [r for r in server.requests if "messages" in r and r["messages"] != probe]


async def test_a_model_that_sees_images_gets_the_image(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "A blue box."}])
    agent = await start(project, server, "qwen2.5-vl-7b")
    try:
        await agent.until("system_init")
        data = png_b64()
        await agent.send(
            type="user_message", text="what is it?", images=[{"data": data, "name": "shot.png"}]
        )
        done = await agent.until("result")
        assert done["subtype"] == "success"
    finally:
        await agent.close()
    content = chat_requests(server)[-1]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "what is it?"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{data}"},
    }


async def test_a_text_only_model_gets_the_vision_models_description(
    mock_server: Any, project: Path
) -> None:
    server = mock_server(
        [
            {"content": "A screenshot of a terminal:\n```\nKeyError: 'user_id'\n```"},
            {"content": "The dict has no 'user_id' key."},
        ]
    )
    agent = await start(project, server, "qwen3-27b", CMCODER_VISION_MODEL="default:qwen2.5-vl-7b")
    try:
        await agent.until("system_init")
        await agent.send(type="user_message", text="", images=[{"data": png_b64()}])
        described = await agent.until("images_described")
        assert (described["model"], described["count"]) == ("qwen2.5-vl-7b", 1)
        assert (await agent.until("result"))["subtype"] == "success"
    finally:
        await agent.close()
    vision, main = chat_requests(server)[-2:]
    assert vision["model"] == "qwen2.5-vl-7b"
    assert vision["messages"][-1]["content"][-1]["type"] == "image_url"
    assert main["model"] == "qwen3-27b"
    text = main["messages"][-1]["content"]
    assert isinstance(text, str) and "KeyError: 'user_id'" in text and "base64" not in text


async def test_without_a_vision_model_a_text_only_model_says_how_to_fix_it(
    mock_server: Any, project: Path
) -> None:
    server = mock_server([])
    agent = await start(project, server, "qwen3-27b")
    try:
        await agent.until("system_init")
        await agent.send(type="user_message", text="look", images=[{"data": png_b64()}])
        error = await agent.until("error")
        assert error["kind"] == "images" and "visionModel" in error["message"]
        assert (await agent.until("result"))["is_error"] is True
    finally:
        await agent.close()
    assert not chat_requests(server)


async def test_something_that_isnt_an_image_is_refused(mock_server: Any, project: Path) -> None:
    server = mock_server([])
    agent = await start(project, server, "qwen2.5-vl-7b")
    try:
        await agent.until("system_init")
        svg = base64.b64encode(b"<svg onload='alert(1)'/>").decode()
        await agent.send(type="user_message", text="see", images=[{"data": svg, "name": "x.png"}])
        error = await agent.until("error")
        assert error["kind"] == "images" and "isn't a PNG, JPEG, GIF or WebP" in error["message"]
    finally:
        await agent.close()
    assert not chat_requests(server)
