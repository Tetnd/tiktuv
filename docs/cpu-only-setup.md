# CPU-only setup

Tiktuv runs fully on CPU. It is slower, but every feature works and the output
is the same — precision differences aside, there is no quality penalty.

## Install

```bash
.venv\Scripts\python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
```

```bash
.venv\Scripts\python.exe -m pip install -e apps/backend[asr,dev]
```

Or:

```bash
scripts\setup.ps1 -Device cpu
```

The CPU wheel is about 200 MB against roughly 2.5 GB for CUDA, so this is also
much the faster setup if you have no NVIDIA GPU.

## Requirements

| Item | Minimum | Recommended |
|---|---|---|
| RAM | 8 GB | 16 GB |
| Cores | 4 | 8 or more |
| Free disk | 8 GB | — |

The large model needs roughly 6–7 GB of RAM at FP32. Streaming decode means the
media file itself costs almost nothing, however long it is.

## Expected speed

Roughly 0.5–2× real time on a modern desktop CPU: an hour of audio takes 30–120
minutes; a 10-minute recording takes 5–20 minutes.

Tiktuv shows a live ETA once a job is a few percent in, computed from actual
throughput rather than a fixed guess.

## Getting the most from CPU

**Set the quality preset to מהיר.** It uses greedy decoding (`num_beams=1`)
instead of beam search — typically 2–3× faster for a modest accuracy cost. This
is by far the biggest lever.

**Leave the device on אוטומטי or CPU.** FP16 is deliberately disabled on CPU:
PyTorch emulates it there and it is *slower* than FP32, so Tiktuv ignores the
FP16 setting rather than pretending to honour it.

**Let PyTorch use every core.** It does by default. To limit it, so the machine
stays responsive:

```bash
set OMP_NUM_THREADS=4
```

**Keep one job at a time.** That is the default. Concurrent CPU jobs contend for
the same cores and finish later in aggregate than running them in sequence.

## Smaller models

If the large model is too slow, `openai/whisper-small` is roughly 6× faster.

**Understand the trade-off.** It is not Hebrew-specialised, and its Hebrew
accuracy is substantially worse. The app labels it as such. Use it to check that
a pipeline works, not to produce a transcript you intend to rely on.

Tiktuv will never make that substitution for you — you have to pick it
deliberately.

## Verify

```bash
.venv\Scripts\python.exe -m tiktuv.doctor
```

Expect `WARN` on **GPU (CUDA)** — that is correct here and not a problem.
Everything else should read `OK`.
