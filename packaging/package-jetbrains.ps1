<#
Builds the JetBrains plugin (IntelliJ IDEA, PyCharm, Rider, WebStorm, GoLand
and the other JetBrains IDEs, 2024.2+) for developers' PCs:
dist\cmcoder-jetbrains-win32-x64.zip, with cmcoder.exe inside. Developers
need the IDE only: no Python, uv or Node.js.

Run it on a build PC (not the developers'), in the repository:

    powershell -ExecutionPolicy Bypass -File packaging\package-jetbrains.ps1

The build PC needs uv (https://docs.astral.sh/uv/), Node.js 22, JDK 21,
Gradle 9.8.1, access to JetBrains' servers and Git for Windows, with the
repository as a git clone. docs\build-package\intellij.md has the details.

It does what the Release workflow does for this file: builds cmcoder with
PyInstaller, runs it with no Python in reach, builds the chat page, builds the
plugin with cmcoder inside, and checks cmcoder.exe is in the file.

    -SkipTests   leave out the test run (try-outs only, never for release)
    -UseBuilt    use dist\cmcoder from an earlier run (built and checked then)
#>
param([switch]$SkipTests, [switch]$UseBuilt)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "package-common.ps1")

Need-Node
Need-Java 21
Need-Gradle
Assert-GitCommit
$exe = Build-Cmcoder
Build-Panel

$distributions = Join-Path $root "jetbrains\build\distributions"
Get-ChildItem $distributions -Filter "cmcoder-jetbrains-*.zip" -ErrorAction SilentlyContinue | Remove-Item -Force
$cmcoderDir = Join-Path $root "dist\cmcoder"
Step "The plugin, with cmcoder inside (the first build downloads the IntelliJ Platform)" (Join-Path $root "jetbrains") {
    gradle --no-daemon "-PcmcoderDir=$cmcoderDir" buildPlugin
}

$built = Get-ChildItem $distributions -Filter "cmcoder-jetbrains-*.zip" | Where-Object { $_.Name -notlike "*-gate.zip" } | Select-Object -First 1
if (-not $built) { throw "The build made no jetbrains\build\distributions\cmcoder-jetbrains-*.zip" }
$out = Join-Path $root "dist\cmcoder-jetbrains-win32-x64.zip"
Copy-Item $built.FullName $out -Force
Assert-ZipHas $out @("/bin/cmcoder/cmcoder.exe", "/panel/chat.js")
Show-Done $out "Developers: Settings > Plugins > gear > Install Plugin from Disk..., the zip as it is."
