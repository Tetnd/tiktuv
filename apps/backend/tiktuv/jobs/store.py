"""Job and transcript persistence.

Job *metadata* lives in SQLite. Transcript *content* lives in one JSON file per
job. Keeping them apart means a partial transcript can be flushed cheaply and
atomically while a job runs, and a database problem never destroys text the
user has already waited an hour for.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from ..config import Settings
from ..db import session_scope
from ..errors import ErrorCode, JobError, TiktuvError
from ..logging_conf import get_logger
from ..models_db import JobRecord, utcnow
from ..schemas import (
    ErrorPayload,
    JobDetail,
    JobStage,
    JobStatus,
    JobSummary,
    MediaInfo,
    Segment,
    Transcript,
    TranscriptionSettings,
)

log = get_logger("jobs.store")

TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})


# --------------------------------------------------------------------------
# Transcript files
# --------------------------------------------------------------------------
class TranscriptStore:
    """Atomic JSON transcript files, one per job."""

    def __init__(self, settings: Settings) -> None:
        self._dir = settings.transcripts_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, job_id: str) -> Path:
        # job_id is always a server-generated UUID, never user input.
        return self._dir / f"{job_id}.json"

    def exists(self, job_id: str) -> bool:
        return self.path_for(job_id).is_file()

    def save(self, transcript: Transcript) -> Path:
        """Write atomically: temp file in the same directory, then replace.

        ``os.replace`` is atomic on both NTFS and POSIX, so a crash mid-write
        leaves the previous good transcript intact rather than a truncated one.
        """
        path = self.path_for(transcript.job_id)
        transcript = transcript.model_copy(update={"updated_at": datetime.now(UTC)})
        payload = transcript.model_dump_json(indent=2)

        # Deliberately not a `with` statement: the file must outlive being
        # closed so os.replace can move it over the target atomically. Cleanup
        # on failure is handled explicitly in the except branch below.
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w",
            encoding="utf-8",
            dir=self._dir,
            prefix=f".{transcript.job_id}-",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, path)
        except OSError as exc:
            Path(handle.name).unlink(missing_ok=True)
            raise TiktuvError(
                f"could not save transcript: {exc}", code=ErrorCode.INSUFFICIENT_DISK
            ) from exc
        return path

    def load(self, job_id: str) -> Transcript | None:
        path = self.path_for(job_id)
        if not path.is_file():
            return None
        try:
            return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("corrupt transcript file", extra={"job_id": job_id, "error": str(exc)})
            return None

    def delete(self, job_id: str) -> bool:
        path = self.path_for(job_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def save_partial(
        self,
        job_id: str,
        segments: list[Segment],
        *,
        language: str,
        model_id: str,
        duration: float,
    ) -> None:
        """Snapshot in-progress results so a crash is not total loss."""
        self.save(
            Transcript(
                job_id=job_id,
                language=language,
                model_id=model_id,
                duration=duration,
                segments=segments,
                partial=True,
                created_at=datetime.now(UTC),
            )
        )


# --------------------------------------------------------------------------
# Job repository
# --------------------------------------------------------------------------
class JobStore:
    """CRUD over :class:`~tiktuv.models_db.JobRecord`."""

    def __init__(self, settings: Settings, transcripts: TranscriptStore) -> None:
        self._settings = settings
        self._transcripts = transcripts

    # -- create ------------------------------------------------------------
    def create(
        self,
        *,
        source_path: Path,
        working_path: Path,
        media: MediaInfo,
        settings: TranscriptionSettings,
    ) -> JobDetail:
        job_id = str(uuid.uuid4())
        with session_scope() as session:
            record = JobRecord(
                id=job_id,
                filename=media.filename,
                source_path=str(source_path),
                working_path=str(working_path),
                size_bytes=media.size_bytes,
                duration=media.duration,
                status=JobStatus.QUEUED.value,
                stage=JobStage.PENDING.value,
                model_id=settings.model_id,
            )
            record.settings = settings
            record.media = media
            session.add(record)
            session.flush()
            detail = record.to_detail(has_transcript=False)
        log.info("job created", extra={"job_id": job_id, "duration": media.duration})
        return detail

    # -- read --------------------------------------------------------------
    def get(self, job_id: str) -> JobDetail:
        with session_scope() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                raise JobError(f"job {job_id} not found", code=ErrorCode.JOB_NOT_FOUND)
            return record.to_detail(has_transcript=self._transcripts.exists(job_id))

    def get_working_path(self, job_id: str) -> Path:
        with session_scope() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                raise JobError(f"job {job_id} not found", code=ErrorCode.JOB_NOT_FOUND)
            return Path(record.working_path)

    def list_jobs(
        self, *, limit: int = 200, status: JobStatus | None = None
    ) -> list[JobSummary]:
        with session_scope() as session:
            statement = select(JobRecord).order_by(JobRecord.created_at.desc()).limit(limit)
            if status is not None:
                statement = statement.where(JobRecord.status == status.value)
            records = session.scalars(statement).all()
            return [
                record.to_summary(has_transcript=self._transcripts.exists(record.id))
                for record in records
            ]

    def queued_ids(self) -> list[str]:
        with session_scope() as session:
            statement = (
                select(JobRecord.id)
                .where(JobRecord.status == JobStatus.QUEUED.value)
                .order_by(JobRecord.created_at.asc())
            )
            return list(session.scalars(statement).all())

    # -- update ------------------------------------------------------------
    def mark_running(self, job_id: str) -> None:
        with session_scope() as session:
            record = self._require(session, job_id)
            record.status = JobStatus.RUNNING.value
            record.started_at = record.started_at or utcnow()
            record.error_json = None

    def update_progress(
        self,
        job_id: str,
        *,
        stage: JobStage,
        percent: float,
        processed_seconds: float,
        message: str | None = None,
        resume_offset: float | None = None,
    ) -> None:
        with session_scope() as session:
            record = self._require(session, job_id)
            record.stage = stage.value
            record.progress_percent = max(0.0, min(1.0, percent))
            record.processed_seconds = processed_seconds
            record.progress_message = message
            if resume_offset is not None:
                record.resume_offset = resume_offset

    def mark_completed(self, job_id: str, output_paths: dict[str, str] | None = None) -> None:
        with session_scope() as session:
            record = self._require(session, job_id)
            record.status = JobStatus.COMPLETED.value
            record.stage = JobStage.DONE.value
            record.progress_percent = 1.0
            record.processed_seconds = record.duration
            record.progress_message = None
            record.finished_at = utcnow()
            record.error_json = None
            if output_paths:
                merged = record.output_paths
                merged.update(output_paths)
                record.output_paths = merged

    def mark_failed(self, job_id: str, error: TiktuvError, *, redact: bool) -> None:
        with session_scope() as session:
            record = self._require(session, job_id)
            record.status = JobStatus.FAILED.value
            record.finished_at = utcnow()
            record.error = ErrorPayload(**error.to_payload(redact=redact))

    def mark_cancelled(self, job_id: str) -> None:
        with session_scope() as session:
            record = self._require(session, job_id)
            record.status = JobStatus.CANCELLED.value
            record.finished_at = utcnow()
            record.progress_message = None

    def requeue(self, job_id: str, *, reset_progress: bool = True) -> JobDetail:
        with session_scope() as session:
            record = self._require(session, job_id)
            if record.status == JobStatus.RUNNING.value:
                raise JobError(
                    "cannot retry a running job", code=ErrorCode.JOB_INVALID_STATE
                )
            record.status = JobStatus.QUEUED.value
            record.stage = JobStage.PENDING.value
            record.error_json = None
            record.finished_at = None
            record.started_at = None
            if reset_progress:
                record.progress_percent = 0.0
                record.processed_seconds = 0.0
                record.resume_offset = 0.0
            return record.to_detail(has_transcript=self._transcripts.exists(job_id))

    def touch_opened(self, job_id: str) -> None:
        with session_scope() as session:
            record = self._require(session, job_id)
            record.last_opened_at = utcnow()

    def set_outputs(self, job_id: str, outputs: dict[str, str]) -> None:
        with session_scope() as session:
            record = self._require(session, job_id)
            merged = record.output_paths
            merged.update(outputs)
            record.output_paths = merged

    def resume_offset(self, job_id: str) -> float:
        with session_scope() as session:
            record = self._require(session, job_id)
            return record.resume_offset

    # -- delete ------------------------------------------------------------
    def delete(self, job_id: str, *, delete_files: bool) -> None:
        """Remove the history row. Generated files go only if asked.

        Deleting history and deleting output files are separate user actions;
        conflating them loses work the user meant to keep.
        """
        with session_scope() as session:
            record = self._require(session, job_id)
            if record.status == JobStatus.RUNNING.value:
                raise JobError(
                    "cannot delete a running job; cancel it first",
                    code=ErrorCode.JOB_INVALID_STATE,
                )
            working = Path(record.working_path)
            outputs = list(record.output_paths.values())
            session.delete(record)

        self._transcripts.delete(job_id)
        # The imported working copy is ours; always reclaim it.
        if working.is_file() and self._is_managed(working):
            working.unlink(missing_ok=True)
        if delete_files:
            for output in outputs:
                path = Path(output)
                if path.is_file():
                    path.unlink(missing_ok=True)
        log.info("job deleted", extra={"job_id": job_id, "deleted_files": delete_files})

    def delete_outputs(self, job_id: str) -> int:
        """Delete generated exports, keeping the history record."""
        removed = 0
        with session_scope() as session:
            record = self._require(session, job_id)
            for output in record.output_paths.values():
                path = Path(output)
                if path.is_file():
                    path.unlink(missing_ok=True)
                    removed += 1
            record.output_paths = {}
        return removed

    def clear_finished(self) -> int:
        """Remove every completed/failed/cancelled job. Keeps output files."""
        with session_scope() as session:
            statement = select(JobRecord).where(
                JobRecord.status.in_([status.value for status in TERMINAL_STATUSES])
            )
            records = list(session.scalars(statement).all())
            ids = [record.id for record in records]
            working_paths = [Path(record.working_path) for record in records]
            for record in records:
                session.delete(record)

        for job_id in ids:
            self._transcripts.delete(job_id)
        for path in working_paths:
            if path.is_file() and self._is_managed(path):
                path.unlink(missing_ok=True)
        return len(ids)

    # -- recovery ----------------------------------------------------------
    def recover_orphans(self) -> list[str]:
        """Fix jobs left ``running`` by a crash or a forced quit.

        The partial transcript on disk is kept, and ``resume_offset`` already
        records how far the job got, so a retry restarts from the last
        committed window instead of from zero.
        """
        recovered: list[str] = []
        with session_scope() as session:
            statement = select(JobRecord).where(JobRecord.status == JobStatus.RUNNING.value)
            for record in session.scalars(statement).all():
                record.status = JobStatus.FAILED.value
                record.finished_at = utcnow()
                record.error = ErrorPayload(
                    code=ErrorCode.INTERNAL.value,
                    message="העבודה הופסקה כשהיישום נסגר.",
                    remedy="לחצו על 'נסה שוב' כדי להמשיך מהנקודה האחרונה שנשמרה.",
                    detail="job was running when the application stopped",
                    context={"resume_offset": record.resume_offset},
                )
                recovered.append(record.id)
        if recovered:
            log.warning("recovered orphaned jobs", extra={"count": len(recovered)})
        return recovered

    # -- helpers -----------------------------------------------------------
    def _is_managed(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return resolved.is_relative_to(self._settings.media_dir.resolve())

    @staticmethod
    def _require(session: object, job_id: str) -> JobRecord:
        record: JobRecord | None = session.get(JobRecord, job_id)  # type: ignore[attr-defined]
        if record is None:
            raise JobError(f"job {job_id} not found", code=ErrorCode.JOB_NOT_FOUND)
        return record


# --------------------------------------------------------------------------
# Temp / storage maintenance
# --------------------------------------------------------------------------
def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def purge_temp(settings: Settings) -> int:
    """Delete everything under ``temp/``. Returns bytes reclaimed."""
    temp = settings.temp_dir
    reclaimed = directory_size(temp)
    if temp.exists():
        for entry in temp.iterdir():
            try:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
            except OSError as exc:  # pragma: no cover - races with the worker
                log.warning("could not remove temp entry", extra={"error": str(exc)})
    temp.mkdir(parents=True, exist_ok=True)
    log.info("purged temp directory", extra={"bytes": reclaimed})
    return reclaimed


def free_disk_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:  # pragma: no cover - unusual filesystems
        return 0


def load_json(path: Path) -> dict[str, object] | None:
    try:
        data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data


def as_sequence(value: Sequence[str] | None) -> list[str]:
    return list(value) if value else []
