"""Streaming splitter for reasoning written inline as <think>...</think>.

Qwen3 (and other open models) put reasoning in the content stream when the
server has no reasoning parser enabled. Tags can be split across chunks, so the
splitter holds back any tail that could be the start of a tag.
"""

from __future__ import annotations

OPEN = "<think>"
CLOSE = "</think>"


def _partial_tag_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of `text` that is a proper prefix of `tag`."""
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:n]):
            return n
    return 0


class ThinkSplitter:
    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False
        self._seen_text = False

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """Feed a content chunk; returns a list of ("text"|"reasoning", piece)."""
        self._buf += chunk
        out: list[tuple[str, str]] = []
        while self._buf:
            if self._in_think:
                idx = self._buf.find(CLOSE)
                if idx >= 0:
                    self._emit(out, "reasoning", self._buf[:idx])
                    self._buf = self._buf[idx + len(CLOSE) :]
                    self._in_think = False
                    # Drop the blank lines models put after </think>.
                    self._buf = self._buf.lstrip("\n")
                    continue
                hold = _partial_tag_suffix(self._buf, CLOSE)
                self._emit(out, "reasoning", self._buf[: len(self._buf) - hold])
                self._buf = self._buf[len(self._buf) - hold :]
                break
            idx = self._buf.find(OPEN)
            if idx >= 0:
                self._emit(out, "text", self._buf[:idx])
                self._buf = self._buf[idx + len(OPEN) :]
                self._in_think = True
                continue
            hold = _partial_tag_suffix(self._buf, OPEN)
            self._emit(out, "text", self._buf[: len(self._buf) - hold])
            self._buf = self._buf[len(self._buf) - hold :]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self._emit(out, "reasoning" if self._in_think else "text", self._buf)
        self._buf = ""
        return out

    def _emit(self, out: list[tuple[str, str]], kind: str, piece: str) -> None:
        if not piece:
            return
        if kind == "text" and not self._seen_text:
            # Leading whitespace before the answer is noise from the template.
            piece = piece.lstrip()
            if not piece:
                return
            self._seen_text = True
        out.append((kind, piece))
