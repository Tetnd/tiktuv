"""Download a model from the command line — ``python -m tiktuv.fetch_model``.

Used by ``install.ps1`` to pre-fetch the Hebrew model when
preparing a machine, so the first transcription does not wait on a 3 GB
download. The application does the same thing automatically on first use; this
just moves it to install time.

    python -m tiktuv.fetch_model                        # the default model
    python -m tiktuv.fetch_model ivrit-ai/whisper-large-v3
"""

from __future__ import annotations

import contextlib
import sys
import time

from .asr.downloader import ModelDownloader
from .asr.registry import get_entry, is_downloaded
from .config import get_settings
from .errors import TiktuvError
from .logging_conf import configure_logging
from .schemas import ModelDownloadState


def _bar(fraction: float, width: int = 34) -> str:
    filled = int(round(fraction * width))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _human(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    settings = get_settings()
    model_id = args[0] if args else settings.default_model_id

    # A console progress bar is the whole point here, so keep logs quiet.
    configure_logging(level="WARNING", json_output=False, log_dir=None)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")

    try:
        entry = get_entry(model_id)
    except TiktuvError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 2

    if is_downloaded(entry.id, settings.model_cache_dir):
        print(f"{entry.id} is already downloaded in {settings.model_cache_dir}")
        return 0

    print(f"Model:    {entry.id}")
    print(f"Download: about {_human(entry.approx_download_bytes)}")
    print(f"Saved to: {settings.model_cache_dir}")
    print("This is the only step that uses the internet.\n")

    downloader = ModelDownloader(settings)
    try:
        downloader.start(entry.id)
    except TiktuvError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 2

    try:
        while downloader.is_running:
            status = downloader.status()
            print(
                f"\r{_bar(status.percent)} {status.percent * 100:5.1f}%  "
                f"{_human(status.downloaded_bytes)} / {_human(status.total_bytes)}   ",
                end="",
                flush=True,
            )
            time.sleep(0.5)
    except KeyboardInterrupt:
        downloader.cancel()
        print("\ncancelled; partly-downloaded files are kept and will resume")
        downloader.wait(timeout=30)
        return 130

    downloader.wait(timeout=30)
    status = downloader.status()
    print()

    if status.state is ModelDownloadState.READY:
        print(f"done: {entry.id} is ready in {settings.model_cache_dir}")
        return 0
    print(f"failed: {status.error or status.state.value}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
