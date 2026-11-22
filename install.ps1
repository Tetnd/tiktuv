<#
.SYNOPSIS
    Install Tiktuv on this computer. Run this once; it does everything.

.DESCRIPTION
    Intended for someone who was handed the Tiktuv folder and just wants the
    program. From a clean Windows 10/11 machine it will:

      1. install Python 3.12, Rust and FFmpeg if they are missing (winget),
      2. create an isolated Python environment for Tiktuv,
      3. install PyTorch — the CUDA build if an NVIDIA GPU is present,
      4. compile the desktop application (Rust, release build),
      5. put a Tiktuv icon on the Desktop,
      6. optionally download the Hebrew model (about 6.2 GB).

    Safe to re-run: every step is skipped if it is already done.

.PARAMETER DownloadModel
    Download the Hebrew model now instead of on first launch.

.PARAMETER SkipModelPrompt
    Do not ask about the model; it downloads by itself on first use.

.PARAMETER Cpu
    Force the CPU build of PyTorch even if an NVIDIA GPU is detected.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -DownloadModel
#>
[CmdletBinding()]
param(
    [switch]$DownloadModel,
    [switch]$SkipModelPrompt,
    [switch]$Cpu
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
Set-Location $repoRoot

$script:step = 0
function Write-Step($message) {
    $script:step++
    Write-Host ""
    Write-Host "[$script:step] $message" -ForegroundColor Cyan
}
function Write-Ok($message) { Write-Host "    $message" -ForegroundColor Green }
function Write-Info($message) { Write-Host "    $message" -ForegroundColor DarkGray }

<#
    winget appends to PATH, but a process that is already running keeps the
    PATH it started with. Without this refresh, installing Python and then
    calling it in the same session fails with "not found" — the single most
    confusing failure in this whole script.
#>
function Update-PathFromRegistry {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ($machine, $user | Where-Object { $_ }) -join ';'
}

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

<#
    Run a python snippet and report only whether it succeeded.

    Written carefully: in Windows PowerShell 5.1 a native command that writes to
    stderr raises NativeCommandError under $ErrorActionPreference='Stop'. An
    import check for a *missing* module always writes a traceback, so the naive
    `python -c "import torch" 2>$null` aborts the whole installer.
#>
function Test-PythonSnippet($python, $snippet) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $python -c $snippet 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Install-WithWinget($id, $friendlyName, $probeCommand) {
    if (Test-Command $probeCommand) {
        Write-Ok "$friendlyName already installed"
        return $true
    }
    if (-not (Test-Command 'winget')) {
        Write-Warning "$friendlyName is missing and winget is unavailable. Install $friendlyName manually, then re-run this script."
        return $false
    }

    Write-Info "installing $friendlyName ..."
    winget install --id $id -e --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 |
        Out-String | Write-Verbose
    Update-PathFromRegistry

    if (Test-Command $probeCommand) {
        Write-Ok "$friendlyName installed"
        return $true
    }
    Write-Warning "$friendlyName did not become available. Close this window, open a NEW terminal, and re-run this script."
    return $false
}

Write-Host ""
Write-Host "  Tiktuv - local Hebrew transcription" -ForegroundColor White
Write-Host "  Installing into: $repoRoot" -ForegroundColor DarkGray
Write-Host "  ------------------------------------------------------------"

Update-PathFromRegistry

# -- 1. prerequisites ------------------------------------------------------
Write-Step "Checking prerequisites"

$osVersion = [Environment]::OSVersion.Version
if ($osVersion.Major -lt 10) {
    throw "Tiktuv requires Windows 10 or newer (found $osVersion)."
}
Write-Ok "Windows $($osVersion.Major) build $($osVersion.Build)"

$okPython = Install-WithWinget 'Python.Python.3.12' 'Python 3.12' 'python'
# Rustup installs the toolchain itself on first run, so `cargo` is the probe.
$okRust = Install-WithWinget 'Rustlang.Rustup' 'Rust' 'cargo'
$okFfmpeg = Install-WithWinget 'Gyan.FFmpeg' 'FFmpeg' 'ffmpeg'

if (-not $okPython -or -not $okRust) {
    throw "Cannot continue without Python and Rust."
}
if (-not $okFfmpeg) {
    Write-Warning "FFmpeg is missing. Tiktuv will start but cannot read media until it is installed."
}

$pythonVersion = (python --version 2>&1) -replace 'Python\s+', ''
if ([version]($pythonVersion -split '\+')[0] -lt [version]'3.12.0') {
    throw "Python 3.12 or newer is required; found $pythonVersion. Install it and re-run."
}
Write-Ok "Python $pythonVersion, $(cargo --version)"

# -- 2. python environment -------------------------------------------------
Write-Step "Creating the Tiktuv environment"
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "could not create the virtual environment" }
    Write-Ok "environment created"
} else {
    Write-Ok "environment already exists"
}
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "pip could not be upgraded" }

