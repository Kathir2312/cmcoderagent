<#
  Builds the Visual Studio extension (.vsix) with Visual Studio's MSBuild.
    build-vsix.ps1 [-Gate]
  -Gate: the test build with the self-test (Gate.cs) and the panel's test
  driver; never given to developers. Prints the .vsix path.
#>
param([switch]$Gate)
$ErrorActionPreference = 'Stop'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
if (-not $msbuild) { throw 'MSBuild (Visual Studio) not found' }
$project = Join-Path $PSScriptRoot 'src\Cmcoder.VisualStudio\Cmcoder.VisualStudio.csproj'
$name = if ($Gate) { 'Gate' } else { 'Release' }
$out = Join-Path $PSScriptRoot "src\Cmcoder.VisualStudio\bin\$name\"
$msbuildArgs = @($project, '/restore', '/p:Configuration=Release', "/p:OutputPath=$out", "/p:IntermediateOutputPath=obj\$name\", '/v:minimal', '/nologo')
if ($Gate) { $msbuildArgs += '/p:CmcoderGate=true' }
& $msbuild @msbuildArgs
if ($LASTEXITCODE -ne 0) { throw "MSBuild failed ($LASTEXITCODE)" }
$vsix = Join-Path $out 'Cmcoder.VisualStudio.vsix'
if (-not (Test-Path $vsix)) { throw "no .vsix at $vsix" }
# What's inside: the panel, the package, and (gate only) the test driver.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$entries = [IO.Compression.ZipFile]::OpenRead($vsix).Entries | ForEach-Object { $_.FullName.Replace('\', '/') }
foreach ($f in 'panel/chat.js', 'panel/navigator.js', 'panel/theme.json', 'panel/brand.json', 'Cmcoder.VisualStudio.dll', 'Cmcoder.Core.dll', 'Cmcoder.VisualStudio.pkgdef', 'extension.vsixmanifest') {
    if ($entries -notcontains $f) { throw "$f is missing from the .vsix" }
}
if (-not $Gate -and ($entries -contains 'panel/test-driver.js')) { throw 'the test driver must not ship' }
$exe = $entries -contains 'bin/cmcoder/cmcoder.exe'
Write-Host "Built $vsix ($($entries.Count) files; cmcoder inside: $exe)"
$vsix
