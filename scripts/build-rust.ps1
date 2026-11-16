<#
.SYNOPSIS
    Build the native Rust desktop app (gpui-kit) into a portable Windows folder.

.DESCRIPTION
    Freezes the Python transcription backend into a single executable with
    PyInstaller, compiles the Rust shell in release mode, and assembles a
    relocatable folder whose entry point is Tiktuv.exe. Double-clicking the exe
    spawns the bundled backend automatically; data, models and FFmpeg all live
    inside the folder, so it runs from any path or drive letter.

    Prerequisites, checked below:
      * Rust stable toolchain (https://rustup.rs)
      * The backend virtualenv at .venv (run scripts\setup.ps1 first)
      * ffmpeg + ffprobe on PATH (or they are downloaded automatically)

    Output: build\Tiktuv-Portable\  and  build\Tiktuv-Portable.zip (-Zip)
#>
[CmdletBinding()]
param(
    [string]$Destination = 'build\Tiktuv-Portable',
    [switch]$SkipBackendFreeze,
    [switch]$SkipRustBuild,
    [switch]$Zip
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$ffmpegUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$cache = Join-Path $env:TEMP 'tiktuv-rust-cache'

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Get-Cached {
    param([string]$Url, [string]$Name)
    if (-not (Test-Path $cache)) { New-Item -ItemType Directory -Path $cache | Out-Null }
    $path = Join-Path $cache $Name
    if (Test-Path $path) { return $path }
    $previous = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try { Invoke-WebRequest -Uri $Url -OutFile $path -UseBasicParsing }
    finally { $ProgressPreference = $previous }
    return $path
}

# -- toolchain --------------------------------------------------------------
$cargo = (Get-Command cargo -ErrorAction SilentlyContinue).Source
if (-not $cargo) {
    $rustupCargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
    if (Test-Path $rustupCargo) { $cargo = $rustupCargo }
}
if (-not $cargo) { throw 'Rust was not found. Install it from https://rustup.rs' }

$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) { throw 'Run scripts\setup.ps1 first.' }

# -- 1. freeze the backend --------------------------------------------------
$backendExe = Join-Path $repoRoot 'build\rust-portable\tiktuv-backend.exe'
New-Item -ItemType Directory -Force -Path (Split-Path $backendExe) | Out-Null

if (-not $SkipBackendFreeze) {
    Write-Step 'Freezing the Python backend with PyInstaller'
    & $venvPython -m pip install --quiet 'pyinstaller==6.11.1'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed' }

    # freeze_entry.py is the entry point: tiktuv/__main__.py uses relative
    # imports, which fail when PyInstaller runs it as a top-level __main__.
    & $venvPython -m PyInstaller `
        --noconfirm --clean --onefile `
        --name tiktuv-backend `
        --distpath (Split-Path $backendExe) `
        --workpath (Join-Path $repoRoot 'build\pyinstaller') `
        --specpath (Join-Path $repoRoot 'build') `
        --collect-all tiktuv `
        --collect-all torch `
        --collect-data reportlab `
        --collect-data docx `
        --hidden-import uvicorn.logging `
        --hidden-import uvicorn.loops.auto `
        --hidden-import uvicorn.protocols.http.auto `
        --hidden-import uvicorn.lifespan.on `
        (Join-Path $repoRoot 'apps\backend\freeze_entry.py')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
}

if (-not (Test-Path $backendExe)) { throw "Frozen backend not found at $backendExe" }
Write-Host "    backend: $([math]::Round((Get-Item $backendExe).Length / 1MB, 1)) MB"

# -- 2. build the Rust shell ------------------------------------------------
$rustExe = Join-Path $repoRoot 'apps\desktop-gpui\target\release\tiktuv-desktop.exe'
if (-not $SkipRustBuild) {
    Write-Step 'Building the Rust desktop app (release)'
    Push-Location (Join-Path $repoRoot 'apps\desktop-gpui')
    & $cargo build --release
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'cargo build failed' }
    Pop-Location
}
if (-not (Test-Path $rustExe)) { throw "Rust release exe not found at $rustExe" }
Write-Host "    shell:   $([math]::Round((Get-Item $rustExe).Length / 1MB, 1)) MB"

