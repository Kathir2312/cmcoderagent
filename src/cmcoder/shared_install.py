"""One cmcoder per user: the program in ~/.cmcoder/bin (on Windows
%USERPROFILE%\\.cmcoder\\bin), for the terminal and every IDE.

The terminal package installs there. Each IDE plugin carries its own copy
(bin/cmcoder inside the plugin, where the IDE puts plugins); started from it,
cmcoder copies itself into the shared folder in the background when that one
is missing or older, and the IDEs run the shared copy when it is at least as
new as their own (vscode/src/executable.ts, ProgramLocator in the JVM and C#
cores). So the terminal and all four IDEs run one program from one folder:
one version, one path for antivirus or AppLocker to allow. The folder is
inside cmcoder's own (protected) folder: the agent can't replace its program
without asking.

"Newer" is the build's STAMP (UTC build time, sortable), written into the
program by packaging/build.py. A copy never replaces a program that is
running from the shared folder: each one holds a marker file open
(bin.running), and the update waits for a start when none does. Two IDEs
starting at once don't both copy (a lock file, bin.updating).
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from .config.settings import config_dir

STAMP = "STAMP"
LOCK_STALE_SECONDS = 15 * 60  # a copy that takes longer than this has died
LEFTOVER_SECONDS = 24 * 3600  # bin.old-* older than this are removed (macOS, Linux)

_held: list[int] = []  # the marker stays open for the life of this process


def shared_dir() -> Path:
    return config_dir() / "bin"


def exe_name() -> str:
    return "cmcoder.exe" if os.name == "nt" else "cmcoder"


def program_dir() -> Path | None:
    """The folder of this standalone program; None when run from Python."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def stamp_of(folder: Path) -> str | None:
    """The build stamp of the program in `folder`, None if there's none (or no program)."""
    if not (folder / exe_name()).is_file():
        return None
    for path in (folder / "_internal" / "cmcoder" / STAMP, folder / "cmcoder" / STAMP):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return None


def needs_update(source: Path, target: Path) -> bool:
    new = stamp_of(source)
    if new is None:
        return False
    old = stamp_of(target)
    return old is None or old < new


def _marker(target: Path) -> Path:
    return target.parent / f"{target.name}.running"


def mark_running() -> None:
    """Running from the shared folder: hold its marker open, so no update
    swaps the program out from under this process."""
    source = program_dir()
    target = shared_dir()
    if source is None or source != target.resolve():
        return
    try:
        fd = os.open(_marker(target), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return
    if os.name != "nt":
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:  # an update is swapping it right now
            os.close(fd)
            return
    _held.append(fd)


def in_use(target: Path) -> bool:
    """Whether a cmcoder is running from `target` (it holds the marker)."""
    marker = _marker(target)
    if not marker.exists():
        return False
    if os.name == "nt":
        # A file another process has open can't be renamed.
        moved = marker.with_name(marker.name + ".check")
        try:
            marker.rename(moved)
        except PermissionError:
            return True
        except OSError:
            return False
        try:
            moved.rename(marker)
        except OSError:
            pass
        return False
    import fcntl

    try:
        fd = os.open(marker, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:  # held by a running cmcoder (or locks don't work here)
        return True
    finally:
        os.close(fd)
    return False


def _remove_leftovers(parent: Path, name: str) -> None:
    """Under the lock: an unfinished copy (bin.new-*) is abandoned. A
    replaced folder (bin.old-*) may still be in use by a program started from
    it, on macOS and Linux, for a while."""
    for leftover in parent.glob(f"{name}.new-*"):
        shutil.rmtree(leftover, ignore_errors=True)
    for leftover in parent.glob(f"{name}.old-*"):
        try:
            if os.name == "nt" or time.time() - leftover.stat().st_mtime > LEFTOVER_SECONDS:
                shutil.rmtree(leftover, ignore_errors=True)
        except OSError:
            pass


def _take_lock(lock: Path) -> int | None:
    for _ in range(2):
        try:
            return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime < LOCK_STALE_SECONDS:
                    return None  # another IDE is copying right now
                lock.unlink()
            except OSError:
                return None
    return None


def _rename(src: Path, dst: Path, attempts: int = 5) -> None:
    """A rename that waits out a virus scanner briefly holding a fresh file."""
    for i in range(attempts):
        try:
            src.rename(dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.5)


def update(source: Path, target: Path | None = None) -> str:
    """Copy the program in `source` to the shared folder, unless that one is
    at least as new. Returns what happened, for the log."""
    target = target or shared_dir()
    if source.resolve() == target.resolve():
        return "this is the shared copy"
    if not needs_update(source, target):
        return f"the shared copy in {target} is up to date"
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    lock = parent / f"{target.name}.updating"
    fd = _take_lock(lock)
    if fd is None:
        return "another IDE is copying it now"
    in_use_message = f"the shared copy in {target} is in use; it is updated on a later start"
    tag = uuid.uuid4().hex[:8]
    staging = parent / f"{target.name}.new-{tag}"
    try:
        _remove_leftovers(parent, target.name)
        if in_use(target):
            return in_use_message
        shutil.copytree(source, staging, symlinks=True)
        old: Path | None = None
        if target.exists():
            old = parent / f"{target.name}.old-{tag}"
            try:
                target.rename(old)
            except OSError:  # Windows: a cmcoder is running from it after all
                shutil.rmtree(staging, ignore_errors=True)
                return in_use_message
        try:
            _rename(staging, target)
        except OSError:
            if old is not None and not target.exists():
                old.rename(target)  # put the previous copy back
            raise
        if old is not None and os.name == "nt":
            shutil.rmtree(old, ignore_errors=True)
        elif old is not None:
            # A program that started just now may still load files from the
            # old folder: it goes a day from now (_remove_leftovers).
            os.utime(old)
        return f"copied to {target}"
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        return f"couldn't copy to {target}: {e}"
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def update_in_background(log: Callable[[str], None]) -> threading.Thread | None:
    """Started from a plugin's own copy: bring the shared copy up to date
    without holding up the conversation. None when there's nothing to do."""
    source = program_dir()
    target = shared_dir()
    if source is None or source == target.resolve() or not needs_update(source, target):
        return None

    def work() -> None:
        try:
            log(f"Copying this cmcoder to {target}, for the terminal and every IDE…")
            log(f"Shared program: {update(source, target)}.")
        except Exception as e:  # never take the session down
            log(f"Shared program: not copied ({type(e).__name__}: {e}).")

    thread = threading.Thread(target=work, name="cmcoder-shared-install", daemon=True)
    thread.start()
    return thread
