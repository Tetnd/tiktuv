# Tiktuv (תכתוב)

**Local-first Hebrew transcription for MP3 / MP4 and other media.**

Tiktuv transcribes Hebrew audio and video entirely on your own machine using the
Hebrew-focused ASR model [`ivrit-ai/whisper-large-v3`](https://huggingface.co/ivrit-ai/whisper-large-v3).
Nothing is uploaded. No analytics. No accounts.

The model downloads itself on first use — the only step that touches the network,
and it always shows its size and destination first.

- 🇮🇱 Hebrew-first UI and Hebrew-first ASR, with correct RTL rendering everywhere
- 🎬 MP3, MP4, WAV, M4A, MOV, MKV, WEBM — audio extracted with FFmpeg
- ⚡ CUDA GPU / CPU, automatic device detection, FP16, configurable batch size
- 🧩 Streaming chunked processing — multi-hour recordings without loading them into RAM
- ✍️ Built-in RTL transcript editor with a synchronized media player
- 📤 Export TXT, timestamped TXT, DOCX, PDF, SRT, VTT, JSON — DOCX/PDF are genuinely RTL
- 💾 Local SQLite job history with crash-safe recovery and partial results

---

## Table of contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Exact commands](#exact-commands)
- [Documentation](#documentation)
- [Repository layout](#repository-layout)
- [Status & known limitations](#status--known-limitations)
- [License](#license)

---

## Requirements

| Component | Minimum | Notes |
|---|---|---|
| OS | Windows 10 21H2 / Windows 11 | Linux & macOS are architecturally supported, not yet packaged |
| Python | 3.12+ | Bundled in the desktop installer |
| Node.js | 20+ | Frontend build only |
| FFmpeg | 6.0+ | `ffmpeg` + `ffprobe` on `PATH`, or set `TIKTUV_FFMPEG_PATH` |
| Rust | stable | Only to build the Tauri desktop shell |
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

From a clean Windows 10/11 machine that installs Python, Node and FFmpeg if
they are missing, sets up an isolated environment, picks the right PyTorch
build for the hardware, builds the interface, and puts a **Tiktuv** icon on the
Desktop. Re-running it is safe — finished steps are skipped.

### Giving it to someone

```bash
scripts\package-source.ps1 -IncludeBuiltUI
```

Produces `Tiktuv-<version>.zip` (about 0.5 MB) on your Desktop. It deliberately
excludes `.venv`, `node_modules`, the model cache and any recordings — those
are either machine-specific or private. The recipient unzips it, double-clicks
`Install Tiktuv.cmd`, and gets the same result.

### Developing on it

```bash
scripts\setup.ps1
```

```bash
scripts\dev.ps1
```

Backend on `http://127.0.0.1:8756`, frontend on `http://127.0.0.1:5173`.

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

Frontend:

```bash
npm --prefix apps/frontend install
```

### 2. Check the environment

```bash
.venv\Scripts\python.exe -m tiktuv.doctor
```

### 3. Start the backend

```bash
.venv\Scripts\python.exe -m tiktuv --host 127.0.0.1 --port 8756
```

### 4. Start the frontend

```bash
npm --prefix apps/frontend run dev
```

### 5. Run the desktop application

**Install it as a desktop app** — creates a "Tiktuv" icon you can
double-click. No Rust toolchain needed:

```bash
scripts\install-desktop.ps1 -StartMenu
```

This builds the UI, generates the icon, and adds Desktop and Start Menu
shortcuts. Launching runs the backend in the background and opens the UI in a
dedicated application window (Edge or Chrome in `--app` mode: no address bar,
no tabs, its own taskbar entry). Closing the window shuts the backend down.

To remove the shortcuts (your transcripts and models are untouched):

```bash
scripts\install-desktop.ps1 -Uninstall
```

**Or run the Tauri shell** during development, which needs Rust:

```bash
npm --prefix apps/desktop install
```

```bash
npm --prefix apps/desktop run tauri dev
```

### 6. Run tests

Backend (fast, model fully mocked — no download, no GPU):

```bash
.venv\Scripts\python.exe -m pytest apps/backend/tests -v
```

Frontend unit + RTL/component tests:

```bash
npm --prefix apps/frontend run test
```

Frontend type-check and lint:

```bash
npm --prefix apps/frontend run check
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

### 7. Build a Windows installer

```bash
npm --prefix apps/frontend run build
```

```bash
scripts/build-windows.ps1
```

The MSI and NSIS installers land in
`apps/desktop/src-tauri/target/release/bundle/`.

---

## Documentation

| Document | Contents |
|---|---|
| [docs/installation.md](docs/installation.md) | End-user install, first run, model download |
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
    frontend/         React 18 + TypeScript + Vite + Tailwind (RTL)
    desktop/          Tauri 2 shell, manages the bundled backend process
  packages/
    shared-types/     TypeScript mirror of the backend Pydantic contracts
  scripts/            setup, dev, smoke-test, build-windows, contract check
  docs/               all documentation listed above
  samples/            drop-in folder for your own recordings (fixtures are generated)
  tests/              cross-cutting end-to-end tests
```

---

## Status & known limitations

Be precise about what has and has not been exercised.

**Verified by running it:**

- 377 backend tests + 5 skipped (`pytest`), 65 frontend tests (`vitest`)
- `ruff` clean, `mypy --strict` clean across 47 backend modules
- Frontend type-check, lint and production build clean
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
- **The desktop app**: shortcut launches the backend, opens a dedicated window,
  serves the UI with an injected session token, and shuts down on close

**Not verified here, and you should not assume it works until you check:**

- **CUDA and FP16 execution.** This machine has no GPU, so the CUDA branch of
  device selection and FP16 inference have not been executed. CPU inference is
  verified.
- **Transcription accuracy on real Hebrew speech.** The end-to-end runs use
  synthetic audio, which proves the pipeline but says nothing about word error
  rate. Transcribe a real recording and read the output.
- **The Tauri MSI/NSIS installer.** No Rust toolchain here, so
  `scriptsuild-windows.ps1` has not been run end to end. The desktop-shortcut
  route (`scripts\install-desktop.ps1`) is what was verified.

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
