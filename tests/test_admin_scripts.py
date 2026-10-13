"""packaging/admin: the scripts IT runs to install the managed settings. They
refuse a file that would stop cmcoder for every user, and what they install
is what cmcoder reads."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cmcoder.config.settings import read_managed_settings

ROOT = Path(__file__).resolve().parent.parent
ADMIN = ROOT / "packaging" / "admin"
WINDOWS = sys.platform == "win32"
GOOD = {"lockProviders": True, "permissions": {"deny": ["Bash(curl:*)"]}}


def files(tmp_path: Path) -> dict[str, Path]:
    out = {}
    for name, text in {
        "good": json.dumps(GOOD),
        "bom": "﻿" + json.dumps(GOOD),  # saved by Notepad or PowerShell 5
        "array": "[1, 2]",
        "broken": '{"lockProviders": tru',
        "example": (ROOT / "docs" / "examples" / "managed-settings.example.json").read_text(
            "utf-8"
        ),
    }.items():
        out[name] = tmp_path / f"{name}.json"
        out[name].write_text(text, encoding="utf-8")
    return out


@pytest.mark.skipif(WINDOWS, reason="install-managed.sh is for macOS and Linux")
def test_install_managed_on_macos_and_linux(tmp_path: Path) -> None:
    src = files(tmp_path)
    target = tmp_path / "etc" / "cmcoder" / "managed-settings.json"
    env = {**os.environ, "CMCODER_MANAGED_TARGET": str(target)}

    def run(name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(ADMIN / "install-managed.sh"), str(src[name])],
            env=env,
            capture_output=True,
            text=True,
        )

    for bad in ("array", "broken", "example"):
        result = run(bad)
        assert result.returncode == 1, (bad, result.stdout, result.stderr)
        assert not target.exists()
    assert "placeholders" in run("example").stderr

    first = run("good")
    assert first.returncode == 0, first.stderr
    assert "Installed the managed settings" in first.stdout
    assert read_managed_settings(target)["lockProviders"] is True
    assert target.stat().st_mode & 0o777 == 0o644  # everyone reads, root writes
    assert "Replaced" in run("good").stdout

    # Not root, no test target: it says how to run it, and changes nothing.
    env.pop("CMCODER_MANAGED_TARGET")
    if os.geteuid() != 0:
        result = run("good")
        assert result.returncode == 1 and "sudo" in result.stderr


@pytest.mark.skipif(not WINDOWS, reason="install-managed.ps1 is for Windows")
def test_install_managed_on_windows(tmp_path: Path) -> None:
    src = files(tmp_path)
    target = tmp_path / "Program Files" / "cmcoder" / "managed-settings.json"
    ps = shutil.which("powershell") or "powershell.exe"

    def run(name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
            + [str(ADMIN / "install-managed.ps1"), "-Source", str(src[name])]
            + ["-Target", str(target)],
            capture_output=True,
            text=True,
        )

    for bad in ("array", "broken", "example"):
        result = run(bad)
        assert result.returncode != 0, (bad, result.stdout, result.stderr)
        assert not target.exists()

    first = run("bom")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "Installed the managed settings" in first.stdout
    assert not target.read_bytes().startswith(b"\xef\xbb\xbf")  # written without a BOM
    assert read_managed_settings(target)["lockProviders"] is True
    assert "Replaced" in run("good").stdout
