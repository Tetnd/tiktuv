"""Device detection and memory budgeting.

Imports of :mod:`torch` are deferred and guarded everywhere: the API, the
export pipeline and the whole test suite must work on a machine where the
inference extras were never installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import ErrorCode, ModelError
from ..logging_conf import get_logger
from ..schemas import DeviceInfo, DevicePreference

log = get_logger("asr.device")


def torch_module() -> Any | None:
    """Return the ``torch`` module, or ``None`` if it is not installed."""
    try:
        import torch
    except ImportError:
        return None
    return torch


def torch_version() -> str | None:
    torch = torch_module()
    return getattr(torch, "__version__", None) if torch is not None else None


def cuda_version() -> str | None:
    torch = torch_module()
    if torch is None:
        return None
    return getattr(getattr(torch, "version", None), "cuda", None)


def list_devices() -> list[DeviceInfo]:
    """Enumerate usable devices, CUDA first."""
    devices: list[DeviceInfo] = []
    torch = torch_module()

    if torch is not None and torch.cuda.is_available():
        for index in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(index)
            try:
                free_bytes, _total = torch.cuda.mem_get_info(index)
            except (RuntimeError, AssertionError):  # pragma: no cover - driver dependent
                free_bytes = None
            capability = f"{props.major}.{props.minor}"
            devices.append(
                DeviceInfo(
                    kind="cuda",
                    name=props.name,
                    index=index,
                    total_memory_bytes=int(props.total_memory),
                    free_memory_bytes=int(free_bytes) if free_bytes is not None else None,
                    # FP16 tensor math is reliable from compute capability 7.0.
                    supports_fp16=(props.major >= 7),
                    compute_capability=capability,
                )
            )

    import platform

    devices.append(
        DeviceInfo(
            kind="cpu",
            name=platform.processor() or platform.machine() or "CPU",
            supports_fp16=False,
        )
    )
    return devices


@dataclass(frozen=True, slots=True)
class ResolvedDevice:
    """The concrete device and dtype a job will actually run on."""

    torch_device: str  # "cuda:0" or "cpu"
    kind: str          # "cuda" or "cpu"
    use_fp16: bool
    batch_size: int
    info: DeviceInfo

    @property
    def dtype_name(self) -> str:
        return "float16" if self.use_fp16 else "float32"


#: Rough working-set estimate for whisper-large-v3 (1.55 B params) including
#: activations and beam-search state. Used to pick a safe batch size and to
#: warn before the driver OOMs mid-job.
_LARGE_V3_WEIGHTS_FP16_BYTES = 3_100_000_000
_PER_BATCH_ITEM_BYTES = 420_000_000


def resolve_device(
    preference: DevicePreference,
    *,
    requested_fp16: bool,
    requested_batch_size: int,
    devices: list[DeviceInfo] | None = None,
) -> ResolvedDevice:
    """Pick a device honouring ``preference``, and size the batch to fit."""
    available = devices if devices is not None else list_devices()
    cuda_devices = [d for d in available if d.kind == "cuda"]
    cpu = next((d for d in available if d.kind == "cpu"), None)
    if cpu is None:  # pragma: no cover - list_devices always appends CPU
        raise ModelError("no CPU device reported", code=ErrorCode.MODEL_LOAD_FAILED)

    if preference is DevicePreference.CUDA and not cuda_devices:
        raise ModelError(
            "GPU was requested but no CUDA device is available",
            code=ErrorCode.INSUFFICIENT_VRAM,
            context={"requested": "cuda"},
        )

    chosen: DeviceInfo
    if preference is DevicePreference.CPU or not cuda_devices:
        chosen = cpu
    else:
        # Most free VRAM wins; ties fall back to the first device.
        chosen = max(cuda_devices, key=lambda d: d.free_memory_bytes or d.total_memory_bytes or 0)

    if chosen.kind == "cpu":
        # FP16 on CPU is emulated and slower than FP32 in PyTorch.
        return ResolvedDevice(
            torch_device="cpu",
            kind="cpu",
            use_fp16=False,
            batch_size=max(1, requested_batch_size or 1),
            info=chosen,
        )

    use_fp16 = bool(requested_fp16 and chosen.supports_fp16)
    if requested_fp16 and not chosen.supports_fp16:
        log.warning(
            "FP16 requested but unsupported on this GPU; using FP32",
            extra={"device": chosen.name, "compute_capability": chosen.compute_capability},
        )

    budget = chosen.free_memory_bytes or chosen.total_memory_bytes or 0
    weights = _LARGE_V3_WEIGHTS_FP16_BYTES * (1 if use_fp16 else 2)
    if budget and budget < weights:
        raise ModelError(
            f"model needs about {weights // (1 << 20)} MiB but only "
            f"{budget // (1 << 20)} MiB of VRAM is free",
            code=ErrorCode.INSUFFICIENT_VRAM,
            context={"free_bytes": budget, "required_bytes": weights},
        )

    if requested_batch_size and requested_batch_size > 0:
        batch_size = requested_batch_size
    else:
        headroom = max(0, budget - weights)
        batch_size = max(1, min(16, int(headroom // _PER_BATCH_ITEM_BYTES) or 1))

    return ResolvedDevice(
        torch_device=f"cuda:{chosen.index or 0}",
        kind="cuda",
        use_fp16=use_fp16,
        batch_size=batch_size,
        info=chosen,
    )


def empty_cuda_cache() -> None:
    """Release cached VRAM. Called after every job, including failures."""
    torch = torch_module()
    if torch is None or not torch.cuda.is_available():
        return
    try:
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
    except RuntimeError as exc:  # pragma: no cover - driver dependent
        log.warning("could not empty CUDA cache", extra={"error": str(exc)})
