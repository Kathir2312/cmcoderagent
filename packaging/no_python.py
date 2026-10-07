"""An environment with no Python in reach, for testing what developers install
(Phase 6: developers' PCs have no Python, uv or pip).

Taking Python off PATH isn't possible on every system (on Linux it sits in
/usr/bin with bash and git; on Windows the `py` launcher is in C:\\Windows), so
instead a "trap" folder goes first on PATH with programs named python,
python3, py, pip, uv, ... that record that they were started and fail. A
program that needs Python then fails its test, and the record says what it
tried to run. PYTHON* and other Python variables are removed too.

    python packaging/no_python.py run -- <command> [args...]

runs a command that way (the IDE tests, a built program) and fails if anything
under it started Python. Tests use `trap()` and `environment()` directly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

NAMES = ("python", "python3", "pythonw", "py", "pip", "pip3", "uv", "uvx", "pipx")
# Variables that point a program at a Python installation or environment.
PYTHON_VARIABLES = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "VIRTUAL_ENV")
PYTHON_PREFIXES = ("PYTHON", "UV_", "CONDA_", "PIPX_", "PIP_")
LOG_VARIABLE = "CMCODER_PYTHON_TRAP_LOG"
EXIT_CODE = 97

_POSIX_TRAP = """#!/bin/sh
# cmcoder's no-Python check: this stands in for {name}. Being run means
# something needed Python on a machine that won't have it.
[ -n "$CMCODER_PYTHON_TRAP_LOG" ] && echo "{name} $*" >> "$CMCODER_PYTHON_TRAP_LOG"
echo "cmcoder no-Python check: {name} was started ($*)" >&2
exit {code}
"""

# Compiled with the C# compiler that is part of every Windows (.NET
# Framework 4), so the trap is a real .exe: Windows finds a bare "python" as
# python.exe, not as a .cmd.
_WINDOWS_TRAP = """
class Trap {
    static int Main(string[] args) {
        string name = System.IO.Path.GetFileNameWithoutExtension(
            System.Environment.GetCommandLineArgs()[0]);
        string line = name + " " + string.Join(" ", args);
        string log = System.Environment.GetEnvironmentVariable("CMCODER_PYTHON_TRAP_LOG");
        if (!string.IsNullOrEmpty(log)) System.IO.File.AppendAllText(log, line + "\\n");
        System.Console.Error.WriteLine("cmcoder no-Python check: " + name + " was started (" + line + ")");
        return EXIT_CODE_HERE;
    }
}
""".replace("EXIT_CODE_HERE", str(EXIT_CODE))


def _csc() -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\\Windows"))
    for framework in ("Framework64", "Framework"):
        csc = windir / "Microsoft.NET" / framework / "v4.0.30319" / "csc.exe"
        if csc.is_file():
            return csc
    raise RuntimeError("The C# compiler of .NET Framework 4 (csc.exe) wasn't found")


def trap(folder: Path) -> Path:
    """Fill `folder` with the stand-in programs; returns it."""
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        source = folder / "trap.cs"
        source.write_text(_WINDOWS_TRAP, encoding="utf-8")
        exe = folder / "trap.exe"
        subprocess.run(
            [str(_csc()), "/nologo", "/target:exe", f"/out:{exe}", str(source)],
            check=True,
            capture_output=True,
        )
        for name in NAMES:
            shutil.copyfile(exe, folder / f"{name}.exe")
    else:
        for name in NAMES:
            path = folder / name
            path.write_text(_POSIX_TRAP.format(name=name, code=EXIT_CODE), encoding="utf-8")
            path.chmod(0o755)
    return folder


def environment(base: Mapping[str, str], folder: Path, log: Path) -> dict[str, str]:
    """`base` with the trap first on PATH and Python's variables removed."""
    env = {
        k: v
        for k, v in base.items()
        if k.upper() not in PYTHON_VARIABLES and not k.upper().startswith(PYTHON_PREFIXES)
    }
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    env[path_key] = os.pathsep.join([str(folder), env.get(path_key, "")])
    env[LOG_VARIABLE] = str(log)
    return env


def started(log: Path) -> list[str]:
    """What the trap recorded (empty: nothing needed Python)."""
    try:
        return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def check(env: Mapping[str, str], folder: Path) -> None:
    """Prove the environment works: each name finds the trap, and running it fails."""
    path = next(v for k, v in env.items() if k.upper() == "PATH")
    for name in NAMES:
        found = shutil.which(name, path=path)
        if found is None or Path(found).parent != folder:
            raise RuntimeError(f"{name} resolves to {found}, not the trap")
    # Through a shell started in `env`, so the lookup uses env's PATH (Windows
    # would otherwise search this process's PATH).
    if sys.platform == "win32":
        shell = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "python --version"]
    else:
        shell = ["/bin/sh", "-c", "python --version"]
    # A fixed command; env is this test tool's own.
    result = subprocess.run(shell, env=dict(env), capture_output=True)  # nosemgrep
    if result.returncode != EXIT_CODE:
        raise RuntimeError(f"python ran ({result.returncode}): the trap isn't first on PATH")


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[0] != "run" or argv[1] != "--":
        print(__doc__, file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="cmcoder-no-python-") as tmp:
        folder = trap(Path(tmp) / "trap")
        log = Path(tmp) / "started.log"
        env = environment(os.environ, folder, log)
        check(env, folder)
        log.unlink(missing_ok=True)  # the check's own python run
        code = subprocess.run(argv[2:], env=env).returncode
        if found := started(log):
            print("Python (or uv/pip) was started, which developers won't have:", file=sys.stderr)
            for line in found:
                print(f"  {line}", file=sys.stderr)
            return EXIT_CODE
        return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
