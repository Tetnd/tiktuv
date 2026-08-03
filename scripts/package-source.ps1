<#
.SYNOPSIS
    Build the Tiktuv package you hand to someone else.

.DESCRIPTION
    Produces a ZIP containing everything needed to install Tiktuv on another
    Windows machine, and nothing else. Deliberately excluded:

      * .venv and node_modules  - multi-gigabyte, and both bake in absolute
                                  paths, so copying them to another machine
                                  produces a subtly broken install
      * apps\desktop\src-tauri	arget - Rust build output
      * the model cache         - 6.2 GB, and the installer fetches it
      * data, logs, transcripts - the previous user's recordings must never
                                  travel with the program
      * .git                    - history, branches and remotes

    The recipient unzips it and double-clicks "Install Tiktuv.cmd".

.PARAMETER Destination
    Where to write the ZIP. Defaults to the Desktop.

.PARAMETER IncludeBuiltUI
    Include the prebuilt interface, so the recipient does not need to wait for
    (or succeed at) an npm build. Adds a few hundred KB. Recommended.

.EXAMPLE
    scripts\package-source.ps1 -IncludeBuiltUI
#>
[CmdletBinding()]
param(
    [string]$Destination = [Environment]::GetFolderPath('Desktop'),
    [switch]$IncludeBuiltUI
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

# Read the version from the backend package so the ZIP name matches the build.
$initFile = Join-Path $repoRoot 'apps\backend\tiktuv\__init__.py'
$version = '0.0.0'
if (Test-Path $initFile) {
    $match = Select-String -Path $initFile -Pattern '__version__\s*=\s*"([^"]+)"' |
        Select-Object -First 1
    if ($match) { $version = $match.Matches[0].Groups[1].Value }
}

$stageName = "Tiktuv-$version"
$stageDir = Join-Path ([System.IO.Path]::GetTempPath()) "tiktuv-package-$(Get-Random)\$stageName"
$zipPath = Join-Path $Destination "$stageName.zip"

Write-Step "Staging $stageName"
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

# What the recipient needs, and nothing more.
$include = @(
    'apps\backend\tiktuv',
    'apps\backend\pyproject.toml',
    'apps\backend\tests',
    'apps\frontend\src',
    'apps\frontend\public',
    'apps\frontend\index.html',
    'apps\frontend\package.json',
    'apps\frontend\package-lock.json',
    'apps\frontend\tsconfig.json',
    'apps\frontend\vite.config.ts',
    'apps\frontend\tailwind.config.js',
    'apps\frontend\postcss.config.js',
    'apps\frontend\.eslintrc.cjs',
    'apps\desktop\src-tauri',
    'apps\desktop\package.json',
    'packages',
    'scripts',
    'docs',
    'samples\README.md',
    'tests',
    'install.ps1',
    'Install Tiktuv.cmd',
    'README.md',
    'LICENSE',
    '.gitignore'
)

foreach ($relative in $include) {
    $source = Join-Path $repoRoot $relative
    if (-not (Test-Path $source)) {
        Write-Warning "skipping missing $relative"
        continue
    }
    $target = Join-Path $stageDir $relative
    $parent = Split-Path -Parent $target
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }

    if (Test-Path $source -PathType Container) {
        Copy-Item $source $target -Recurse -Force
    } else {
        Copy-Item $source $target -Force
    }
}

if ($IncludeBuiltUI) {
    $dist = Join-Path $repoRoot 'apps\frontend\dist'
    if (-not (Test-Path (Join-Path $dist 'index.html'))) {
        Write-Step "Building the interface first"
        npm --prefix apps/frontend install --no-audit --no-fund
        npm --prefix apps/frontend run build
        if ($LASTEXITCODE -ne 0) { throw "interface build failed" }
    }
    Copy-Item $dist (Join-Path $stageDir 'apps\frontend\dist') -Recurse -Force
    Write-Host "    included the prebuilt interface" -ForegroundColor DarkGray
}

# Belt and braces: remove anything that should never ship, even if it slipped
# into one of the directories copied above.
Write-Step "Removing build artefacts and private data"
$purge = @(
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    'node_modules', 'target', 'binaries', '.env.local', '*.egg-info'
)
foreach ($pattern in $purge) {
    Get-ChildItem -Path $stageDir -Filter $pattern -Recurse -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            Write-Host "    removed $($_.FullName.Substring($stageDir.Length + 1))" -ForegroundColor DarkGray
        }
}

# Media files must never travel with the package.
Get-ChildItem -Path $stageDir -Include '*.mp3', '*.mp4', '*.wav', '*.m4a', '*.mkv', '*.mov', '*.webm' `
    -Recurse -Force -ErrorAction SilentlyContinue |
    ForEach-Object {
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
        Write-Warning "removed a media file that would have shipped: $($_.Name)"
    }

# -- a short read-me for whoever receives it -------------------------------
@"
Tiktuv $version - local Hebrew transcription
============================================

TO INSTALL
----------
1. Unzip this folder somewhere permanent, for example C:\Tiktuv
   (Do not run it from inside the ZIP.)
2. Double-click "Install Tiktuv.cmd".
3. Wait. It installs everything it needs and puts a Tiktuv icon on
   your Desktop. This takes several minutes the first time.

If Windows warns about running the file, choose "More info" then
"Run anyway" - the installer is a plain text script you can open and read.

TO USE
------
Double-click the Tiktuv icon on your Desktop, drag in an MP3 or MP4,
and press "התחל תמלול".

The first transcription downloads the Hebrew model (about 6.2 GB).
That is the only time Tiktuv uses the internet.

WHAT IT NEEDS
-------------
Windows 10 or 11, about 15 GB of free disk space, and an internet
connection for the one-time setup and model download.
A graphics card is optional - it is faster with one, and works without.

PRIVACY
-------
Everything runs on your computer. No recording is ever uploaded,
there is no analytics and there are no accounts.
Full details: docs\privacy.md

HELP
----
Something not working? docs\troubleshooting.md
"@ | Set-Content -Path (Join-Path $stageDir 'READ ME FIRST.txt') -Encoding utf8

# -- zip -------------------------------------------------------------------
Write-Step "Creating the ZIP"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path $stageDir -DestinationPath $zipPath -CompressionLevel Optimal

$sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Remove-Item (Split-Path -Parent $stageDir) -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "  Package ready:" -ForegroundColor Green
Write-Host "    $zipPath  ($sizeMb MB)" -ForegroundColor White
Write-Host ""
Write-Host "  Send that file. The recipient unzips it and double-clicks" -ForegroundColor DarkGray
Write-Host "  'Install Tiktuv.cmd'." -ForegroundColor DarkGray
Write-Host ""
