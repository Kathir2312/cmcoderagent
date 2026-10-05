"""Terminal symbols: the classic Windows console gets a set its fonts can draw."""

from __future__ import annotations

from dataclasses import astuple

from rich.console import Console
from rich.spinner import Spinner

from cmcoder.cli import symbols
from cmcoder.cli.symbols import BASIC, UNICODE, choose
from cmcoder.config.settings import Settings

# What the classic console's default fonts (Consolas, Lucida Console) lack:
# each showed as "?" (reported on Windows).
MISSING_IN_CONSOLE_FONTS = set("◐◑⏸✓✗⚠☐☑✻◦━") | {chr(c) for c in range(0x2800, 0x2900)}


def all_chars(s: symbols.Symbols) -> set[str]:
    text = "".join(str(v) for v in astuple(s)[2:])  # not the name and spinner name
    return set(text) | set("".join(Spinner(s.spinner).frames))


def test_basic_set_has_nothing_the_console_fonts_lack() -> None:
    assert not all_chars(BASIC) & MISSING_IN_CONSOLE_FONTS
    assert all_chars(UNICODE) & MISSING_IN_CONSOLE_FONTS  # the full set is different
    assert set(BASIC.states) == set(UNICODE.states) and set(BASIC.todo) == set(UNICODE.todo)


def test_basic_symbols_are_in_the_console_code_page() -> None:
    # Raster fonts draw only the console's code page: each symbol must be in it.
    for codec in ("cp437", "cp850", "cp852", "cp866", "cp1252"):
        chosen = symbols.basic_symbols(codec)
        assert all(symbols.has(c, codec) for c in all_chars(chosen)), codec
    assert symbols.basic_symbols("cp437").ok == "√"  # 437 has it
    assert symbols.basic_symbols("cp850").ok == "+"  # 850 doesn't


def test_output_filter_turns_missing_characters_into_ascii() -> None:
    from io import StringIO

    # From the screenshot: "● Todo list", "☐ Read…", "… 11 more lines", "Waiting for model…".
    text = "● Todo list\n  ☐ Read ✓ ⚠ ◐\n… 11 more lines · ├─ √ ’"
    assert symbols.make_safe(text, "cp437") == (
        "* Todo list\n  [ ] Read + ! *\n... 11 more lines · ├─ √ '"
    )
    assert symbols.make_safe("√ ·", "cp850") == "+ ·"
    out = StringIO()
    stream = symbols.SafeStream(out, "cp437")  # type: ignore[arg-type]
    assert stream.write("Waiting for model…") == len("Waiting for model…")
    assert out.getvalue() == "Waiting for model..." and stream.getvalue() == out.getvalue()


def test_auto_picks_basic_only_in_the_classic_windows_console() -> None:
    win = "win32"
    assert choose("auto", {}, win).name == "basic"  # conhost: PowerShell or cmd in their own window
    for modern in (
        {"WT_SESSION": "x"},
        {"TERM_PROGRAM": "vscode"},
        {"ConEmuPID": "1"},
        {"TERM": "xterm"},
    ):
        assert choose("auto", modern, win) is UNICODE, modern
    assert choose("auto", {}, "linux") is UNICODE and choose(None, {}, "darwin") is UNICODE


def test_setting_and_environment_override() -> None:
    assert choose("unicode", {}, "win32") is UNICODE
    assert choose("basic", {"WT_SESSION": "x"}, "win32").name == "basic"
    assert choose("unicode", {"CMCODER_SYMBOLS": "basic"}, "linux").name == "basic"  # env wins
    assert Settings.model_validate({"symbols": "basic"}).symbols == "basic"
    assert Settings.model_validate({}).symbols == "auto"


def test_the_terminal_uses_the_chosen_set() -> None:
    from cmcoder.cli.agent_map import AgentMap
    from cmcoder.cli.repl import StatusView
    from cmcoder.protocol import events as ev

    m = AgentMap()
    m.start_turn("go", "m")
    m.observe(
        ev.ToolUse(id="s1", name="Read", input={}, label="Read(a.cs)", parent_tool_use_id="t")
    )
    m.observe(
        ev.SubagentStatus(
            id="t", number=1, description="Analyse core", agent_type="explore", model="m",
            state="running", steps=1, max_steps=100, tool_uses=1, tokens=10, elapsed_ms=0,
            activity="Read(a.cs)",
        )
    )  # fmt: skip
    try:
        symbols.configure("basic", {})
        console = Console(record=True, width=120, color_system=None)
        console.print(StatusView(m, "Subagents working…"))
        text = console.export_text()
        assert (
            "» main agent ─" in text
            and "── » 1. Analyse core ─" in text
            and "── Read(a.cs)" in text
        )
        assert not set(text) & MISSING_IN_CONSOLE_FONTS
        assert (
            m.final_line(
                "t",
            )
            is not None
        )
    finally:
        symbols.configure("unicode", {})
    console = Console(record=True, width=120, color_system=None)
    console.print(StatusView(m, "x"))
    assert "── ◐ 1. Analyse core ─" in console.export_text()
