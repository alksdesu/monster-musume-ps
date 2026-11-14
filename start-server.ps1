[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Saves
)

$ErrorActionPreference = 'Stop'
$previousPythonPath = $env:PYTHONPATH
$restartCode = 75
try {
    $env:PYTHONPATH = Join-Path $PSScriptRoot 'server'
    $serverArgs = @('-m', 'monmusu_ps.app', '--root', $PSScriptRoot)
    if ($Saves) { $serverArgs += @('--saves', $Saves) }
    do {
        & $Python @serverArgs
    } while ($LASTEXITCODE -eq $restartCode)
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE" }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
