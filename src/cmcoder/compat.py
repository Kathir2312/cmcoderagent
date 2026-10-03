"""Platform differences (Windows vs POSIX) in one place.

On Windows, cmcoder runs shell commands with Git Bash (from Git for Windows),
as Claude Code does, so the Bash tool and its permission rules behave the
same on every OS.
"""

from __future__ import annotations

import asyncio
import os
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

IS_WINDOWS = sys.platform == "win32"

SHELL_HELP = (
    "cmcoder runs commands with bash. On Windows, install Git for Windows "
    "(https://git-scm.com/download/win), which includes Git Bash, or set "
    "CMCODER_GIT_BASH_PATH to the full path of bash.exe."
)


def find_program(name: str) -> str | None:
    """Full path of a program on PATH, like `shutil.which`, but never from the
    current folder.

    On Windows, `shutil.which` and starting a program by bare name both look in
    the current folder first. cmcoder runs inside the user's project, so a
    cloned repository could ship its own `git.exe` or `rg.exe` and have it run.
    Only absolute PATH entries other than the current folder are searched.
    """
    try:
        here = Path.cwd().resolve()
    except OSError:
        here = None
    dirs: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue  # "" and "." mean the current folder
        try:
            if here is not None and Path(entry).resolve() == here:
                continue
        except OSError:
            continue
        dirs.append(entry)
    if IS_WINDOWS:
        pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").lower().split(";")
        exts = [e for e in pathext if e]
        if Path(name).suffix.lower() in exts:
            exts = [""]
    else:
        exts = [""]
    for d in dirs:
        for ext in exts:
            candidate = os.path.join(d, name + ext)
            if os.path.isfile(candidate) and (IS_WINDOWS or os.access(candidate, os.X_OK)):
                return candidate
    return None


def system_program(name: str) -> str:
    """A Windows system program (e.g. taskkill) by its full System32 path."""
    root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
    return os.path.join(root, "System32", name)


def _usable_windows_bash(path: str | Path) -> bool:
    low = str(path).lower()
    # System32\bash.exe and the WindowsApps alias start WSL, which sees a
    # different filesystem; they can't run commands in the user's project.
    return "system32" not in low and "windowsapps" not in low and Path(path).is_file()


def find_shell() -> str | None:
    """Path of the bash executable to use, or None if there is none."""
    override = os.environ.get("CMCODER_GIT_BASH_PATH") or os.environ.get("CMCODER_SHELL")
    if override:
        return override if Path(override).is_file() else None
    if not IS_WINDOWS:
        return find_program("bash")

    candidates: list[Path] = []
    on_path = find_program("bash")
    if on_path and _usable_windows_bash(on_path):
        candidates.append(Path(on_path))
    git = find_program("git")
    if git:
        # ...\Git\cmd\git.exe or ...\Git\bin\git.exe -> ...\Git\bin\bash.exe
        for parent in list(Path(git).resolve().parents)[:3]:
            candidates += [parent / "bin" / "bash.exe", parent / "usr" / "bin" / "bash.exe"]
    for base in (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMW6432"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")
        if os.environ.get("LOCALAPPDATA")
        else None,
    ):
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if _usable_windows_bash(c):
            return str(c)
    return None


# Known Folder IDs (Windows): 64-bit Program Files, then the process's own.
_FOLDERID_PROGRAM_FILES = (
    "{6D809377-6AF0-444B-8957-A3773F02200E}",  # FOLDERID_ProgramFilesX64
    "{905E63B6-C1BF-494E-B29C-65B732D3D21A}",  # FOLDERID_ProgramFiles
)


