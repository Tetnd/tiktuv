"""Background job queue.

A small fixed pool of worker threads (one by default — concurrent GPU jobs
exhaust VRAM) pulls job ids off a bounded queue and runs the pipeline. Threads
rather than processes because the expensive resource is the loaded model, and
sharing it across processes would mean loading it more than once.

The API never blocks on transcription: it enqueues an id and returns.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from datetime import UTC, datetime
from pathlib import Path

from ..asr.registry import ModelManager
from ..config import Settings
from ..errors import ErrorCode, JobCancelled, JobError, TiktuvError
from ..events import Event, EventBus, EventType
from ..logging_conf import get_logger
from ..media.audio import CancelToken
from ..schemas import JobDetail, JobStage, JobStatus
from .pipeline import TranscriptionPipeline
from .store import JobStore, TranscriptStore

log = get_logger("jobs.queue")

#: How often progress is written to the database. Every window would hammer
#: SQLite on a long file; the SSE stream is unthrottled and drives the UI.
_DB_PROGRESS_INTERVAL_SECONDS = 2.0


class JobQueue:
    """Bounded work queue with cancellation and graceful shutdown."""

    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        transcripts: TranscriptStore,
        model_manager: ModelManager,
        bus: EventBus,
        *,
        pipeline: TranscriptionPipeline | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._transcripts = transcripts
        self._models = model_manager
        self._bus = bus
        self._pipeline = pipeline or TranscriptionPipeline(settings, model_manager, transcripts)

        self._queue: queue.Queue[str] = queue.Queue(maxsize=settings.max_queue_length)
        self._workers: list[threading.Thread] = []
        self._shutdown = threading.Event()
        self._lock = threading.Lock()
        #: job_id -> token, for jobs that are queued or running.
        self._tokens: dict[str, CancelToken] = {}
        self._running: set[str] = set()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._workers:
            return
        self._shutdown.clear()
        for index in range(max(1, self._settings.max_concurrent_jobs)):
            thread = threading.Thread(
                target=self._worker_loop, name=f"tiktuv-worker-{index}", daemon=True
            )
            thread.start()
            self._workers.append(thread)
        log.info("job queue started", extra={"workers": len(self._workers)})

    def shutdown(self, *, timeout: float = 10.0) -> None:
        """Stop accepting work and ask running jobs to stop.

        Cancelled jobs keep their partial transcript, and ``resume_offset``
        lets a retry pick up where it left off.
        """
        self._shutdown.set()
        with self._lock:
            for token in self._tokens.values():
                token.cancel()
        for _ in self._workers:
            # Sentinel to wake a blocked worker. A full queue means the
            # workers are already busy and will see the shutdown flag anyway.
            with contextlib.suppress(queue.Full):  # pragma: no cover - draining
                self._queue.put_nowait("")
        for thread in self._workers:
            thread.join(timeout=timeout)
        self._workers.clear()
        self._models.release()
        log.info("job queue stopped")

    # ------------------------------------------------------------ enqueueing
    def submit(self, job_id: str) -> None:
        if self._shutdown.is_set():
            raise JobError("the application is shutting down", code=ErrorCode.JOB_INVALID_STATE)
        with self._lock:
            if job_id in self._tokens:
                raise JobError(
                    "job is already queued or running", code=ErrorCode.JOB_INVALID_STATE
                )
            self._tokens[job_id] = CancelToken()
        try:
            self._queue.put_nowait(job_id)
        except queue.Full:
            with self._lock:
                self._tokens.pop(job_id, None)
            raise JobError(
                f"the queue is full ({self._settings.max_queue_length} jobs)",
                code=ErrorCode.JOB_INVALID_STATE,
            ) from None
        self._emit(EventType.QUEUE_CHANGED, {"queued": self._queue.qsize()})

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. Returns False if the job was not active."""
        with self._lock:
            token = self._tokens.get(job_id)
        if token is None:
            return False
        token.cancel()
        log.info("cancellation requested", extra={"job_id": job_id})
        return True

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._tokens

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._running)

    # ------------------------------------------------------------- internals
    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if not job_id:  # shutdown sentinel
                    return
                self._run_one(job_id)
            finally:
                self._queue.task_done()

    def _run_one(self, job_id: str) -> None:
        with self._lock:
            token = self._tokens.get(job_id)
            if token is None:
                return
            self._running.add(job_id)

        try:
            job = self._store.get(job_id)
        except TiktuvError as exc:
            log.error("could not load job", extra={"job_id": job_id, "error": str(exc)})
            self._finish(job_id)
            return

        if token.cancelled:
            self._store.mark_cancelled(job_id)
            self._emit(EventType.JOB_CANCELLED, {"job_id": job_id})
            self._finish(job_id)
            return

        self._store.mark_running(job_id)
        self._emit(
            EventType.JOB_PROGRESS,
            {"job_id": job_id, "status": JobStatus.RUNNING.value, "stage": JobStage.PENDING.value,
             "percent": 0.0},
        )

        last_db_write = 0.0
        started_at = datetime.now(UTC)

        def on_progress(
            stage: JobStage, percent: float, processed: float, message: str | None
        ) -> None:
            nonlocal last_db_write
            elapsed = (datetime.now(UTC) - started_at).total_seconds()
            eta = None
            if percent > 0.02 and stage is not JobStage.DONE:
                eta = max(0.0, elapsed * (1 - percent) / percent)

            # SSE every time — cheap, and it is what the UI renders.
            self._emit(
                EventType.JOB_PROGRESS,
                {
                    "job_id": job_id,
                    "status": JobStatus.RUNNING.value,
                    "stage": stage.value,
                    "percent": round(percent, 4),
                    "processed_seconds": round(processed, 2),
                    "total_seconds": round(job.duration, 2),
                    "eta_seconds": round(eta, 1) if eta is not None else None,
                    "message": message,
                },
            )
            # Database less often, and always at a stage change.
            if elapsed - last_db_write >= _DB_PROGRESS_INTERVAL_SECONDS or stage in (
                JobStage.DONE,
                JobStage.EXPORTING,
                JobStage.LOADING_MODEL,
            ):
                last_db_write = elapsed
                self._store.update_progress(
                    job_id,
                    stage=stage,
                    percent=percent,
                    processed_seconds=processed,
                    message=message,
                    # Only committed audio counts as resumable.
                    resume_offset=processed if stage is JobStage.TRANSCRIBING else None,
                )

        try:
            resume_from = self._store.resume_offset(job_id)
            media_path = Path(job.media.path if job.media else "")
            if not media_path.is_file():
                media_path = self._store.get_working_path(job_id)

            result = self._pipeline.run(
                job,
                media_path,
                cancel=token,
                on_progress=on_progress,
                resume_from=resume_from,
            )
        except JobCancelled:
            self._store.mark_cancelled(job_id)
            self._models.cleanup_after_failure()
            self._emit(EventType.JOB_CANCELLED, {"job_id": job_id})
            log.info("job cancelled", extra={"job_id": job_id})
        except TiktuvError as exc:
            self._store.mark_failed(job_id, exc, redact=self._settings.redact_paths_in_errors)
            self._models.cleanup_after_failure()
            self._emit(
                EventType.JOB_FAILED,
                {
                    "job_id": job_id,
                    "error": exc.to_payload(redact=self._settings.redact_paths_in_errors),
                },
            )
            log.error("job failed", extra={"job_id": job_id, "code": exc.code.value})
        except Exception as exc:  # noqa: BLE001 - never let a worker die silently
            wrapped = TiktuvError(str(exc), code=ErrorCode.INTERNAL)
            self._store.mark_failed(job_id, wrapped, redact=self._settings.redact_paths_in_errors)
            self._models.cleanup_after_failure()
            self._emit(
                EventType.JOB_FAILED,
                {
                    "job_id": job_id,
                    "error": wrapped.to_payload(redact=self._settings.redact_paths_in_errors),
                },
            )
            log.exception("unexpected job failure", extra={"job_id": job_id})
        else:
            self._store.mark_completed(job_id, result.exports)
            self._emit(
                EventType.JOB_COMPLETED,
                {
                    "job_id": job_id,
                    "segments": len(result.transcript.segments),
                    "exports": result.exports,
                },
            )
            log.info(
                "job completed",
                extra={"job_id": job_id, "segments": len(result.transcript.segments)},
            )
        finally:
            self._finish(job_id)

    def _finish(self, job_id: str) -> None:
        with self._lock:
            self._tokens.pop(job_id, None)
            self._running.discard(job_id)
        self._emit(EventType.QUEUE_CHANGED, {"queued": self._queue.qsize()})

    def _emit(self, event_type: EventType, data: dict[str, object]) -> None:
        self._bus.publish_threadsafe(Event(event_type, data))


def restore_queue(store: JobStore, job_queue: JobQueue) -> list[str]:
    """Re-submit jobs that were still queued when the app last closed."""
    restored: list[str] = []
    for job_id in store.queued_ids():
        try:
            job_queue.submit(job_id)
        except JobError as exc:
            log.warning("could not restore job", extra={"job_id": job_id, "error": str(exc)})
            continue
        restored.append(job_id)
    if restored:
        log.info("restored queued jobs", extra={"count": len(restored)})
    return restored


__all__ = ["JobQueue", "JobDetail", "restore_queue"]
