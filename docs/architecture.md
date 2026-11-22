# Architecture

## Process model

Tiktuv is two processes of its own on one machine — the application and the
backend it starts — plus FFmpeg, and nothing leaves the machine.

```text
┌─────────────────────────────────────────────────────────────┐
│ Desktop app — Rust, gpui-kit, drawn on the GPU              │
│  · spawns and supervises the backend                        │
│  · reads runtime.json → { port, token }                     │
│  · reqwest → http://127.0.0.1:8756  (Bearer <token>)        │
│  · SSE     → /api/events?token=…                            │
│  · reorders Hebrew itself: gpui shapes left-to-right only,  │
│    so text passes through the bidi algorithm before it is   │
│    handed to the renderer (src/rtl_text.rs)                 │
└─────────────────────────────────────────────────────────────┘
                              │ stdout: "TIKTUV_READY <path>"
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Backend — Python 3.12, FastAPI, bound to 127.0.0.1 only     │
│  API ──▶ JobQueue (worker threads) ──▶ TranscriptionPipeline│
│                                          │                  │
│              ┌───────────────────────────┼──────────────┐   │
│              ▼                           ▼              ▼   │
│        media/ (FFmpeg)            asr/ (Transformers)  exports/│
│              │                           │                  │
│              └──── SQLite + JSON transcripts ───────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │ argv arrays, shell=False
                              ▼
                    ┌──────────────────┐
                    │ ffmpeg / ffprobe │
                    └──────────────────┘
```

There is no server, no account system and no network client other than the
Hugging Face download the user explicitly triggers.

## Layers

| Layer | Package | Responsibility |
|---|---|---|
| API | `tiktuv/api/` | HTTP routes, auth, SSE. No business logic. |
| Orchestration | `tiktuv/jobs/` | Queue, pipeline, persistence, recovery |
| Inference | `tiktuv/asr/` | Model loading, device selection, chunk merging |
| Media | `tiktuv/media/` | ffprobe validation, streaming decode |
| Text | `tiktuv/text/` | Hebrew post-processing, subtitle shaping |
| Export | `tiktuv/exports/` | TXT, DOCX, PDF, SRT, VTT, JSON |
| Contracts | `tiktuv/schemas.py` | Pydantic models, mirrored by hand in `apps/desktop-gpui/src/models.rs` |

Dependencies point inwards. `exports/` does not know about jobs; `asr/` does
not know about HTTP; `api/` holds no algorithm.

## The job pipeline

`TranscriptionPipeline.run()` is a plain synchronous function — no event loop,
no queue — so it is directly unit-testable. Stages, with their share of the
progress bar:

| Stage | Share | What happens |
|---|---|---|
| `preparing_media` | 0–3% | Locate FFmpeg, verify disk space and the working file |
| `loading_model` | 3–10% | `ModelManager.acquire()` — a cache hit is instant |
| `transcribing` | 10–88% | Stream windows, transcribe, merge, flush partials |
| `diarizing` | 88–92% | Optional; skipped when no backend is registered |
| `generating_subtitles` | 92–94% | Cue shaping for SRT/VTT |
| `exporting` | 94–100% | Write the requested formats |

Cancellation is checked between windows *and* between emitted windows inside a
single read block, so a cancel is honoured within one window at worst.

## Long recordings

The requirement is that a five-hour file costs no more memory than a
five-minute one. Three things make that true:

1. **One FFmpeg process, streamed.** `iter_windows` decodes to raw `f32le` on
   stdout and reads 1 MiB at a time. The media file is never read into memory,
   and no intermediate WAV is written to disk.
2. **Bounded buffer.** Only one window plus the overlap tail is retained. At
   the default 300 s window and 16 kHz mono float32, that is ~19 MB regardless
   of file length.
3. **Incremental commit.** After every window the merged transcript is written
   to `transcripts/<job_id>.json` and `resume_offset` is advanced.

### Chunk merging

Windows overlap so that a word cut in half at a boundary is heard whole by one
of them. Merging then has to remove what both windows transcribed:

```text
window n     : ├────────────────────────┤
window n+1   :                    ├────────────────────────┤
                                  │◀── overlap ──▶│
                                       ▲
                                    seam (midpoint)
```

