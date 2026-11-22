<#
.SYNOPSIS
    One-time developer setup for Tiktuv on Windows.

.DESCRIPTION
    Creates the Python virtualenv, installs PyTorch for the chosen device,
    installs the backend, and runs the environment doctor. Safe to re-run.

    The desktop app itself is Rust: build it with `cargo run` (debug) or
    scripts\build-rust.ps1 (release, packaged). Rust is not installed here —
    get it from https://rustup.rs if `cargo` is missing.

.PARAMETER Device
    'cpu' (default) or 'cuda'. Chooses which PyTorch wheel index to use.

.PARAMETER SkipAsr
    Skip the multi-gigabyte inference extras. The API, exports and the whole
    test suite still work; transcription does not.

.EXAMPLE
    scripts\setup.ps1 -Device cuda
#>
[CmdletBinding()]
param(
    [ValidateSet('cpu', 'cuda')]
    [string]$Device = 'cpu',
    [switch]$SkipAsr
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Test-Tool($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# -- prerequisites ---------------------------------------------------------
Write-Step "Checking prerequisites"

if (-not (Test-Tool 'python')) {
    throw "Python was not found on PATH. Install Python 3.12+ (winget install Python.Python.3.12) and open a new terminal."
}
$pythonVersion = (python --version) -replace 'Python\s+', ''
Write-Host "    Python $pythonVersion"
if ([version]($pythonVersion -split '\+')[0] -lt [version]'3.12.0') {
    throw "Python 3.12 or newer is required; found $pythonVersion."
}

if (Test-Tool 'cargo') {
    Write-Host "    $(cargo --version)"
} else {
    Write-Warning "Rust was not found on PATH. The backend will work, but the"
    Write-Warning "desktop app cannot be built. Install it from https://rustup.rs"
}

if (Test-Tool 'ffmpeg') {
    Write-Host "    FFmpeg found"
} else {
    Write-Warning "FFmpeg was not found on PATH. Install it with: winget install Gyan.FFmpeg"
    Write-Warning "Then open a NEW terminal so the updated PATH is picked up."
}

# -- virtualenv ------------------------------------------------------------
Write-Step "Creating the Python virtual environment"
if (-not (Test-Path '.venv')) {
    python -m venv .venv
} else {
    Write-Host "    .venv already exists"
}
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'

& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

# -- PyTorch ---------------------------------------------------------------
Write-Step "Installing PyTorch ($Device)"
# CUDA 12.8 covers Turing through Blackwell (RTX 50-series). Older CUDA
# lines do not build for Blackwell, and fail at run time rather than install.
if ($Device -eq 'cuda') {
    & $venvPython -m pip install 'torch==2.8.0' --index-url 'https://download.pytorch.org/whl/cu128'
} else {
    & $venvPython -m pip install 'torch==2.5.1' --index-url 'https://download.pytorch.org/whl/cpu'
}
if ($LASTEXITCODE -ne 0) { throw "PyTorch installation failed" }

# -- backend ---------------------------------------------------------------
Write-Step "Installing the backend"
$extras = if ($SkipAsr) { 'dev' } else { 'asr,dev' }
& $venvPython -m pip install -e "apps/backend[$extras]"
if ($LASTEXITCODE -ne 0) { throw "backend installation failed" }

# -- doctor ----------------------------------------------------------------
Write-Step "Checking the environment"
& $venvPython -m tiktuv.doctor
$doctorExit = $LASTEXITCODE

Write-Host ""
if ($doctorExit -eq 0) {
    Write-Host "Setup complete. Start the app with: cargo run --manifest-path apps/desktop-gpui/Cargo.toml" -ForegroundColor Green
} else {
    Write-Warning "Setup finished, but the environment check reported problems (see above)."
    Write-Host "See docs/troubleshooting.md" -ForegroundColor Yellow
}
exit $doctorExit
