# Removes what install.ps1 added: the program folder (%USERPROFILE%\.cmcoder\bin)
# and its PATH entry. Your settings, sessions and keys (the rest of
# %USERPROFILE%\.cmcoder and the Windows Credential Manager) are kept. The
# IDE plugins keep working with their own copy of cmcoder.

param(
    [string]$Destination = (Join-Path $(if ($env:CMCODER_CONFIG_DIR) { $env:CMCODER_CONFIG_DIR } else { Join-Path $env:USERPROFILE ".cmcoder" }) "bin")
)

$ErrorActionPreference = "Stop"
$running = Get-Process -Name cmcoder -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Destination, [StringComparison]::OrdinalIgnoreCase) }
if ($running) {
    Write-Error "cmcoder is running from $Destination (in a terminal or an IDE). Close it, then run this again."
}
# The program, and what cmcoder's own updates leave next to it.
$parent = Split-Path -Parent $Destination
$leaf = Split-Path -Leaf $Destination
foreach ($extra in @(Get-ChildItem -LiteralPath $parent -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "$leaf.new-*" -or $_.Name -like "$leaf.old-*" -or
                       $_.Name -eq "$leaf.running" -or $_.Name -eq "$leaf.updating" })) {
    Remove-Item -LiteralPath $extra.FullName -Recurse -Force
}
if (Test-Path -LiteralPath $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
}
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath) {
    $kept = $userPath -split ";" | Where-Object { $_ -ne "" -and $_.TrimEnd("\") -ine $Destination.TrimEnd("\") }
    [Environment]::SetEnvironmentVariable("Path", ($kept -join ";"), "User")
}
Write-Host "Removed cmcoder from $Destination and from your PATH."
