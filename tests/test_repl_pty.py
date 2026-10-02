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
