from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cmcoder import compat


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compat, "IS_WINDOWS", True)


def test_shell_path_conversion(windows: None) -> None:
    assert compat.to_shell_path(r"C:\Users\me\proj") == "/c/Users/me/proj"
    assert compat.from_shell_path("/c/Users/me/proj") == "C:/Users/me/proj"
    assert compat.from_shell_path("/d") == "D:/"
    assert compat.from_shell_path("/usr/bin") == "/usr/bin"
    assert compat.from_shell_path("src/a.py") == "src/a.py"


def test_shell_paths_unchanged_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compat, "IS_WINDOWS", False)
    assert compat.from_shell_path("/c/Users") == "/c/Users"
    assert compat.to_shell_path("/home/me") == "/home/me"


def test_find_git_bash_next_to_git(
    windows: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_root = tmp_path / "Git"
    (git_root / "cmd").mkdir(parents=True)
    (git_root / "bin").mkdir()
    (git_root / "cmd" / "git.exe").write_text("")
    (git_root / "bin" / "bash.exe").write_text("")
    wsl = tmp_path / "Windows" / "System32" / "bash.exe"
    wsl.parent.mkdir(parents=True)
    wsl.write_text("")
    which = {"bash": str(wsl), "git": str(git_root / "cmd" / "git.exe")}
    monkeypatch.setattr(compat.shutil, "which", lambda name: which.get(name))
    for var in (
        "CMCODER_GIT_BASH_PATH",
        "CMCODER_SHELL",
        "PROGRAMFILES",
        "PROGRAMW6432",
        "PROGRAMFILES(X86)",
        "LOCALAPPDATA",
    ):
        monkeypatch.delenv(var, raising=False)
    # The WSL launcher in System32 is skipped in favour of Git Bash.
    assert compat.find_shell() == str(git_root / "bin" / "bash.exe")


def test_find_shell_override_and_missing(
    windows: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "bash.exe"
    custom.write_text("")
    monkeypatch.setenv("CMCODER_GIT_BASH_PATH", str(custom))
    assert compat.find_shell() == str(custom)
    monkeypatch.setenv("CMCODER_GIT_BASH_PATH", str(tmp_path / "nope.exe"))
    assert compat.find_shell() is None
    monkeypatch.delenv("CMCODER_GIT_BASH_PATH")
    monkeypatch.setattr(compat.shutil, "which", lambda name: None)
    for var in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / "empty"))
    assert compat.find_shell() is None


async def test_interrupt_handler_routes_ctrl_c() -> None:
    import asyncio
    import signal

    if sys.platform == "win32":
        pytest.skip("raising SIGINT in-process is not reliable on Windows")
    loop = asyncio.get_running_loop()
    handler = compat.InterruptHandler(loop)
    fired = asyncio.Event()
    handler.arm(fired.set)
    try:
        signal.raise_signal(signal.SIGINT)
        await asyncio.wait_for(fired.wait(), 2)
    finally:
        handler.disarm()
    assert fired.is_set()


async def test_interrupt_handler_signal_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows code path: no loop.add_signal_handler, plain signal handler instead."""
    import asyncio
    import signal

    if sys.platform == "win32":
        pytest.skip("raising SIGINT in-process is not reliable on Windows")
    loop = asyncio.get_running_loop()

    def unsupported(*args: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(loop, "add_signal_handler", unsupported)
    handler = compat.InterruptHandler(loop)
    fired = asyncio.Event()
    before = signal.getsignal(signal.SIGINT)
    handler.arm(fired.set)
    try:
        signal.raise_signal(signal.SIGINT)
        await asyncio.wait_for(fired.wait(), 2)
    finally:
        handler.disarm()
    assert fired.is_set()
    assert signal.getsignal(signal.SIGINT) == before
