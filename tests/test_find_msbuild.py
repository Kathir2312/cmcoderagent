"""visualstudio/find-msbuild.ps1: Visual Studio's MSBuild for the extension
build, or a message that says what's installed and what's missing (a build PC
can have Visual Studio's installer from another product, such as SQL Server
Management Studio, without any Visual Studio). Run with a stand-in vswhere in
Windows PowerShell, as build PCs run it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "visualstudio" / "find-msbuild.ps1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None,
    reason="Windows PowerShell, as on build PCs",
)

# Answers like vswhere: -find from msbuild.txt, -format json from
# instances.json, -property instanceId from ids.txt.
VSWHERE = r"""@echo off
echo %* | findstr /C:"-find" >nul
if not errorlevel 1 goto find
echo %* | findstr /C:"-format" >nul
if not errorlevel 1 goto json
echo %* | findstr /C:"-property" >nul
if not errorlevel 1 goto ids
exit /b 0
:find
if exist "%~dp0msbuild.txt" type "%~dp0msbuild.txt"
exit /b 0
:json
type "%~dp0instances.json"
exit /b 0
:ids
if exist "%~dp0ids.txt" type "%~dp0ids.txt"
exit /b 0
"""


def find_msbuild(
    tmp_path: Path, instances: list[dict[str, str]], ids: str = "", msbuild: str = ""
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "vswhere.cmd").write_text(VSWHERE.replace("\n", "\r\n"), encoding="ascii")
    (tmp_path / "instances.json").write_text(json.dumps(instances, indent=2), encoding="utf-8")
    if ids:
        (tmp_path / "ids.txt").write_text(ids, encoding="ascii")
    if msbuild:
        (tmp_path / "msbuild.txt").write_text(msbuild, encoding="ascii")
    env = {**os.environ, "CMCODER_VSWHERE": str(tmp_path / "vswhere.cmd")}
    # The message itself on stdout: PowerShell's error display wraps long lines mid-word.
    command = f"try {{ & '{SCRIPT}' }} catch {{ Write-Output ('ERROR: ' + $_.Exception.Message); exit 1 }}"
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_prints_msbuild_when_there_is_one(tmp_path: Path) -> None:
    exe = r"C:\VS\MSBuild\Current\Bin\MSBuild.exe"
    out = find_msbuild(tmp_path, [], msbuild=exe)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == exe


def test_says_what_is_installed_and_why_it_does_not_do(tmp_path: Path) -> None:
    instances = [
        {
            "instanceId": "ssms",
            "displayName": "SQL Server Management Studio 21",
            "installationVersion": "21.1.3.0",
        },
        {
            "instanceId": "vs19",
            "displayName": "Visual Studio Professional 2019",
            "installationVersion": "16.11.35.0",
        },
    ]
    out = find_msbuild(tmp_path, instances, ids="vs19")
    assert out.returncode != 0
    message = out.stdout
    assert "SQL Server Management Studio 21 (21.1.3.0): no MSBuild" in message
    assert "Visual Studio Professional 2019 (16.11.35.0): too old" in message
    assert "Visual Studio extension development" in message


def test_says_when_only_the_installer_is_there(tmp_path: Path) -> None:
    out = find_msbuild(tmp_path, [])
    assert out.returncode != 0
    assert "no Visual Studio is installed" in out.stdout
