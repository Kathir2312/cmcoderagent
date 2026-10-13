<#
Builds the terminal package (the standalone cmcoder CLI) for developers' PCs:
dist\cmcoder-terminal-win32-x64.zip, with cmcoder.exe and its install scripts.
Developers need no Python, uv or Node.js, and no admin rights.

Run it on a build PC (not the developers'), in the repository:

    powershell -ExecutionPolicy Bypass -File packaging\package-terminal.ps1

The build PC needs uv (https://docs.astral.sh/uv/; it downloads its own
Python 3.12) and Git for Windows, with the repository as a git clone (the
build is named after its commit). docs\building\cli.md has the details.

It does what the Release workflow does for this file: builds cmcoder with
PyInstaller, runs it with no Python in reach, packs it with the install
scripts, tests the package, and checks cmcoder.exe is in the file.

    -SkipTests   leave out the test runs (try-outs only, never for release)
    -UseBuilt    use dist\cmcoder from an earlier run (built and checked then)
#>
param([switch]$SkipTests, [switch]$UseBuilt)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "package-common.ps1")

Assert-GitCommit
$exe = Build-Cmcoder

Step "The terminal package" $root { uv run python packaging/terminal.py }
if (-not $SkipTests) {
    $env:CMCODER_TEST_BINARY = $exe
    try {
        Step "The package's tests" $root { uv run pytest -q tests/test_terminal_package.py }
    } finally {
        Remove-Item Env:CMCODER_TEST_BINARY -ErrorAction SilentlyContinue
    }
}

$out = Join-Path $root "dist\cmcoder-terminal-win32-x64.zip"
Copy-Item (Join-Path $root "dist\terminal\cmcoder-win32-x64.zip") $out -Force
Assert-ZipHas $out @("cmcoder-win32-x64/cmcoder/cmcoder.exe", "cmcoder-win32-x64/install.cmd")
Show-Done $out "Developers: unzip it, double-click install.cmd, open a new terminal, run cmcoder doctor."
