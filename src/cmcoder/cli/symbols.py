"""The symbols the terminal front ends draw: the spinner, tool and state marks,
the agent map's tree.

The classic Windows console (conhost) shows "?" for characters its font
lacks. With a raster font (the old default, still common) that is anything
outside the console's DOS code page (437, 850, ...): the Braille spinner, but
also ● … ☐ ✓ ◐ ⚠. Windows Terminal, the VS Code terminal and other terminals
draw them all. So there are two sets:

- ``unicode``: the full set (the default everywhere but the classic console).
- ``basic``: each symbol picked from what the console's code page has
  (√ ■ · » and the box-drawing lines in code page 437, ASCII otherwise), an
  ASCII spinner, and an output filter that turns any other character the
  code page lacks into an ASCII stand-in (… → ..., ● → *, ☐ → [ ]).

Setting ``"symbols": "auto" | "unicode" | "basic"`` (or ``CMCODER_SYMBOLS``)
chooses; ``auto`` picks ``basic`` only in the classic Windows console.
"""

from __future__ import annotations

import codecs
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TextIO

Mode = Literal["auto", "unicode", "basic"]


@dataclass(frozen=True)
class Symbols:
    name: str
    spinner: str  # a rich spinner name
    tool: str  # a tool call: "● Read(x.py)"
    note: str  # code added from the index
    warn: str
    error: str
    ok: str
    compacted: str
    states: dict[str, str] = field(default_factory=dict)  # subagent states
    todo: dict[str, str] = field(default_factory=dict)  # todo list statuses
    branch: str = "├─"
    last: str = "└─"
    pipe: str = "│"
    end: str = "└"


UNICODE = Symbols(
    name="unicode",
    spinner="dots",
    tool="●",
    note="◦",
    warn="⚠",
    error="✗",
    ok="✓",
    compacted="✻",
    states={
        "queued": "○",
        "running": "◐",
        "waiting": "⏸",
        "stopping": "◑",
        "done": "✓",
        "limit": "✓",
        "stopped": "■",
        "failed": "✗",
    },
    todo={"pending": "☐", "in_progress": "►", "completed": "☑"},
)

# What the basic set and the output filter use for characters a code page
# lacks: plain ASCII, so it shows in any console font.
ASCII_STAND_INS = {
    "…": "...",
    "●": "*",
    "•": "*",
    "○": "o",
    "◦": "-",
    "◐": "*",
    "◑": "*",
    "⏸": "!",
    "✓": "+",
    "√": "+",
    "✗": "x",
    "×": "x",
    "⚠": "!",
    "‼": "!",
    "✻": "*",
    "☐": "[ ]",
    "□": "[ ]",
    "☑": "[x]",
    "■": "#",
    "►": ">",
    "»": ">",
    "▼": "v",
    "→": "->",
    "←": "<-",
    "≈": "~",
    "━": "-",
    "─": "-",
    "│": "|",
    "├": "+",
    "└": "`",
    "·": "-",
    "—": "-",
    "–": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    " ": " ",
}


def console_codepage(platform: str = "") -> str:
    """The classic console's code page as a Python codec ("cp437", "cp850").
    UTF-8 (65001) says nothing about the font, so it counts as 437 too."""
    if (platform or sys.platform) == "win32":
        try:
            import ctypes

            cp = int(ctypes.windll.kernel32.GetConsoleOutputCP())  # type: ignore[attr-defined]
        except (AttributeError, OSError, ValueError):
            cp = 0
        if cp and cp != 65001:
            name = f"cp{cp}"
            try:
                codecs.lookup(name)
                return name
            except LookupError:
                pass
    return "cp437"


def has(char: str, codec: str) -> bool:
    """Whether the code page has this character (as a printable glyph)."""
    if char.isascii():
        return True
    try:
        encoded = char.encode(codec)
    except (UnicodeEncodeError, LookupError):
        return False
    return all(b >= 0x20 for b in encoded)  # not a control code


def basic_symbols(codec: str = "cp437") -> Symbols:
    """The basic set for a code page: each symbol the first of its candidates
    the code page has (the last candidate is always ASCII)."""

    def pick(*candidates: str) -> str:
        for c in candidates:
            if all(has(ch, codec) for ch in c):
                return c
        return candidates[-1]

    done = pick("√", "+")
    stopped = pick("■", "#")
    return Symbols(
        name="basic",
        spinner="line",  # - \ | /
        tool=pick("»", "*"),
        note=pick("·", "-"),
        warn="!",
        error="x",
        ok=done,
        compacted="*",
        states={
            "queued": pick("·", "."),
            "running": pick("»", ">"),
            "waiting": "!",
            "stopping": "-",
            "done": done,
            "limit": done,
            "stopped": stopped,
            "failed": "x",
        },
        todo={"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"},
        branch=pick("├─", "+-"),
        last=pick("└─", "`-"),
        pipe=pick("│", "|"),
        end=pick("└", "`"),
    )


BASIC = basic_symbols("cp437")


def make_safe(text: str, codec: str) -> str:
    """Text the console can show: characters the code page lacks become
    ASCII stand-ins (others are left to Windows, which maps many itself)."""
    if text.isascii():
        return text
    return "".join(
        ch if has(ch, codec) or ch not in ASCII_STAND_INS else ASCII_STAND_INS[ch] for ch in text
    )


class SafeStream:
    """A text stream that passes everything through `make_safe` (the classic
    console with `basic` symbols)."""

    def __init__(self, stream: TextIO, codec: str) -> None:
        self._stream = stream
        self._codec = codec

    def write(self, text: str) -> int:
        self._stream.write(make_safe(text, self._codec))
        return len(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def classic_windows_console(environ: Mapping[str, str] | None = None, platform: str = "") -> bool:
    """True in the classic Windows console (not Windows Terminal, the VS Code
    terminal, ConEmu, or a terminal like mintty that sets TERM)."""
    env = os.environ if environ is None else environ
    if (platform or sys.platform) != "win32":
        return False
    modern = (
        env.get("WT_SESSION")  # Windows Terminal
        or env.get("TERM_PROGRAM")  # VS Code, and others that say who they are
        or env.get("ConEmuPID")
        or env.get("TERM")  # mintty (Git Bash), MSYS2, Cygwin
    )
    return not modern


def wants_basic(
    mode: str | None, environ: Mapping[str, str] | None = None, platform: str = ""
) -> bool:
    env = os.environ if environ is None else environ
    mode = (env.get("CMCODER_SYMBOLS") or mode or "auto").strip().lower()
    if mode in ("basic", "unicode"):
        return mode == "basic"
    return classic_windows_console(env, platform)


def choose(
    mode: str | None, environ: Mapping[str, str] | None = None, platform: str = ""
) -> Symbols:
    if not wants_basic(mode, environ, platform):
        return UNICODE
    return basic_symbols(console_codepage(platform))


def configure(mode: str | None, environ: Mapping[str, str] | None = None) -> Symbols:
    """Pick the set for this process's terminal (the CLI calls this at start).
    In the classic Windows console, `basic` also filters stdout and stderr."""
    global _current
    _current = choose(mode, environ)
    if _current.name == "basic" and sys.platform == "win32":
        codec = console_codepage()
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name)
            if stream is not None and not isinstance(stream, SafeStream) and stream.isatty():
                setattr(sys, name, SafeStream(stream, codec))
    return _current


def sym() -> Symbols:
    return _current


_current = choose(None)  # until the settings are read
