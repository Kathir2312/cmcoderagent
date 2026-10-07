"""Keep each request inside the model's context window.

Phase 0 safeguard for small windows (Qwen3 serves 32K by default): size
`max_tokens` to the room that is left, and when the window fills, replace the
oldest large tool outputs with a short note (as Claude Code clears old tool
results). Phase 1 adds summarisation on top of this.

Token counts are estimated from characters, calibrated against the prompt
token counts the server reports, so no tokenizer download is needed.
"""

from __future__ import annotations

import json

from ..images import TOKENS_PER_IMAGE
from ..providers.messages import Message, ToolSpec

DEFAULT_CHARS_PER_TOKEN = 3.0  # conservative for code
MIN_OUTPUT_TOKENS = 1024
PREFERRED_OUTPUT_TOKENS = 4096
SAFETY_TOKENS = 256
KEEP_RECENT_TOOL_RESULTS = 4
WARN_RATIO = 0.75

ELIDED_RESULT = "[Older tool output removed to free context. Run the tool again if you need it.]"
ELIDED_ARGS = '{"note": "arguments removed to free context"}'
_MESSAGE_OVERHEAD_CHARS = 20


def message_chars(m: Message) -> int:
    calls = sum(len(c.name) + len(c.arguments) + 40 for c in m.tool_calls)
    # An image costs about TOKENS_PER_IMAGE (a description: its own length).
    images = sum(len(i.description) or TOKENS_PER_IMAGE * 4 for i in m.images)
    return len(m.content) + calls + images + _MESSAGE_OVERHEAD_CHARS


class ContextBudget:
    def __init__(self, context_window: int, max_output: int, tools: list[ToolSpec]) -> None:
        self.window = context_window
        self.max_output = max_output
        self.tools_chars = sum(
            len(t.name) + len(t.description) + len(json.dumps(t.parameters)) + 40 for t in tools
        )
        self.chars_per_token = DEFAULT_CHARS_PER_TOKEN
        # Lowered when the server rejects a request as too long, so later
        # calibration can't return to an estimate that already failed.
        self.max_chars_per_token = 6.0

    def request_chars(self, messages: list[Message]) -> int:
        return sum(message_chars(m) for m in messages) + self.tools_chars

    def estimate(self, messages: list[Message]) -> int:
        return int(self.request_chars(messages) / self.chars_per_token)

    def calibrate(self, request_chars: int, reported_prompt_tokens: int) -> None:
        """Learn this model's chars-per-token from a server-reported count."""
        if reported_prompt_tokens > 0:
            ratio = request_chars / reported_prompt_tokens
            self.chars_per_token = min(self.max_chars_per_token, max(1.5, ratio))

    def distrust(self) -> None:
        """The server said a request was too long: estimate more tokens from now on."""
        self.chars_per_token = max(1.5, self.chars_per_token * 0.75)
        self.max_chars_per_token = self.chars_per_token

    def max_tokens(self, messages: list[Message]) -> int:
        """Output tokens to request: the profile maximum, or what still fits."""
        room = self.window - self.estimate(messages) - SAFETY_TOKENS
        return min(self.max_output, room)

    def fits(self, messages: list[Message]) -> bool:
        return self.max_tokens(messages) >= MIN_OUTPUT_TOKENS

    def free_space(
        self, messages: list[Message], keep_recent: int = KEEP_RECENT_TOOL_RESULTS
    ) -> int:
        """Replace the oldest large tool outputs (then large tool-call arguments,
        e.g. whole files passed to Write) until there is room for a reply.

        Tries to keep the `keep_recent` latest tool results; in a small window
        it keeps fewer, but always the latest one and the latest assistant
        message. Returns how many items were removed.
        """
        target = min(self.max_output, PREFERRED_OUTPUT_TOKENS)

        def roomy() -> bool:
            return self.max_tokens(messages) >= target

        tool_idx = [i for i, m in enumerate(messages) if m.role == "tool"]
        assistant_idx = [i for i, m in enumerate(messages) if m.role == "assistant"]
        removed = 0
        for keep in range(max(1, keep_recent), 0, -1):
            for i in tool_idx[: max(0, len(tool_idx) - keep)]:
                if roomy():
                    return removed
                m = messages[i]
                if len(m.content) > len(ELIDED_RESULT) * 2:
                    m.content = ELIDED_RESULT
                    removed += 1
            for i in assistant_idx[:-1]:
                for call in messages[i].tool_calls:
                    if roomy():
                        return removed
                    if len(call.arguments) > 1000:
                        call.arguments = ELIDED_ARGS
                        removed += 1
            if roomy():
                return removed
        return removed

    def usage_ratio(self, prompt_tokens: int) -> float:
        return prompt_tokens / self.window if self.window else 0.0
