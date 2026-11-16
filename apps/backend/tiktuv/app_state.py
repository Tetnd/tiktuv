"""Wiring for the whole backend.

One container holds the settings, the stores, the model manager and the queue,
so routes depend on an explicit object rather than module-level globals — and
tests can build a fully isolated instance pointed at a temporary directory.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .asr.downloader import ModelDownloader
from .asr.registry import ModelManager
from .config import Settings
from .credentials import DeepSeekCredentials, SonioxCredentials
from .db import dispose_engine, init_engine
from .errors import FFmpegMissingError
from .events import Event, EventBus, EventType
from .importers.fetcher import UrlFetcher
from .jobs.pipeline import TranscriptionPipeline
from .jobs.queue import JobQueue, restore_queue
from .jobs.store import JobStore, TranscriptStore
from .logging_conf import get_logger
from .media.ffmpeg import FFmpegTools, locate_tools

log = get_logger("app_state")


@dataclass
class AppState:
    settings: Settings
    bus: EventBus
    transcripts: TranscriptStore
    jobs: JobStore
    models: ModelManager
    downloader: ModelDownloader
    fetcher: UrlFetcher
    queue: JobQueue
    credentials: SonioxCredentials
    deepseek: DeepSeekCredentials
    #: ``None`` when FFmpeg is missing. The app still starts — the preflight
    #: screen needs to run in order to *tell* the user it is missing.
    tools: FFmpegTools | None = None
    #: Background thread that finishes crash recovery after the server is up.
    recovery: threading.Thread | None = None

    def require_tools(self) -> FFmpegTools:
        if self.tools is None:
            self.tools = locate_tools(self.settings)
        return self.tools


def build_state(settings: Settings, bus: EventBus) -> AppState:
    settings.ensure_dirs()
    init_engine(settings.db_path)

    transcripts = TranscriptStore(settings)
    jobs = JobStore(settings, transcripts)
    credentials = SonioxCredentials(settings)
    deepseek = DeepSeekCredentials(settings)
    models = ModelManager(settings, credentials=credentials)

    downloader = ModelDownloader(settings)
    # Mirror download progress onto the SSE stream so the UI can show it live.
    downloader.add_listener(
        lambda status: bus.publish_threadsafe(
            Event(EventType.MODEL_STATUS, status.model_dump(mode="json"))
        )
    )

    tools: FFmpegTools | None
    try:
        tools = locate_tools(settings)
    except FFmpegMissingError as exc:
        tools = None
        log.error("FFmpeg not found at startup", extra={"detail": exc.detail})

    fetcher = UrlFetcher(settings, tools=tools)
    fetcher.add_listener(
        lambda status: bus.publish_threadsafe(
            Event(EventType.MEDIA_FETCH, status.model_dump(mode="json"))
        )
    )

    pipeline = TranscriptionPipeline(
        settings, models, transcripts, tools=tools, downloader=downloader
    )
    job_queue = JobQueue(settings, jobs, transcripts, models, bus, pipeline=pipeline)

    return AppState(
        settings=settings,
        bus=bus,
        transcripts=transcripts,
        jobs=jobs,
        models=models,
        downloader=downloader,
        fetcher=fetcher,
        queue=job_queue,
        credentials=credentials,
        deepseek=deepseek,
        tools=tools,
    )


def start_state(state: AppState) -> None:
    """Start accepting work, then finish crash recovery in the background.

    Recovery marks jobs left ``running`` by an unclean shutdown as failed and
    re-submits jobs that were still queued. It writes to SQLite and, on a cold
    start, that can take seconds — the window must not wait for it, so it runs
    after the server is already answering ``/api/health``. The UI is told over
    SSE when it finishes, so a recovered job flips to failed without a manual
    refresh.
    """
    state.queue.start()
    state.recovery = threading.Thread(
        target=_recover,
        args=(state,),
        name="tiktuv-recovery",
        daemon=True,
    )
    state.recovery.start()


def _recover(state: AppState) -> None:
    try:
        recovered = state.jobs.recover_orphans()
        if recovered:
            log.warning("marked orphaned jobs as failed", extra={"job_ids": recovered})
        restored = restore_queue(state.jobs, state.queue)
        if recovered or restored:
            # The job list the UI fetched on mount may show these jobs in their
            # pre-recovery state; a queue.changed event makes it re-sync.
            state.bus.publish_threadsafe(
                Event(EventType.QUEUE_CHANGED, {"reason": "recovery"})
            )
    except Exception:  # pragma: no cover - recovery must never take the app down
        log.exception("background recovery failed")


def stop_state(state: AppState) -> None:
    state.queue.shutdown()
    if state.recovery is not None:
        # Recovery writes to the engine this stops, so let it finish first.
        state.recovery.join(timeout=5.0)
    state.models.release()
    dispose_engine()
