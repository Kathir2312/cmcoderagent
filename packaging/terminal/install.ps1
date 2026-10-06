# Installs cmcoder for this Windows user: no administrator rights, no Python.
# Run install.cmd (double-click), or: powershell -ExecutionPolicy Bypass -File install.ps1
#
# Copies the program to %LOCALAPPDATA%\Programs\cmcoder and adds that folder
# to *your* PATH (not the machine's). Run it again to update; uninstall.cmd
# removes both.

param(
    # Where to put it (tests use another folder).
    [string]$Destination = (Join-Path $env:LOCALAPPDATA "Programs\cmcoder")
)

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "cmcoder"
$exe = Join-Path $source "cmcoder.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    Write-Error "cmcoder.exe isn't next to this script ($source). Unzip the whole file first."
}

# A running cmcoder holds its files open: say so instead of half-copying.
$running = Get-Process -Name cmcoder -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Destination, [StringComparison]::OrdinalIgnoreCase) }
if ($running) {
    Write-Error "cmcoder is running from $Destination (in a terminal or an IDE). Close it, then run this again."
}

if (Test-Path -LiteralPath $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
Copy-Item -Path (Join-Path $source "*") -Destination $Destination -Recurse -Force

# Your PATH only, added at the end; the change reaches newly opened terminals.
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$entries = @()
if ($userPath) { $entries = $userPath -split ";" | Where-Object { $_ -ne "" } }
$already = $entries | Where-Object { $_.TrimEnd("\") -ieq $Destination.TrimEnd("\") }
if (-not $already) {
    [Environment]::SetEnvironmentVariable("Path", (($entries + $Destination) -join ";"), "User")
    $pathNote = "Added $Destination to your PATH."
} else {
    $pathNote = "$Destination was already on your PATH."
}

$version = & (Join-Path $Destination "cmcoder.exe") --version
Write-Host "Installed cmcoder $version in $Destination."
Write-Host $pathNote

# Another cmcoder earlier on the PATH (an older install) would be the one that runs.
$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
foreach ($dir in ("$machinePath;" + [Environment]::GetEnvironmentVariable("Path", "User")) -split ";") {
    if (-not $dir) { continue }
    if ($dir.TrimEnd("\") -ieq $Destination.TrimEnd("\")) { break }
    if (Test-Path -LiteralPath (Join-Path $dir "cmcoder.exe")) {
        Write-Warning "Another cmcoder comes first on your PATH: $dir. Remove it, or it runs instead of this one."
        break
    }
}

Write-Host ""
Write-Host "Open a NEW terminal, then:  cmcoder doctor"
