# Windows developer setup

## Prerequisites

```bash
winget install Python.Python.3.12
```

```bash
winget install Gyan.FFmpeg
```

```bash
winget install Git.Git
```

```bash
winget install Rustlang.Rustup
```

**Open a new terminal after installing.** winget updates `PATH`, and processes
that were already running do not see the change.

Rust builds the application itself, so it is required, not optional. Python
runs the transcription backend the application talks to.

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

## Running

```bash
cargo run --manifest-path apps/desktop-gpui/Cargo.toml
```

That is the whole loop: the app walks up from the working directory until it
finds `.venv`, starts `python -m tiktuv` as a child process, and shuts it down
again when the window closes.

Without any model download — the deterministic mock backend, which is ideal for
UI work:

```bash
$env:TIKTUV_ASR_BACKEND = 'mock'
```

To watch the backend's log, or to work on the API on its own, start it by hand
first; the app attaches to a backend that is already running rather than
spawning a second one:

```bash
.venv\Scripts\python.exe -m tiktuv --log-level DEBUG
```

The backend prints `TIKTUV_READY <path>` on stdout; that file holds the live
port and the session token.

With `TIKTUV_LOG_LEVEL=DEBUG` the OpenAPI docs are served at
<http://127.0.0.1:8756/api/docs>. They are disabled otherwise, because they
would be an unauthenticated surface on a port every local process can reach.

## Release build

```bash
scripts\build-rust.ps1 -Zip
```

In development the app locates the repository's `.venv` and runs
`python -m tiktuv`. In a packaged build it runs the frozen `tiktuv-backend.exe`
next to the executable — see [portable.md](portable.md).

## Tests

```bash
.venv\Scripts\python.exe -m pytest apps/backend/tests -v
```

```bash
cargo test --manifest-path apps/desktop-gpui/Cargo.toml
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

Backend: ruff plus mypy in strict mode.

The application uses rustfmt and clippy, neither of which is installed by
default with the minimal rustup profile:

```bash
rustup component add rustfmt clippy
```

```bash
cargo fmt --manifest-path apps/desktop-gpui/Cargo.toml --check
```

```bash
cargo clippy --manifest-path apps/desktop-gpui/Cargo.toml
```