# -- 3. PyTorch ------------------------------------------------------------
Write-Step "Installing the inference engine"
$hasNvidia = $false
if (-not $Cpu) {
    try {
        $gpu = Get-CimInstance Win32_VideoController -ErrorAction Stop |
            Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1
        if ($gpu) { $hasNvidia = $true; Write-Info "detected $($gpu.Name)" }
    } catch { $hasNvidia = $false }
}

$cpuTorch = @{ Spec = 'torch==2.5.1'; Index = 'https://download.pytorch.org/whl/cpu' }
# CUDA 12.8 wheels cover everything from Turing to Blackwell (RTX 50-series).
# Older CUDA lines do not build for Blackwell at all: torch installs happily
# and then fails at run time with "no kernel image is available".
$gpuTorch = @{ Spec = 'torch==2.8.0'; Index = 'https://download.pytorch.org/whl/cu128' }

if (Test-PythonSnippet $venvPython 'import torch') {
    $existing = (& $venvPython -c "import torch; print(torch.__version__)").Trim()
    Write-Ok "PyTorch already installed ($existing)"
} else {
    $choice = if ($hasNvidia) { $gpuTorch } else { $cpuTorch }
    if ($hasNvidia) {
        Write-Info "installing the CUDA build - this download is about 2.5 GB"
    } else {
        Write-Info "no NVIDIA GPU detected - installing the CPU build (about 200 MB)"
    }
    & $venvPython -m pip install $choice.Spec --index-url $choice.Index
    if ($LASTEXITCODE -ne 0) { throw "PyTorch installation failed" }
    Write-Ok "PyTorch installed"
}

# Trust nothing: actually run a CUDA operation. A GPU too old or too new for
# the wheel reports is_available() = True and then fails on first use, which
# would surface as a broken transcription rather than a setup problem.
if ($hasNvidia) {
    $probe = 'import torch,sys; sys.exit(0 if torch.cuda.is_available() and (torch.ones(8,device="cuda")*2).sum().item()==16 else 1)'
    if (Test-PythonSnippet $venvPython $probe) {
        $name = (& $venvPython -c "import torch; print(torch.cuda.get_device_name(0))").Trim()
        Write-Ok "GPU acceleration working: $name"
    } else {
        Write-Warning "the GPU could not be used with this PyTorch build - falling back to the CPU build"
        & $venvPython -m pip uninstall -y torch --quiet
        & $venvPython -m pip install $cpuTorch.Spec --index-url $cpuTorch.Index
        if ($LASTEXITCODE -ne 0) { throw "CPU PyTorch installation failed" }
        Write-Ok "CPU build installed - transcription will be slower but correct"
    }
}

# -- 4. Tiktuv itself ------------------------------------------------------
Write-Step "Installing Tiktuv"
& $venvPython -m pip install -e "apps/backend[asr]" --quiet
if ($LASTEXITCODE -ne 0) { throw "Tiktuv installation failed" }
& $venvPython -m pip install pillow --quiet
Write-Ok "backend installed"

