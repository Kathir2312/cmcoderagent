"""Drive the interactive REPL through a real pseudo-terminal."""

from __future__ import annotations

import json
import os
import re
import select
import sys
import time
from pathlib import Path
from typing import Any

import pytest

pty = pytest.importorskip("pty")

ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]")
CMCODER = str(Path(sys.executable).parent / "cmcoder")
PROMPT = "1 yes · 2 always · 3 no(?: · v view all)?:".encode()


class Term:
    def __init__(
        self, project: Path, server: Any, *args: str, cols: int = 100, lines: int = 40
    ) -> None:
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        env.update(
            CMCODER_BASE_URL=server.base_url,
            CMCODER_API_KEY="sk-test-key",
            CMCODER_MODEL="qwen3-27b",
            TERM="xterm",
            COLUMNS=str(cols),
            LINES=str(lines),
        )
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child
            os.chdir(project)
            os.execve(CMCODER, [CMCODER, *args], env)
        self.buf = b""

    def expect(self, pattern: bytes, timeout: float = 15) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.2)
            if ready:
                try:
                    self.buf += os.read(self.fd, 65536)
                except OSError:
                    break
            if re.search(pattern, ANSI.sub(b"", self.buf)):
                return True
        return False

    def send(self, text: str) -> None:
        # Real users don't type within microseconds of a prompt appearing; give
        # prompt_toolkit time to switch the terminal into its input mode.
        time.sleep(0.3)
        os.write(self.fd, text.encode())

    def text(self) -> str:
        return ANSI.sub(b"", self.buf).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.send("/exit\r")
            time.sleep(0.5)
            os.kill(self.pid, 9)
        except OSError:
            pass
        os.waitpid(self.pid, 0)


def test_permission_prompt_and_allow_always(mock_server: Any, project: Path) -> None:
    server = mock_server(
        [
            {
                "content": "I'll create it.",
                "tool_calls": [
                    {
                        "name": "Write",
                        "arguments": {"file_path": "notes.md", "content": "# Notes\n"},
                    }
                ],
            },
            {"content": "Created notes.md."},
        ]
    )
    term = Term(project, server)
    try:
        assert term.expect(rb"/help for commands")
        term.send("make notes\r")
        assert term.expect(PROMPT), term.text()
        term.send("2\r")
        assert term.expect(rb"Created notes\.md\."), term.text()
        term.send("/cost\r")
        assert term.expect(rb"Tokens: \d+ in"), term.text()
    finally:
        term.close()
    out = term.text()
    assert "Allow Write(notes.md)?" in out
    assert (project / "notes.md").read_text() == "# Notes\n"
    saved = json.loads((project / ".cmcoder/settings.local.json").read_text())
    assert saved["permissions"]["allow"] == ["Edit(/notes.md)"]


@pytest.mark.parametrize("with_prompt", [True, False])
def test_ctrl_c_interrupts_running_command(
    mock_server: Any, project: Path, with_prompt: bool
) -> None:
    server = mock_server([{"tool_calls": [{"name": "Bash", "arguments": {"command": "sleep 30"}}]}])
    args = () if with_prompt else ("--permission-mode", "bypassPermissions")
    term = Term(project, server, *args)
    try:
        assert term.expect(rb"/help for commands")
        term.send("run it\r")
        if with_prompt:
            assert term.expect(PROMPT)
            term.send("1\r")
        assert term.expect(rb"Bash\(sleep 30\)")
        time.sleep(1.0)
        started = time.time()
        term.send("\x03")
        assert term.expect(rb"Interrupted", timeout=5), term.text()
        assert time.time() - started < 5
        # The session is still usable afterwards.
        term.send("/mode\r")
        assert term.expect(rb"Permission mode:")
    finally:
        term.close()


def test_long_preview_keeps_options_on_screen(mock_server: Any, project: Path) -> None:
    """User-trial bug: a 500-line heredoc pushed the options off an 80x24 screen."""
    # A long script fed to Python (file writes like `cat > f << EOF` are now
    # redirected to the Write tool before any prompt; see core/steer.py).
    body = "\n".join(f"# row {i}" for i in range(1, 501))
    command = f"python3 - << 'EOF'\n{body}\nEOF"
    server = mock_server(
        [
            {"tool_calls": [{"name": "Bash", "arguments": {"command": command}}]},
            {"content": "OK, stopping."},
        ]
    )
    term = Term(project, server, cols=80, lines=24)
    try:
        assert term.expect(rb"/help for commands")
        term.send("write the file\r")
        assert term.expect(PROMPT), term.text()
        out = term.text()
        prompt_screen = out[out.index("Allow Bash") :]
        prompt_screen = prompt_screen[: prompt_screen.index("v view all:") + 1]
        # Panel, preview, options and input line all fit in one 24-line screen.
        assert prompt_screen.count("\n") < 24, prompt_screen
        assert re.search(r"\brow 1\b", prompt_screen)  # first lines and the last one shown
        assert "row 500" in prompt_screen
        assert not re.search(r"\brow 250\b", prompt_screen)
        assert "more lines (v to view all)" in prompt_screen
        # Invalid input re-shows the options instead of a bare prompt.
        term.send("11\r")
        assert term.expect(rb"Please answer"), term.text()
        # `v` shows everything, then the options again.
        term.send("v\r")
        assert term.expect(rb"row 250"), term.text()
        term.send("3\r")
        assert term.expect(rb"what should cmcoder do instead"), term.text()
        term.send("\r")
        assert term.expect(rb"denied by user"), term.text()
    finally:
        term.close()
    assert not (project / "big.txt").exists()


