param(
    [ValidatePattern('\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\z')]
    [string]$Profile = 'local',
    [string]$BaseUrl = 'http://127.0.0.1:18621',
    [string]$ClientDir,
    [switch]$ShippedDates,
    [Nullable[double]]$Mosaic
)

$ErrorActionPreference = 'Stop'
$settings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'server\data\settings.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$client = $settings.client
function Setting($name, $fallback) {
    if ($null -ne $client -and $null -ne $client.$name -and "$($client.$name)" -ne '') { return $client.$name }
    return $fallback
}

if (-not $ClientDir) { $ClientDir = Setting 'dir' '..\mnms_td_r18' }
if (-not [System.IO.Path]::IsPathRooted($ClientDir)) { $ClientDir = Join-Path $PSScriptRoot $ClientDir }
if (-not (Test-Path -LiteralPath $ClientDir -PathType Container)) {
    throw "Game folder not found: $ClientDir. Set client.dir in server\data\settings.json to the folder that holds monster_musume_tdx.exe."
}
$clientPath = (Resolve-Path -LiteralPath $ClientDir).Path
$gamePath = Join-Path $clientPath 'monster_musume_tdx.exe'
if (-not (Test-Path -LiteralPath $gamePath -PathType Leaf)) {
    throw "Game executable not found: $gamePath"
}
if (-not (Test-Path -LiteralPath (Join-Path $clientPath 'BepInEx\core') -PathType Container)) {
    throw "BepInEx is not installed in $clientPath. Unpack BepInEx (IL2CPP, x64) into the game folder and run the game once before using the local server."
}

& (Join-Path $PSScriptRoot 'client-plugin\build.ps1') -ClientDir $clientPath

$configDirectory = Join-Path $clientPath 'BepInEx\config'
[System.IO.Directory]::CreateDirectory($configDirectory) | Out-Null
$configPath = Join-Path $configDirectory 'local.monmusutd.pc.cfg'
$schedule = if ($ShippedDates) { 'false' } else { 'true' }
if ($null -eq $Mosaic) { $Mosaic = [double](Setting 'mosaic' 1) }
$mosaicText = ([double]$Mosaic).ToString([System.Globalization.CultureInfo]::InvariantCulture)
$font = Setting 'font' ''
$fontFamily = Setting 'font_family' 'Microsoft YaHei,SimHei'
$configText = @(
    '[Local]',
    'Enabled = true',
    "BaseUrl = $BaseUrl",
    "Schedule = $schedule",
    "Mosaic = $mosaicText",
    "Font = $font",
    "FontFamily = $fontFamily",
    ''
) -join "`n"
[System.IO.File]::WriteAllText($configPath, $configText, [System.Text.UTF8Encoding]::new($false))

$logDirectory = Join-Path $PSScriptRoot 'logs'
[System.IO.Directory]::CreateDirectory($logDirectory) | Out-Null
$logPath = Join-Path $logDirectory ("client-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$gameArguments = @("/viewer_id=$Profile", '/onetime_token=local', '/access_token=local', '-logFile', ('"{0}"' -f $logPath))
$gameProcess = Start-Process -FilePath $gamePath -ArgumentList $gameArguments -WorkingDirectory $clientPath -PassThru
Write-Output "Started profile $Profile with process $($gameProcess.Id)."
Write-Output "Client log: $logPath"
