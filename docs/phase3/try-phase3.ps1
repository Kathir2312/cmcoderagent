<#
.SYNOPSIS
  Makes a small project for trying Phase 3 by hand (see docs/phase3/TESTING.md).

.DESCRIPTION
  In the new project folder: a slash command (/explain), a skill (haiku), a
  hook (appends to hook.log after each edit) and an MCP server (tickets, in
  .mcp.json). In your own cmcoder folder (~/.cmcoder): a subagent (reviewer),
  unless you already have one with that name. Nothing else is changed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File docs\phase3\try-phase3.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File docs\phase3\try-phase3.ps1 -Path C:\temp\p3test
#>
param([string]$Path = (Join-Path $env:TEMP "cmcoder-phase3"))

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Marker = ".cmcoder-phase3-test"
$NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-Text([string]$File, [string]$Text) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $File) | Out-Null
    [System.IO.File]::WriteAllText($File, $Text.Replace("`r`n", "`n"), $NoBom)
}

# A Python that has the mcp package (cmcoder's own), to run the MCP server.
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    try {
        $ToolDir = ((& uv tool dir) | Out-String).Trim()
        $Python = Join-Path $ToolDir "cmcoder\Scripts\python.exe"
    } catch { }
}
if (-not (Test-Path $Python)) {
    throw "No Python with cmcoder installed was found. Run 'uv sync' in $Repo, then run this again."
}

# Start from a clean folder, but never delete one this script didn't make.
if (Test-Path $Path) {
    if (-not (Test-Path (Join-Path $Path $Marker))) {
        throw "$Path already exists and wasn't made by this script. Choose another folder with -Path."
    }
    Remove-Item -Recurse -Force $Path
}
New-Item -ItemType Directory -Force -Path $Path | Out-Null
$Path = (Resolve-Path $Path).Path

Write-Text (Join-Path $Path $Marker) "Made by docs/phase3/try-phase3.ps1. Safe to delete.`n"

Write-Text (Join-Path $Path "app.py") @'
def add(a, b):
    """Add two numbers."""
    return a - b


def average(values):
    """The mean of a list of numbers."""
    return sum(values) / len(values)
'@

# A slash command: /explain <file>
Write-Text (Join-Path $Path ".cmcoder\commands\explain.md") @'
---
description: Explain a file to a new team member
argument-hint: <file>
---
Explain $1 to a new team member in at most 5 bullet points. Don't change any file.
'@

# A skill: loaded only when the request matches its description
Write-Text (Join-Path $Path ".cmcoder\skills\haiku\SKILL.md") @'
---
name: haiku
description: Use when asked for a poem about code.
---
Write one haiku (5-7-5 syllables) about the code in question. Nothing else.
'@

# A hook: after every Edit or Write, append a line to hook.log
Write-Text (Join-Path $Path ".cmcoder\settings.json") @'
{
  "hooks": {
    "PostToolUse": [
      {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "echo edited >> hook.log"}]}
    ]
  }
}
'@

# An MCP server: a tiny ticket tracker (the same one the evals use)
Copy-Item (Join-Path $Repo "evals\tasks\mcp-ticket\repo\tickets_server.py") $Path
$Servers = @{ mcpServers = @{ tickets = @{ command = $Python; args = @("tickets_server.py") } } }
Write-Text (Join-Path $Path ".mcp.json") ($Servers | ConvertTo-Json -Depth 5)

# A subagent, in your own folder (a project's agents need trust; yours don't)
$ConfigDir = $env:CMCODER_CONFIG_DIR
if (-not $ConfigDir) { $ConfigDir = Join-Path $HOME ".cmcoder" }
$Agent = Join-Path $ConfigDir "agents\reviewer.md"
if (Test-Path $Agent) {
    Write-Host "Kept your existing $Agent"
} else {
    Write-Text $Agent @'
---
name: reviewer
description: Reviews code for bugs. Use when asked to review code.
tools: Read, Grep, Glob
---
Review the files you are given. Report real bugs only, each with file:line and a fix.
'@
}

# A git repository, so cmcoder sees a project
Push-Location $Path
try {
    git init -q
    git config core.autocrlf false
    git add -A
    git -c user.name=cmcoder-test -c user.email=test@example.invalid commit -qm "Phase 3 test project"
    if ($LASTEXITCODE -ne 0) { throw "git commit failed" }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Test project ready: $Path"
Write-Host "Next (docs/phase3/TESTING.md):"
Write-Host "  cd `"$Path`""
Write-Host "  cmcoder trust              # answer y"
Write-Host "  cmcoder doctor --no-probe"
Write-Host "  cmcoder"