`TranscriptAccumulator.add_window` places the seam at the **midpoint of the
overlap** — the point furthest from either window's edge, where truncation
artefacts are least likely — and assigns each segment to a side by its centre.
It then compares the normalised token runs either side of the seam and deletes
the longest genuine repetition (2–24 tokens). Normalisation folds niqqud,
punctuation, geresh/gershayim and case, because the model is not consistent
about any of them across windows.

Timestamps are made absolute at the moment a window's results arrive
(`shift_segments`), so everything downstream works on one timeline.

Covered by `tests/test_chunking.py` — including the case where two windows
transcribe the overlap identically and the case where they only partly agree.

### Crash recovery

| Event | Behaviour |
|---|---|
| App killed mid-job | `recover_orphans()` marks the job failed and keeps `resume_offset` |
| User clicks *retry* | Job restarts from the last committed window, keeping earlier text |
| Transcript file corrupt | `load()` returns `None`; the job can be re-run, nothing crashes |
| Partial write interrupted | `os.replace` is atomic — the previous good file survives |

## Concurrency

One worker thread by default. The reason is VRAM: two concurrent
`whisper-large-v3` jobs will not fit on a consumer GPU, and the failure mode is
a mid-job CUDA OOM rather than a queue delay. `max_concurrent_jobs` raises it;
`WhisperHFTranscriber` holds a lock around generation so raising it is safe
rather than corrupting, just slower per job.

Threads, not processes, because the expensive resource is the loaded model —
3 GB of weights that must be shared, not duplicated.

## Model management

`ModelManager` caches one transcriber keyed by
`(model_id, device, fp16, batch_size, quantized)`. A job whose settings match
reuses it; a job that differs triggers an unload (freeing VRAM) and a reload.

**The Hebrew model is never substituted.** `ivrit-ai/whisper-large-v3` is the
default and the only model flagged `hebrew_specialised`. The generic Whisper
models are in the catalogue so a user who needs them can choose one *by hand*,
and the UI labels them as unsuitable for Hebrew. If the Hebrew model cannot
load, the job fails with a specific error — it does not fall back.

## Diarization

Off by default and no backend bundled. `pyannote/speaker-diarization-3.1` is
the reliable open-source option, but it is a gated Hugging Face repo requiring
an account, two licence acceptances and an access token — which contradicts the
product's central promise, and its licence does not let us redistribute the
weights.

So the feature is an interface: implement `DiarizationBackend` and call
`register_backend()`. Everything downstream already handles speakers —
`Segment.speaker`, the transcript store, the editor's rename UI, and every
exporter. `assign_speakers()` and `build_speaker_map()` are pure functions and
are unit-tested without any model.

If diarization is requested and no backend is registered, the job logs a
warning and completes without speaker labels rather than discarding a finished
transcription.

## Security

| Risk | Control |
|---|---|
| Another local process driving the API | Random per-session bearer token; loopback-only bind; a middleware that rejects any routable peer IP |
| Path traversal via API parameters | `resolve_within()` resolves first, then asserts containment; media tokens must survive `sanitize_filename` unchanged |
| Command injection through filenames | FFmpeg is invoked with argument arrays and `shell=False`; no string is ever concatenated into a command line |
| Extension spoofing | Every file is validated by ffprobe inspection, never by extension |
| Bidi extension spoofing | Bidi control characters are stripped from filenames |
| Leaking the user's paths into logs | `redact_paths()` reduces paths to `<path>/<basename>`, handling repr-escaped separators |
| Arbitrary file download | `/api/exports/download` is confined to the exports directory |

## Why these choices

**SSE, not WebSockets.** Progress is one-directional. SSE reconnects on its
own, needs no protocol handling in the shell, and survives the web view being
backgrounded.

**Transcripts as JSON files, jobs in SQLite.** A partial transcript is flushed
after every window; doing that to a database row would mean rewriting a large
blob repeatedly, and a corrupt database would cost the user an hour of
transcription. Files are cheap to write atomically and trivially recoverable.

**Logical-order text everywhere except PDF painting.** Reversing strings
produces files that look correct in exactly one viewer and are broken
everywhere else, and it destroys copy-paste and search. The Unicode
bidirectional algorithm does the work in Word. PDF is the
one format with no bidi engine, so `pdf_export.py` runs the algorithm itself —
wrapping lines in logical order first, then converting each finished line to
visual order. That single exception is documented at length in that file.
