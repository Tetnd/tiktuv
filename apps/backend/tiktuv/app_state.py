"""Wiring for the whole backend.

One container holds the settings, the stores, the model manager and the queue,
so routes depend on an explicit object rather than module-level globals — and
tests can build a fully isolated instance pointed at a temporary directory.
"""

from __future__ import annotations

from dataclasses import dataclass

from .asr.downloader import ModelDownloader
from .asr.registry import ModelManager
from .config import Settings
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
    #: ``None`` when FFmpeg is missing. The app still starts — the preflight
    #: screen needs to run in order to *tell* the user it is missing.
    tools: FFmpegTools | None = None

    def require_tools(self) -> FFmpegTools:
        if self.tools is None:
            self.tools = locate_tools(self.settings)
        return self.tools


def build_state(settings: Settings, bus: EventBus) -> AppState:
    settings.ensure_dirs()
    init_engine(settings.db_path)

    transcripts = TranscriptStore(settings)
    jobs = JobStore(settings, transcripts)
    models = ModelManager(settings)

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
        tools=tools,
    )


def start_state(state: AppState) -> None:
    """Recover from an unclean shutdown, then start accepting work."""
    recovered = state.jobs.recover_orphans()
    if recovered:
        log.warning("marked orphaned jobs as failed", extra={"job_ids": recovered})
    state.queue.start()
    restore_queue(state.jobs, state.queue)


def stop_state(state: AppState) -> None:
    state.queue.shutdown()
    state.models.release()
    dispose_engine()
