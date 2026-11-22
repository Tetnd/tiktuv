"""Export endpoints — one job, or every completed job at once."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ErrorCode, ExportError, TiktuvError
from ..exports import ANALYSIS_FORMATS, SUPPORTED_FORMATS

# Aliased: the route below is also called ``export_job``.
from ..exports import export_job as write_export_files
from ..logging_conf import get_logger
from ..schemas import (
    ExportFormat,
    ExportRequest,
    ExportResult,
    ExportSection,
    JobStatus,
)
from ..security import validate_output_dir
from .deps import AuthDep, StateDep, require_token

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


class SectionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: ExportSection
    #: The formats this section can be written in.
    formats: list[ExportFormat]


@router.get("/export/sections", response_model=list[SectionInfo])
def list_sections(_auth: AuthDep) -> list[SectionInfo]:
    """Which parts of a job can be exported, and in which formats."""
    return [
        SectionInfo(
            section=section,
            formats=(
                list(SUPPORTED_FORMATS)
                if section is ExportSection.TRANSCRIPT
                else list(ANALYSIS_FORMATS)
            ),
        )
        for section in ExportSection
    ]


def _export_one(
    state: object,
    job_id: str,
    formats: list[ExportFormat],
    output_dir: str | None,
    sections: list[ExportSection] | None = None,
) -> ExportResponse:
    sections = list(sections or [ExportSection.TRANSCRIPT])
    transcript = state.transcripts.load(job_id)  # type: ignore[attr-defined]
    if transcript is None:
        raise TiktuvError(
            "this job has no transcript to export", code=ErrorCode.TRANSCRIPT_NOT_FOUND
        )
    job = state.jobs.get(job_id)  # type: ignore[attr-defined]

    # The analysis tabs come from a separate store, and are only loaded when
    # one of them was actually asked for.
    analysis = None
    if any(section is not ExportSection.TRANSCRIPT for section in sections):
        analysis = state.transcripts.load_analysis(job_id)  # type: ignore[attr-defined]
        if analysis is None:
            raise TiktuvError(
                "this job has no analysis to export yet",
                code=ErrorCode.TRANSCRIPT_NOT_FOUND,
            )

    directory = validate_output_dir(
        output_dir or job.settings.output_dir,
        state.settings.exports_dir,  # type: ignore[attr-defined]
    )
    stem = Path(job.filename).stem or "transcript"

    results = write_export_files(
        transcript,
        job.settings,
        sections=sections,
        formats=formats,
        output_dir=directory,
        stem=stem,
        analysis=analysis,
    )
    # The transcript keeps its bare format key so older readers of the job
    # record still find it; a section file is keyed by section and format.
    state.jobs.set_outputs(  # type: ignore[attr-defined]
        job_id,
        {_output_key(result): result.path for result in results},
    )
    return ExportResponse(job_id=job_id, results=results)


def _output_key(result: ExportResult) -> str:
    if result.section is ExportSection.TRANSCRIPT:
        return result.format.value
    return f"{result.section.value}:{result.format.value}"


@router.post("/jobs/{job_id}/export", response_model=ExportResponse)
def export_job(
    job_id: str, payload: ExportRequest, state: StateDep, _auth: AuthDep
) -> ExportResponse:
    return _export_one(
        state, job_id, payload.formats, payload.output_dir, payload.sections
    )


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
            exported.append(
                _export_one(
                    state, job_id, payload.formats, payload.output_dir, payload.sections
                )
            )
        except TiktuvError as exc:
            failed[job_id] = exc.message_he
            log.error("bulk export failed", extra={"job_id": job_id, "code": exc.code.value})
    return BulkExportResponse(exported=exported, failed=failed)


@router.get("/exports/download")
def download_export(path: str, request: Request, state: StateDep) -> FileResponse:
    """Serve a previously generated export file.

    ``path`` is confined to the exports directory. Anything else — a traversal
    attempt, a symlink out, an absolute path elsewhere on the disk — is
    rejected before the file is opened.

    A plain ``<a download>`` cannot send headers, so the session token is also
    accepted as a query parameter, exactly like the media and SSE endpoints.
    """
    token = request.query_params.get("token")
    if token:
        require_token(request, authorization=f"Bearer {token}", x_tiktuv_token=None)
    else:
        require_token(
            request,
            authorization=request.headers.get("authorization"),
            x_tiktuv_token=request.headers.get("x-tiktuv-token"),
        )

    from ..security import resolve_within

    resolved = resolve_within(path, [state.settings.exports_dir.resolve()])
    if not resolved.is_file():
        raise ExportError("the export file no longer exists", code=ErrorCode.EXPORT_FAILED)
    return FileResponse(resolved, filename=resolved.name)
