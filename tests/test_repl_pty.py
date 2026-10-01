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


class Term:
    def __init__(self, project: Path, server: Any, *args: str) -> None:
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        env.update(
            CMCODER_BASE_URL=server.base_url,
            CMCODER_API_KEY="sk-test-key",
            CMCODER_MODEL="qwen3-27b",
            TERM="xterm",
            COLUMNS="100",
            LINES="40",
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
        assert term.expect(rb"choose \[1/2/3\]"), term.text()
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
            assert term.expect(rb"choose \[1/2/3\]")
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
