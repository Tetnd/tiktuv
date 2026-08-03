<#
.SYNOPSIS
    Launch Tiktuv as a desktop application.

.DESCRIPTION
    Started by the desktop shortcut. It:

      1. starts the backend and waits for its handshake file,
      2. opens the UI in a dedicated application window (Edge or Chrome in
         --app mode: no address bar, no tabs, its own taskbar entry),
      3. waits for that window to close, then shuts the backend down.

    The backend serves the UI itself, so there is no second server and the
    session token never leaves the loopback interface.

.PARAMETER Port
    Backend port. A free one is chosen automatically if this is taken.

.PARAMETER NoWindow
    Start the backend only and print the URL. Useful for debugging.
#>
[CmdletBinding()]
param(
    [int]$Port = 8756,
    [switch]$NoWindow
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

function Show-Error($message) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        $message, 'Tiktuv', 'OK', 'Error') | Out-Null
}

# -- prerequisites ---------------------------------------------------------
$venvPython = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $venvPython)) {
    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path $venvPython)) {
    Show-Error "Tiktuv is not installed yet.`n`nRun scripts\setup.ps1 from:`n$repoRoot"
    exit 1
}

$distIndex = Join-Path $repoRoot 'apps\frontend\dist\index.html'
if (-not (Test-Path $distIndex)) {
    Show-Error "The Tiktuv interface has not been built yet.`n`nRun:`nnpm --prefix apps/frontend run build"
    exit 1
}

# -- reuse a running instance ----------------------------------------------
# Starting a second backend on the same data directory is refused by the
# backend itself. Detect a live one here and just open a window against it,
# so double-clicking the icon twice behaves like any other desktop app.
$dataDir = Join-Path $env:LOCALAPPDATA 'Tiktuv'
$lockPath = Join-Path $dataDir 'instance.lock'
$existing = $null

if (Test-Path $lockPath) {
    try {
        $lock = Get-Content $lockPath -Raw | ConvertFrom-Json
        if (Get-Process -Id $lock.pid -ErrorAction SilentlyContinue) {
            $probe = Invoke-WebRequest "http://127.0.0.1:$($lock.port)/api/health" `
                -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($probe.StatusCode -eq 200) { $existing = $lock }
        }
    } catch {
        # Stale or unreachable: the backend clears it on the next start.
        $existing = $null
    }
}

# -- pick a free port ------------------------------------------------------
function Test-PortFree([int]$candidate) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Loopback, $candidate)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

if (-not $existing -and -not (Test-PortFree $Port)) {
    $found = $false
    foreach ($candidate in ($Port + 1)..($Port + 20)) {
        if (Test-PortFree $candidate) { $Port = $candidate; $found = $true; break }
    }
    if (-not $found) {
        Show-Error "No free port near $Port. Is another copy of Tiktuv already running?"
        exit 1
    }
}

# -- start the backend, or attach to the running one -----------------------
$handshakePath = Join-Path $dataDir 'runtime.json'
$backend = $null
$weStartedIt = $false

if (-not $existing) {
    Remove-Item $handshakePath -ErrorAction SilentlyContinue
    $env:TIKTUV_PORT = "$Port"
    $backend = Start-Process -FilePath $venvPython `
        -ArgumentList '-m', 'tiktuv', '--port', "$Port" `
        -WindowStyle Hidden -PassThru
    $weStartedIt = $true
}

try {
    if ($existing) {
        $url = "http://127.0.0.1:$($existing.port)/?s=$(Get-Random)"
    } else {
        $deadline = (Get-Date).AddSeconds(90)
        while (-not (Test-Path $handshakePath)) {
            if ($backend.HasExited) {
                throw "The Tiktuv service stopped during startup (exit code $($backend.ExitCode)). See $dataDir\logs\tiktuv.log"
            }
            if ((Get-Date) -gt $deadline) {
                throw "The Tiktuv service did not start within 90 seconds. See $dataDir\logs\tiktuv.log"
            }
            Start-Sleep -Milliseconds 200
        }
        $handshake = Get-Content $handshakePath -Raw | ConvertFrom-Json
        $url = "http://127.0.0.1:$($handshake.port)/?s=$(Get-Random)"
    }

    if ($NoWindow) {
        Write-Host "Tiktuv is running at $url"
        Write-Host "Press Ctrl+C to stop."
        while ($backend -and -not $backend.HasExited) { Start-Sleep -Seconds 1 }
        exit 0
    }

    # -- open a dedicated application window -------------------------------
    # --app mode gives a real window with no address bar and its own taskbar
    # entry. A separate --user-data-dir keeps it out of the user's browser
    # profile, so closing the window closes only Tiktuv.
    $browsers = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    $browser = $browsers | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $browser) {
        Start-Process $url   # last resort: the default browser, with chrome
        Write-Host "Opened $url in your default browser."
        while ($backend -and -not $backend.HasExited) { Start-Sleep -Seconds 1 }
        exit 0
    }

    $profileDir = Join-Path $dataDir 'window-profile'
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

    $window = Start-Process -FilePath $browser -PassThru -ArgumentList @(
        "--app=$url",
        "--user-data-dir=$profileDir",
        '--window-size=1280,860',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=Translate,MediaRouter'
    )

    # The launched process may hand off to an existing instance for this
    # profile and exit immediately; in that case fall back to watching for any
    # process using our profile directory.
    Start-Sleep -Seconds 2
    if ($window.HasExited) {
        while ($true) {
            Start-Sleep -Seconds 2
            $alive = Get-CimInstance Win32_Process -Filter "Name='$(Split-Path $browser -Leaf)'" |
                Where-Object { $_.CommandLine -like "*$profileDir*" }
            if (-not $alive) { break }
            if ($backend -and $backend.HasExited) { break }
        }
    } else {
        $window.WaitForExit()
    }
}
catch {
    Show-Error $_.Exception.Message
    exit 1
}
finally {
    # Only tear down a backend this launcher started. Attaching to someone
    # else's instance and then killing it would close their app.
    if ($weStartedIt -and $backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
        Remove-Item $handshakePath -ErrorAction SilentlyContinue
    }
}
