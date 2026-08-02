# GPU and CUDA setup

## Requirements

| Item | Minimum | Recommended |
|---|---|---|
| GPU | NVIDIA, 6 GB VRAM | 8 GB or more |
| Compute capability | 5.2 | 7.0+ (enables reliable FP16) |
| Driver | 527+ for the CUDA 12.1 wheels | latest |

AMD and Intel GPUs are not supported: PyTorch's ROCm builds are Linux-only and
Tiktuv targets Windows first. Those machines run on CPU, which works fine —
see [cpu-only-setup.md](cpu-only-setup.md).

## 1. Check the driver

```bash
nvidia-smi
```

The **CUDA Version** it reports is the maximum your driver supports, not what
is installed. You do **not** need a separate CUDA toolkit: the PyTorch wheels
bundle their own runtime.

## 2. Install the CUDA build of PyTorch

```bash
.venv\Scripts\python.exe -m pip uninstall -y torch
```

```bash
.venv\Scripts\python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121
```

Or let the setup script do it:

```bash
scripts\setup.ps1 -Device cuda
```

## 3. Verify

```bash
.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

`True` means it worked. The doctor reports the same thing with more context:

```bash
.venv\Scripts\python.exe -m tiktuv.doctor
```

## VRAM budget

`whisper-large-v3` has 1.55 B parameters:

| Precision | Weights | With activations and beam search |
|---|---|---|
| FP16 | ~3.1 GB | ~4.5–6 GB |
| FP32 | ~6.2 GB | ~8–11 GB |

Tiktuv checks free VRAM before loading and fails with `INSUFFICIENT_VRAM` and a
concrete number, rather than letting the driver run out mid-job.

## FP16

On by default, and used when the GPU reports compute capability 7.0 or higher
(Turing, Ampere, Ada and newer). Below that, FP16 tensor math is not reliable,
so Tiktuv logs a warning and runs FP32 — it does not silently produce degraded
output while claiming to use FP16.

## Batch size

`0`, the default, sizes the batch from free VRAM. Batching only helps when a
processing window is longer than one 30-second model chunk.

Set it to `1` if you hit `INSUFFICIENT_VRAM`. Values above 8 rarely help —
throughput is bounded by memory bandwidth well before that.

## 8-bit quantization

The **quantize_8bit** setting cuts the weights to roughly 1.6 GB. It requires
`bitsandbytes`:

```bash
.venv\Scripts\python.exe -m pip install bitsandbytes
```

If it is not installed, the job **fails with an explicit message** rather than
quietly running at full precision — a silent fallback would leave you puzzling
over why memory use never dropped.

Expect some accuracy loss. If accuracy matters more than speed, prefer CPU
inference over 8-bit.

## Multiple GPUs

Tiktuv selects the CUDA device with the most free memory. To pin one:

```bash
set CUDA_VISIBLE_DEVICES=0
```

Splitting a model across GPUs is not supported; one job runs on one device.

## Troubleshooting

**`torch.cuda.is_available()` is False with a working GPU.** You have the CPU
wheel installed. Uninstall it and reinstall from the `cu121` index.

**`CUDA error: no kernel image is available`.** Your GPU predates the
architectures the wheel was compiled for. Use CPU inference.

**Out of memory partway through a long file.** Another application took VRAM.
Tiktuv frees its cached blocks after every failure, so retrying usually works,
and the job resumes from its last saved window rather than starting over.

**The GPU sits idle during transcription.** Check that **מעבד** is not pinned to
CPU, and confirm the active device on the **מערכת ומודלים** screen.
