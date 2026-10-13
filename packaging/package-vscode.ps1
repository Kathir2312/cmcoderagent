<#
Builds the VS Code extension for developers' PCs, with the standalone cmcoder
inside: vscode\cmcoder-win32-x64.vsix. Developers need only VS Code: no
Python, uv or Node.js, and no admin rights (VS Code installs extensions per
user).

Run it on a build PC (not the developers'), in the repository:

    powershell -ExecutionPolicy Bypass -File packaging\package-vscode.ps1

The build PC needs:
  - uv (https://docs.astral.sh/uv/): it downloads its own Python 3.12 if
    there's none, so Python itself needn't be installed;
  - Node.js 22 or newer (packaging only);
  - Git, and the repository as a git clone (the build is named after its
    commit; a copied folder or a downloaded zip has none).

It does what the Release workflow does for this platform: builds cmcoder
with PyInstaller, runs it with no Python in reach, packages the extension
with it (and the brand in branding\), and checks cmcoder.exe is in the file.

    -SkipTests   don't run the built cmcoder before packaging (faster); also
                 goes on when git can't name the build ("unknown")
    -UseBuilt    use dist\cmcoder from an earlier run (built and checked then)

The other "cmcoder.vsix" (npm run package, or CI's cmcoder-vsix artifact)
has no cmcoder inside: it's for testing on PCs that have cmcoder already.
#>
param([switch]$SkipTests, [switch]$UseBuilt)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Need([string]$command, [string]$hint) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "$command is needed: $hint" }
}

# A step: runs a program and stops on its failure (external programs don't throw).
function Step([string]$what, [string]$folder, [scriptblock]$run) {
    Write-Host ""
    Write-Host "== $what" -ForegroundColor Cyan
    Push-Location $folder
    try {
        & $run
        if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}

Need uv "install it from https://docs.astral.sh/uv/ (it brings its own Python)"
Need node "install Node.js 22 LTS"
Need npm "it comes with Node.js"
$major = [int](node -p "process.versions.node.split('.')[0]")
if ($major -lt 22) { throw "Node.js 22 or newer is needed for packaging (this is $(node --version))" }

# The build is named after its git commit ("0.1.0 (aa3dc83 2026-10-12)", in
# --version and Copy Diagnostics). Checked first, not after a long build.
# Through cmd, so git's message comes back as text whatever the PowerShell.
Need git "install Git for Windows (the build is named after its commit)"
$commit = (cmd /c "git -C `"$root`" rev-parse --short=7 HEAD 2>&1" | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    $why = "git can't read this folder's commit, which names the build:`n  $commit`n" +
        "- 'dubious ownership': trust the folder, then run this again:`n" +
        "    git config --global --add safe.directory $($root -replace '\\', '/')`n" +
        "- 'not a git repository': use a git clone of the repository, not a copy or a downloaded zip."
    if (-not $SkipTests) { throw $why }
    Write-Warning "$why`nGoing on (-SkipTests): the build will say 'unknown' instead of its commit."
}

$exe = Join-Path $root "dist\cmcoder\cmcoder.exe"
if ($UseBuilt) {
    if (-not (Test-Path $exe)) { throw "-UseBuilt: there's no $exe (run without -UseBuilt)" }
} else {
    Step "Python environment for the build (uv)" $root { uv sync --python 3.12 }
    Step "Standalone cmcoder (PyInstaller)" $root { uv run --with pyinstaller python packaging/build.py }
    if (-not (Test-Path $exe)) { throw "The build made no $exe" }
}

if (-not $SkipTests -and -not $UseBuilt) {
    $env:CMCODER_TEST_BINARY = $exe
    $env:CMCODER_TEST_NO_PYTHON = "1"
    try {
        Step "cmcoder, run as developers run it (no Python in reach)" $root { uv run pytest -q tests/test_standalone.py }
    } finally {
        Remove-Item Env:CMCODER_TEST_BINARY, Env:CMCODER_TEST_NO_PYTHON -ErrorAction SilentlyContinue
    }
}

Step "The shared chat panel" (Join-Path $root "clients\web-panel") { npm ci; if ($LASTEXITCODE -eq 0) { npm run build } }
Step "The extension's packages" (Join-Path $root "vscode") { npm ci }
Step "The .vsix, with cmcoder inside" $root { uv run python packaging/vsix.py }

# The file developers get must hold the program.
$vsix = Get-ChildItem (Join-Path $root "vscode") -Filter "cmcoder-win32-*.vsix" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $vsix) { throw "No vscode\cmcoder-win32-*.vsix was made" }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($vsix.FullName)
try {
    $inside = $zip.Entries | Where-Object { $_.FullName -eq "extension/bin/cmcoder/cmcoder.exe" }
} finally {
    $zip.Dispose()
}
if (-not $inside) { throw "$($vsix.Name) has no extension/bin/cmcoder/cmcoder.exe" }

$hash = (Get-FileHash $vsix.FullName -Algorithm SHA256).Hash.ToLower()
Write-Host ""
Write-Host "Done: $($vsix.FullName)" -ForegroundColor Green
Write-Host ("  {0:N1} MB, SHA256 {1}" -f ($vsix.Length / 1MB), $hash)
Write-Host "  cmcoder.exe is inside. Developers: VS Code > Extensions > ... > Install from VSIX"
Write-Host "  (or: code --install-extension $($vsix.Name)). No admin rights needed."
