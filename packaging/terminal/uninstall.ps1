# Removes what install.ps1 added: the program folder and the PATH entry.
# Your settings, sessions and keys (in %USERPROFILE%\.cmcoder and the
# Windows Credential Manager) are kept.

param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA "Programs\cmcoder")
)

$ErrorActionPreference = "Stop"
$running = Get-Process -Name cmcoder -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Destination, [StringComparison]::OrdinalIgnoreCase) }
if ($running) {
    Write-Error "cmcoder is running from $Destination (in a terminal or an IDE). Close it, then run this again."
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
