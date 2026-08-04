"""Fetch a recording from a URL into the media directory, with progress.

Runs one fetch at a time on a background thread so the API stays responsive
while a multi-hundred-megabyte recording downloads. Progress is reported the
same way model downloads are, and the result is an ordinary imported media file
— from that point on a URL import is indistinguishable from a dropped file.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..errors import ErrorCode, TiktuvError
from ..logging_conf import get_logger
from ..media.ffmpeg import FFmpegTools
from ..media.probe import probe_media
from ..schemas import MediaInfo, UrlFetchState, UrlFetchStatus
from ..security import unique_filename
from . import zoom

log = get_logger("importers.fetcher")

#: Read size while streaming. Large enough to keep the socket busy, small
#: enough that a cancel is honoured promptly.
_CHUNK_BYTES = 1 << 20


@dataclass
class _Progress:
    downloaded: int = 0
    total: int = 0
    started_at: float = field(default_factory=time.monotonic)


class UrlFetcher:
    """Downloads one recording at a time."""

    def __init__(self, settings: Settings, tools: FFmpegTools | None = None) -> None:
        self._settings = settings
        self._tools = tools
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = UrlFetchState.IDLE
        self._progress = _Progress()
        self._title = ""
        self._error: str | None = None
        self._media: MediaInfo | None = None
        self._token: str | None = None
        self._listeners: list[Callable[[UrlFetchStatus], None]] = []

    # -- observation -------------------------------------------------------
    def add_listener(self, listener: Callable[[UrlFetchStatus], None]) -> None:
        self._listeners.append(listener)

    def status(self) -> UrlFetchStatus:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> UrlFetchStatus:
        progress = self._progress
        fraction = 0.0
        if progress.total > 0:
            fraction = min(1.0, progress.downloaded / progress.total)

        eta: float | None = None
        elapsed = time.monotonic() - progress.started_at
        if self._state is UrlFetchState.DOWNLOADING and fraction > 0.01 and elapsed > 2:
            eta = max(0.0, elapsed * (1 - fraction) / fraction)

        return UrlFetchStatus(
            state=self._state,
            title=self._title,
            percent=fraction,
            downloaded_bytes=progress.downloaded,
            total_bytes=progress.total,
            eta_seconds=eta,
            error=self._error,
            media=self._media,
            media_token=self._token,
        )

    def _emit(self) -> None:
        status = self.status()
        for listener in list(self._listeners):
            try:
                listener(status)
            except Exception as exc:  # noqa: BLE001 - a listener must not break a fetch
                log.warning("fetch listener failed", extra={"error": str(exc)})

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state in (UrlFetchState.RESOLVING, UrlFetchState.DOWNLOADING)

    # -- control -----------------------------------------------------------
    def start(self, url: str, passcode: str | None = None) -> UrlFetchStatus:
        with self._lock:
            if self._state in (UrlFetchState.RESOLVING, UrlFetchState.DOWNLOADING):
                raise TiktuvError(
                    "a download is already in progress",
                    code=ErrorCode.JOB_INVALID_STATE,
                )
            self._cancel.clear()
            self._state = UrlFetchState.RESOLVING
            self._progress = _Progress()
            self._title = ""
            self._error = None
            self._media = None
            self._token = None

        self._thread = threading.Thread(
            target=self._run, args=(url, passcode), name="tiktuv-url-fetch", daemon=True
        )
        self._thread.start()
        self._emit()
        return self.status()

    def cancel(self) -> bool:
        with self._lock:
            if self._state not in (UrlFetchState.RESOLVING, UrlFetchState.DOWNLOADING):
                return False
        self._cancel.set()
        log.info("url fetch cancellation requested")
        return True

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # -- worker ------------------------------------------------------------
    def _run(self, url: str, passcode: str | None) -> None:
        destination: Path | None = None
        try:
            recording = zoom.resolve(url, passcode)
            with self._lock:
                self._title = recording.title
                self._state = UrlFetchState.DOWNLOADING
                self._progress = _Progress(total=recording.size_bytes)
            self._emit()

            destination = unique_filename(self._settings.media_dir, recording.filename)
            self._download(recording, destination)

            if self._cancel.is_set():
                destination.unlink(missing_ok=True)
                self._finish(UrlFetchState.CANCELLED)
                return

            # Validated exactly like a dropped file: a truncated or non-media
            # download must fail here rather than at transcription time.
            media = probe_media(destination, self._require_tools(), self._settings)
            with self._lock:
                self._media = media.model_copy(update={"filename": destination.name})
                self._token = destination.name
            self._finish(UrlFetchState.READY)
            log.info(
                "url import complete",
                extra={"bytes": self._progress.downloaded, "duration": media.duration},
            )
        except TiktuvError as exc:
            if destination is not None:
                destination.unlink(missing_ok=True)
            self._fail(exc.message_he if exc.detail == "" else exc.detail, exc)
        except Exception as exc:  # noqa: BLE001 - never let the thread die silently
            if destination is not None:
                destination.unlink(missing_ok=True)
            self._fail(str(exc), exc)

    def _require_tools(self) -> FFmpegTools:
        if self._tools is None:
            from ..media.ffmpeg import locate_tools

            self._tools = locate_tools(self._settings)
        return self._tools

    def _download(self, recording: zoom.ZoomRecording, destination: Path) -> None:
        import httpx

        headers = zoom.media_request_headers(recording)
        limit = self._settings.max_upload_bytes

        with httpx.Client(
            follow_redirects=True, timeout=60.0, cookies=recording.cookies
        ) as client, client.stream("GET", recording.media_url, headers=headers) as response:
            if response.status_code >= 400:
                raise TiktuvError(
                    f"the media server returned {response.status_code}",
                    code=ErrorCode.MEDIA_CORRUPT,
                    context={"hint": "the recording link may have expired"},
                )

            declared = response.headers.get("content-length")
            if declared and declared.isdigit():
                with self._lock:
                    self._progress.total = int(declared)

            with destination.open("wb") as handle:
                for chunk in response.iter_bytes(_CHUNK_BYTES):
                    if self._cancel.is_set():
                        return
                    handle.write(chunk)
                    with self._lock:
                        self._progress.downloaded += len(chunk)
                        downloaded = self._progress.downloaded
                    if downloaded > limit:
                        raise TiktuvError(
                            f"the recording exceeds the {limit} byte limit",
                            code=ErrorCode.MEDIA_TOO_LARGE,
                        )
                    self._emit()

    # -- terminal states ---------------------------------------------------
    def _finish(self, state: UrlFetchState) -> None:
        with self._lock:
            self._state = state
        self._emit()

    def _fail(self, message: str, exc: BaseException | None) -> None:
        log.error("url import failed", extra={"error": message})
        with self._lock:
            self._state = UrlFetchState.FAILED
            self._error = message
        self._emit()
        if exc is not None:
            log.debug("url import exception", exc_info=exc)
