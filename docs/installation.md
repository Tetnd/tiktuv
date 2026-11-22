# Installation (end users)

Windows 10 (21H2 or newer) and Windows 11.

## 1. Install

**From the package you were given:** unzip it somewhere permanent (for example
`C:\Tiktuv` — not inside the ZIP), then double-click **`Install Tiktuv.cmd`**.

It installs everything needed, including Python, Rust and FFmpeg if they are
missing, compiles Tiktuv, and puts a **Tiktuv** icon on your Desktop and in the
Start Menu. Expect 10-20 minutes the first time, most of it compiling.

If Windows warns about the file, choose *More info* → *Run anyway*; the
installer is a plain-text script you can open and read first.

**From a source checkout**, the equivalent is:

```bash
.\install.ps1
```

That puts a **Tiktuv** icon on your Desktop and in the Start Menu. Launching it
starts the transcription service in the background and opens Tiktuv's own
window. Closing the window stops the service.

**Without installing anything**, use the portable build: a folder holding
`Tiktuv.exe` and everything it needs, which runs from any path — a USB stick
included. See [portable.md](portable.md).

## 2. Install FFmpeg

Tiktuv uses FFmpeg to read media. It is not bundled, because FFmpeg builds carry
their own licence terms.

```bash
winget install Gyan.FFmpeg
```

Then **restart Tiktuv** so it picks up the updated `PATH`.

## 3. First run

Tiktuv opens on the **מערכת ומודלים** screen and checks:

| Check | Meaning |
|---|---|
| מערכת ההפעלה | Windows 10/11 confirmed |
| סביבת Python | Bundled runtime, 3.12+ |
| FFmpeg | Found, with its version |
| מנוע ההסקה | PyTorch present |
| כרטיס מסך | GPU and VRAM, or a note that the CPU will be used |
| מודל התמלול העברי | Whether the model has been downloaded |
| מקום פנוי בדיסק | At least 8 GB recommended |
| הרשאות כתיבה | Data folders are writable |

Green and amber are both fine — amber means slower, not broken. Red must be
fixed, and each red item states how.

## 4. Download the model

The Hebrew model is not bundled: it is about **6.2 GB** and has its own licence.

**You do not need to do anything** — Tiktuv starts the download by itself the
first time you open it, and shows the size, the destination folder and live
progress while it runs. You can stop it and it resumes where it left off.

If you would rather fetch it during setup, so the first transcription starts
immediately:

```bash
.\install.ps1 -DownloadModel
```

**This is the only time Tiktuv uses the internet.** Once it completes,
transcription works fully offline.

## 5. Transcribe

1. Drag a file onto the window, or press **בחירת קבצים**.
2. Adjust **הגדרות תמלול** if you want. The defaults are Hebrew, balanced
   quality, and segment timestamps.
3. Press **התחל תמלול**.
4. Watch the stage and percentage. You can press **ביטול** at any time —
   partial results are kept.
5. When it finishes, press **פתיחת התמליל** to edit, then **ייצוא**.

## Expected speed

| Hardware | Roughly |
|---|---|
| NVIDIA GPU, 8 GB+ | 5–15× real time (1 hour of audio in 4–12 minutes) |
| NVIDIA GPU, 6 GB | 3–8× real time |
| Modern CPU, 16 GB RAM | 0.5–2× real time (1 hour in 30–120 minutes) |

The first job after launching includes a one-off model load of 10–60 seconds.
Later jobs reuse the loaded model.

## Long recordings

Multi-hour files are supported and processed in streamed chunks, so memory use
does not grow with length. Progress is saved continuously — if the app closes
mid-job, **נסה שוב** resumes from the last saved point instead of restarting.

## Uninstall

Tiktuv installs no system-wide state: delete the Desktop and Start Menu
shortcuts, and the folder you unzipped (or the portable folder). Your
data stays in place. To remove
that as well, delete `%LOCALAPPDATA%\Tiktuv` — it holds the models,
transcripts, history and exports. See [privacy.md](privacy.md).