# -- 5. the application ----------------------------------------------------
Write-Step "Building the application"
$appExe = Join-Path $repoRoot 'apps\desktop-gpui\target\release\tiktuv-desktop.exe'
if (Test-Path $appExe) {
    Write-Ok "application already built"
} else {
    # The first build compiles the GUI stack from source and takes a while.
    Write-Info "this takes 10-20 minutes the first time"
    cargo build --release --manifest-path 'apps/desktop-gpui/Cargo.toml'
    if ($LASTEXITCODE -ne 0) { throw "the application build failed" }
    if (-not (Test-Path $appExe)) { throw "the build produced no executable at $appExe" }
    Write-Ok "application built"
}

# -- 6. shortcut -----------------------------------------------------------
Write-Step "Creating the Desktop shortcut"
# The icon is generated from a font glyph rather than committed as a binary.
$iconPath = Join-Path $repoRoot 'apps\desktop-gpui\assets\icon.ico'
if (-not (Test-Path $iconPath)) {
    & $venvPython (Join-Path $repoRoot 'scripts\make-icon.py') | Out-Null
}

<#
    The shortcut points straight at the compiled exe. Its working directory is
    the repository, which is how the app finds .venv and starts the backend
    from a source checkout (see apps/desktop-gpui/src/backend.rs).
#>
function New-TiktuvShortcut([string]$path) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $appExe
    $shortcut.WorkingDirectory = $repoRoot
    if (Test-Path $iconPath) { $shortcut.IconLocation = "$iconPath,0" }
    $shortcut.Description = 'Tiktuv - local Hebrew transcription'
    $shortcut.WindowStyle = 1
    $shortcut.Save()
}

New-TiktuvShortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Tiktuv.lnk')
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
if (Test-Path $startMenu) { New-TiktuvShortcut (Join-Path $startMenu 'Tiktuv.lnk') }
Write-Ok "shortcut created"

# -- 7. the model ----------------------------------------------------------
Write-Step "Hebrew transcription model"
$modelId = 'ivrit-ai/whisper-large-v3'
$modelsDir = Join-Path $env:LOCALAPPDATA 'Tiktuv\models'
$modelPresent = Test-Path (Join-Path $modelsDir "models--$($modelId.Replace('/','--'))\snapshots")

if ($modelPresent) {
    Write-Ok "model already downloaded"
} else {
    $fetch = $DownloadModel
    if (-not $fetch -and -not $SkipModelPrompt) {
        Write-Host ""
        Write-Host "    Tiktuv needs the Hebrew model to transcribe." -ForegroundColor White
        Write-Host "      Size:     about 6.2 GB"
        Write-Host "      Saved to: $modelsDir"
        Write-Host "      This is the only step that uses the internet."
        Write-Host ""
        Write-Host "    It downloads by itself the first time you open Tiktuv." -ForegroundColor DarkGray
        $answer = Read-Host "    Download it now instead? [y/N]"
        $fetch = ($answer -match '^[Yy]')
    }
    if ($fetch) {
        $env:TIKTUV_MODELS_DIR = $modelsDir
        & $venvPython -m tiktuv.fetch_model $modelId
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "the download did not finish - Tiktuv will retry automatically on first use"
        } else {
            Write-Ok "model ready"
        }
    } else {
        Write-Info "the model will download automatically on first use"
    }
}

# -- done ------------------------------------------------------------------
Write-Host ""
Write-Host "  ------------------------------------------------------------"
Write-Host "  Tiktuv is installed." -ForegroundColor Green
Write-Host ""
Write-Host "  Double-click the Tiktuv icon on your Desktop to start." -ForegroundColor White
if (-not $okFfmpeg) {
    Write-Host ""
    Write-Warning "  FFmpeg is still missing. Install it with:  winget install Gyan.FFmpeg"
    Write-Host "  Then restart Tiktuv." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  Everything runs on this computer. No recording is ever uploaded." -ForegroundColor DarkGray
Write-Host ""
