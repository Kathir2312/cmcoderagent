"""packaging/package-vscode.ps1 and .sh: one command that builds the VS Code
extension with cmcoder inside, for PCs without Python. They must do what the
Release workflow does for that file, and refuse a .vsix without the program
(the development cmcoder.vsix has none)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "packaging" / "package-vscode.ps1"
SH = ROOT / "packaging" / "package-vscode.sh"
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")

# The Release workflow's steps for the VS Code file, in order.
STEPS = [
    "uv run --with pyinstaller python packaging/build.py",
    "tests/test_standalone.py",
    "npm ci",
    "uv run python packaging/vsix.py",
]


@pytest.mark.parametrize("script", [PS1, SH], ids=["ps1", "sh"])
def test_the_release_steps_in_order(script: Path) -> None:
    text = script.read_text("utf-8")
    at = -1
    for step in STEPS:
        assert step in RELEASE, f"release.yml no longer has {step!r}: update the scripts"
        found = text.find(step, at + 1)
        assert found > at, f"{script.name}: {step!r} missing or out of order"
        at = found
    # Run with no Python in reach, as on developers' PCs.
    assert "CMCODER_TEST_NO_PYTHON" in text
    # The web panel is built before the extension (vscode's build imports it).
    assert text.find("clients") < text.find("uv run python packaging/vsix.py")


@pytest.mark.parametrize("script", [PS1, SH], ids=["ps1", "sh"])
def test_checks_the_program_is_in_the_file(script: Path) -> None:
    text = script.read_text("utf-8")
    exe = "cmcoder.exe" if script.suffix == ".ps1" else "cmcoder"
    assert f"extension/bin/cmcoder/{exe}" in text


def test_shell_script_syntax_and_help() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    subprocess.run([bash, "-n", str(SH)], check=True)
    out = subprocess.run(
        [bash, str(SH), "--help"], capture_output=True, text=True, check=True
    ).stdout
    assert "no Python" in out and "--skip-tests" in out
    bad = subprocess.run([bash, str(SH), "--nope"], capture_output=True, text=True)
    assert bad.returncode == 2


def test_powershell_script_parses() -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("no PowerShell")
    check = (
        "$e = $null; [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{PS1}', [ref]$null, [ref]$e) | Out-Null; if ($e) {{ $e; exit 1 }}"
    )
    subprocess.run([pwsh, "-NoProfile", "-Command", check], check=True)