def program_files_dir() -> Path:
    """C:\\Program Files, asked from Windows itself (SHGetKnownFolderPath).

    Not taken from %ProgramFiles%, which any user can change for their own
    processes. Falls back to C:\\Program Files.
    """
    if IS_WINDOWS:
        import ctypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [  # noqa: RUF012 - ctypes layout
                ("Data1", ctypes.c_uint32),
                ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        for folder_id in _FOLDERID_PROGRAM_FILES:
            u = uuid.UUID(folder_id)
            guid = GUID(
                u.time_low,
                u.time_mid,
                u.time_hi_version,
                (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]),
            )
            out = ctypes.c_wchar_p()
            try:
                hr = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
                    ctypes.byref(guid), 0, None, ctypes.byref(out)
                )
                if hr == 0 and out.value:
                    path = Path(out.value)
                    ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
                    return path
            except Exception:  # any failure: use the standard location
                pass
    return Path("C:/Program Files")


def to_shell_path(path: str | Path) -> str:
    """A path as bash sees it: C:\\Users\\me -> /c/Users/me on Windows (Git Bash)."""
    p = str(path)
    if IS_WINDOWS and len(p) >= 2 and p[1] == ":":
        return "/" + p[0].lower() + p[2:].replace("\\", "/")
    return p


def from_shell_path(path: str) -> str:
    """Accept Git Bash paths on Windows: /c/Users/me -> C:/Users/me. Others unchanged."""
    if (
        IS_WINDOWS
        and len(path) >= 2
        and path[0] == "/"
        and path[1].isalpha()
        and (len(path) == 2 or path[2] == "/")
    ):
        return f"{path[1].upper()}:/{path[3:]}"
    return path


def new_process_group_kwargs() -> dict[str, Any]:
    """Start a child in its own process group, so it (and its children) can be
    killed as a unit and doesn't receive the terminal's Ctrl+C directly."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_process_tree(pid: int) -> None:
    if sys.platform == "win32":
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [system_program("taskkill.exe"), "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=15,
            )
    else:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)


def stdin_has_data(wait: float = 0.2) -> bool:
    """True when input is piped in (`cat file | cmcoder -p ...`).

    An open but idle stdin (e.g. when launched by another program) must not
    block, so only report data that arrives (or EOF) within `wait` seconds.
    """
    if sys.stdin is None or sys.stdin.isatty():
        return False
    try:
        fileno = sys.stdin.fileno()
        mode = os.fstat(fileno).st_mode
    except (OSError, ValueError):
        return False
    if stat.S_ISREG(mode):  # `cmcoder -p < file`
        return True
    if sys.platform == "win32":
        if not stat.S_ISFIFO(mode):
            return False
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fileno))
        available = wintypes.DWORD()
        deadline = time.monotonic() + wait
        while True:
            ok = ctypes.windll.kernel32.PeekNamedPipe(
                handle, None, 0, None, ctypes.byref(available), None
            )
            if not ok:  # writer closed the pipe: reading returns EOF immediately
                return True
            if available.value:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
    import select

    try:
        ready, _, _ = select.select([sys.stdin], [], [], wait)
    except (OSError, ValueError):
        return False
    return bool(ready)


class InterruptHandler:
    """Route Ctrl+C to a callback while armed (e.g. cancel the running turn).

    Uses the event loop's signal support on POSIX and a plain signal handler
    on Windows, where `loop.add_signal_handler` is not available.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self._previous: Any = None
        self._mode: str | None = None

    def arm(self, callback: Callable[[], object]) -> None:
        self.disarm()
        try:
            self.loop.add_signal_handler(signal.SIGINT, callback)
            self._mode = "loop"
        except (NotImplementedError, RuntimeError):
            self._previous = signal.signal(
                signal.SIGINT, lambda signum, frame: self.loop.call_soon_threadsafe(callback)
            )
            self._mode = "signal"

    def disarm(self) -> None:
        if self._mode == "loop":
            with suppress(NotImplementedError, RuntimeError, ValueError):
                self.loop.remove_signal_handler(signal.SIGINT)
        elif self._mode == "signal":
            with suppress(ValueError, TypeError):
                signal.signal(signal.SIGINT, self._previous or signal.default_int_handler)
        self._mode = None
        self._previous = None


def use_utf8_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows consoles/pipes using a legacy code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")
