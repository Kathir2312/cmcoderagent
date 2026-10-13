<#
Builds the Eclipse plugin for developers' PCs: dist\cmcoder-eclipse.zip, an
update-site archive for Windows, macOS and Linux in one file, with each
platform's cmcoder inside. Developers need Eclipse 2024-06+ only: no Python,
uv or Node.js.

Run it on a build PC (not the developers'), in the repository:

    powershell -ExecutionPolicy Bypass -File packaging\package-eclipse.ps1 `
        -DarwinArm64 <folder or zip> -LinuxX64 <folder or zip> [-Win32X64 <folder or zip>]

Each platform's cmcoder is built on that platform (packaging/build.py there:
its dist/cmcoder folder), or taken from a Release build run's artifacts
(cmcoder-win32-x64, cmcoder-darwin-arm64, cmcoder-linux-x64): give the
folder, or the zip as downloaded. Without -Win32X64, Windows' cmcoder is
built here (and run with no Python in reach).

    -OnePlatform  only Windows' cmcoder, for a try-out: the file is named
                  cmcoder-eclipse-TRYOUT-win32-x64-only.zip (on the other
                  platforms the plugin would look for cmcoder on PATH)
    -SkipTests    leave out the test run (try-outs only, never for release)
    -UseBuilt     use dist\cmcoder from an earlier run (built and checked then)

The build PC needs uv (https://docs.astral.sh/uv/), Node.js 22, JDK 21,
Maven 3.9+, access to download.eclipse.org and Git for Windows, with the
repository as a git clone. docs\build-package\eclipse.md has the details.
#>
param(
    [string]$Win32X64,
    [string]$DarwinArm64,
    [string]$LinuxX64,
    [switch]$OnePlatform,
    [switch]$SkipTests,
    [switch]$UseBuilt
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "package-common.ps1")

# Per platform: the folder or zip given, the fragment its cmcoder goes in, the program's name.
$platforms = [ordered]@{
    "win32-x64"    = @{ Given = $Win32X64;    Fragment = "win32.x86_64";   Program = "cmcoder.exe" }
    "darwin-arm64" = @{ Given = $DarwinArm64; Fragment = "macosx.aarch64"; Program = "cmcoder" }
    "linux-x64"    = @{ Given = $LinuxX64;    Fragment = "linux.x86_64";   Program = "cmcoder" }
}
if ($OnePlatform) {
    $use = @("win32-x64")
} else {
    $use = @($platforms.Keys)
    $missing = @($use | Where-Object { $_ -ne "win32-x64" -and -not $platforms[$_].Given })
    if ($missing.Count -gt 0) {
        $names = ($missing | ForEach-Object { if ($_ -eq "darwin-arm64") { "-DarwinArm64" } else { "-LinuxX64" } }) -join " "
        throw "The archive holds every platform's cmcoder: give $names (a folder or zip; see the help at the top of this script).`nFor a try-out with Windows' only: -OnePlatform."
    }
}

Need-Node
Need-Java 21
Need-Maven
Assert-GitCommit
if (-not $Win32X64) { $exe = Build-Cmcoder }
Build-Panel

$work = Join-Path ([IO.Path]::GetTempPath()) ("cmcoder-eclipse-" + [guid]::NewGuid())
New-Item -ItemType Directory $work | Out-Null

# A cmcoder folder (the one holding the program and _internal), from a folder or a zip.
function Find-CmcoderFolder([string]$platform, [string]$source) {
    $name = $platforms[$platform].Program
    if (Test-Path $source -PathType Leaf) {
        $dir = Join-Path $work $platform
        Expand-Archive -LiteralPath $source -DestinationPath $dir
        $source = $dir
    }
    if (-not (Test-Path $source -PathType Container)) { throw "${platform}: no folder or zip at $source" }
    $found = Get-ChildItem $source -Recurse -Depth 4 -File -Filter $name |
        Where-Object { Test-Path (Join-Path $_.DirectoryName "_internal") -PathType Container } |
        Select-Object -First 1
    if (-not $found) { throw "${platform}: $source holds no $name next to an _internal folder (is it that platform's dist/cmcoder?)" }
    return $found.DirectoryName
}

function Clear-Fragments {
    Get-ChildItem (Join-Path $root "eclipse\fragments") -Directory | ForEach-Object {
        $bin = Join-Path $_.FullName "bin\cmcoder"
        if (Test-Path $bin) { Remove-Item -Recurse -Force $bin }
    }
}

try {
    Write-Host ""
    Write-Host "== Each platform's cmcoder into its fragment" -ForegroundColor Cyan
    Clear-Fragments
    foreach ($p in $use) {
        $given = $platforms[$p].Given
        if ($given) { $src = Find-CmcoderFolder $p $given } else { $src = Join-Path $root "dist\cmcoder" }
        $dest = Join-Path $root ("eclipse\fragments\cmcoder.eclipse." + $platforms[$p].Fragment + "\bin\cmcoder")
        New-Item -ItemType Directory -Force (Split-Path -Parent $dest) | Out-Null
        Copy-Item -Recurse $src $dest
        Write-Host "  ${p}: $src"
    }
    # Windows can't mark the macOS and Linux programs executable: the plugin
    # does it the first time it starts there.

    Remove-Item (Join-Path $root "eclipse\site\target\cmcoder-eclipse-*.zip") -ErrorAction SilentlyContinue # not an earlier build's
    Step "The update-site archive (the gate test runs in the Release build: it needs a real Eclipse)" (Join-Path $root "eclipse") {
        mvn -B "-DskipTests" verify
    }
    $site = Get-ChildItem (Join-Path $root "eclipse\site\target") -Filter "cmcoder-eclipse-*.zip" | Select-Object -First 1
    if (-not $site) { throw "The build made no eclipse\site\target\cmcoder-eclipse-*.zip" }

    if ($OnePlatform) { $name = "cmcoder-eclipse-TRYOUT-win32-x64-only.zip" } else { $name = "cmcoder-eclipse.zip" }
    $out = Join-Path $root "dist\$name"
    New-Item -ItemType Directory -Force (Join-Path $root "dist") | Out-Null
    Copy-Item $site.FullName $out -Force
    $checks = @($use | ForEach-Object { "plugins/cmcoder.eclipse." + $platforms[$_].Fragment + "_*.jar!bin/cmcoder/" + $platforms[$_].Program })
    Assert-ZipHas $out $checks
    Show-Done $out "Developers: Eclipse > Help > Install New Software... > Add... > Archive..., then restart."
    if ($OnePlatform) { Write-Host "  A try-out with win32-x64 only: not for handing out." }
} finally {
    Clear-Fragments # git ignores them; the next build must not take them
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
