"""Job lifecycle: create, list, inspect, cancel, retry, delete."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from ..errors import ErrorCode, JobError, MediaError
from ..events import Event, EventType
from ..logging_conf import get_logger
from ..media.probe import probe_media
from ..schemas import (
    CreateJobRequest,
    JobDetail,
    JobStatus,
    JobSummary,
)
from ..security import sanitize_filename
from .deps import AuthDep, StateDep

log = get_logger("api.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    detail: str = ""


class ClearResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    removed: int


@router.post("", response_model=JobDetail, status_code=201)
def create_job(payload: CreateJobRequest, state: StateDep, _auth: AuthDep) -> JobDetail:
    """Create a job from a previously imported media token."""
    # The token is a bare filename inside our media directory. Sanitizing it
    # and rejecting any name that changes guarantees no traversal is possible.
    token = payload.media_token
    if token != sanitize_filename(token) or "/" in token or "\\" in token:
        raise MediaError("invalid media token", code=ErrorCode.INVALID_FILENAME)

    working = state.settings.media_dir / token
    if not working.is_file():
        raise MediaError(
            "the imported media file no longer exists; import it again",
            code=ErrorCode.MEDIA_CORRUPT,
        )

    media = probe_media(working, state.require_tools(), state.settings)
    job = state.jobs.create(
        source_path=working,
        working_path=working,
        media=media,
        settings=payload.settings,
    )

    state.bus.publish_threadsafe(
        Event(EventType.JOB_CREATED, {"job_id": job.id, "filename": job.filename})
    )
    if payload.autostart:
        state.queue.submit(job.id)
    return job


@router.get("", response_model=list[JobSummary])
def list_jobs(
    state: StateDep,
    _auth: AuthDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    status: JobStatus | None = None,
) -> list[JobSummary]:
    return state.jobs.list_jobs(limit=limit, status=status)


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: str, state: StateDep, _auth: AuthDep) -> JobDetail:
    return state.jobs.get(job_id)


@router.post("/{job_id}/start", response_model=ActionResult)
def start_job(job_id: str, state: StateDep, _auth: AuthDep) -> ActionResult:
    job = state.jobs.get(job_id)
    if job.status is JobStatus.RUNNING or state.queue.is_active(job_id):
        raise JobError("job is already running", code=ErrorCode.JOB_INVALID_STATE)
    if job.status is not JobStatus.QUEUED:
        state.jobs.requeue(job_id, reset_progress=False)
    state.queue.submit(job_id)
    return ActionResult(ok=True, detail="job queued")


@router.post("/{job_id}/cancel", response_model=ActionResult)
def cancel_job(job_id: str, state: StateDep, _auth: AuthDep) -> ActionResult:
    job = state.jobs.get(job_id)
    if not state.queue.cancel(job_id):
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            # Queued but the worker never picked it up (for example after a
            # restore); mark it directly.
            state.jobs.mark_cancelled(job_id)
            state.bus.publish_threadsafe(
                Event(EventType.JOB_CANCELLED, {"job_id": job_id})
            )
            return ActionResult(ok=True, detail="job cancelled")
        raise JobError("job is not running", code=ErrorCode.JOB_INVALID_STATE)
    return ActionResult(ok=True, detail="cancellation requested")


@router.post("/{job_id}/retry", response_model=JobDetail)
def retry_job(
    job_id: str,
    state: StateDep,
    _auth: AuthDep,
    resume: Annotated[bool, Query()] = True,
) -> JobDetail:
    """Re-run a failed or cancelled job.

    With ``resume=true`` (the default) the job continues from the last window
    that was committed to the partial transcript, which is what makes a crash
    during a three-hour recording survivable.
    """
    job = state.jobs.requeue(job_id, reset_progress=not resume)
    state.queue.submit(job_id)
    return job


@router.delete("/{job_id}", response_model=ActionResult)
def delete_job(
    job_id: str,
    state: StateDep,
    _auth: AuthDep,
    delete_files: Annotated[bool, Query()] = False,
) -> ActionResult:
    state.jobs.delete(job_id, delete_files=delete_files)
    state.bus.publish_threadsafe(Event(EventType.JOB_DELETED, {"job_id": job_id}))
    return ActionResult(ok=True, detail="job deleted")


@router.delete("/{job_id}/outputs", response_model=ClearResult)
def delete_job_outputs(job_id: str, state: StateDep, _auth: AuthDep) -> ClearResult:
    """Delete exported files but keep the history entry and the transcript."""
    return ClearResult(removed=state.jobs.delete_outputs(job_id))


@router.post("/clear/completed", response_model=ClearResult)
def clear_completed(state: StateDep, _auth: AuthDep) -> ClearResult:
    removed = state.jobs.clear_finished()
    state.bus.publish_threadsafe(Event(EventType.QUEUE_CHANGED, {"cleared": removed}))
    return ClearResult(removed=removed)
