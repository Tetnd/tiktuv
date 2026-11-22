# Portable build

A portable build is a single folder that runs Tiktuv on any Windows 10/11
machine with nothing installed: no setup program, no registry entries, no
Python on the host, and no files written outside the folder. Copy it to a USB
stick or another PC, double-click `Tiktuv.exe`, and delete the folder to
uninstall.

## Building one

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-rust.ps1 -Zip
```

| Switch | Effect |
|---|---|
| *(none)* | Builds into `build\Tiktuv-Portable`; the model downloads on first run |
| `-Destination <path>` | Where to build (default: `build\Tiktuv-Portable`) |
| `-SkipBackendFreeze` | Reuse the frozen backend from a previous run |
| `-SkipRustBuild` | Reuse `target\release\tiktuv-desktop.exe` from a previous run |
| `-Zip` | Also produce a `.zip` beside the folder |

It needs the Rust toolchain, the developer virtualenv (`scripts\setup.ps1`),
and FFmpeg — either on `PATH` or downloaded automatically.

## What is inside

```
Tiktuv-Portable\
    Tiktuv.exe          the app — double-click this
    tiktuv-backend.exe  the transcription engine, started by the app
    README.txt
    tools\              ffmpeg.exe, ffprobe.exe
    data\               created on first run
    models\             the speech model, downloaded on first use
```

## Why it is path-independent

Two separate executables, neither of which records where it was built:

- **The app** resolves everything from the folder holding `Tiktuv.exe`,
  computed at run time from its own path. When it finds `tiktuv-backend.exe`
  beside itself, it treats that folder as the application root and keeps
  `data\` and `models\` inside it — see `portable_data_dir` in
  `apps/desktop-gpui/src/backend.rs`.
- **The backend** is frozen by PyInstaller into one executable: an interpreter
  and every library in a single file that unpacks to a temporary directory at
  start-up. There is no virtualenv, and so none of the absolute paths a venv
  bakes into `pyvenv.cfg` and its `Scripts\*.exe` shims — which is exactly what
  stops an ordinary installed copy from being moved to another drive letter.

The app passes the folder's locations to the backend as `TIKTUV_DATA_DIR`,
`TIKTUV_MODELS_DIR`, `TIKTUV_FFMPEG_PATH` and `TIKTUV_FFPROBE_PATH`, so no
absolute path is ever written into the build.

## Requirements on the host machine

Windows 10 or 11, 64-bit. Nothing else: Python, FFmpeg and the model all travel
in the folder, and the app draws its own window rather than using a browser
engine, so no WebView2 runtime is involved.

## Running from a USB stick

It works, but the model is over 6 GB and is read on every start, so the stick's
speed dominates start-up time. USB 3.0 or an external SSD makes a large
difference; USB 2.0 is painful. The transcription itself is CPU-bound and is
unaffected.

Writes go to `data\` on the stick — including temporary decoded audio, which
can be large for long recordings. Leave a few GB free.

## Before you rely on it

Build it, then run it from a different drive letter than it was built at (via
`subst`) and check that:

- the app starts and the backend comes up with it,
- FFmpeg resolves to the bundled copy rather than one on the host,
- `data\`, `models\`, logs and exports are all created **inside** the folder,
- the model download starts into the folder's own `models\`.

The same run on a machine that has never had Python or Tiktuv installed is the
one that actually proves the "nothing installed" claim.
