"""A persistent bash process, so `cd` and exported variables carry over between commands."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..compat import kill_process_tree, new_process_group_kwargs

if TYPE_CHECKING:
    from ..sandbox import Sandbox

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
    # Exit codes of each command in the last pipeline, e.g. [1, 0] for a failed
    # `pip install x | tail -5` (whose own exit code is tail's 0).
    pipe_status: list[int] | None = None


def child_env(cwd: Path) -> dict[str, str]:
    """The environment for commands: cmcoder's own virtualenv taken out.

    `uv run cmcoder` (or an activated venv) puts cmcoder's venv first on PATH,
    so `python`, `pytest` and `pip` would be cmcoder's, not the user's project's.
    Kept when the venv lives inside the project (cmcoder installed in the
    project's own venv)."""
    env = dict(os.environ)
    venv = Path(sys.prefix).resolve()
    if venv == Path(sys.base_prefix).resolve():
        return env  # not in a venv
    with suppress(ValueError, OSError):
        cwd.resolve().relative_to(venv.parent)
        return env  # the project's own venv
    key = next((k for k in env if k.upper() == "PATH"), "PATH")

    def inside(entry: str) -> bool:
        try:
            return Path(entry).resolve().is_relative_to(venv)
        except (OSError, ValueError):
            return False

    env[key] = os.pathsep.join(p for p in env.get(key, "").split(os.pathsep) if p and not inside(p))
    virtual_env = env.get("VIRTUAL_ENV")
    if virtual_env and inside(virtual_env):
        del env["VIRTUAL_ENV"]
    for name in ("PYTHONHOME", "UV_RUN_RECURSION_DEPTH"):
        env.pop(name, None)
    return env


class PersistentShell:
    def __init__(self, cwd: Path, shell: str, sandbox: Sandbox | None = None) -> None:
        self.initial_cwd = cwd
        # Where the shell is now (`cd` carries over between commands), as it
        # reported after the last one; None until then, or after a restart.
        self.current_cwd: Path | None = None
        self.shell = shell
        self.sandbox = sandbox  # run the shell inside it
        self._proc: asyncio.subprocess.Process | None = None
        # Killed shells still to be reaped, so their pipes are closed cleanly
        # (Windows warns about unclosed transports otherwise).
        self._killed: list[asyncio.subprocess.Process] = []
        self._lock = asyncio.Lock()

    async def _start(self) -> asyncio.subprocess.Process:
        env = child_env(self.initial_cwd)
        env.update(
            {
                "CMCODER": "1",
                "PAGER": "cat",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "TERM": "dumb",
            }
        )
        argv = [self.shell, "--noprofile", "--norc"]
        if self.sandbox is not None:
            argv, sandbox_env = await self.sandbox.shell_command(self.shell, self.initial_cwd)
            for name in self.sandbox.hidden_variables(env):
                env.pop(name, None)
            env.update(sandbox_env)
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
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
        self.current_cwd = None  # a new shell starts in initial_cwd
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
            # Close the pipes ourselves rather than waiting for EOF: on Windows,
            # programs started by Git Bash (e.g. a `sleep`) can keep stdout open
            # after the shell is killed. asyncio has no public API for this.
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                transport.close()
        if procs:
            # Let the pipes' close callbacks run before the caller may close the
            # event loop; otherwise Windows warns about unclosed transports.
            for _ in range(3):
                await asyncio.sleep(0)

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
            # The exit codes are read inside the eval, where PIPESTATUS still
            # describes the command's last pipeline (empty on a syntax error).
            script = (
                f"__cmc_cmd=$(cat <<'__CMC_EOF_{tag}'\n{command}\n__CMC_EOF_{tag}\n)\n"
                "__cmc_rc= __cmc_ps=\n"
                "eval \"$__cmc_cmd\"$'\\n''__cmc_rc=$? __cmc_ps=\"${PIPESTATUS[*]}\"' < /dev/null\n"
                "__cmc_rc=${__cmc_rc:-$?}\n"
                # The folder too (Git Bash: as C:/..., which Windows paths understand).
                f'printf \'\\n{marker}%d %s\\t%s\\n\' "$__cmc_rc" "$__cmc_ps" '
                '"$(pwd -W 2>/dev/null || pwd)"\n'
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
                        code: int | None
                        status, _, where = after.partition(b"\t")
                        try:
                            codes = [int(x) for x in status.split()] or [0]
                            code, pipe = codes[0], codes[1:]
                        except ValueError:
                            code, pipe = None, []
                        where_text = where.strip().decode("utf-8", "replace")
                        self.current_cwd = Path(where_text) if where_text else None
                        output = _decode(chunks)
                        # Drop the newline printf put before the marker.
                        output = output[:-1] if output.endswith("\n") else output
                        return ShellResult(
                            output, code, pipe_status=pipe if len(pipe) > 1 else None
                        )
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
