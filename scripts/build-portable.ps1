<#
.SYNOPSIS
    Builds a self-contained, path-independent Tiktuv folder.

.DESCRIPTION
    The normal installer creates a virtual environment, which is *not*
    relocatable: 'pyvenv.cfg' and every 'Scripts\*.exe' shim embed the absolute
    path the venv was created at, so moving the folder — or plugging the stick
    into a machine that assigns a different drive letter — breaks it.

    This build instead uses CPython's *embeddable* distribution: a plain
    interpreter with no installer, no registry keys and no %APPDATA% presence,
    which runs correctly from any path. Everything it needs sits beside it:

        Tiktuv-Portable\
            Tiktuv.cmd              launch (no console window)
            Tiktuv (Console).cmd    same, with the log visible — for diagnosis
            README.txt
            python\                 embeddable interpreter + site-packages
            app\apps\backend\       the backend package
            app\apps\frontend\dist\ the built UI
            tools\                  ffmpeg.exe, ffprobe.exe
            models\                 model cache (only with -IncludeModel)
            data\                   created on first run: database, media, logs

    The launcher points TIKTUV_DATA_DIR, TIKTUV_MODELS_DIR and the FFmpeg paths
    at folders beside itself, resolved at run time from %~dp0, so nothing is
    written outside the folder and no absolute path is ever baked in.

.PARAMETER Destination
    Where to build. Defaults to the desktop.

.PARAMETER Cuda
    Install the CUDA build of PyTorch instead of the CPU build. Adds roughly
    2 GB and only helps on machines with a recent NVIDIA GPU and driver; the
    CPU build runs everywhere. Off by default because a portable folder is
    normally meant to run on machines you do not control.

.PARAMETER IncludeModel
    Copy the already-downloaded ivrit-ai/whisper-large-v3 weights (~6.2 GB)
    into the folder, so the first run works with no network at all. Without
    this the folder is ~2.5 GB and downloads the model on first use.

.PARAMETER Zip
    Also produce a .zip beside the folder.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-portable.ps1 -IncludeModel -Zip
#>

