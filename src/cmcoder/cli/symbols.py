"""The symbols the terminal front ends draw: the spinner, tool and state marks,
the agent map's tree.

The classic Windows console (conhost, in its default Consolas or Lucida
Console font) has no glyphs for Braille spinners, ◐ ⏸ ✓ ✗ ⚠ ☐ and the like:
it shows "?" for each. Windows Terminal, the VS Code terminal and other
terminals draw them. So there are two sets:

- ``unicode``: the full set (the default everywhere but the classic console).
- ``basic``: only characters from WGL4, the set every Windows console font
  has (● ○ ■ □ ► √ × ‼ ▼ · and the box-drawing lines), and an ASCII spinner.

Setting ``"symbols": "auto" | "unicode" | "basic"`` (or ``CMCODER_SYMBOLS``)
chooses; ``auto`` picks ``basic`` only in the classic Windows console.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

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

BASIC = Symbols(
    name="basic",
    spinner="line",  # - \ | /
    tool="●",
    note="·",
    warn="!",
    error="×",
    ok="√",
    compacted="*",
    states={
        "queued": "○",
        "running": "►",
        "waiting": "‼",
        "stopping": "▼",
        "done": "√",
        "limit": "√",
        "stopped": "■",
        "failed": "×",
    },
    todo={"pending": "□", "in_progress": "►", "completed": "■"},
)


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


def choose(
    mode: str | None, environ: Mapping[str, str] | None = None, platform: str = ""
) -> Symbols:
    env = os.environ if environ is None else environ
    mode = (env.get("CMCODER_SYMBOLS") or mode or "auto").strip().lower()
    if mode == "basic":
        return BASIC
    if mode == "unicode":
        return UNICODE
    return BASIC if classic_windows_console(env, platform) else UNICODE


def configure(mode: str | None, environ: Mapping[str, str] | None = None) -> Symbols:
    """Pick the set for this process's terminal (the CLI calls this at start)."""
    global _current
    _current = choose(mode, environ)
    return _current


def sym() -> Symbols:
    return _current


_current = choose(None)  # until the settings are read
