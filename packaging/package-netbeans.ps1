<#
Builds the NetBeans plugin for developers' PCs:
dist\cmcoder-netbeans-win32-x64.nbm, with cmcoder.exe and the chat's browser
(OpenJFX) inside. Developers need NetBeans 28+ only: no Python, uv, Node.js
or JavaFX.

Run it on a build PC (not the developers'), in the repository:

    powershell -ExecutionPolicy Bypass -File packaging\package-netbeans.ps1

The build PC needs uv (https://docs.astral.sh/uv/), Node.js 22, a JDK 17 or
newer (21 recommended), Maven 3.9+ and Git for Windows, with the repository
as a git clone. docs\build-package\netbeans.md has the details.

It does what the Release workflow does for this file: builds cmcoder with
PyInstaller, runs it with no Python in reach, builds the chat page, puts
cmcoder in netbeans\bin, builds the .nbm, and checks cmcoder.exe is in it.

    -SkipTests   leave out the test runs (try-outs only, never for release)
    -UseBuilt    use dist\cmcoder from an earlier run (built and checked then)
#>
param([switch]$SkipTests, [switch]$UseBuilt)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "package-common.ps1")

Need-Node
Need-Java 17
Need-Maven
Assert-GitCommit
$exe = Build-Cmcoder
Build-Panel

$bin = Join-Path $root "netbeans\bin"
Step "cmcoder into the plugin (netbeans\bin\cmcoder)" $root {
    if (Test-Path $bin) { Remove-Item -Recurse -Force $bin } # no copy from an earlier build
    New-Item -ItemType Directory $bin | Out-Null
    Copy-Item -Recurse (Join-Path $root "dist\cmcoder") (Join-Path $bin "cmcoder")
}

$mvnArgs = @("-B", "package", "-Dplatform=win")
if ($SkipTests) { $mvnArgs += "-DskipTests" }
Step "The .nbm for win32-x64" (Join-Path $root "netbeans") { mvn @mvnArgs }

$out = Join-Path $root "dist\cmcoder-netbeans-win32-x64.nbm"
Copy-Item (Join-Path $root "netbeans\target\cmcoder-netbeans-win32-x64.nbm") $out -Force
Assert-ZipHas $out @("/bin/cmcoder/cmcoder.exe", "/panel/chat.js")
Show-Done $out "Developers: NetBeans > Tools > Plugins > Downloaded > Add Plugins..., then restart."
