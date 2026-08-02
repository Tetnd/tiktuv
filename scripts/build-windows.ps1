<#
.SYNOPSIS
    Build the Windows installers (MSI + NSIS).

.DESCRIPTION
    Freezes the Python backend into a single executable with PyInstaller,
    builds the frontend, and packages both with Tauri.

    Prerequisites, checked below:
      * Rust stable toolchain (https://rustup.rs)
      * Microsoft Visual C++ Build Tools
      * WebView2 runtime (present on Windows 11; Tauri bootstraps it on 10)

.PARAMETER SkipBackendFreeze
    Reuse an existing frozen backend in apps/desktop/src-tauri/binaries.
#>
[CmdletBinding()]
param([switch]$SkipBackendFreeze)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) { throw "Run scripts\setup.ps1 first." }

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Rust was not found. Install it from https://rustup.rs and open a new terminal."
}

# -- 1. freeze the backend -------------------------------------------------
$binariesDir = Join-Path $repoRoot 'apps\desktop\src-tauri\binaries'
New-Item -ItemType Directory -Force -Path $binariesDir | Out-Null

if (-not $SkipBackendFreeze) {
    Write-Step "Freezing the Python backend with PyInstaller"
    & $venvPython -m pip install --quiet 'pyinstaller==6.11.1'
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed" }

    & $venvPython -m PyInstaller `
        --noconfirm --clean --onefile `
        --name tiktuv-backend `
        --distpath $binariesDir `
        --workpath (Join-Path $repoRoot 'build\pyinstaller') `
        --specpath (Join-Path $repoRoot 'build') `
        --collect-all tiktuv `
        --collect-data reportlab `
        --collect-data docx `
        --hidden-import uvicorn.logging `
        --hidden-import uvicorn.loops.auto `
        --hidden-import uvicorn.protocols.http.auto `
        --hidden-import uvicorn.lifespan.on `
        (Join-Path $repoRoot 'apps\backend\tiktuv\__main__.py')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
} else {
    Write-Step "Reusing the existing frozen backend"
}

$frozen = Join-Path $binariesDir 'tiktuv-backend.exe'
if (-not (Test-Path $frozen)) { throw "Frozen backend not found at $frozen" }
Write-Host "    $([math]::Round((Get-Item $frozen).Length / 1MB, 1)) MB"

# -- 2. frontend -----------------------------------------------------------
Write-Step "Building the frontend"
npm --prefix apps/frontend install
npm --prefix apps/frontend run build
if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }

# -- 3. installers ---------------------------------------------------------
Write-Step "Building the Windows installers"
npm --prefix apps/desktop install
npm --prefix apps/desktop run tauri build
if ($LASTEXITCODE -ne 0) { throw "Tauri build failed" }

$bundleDir = Join-Path $repoRoot 'apps\desktop\src-tauri\target\release\bundle'
Write-Host ""
Write-Host "Installers written to:" -ForegroundColor Green
Get-ChildItem -Path $bundleDir -Recurse -Include *.msi, *.exe -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host "    $($_.FullName)" }
