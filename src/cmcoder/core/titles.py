"""Session titles, written by the small/fast model.

After the first turn of a conversation, the small model is asked for a short
title in the background. Nothing waits for it, and if it fails the title
falls back to the start of the first message.
"""

from __future__ import annotations

import re

from ..providers.messages import Message, StreamDone, Usage
from .compaction import Summarizer

# Recognised by the mock server in tests; harmless to real models.
TITLE_TAG = "<cmcoder-title>"
SYSTEM_PROMPT = f"""{TITLE_TAG}
Write a short title (3 to 7 words) for a coding conversation that starts
with the user's message below. Reply with the title only: no quotes, no
punctuation at the end, no explanation."""
MAX_PROMPT_CHARS = 2000
MAX_TITLE_CHARS = 60


def clean_title(text: str) -> str | None:
    """First non-empty line, without quotes, labels or trailing punctuation."""
    for line in text.splitlines():
        line = line.strip().strip("*#`\"'“”‘’ ").strip()
        line = re.sub(r"^(title|session title)\s*:\s*", "", line, flags=re.I).strip("\"'“” ")
        line = line.rstrip(".:;!,")
        if line:
            return line[:MAX_TITLE_CHARS].rstrip()
    return None


async def make_title(summarizer: Summarizer, first_message: str) -> tuple[str | None, Usage]:
    messages = [Message.system(SYSTEM_PROMPT), Message.user(first_message[:MAX_PROMPT_CHARS])]
    done: StreamDone | None = None
    async for ev in summarizer.provider.stream_chat(
        summarizer.model, messages, [], summarizer.profile, thinking=False, max_tokens=40
    ):
        if isinstance(ev, StreamDone):
            done = ev
    if done is None:
        return None, Usage()
    return clean_title(done.message.content), done.usage
