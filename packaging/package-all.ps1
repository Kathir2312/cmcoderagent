<#
Builds every Windows package in one go: cmcoder is built and run with no
Python in reach once, then each package is made around it:

    terminal      dist\cmcoder-terminal-win32-x64.zip       (package-terminal.ps1)
    vscode        vscode\cmcoder-win32-x64.vsix             (package-vscode.ps1)
    visualstudio  dist\cmcoder-visualstudio-win32-x64.vsix  (package-visualstudio.ps1)
    netbeans      dist\cmcoder-netbeans-win32-x64.nbm       (package-netbeans.ps1)
    jetbrains     dist\cmcoder-jetbrains-win32-x64.zip      (package-jetbrains.ps1)

The Eclipse plugin is one file for all platforms: make it with
package-eclipse.ps1 and the three platforms' cmcoder.

    powershell -ExecutionPolicy Bypass -File packaging\package-all.ps1 [-SkipTests] [-Only terminal,vscode,...]

The build PC needs what each package's page in docs\building\ lists; -Only
leaves out the packages whose tools it doesn't have.

    -SkipTests   leave out the test runs (try-outs only, never for release)
    -Only LIST   only these packages (comma-separated)
#>
param([switch]$SkipTests, [string[]]$Only)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$UseBuilt = $false
. (Join-Path $PSScriptRoot "package-common.ps1")

$all = @("terminal", "vscode", "visualstudio", "netbeans", "jetbrains")
if ($Only) { $packages = @($Only | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ }) } else { $packages = $all }
foreach ($p in $packages) {
    if ($all -notcontains $p) { throw "unknown package: $p ($($all -join ', '))" }
}

Assert-GitCommit
$exe = Build-Cmcoder # once, for every package

foreach ($p in $packages) {
    Write-Host ""
    Write-Host "######## $p ########" -ForegroundColor Magenta
    & (Join-Path $PSScriptRoot "package-$p.ps1") -UseBuilt -SkipTests:$SkipTests
}

Write-Host ""
Write-Host "All done ($($packages -join ', ')). The files:" -ForegroundColor Green
$files = @(
    "dist\cmcoder-terminal-win32-x64.zip", "vscode\cmcoder-win32-x64.vsix",
    "dist\cmcoder-visualstudio-win32-x64.vsix", "dist\cmcoder-netbeans-win32-x64.nbm",
    "dist\cmcoder-jetbrains-win32-x64.zip"
)
foreach ($f in $files) {
    $path = Join-Path $root $f
    if (Test-Path $path) { Write-Host ("  {0}  {1}" -f (Get-FileHash $path -Algorithm SHA256).Hash.ToLower(), $f) }
}
