# Troubleshooting

Start here:

```bash
.venv\Scripts\python.exe -m tiktuv.doctor
```

It checks the OS, Python, FFmpeg, PyTorch, GPU, model, disk space and directory
permissions, and prints a remedy for anything that fails. The same checks are
on the app's **מערכת ומודלים** screen.

---

## Error codes

Every failure carries a stable code, shown in the UI with a Hebrew message and
a suggested fix.

### `FFMPEG_MISSING` — FFmpeg לא נמצא במחשב

Tiktuv could not find `ffmpeg` or `ffprobe`.

```bash
winget install Gyan.FFmpeg
```

**Then open a new terminal.** winget appends to `PATH`, and processes that were
already running do not see the change. Tiktuv also searches the winget package
directory directly, so restarting the app is usually enough.

If FFmpeg lives somewhere unusual, point at it explicitly:

```bash
setx TIKTUV_FFMPEG_PATH "C:\tools\ffmpeg\bin\ffmpeg.exe"
```

### `MEDIA_CORRUPT` — קובץ המדיה פגום

ffprobe could not parse the file. Tiktuv validates by inspecting the file, not
by trusting its extension, so a renamed or truncated file is rejected here.
Confirm it plays in a normal player. For a partial download, re-copy it.

### `MEDIA_NO_AUDIO` — לא נמצא ערוץ אודיו

A video with no audio track. Check with:

```bash
ffprobe -hide_banner -show_streams -select_streams a "your-file.mp4"
```

### `UNSUPPORTED_CODEC` — קודק המדיה אינו נתמך

Your FFmpeg build lacks the decoder. Convert first:

```bash
ffmpeg -i input.mkv -vn -ac 1 -ar 16000 output.wav
```

### `MODEL_NOT_DOWNLOADED` — המודל טרם הורד

Open **מערכת ומודלים** and download it. The screen shows the size (~3.1 GB) and
the exact folder before anything is fetched. This is the only operation in
Tiktuv that uses the network.

### `MODEL_DOWNLOAD_FAILED` — הורדת המודל נכשלה

Usually connectivity or disk space. A partial download can also leave the cache
in a state where the model looks absent. Delete it from the settings screen and
retry, or remove the folder shown there.

Behind a proxy, set `HTTPS_PROXY` before starting the backend.

### `ASR_BACKEND_MISSING` — רכיבי התמלול אינם מותקנים

PyTorch or Transformers is missing:

```bash
.venv\Scripts\python.exe -m pip install -e apps/backend[asr]
```

PyTorch must come from its own index — see
[gpu-cuda-setup.md](gpu-cuda-setup.md) or [cpu-only-setup.md](cpu-only-setup.md).

### `INSUFFICIENT_VRAM` — אין מספיק זיכרון בכרטיס המסך

`whisper-large-v3` needs roughly 3.1 GB in FP16 plus working memory; 6 GB of
VRAM is the practical floor. In order of preference:

1. Set **גודל אצווה** to `1`.
2. Confirm FP16 is on (it is by default; it is ignored below compute
   capability 7.0, where it is not reliable).
3. Switch **מעבד** to **CPU** — slower, but it always works.
4. Close other GPU applications; browsers and games hold VRAM.

### `INSUFFICIENT_RAM`

CPU inference for the large model wants ~8 GB free. Use a shorter recording,
close other applications, or pick a smaller model — knowing it is not
Hebrew-specialised.

### `INSUFFICIENT_DISK` — אין מספיק מקום פנוי

Keep ~8 GB free. Use the **מחיקת קבצים זמניים** button, and check the storage
breakdown on the same screen.

### `UNAUTHORIZED` — גישה נדחתה

The session token is regenerated on every backend start. If the frontend is
holding an old one, restart the app. In `vite dev`, `scripts/dev.ps1` rewrites
`apps/frontend/.env.local` each run — restart Vite so it re-reads it.

### `PATH_NOT_ALLOWED` — הנתיב אינו מורשה

A path escaped the directories the API is allowed to touch. For exports, choose
a folder you can write to; for downloads, only files inside the exports
directory are served.

### `JOB_INVALID_STATE`

The job is not in a state that permits the action — for example deleting a job
that is still running. Cancel it first, then refresh the list.

---

## Symptoms without an error code

### The app opens but nothing loads; the header says "מתחבר…"

The backend did not start or died. Check the log:

```bash
type %LOCALAPPDATA%\Tiktuv\logs\tiktuv.log
```

Then start it by hand to see the failure directly:

```bash
.venv\Scripts\python.exe -m tiktuv --log-level DEBUG
```

### Port 8756 is already in use

Another copy is still running, or something else took the port:

```bash
netstat -ano | findstr :8756
```

Start on a different port with `--port 8760`.

### Transcription is extremely slow

Check which device is in use on the **מערכת ומודלים** screen. CPU inference for
`large-v3` runs at roughly 0.5–2× real time depending on the processor — an
hour of audio can take an hour or more. On GPU it is typically 5–15× real time.

If a GPU is present but unused, PyTorch is probably the CPU build:

```bash
.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

`False` with a working GPU means you installed the CPU wheel. Reinstall from
the CUDA index.

### Hebrew looks reversed or scrambled

**In the app or in a TXT/DOCX export:** this would be a bug — please report it
with the file. Tiktuv stores text in logical order and never reverses it.

**In a plain-text editor:** some editors ignore the bidi algorithm. The file is
correct; open it in Notepad, VS Code or Word.

**In a PDF viewer:** PDFs have no bidi engine, so Tiktuv performs the
reordering when painting. Text extracted from the PDF (copy-paste) will come
out in visual order — that is inherent to the format. Use the TXT, DOCX or JSON
export when you need to reuse the text.

### DOCX opens left-aligned in Word

Tiktuv sets RTL at the section, paragraph and run level, plus the complex-script
font. If a document still opens LTR, check whether it was converted by another
tool (Google Docs round-trips often drop `w:bidi`). Open the original export
directly in Word to confirm.

### Words are duplicated at regular intervals

That would mean chunk-boundary de-duplication failed. Note the timestamps of a
duplicate and report them — the merge places its seam at the midpoint of the
overlap and removes repeated token runs, and the behaviour is covered by tests
in `apps/backend/tests/test_chunking.py`.

### A job disappeared after a crash

It did not. `recover_orphans()` marks jobs that were running at shutdown as
failed and preserves the resume point. Open **היסטוריה** and click **נסה שוב** —
it continues from the last committed window rather than starting over.

### Hebrew filenames show as `????`

Tiktuv normalises filenames to NFC and preserves Hebrew. Question marks usually
mean a console using a legacy code page. It does not affect the files:

```bash
chcp 65001
```

---

## Collecting diagnostics

```bash
.venv\Scripts\python.exe -m tiktuv.doctor > doctor.txt 2>&1
```

Logs are at `%LOCALAPPDATA%\Tiktuv\logs\tiktuv.log`. Absolute paths are
redacted to `<path>/<basename>` before anything is written, so a log can be
shared without revealing your folder structure — but skim it before sending.
