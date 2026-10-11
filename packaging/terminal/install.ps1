# Installs cmcoder for this Windows user: no administrator rights, no Python.
# Run install.cmd (double-click), or: powershell -ExecutionPolicy Bypass -File install.ps1
#
# Copies the program to %USERPROFILE%\.cmcoder\bin, the one cmcoder for the
# terminal and every IDE (the IDEs use it too, when it's at least as new as
# their own copy), and adds that folder to *your* PATH (not the machine's).
# It also makes %USERPROFILE%\.cmcoder with your company's settings, if they
# came with this package and you have none yet. An install in the old place
# (%LOCALAPPDATA%\Programs\cmcoder) is moved. Run it again to update;
# uninstall.cmd removes the program (your settings stay).

param(
    # Where to put it (tests and IT can choose another folder).
    [string]$Destination = (Join-Path $(if ($env:CMCODER_CONFIG_DIR) { $env:CMCODER_CONFIG_DIR } else { Join-Path $env:USERPROFILE ".cmcoder" }) "bin"),
    # Where earlier versions installed it: moved from there.
    [string]$OldDestination = (Join-Path $env:LOCALAPPDATA "Programs\cmcoder")
)

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "cmcoder"
$exe = Join-Path $source "cmcoder.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    Write-Error "cmcoder.exe isn't next to this script ($source). Unzip the whole file first."
}

function Assert-NotRunning([string]$folder) {
    # A running cmcoder holds its files open: say so instead of half-copying.
    $running = Get-Process -Name cmcoder -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($folder, [StringComparison]::OrdinalIgnoreCase) }
    if ($running) {
        Write-Error "cmcoder is running from $folder (in a terminal or an IDE). Close it, then run this again."
    }
}

function Remove-FromUserPath([string]$folder) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { return }
    $kept = $userPath -split ";" | Where-Object { $_ -ne "" -and $_.TrimEnd("\") -ine $folder.TrimEnd("\") }
    [Environment]::SetEnvironmentVariable("Path", ($kept -join ";"), "User")
}

Assert-NotRunning $Destination
$moved = $null
if ($OldDestination -and ($OldDestination.TrimEnd("\") -ine $Destination.TrimEnd("\")) -and
    (Test-Path -LiteralPath (Join-Path $OldDestination "cmcoder.exe"))) {
    Assert-NotRunning $OldDestination
    Remove-Item -LiteralPath $OldDestination -Recurse -Force
    Remove-FromUserPath $OldDestination
    $moved = $OldDestination
}

# The program, and unfinished copies cmcoder's own updates may have left.
$parent = Split-Path -Parent $Destination
$leaf = Split-Path -Leaf $Destination
foreach ($extra in @(Get-ChildItem -LiteralPath $parent -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "$leaf.new-*" -or $_.Name -like "$leaf.old-*" })) {
    Remove-Item -LiteralPath $extra.FullName -Recurse -Force
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

# Its first run makes %USERPROFILE%\.cmcoder (and settings.json from your
# company's template, if this build has one and you have no settings yet).
$version = & (Join-Path $Destination "cmcoder.exe") --version
Write-Host "Installed cmcoder $version in $Destination."
if ($moved) { Write-Host "Moved it from $moved (removed there, and from your PATH)." }
Write-Host $pathNote

$cmcoderHome = if ($env:CMCODER_CONFIG_DIR) { $env:CMCODER_CONFIG_DIR } else { Join-Path $env:USERPROFILE ".cmcoder" }
$settings = Join-Path $cmcoderHome "settings.json"
if (Test-Path -LiteralPath $settings) {
    Write-Host "Your settings: $settings"
} else {
    Write-Host "No settings yet: run  cmcoder setup  (the gateway, your API key, the models)."
}

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
