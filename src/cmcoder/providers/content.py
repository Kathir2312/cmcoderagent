"""A user message's content on the wire (OpenAI chat format), with its images."""

from __future__ import annotations

from typing import Any

from ..images import as_text
from .messages import Message


def user_content(m: Message, vision: bool) -> str | list[dict[str, Any]]:
    """Text, plus the message's images: as image parts for a model that sees
    images, or as text (a vision model's description) for one that doesn't."""
    if not m.images:
        return m.content
    if not vision:
        notes = "\n".join(as_text(img, n) for n, img in enumerate(m.images, 1))
        return f"{m.content}\n\n{notes}" if m.content else notes
    parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}] if m.content else []
    parts += [{"type": "image_url", "image_url": {"url": img.data_url}} for img in m.images]
    return parts
