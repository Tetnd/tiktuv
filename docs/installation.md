# Installation (end users)

Windows 10 (21H2 or newer) and Windows 11.

## 1. Install

Run `Tiktuv_1.0.0_x64-setup.exe` (or the `.msi`). It installs for the current
user, so no administrator rights are needed.

Windows 10 may prompt to install the **WebView2 runtime** — accept it. Windows
11 already includes it.

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

The Hebrew model is not bundled: it is about 3.1 GB and has its own licence. On
the same screen, press **הורדה** next to
**עברית — ivrit.ai Whisper Large v3**.

You are shown the download size and the exact destination folder before
anything is fetched. **This is the only time Tiktuv uses the internet.** Once it
completes, transcription works fully offline.

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

Uninstall from **Settings → Apps**. That leaves your data in place. To remove
that as well, delete `%LOCALAPPDATA%\Tiktuv` — it holds the models,
transcripts, history and exports. See [privacy.md](privacy.md).