[CmdletBinding()]
param(
    [string]$Destination = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Tiktuv-Portable'),
    [switch]$Cuda,
    [switch]$IncludeModel,
    [switch]$Zip
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# The embeddable build must match the Python the project targets (3.12).
$pythonVersion = '3.12.8'
$pythonZipUrl  = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip"
$getPipUrl     = 'https://bootstrap.pypa.io/get-pip.py'
$ffmpegUrl     = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'

$cpuTorch = @{ Spec = 'torch==2.5.1'; Index = 'https://download.pytorch.org/whl/cpu' }
$gpuTorch = @{ Spec = 'torch==2.8.0'; Index = 'https://download.pytorch.org/whl/cu128' }

$repoRoot = Split-Path -Parent $PSScriptRoot
$stage    = $Destination
$cache    = Join-Path $env:TEMP 'tiktuv-portable-cache'

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Note { param([string]$Text) Write-Host "    $Text" -ForegroundColor DarkGray }

function Get-Cached {
    <# Downloads once into a temp cache, so re-running the build is cheap. #>
    param([string]$Url, [string]$Name)
    if (-not (Test-Path $cache)) { New-Item -ItemType Directory -Path $cache | Out-Null }
    $path = Join-Path $cache $Name
    if (Test-Path $path) {
        Write-Note "using cached $Name"
        return $path
    }
    Write-Note "downloading $Name"
    $previous = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'   # the progress bar makes this ~10x slower
    try { Invoke-WebRequest -Uri $Url -OutFile $path -UseBasicParsing }
    finally { $ProgressPreference = $previous }
    return $path
}

# -- 0. the UI must be built, since it is copied, not built here -------------
Write-Step 'Checking prerequisites'

$dist = Join-Path $repoRoot 'apps\frontend\dist'
if (-not (Test-Path (Join-Path $dist 'index.html'))) {
    throw "The frontend is not built. Run: npm --prefix apps/frontend run build"
}
Write-Note 'frontend build found'

if (Test-Path $stage) {
    Write-Note "removing the previous build at $stage"
    Remove-Item -Recurse -Force $stage
}
New-Item -ItemType Directory -Path $stage | Out-Null

# -- 1. the interpreter ------------------------------------------------------
Write-Step "Unpacking the embeddable Python $pythonVersion"

$pythonDir = Join-Path $stage 'python'
Expand-Archive -Path (Get-Cached $pythonZipUrl "python-$pythonVersion-embed-amd64.zip") `
               -DestinationPath $pythonDir -Force

# The embeddable build ships with site-packages disabled and a fixed search
# path. Both have to be opened up or pip cannot install and nothing it installs
# can be imported. '.\Lib\site-packages' is where pip puts things here.
#
# The application directory has to go in here too, and *not* in PYTHONPATH:
# when a ._pth file is present the interpreter ignores PYTHONPATH entirely and
# builds sys.path from this file alone. Entries are resolved relative to the
# directory holding python.exe, so '..\app\apps\backend' follows the folder
# wherever it is copied — which is the whole point of this build.
$pthFile = Get-ChildItem -Path $pythonDir -Filter 'python*._pth' | Select-Object -First 1
if (-not $pthFile) { throw "no ._pth file in the embeddable distribution" }
$pth = Get-Content $pthFile.FullName
$pth = $pth -replace '^\s*#\s*import site\s*$', 'import site'
foreach ($entry in 'Lib\site-packages', '..\app\apps\backend') {
    if ($pth -notcontains $entry) { $pth += $entry }
}
Set-Content -Path $pthFile.FullName -Value $pth -Encoding ascii
Write-Note "enabled site-packages and the app path in $($pthFile.Name)"

$portablePython = Join-Path $pythonDir 'python.exe'
if (-not (Test-Path (Join-Path $pythonDir 'pythonw.exe'))) {
    throw "pythonw.exe missing — the launcher needs it to start without a console"
}

Write-Step 'Installing pip'
& $portablePython (Get-Cached $getPipUrl 'get-pip.py') --no-warn-script-location --quiet
if ($LASTEXITCODE -ne 0) { throw 'get-pip failed' }

# -- 2. dependencies ---------------------------------------------------------
$torch = if ($Cuda) { $gpuTorch } else { $cpuTorch }
Write-Step "Installing PyTorch ($($torch.Spec), $(if ($Cuda) { 'CUDA' } else { 'CPU' }))"
Write-Note 'this is the slow part — several hundred MB'
& $portablePython -m pip install $torch.Spec --index-url $torch.Index --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw 'installing torch failed' }

Write-Step 'Installing the application dependencies'
# --target is deliberately not used: plain 'install' with site enabled puts
# everything in python\Lib\site-packages, which the ._pth above already reads.
& $portablePython -m pip install "$repoRoot\apps\backend[asr,desktop]" --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw 'installing the backend dependencies failed' }

# Installing the project pulled its dependencies in, which is the point, but it
# also left a copy of the package in site-packages. The copy under app\ is the
# one the interpreter loads and the one a user can update by replacing files, so
# remove the duplicate and keep exactly one.
# ('tiktuv-backend' is the distribution name; 'tiktuv' is the import name.)
# stderr is not redirected here: under 'Stop', PowerShell 5.1 turns a native
# command's stderr into a terminating NativeCommandError even on success.
$previousEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $portablePython -m pip uninstall -y tiktuv-backend --quiet | Out-Null
$ErrorActionPreference = $previousEap

# -- 3. the application ------------------------------------------------------
Write-Step 'Copying the application'

$appBackend = Join-Path $stage 'app\apps\backend'
New-Item -ItemType Directory -Path $appBackend -Force | Out-Null
Copy-Item -Recurse -Path (Join-Path $repoRoot 'apps\backend\tiktuv') -Destination $appBackend

# static_files.py locates the UI at ../../frontend/dist relative to the
# package, so the repository's own layout is preserved inside app\.
$appFrontend = Join-Path $stage 'app\apps\frontend'
New-Item -ItemType Directory -Path $appFrontend -Force | Out-Null
Copy-Item -Recurse -Path $dist -Destination $appFrontend

Get-ChildItem -Path $appBackend -Include '__pycache__' -Recurse -Directory |
    Remove-Item -Recurse -Force

# app.py looks for its window icon at <package>\assets\icon.ico first.
$icon = Join-Path $repoRoot 'apps\desktop\src-tauri\icons\icon.ico'
if (Test-Path $icon) {
    $assets = Join-Path $appBackend 'tiktuv\assets'
    New-Item -ItemType Directory -Path $assets -Force | Out-Null
    Copy-Item $icon (Join-Path $assets 'icon.ico')
    Write-Note 'window icon bundled'
}

# -- 4. FFmpeg ---------------------------------------------------------------
Write-Step 'Bundling FFmpeg'

$tools = Join-Path $stage 'tools'
New-Item -ItemType Directory -Path $tools | Out-Null

# Prefer the copy already on this machine; fall back to a download.
$localFfmpeg = (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue).Source
$localFfprobe = (Get-Command ffprobe.exe -ErrorAction SilentlyContinue).Source
if ($localFfmpeg -and $localFfprobe) {
    Copy-Item $localFfmpeg (Join-Path $tools 'ffmpeg.exe')
    Copy-Item $localFfprobe (Join-Path $tools 'ffprobe.exe')
    Write-Note "copied from $localFfmpeg"
} else {
    $zipPath = Get-Cached $ffmpegUrl 'ffmpeg-release-essentials.zip'
    $extract = Join-Path $cache 'ffmpeg-extract'
    if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    Expand-Archive -Path $zipPath -DestinationPath $extract -Force
    foreach ($exe in 'ffmpeg.exe', 'ffprobe.exe') {
        $found = Get-ChildItem -Path $extract -Filter $exe -Recurse | Select-Object -First 1
        if (-not $found) { throw "$exe not found in the FFmpeg archive" }
        Copy-Item $found.FullName (Join-Path $tools $exe)
    }
    Write-Note 'downloaded a release build'
}

# -- 5. the model (optional) -------------------------------------------------
$modelsDir = Join-Path $stage 'models'
New-Item -ItemType Directory -Path $modelsDir | Out-Null

if ($IncludeModel) {
    Write-Step 'Copying the model weights'
    $source = Join-Path $env:LOCALAPPDATA 'Tiktuv\models\models--ivrit-ai--whisper-large-v3'
    if (-not (Test-Path $source)) {
        Write-Warning "The model is not downloaded on this machine ($source)."
        Write-Warning "Skipping. The folder will download it on first run instead."
    } else {
        $bytes = (Get-ChildItem $source -Recurse -File | Measure-Object -Property Length -Sum).Sum
        Write-Note ("copying {0:N1} GB — this takes a while" -f ($bytes / 1GB))
        Copy-Item -Recurse -Path $source -Destination $modelsDir
    }
}

# -- 6. launchers ------------------------------------------------------------
Write-Step 'Writing the launchers'

# %~dp0 is the folder this .cmd lives in, resolved at run time, and it already
# ends in a backslash. Nothing here depends on the drive letter or the path.
$launcher = @'
@echo off
rem Tiktuv - portable launcher. Everything stays inside this folder.
setlocal
set "ROOT=%~dp0"
set "TIKTUV_DATA_DIR=%ROOT%data"
set "TIKTUV_MODELS_DIR=%ROOT%models"
set "TIKTUV_FFMPEG_PATH=%ROOT%tools\ffmpeg.exe"
set "TIKTUV_FFPROBE_PATH=%ROOT%tools\ffprobe.exe"
set "HF_HUB_DISABLE_TELEMETRY=1"
rem No PYTHONPATH here: an embeddable interpreter ignores it and builds
rem sys.path from python\python312._pth, which already lists the app folder.
start "" "%ROOT%python\pythonw.exe" -m tiktuv.app
'@

$console = @'
@echo off
rem Same as Tiktuv.cmd but keeps the console open so the log is visible.
rem Use this if the window does not appear.
setlocal
set "ROOT=%~dp0"
set "TIKTUV_DATA_DIR=%ROOT%data"
set "TIKTUV_MODELS_DIR=%ROOT%models"
set "TIKTUV_FFMPEG_PATH=%ROOT%tools\ffmpeg.exe"
set "TIKTUV_FFPROBE_PATH=%ROOT%tools\ffprobe.exe"
set "HF_HUB_DISABLE_TELEMETRY=1"
"%ROOT%python\python.exe" -m tiktuv.app
echo.
pause
'@

# ASCII: these launchers contain no non-ASCII text, and a BOM would be echoed.
Set-Content -Path (Join-Path $stage 'Tiktuv.cmd') -Value $launcher -Encoding ascii
Set-Content -Path (Join-Path $stage 'Tiktuv (Console).cmd') -Value $console -Encoding ascii

$readme = @"
Tiktuv - portable
=================

Double-click 'Tiktuv.cmd'. Nothing is installed: no setup, no registry entries,
no files outside this folder. To remove Tiktuv, delete the folder.

The folder may be copied to a USB stick or another PC and run from any path or
drive letter.

Where things are kept
---------------------
  data\        your jobs, transcripts, exports, logs, temporary audio
  models\      the speech model
  tools\       FFmpeg
  python\      the interpreter and libraries
  app\         Tiktuv itself

Requirements on the host machine
--------------------------------
  Windows 10 or 11, 64-bit.
  The WebView2 runtime, which ships with Windows 11 and with Microsoft Edge on
  Windows 10. If the window does not open, Tiktuv falls back to your default
  browser and still works.

Notes
-----
  Transcription is entirely local. The only outbound request Tiktuv ever makes
  is downloading the speech model, and importing a Zoom link if you use one.

  Running from a USB stick works, but the model is over 6 GB and is read on
  every start, so USB 3.0 or an SSD makes a large difference.

  If the window does not appear, run 'Tiktuv (Console).cmd' to see the log.
"@
Set-Content -Path (Join-Path $stage 'README.txt') -Value $readme -Encoding utf8

# -- 7. verify ---------------------------------------------------------------
Write-Step 'Verifying the build'

$checkScript = @'
"""Runs with the launcher's environment and no sys.path help of its own:
this has to fail if the interpreter cannot find the application by itself."""
import os, sys
import tiktuv
from tiktuv.config import Settings
from tiktuv.api.static_files import find_frontend_dist
from tiktuv.media.ffmpeg import locate_tools
import torch, transformers
s = Settings()
assert s.data_dir == __import__("pathlib").Path(os.environ["TIKTUV_DATA_DIR"]), s.data_dir
tools = locate_tools(s)
print("python      ", sys.version.split()[0])
print("torch       ", torch.__version__)
print("transformers", transformers.__version__)
print("ffmpeg      ", tools.version)
print("frontend    ", find_frontend_dist())
print("data dir    ", s.data_dir)
print("model cache ", s.model_cache_dir)
print("app from    ", os.path.dirname(tiktuv.__file__))
'@
$checkPath = Join-Path $stage '_verify.py'
Set-Content -Path $checkPath -Value $checkScript -Encoding utf8

$env:ROOT = "$stage\"
$env:TIKTUV_DATA_DIR = Join-Path $stage 'data'
$env:TIKTUV_MODELS_DIR = $modelsDir
$env:TIKTUV_FFMPEG_PATH = Join-Path $tools 'ffmpeg.exe'
$env:TIKTUV_FFPROBE_PATH = Join-Path $tools 'ffprobe.exe'
& $portablePython $checkPath
$ok = $LASTEXITCODE -eq 0
Remove-Item $checkPath -Force
if (-not $ok) { throw 'the portable build does not import correctly' }

$size = (Get-ChildItem $stage -Recurse -File | Measure-Object -Property Length -Sum).Sum

if ($Zip) {
    Write-Step 'Creating the ZIP'
    $zipOut = "$stage.zip"
    if (Test-Path $zipOut) { Remove-Item $zipOut -Force }
    Compress-Archive -Path "$stage\*" -DestinationPath $zipOut
    Write-Note ("{0}  ({1:N1} GB)" -f $zipOut, ((Get-Item $zipOut).Length / 1GB))
}

Write-Host ""
Write-Host "  Portable build ready:" -ForegroundColor Green
Write-Host ("    {0}  ({1:N1} GB)" -f $stage, ($size / 1GB))
Write-Host ""
Write-Host "  Copy the folder anywhere and double-click 'Tiktuv.cmd'."
if (-not $IncludeModel) {
    Write-Host "  The model is not bundled: the first run downloads it (6.2 GB)."
}
Write-Host ""
