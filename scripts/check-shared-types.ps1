<#
.SYNOPSIS
    Verify that packages/shared-types matches the backend Pydantic enums.

.DESCRIPTION
    The TypeScript contracts are written by hand, so they can drift from the
    Python schemas. This compares every string-union member and exits non-zero
    on any mismatch, so CI catches drift instead of the UI silently mishandling
    an unknown value.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) { throw "Run scripts\setup.ps1 first." }

$sharedTypes = Join-Path $repoRoot 'packages\shared-types\src\index.ts'
if (-not (Test-Path $sharedTypes)) { throw "shared-types not found at $sharedTypes" }
$typescript = Get-Content $sharedTypes -Raw

# Python enum name -> the TypeScript const array that mirrors it.
$pairs = @{
    'JobStatus'         = 'JOB_STATUSES'
    'JobStage'          = 'JOB_STAGES'
    'Language'          = 'LANGUAGES'
    'DevicePreference'  = 'DEVICE_PREFERENCES'
    'QualityPreset'     = 'QUALITY_PRESETS'
    'TimestampMode'     = 'TIMESTAMP_MODES'
    'ExportFormat'      = 'EXPORT_FORMATS'
    'CheckStatus'       = 'CHECK_STATUSES'
    'ModelDownloadState' = 'MODEL_DOWNLOAD_STATES'
    'UrlFetchState'     = 'URL_FETCH_STATES'
}

$failed = $false

foreach ($entry in $pairs.GetEnumerator()) {
    $pythonEnum = $entry.Key
    $tsConst = $entry.Value

    $pythonMembers = & $venvPython -c @"
from tiktuv.schemas import $pythonEnum
print(','.join(member.value for member in $pythonEnum))
"@
    if ($LASTEXITCODE -ne 0) { throw "could not read $pythonEnum from tiktuv.schemas" }
    $pythonList = ($pythonMembers.Trim() -split ',') | Sort-Object

    if ($typescript -notmatch "(?s)$tsConst\s*=\s*\[(.*?)\]\s*as const") {
        Write-Host "[FAIL] $tsConst is missing from shared-types" -ForegroundColor Red
        $failed = $true
        continue
    }
    $tsList = ([regex]::Matches($Matches[1], "'([^']+)'") |
        ForEach-Object { $_.Groups[1].Value }) | Sort-Object

    $difference = Compare-Object -ReferenceObject $pythonList -DifferenceObject $tsList
    if ($difference) {
        Write-Host "[FAIL] $pythonEnum <-> $tsConst drifted:" -ForegroundColor Red
        foreach ($item in $difference) {
            $side = if ($item.SideIndicator -eq '<=') { 'only in Python' } else { 'only in TypeScript' }
            Write-Host "         $($item.InputObject)  ($side)" -ForegroundColor Red
        }
        $failed = $true
    } else {
        Write-Host "[ OK ] $pythonEnum matches $tsConst ($($pythonList.Count) members)" -ForegroundColor Green
    }
}

Write-Host ""
if ($failed) {
    Write-Host "shared-types is out of sync with the backend schemas." -ForegroundColor Red
    exit 1
}
Write-Host "shared-types matches the backend schemas." -ForegroundColor Green
exit 0
