# Windows developer setup

## Prerequisites

```bash
winget install Python.Python.3.12
```

```bash
winget install OpenJS.NodeJS.LTS
```

```bash
winget install Gyan.FFmpeg
```

```bash
winget install Git.Git
```

**Open a new terminal after installing.** winget updates `PATH`, and processes
that were already running do not see the change.

For the desktop shell only:

```bash
winget install Rustlang.Rustup
```

```bash
winget install Microsoft.VisualStudio.2022.BuildTools
```

Select **Desktop development with C++** in the Build Tools installer — Rust's
MSVC toolchain needs that linker.

## Automated setup

```bash
scripts\setup.ps1
```

With a CUDA build of PyTorch:

```bash
scripts\setup.ps1 -Device cuda
```

If PowerShell blocks the script:

```bash
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Manual setup

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install --upgrade pip
```

```bash
.venv\Scripts\python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
```

```bash
.venv\Scripts\python.exe -m pip install -e apps/backend[asr,dev]
```

```bash
npm --prefix apps/frontend install
```

## Running

Both processes at once:

```bash
scripts\dev.ps1
```

Without any model download — the deterministic mock backend, which is ideal for
UI work:

```bash
scripts\dev.ps1 -Mock
```

Separately, if you prefer two terminals:

```bash
.venv\Scripts\python.exe -m tiktuv --log-level DEBUG
```

```bash
npm --prefix apps/frontend run dev
```

The backend prints `TIKTUV_READY <path>` on stdout; that file holds the live
port and the session token. `scripts\dev.ps1` copies the token into
`apps/frontend/.env.local` for you.

With `TIKTUV_LOG_LEVEL=DEBUG` the OpenAPI docs are served at
<http://127.0.0.1:8756/api/docs>. They are disabled otherwise, because they
would be an unauthenticated surface on a port every local process can reach.

## Desktop shell

```bash
npm --prefix apps/desktop install
```

```bash
npm --prefix apps/desktop run tauri dev
```

In development the shell locates the repository's `.venv` and runs
`python -m tiktuv`. In a packaged build it runs the frozen sidecar next to the
executable.

## Tests

```bash
.venv\Scripts\python.exe -m pytest apps/backend/tests -v
```

```bash
npm --prefix apps/frontend run test
```

```bash
npm --prefix apps/frontend run check
```

The backend suite uses real FFmpeg with the mock ASR backend, so it needs no
GPU, no model and no network.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TIKTUV_PORT` | `8756` | Backend port |
| `TIKTUV_DATA_DIR` | `%LOCALAPPDATA%\Tiktuv` | All application data |
| `TIKTUV_MODELS_DIR` | `<data>/models` | Model cache |
| `TIKTUV_FFMPEG_PATH` | auto | Explicit path to `ffmpeg` |
| `TIKTUV_FFPROBE_PATH` | auto | Explicit path to `ffprobe` |
| `TIKTUV_LOG_LEVEL` | `INFO` | `DEBUG` also enables `/api/docs` |
| `TIKTUV_LOG_JSON` | `1` | `0` for readable text logs |
| `TIKTUV_ASR_BACKEND` | — | `mock` selects the deterministic test backend |
| `TIKTUV_MAX_CONCURRENT_JOBS` | `1` | Raise only with VRAM to spare |
| `TIKTUV_WINDOW_SECONDS` | `300` | Outer processing window |
| `TIKTUV_WINDOW_OVERLAP_SECONDS` | `5` | Overlap used to repair boundaries |

`TIKTUV_HOST` exists but accepts only loopback values. Anything else is
rejected at startup, by design.

## Code style

```bash
.venv\Scripts\python.exe -m ruff check apps/backend
```

```bash
.venv\Scripts\python.exe -m mypy apps/backend/tiktuv
```

Backend: ruff plus mypy in strict mode. Frontend: ESLint plus `tsc --noEmit`,
which `npm run check` runs together.
