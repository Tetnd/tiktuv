# Tiktuv (תכתוב)

**Local-first Hebrew transcription for MP3 / MP4 and other media.**

Tiktuv transcribes Hebrew audio and video entirely on your own machine using the
Hebrew-focused ASR model [`ivrit-ai/whisper-large-v3`](https://huggingface.co/ivrit-ai/whisper-large-v3).
Nothing is uploaded. No analytics. No accounts.

The model downloads itself on first use — the only step that touches the network,
and it always shows its size and destination first.

- 🇮🇱 Hebrew-first UI and Hebrew-first ASR, with correct RTL rendering everywhere
- 🎬 MP3, MP4, WAV, M4A, MOV, MKV, WEBM — audio extracted with FFmpeg
- 🔗 Import a Zoom cloud recording straight from its share link — see [Zoom recordings](#zoom-recordings)
- ⚡ CUDA GPU / CPU, automatic device detection, FP16, configurable batch size
- 🧩 Streaming chunked processing — multi-hour recordings without loading them into RAM
- ✍️ Built-in RTL transcript editor with a synchronized media player
- ✨ AI proofreading with DeepSeek — every correction stays reviewable: removed words struck through, replacements highlighted, and a one-click jump to the segment
- 📤 Export TXT, timestamped TXT, DOCX, PDF, SRT, VTT, JSON — DOCX/PDF are genuinely RTL
- 💾 Local SQLite job history with crash-safe recovery and partial results

---

## Table of contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Exact commands](#exact-commands)
- [Zoom recordings](#zoom-recordings)
- [Documentation](#documentation)
- [Repository layout](#repository-layout)
- [Status & known limitations](#status--known-limitations)
- [License](#license)

---

## Requirements

| Component | Minimum | Notes |
|---|---|---|
| OS | Windows 10 21H2 / Windows 11 | Linux & macOS are architecturally supported, not yet packaged |
| Python | 3.12+ | The transcription backend; bundled in the portable build |
| Rust | stable | Builds the desktop application itself |
| FFmpeg | 6.0+ | `ffmpeg` + `ffprobe` on `PATH`, or set `TIKTUV_FFMPEG_PATH` |
| Disk | ~15 GB free | `ivrit-ai/whisper-large-v3` is ≈6.2 GB to download and on disk — it ships FP32 weights in two shards, twice the size of OpenAI's FP16 upload |
| RAM | 8 GB (CPU) | 16 GB recommended |
| VRAM | 6 GB (FP16 GPU) | CPU inference works without a GPU, just slower |

> Tiktuv never downloads the model without telling you the size and the exact
> folder first. See [docs/privacy.md](docs/privacy.md).

---

## Quick start

### Installing it (including on someone else's machine)

Double-click **`Install Tiktuv.cmd`**, or from PowerShell:

```bash
.\install.ps1
```

From a clean Windows 10/11 machine that installs Python, Rust and FFmpeg if
they are missing, sets up an isolated environment, picks the right PyTorch
build for the hardware, compiles the application, and puts a **Tiktuv** icon on
the Desktop. Re-running it is safe — finished steps are skipped. The first
build of the GUI stack takes 10-20 minutes.

### Running it from a folder, with nothing installed

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-rust.ps1 -Zip
```

Produces a self-contained folder — `Tiktuv.exe` plus a frozen backend — that
runs on any Windows 10/11 machine with no setup, no Python and no registry
entries, from any path or drive letter, a USB stick included. See
[docs/portable.md](docs/portable.md).

### Giving it to someone

```bash
scripts\package-source.ps1
```

Produces `Tiktuv-<version>.zip` on your Desktop. It deliberately excludes
`.venv`, Rust build output, the model cache and any recordings — those are
either machine-specific or private. The recipient unzips it, double-clicks
`Install Tiktuv.cmd`, and gets the same result.

### Developing on it

```bash
scripts\setup.ps1
```

```bash
cargo run --manifest-path apps/desktop-gpui/Cargo.toml
```

`setup.ps1` prepares the Python side. The app starts the backend itself: in a
source checkout it walks up from the working directory until it finds `.venv`
and runs `python -m tiktuv`, so there is no second command to keep running.

---

## Exact commands

### 1. Install dependencies

Backend (from the repository root):

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install --upgrade pip
```

CPU-only PyTorch:

```bash
.venv\Scripts\python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
```

or CUDA 12.1 PyTorch:

```bash
.venv\Scripts\python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121
```

Then the backend itself. `asr` pulls in Transformers for real transcription;
`dev` pulls in the test and lint tooling:

```bash
.venv\Scripts\python.exe -m pip install -e apps/backend[asr,dev]
```

To work on the API, exports or tests without the multi-gigabyte ML stack,
install `[dev]` alone and run with `TIKTUV_ASR_BACKEND=mock`.

### 2. Check the environment

```bash
.venv\Scripts\python.exe -m tiktuv.doctor
```

### 3. Run the desktop application

```bash
cargo run --manifest-path apps/desktop-gpui/Cargo.toml
```

The app spawns the backend as a child process, waits for its
`TIKTUV_READY <path>` handshake, and reads the port and session token from that
file. Closing the window kills the child, so a stray backend can never outlive
it.

### 4. Start the backend on its own

Only needed to work on the API directly — the app does this for you:

```bash
.venv\Scripts\python.exe -m tiktuv --host 127.0.0.1 --port 8756
```

### 6. Run tests

Backend (fast, model fully mocked — no download, no GPU):

```bash
.venv\Scripts\python.exe -m pytest apps/backend/tests -v
```

Desktop application:

```bash
cargo test --manifest-path apps/desktop-gpui/Cargo.toml
```

Backend lint and type-check:

```bash
.venv\Scripts\python.exe -m ruff check apps/backend
```

```bash
.venv\Scripts\python.exe -m mypy --config-file apps/backend/pyproject.toml apps/backend/tiktuv
```

End-to-end, against a real backend process (starts the server, transcribes a
Hebrew-named MP3 and an MP4, edits, exports all six formats, checks the
security boundaries):

```bash
scripts\smoke-test.ps1
```

The optional real-model integration test (downloads ≈3.1 GB, needs FFmpeg and a
real Hebrew recording, takes several minutes) is opt-in:

```bash
$env:TIKTUV_RUN_REAL_MODEL_TEST=1
```

```bash
.venv\Scripts\python.exe -m pytest apps/backend/tests/integration -v -s
```

### 7. Build a distributable

```bash
scripts\build-rust.ps1 -Zip
```

Freezes the backend with PyInstaller, compiles the app in release mode, and
assembles `build\Tiktuv-Portable\` (plus a `.zip`) — a folder that runs
anywhere with nothing installed. See [docs/portable.md](docs/portable.md).

---

## Zoom recordings

Under **בחירת קבצים** in the upload area there is a second button, **תמלול
מקישור Zoom**. Paste a cloud-recording share link (`/rec/share/…` or
`/rec/play/…`), and Tiktuv resolves it, downloads the MP4 to your media folder
with a live progress bar, and queues it for transcription like any dropped file.

- The download runs **directly between your machine and Zoom**. The link is not
  sent anywhere else, and the recording is transcribed locally as usual.
- Only `zoom.us` hostnames on a recording path are accepted; anything else is
  rejected before a request is made.
- Passcode-protected links show a passcode field. **This path is untested** —
  no protected link was available to verify against.
- The file is named after the meeting topic when Zoom exposes one. Anonymous
  share links carry no topic, so those are named by their recording start time
  (`Zoom 2026-05-28 14-03.mp4`).
- **Only import recordings you are allowed to have.** Tiktuv fetches the same
  stream the Zoom player uses, so it also works on recordings whose owner
  disabled the download button. That is a permission you should have from the
  owner, not one the tool grants you.

---

## Documentation

| Document | Contents |
|---|---|
| [docs/installation.md](docs/installation.md) | End-user install, first run, model download |
| [docs/portable.md](docs/portable.md) | The no-install portable folder, and how it stays path-independent |
| [docs/windows-dev-setup.md](docs/windows-dev-setup.md) | Developer setup on Windows 10/11 |
| [docs/gpu-cuda-setup.md](docs/gpu-cuda-setup.md) | CUDA, drivers, FP16, VRAM sizing |
| [docs/cpu-only-setup.md](docs/cpu-only-setup.md) | Running without a GPU |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Every error code and its fix |
| [docs/architecture.md](docs/architecture.md) | Process model, job pipeline, chunking |
| [docs/privacy.md](docs/privacy.md) | What is stored, where, and how to delete it |
| [docs/model-licensing.md](docs/model-licensing.md) | Model licences and attribution |
| [docs/export-formats.md](docs/export-formats.md) | Exact shape of every export |
| [docs/release-checklist.md](docs/release-checklist.md) | Pre-release verification |

---

## Repository layout

```text
tiktuv/
  apps/
    backend/          FastAPI + inference + media + exports (Python 3.12)
    desktop-gpui/     the application: native Rust GUI on gpui-kit (RTL)
  scripts/            setup, build-rust, package-source, smoke-test
  docs/               all documentation listed above
  samples/            drop-in folder for your own recordings (fixtures are generated)
  tests/              cross-cutting end-to-end tests
```

---

## Status & known limitations

Be precise about what has and has not been exercised.

**Verified by running it:**

- 473 backend tests + 5 skipped (`pytest`)
- `ruff` clean, `mypy --strict` clean across 57 backend modules
- End-to-end smoke test: 31 checks against a real backend process — Hebrew
  filenames, transcription, editing, all six export formats written and opened,
  MP4 audio extraction, path-traversal rejection, history reopen
- PDF exports contain real embedded Hebrew glyphs (asserted by extracting text
  back out of the generated PDF), DOCX contains the RTL properties Word needs
  (asserted by unzipping and inspecting `document.xml`)
- Chunk-boundary de-duplication, timestamp reconstruction, cancellation mid-job,
  partial-result flushing, and resume-after-crash

- **Real `ivrit-ai/whisper-large-v3` inference**, end to end through the running
  app: the model loads via `AutoModelForSpeechSeq2Seq` + `AutoProcessor`,
  transcribes an MP3 and an MP4, returns Hebrew, and is loaded once and reused
  across jobs (`scripts\smoke-test.ps1 -Real` — 31/31)

**Not verified here, and you should not assume it works until you check:**

- **CUDA and FP16 execution.** Verified only as far as device selection and a
  CUDA op probe; full FP16 inference through the model has not been run here.
  CPU inference is verified end to end.
- **Transcription accuracy on real Hebrew speech.** The end-to-end runs use
  synthetic audio, which proves the pipeline but says nothing about word error
  rate. Transcribe a real recording and read the output.
- **Zoom passcode-protected links.** Link resolution, streaming download,
  progress and cancellation are verified against a real recording; the passcode
  submission path is not.
- **The desktop application end to end.** The backend is covered by the tests
  and the smoke test above, and the app compiles clean; the window itself —
  importing, transcribing, editing and exporting through the native UI — has
  not been exercised in this checkout. The end-to-end evidence that used to
  stand here was for the browser-based UI, which has been removed, so it says
  nothing about this one.
- **The portable build.** `scripts\build-rust.ps1` assembles it, but it has not
  been run here, and running it from a different drive letter on a machine that
  has never had Python or Tiktuv installed is what would actually prove the
  "nothing installed" claim. [docs/portable.md](docs/portable.md) lists what to
  check.

[docs/release-checklist.md](docs/release-checklist.md) is the list to work
through before shipping.
- **Speaker diarization** ships as an optional, disabled-by-default module
  behind a stable interface (`tiktuv.diarization`). No diarization backend is
  bundled, because the reliable open-source options require a Hugging Face
  token and gated-model acceptance. See [docs/architecture.md](docs/architecture.md#diarization).
- **Fallback models** (`openai/whisper-large-v3`, `openai/whisper-small`) exist
  in the registry but are **never selected automatically** — the user must pick
  them explicitly, and the UI marks them as non-Hebrew-specialised.

## License

Tiktuv is released under the [MIT License](LICENSE). The ASR model has its own
licence — see [docs/model-licensing.md](docs/model-licensing.md).
