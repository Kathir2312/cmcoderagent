"""A persistent bash process, so `cd` and exported variables carry over between commands."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ..compat import kill_process_tree, new_process_group_kwargs

MAX_CAPTURE_BYTES = 5_000_000
# Max length of a single output line the reader accepts (default asyncio limit is 64 KiB).
STREAM_LIMIT = 16 * 1024 * 1024


@dataclass
class ShellResult:
    output: str
    exit_code: int | None
    timed_out: bool = False
    # The shell exited (e.g. the command ran `exit`) or was killed and will restart.
    restarted: bool = False


class PersistentShell:
    def __init__(self, cwd: Path, shell: str) -> None:
        self.initial_cwd = cwd
        self.shell = shell
        self._proc: asyncio.subprocess.Process | None = None
        # Killed shells still to be reaped, so their pipes are closed cleanly
        # (Windows warns about unclosed transports otherwise).
        self._killed: list[asyncio.subprocess.Process] = []
        self._lock = asyncio.Lock()

    async def _start(self) -> asyncio.subprocess.Process:
        env = dict(os.environ)
        env.update(
            {
                "CMCODER": "1",
                "PAGER": "cat",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "TERM": "dumb",
            }
        )
        self._proc = await asyncio.create_subprocess_exec(
            self.shell,
            "--noprofile",
            "--norc",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.initial_cwd,
            env=env,
            limit=STREAM_LIMIT,
            # Own process group: a timeout can kill children too, and the
            # terminal's Ctrl+C goes to cmcoder rather than the command.
            **new_process_group_kwargs(),
        )
        return self._proc

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.returncode is None:
            kill_process_tree(proc.pid)
        self._killed.append(proc)

    async def close(self) -> None:
        self._kill()
        procs, self._killed = self._killed, []
        for proc in procs:
            with suppress(TimeoutError, ProcessLookupError):
                await asyncio.wait_for(proc.wait(), 5)
            if proc.stdin is not None:
                proc.stdin.close()

    async def run(self, command: str, timeout: float) -> ShellResult:
        async with self._lock:
            proc = (
                self._proc if self._proc and self._proc.returncode is None else await self._start()
            )
            assert proc.stdin and proc.stdout
            tag = uuid.uuid4().hex
            marker = f"__CMCODER_DONE_{tag}__"
            # The command goes through a quoted heredoc + eval: no quoting issues, and a
            # syntax error fails the command instead of killing the shell. stdin is
            # /dev/null so commands that prompt for input fail fast instead of hanging.
            script = (
                f"__cmc_cmd=$(cat <<'__CMC_EOF_{tag}'\n{command}\n__CMC_EOF_{tag}\n)\n"
                f'eval "$__cmc_cmd" < /dev/null\n'
                f"__cmc_rc=$?\n"
                f"printf '\\n{marker}%d\\n' \"$__cmc_rc\"\n"
            )
            try:
                proc.stdin.write(script.encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                self._kill()
                return ShellResult(
                    "Shell process died; it will restart on the next command.", None, restarted=True
                )

            chunks: list[bytes] = []
            size = 0
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            try:
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError
                    line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                    if not line:  # EOF: the command exited the shell
                        self._kill()
                        return ShellResult(
                            _decode(chunks)
                            + "\n(shell exited; it will restart in the project root)",
                            None,
                            restarted=True,
                        )
                    if marker.encode() in line:
                        before, _, after = line.partition(marker.encode())
                        if before.strip():
                            chunks.append(before)
                        try:
                            code = int(after.strip() or b"0")
                        except ValueError:
                            code = None
                        output = _decode(chunks)
                        # Drop the newline printf put before the marker.
                        output = output[:-1] if output.endswith("\n") else output
                        return ShellResult(output, code)
                    if size < MAX_CAPTURE_BYTES:
                        chunks.append(line)
                        size += len(line)
            except TimeoutError:
                self._kill()
                return ShellResult(
                    _decode(chunks) + f"\n(command timed out after {timeout:.0f}s and was killed; "
                    "the shell restarted in the project root)",
                    None,
                    timed_out=True,
                    restarted=True,
                )
            except asyncio.CancelledError:
                self._kill()
                raise


def _decode(chunks: list[bytes]) -> str:
    # Windows programs run from Git Bash emit CRLF; normalise for the model.
    return b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n")
