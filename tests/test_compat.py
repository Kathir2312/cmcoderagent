from __future__ import annotations

import os
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
    monkeypatch.setattr(compat, "find_program", lambda name: which.get(name))
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
    monkeypatch.setattr(compat, "find_program", lambda name: None)
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


def _program(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return p


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX names; Windows rules are tested below")
def test_find_program_never_uses_the_current_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SAST finding (Bandit B607, verified by hand): a program started by bare
    name, or found with shutil.which, comes from the current folder first on
    Windows. cmcoder runs in the user's project, so a cloned repository could
    plant git.exe or rg.exe. find_program searches absolute PATH entries only."""
    project, tools = tmp_path / "project", tmp_path / "tools"
    _program(project, "git")  # planted
    real = _program(tools, "git")
    monkeypatch.chdir(project)
    for path in (f".{os.pathsep}{tools}", f"{os.pathsep}{tools}", f"{project}{os.pathsep}{tools}"):
        monkeypatch.setenv("PATH", path)
        assert compat.find_program("git") == str(real), path
    monkeypatch.setenv("PATH", f".{os.pathsep}{project}")
    assert compat.find_program("git") is None


def test_find_program_on_windows_rules(
    windows: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, tools = tmp_path / "project", tmp_path / "tools"
    _program(project, "git.exe")  # planted
    real = _program(tools, "git.exe")
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    assert compat.find_program("git") == str(real)
    assert compat.find_program("git.exe") == str(real)
    assert (
        compat.system_program("taskkill.exe").lower().endswith("system32" + os.sep + "taskkill.exe")
    )


def test_every_subprocess_sets_stdin() -> None:
    """Under --protocol stdio a thread reads stdin; on Windows a child process
    that inherits that pipe can hang when it starts (seen: a subagent's prompt
    running git mid-turn). So every subprocess cmcoder starts names its stdin."""
    import ast

    calls = {
        "subprocess": {"run", "Popen", "call", "check_call", "check_output"},
        "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    }
    missing = []
    for path in (Path(__file__).parent.parent / "src" / "cmcoder").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.attr in calls.get(node.func.value.id, set())
                and not any(k.arg == "stdin" for k in node.keywords)
            ):
                missing.append(f"{path.name}:{node.lineno}")
    assert missing == []
