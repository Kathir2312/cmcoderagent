<#
  Prints the path of Visual Studio's MSBuild.exe for building the extension:
  Visual Studio 2022 (17.0) or newer, any edition, or the Build Tools for
  Visual Studio, a release or else a preview. Without one, stops with what
  is installed and what to add. Used by build-vsix.ps1 and
  packaging\package-visualstudio.ps1 (which checks first, before building
  cmcoder).
    find-msbuild.ps1
  CMCODER_VSWHERE: another vswhere.exe (tests only).
#>
$ErrorActionPreference = 'Stop'
$vswhere = $env:CMCODER_VSWHERE
if (-not $vswhere) { $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe' }
$need = "Visual Studio 2022 or newer (any edition) with the 'Visual Studio extension development' workload, " +
    "or the Build Tools for Visual Studio with 'MSBuild' and the '.NET Framework 4.8 targeting pack'"
if (-not (Test-Path $vswhere)) {
    throw "No Visual Studio on this PC (no $vswhere). Install $need."
}

# A release first; a preview (Preview, Insiders) only if there's no release.
foreach ($preview in @($false, $true)) {
    $query = @('-latest', '-products', '*', '-requires', 'Microsoft.Component.MSBuild', '-version', '[17.0,')
    if ($preview) { $query += '-prerelease' }
    $msbuild = & $vswhere @query -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
    if ($msbuild) {
        $framework = Join-Path ${env:ProgramFiles(x86)} 'Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8'
        if (-not $env:CMCODER_VSWHERE -and -not (Test-Path $framework)) {
            throw "Found MSBuild ($msbuild), but not the .NET Framework 4.8 targeting pack the extension builds against. " +
                "Visual Studio Installer > Modify > Individual components > '.NET Framework 4.8 targeting pack'."
        }
        return $msbuild
    }
}

# None: say what is there, and why each one doesn't do.
$text = (& $vswhere -all -prerelease -products * -format json) -join "`n"
$instances = @()
# (ForEach-Object unrolls the array, which Windows PowerShell 5.1's ConvertFrom-Json passes as one object.)
if ($text.Trim()) { $instances = @($text | ConvertFrom-Json | ForEach-Object { $_ }) }
$withMsbuild = @(& $vswhere -all -prerelease -products * -requires Microsoft.Component.MSBuild -property instanceId)
if ($instances.Count -eq 0) {
    throw "Visual Studio's installer is on this PC, but no Visual Studio is installed. Install $need."
}
$lines = foreach ($i in $instances) {
    $why = if ([version]$i.installationVersion -lt [version]'17.0') { 'too old: 2022 (17.0) or newer is needed' }
        elseif ($withMsbuild -notcontains $i.instanceId) { 'no MSBuild' }
        else { 'should do: please report this' }
    "  - $($i.displayName) ($($i.installationVersion)): $why"
}
throw ("Visual Studio's installer is on this PC, but none of what it installed can build the extension:`n" +
    ($lines -join "`n") + "`nInstall $need (Visual Studio Installer > Modify, or a new install).")
