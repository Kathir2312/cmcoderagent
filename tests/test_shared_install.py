"""One cmcoder per user in ~/.cmcoder/bin (src/cmcoder/shared_install.py):
the newest copy wins, a running one is never swapped out, two IDEs don't
copy at once, unfinished copies are cleared away."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from cmcoder import shared_install as si
from cmcoder.config.settings import config_dir


def program(folder: Path, stamp: str | None, marker: str = "") -> Path:
    """A standalone cmcoder folder (PyInstaller's layout) with this build stamp."""
    (folder / "_internal" / "cmcoder").mkdir(parents=True, exist_ok=True)
    (folder / si.exe_name()).write_text(f"program {marker}", encoding="utf-8")
    (folder / "_internal" / "lib.txt").write_text(f"library {marker}", encoding="utf-8")
    if stamp:
        (folder / "_internal" / "cmcoder" / si.STAMP).write_text(f"{stamp}\n", encoding="utf-8")
    return folder


def content(folder: Path) -> str:
    return (folder / si.exe_name()).read_text(encoding="utf-8")


@pytest.fixture
def released() -> list[int]:
    """Markers this test held, closed afterwards (as when the program exits)."""
    held = si._held
    start = len(held)
    yield held
    for fd in held[start:]:
        os.close(fd)
    del held[start:]


def test_the_shared_folder_is_in_the_users_cmcoder_folder() -> None:
    assert si.shared_dir() == config_dir() / "bin"


def test_stamps_decide_which_copy_is_newer(tmp_path: Path) -> None:
    new = program(tmp_path / "new", "20261010T120000Z")
    old = program(tmp_path / "old", "20261001T080000Z")
    assert si.stamp_of(new) == "20261010T120000Z"
    assert si.needs_update(new, old) and not si.needs_update(old, new)
    assert not si.needs_update(new, new)
    assert si.needs_update(new, tmp_path / "nothing-there")
    # A build without a stamp (run from Python, or a broken copy) never replaces one.
    unstamped = program(tmp_path / "unstamped", None)
    assert si.stamp_of(unstamped) is None
    assert not si.needs_update(unstamped, tmp_path / "nothing-there")
    assert si.needs_update(new, unstamped)
    # A stamp without the program isn't a program.
    (old / si.exe_name()).unlink()
    assert si.stamp_of(old) is None


def test_the_newest_copy_wins(tmp_path: Path) -> None:
    target = tmp_path / "home" / ".cmcoder" / "bin"
    v1 = program(tmp_path / "ide-a", "20261001T080000Z", "v1")
    v2 = program(tmp_path / "ide-b", "20261010T120000Z", "v2")

    assert si.update(v1, target) == f"copied to {target}"
    assert content(target) == "program v1" and si.stamp_of(target) == "20261001T080000Z"
    assert si.update(v2, target) == f"copied to {target}"
    assert content(target) == "program v2"
    # The older plugin starting afterwards leaves the newer copy alone.
    assert "up to date" in si.update(v1, target)
    assert content(target) == "program v2"
    assert "up to date" in si.update(v2, target)
    assert si.update(target, target) == "this is the shared copy"
    # No staging folders or locks left behind.
    left = sorted(p.name for p in target.parent.iterdir())
    if os.name == "nt":
        assert left == ["bin"]
    else:  # the replaced folder stays a day for a program started from it
        assert left[0] == "bin" and all(n.startswith("bin.old-") for n in left[1:])


def test_a_running_shared_copy_is_never_swapped_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, released: list[int]
) -> None:
    target = si.shared_dir()
    program(target, "20261001T080000Z", "v1")
    newer = program(tmp_path / "ide", "20261010T120000Z", "v2")
    assert not si.in_use(target)

    # cmcoder started from the shared folder holds its marker...
    monkeypatch.setattr(si, "program_dir", lambda: target.resolve())
    si.mark_running()
    assert len(released) == 1
    assert si.in_use(target)
    assert "in use; it is updated on a later start" in si.update(newer, target)
    assert content(target) == "program v1"
    assert not list(target.parent.glob("bin.new-*"))

    # ...until it exits: the next start brings the update.
    os.close(released.pop())
    assert not si.in_use(target)
    assert si.update(newer, target) == f"copied to {target}"
    assert content(target) == "program v2"


def test_a_program_elsewhere_holds_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, released: list[int]
) -> None:
    monkeypatch.setattr(si, "program_dir", lambda: tmp_path / "ide" / "bin")
    si.mark_running()
    monkeypatch.setattr(si, "program_dir", lambda: None)  # run from Python
    si.mark_running()
    assert released == []


def test_two_ides_dont_copy_at_once(tmp_path: Path) -> None:
    target = tmp_path / "cfg" / "bin"
    target.parent.mkdir()
    newer = program(tmp_path / "ide", "20261010T120000Z")
    lock = target.parent / "bin.updating"
    lock.write_text("")
    assert si.update(newer, target) == "another IDE is copying it now"
    assert not target.exists()
    # A lock left by a copy that died long ago doesn't block forever.
    old = time.time() - si.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))
    assert si.update(newer, target) == f"copied to {target}"
    assert not lock.exists()


def test_unfinished_copies_are_cleared_away(tmp_path: Path) -> None:
    target = tmp_path / "cfg" / "bin"
    program(target, "20261001T080000Z")
    unfinished = program(target.parent / "bin.new-dead1234", None)
    recent_old = program(target.parent / "bin.old-recent12", None)
    day_old = program(target.parent / "bin.old-dayold12", None)
    long_ago = time.time() - si.LEFTOVER_SECONDS - 60
    os.utime(day_old, (long_ago, long_ago))

    si.update(program(tmp_path / "ide", "20261010T120000Z"), target)
    assert not unfinished.exists() and not day_old.exists()
    # A folder replaced just now may still serve a program started from it
    # (macOS, Linux); on Windows nothing runs from it (it couldn't be moved).
    assert recent_old.exists() == (os.name != "nt")


def test_the_background_copy_only_runs_from_an_ides_own_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines: list[str] = []
    assert si.update_in_background(lines.append) is None  # run from Python
    plugin = program(tmp_path / "ext" / "bin" / "cmcoder", "20261010T120000Z", "plugin")
    monkeypatch.setattr(si, "program_dir", lambda: plugin)
    thread = si.update_in_background(lines.append)
    assert thread is not None
    thread.join(30)
    assert content(si.shared_dir()) == "program plugin"
    assert lines == [
        f"Copying this cmcoder to {si.shared_dir()}, for the terminal and every IDE…",
        f"Shared program: copied to {si.shared_dir()}.",
    ]
    # Up to date now: nothing to do on the next start.
    assert si.update_in_background(lines.append) is None
