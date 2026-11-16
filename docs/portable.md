# Portable build

A portable build is a single folder that runs Tiktuv on any Windows 10/11
machine with nothing installed: no setup program, no registry entries, no
Python on the host, and no files written outside the folder. Copy it to a USB
stick or another PC, double-click `Tiktuv.cmd`, and delete the folder to
uninstall.

## Building one

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-portable.ps1 -IncludeModel -Zip
```

| Switch | Effect |
|---|---|
| *(none)* | ~1.9 GB folder; the model downloads on first run |
| `-IncludeModel` | Copies the 6.2 GB weights in, so the first run needs no network |
| `-Cuda` | CUDA build of PyTorch instead of CPU — bigger, and only helps on a recent NVIDIA GPU |
| `-Destination <path>` | Where to build (default: your desktop) |
| `-Zip` | Also produce a `.zip` beside the folder |

The frontend must already be built (`npm --prefix apps/frontend run build`); the
script copies `dist/` rather than building it.

## What is inside

```
Tiktuv-Portable\
    Tiktuv.cmd              launch
    Tiktuv (Console).cmd    launch with the log visible, for diagnosing
    README.txt
    python\                 the interpreter and every library
    app\apps\backend\       Tiktuv itself
    app\apps\frontend\dist\ the built UI
    tools\                  ffmpeg.exe, ffprobe.exe
    models\                 the speech model
    data\                   created on first run
```

## Why it is path-independent

The ordinary installer builds a virtual environment, and a venv is **not**
relocatable: `pyvenv.cfg` and every `Scripts\*.exe` shim record the absolute
path it was created at. Move the folder, or plug the stick into a machine that
assigns a different drive letter, and it breaks.

The portable build uses CPython's **embeddable distribution** instead — a
plain interpreter with no installer, no registry keys and no `%APPDATA%`
presence, which runs correctly from any path.

Two details make that work:

- **`python\python312._pth` lists the search path**, including
  `..\app\apps\backend`. Entries are resolved relative to the folder holding
  `python.exe`, so they follow the folder wherever it goes.
- **`PYTHONPATH` is deliberately not used.** When a `._pth` file is present the
  interpreter *ignores* `PYTHONPATH` entirely and builds `sys.path` from that
  file alone. Setting it looks like it should work and silently does nothing.

The launcher derives every location from `%~dp0` — the folder the `.cmd` is
sitting in, resolved at run time — and passes them in as `TIKTUV_DATA_DIR`,
`TIKTUV_MODELS_DIR`, `TIKTUV_FFMPEG_PATH` and `TIKTUV_FFPROBE_PATH`. No
absolute path is ever written into the build.

## Requirements on the host machine

- Windows 10 or 11, 64-bit.
- The **WebView2 runtime**, which ships with Windows 11 and with Microsoft Edge
  on Windows 10. Without it Tiktuv falls back to opening in the default
  browser, which still works — you just lose the dedicated window and icon.

Nothing else. Python, FFmpeg and the model all travel in the folder.

## Running from a USB stick

It works, but the model is over 6 GB and is read on every start, so the stick's
speed dominates start-up time. USB 3.0 or an external SSD makes a large
difference; USB 2.0 is painful. The transcription itself is CPU-bound and is
unaffected.

Writes go to `data\` on the stick — including temporary decoded audio, which
can be large for long recordings. Leave a few GB free.

## Verified

Built, then run from a different drive letter than it was built at (via
`subst`), on Windows 11:

- the app started, cleared a stale lock left by an earlier crash, and served
  the UI over HTTP 200 with its session token injected
- FFmpeg resolved to the bundled copy (`8.1.2-full_build`)
- the database, logs, media, temp, transcripts, exports and the WebView2
  profile were all created **inside** the folder
- the window opened with the Tiktuv icon
- the model download started into the folder's own `models\`

Not verified: running on a machine that has never had Python, Tiktuv or the
WebView2 runtime installed. Everything is bundled and no host state is read,
but that is reasoning, not a test — try it on the target machine before relying
on it.
