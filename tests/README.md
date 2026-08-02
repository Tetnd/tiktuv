# Cross-cutting tests

Most tests live next to the code they cover:

| Suite | Location | Command |
|---|---|---|
| Backend unit + integration | `apps/backend/tests/` | `.venv\Scripts\python.exe -m pytest apps/backend/tests -v` |
| Real-model integration (opt-in) | `apps/backend/tests/integration/` | see below |
| Frontend unit + component | `apps/frontend/src/test/` | `npm --prefix apps/frontend run test` |

## End-to-end

`scripts/smoke-test.ps1` is the end-to-end suite. Unlike the pytest suite — which
drives the app in-process — it starts the backend as a **real process** and
exercises the whole user journey over HTTP:

1. Health check and token authorization
2. First-run preflight
3. Generating Hebrew-named MP3 and MP4 fixtures with FFmpeg
4. Importing and transcribing the MP3
5. Reading the transcript, editing a segment, reloading it
6. Exporting TXT, DOCX, PDF, SRT, VTT and JSON, and verifying each file
7. MP4 audio extraction
8. Security: path traversal in downloads and in media tokens
9. Reopening a completed job from history
10. Temp-file cleanup

```bash
scripts\smoke-test.ps1
```

It uses the mock ASR backend by default, so it needs no GPU and no model
download. To run the same journey through the real Hebrew model:

```bash
scripts\smoke-test.ps1 -Real
```

## Contract drift

The TypeScript contracts in `packages/shared-types` are written by hand and can
drift from the backend's Pydantic enums. This compares them and fails on any
mismatch:

```bash
scripts\check-shared-types.ps1
```
