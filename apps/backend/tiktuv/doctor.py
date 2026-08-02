"""Environment doctor — ``python -m tiktuv.doctor``.

Runs the same checks as the first-run screen, from a terminal, before anything
else is started. Exit code 0 means the environment is usable; 1 means at least
one check failed.
"""

from __future__ import annotations

import contextlib
import platform
import sys

from . import __version__
from .asr import registry
from .asr.device import cuda_version, list_devices, torch_version
from .config import get_settings
from .errors import FFmpegMissingError
from .jobs.store import free_disk_bytes
from .media.ffmpeg import locate_tools

OK = "OK  "
WARN = "WARN"
FAIL = "FAIL"


def _line(status: str, label: str, detail: str = "", remedy: str = "") -> str:
    text = f"[{status}] {label:<28} {detail}"
    if remedy:
        text += f"\n         -> {remedy}"
    return text


def run() -> int:
    # The Windows console defaults to a legacy code page, which cannot encode
    # the characters used below. Reconfigure rather than degrade the output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()
    failed = False
    lines: list[str] = [
        f"Tiktuv {__version__} — environment check",
        "=" * 62,
    ]

    # -- OS ---------------------------------------------------------------
    is_windows = sys.platform == "win32"
    lines.append(
        _line(
            OK if is_windows else WARN,
            "Operating system",
            f"{platform.system()} {platform.release()} ({platform.machine()})",
            "" if is_windows else "Windows 10/11 is the officially supported platform.",
        )
    )

    # -- Python -----------------------------------------------------------
    ok_python = sys.version_info >= (3, 12)
    failed |= not ok_python
    lines.append(
        _line(
            OK if ok_python else FAIL,
            "Python",
            platform.python_version(),
            "" if ok_python else "Python 3.12 or newer is required.",
        )
    )

    # -- FFmpeg -----------------------------------------------------------
    try:
        tools = locate_tools(settings)
        lines.append(_line(OK, "FFmpeg", f"{tools.version} ({tools.ffmpeg})"))
    except FFmpegMissingError as exc:
        failed = True
        lines.append(_line(FAIL, "FFmpeg", exc.detail, "Install FFmpeg and add it to PATH."))

    # -- PyTorch ----------------------------------------------------------
    torch_v = torch_version()
    if torch_v is None:
        failed = True
        lines.append(
            _line(FAIL, "PyTorch", "not installed", "See docs/cpu-only-setup.md.")
        )
    else:
        lines.append(_line(OK, "PyTorch", torch_v))

    # -- Transformers -----------------------------------------------------
    try:
        import transformers

        lines.append(_line(OK, "Transformers", transformers.__version__))
    except ImportError:
        failed = True
        lines.append(
            _line(FAIL, "Transformers", "not installed", "pip install -e apps/backend[asr]")
        )

    # -- Devices ----------------------------------------------------------
    devices = list_devices()
    gpus = [device for device in devices if device.kind == "cuda"]
    if gpus:
        gpu = gpus[0]
        total = (gpu.total_memory_bytes or 0) / (1 << 30)
        lines.append(
            _line(
                OK if total >= 6 else WARN,
                "GPU (CUDA)",
                f"{gpu.name}, {total:.1f} GiB, CUDA {cuda_version() or '?'}, "
                f"fp16={'yes' if gpu.supports_fp16 else 'no'}",
                "" if total >= 6 else "Under 6 GiB VRAM; consider CPU or a smaller batch size.",
            )
        )
    else:
        lines.append(
            _line(WARN, "GPU (CUDA)", "none detected", "Transcription will run on the CPU.")
        )

    # -- Model ------------------------------------------------------------
    entry = registry.get_entry(settings.default_model_id)
    downloaded = registry.is_downloaded(entry.id, settings.model_cache_dir)
    lines.append(
        _line(
            OK if downloaded else WARN,
            "Hebrew ASR model",
            f"{entry.id} - {'downloaded' if downloaded else 'not downloaded'}",
            ""
            if downloaded
            else f"~{entry.approx_download_bytes >> 20} MiB will be saved to "
            f"{settings.model_cache_dir}",
        )
    )

    # -- Disk -------------------------------------------------------------
    free = free_disk_bytes(settings.data_dir)
    enough = free >= 8 * (1 << 30)
    lines.append(
        _line(
            OK if enough else WARN,
            "Free disk space",
            f"{free / (1 << 30):.1f} GiB at {settings.data_dir}",
            "" if enough else "At least 8 GiB is recommended.",
        )
    )

    # -- Directories ------------------------------------------------------
    unwritable: list[str] = []
    for directory in settings.managed_dirs():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".tiktuv-write-test"
            probe.touch()
            probe.unlink()
        except OSError:
            unwritable.append(str(directory))
    failed |= bool(unwritable)
    lines.append(
        _line(
            OK if not unwritable else FAIL,
            "Writable data directories",
            str(settings.data_dir) if not unwritable else ", ".join(unwritable),
            "" if not unwritable else "Check folder permissions.",
        )
    )

    lines.append("=" * 62)
    lines.append("Result: " + ("PROBLEMS FOUND" if failed else "environment looks good"))
    print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
