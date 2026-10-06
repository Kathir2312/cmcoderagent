<#
  The release gate for Visual Studio (Phase 6 item 7): installs the gate build
  of the extension (bundled cmcoder inside, its self-test compiled in) into
  Visual Studio's experimental instance, starts the mock model server, opens a
  small project folder in Visual Studio, and waits for the extension's
  self-test (Gate.cs) to write its result. Fails unless every step passed.

    run-gate.ps1 -Vsix path\to\gate.vsix -Python path\to\python.exe

  -Python is a Python with cmcoder, for the mock server only (test equipment);
  Visual Studio and cmcoder run without it (run this under
  packaging\no_python.py so nothing finds Python on PATH).
#>
param(
    [Parameter(Mandatory = $true)][string]$Vsix,
    [Parameter(Mandatory = $true)][string]$Python,
    [int]$TimeoutSeconds = 900
)
$ErrorActionPreference = 'Stop'

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.CoreEditor -property installationPath
if (-not $vs) { throw 'Visual Studio not found' }
$devenv = Join-Path $vs 'Common7\IDE\devenv.exe'
$installer = Join-Path $vs 'Common7\IDE\VSIXInstaller.exe'
Write-Host "Visual Studio: $vs ($(& $vswhere -latest -products * -property catalog_productDisplayVersion))"

$work = Join-Path $env:RUNNER_TEMP ('cmcoder-vs-gate-' + [guid]::NewGuid().ToString('N'))
$project = Join-Path $work 'project'
New-Item -ItemType Directory -Force -Path (Join-Path $project '.git') | Out-Null
# No .py files: Visual Studio's own Python tooling would start Python for them.
[IO.File]::WriteAllText((Join-Path $project 'app.cs'), "class App { }`n")
$result = Join-Path $work 'result.json'
$config = Join-Path $work 'config'
New-Item -ItemType Directory -Force -Path $config | Out-Null

# The model's replies, in order: a getDiagnostics call, a reply, a Write
# accepted, a reply, a Write rejected (the turn stops there).
$script = Join-Path $work 'script.json'
[IO.File]::WriteAllText($script, '[{"tool_calls":[{"name":"getDiagnostics","arguments":{}}]},{"content":"Checked the problems."},{"tool_calls":[{"name":"Write","arguments":{"file_path":"new.txt","content":"x = 2\n"}}]},{"content":"Wrote new.py."},{"tool_calls":[{"name":"Write","arguments":{"file_path":"other.txt","content":"y = 3\n"}}]}]')
$apiKey = 'sk-gate-' + [guid]::NewGuid().ToString('N')

$mockInfo = New-Object System.Diagnostics.ProcessStartInfo
$mockInfo.FileName = $Python
$mockInfo.Arguments = "-m cmcoder.testing.mock_server --script `"$script`" --port 0 --api-key $apiKey"
$mockInfo.UseShellExecute = $false
$mockInfo.RedirectStandardOutput = $true
$mock = [System.Diagnostics.Process]::Start($mockInfo)
$line = $mock.StandardOutput.ReadLine()
if (-not $line.StartsWith('mock server on ')) { throw "mock server didn't start: $line" }
$url = $line.Substring('mock server on '.Length).Trim()
Write-Host "Mock model server: $url"

try {
    # The experimental instance: created, then the extension installed into it.
    & $devenv /rootsuffix Exp /updateconfiguration | Out-Host
    $p = Start-Process -FilePath $installer -ArgumentList @('/quiet', '/rootSuffix:Exp', "`"$Vsix`"") -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "VSIXInstaller failed with $($p.ExitCode) (see %TEMP%\dd_VSIXInstaller_*.log)" }
    Write-Host 'Extension installed in the experimental instance.'

    $env:CMCODER_VS_GATE = $result
    $env:CMCODER_BASE_URL = $url
    $env:CMCODER_API_KEY = $apiKey
    $env:CMCODER_MODEL = 'qwen3-27b'
    $env:CMCODER_CONFIG_DIR = $config
    $env:PYTHON_KEYRING_BACKEND = 'keyring.backends.fail.Keyring'
    foreach ($v in 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY') { Remove-Item "env:$v" -ErrorAction SilentlyContinue }

    $vsProcess = Start-Process -FilePath $devenv -ArgumentList @('/rootsuffix', 'Exp', "`"$project`"") -PassThru
    $end = (Get-Date).AddSeconds($TimeoutSeconds)
    while (-not (Test-Path $result) -and (Get-Date) -lt $end -and -not $vsProcess.HasExited) { Start-Sleep -Seconds 2 }
    if (-not (Test-Path $result)) {
        if (-not $vsProcess.HasExited) {
            # What's on screen (a dialog Visual Studio waits on?), for the log.
            Add-Type -AssemblyName System.Windows.Forms, System.Drawing
            $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
            $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
            [System.Drawing.Graphics]::FromImage($bmp).CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
            $shot = Join-Path $env:RUNNER_TEMP 'vs-gate-screen.png'
            $bmp.Save($shot)
            Write-Host "Screenshot: $shot"
            Stop-Process -Id $vsProcess.Id -Force
        }
        throw "No gate result after $TimeoutSeconds s (Visual Studio exited: $($vsProcess.HasExited))"
    }
    $vsProcess.WaitForExit(60000) | Out-Null
    if (-not $vsProcess.HasExited) { Stop-Process -Id $vsProcess.Id -Force }

    $report = Get-Content $result -Raw | ConvertFrom-Json
    Write-Host 'Steps:'
    $report.steps | ForEach-Object { Write-Host "  $_" }
    Write-Host "State: $($report.state)"
    if (-not $report.ok) {
        Write-Host 'Log:'
        $report.log | ForEach-Object { Write-Host "  $_" }
        throw "The Visual Studio gate failed: $($report.failure)"
    }
    Write-Host 'The Visual Studio gate passed.'
}
finally {
    if (-not $mock.HasExited) { $mock.Kill() }
}