def test_compact_command(mock_server: Any, project: Path) -> None:
    (project / "a.txt").write_text("hello\n")
    server = mock_server(
        [
            {"tool_calls": [{"name": "Read", "arguments": {"file_path": "a.txt"}}]},
            {"content": "It says hello."},
        ]
    )
    term = Term(project, server)
    try:
        assert term.expect(rb"/help for commands")
        term.send("/compact\r")
        assert term.expect(rb"Nothing to compact yet"), term.text()
        term.send("what is in a.txt?\r")
        assert term.expect(rb"It says hello\."), term.text()
        term.send("/compact keep the file name\r")
        assert term.expect(rb"Compacted the conversation: summarised \d+ earlier"), term.text()
    finally:
        term.close()
    assert "keep the file name" in json.dumps(server.state.summary_requests)


def test_resume_command(mock_server: Any, project: Path) -> None:
    server = mock_server([{"content": "Apple noted."}, {"content": "It was apple."}])
    term = Term(project, server)
    try:
        assert term.expect(rb"/help for commands")
        term.send("remember the word apple\r")
        assert term.expect(rb"Apple noted\."), term.text()
        term.send("/clear\r")
        assert term.expect(rb"Started a new conversation"), term.text()
        term.send("/resume\r")
        assert term.expect(rb"remember the word apple"), term.text()
        assert term.expect(rb"resume which\?"), term.text()
        term.send("1\r")
        assert term.expect(rb"Resumed conversation [0-9a-f]{8} \(2 messages\)"), term.text()
        term.send("which word?\r")
        assert term.expect(rb"It was apple\."), term.text()
    finally:
        term.close()
    contents = [m["content"] for m in server.requests[1]["messages"]]
    assert "remember the word apple" in contents


def test_rewind_command(mock_server: Any, project: Path) -> None:
    (project / "app.py").write_text("x = 1\n")
    server = mock_server(
        [
            {"tool_calls": [{"name": "Read", "arguments": {"file_path": "app.py"}}]},
            {
                "tool_calls": [
                    {
                        "name": "Edit",
                        "arguments": {
                            "file_path": "app.py",
                            "old_string": "x = 1",
                            "new_string": "x = 2",
                        },
                    }
                ]
            },
            {"content": "Set x to 2."},
        ]
    )
    term = Term(project, server, "--permission-mode", "acceptEdits")
    try:
        assert term.expect(rb"/help for commands")
        term.send("set x to 2\r")
        assert term.expect(rb"Set x to 2\."), term.text()
        assert (project / "app.py").read_text() == "x = 2\n"
        term.send("/rewind\r")
        assert term.expect(rb"rewind to before which message\?"), term.text()
        term.send("1\r")
        assert term.expect(rb"1 code and conversation"), term.text()
        term.send("1\r")
        assert term.expect(rb"Files: 1 restored"), term.text()
        assert term.expect(rb"> set x to 2"), term.text()  # the message is back in the input line
    finally:
        term.close()
    assert (project / "app.py").read_text() == "x = 1\n"


def test_todo_list_is_shown_as_a_checklist(mock_server: Any, project: Path) -> None:
    todos = [
        {"content": "Read the code", "status": "completed"},
        {"content": "Fix the bug", "status": "in_progress"},
        {"content": "Run the tests", "status": "pending"},
    ]
    server = mock_server(
        [{"tool_calls": [{"name": "TodoWrite", "arguments": {"todos": todos}}]}, {"content": "ok"}]
    )
    term = Term(project, server)
    try:
        assert term.expect(rb"/help for commands")
        term.send("fix it\r")
        assert term.expect("◐ Fix the bug".encode()), term.text()
        term.send("/todos\r")
        assert term.expect("☐ Run the tests[\\s\\S]*☐ Run the tests".encode()), term.text()
    finally:
        term.close()
