"""packaging/package-<package>.ps1 and .sh: one command per package that builds
it with cmcoder inside, for PCs without Python (docs/build-package/). They must do
what the Release workflow does for that file, and refuse a file without the
program. (package-vscode has its own test.) The CI workflow
packaging-scripts.yml runs them for real on Windows, macOS and Linux."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
COMMON_SH = (PACKAGING / "package-common.sh").read_text("utf-8")
COMMON_PS1 = (PACKAGING / "package-common.ps1").read_text("utf-8")

# Per package: the Release workflow's command that makes it (in the script and
# in release.yml), and the program's path inside the finished file.
PACKAGES = {
    "terminal": (["packaging/terminal.py", "tests/test_terminal_package.py"], "cmcoder/cmcoder"),
    "netbeans": (["-Dplatform=", "package"], "/bin/cmcoder/cmcoder"),
    "jetbrains": (["-PcmcoderDir=", "buildPlugin"], "/bin/cmcoder/cmcoder"),
    "eclipse": (["-DskipTests", "verify"], "bin/cmcoder/"),
    "visualstudio": (["build-vsix.ps1"], "bin/cmcoder/cmcoder.exe"),
}
SCRIPTS = [
    PACKAGING / f"package-{name}.{ext}"
    for name in PACKAGES
    for ext in ("sh", "ps1")
    if not (name == "visualstudio" and ext == "sh")  # Visual Studio: Windows only
]
SH_SCRIPTS = [s for s in SCRIPTS if s.suffix == ".sh"] + [PACKAGING / "package-all.sh"]
PS1_SCRIPTS = [s for s in SCRIPTS if s.suffix == ".ps1"] + [
    PACKAGING / "package-all.ps1",
    PACKAGING / "package-common.ps1",
    ROOT / "visualstudio" / "find-msbuild.ps1",
    ROOT / "visualstudio" / "build-vsix.ps1",
]


def package_of(script: Path) -> str:
    return script.stem.removeprefix("package-")


@pytest.mark.parametrize("common", [COMMON_SH, COMMON_PS1], ids=["sh", "ps1"])
def test_common_builds_cmcoder_as_the_release_does(common: str) -> None:
    build = "uv run --with pyinstaller python packaging/build.py"
    assert build in RELEASE and build in common
    # Run with no Python in reach, as on developers' PCs.
    assert "tests/test_standalone.py" in common and "CMCODER_TEST_NO_PYTHON" in common
    # git names the build: checked before the build, with the fixes spelled out.
    assert -1 < common.find("rev-parse") < common.find(build)
    assert "safe.directory" in common and "git clone" in common
    # The shared chat page, as the release builds it.
    assert "npm ci" in common and "npm run build" in common


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
def test_the_release_steps_in_order(script: Path) -> None:
    text = script.read_text("utf-8")
    sh = script.suffix == ".sh"
    commands, inside = PACKAGES[package_of(script)]
    for command in commands:
        assert command in RELEASE, f"release.yml no longer has {command!r}: update {script.name}"
        assert command in text, f"{script.name}: no {command!r}"
    git, build, panel = (
        ("check_git", "build_cmcoder", "build_panel")
        if sh
        else ("Assert-GitCommit", "Build-Cmcoder", "Build-Panel")
    )
    assert "package-common" in text
    assert -1 < text.find(git) < text.find(build), f"{script.name}: git is checked first"
    first = text.find(commands[0])
    if package_of(script) != "terminal":  # every IDE package carries the chat page
        assert -1 < text.find(panel) < first, f"{script.name}: the chat page before the package"
    # The finished file must hold the program (the .nbm build packs none, silently,
    # when netbeans/bin/cmcoder is missing).
    assert ("zip_has" if sh else "Assert-ZipHas") in text
    assert inside in text


@pytest.mark.parametrize("script", SH_SCRIPTS, ids=lambda s: s.name)
def test_shell_script_syntax_help_and_bad_options(script: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    subprocess.run([bash, "-n", str(script)], check=True)
    out = subprocess.run(
        [bash, str(script), "--help"], capture_output=True, text=True, check=True
    ).stdout
    assert "--skip-tests" in out and "set -e" not in out  # the comment block only
    if script.name != "package-all.sh":
        assert "no Python" in out and "--use-built" in out
    bad = subprocess.run([bash, str(script), "--nope"], capture_output=True, text=True)
    assert bad.returncode == 2


@pytest.mark.parametrize("script", PS1_SCRIPTS, ids=lambda s: s.name)
def test_powershell_script_parses(script: Path) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("no PowerShell")
    check = (
        "$e = $null; [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$e) | Out-Null; if ($e) {{ $e; exit 1 }}"
    )
    subprocess.run([pwsh, "-NoProfile", "-Command", check], check=True)


def bash_or_skip() -> str:
    bash = shutil.which("bash")
    if bash is None or sys.platform == "win32":
        pytest.skip("the .sh scripts are for macOS and Linux")
    return bash


def test_eclipse_needs_every_platform_or_a_try_out() -> None:
    bash = bash_or_skip()
    if (platform.system(), platform.machine()) not in {("Linux", "x86_64"), ("Darwin", "arm64")}:
        pytest.skip("not a platform the files are made for")
    script = str(PACKAGING / "package-eclipse.sh")
    out = subprocess.run([bash, script], capture_output=True, text=True)
    assert out.returncode == 1
    assert "every platform's cmcoder" in out.stderr and "--one-platform" in out.stderr
    # This machine's own platform may be left out (it's built here); the others not.
    other = "--darwin-arm64" if platform.system() == "Linux" else "--linux-x64"
    assert other in out.stderr and "--win32-x64" in out.stderr
    no_value = subprocess.run([bash, script, "--win32-x64"], capture_output=True, text=True)
    assert no_value.returncode == 2


def test_package_all_refuses_an_unknown_package() -> None:
    bash = bash_or_skip()
    out = subprocess.run(
        [bash, str(PACKAGING / "package-all.sh"), "--only", "terminal,nope"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 2 and "unknown package: nope" in out.stderr


def test_zip_has_finds_the_program_also_inside_nested_jars(tmp_path: Path) -> None:
    bash = bash_or_skip()
    if shutil.which("uv") is None:
        pytest.skip("no uv")
    jar = tmp_path / "fragment.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("bin/cmcoder/cmcoder", "program")
    site = tmp_path / "site.zip"
    with zipfile.ZipFile(site, "w") as z:
        z.write(jar, "plugins/cmcoder.eclipse.linux.x86_64_0.1.0.jar")
        z.writestr("cmcoder-netbeans/panel/chat.js", "page")

    def zip_has(*wanted: str) -> subprocess.CompletedProcess[str]:
        command = '. packaging/package-common.sh && zip_has "$@"'
        return subprocess.run(
            [bash, "-c", command, "zip_has", str(site), *wanted],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "root": str(ROOT)},
        )

    linux = "plugins/cmcoder.eclipse.linux.x86_64_*.jar!bin/cmcoder/cmcoder"
    assert zip_has("/panel/chat.js", linux).returncode == 0
    missing = zip_has("plugins/cmcoder.eclipse.win32.x86_64_*.jar!bin/cmcoder/cmcoder.exe")
    assert missing.returncode != 0 and "the program isn't in the file" in missing.stderr


def test_the_guides_name_the_scripts() -> None:
    pages = {
        "terminal": "cli.md",
        "netbeans": "netbeans.md",
        "jetbrains": "intellij.md",
        "eclipse": "eclipse.md",
        "visualstudio": "visualstudio.md",
    }
    for name, page in pages.items():
        text = (ROOT / "docs" / "build-package" / page).read_text("utf-8")
        assert f"package-{name}" in text, f"docs/build-package/{page} doesn't name package-{name}"
    assert "package-all" in (ROOT / "docs" / "build-package" / "README.md").read_text("utf-8")
