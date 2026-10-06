"""The no-Python environment (packaging/no_python.py) that Phase 6's tests use
to prove what developers install never needs Python."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))

import no_python  # noqa: E402

SCRIPT = Path(no_python.__file__)


def shell(command: str) -> list[str]:
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", command]
    return ["/bin/sh", "-c", command]


def test_python_uv_and_pip_are_all_trapped(tmp_path: Path) -> None:
    folder = no_python.trap(tmp_path / "trap")
    log = tmp_path / "started.log"
    base = {**os.environ, "PYTHONPATH": "/somewhere", "VIRTUAL_ENV": "/venv", "UV_CACHE_DIR": "/c"}
    env = no_python.environment(base, folder, log)
    assert not {"PYTHONPATH", "VIRTUAL_ENV", "UV_CACHE_DIR"} & set(env)
    no_python.check(env, folder)
    log.unlink(missing_ok=True)
    for name in ("python", "py", "uv", "pip"):
        result = subprocess.run(shell(f"{name} -m something"), env=env, capture_output=True)
        assert result.returncode == no_python.EXIT_CODE, name
    assert [line.split()[0] for line in no_python.started(log)] == ["python", "py", "uv", "pip"]
    assert no_python.started(log)[0].endswith("-m something")


def test_run_fails_a_command_that_starts_python(tmp_path: Path) -> None:
    uses_python = subprocess.run(
        [sys.executable, str(SCRIPT), "run", "--", *shell("python --version")],
        capture_output=True,
        text=True,
    )
    assert uses_python.returncode == no_python.EXIT_CODE
    assert "Python (or uv/pip) was started" in uses_python.stderr
    plain = subprocess.run(
        [sys.executable, str(SCRIPT), "run", "--", *shell("exit 3")],
        capture_output=True,
        text=True,
    )
    assert plain.returncode == 3  # its own exit code when Python wasn't needed


@pytest.mark.skipif(sys.platform != "win32", reason="the trap is a compiled .exe on Windows")
def test_windows_finds_the_trap_exe_by_its_bare_name(tmp_path: Path) -> None:
    folder = no_python.trap(tmp_path / "trap")
    assert (folder / "python.exe").is_file() and (folder / "py.exe").is_file()
