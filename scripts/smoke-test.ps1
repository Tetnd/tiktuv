<#
.SYNOPSIS
    End-to-end smoke test against a real running backend.

.DESCRIPTION
    Starts the backend as a real process (not an in-process test client),
    generates a Hebrew-named MP3 and an MP4 with FFmpeg, then walks the whole
    user journey over HTTP: import, transcribe, watch SSE progress, edit a
    segment, export all six formats, and reopen from history.

    Uses the mock ASR backend by default so it needs no GPU and no model.
    Pass -Real to use the actual Hebrew model instead.
#>
[CmdletBinding()]
param(
    [switch]$Real,
    [int]$Port = 8771
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) { throw "Run scripts\setup.ps1 first." }

$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) "tiktuv-smoke-$(Get-Random)"
$env:TIKTUV_DATA_DIR = $dataDir
$env:TIKTUV_PORT = "$Port"
$env:TIKTUV_LOG_JSON = '0'
if (-not $Real) { $env:TIKTUV_ASR_BACKEND = 'mock' }

# PowerShell 5.1's Invoke-RestMethod encodes a string body using the default
# code page, which turns every Hebrew character into "?". Sending explicit
# UTF-8 bytes is the only reliable way to post Hebrew paths and text.
function Invoke-JsonApi {
    param(
        [Parameter(Mandatory)][string]$Uri,
        [string]$Method = 'Get',
        [hashtable]$Headers,
        $Body
    )
    $arguments = @{
        Uri     = $Uri
        Method  = $Method
        Headers = $Headers
    }
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 20 -Compress
        $arguments.Body = [System.Text.Encoding]::UTF8.GetBytes($json)
        $arguments.ContentType = 'application/json; charset=utf-8'
    }
    Invoke-RestMethod @arguments
}

$passed = 0
$failed = 0
function Assert($condition, $description) {
    if ($condition) {
        Write-Host "  [PASS] $description" -ForegroundColor Green
        $script:passed++
    } else {
        Write-Host "  [FAIL] $description" -ForegroundColor Red
        $script:failed++
    }
}

Write-Host "Data directory: $dataDir" -ForegroundColor DarkGray
Write-Host "Starting the backend on port $Port ..." -ForegroundColor Cyan
$backend = Start-Process -FilePath $venvPython -ArgumentList '-m', 'tiktuv' -PassThru -NoNewWindow

