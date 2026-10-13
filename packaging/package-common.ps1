<#
Shared by the packaging\package-*.ps1 scripts (dot-sourced, not run): checking
the build machine's tools, naming the build after its git commit, building
the standalone cmcoder and running it with no Python in reach, building the
shared chat page, and checking what's inside a finished file. Works in
Windows PowerShell 5.1 and PowerShell 7.

The calling script sets, before using these:
  $root       the repository
  $SkipTests  leave out the test runs (try-outs only)
  $UseBuilt   use dist\cmcoder from an earlier run (built and checked then)
#>

function Need([string]$command, [string]$hint) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "$command is needed: $hint" }
}

# A step: runs a block and stops on a program's failure (programs don't throw).
# Their output goes to the screen, not to the caller.
function Step([string]$what, [string]$folder, [scriptblock]$run) {
    Write-Host ""
    Write-Host "== $what" -ForegroundColor Cyan
    Push-Location $folder
    try {
        $global:LASTEXITCODE = 0
        & $run | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}

# A program's output as text, through cmd (Windows PowerShell turns a program's
# error output into errors otherwise).
function Text-Of([string]$commandLine) {
    return (cmd /c "$commandLine 2>&1" | Out-String)
}

function Need-Node {
    Need node "install Node.js 22 LTS"
    Need npm "it comes with Node.js"
    $major = [int](node -p "process.versions.node.split('.')[0]")
    if ($major -lt 22) { throw "Node.js 22 or newer is needed (this is $(node --version))" }
}

function Need-Java([int]$min) {
    Need java "install a JDK $min or newer (for example Eclipse Temurin)"
    $m = [regex]::Match((Text-Of "java -XshowSettings:properties -version"), 'java\.specification\.version = (?:1\.)?(\d+)')
    if (-not $m.Success -or [int]$m.Groups[1].Value -lt $min) {
        throw "JDK $min or newer is needed: set JAVA_HOME and PATH to it"
    }
}

function Need-Maven {
    Need mvn "install Apache Maven 3.9 or newer"
    $m = [regex]::Match((Text-Of "mvn -v"), 'Apache Maven (\d+)\.(\d+)')
    if (-not $m.Success -or ([int]$m.Groups[1].Value -eq 3 -and [int]$m.Groups[2].Value -lt 9) -or [int]$m.Groups[1].Value -lt 3) {
        throw "Maven 3.9 or newer is needed"
    }
}

function Need-Gradle {
    Need gradle "install Gradle 9.8.1 (https://gradle.org/releases/)"
    $m = [regex]::Match((Text-Of "gradle --version"), 'Gradle (\d+)\.')
    if (-not $m.Success -or [int]$m.Groups[1].Value -lt 9) { throw "Gradle 9 or newer is needed (9.8.1 is what CI uses)" }
}

# The build is named after its git commit ("0.1.0 (aa3dc83 2026-10-12)", in
# --version and Copy Diagnostics). Checked first, not after a long build.
function Assert-GitCommit {
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
}

# The standalone cmcoder in dist\cmcoder, built and run with no Python in reach
# (or, with -UseBuilt, the one an earlier run built and checked). Returns the
# program's path.
function Build-Cmcoder {
    Need uv "install it from https://docs.astral.sh/uv/ (it brings its own Python)"
    $exe = Join-Path $root "dist\cmcoder\cmcoder.exe"
    if ($UseBuilt) {
        if (-not (Test-Path $exe)) { throw "-UseBuilt: there's no $exe (run without -UseBuilt)" }
        Write-Host ""
        Write-Host "== Standalone cmcoder: the one already in dist\cmcoder" -ForegroundColor Cyan
        return $exe
    }
    Step "Python environment for the build (uv)" $root { uv sync --python 3.12 }
    Step "Standalone cmcoder (PyInstaller)" $root { uv run --with pyinstaller python packaging/build.py }
    if (-not (Test-Path $exe)) { throw "The build made no $exe" }
    if (-not $SkipTests) {
        $env:CMCODER_TEST_BINARY = $exe
        $env:CMCODER_TEST_NO_PYTHON = "1"
        try {
            Step "cmcoder, run as developers run it (no Python in reach)" $root { uv run pytest -q tests/test_standalone.py }
        } finally {
            Remove-Item Env:CMCODER_TEST_BINARY, Env:CMCODER_TEST_NO_PYTHON -ErrorAction SilentlyContinue
        }
    }
    return $exe
}

function Build-Panel {
    Step "The shared chat panel" (Join-Path $root "clients\web-panel") { npm ci; if ($LASTEXITCODE -eq 0) { npm run build } }
}

# Fails unless $file (a zip: .vsix, .nbm, .zip) has an entry ending in each of
# $wanted. "a.zip!b" looks inside the zips in $file whose names match the
# wildcard "a.zip" for an entry ending in "b".
function Assert-ZipHas([string]$file, [string[]]$wanted) {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($file)
    try {
        foreach ($want in $wanted) {
            $ok = $false
            if ($want.Contains("!")) {
                $parts = $want.Split([char[]]"!", 2)
                foreach ($entry in $zip.Entries) {
                    if ($ok -or $entry.FullName.Replace("\", "/") -notlike $parts[0]) { continue }
                    $memory = New-Object IO.MemoryStream
                    $stream = $entry.Open()
                    try { $stream.CopyTo($memory) } finally { $stream.Dispose() }
                    $memory.Position = 0
                    $nested = New-Object IO.Compression.ZipArchive($memory)
                    try {
                        foreach ($inner in $nested.Entries) {
                            if ($inner.FullName.Replace("\", "/").EndsWith($parts[1])) { $ok = $true }
                        }
                    } finally {
                        $nested.Dispose()
                    }
                }
            } else {
                foreach ($entry in $zip.Entries) {
                    if ($entry.FullName.Replace("\", "/").EndsWith($want)) { $ok = $true }
                }
            }
            if (-not $ok) { throw "$file has no $want`: the program isn't in the file" }
        }
    } finally {
        $zip.Dispose()
    }
}

function Show-Done([string]$file, [string]$hint) {
    $item = Get-Item $file
    $hash = (Get-FileHash $item.FullName -Algorithm SHA256).Hash.ToLower()
    Write-Host ""
    Write-Host "Done: $($item.FullName)" -ForegroundColor Green
    Write-Host ("  {0:N1} MB, SHA256 {1}" -f ($item.Length / 1MB), $hash)
    Write-Host "  cmcoder is inside. $hint"
}
