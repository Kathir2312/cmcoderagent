from __future__ import annotations

from cmcoder.cli.repl import MAX_PREVIEW_LINE_CHARS, clip_preview, short_rule


def test_short_preview_unchanged() -> None:
    assert clip_preview("a\nb\nc", 10) == ("a\nb\nc", 0)


def test_long_preview_keeps_head_and_tail() -> None:
    text = "\n".join(f"line {i}" for i in range(1, 101))
    shown, hidden = clip_preview(text, 10)
    lines = shown.splitlines()
    assert len(lines) == 10
    assert lines[0] == "line 1"
    assert lines[-1] == "line 100"
    assert hidden == 91
    assert "91 more lines" in shown


def test_tiny_terminal_still_shows_something() -> None:
    shown, hidden = clip_preview("\n".join(str(i) for i in range(50)), -5)
    assert len(shown.splitlines()) == 3 and hidden == 48


def test_very_long_lines_are_cut() -> None:
    shown, _ = clip_preview("x" * 10_000, 10)
    assert len(shown) < MAX_PREVIEW_LINE_CHARS + 5


def test_short_rule_is_one_line() -> None:
    assert short_rule("Bash(npm test:*)") == "Bash(npm test:*)"
    rule = "Bash(cat > f << 'EOF'\nrow 1\nrow 2\nEOF)"
    assert short_rule(rule) == "Bash(cat > f << 'EOF' …)"
    assert "\n" not in short_rule(rule)
    assert len(short_rule("Bash(" + "x" * 500 + ")")) <= 60
