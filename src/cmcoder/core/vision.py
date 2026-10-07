"""The vision helper (settings `visionModel`): describes the user's images for a
main model that can't see images.

The description replaces the image for that model, so it has to carry
everything: every piece of text exactly (code, errors, logs, names), and what
the picture shows. It is written once, saved with the session, and reused.
"""

from __future__ import annotations

from ..images import Image
from ..providers.messages import Message, StreamDone, Usage
from .compaction import Summarizer

SYSTEM_PROMPT = """\
You describe an image for a coding assistant that cannot see it. Your \
description replaces the image, so leave nothing out that the assistant may need.

- Transcribe every piece of visible text exactly as written: code, error \
messages, stack traces, logs, terminal output, file and folder names, UI labels, \
values in tables. Put code, logs and terminal output in fenced code blocks.
- Then describe what the image shows: the application or kind of screen, the \
layout, which element is selected, highlighted or marked in red, dialogs, \
diagrams and charts (their parts, arrows and values).
- Be factual and complete. Don't guess what the user wants and don't answer \
their question: only describe the image."""

MAX_DESCRIPTION_TOKENS = 2000


class VisionError(Exception):
    """The vision model didn't describe the image."""


async def describe(helper: Summarizer, image: Image, user_text: str) -> tuple[str, Usage]:
    """The vision model's description of `image`; `user_text` is the user's
    message, so the description can be precise where the user is looking."""
    note = f"The user's message that came with the image:\n{user_text}" if user_text.strip() else ""
    message = Message.user(note)
    message.images = [image]
    # The user named this model as the one that sees images.
    profile = helper.profile.model_copy(update={"vision": True})
    done: StreamDone | None = None
    async for ev in helper.provider.stream_chat(
        helper.model,
        [Message.system(SYSTEM_PROMPT), message],
        [],
        profile,
        thinking=False,
        max_tokens=MAX_DESCRIPTION_TOKENS,
    ):
        if isinstance(ev, StreamDone):
            done = ev
    if done is None or not done.message.content.strip():
        raise VisionError(f"{helper.model} returned no description")
    return done.message.content.strip(), done.usage
