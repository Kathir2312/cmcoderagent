"""The terminal package (packaging/terminal.py and packaging/terminal/):
build the zip, unzip it, install, update, run, uninstall, as a developer
would. With CMCODER_TEST_BINARY the real standalone program is packaged and
run with no Python in reach; otherwise a stand-in program is used."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import no_python  # noqa: E402
import terminal  # noqa: E402

BINARY = os.environ.get("CMCODER_TEST_BINARY")
WINDOWS = sys.platform == "win32"


def program(tmp_path: Path) -> Path:
    """The real standalone folder, or a stand-in that answers --version."""
    if BINARY:
        return Path(BINARY).resolve().parent
    if WINDOWS:
        pytest.skip("on Windows this needs the built program (CMCODER_TEST_BINARY)")
    folder = tmp_path / "program"
    (folder / "_internal").mkdir(parents=True)
    (folder / "_internal" / "lib.txt").write_text("library\n")
    exe = folder / "cmcoder"
    exe.write_text('#!/bin/sh\n[ "$1" = --version ] && echo 9.9.9 && exit 0\necho "args: $*"\n')
    exe.chmod(0o644)  # the package must make it executable itself
    return folder


def unzip(archive: Path, into: Path) -> Path:
    """Unzip as the system's tools do (keeping the executable bits)."""
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(into)
        for info in zf.infolist():
            mode = (info.external_attr >> 16) & 0o777
            if mode and not WINDOWS:
                (into / info.filename).chmod(mode)
    [top] = list(into.iterdir())
    return top


def build(tmp_path: Path) -> Path:
    return terminal.build(program(tmp_path), tmp_path / "out", "test-x64", windows=WINDOWS)


def test_the_zip_holds_the_program_scripts_and_readme(tmp_path: Path) -> None:
    archive = build(tmp_path)
    assert archive.name == "cmcoder-test-x64.zip"
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        exe = "cmcoder-test-x64/cmcoder/" + ("cmcoder.exe" if WINDOWS else "cmcoder")
        assert exe in names
        scripts = terminal.WINDOWS_SCRIPTS if WINDOWS else terminal.POSIX_SCRIPTS
        assert {f"cmcoder-test-x64/{s}" for s in scripts} <= names
        readme = zf.read("cmcoder-test-x64/README.txt").decode()
        assert terminal.version() in readme and "{" not in readme
        if not WINDOWS:
            assert (zf.getinfo(exe).external_attr >> 16) & stat.S_IXUSR
            assert (zf.getinfo("cmcoder-test-x64/install.sh").external_attr >> 16) & stat.S_IXUSR


@pytest.mark.skipif(WINDOWS, reason="install.sh is for macOS and Linux")
def test_install_update_run_and_uninstall_on_macos_and_linux(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text("# my own settings\nexport EDITOR=vi\n")
    top = unzip(build(tmp_path), tmp_path / "unzipped")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    if BINARY:  # the real program: nothing it does may need Python
        trap = no_python.trap(tmp_path / "trap")
        log = tmp_path / "started.log"
        env = no_python.environment(env, trap, log)

    first = subprocess.run(["sh", str(top / "install.sh")], env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    installed = home / ".local" / "share" / "cmcoder" / "cmcoder"
    link = home / ".local" / "bin" / "cmcoder"
    assert link.is_symlink() and link.resolve() == installed.resolve()
    assert os.access(installed, os.X_OK)
    assert "Added" in first.stdout and "Open a NEW terminal" in first.stdout
    profile = (home / ".profile").read_text()
    assert profile.startswith("# my own settings") and profile.count("cmcoder's install.sh") == 1

    again = subprocess.run(["sh", str(top / "install.sh")], env=env, capture_output=True, text=True)
    assert again.returncode == 0, again.stderr  # an update: same place, PATH line not repeated
    assert (home / ".profile").read_text().count("cmcoder's install.sh") == 1

    # A new login shell finds it by name.
    shell = subprocess.run(
        ["sh", "-c", ". ~/.profile && cmcoder --version"], env=env, capture_output=True, text=True
    )
    assert shell.returncode == 0, shell.stderr
    # The standalone build says "0.1.0 (<commit> <date>)".
    assert shell.stdout.split()[0] == ("9.9.9" if not BINARY else terminal.version())
    if BINARY:
        doctor = subprocess.run(
            [str(link), "doctor", "--no-probe"], env=env, capture_output=True, text=True
        )
        assert "cmcoder" in doctor.stdout
        assert no_python.started(log) == []

    gone = subprocess.run(
        ["sh", str(top / "uninstall.sh")], env=env, capture_output=True, text=True
    )
    assert gone.returncode == 0, gone.stderr
    assert not installed.exists() and not link.exists()
    assert (home / ".profile").read_text() == "# my own settings\nexport EDITOR=vi\n"


@pytest.mark.skipif(not WINDOWS, reason="install.ps1 is for Windows")
@pytest.mark.skipif(not os.environ.get("CI"), reason="changes the user's PATH: CI runners only")
def test_install_run_and_uninstall_on_windows(tmp_path: Path) -> None:
    import winreg

    def user_path() -> str:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                return str(winreg.QueryValueEx(key, "Path")[0])
            except FileNotFoundError:
                return ""

    before = user_path()
    top = unzip(build(tmp_path), tmp_path / "unzipped folder ä")  # spaces, non-ASCII
    dest = tmp_path / "Programs dir" / "cmcoder"
    ps = shutil.which("powershell") or "powershell.exe"
    trap = no_python.trap(tmp_path / "trap")
    log = tmp_path / "started.log"
    env = no_python.environment(os.environ, trap, log)
    args = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    try:
        first = subprocess.run(
            [*args, str(top / "install.ps1"), "-Destination", str(dest)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert first.returncode == 0, first.stdout + first.stderr
        assert (dest / "cmcoder.exe").is_file()
        assert terminal.version() in first.stdout
        entries = [e.rstrip("\\").lower() for e in user_path().split(";")]
        assert str(dest).lower() in entries
        again = subprocess.run(
            [*args, str(top / "install.ps1"), "-Destination", str(dest)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert again.returncode == 0, again.stdout + again.stderr
        assert [e.rstrip("\\").lower() for e in user_path().split(";")].count(
            str(dest).lower()
        ) == 1
        doctor = subprocess.run(
            [str(dest / "cmcoder.exe"), "doctor", "--no-probe"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert "cmcoder" in doctor.stdout
        assert no_python.started(log) == []
        gone = subprocess.run(
            [*args, str(top / "uninstall.ps1"), "-Destination", str(dest)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert gone.returncode == 0, gone.stdout + gone.stderr
        assert not dest.exists()
        assert str(dest).lower() not in [e.rstrip("\\").lower() for e in user_path().split(";")]
    finally:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, before)
