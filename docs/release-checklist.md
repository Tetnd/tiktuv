# Release checklist

## 1. Code quality

- [ ] `.venv\Scripts\python.exe -m pytest apps/backend/tests -v` — all pass
- [ ] `.venv\Scripts\python.exe -m ruff check apps/backend` — clean
- [ ] `.venv\Scripts\python.exe -m mypy apps/backend/tiktuv` — clean
- [ ] `npm --prefix apps/frontend run check` — typecheck and lint clean
- [ ] `npm --prefix apps/frontend run test` — all pass
- [ ] Version bumped in `apps/backend/tiktuv/__init__.py`, both `package.json`
      files, `Cargo.toml` and `tauri.conf.json`, and all four agree

## 2. The real model

The automated suite mocks inference. Before every release, exercise the real
model — this is the step that catches a broken inference path.

- [ ] `TIKTUV_RUN_REAL_MODEL_TEST=1 pytest apps/backend/tests/integration -v -s`
- [ ] Confirm the log shows `ivrit-ai/whisper-large-v3`, not a fallback
- [ ] Transcribe a real Hebrew recording and read the output for sense
- [ ] Repeat on CPU **and** on a CUDA GPU
- [ ] Confirm FP16 is actually used on a compute-capability 7.0+ GPU

## 3. Long recordings

- [ ] Transcribe a recording of two hours or more
- [ ] Watch peak memory in Task Manager — it must stay flat, not grow with
      elapsed time
- [ ] Read across several chunk boundaries: no duplicated or dropped words
- [ ] Timestamps are still aligned at the end of the file

## 4. Crash and cancellation

- [ ] Cancel a running job; the partial transcript is kept
- [ ] Kill the backend mid-job; on restart the job shows as failed with a
      resume point
- [ ] **נסה שוב** resumes rather than restarting, and earlier text survives
- [ ] Force-quit during an export; no truncated output file is left behind

## 5. Exports

Take one Hebrew transcript, export every format, and open each one:

- [ ] TXT — opens right-aligned in Notepad, Hebrew intact
- [ ] Timestamped TXT — brackets sit on the correct side
- [ ] DOCX — **opens RTL in real Microsoft Word**, not only LibreOffice;
      punctuation on the correct side; timestamp table columns right-to-left
- [ ] PDF — Hebrew glyphs render (no empty boxes), text right-aligned,
      pagination correct on a multi-page transcript
- [ ] SRT — plays correctly in VLC, Hebrew right-aligned
- [ ] VTT — plays correctly in a browser
- [ ] JSON — valid, Hebrew unescaped, word timestamps present when requested
- [ ] A file with a Hebrew name exports with that name intact

## 6. Security

- [ ] `netstat -ano | findstr :8756` shows a loopback listener only
- [ ] A request without the token returns 401
- [ ] `/api/exports/download?path=C:\Windows\win.ini` returns 403
- [ ] A media token containing `..` is rejected
- [ ] No `/api/docs` in a release build
- [ ] `runtime.json` is removed on exit
- [ ] Logs contain no absolute user paths
- [ ] Re-read the control table in `docs/architecture.md#security` and confirm
      each control still holds

## 7. Privacy

- [ ] Monitor network traffic across a full transcription — no outbound
      connections
- [ ] The model download discloses size and destination **before** downloading
- [ ] Temp-file deletion works and reports the bytes reclaimed
- [ ] Model deletion works and frees the space
- [ ] Deleting a history record leaves exported files alone

## 8. First run on a clean machine

Use a fresh VM or a new Windows user account.

- [ ] The installer completes without administrator rights
- [ ] The app launches and correctly reports FFmpeg as missing
- [ ] After installing FFmpeg and restarting, that check turns green
- [ ] The model download completes and its check turns green
- [ ] A first transcription succeeds end to end
- [ ] Uninstalling removes the program and leaves user data
- [ ] Deleting `%LOCALAPPDATA%\Tiktuv` removes everything else

## 9. Accessibility and UX

- [ ] Full keyboard navigation; focus is always visible
- [ ] A screen reader announces progress and job status
- [ ] Every interactive control has an accessible name
- [ ] Layout holds at 1280×800 and at 1920×1080
- [ ] No horizontal scrolling at the minimum window size
- [ ] Every error shown is Hebrew, specific, and actionable

## 10. Packaging

- [ ] `scripts\build-windows.ps1` produces both MSI and NSIS installers
- [ ] Installers are signed, or the unsigned SmartScreen warning is documented
      in the release notes
- [ ] The frozen backend starts and prints its handshake line
- [ ] Closing the window terminates the backend — verify in Task Manager
- [ ] Installing over a previous version preserves history and downloaded models

## 11. Documentation

- [ ] Every README command runs as written, on a clean checkout
- [ ] Every documented environment variable exists in `config.py`
- [ ] Troubleshooting covers every code in `errors.py`
- [ ] `docs/model-licensing.md` matches the current licence on the model page
- [ ] The README's "Status & known limitations" section reflects what was
      actually verified for *this* release — not what was verified last time
