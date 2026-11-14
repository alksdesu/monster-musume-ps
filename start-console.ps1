[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Saves,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $PSScriptRoot 'server'
    & $Python -c 'import webview'
    if ($LASTEXITCODE -ne 0) { throw "The console window needs pywebview: $Python -m pip install pywebview" }
    $consoleArgs = @('-m', 'monmusu_console', '--root', $PSScriptRoot)
    if ($Saves) { $consoleArgs += @('--saves', $Saves) }
    if ($Foreground) {
        & $Python @consoleArgs
        if ($LASTEXITCODE -ne 0) { throw "Console exited with code $LASTEXITCODE" }
    }
    else {
        # pythonw opens the window without tying up this terminal.
        $windowed = Join-Path (Split-Path (Get-Command $Python).Source) 'pythonw.exe'
        if (-not (Test-Path $windowed)) { $windowed = $Python }
        $quoted = $consoleArgs | ForEach-Object { '"{0}"' -f $_ }
        Start-Process -FilePath $windowed -ArgumentList $quoted -WorkingDirectory $PSScriptRoot
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
