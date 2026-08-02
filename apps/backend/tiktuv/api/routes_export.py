"""Export endpoints — one job, or every completed job at once."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ErrorCode, ExportError, TiktuvError
from ..exports import SUPPORTED_FORMATS, export_transcript
from ..logging_conf import get_logger
from ..schemas import ExportFormat, ExportRequest, ExportResult, JobStatus
from ..security import validate_output_dir
from .deps import AuthDep, StateDep

log = get_logger("api.export")

router = APIRouter(prefix="/api", tags=["export"])


class ExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    results: list[ExportResult]


class BulkExportRequest(ExportRequest):
    model_config = ConfigDict(extra="forbid")

    #: Empty means "every completed job that has a transcript".
    job_ids: list[str] = Field(default_factory=list)


class BulkExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exported: list[ExportResponse]
    failed: dict[str, str] = Field(default_factory=dict)


@router.get("/export/formats", response_model=list[str])
def list_formats(_auth: AuthDep) -> list[str]:
    return [fmt.value for fmt in SUPPORTED_FORMATS]


def _export_one(
    state: object, job_id: str, formats: list[ExportFormat], output_dir: str | None
) -> ExportResponse:
    transcript = state.transcripts.load(job_id)  # type: ignore[attr-defined]
    if transcript is None:
        raise TiktuvError(
            "this job has no transcript to export", code=ErrorCode.TRANSCRIPT_NOT_FOUND
        )
    job = state.jobs.get(job_id)  # type: ignore[attr-defined]

    directory = validate_output_dir(
        output_dir or job.settings.output_dir,
        state.settings.exports_dir,  # type: ignore[attr-defined]
    )
    stem = Path(job.filename).stem or "transcript"

    results = export_transcript(
        transcript, job.settings, formats=formats, output_dir=directory, stem=stem
    )
    state.jobs.set_outputs(  # type: ignore[attr-defined]
        job_id, {result.format.value: result.path for result in results}
    )
    return ExportResponse(job_id=job_id, results=results)


@router.post("/jobs/{job_id}/export", response_model=ExportResponse)
def export_job(
    job_id: str, payload: ExportRequest, state: StateDep, _auth: AuthDep
) -> ExportResponse:
    return _export_one(state, job_id, payload.formats, payload.output_dir)


@router.post("/export/bulk", response_model=BulkExportResponse)
def export_bulk(
    payload: BulkExportRequest, state: StateDep, _auth: AuthDep
) -> BulkExportResponse:
    """Export several jobs. One failure does not abort the rest."""
    job_ids = payload.job_ids or [
        job.id
        for job in state.jobs.list_jobs(limit=1000, status=JobStatus.COMPLETED)
        if job.has_transcript
    ]

    exported: list[ExportResponse] = []
    failed: dict[str, str] = {}
    for job_id in job_ids:
        try:
            exported.append(_export_one(state, job_id, payload.formats, payload.output_dir))
        except TiktuvError as exc:
            failed[job_id] = exc.message_he
            log.error("bulk export failed", extra={"job_id": job_id, "code": exc.code.value})
    return BulkExportResponse(exported=exported, failed=failed)


@router.get("/exports/download")
def download_export(path: str, state: StateDep, _auth: AuthDep) -> FileResponse:
    """Serve a previously generated export file.

    ``path`` is confined to the exports directory. Anything else — a traversal
    attempt, a symlink out, an absolute path elsewhere on the disk — is
    rejected before the file is opened.
    """
    from ..security import resolve_within

    resolved = resolve_within(path, [state.settings.exports_dir.resolve()])
    if not resolved.is_file():
        raise ExportError("the export file no longer exists", code=ErrorCode.EXPORT_FAILED)
    return FileResponse(resolved, filename=resolved.name)