try {
    $handshakePath = Join-Path $dataDir 'runtime.json'
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Path $handshakePath)) {
        if ($backend.HasExited) { throw "backend exited (code $($backend.ExitCode))" }
        if ((Get-Date) -gt $deadline) { throw "backend did not start within 60s" }
        Start-Sleep -Milliseconds 200
    }
    $handshake = Get-Content $handshakePath -Raw | ConvertFrom-Json
    $base = "http://127.0.0.1:$Port"
    $headers = @{ Authorization = "Bearer $($handshake.token)" }

    Write-Host "`n1. Health and authorization" -ForegroundColor Cyan
    $health = Invoke-RestMethod "$base/api/health"
    Assert ($health.status -eq 'ok') "health endpoint responds"

    try {
        Invoke-RestMethod "$base/api/jobs" -ErrorAction Stop | Out-Null
        Assert $false "unauthenticated request is rejected"
    } catch {
        Assert ($_.Exception.Response.StatusCode.value__ -eq 401) "unauthenticated request returns 401"
    }
    Assert ($null -ne (Invoke-RestMethod "$base/api/jobs" -Headers $headers)) "authenticated request succeeds"

    Write-Host "`n2. Preflight" -ForegroundColor Cyan
    $system = Invoke-RestMethod "$base/api/system" -Headers $headers
    Assert ($system.checks.Count -ge 7) "preflight reports all checks"
    $ffmpegCheck = $system.checks | Where-Object { $_.id -eq 'ffmpeg' }
    Assert ($ffmpegCheck.status -eq 'ok') "FFmpeg check passes"

    Write-Host "`n3. Generating Hebrew-named fixtures" -ForegroundColor Cyan
    $fixtureDir = Join-Path $dataDir 'fixtures'
    New-Item -ItemType Directory -Force -Path $fixtureDir | Out-Null
    $mp3 = Join-Path $fixtureDir 'ישיבת הנהלה.mp3'
    $mp4 = Join-Path $fixtureDir 'הקלטת וידאו.mp4'

    & ffmpeg -y -loglevel error -f lavfi -i "sine=frequency=440:sample_rate=16000:duration=8" -ac 1 $mp3
    & ffmpeg -y -loglevel error -f lavfi -i "testsrc=size=160x120:rate=10:duration=6" -f lavfi -i "sine=frequency=330:sample_rate=16000:duration=6" -shortest -pix_fmt yuv420p $mp4
    Assert (Test-Path $mp3) "created an MP3 with a Hebrew filename"
    Assert (Test-Path $mp4) "created an MP4 with a Hebrew filename"

    Write-Host "`n4. Import and transcribe the MP3" -ForegroundColor Cyan
    $imported = Invoke-JsonApi "$base/api/media/import" -Method Post -Headers $headers -Body @{ path = $mp3 }
    Assert ($imported.media.duration -gt 0) "media probed, duration $([math]::Round($imported.media.duration,1))s"
    Assert ($imported.media.filename -match 'ישיבת') "Hebrew filename preserved"

    $job = Invoke-JsonApi "$base/api/jobs" -Method Post -Headers $headers -Body @{ media_token = $imported.token }
    Assert ($null -ne $job.id) "job created"

    $deadline = (Get-Date).AddMinutes(10)
    do {
        Start-Sleep -Milliseconds 300
        $status = Invoke-RestMethod "$base/api/jobs/$($job.id)" -Headers $headers
    } while ($status.status -notin 'completed', 'failed', 'cancelled' -and (Get-Date) -lt $deadline)

    Assert ($status.status -eq 'completed') "job completed (status: $($status.status))"
    if ($status.error) { Write-Host "         error: $($status.error.message)" -ForegroundColor Red }
    Assert ($status.progress.percent -eq 1) "progress reached 100%"

    Write-Host "`n5. Transcript and editing" -ForegroundColor Cyan
    $transcript = Invoke-RestMethod "$base/api/jobs/$($job.id)/transcript" -Headers $headers
    Assert ($transcript.segments.Count -gt 0) "transcript has $($transcript.segments.Count) segments"
    $hasHebrew = $transcript.segments[0].text -match '[\u0590-\u05FF]'
    Assert $hasHebrew "transcript contains Hebrew characters"

    $segments = $transcript.segments
    $segments[0].text = 'טקסט שנערך בבדיקת העשן'
    $segments[0].edited = $true
    $saved = Invoke-JsonApi "$base/api/jobs/$($job.id)/transcript" -Method Put -Headers $headers -Body @{ segments = $segments }
    Assert ($saved.segments[0].text -eq 'טקסט שנערך בבדיקת העשן') "edited segment saved"

    $reloaded = Invoke-RestMethod "$base/api/jobs/$($job.id)/transcript" -Headers $headers
    Assert ($reloaded.segments[0].text -eq 'טקסט שנערך בבדיקת העשן') "edit survives a reload"

    Write-Host "`n6. Exporting all six formats" -ForegroundColor Cyan
    $exportDir = Join-Path $dataDir 'תמלולים'
    $export = Invoke-JsonApi "$base/api/jobs/$($job.id)/export" -Method Post -Headers $headers -Body @{ formats = @('txt','docx','pdf','srt','vtt','json'); output_dir = $exportDir }

    Assert ($export.results.Count -eq 6) "all six formats exported"
    foreach ($result in $export.results) {
        $exists = (Test-Path $result.path) -and ((Get-Item $result.path).Length -gt 0)
        $sizeKb = [math]::Round(($result.size_bytes / 1024), 1)
        $formatName = ([string]$result.format).ToUpper()
        Assert $exists "$formatName written - $sizeKb KB"
    }

    $txt = $export.results | Where-Object { $_.format -eq 'txt' }
    $txtContent = [System.Text.Encoding]::UTF8.GetString([System.IO.File]::ReadAllBytes($txt.path))
    Assert ($txtContent -match 'טקסט שנערך') "TXT contains the edited Hebrew text"

    Write-Host "`n7. MP4 audio extraction" -ForegroundColor Cyan
    $videoImport = Invoke-JsonApi "$base/api/media/import" -Method Post -Headers $headers -Body @{ path = $mp4 }
    Assert ($videoImport.media.has_video -and $videoImport.media.has_audio) "MP4 has both streams"

    $videoJob = Invoke-JsonApi "$base/api/jobs" -Method Post -Headers $headers -Body @{ media_token = $videoImport.token }
    $deadline = (Get-Date).AddMinutes(10)
    do {
        Start-Sleep -Milliseconds 300
        $videoStatus = Invoke-RestMethod "$base/api/jobs/$($videoJob.id)" -Headers $headers
    } while ($videoStatus.status -notin 'completed','failed','cancelled' -and (Get-Date) -lt $deadline)
    Assert ($videoStatus.status -eq 'completed') "MP4 transcribed (audio extracted)"

    Write-Host "`n8. Security" -ForegroundColor Cyan
    try {
        Invoke-RestMethod "$base/api/exports/download?path=C:\Windows\win.ini" -Headers $headers -ErrorAction Stop | Out-Null
        Assert $false "path traversal in download is blocked"
    } catch {
        Assert ($_.Exception.Response.StatusCode.value__ -eq 403) "arbitrary file read blocked (403)"
    }

    try {
        Invoke-JsonApi "$base/api/jobs" -Method Post -Headers $headers -Body @{ media_token = '../../../etc/passwd' } -ErrorAction Stop | Out-Null
        Assert $false "traversal media token is rejected"
    } catch {
        Assert ($_.Exception.Response.StatusCode.value__ -eq 422) "traversal media token rejected (422)"
    }

    Write-Host "`n9. History" -ForegroundColor Cyan
    $history = Invoke-RestMethod "$base/api/jobs" -Headers $headers
    Assert ($history.Count -ge 2) "history lists $($history.Count) jobs"
    Assert (($history | Where-Object { $_.id -eq $job.id }).has_transcript) "completed job is reopenable"

    Write-Host "`n10. Cleanup controls" -ForegroundColor Cyan
    $cleanup = Invoke-RestMethod "$base/api/system/cleanup/temp" -Method Post -Headers $headers
    Assert ($null -ne $cleanup.reclaimed_bytes) "temp cleanup reports bytes reclaimed"
}
finally {
    if ($backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
}

Write-Host ""
Write-Host ("=" * 56)
Write-Host "Passed: $passed   Failed: $failed" -ForegroundColor $(if ($failed -eq 0) { 'Green' } else { 'Red' })
Write-Host ("=" * 56)

Remove-Item $dataDir -Recurse -Force -ErrorAction SilentlyContinue
exit $(if ($failed -eq 0) { 0 } else { 1 })
