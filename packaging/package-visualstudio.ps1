<#
Builds the Visual Studio 2022 extension for developers' PCs:
dist\cmcoder-visualstudio-win32-x64.vsix, with cmcoder.exe inside.
Developers need Visual Studio 2022 (17.10+) only: no Python, uv or Node.js.

Run it on a build PC (not the developers'), in the repository:

    powershell -ExecutionPolicy Bypass -File packaging\package-visualstudio.ps1

The build PC needs Visual Studio 2022 with the "Visual Studio extension
development" workload, uv (https://docs.astral.sh/uv/), Node.js 22 and Git
for Windows, with the repository as a git clone (the build is named after its
commit). docs\build-package\visualstudio.md has the details.

It does what the Release workflow does for this file: builds cmcoder with
PyInstaller, runs it with no Python in reach, builds the chat page, puts
cmcoder into the extension, builds it with Visual Studio's MSBuild, and checks
cmcoder.exe is in the file.

    -SkipTests   leave out the test run (try-outs only, never for release)
    -UseBuilt    use dist\cmcoder from an earlier run (built and checked then)
#>
param([switch]$SkipTests, [switch]$UseBuilt)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "package-common.ps1")

# Visual Studio's MSBuild first: a missing one stops here, not after building cmcoder.
$msbuild = & (Join-Path $root "visualstudio\find-msbuild.ps1")
Write-Host "MSBuild: $msbuild"
Need-Node
Assert-GitCommit
$exe = Build-Cmcoder
Build-Panel

$bin = Join-Path $root "visualstudio\src\Cmcoder.VisualStudio\bin\cmcoder"
Step "cmcoder into the extension" $root {
    if (Test-Path $bin) { Remove-Item -Recurse -Force $bin } # no copy from an earlier build
    Copy-Item -Recurse (Join-Path $root "dist\cmcoder") $bin
}

Write-Host ""
Write-Host "== The .vsix (Visual Studio's MSBuild)" -ForegroundColor Cyan
$lines = @()
& (Join-Path $root "visualstudio\build-vsix.ps1") | Tee-Object -Variable lines | Out-Host
$built = @($lines)[-1]
if (-not $built -or -not (Test-Path $built)) { throw "build-vsix.ps1 made no .vsix" }

$out = Join-Path $root "dist\cmcoder-visualstudio-win32-x64.vsix"
Copy-Item $built $out -Force
Assert-ZipHas $out @("bin/cmcoder/cmcoder.exe", "panel/chat.js")
Show-Done $out "Developers: close Visual Studio, double-click the file, Install."
