# Installs your company's managed settings on this PC: the rules developers
# can't change (docs/guides/admin.md, section 5). Run it as administrator:
#   right-click install-managed.cmd, Run as administrator; or
#   powershell -ExecutionPolicy Bypass -File install-managed.ps1 [-Source file]
#
# Copies managed-settings.json (next to this script) to
#   C:\Program Files\cmcoder\managed-settings.json
# which every user's cmcoder reads (terminal and IDEs) and only administrators
# can change. For many PCs, deploy that same file there with Intune, Group
# Policy or Configuration Manager instead.

param(
    [string]$Source = (Join-Path $PSScriptRoot "managed-settings.json"),
    # Tests only: install there, without the administrator check.
    [string]$Target = ""
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Source)) {
    Write-Error "No managed settings to install: $Source is missing. Fill in managed-settings.example.json and save it as managed-settings.json next to this script."
}

# A broken file stops cmcoder for everyone on this PC: check it first.
$text = Get-Content -LiteralPath $Source -Raw -Encoding UTF8
try {
    $parsed = $text | ConvertFrom-Json
} catch {
    Write-Error "$Source isn't valid JSON: $($_.Exception.Message)"
}
if ($parsed -isnot [System.Management.Automation.PSCustomObject]) {
    Write-Error "$Source must hold one JSON object: { ... }."
}
if ($text -match "YOUR-") {
    Write-Error "$Source still has the example's placeholders (YOUR-...). Put in your company's values."
}

if (-not $Target) {
    $me = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "Run this as administrator (right-click install-managed.cmd, Run as administrator)."
    }
    # The 64-bit Program Files, also from a 32-bit PowerShell: where cmcoder looks.
    $programFiles = if ($env:ProgramW6432) { $env:ProgramW6432 } else { [Environment]::GetFolderPath("ProgramFiles") }
    $Target = Join-Path $programFiles "cmcoder\managed-settings.json"
}

$replaced = Test-Path -LiteralPath $Target
New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
# UTF-8 without a byte order mark, whatever editor saved the source.
[IO.File]::WriteAllText($Target, $text, (New-Object Text.UTF8Encoding $false))
if ($replaced) {
    Write-Host "Replaced the managed settings: $Target"
} else {
    Write-Host "Installed the managed settings: $Target"
}
Write-Host "Every user's cmcoder uses them from its next start. Check on a developer's PC:  cmcoder doctor"
