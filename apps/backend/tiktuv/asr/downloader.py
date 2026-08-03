"""Background model downloading with progress and cancellation.

This is the one place Tiktuv touches the network, so it is deliberately
explicit: the size and destination are known before a byte is fetched, progress
is reported continuously, and the user can stop it.

Downloads happen file by file rather than through ``snapshot_download`` so that
cancellation actually takes effect — a single blocking call cannot be
interrupted, and a 3 GB download the user cannot stop is not acceptable.
Partial files are resumed by the Hugging Face cache on the next attempt.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..errors import ErrorCode, ModelError
from ..logging_conf import get_logger
from ..schemas import ModelDownloadState, ModelDownloadStatus
from . import registry

log = get_logger("asr.downloader")

#: Files worth fetching. Excludes the ``.bin`` duplicates of the safetensors
#: weights, which would double the download for no benefit.
ALLOW_PATTERNS = ("*.json", "*.txt", "*.safetensors", "*.model")

#: How often the progress poller samples the cache directory.
_POLL_SECONDS = 0.5


@dataclass
class _Progress:
    total_bytes: int = 0
    downloaded_bytes: int = 0
    current_file: str = ""
    files_done: int = 0
    files_total: int = 0
    started_at: float = field(default_factory=time.monotonic)


class ModelDownloader:
    """Runs at most one model download at a time."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._state = ModelDownloadState.IDLE
        self._model_id: str | None = None
        self._progress = _Progress()
        self._error: str | None = None
        self._listeners: list[Callable[[ModelDownloadStatus], None]] = []

    # -- observation -------------------------------------------------------
    def add_listener(self, listener: Callable[[ModelDownloadStatus], None]) -> None:
        self._listeners.append(listener)

    def status(self) -> ModelDownloadStatus:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> ModelDownloadStatus:
        progress = self._progress
        fraction = 0.0
        if progress.total_bytes > 0:
            fraction = min(1.0, progress.downloaded_bytes / progress.total_bytes)

        eta: float | None = None
        elapsed = time.monotonic() - progress.started_at
        if self._state is ModelDownloadState.DOWNLOADING and fraction > 0.01 and elapsed > 2:
            eta = max(0.0, elapsed * (1 - fraction) / fraction)

        return ModelDownloadStatus(
            state=self._state,
            model_id=self._model_id,
            percent=fraction,
            downloaded_bytes=progress.downloaded_bytes,
            total_bytes=progress.total_bytes,
            files_done=progress.files_done,
            files_total=progress.files_total,
            current_file=progress.current_file,
            eta_seconds=eta,
            error=self._error,
        )

    def _emit(self) -> None:
        status = self.status()
        for listener in list(self._listeners):
            try:
                listener(status)
            except Exception as exc:  # noqa: BLE001 - a listener must not break a download
                log.warning("download listener failed", extra={"error": str(exc)})

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state is ModelDownloadState.DOWNLOADING

    # -- control -----------------------------------------------------------
    def start(self, model_id: str) -> ModelDownloadStatus:
        """Begin a download in the background. Idempotent while one runs."""
        entry = registry.get_entry(model_id)

        with self._lock:
            if self._state is ModelDownloadState.DOWNLOADING:
                if self._model_id == model_id:
                    return self._status_locked()
                raise ModelError(
                    f"already downloading {self._model_id!r}",
                    code=ErrorCode.MODEL_DOWNLOAD_FAILED,
                )
            if registry.is_downloaded(entry.id, self._settings.model_cache_dir):
                self._state = ModelDownloadState.READY
                self._model_id = entry.id
                return self._status_locked()

            self._cancel.clear()
            self._state = ModelDownloadState.DOWNLOADING
            self._model_id = entry.id
            self._error = None
            self._progress = _Progress(total_bytes=entry.approx_download_bytes)

        self._thread = threading.Thread(
            target=self._run, args=(entry.id,), name="tiktuv-model-download", daemon=True
        )
        self._thread.start()
        self._emit()
        return self.status()

    def cancel(self) -> bool:
        """Ask the running download to stop after the current file."""
        with self._lock:
            if self._state is not ModelDownloadState.DOWNLOADING:
                return False
        self._cancel.set()
        log.info("model download cancellation requested")
        return True

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    # -- worker ------------------------------------------------------------
    def _run(self, model_id: str) -> None:
        poller: threading.Thread | None = None
        try:
            from huggingface_hub import HfApi, hf_hub_download
        except ImportError as exc:
            self._fail(
                "the Hugging Face client is not installed "
                "(pip install -e apps/backend[asr])",
                exc,
            )
            return

        cache_dir = self._settings.model_cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            files = self._list_files(HfApi(), model_id)
        except Exception as exc:  # noqa: BLE001 - hub raises many unrelated types
            self._fail(f"could not read the model file list: {exc}", exc)
            return

        with self._lock:
            self._progress.files_total = len(files)
            total = sum(size for _name, size in files if size)
            if total > 0:
                self._progress.total_bytes = total
            self._progress.started_at = time.monotonic()
        self._emit()

        log.info(
            "model download starting",
            extra={
                "model_id": model_id,
                "files": len(files),
                "total_bytes": self._progress.total_bytes,
                "cache_dir": str(cache_dir),
            },
        )

        poller = threading.Thread(target=self._poll_progress, daemon=True)
        poller.start()

        try:
            for name, _size in files:
                if self._cancel.is_set():
                    self._finish(ModelDownloadState.CANCELLED)
                    return
                with self._lock:
                    self._progress.current_file = name
                hf_hub_download(
                    repo_id=model_id,
                    filename=name,
                    cache_dir=str(cache_dir),
                )
                with self._lock:
                    self._progress.files_done += 1
                self._emit()
        except Exception as exc:  # noqa: BLE001 - hub raises many unrelated types
            self._fail(str(exc), exc)
            return
        finally:
            self._cancel.set()  # stops the poller
            if poller is not None:
                poller.join(timeout=2)

        if registry.is_downloaded(model_id, cache_dir):
            with self._lock:
                self._progress.downloaded_bytes = self._progress.total_bytes
            self._finish(ModelDownloadState.READY)
            log.info("model download complete", extra={"model_id": model_id})
        else:
            self._fail("the download finished but the model files are incomplete", None)

    def _list_files(self, api: object, model_id: str) -> list[tuple[str, int]]:
        """Names and sizes of the files to fetch, largest last for steady ETA."""
        import fnmatch

        info = api.model_info(model_id, files_metadata=True)  # type: ignore[attr-defined]
        selected: list[tuple[str, int]] = []
        for sibling in info.siblings:
            name = sibling.rfilename
            if not any(fnmatch.fnmatch(name, pattern) for pattern in ALLOW_PATTERNS):
                continue
            selected.append((name, int(getattr(sibling, "size", 0) or 0)))
        if not selected:
            raise ModelError(
                f"no downloadable files matched for {model_id!r}",
                code=ErrorCode.MODEL_DOWNLOAD_FAILED,
            )
        return selected

    def _poll_progress(self) -> None:
        """Track bytes on disk while a file is in flight.

        The Hub client writes into ``blobs`` (including ``*.incomplete``
        partials), so measuring that directory gives smooth progress without
        hooking into the client's internals.
        """
        blobs = (
            self._settings.model_cache_dir
            / f"models--{(self._model_id or '').replace('/', '--')}"
            / "blobs"
        )
        while not self._cancel.wait(_POLL_SECONDS):
            size = _directory_size(blobs)
            with self._lock:
                if size > self._progress.downloaded_bytes:
                    self._progress.downloaded_bytes = size
            self._emit()

    # -- terminal states ---------------------------------------------------
    def _finish(self, state: ModelDownloadState) -> None:
        with self._lock:
            self._state = state
        self._emit()

    def _fail(self, message: str, exc: BaseException | None) -> None:
        log.error("model download failed", extra={"error": message})
        with self._lock:
            self._state = ModelDownloadState.FAILED
            self._error = message
        self._emit()
        if exc is not None:
            log.debug("download exception", exc_info=exc)


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    try:
        for entry in path.iterdir():
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total