# -- 3. assemble the folder -------------------------------------------------
Write-Step 'Assembling the portable folder'
$stage = Join-Path $repoRoot $Destination
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'tools') | Out-Null

Copy-Item $rustExe (Join-Path $stage 'Tiktuv.exe')
Copy-Item $backendExe (Join-Path $stage 'tiktuv-backend.exe')

# FFmpeg: the local install first, then a download.
$ffmpeg = (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue).Source
$ffprobe = (Get-Command ffprobe.exe -ErrorAction SilentlyContinue).Source
if ($ffmpeg -and $ffprobe) {
    Copy-Item $ffmpeg (Join-Path $stage 'tools\ffmpeg.exe')
    Copy-Item $ffprobe (Join-Path $stage 'tools\ffprobe.exe')
} else {
    Write-Step 'Downloading FFmpeg'
    $zipPath = Get-Cached $ffmpegUrl 'ffmpeg-release-essentials.zip'
    $extract = Join-Path $cache 'ffmpeg-extract'
    if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    Expand-Archive -Path $zipPath -DestinationPath $extract -Force
    foreach ($exe in 'ffmpeg.exe', 'ffprobe.exe') {
        $found = Get-ChildItem -Path $extract -Filter $exe -Recurse | Select-Object -First 1
        if (-not $found) { throw "$exe not found in the FFmpeg archive" }
        Copy-Item $found.FullName (Join-Path $stage "tools\$exe")
    }
}

$readme = @"
Tiktuv (תכתוב) — portable Windows build
======================================

Double-click Tiktuv.exe. Nothing is installed: no setup, no registry entries,
no files outside this folder. To remove Tiktuv, delete the folder.

Tiktuv is a native desktop program that transcribes Hebrew audio and video
entirely on your machine. On first use it downloads the Hebrew speech model
(~6.2 GB) into this folder and shows its size and destination first. That is
the only step that touches the network.

What's in here
--------------
  Tiktuv.exe          the app — double-click this
  tiktuv-backend.exe  the transcription engine (started automatically)
  tools\              FFmpeg
  data\               your jobs, transcripts, exports, logs (created on first run)
  models\             the speech model (downloaded on first use)

Requirements
------------
  Windows 10 or 11, 64-bit. No Python and no installer are required.

The folder may be copied to a USB stick or another PC and run from any path or
drive letter. Running from a USB stick works, but the model is over 6 GB and is
read on every start, so USB 3.0 or an SSD makes a large difference.
"@
Set-Content -Path (Join-Path $stage 'README.txt') -Value $readme -Encoding utf8

# -- 4. zip -----------------------------------------------------------------
if ($Zip) {
    Write-Step 'Creating the ZIP'
    $zipOut = "$stage.zip"
    if (Test-Path $zipOut) { Remove-Item $zipOut -Force }
    # tar streams to disk; Compress-Archive buffers the whole tree in memory.
    tar -a -c -f $zipOut -C (Split-Path $stage) (Split-Path $stage -Leaf)
    if ($LASTEXITCODE -ne 0) { throw 'zip failed' }
    Write-Host ("    {0}  ({1:N1} GB)" -f $zipOut, ((Get-Item $zipOut).Length / 1GB))
}

$size = (Get-ChildItem $stage -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ""
Write-Host '  Portable build ready:' -ForegroundColor Green
Write-Host ("    {0}  ({1:N1} GB)" -f $stage, ($size / 1GB))
Write-Host ''
Write-Host "  Copy the folder anywhere and double-click 'Tiktuv.exe'."
Write-Host ''
