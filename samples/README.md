# Samples

This folder is intentionally empty of media. Recordings are not committed —
`.gitignore` excludes every audio and video extension here, because sample
recordings are exactly the kind of file that should not end up in a public
repository by accident.

## Test fixtures

The automated tests do not need anything here. `apps/backend/tests/conftest.py`
generates its own fixtures with FFmpeg at run time — MP3 and MP4 files, some
with Hebrew filenames — so the suite is self-contained and deterministic.

## For the real-model test

The opt-in integration test needs genuine Hebrew speech, which cannot be
synthesised. Either drop a recording here as `hebrew-sample.mp3`, or point the
test at one:

```bash
$env:TIKTUV_REAL_MODEL_AUDIO="C:\path\to\hebrew.mp3"
```

Optionally assert that a specific word appears:

```bash
$env:TIKTUV_REAL_MODEL_EXPECT="ישיבה"
```

Then:

```bash
$env:TIKTUV_RUN_REAL_MODEL_TEST=1
```

```bash
.venv\Scripts\python.exe -m pytest apps/backend/tests/integration -v -s
```

## Making a quick test file

A tone, for checking that the pipeline runs (it will not produce meaningful
Hebrew — use real speech for that):

```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=10" -ac 1 -ar 16000 tone.mp3
```
