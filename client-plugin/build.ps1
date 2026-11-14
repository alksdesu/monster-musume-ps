param(
    [string]$ClientDir = (Join-Path $PSScriptRoot '..\..\mnms_td_r18'),
    [switch]$Prebuilt
)

$ErrorActionPreference = 'Stop'
$clientPath = (Resolve-Path -LiteralPath $ClientDir).Path
$assemblyName = 'MonmusuTd.Local.dll'
$built = Join-Path $PSScriptRoot "bin\Release\net6.0\$assemblyName"
$shipped = Join-Path $PSScriptRoot "dist\$assemblyName"
$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue

if ($Prebuilt -or -not $dotnet) {
    if (-not (Test-Path -LiteralPath $shipped -PathType Leaf)) {
        throw "No .NET SDK found and no prebuilt plugin at $shipped. Install the .NET 6 SDK or restore client-plugin\dist."
    }
    $source = $shipped
    Write-Output "Using the prebuilt plugin $shipped"
}
else {
    & dotnet build (Join-Path $PSScriptRoot 'MonmusuTd.Local.csproj') -c Release "-p:ClientDir=$clientPath" --nologo
    if ($LASTEXITCODE -ne 0) {
        throw "Local plugin build failed with exit code $LASTEXITCODE."
    }
    $source = $built
    # The copy in dist is what a machine without the SDK runs, so every build refreshes it.
    [System.IO.Directory]::CreateDirectory((Split-Path $shipped)) | Out-Null
    Copy-Item -LiteralPath $built -Destination $shipped -Force
}

$pluginDirectory = Join-Path $clientPath 'BepInEx\plugins\MonmusuTd.Local'
[System.IO.Directory]::CreateDirectory($pluginDirectory) | Out-Null
Copy-Item -LiteralPath $source -Destination (Join-Path $pluginDirectory $assemblyName) -Force
Write-Output "Local plugin deployed to $pluginDirectory"
