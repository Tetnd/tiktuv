"""Transcript read/write and media playback for the editor."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from ..errors import ErrorCode, TiktuvError
from ..logging_conf import get_logger
from ..media.playback import ensure_playback_copy
from ..schemas import Transcript, UpdateSegmentsRequest
from ..security import is_within
from ..speakers import label_changes, relabel_analysis
from .deps import AuthDep, StateDep, get_state, require_token

log = get_logger("api.transcript")

router = APIRouter(prefix="/api/jobs", tags=["transcript"])

#: Playback is served in slices so seeking in a two-hour MP4 is instant.
_STREAM_CHUNK = 512 * 1024


@router.get("/{job_id}/transcript", response_model=Transcript)
def get_transcript(job_id: str, state: StateDep, _auth: AuthDep) -> Transcript:
    """Load a transcript, including partial results from a running job."""
    transcript = state.transcripts.load(job_id)
    if transcript is None:
        # Confirm the job exists so the client gets the right error.
        state.jobs.get(job_id)
        raise TiktuvError(
            "no transcript has been produced for this job yet",
            code=ErrorCode.TRANSCRIPT_NOT_FOUND,
        )
    state.jobs.touch_opened(job_id)
    return transcript


@router.put("/{job_id}/transcript", response_model=Transcript)
def update_transcript(
    job_id: str,
    payload: UpdateSegmentsRequest,
    state: StateDep,
    _auth: AuthDep,
) -> Transcript:
    """Save edits from the built-in editor.

    The editor autosaves, so this is called often. It replaces the segment
    list wholesale — simpler and safer than patching, and the payload for even
    a three-hour transcript is well under a megabyte.
    """
    existing = state.transcripts.load(job_id)
    if existing is None:
        job = state.jobs.get(job_id)
        existing = Transcript(
            job_id=job_id,
            language=job.settings.language.value,
            model_id=job.model_id,
            duration=job.duration,
            created_at=datetime.now(UTC),
        )

    # Renumber so ids stay dense and ordered after inserts and deletions.
    segments = [
        segment.model_copy(update={"id": index})
        for index, segment in enumerate(
            sorted(payload.segments, key=lambda s: (s.start, s.end))
        )
    ]
    updated = existing.model_copy(
        update={
            "segments": segments,
            "speakers": payload.speakers if payload.speakers is not None else existing.speakers,
            # An edited transcript is authoritative, never "partial" again.
            "partial": False,
        }
    )
    state.transcripts.save(updated)
    # A renamed speaker is renamed everywhere: the AI analysis was written with
    # the old labels and has to follow, or its tabs would still say "דובר 1".
    if payload.speakers is not None:
        renames = label_changes(existing.speakers, payload.speakers)
        if renames:
            analysis = state.transcripts.load_analysis(job_id)
            if analysis is not None:
                state.transcripts.save_analysis(relabel_analysis(analysis, renames))
    state.jobs.touch_opened(job_id)
    log.info("transcript saved", extra={"job_id": job_id, "segments": len(segments)})
    return updated


def _iter_file(path: Path, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(_STREAM_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/{job_id}/media")
def stream_media(
    job_id: str,
    request: Request,
    range_header: Annotated[str | None, Header(alias="range")] = None,
) -> StreamingResponse:
    """Serve the job's media with HTTP range support, for the player.

    The path comes from the job record, never from the request, so this cannot
    be pointed at an arbitrary file. It is re-validated against the managed
    media directory regardless.
    """
    state = get_state(request)
    # Browsers cannot set headers on <video src>; the token rides as a query
    # parameter here and is validated explicitly.
    _authorise_media_request(request)

    job = state.jobs.get(job_id)
    path = Path(job.media.path if job.media else "")
    if not path.is_file():
        path = state.jobs.get_working_path(job_id)
    if not path.is_file():
        raise TiktuvError("media file is missing", code=ErrorCode.MEDIA_CORRUPT)

    resolved = path.resolve()
    if not is_within(path, state.settings.media_dir):
        raise TiktuvError("media path is outside the managed directory",
                          code=ErrorCode.PATH_NOT_ALLOWED)

    media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return _ranged_response(resolved, range_header, media_type)


def _authorise_media_request(request: Request) -> None:
    """Players cannot set headers, so the token may ride as a query parameter."""
    token = request.query_params.get("token")
    if token:
        require_token(request, authorization=f"Bearer {token}", x_tiktuv_token=None)
    else:
        require_token(
            request,
            authorization=request.headers.get("authorization"),
            x_tiktuv_token=request.headers.get("x-tiktuv-token"),
        )


@router.get("/{job_id}/playback")
def stream_playback(
    job_id: str,
    request: Request,
    range_header: Annotated[str | None, Header(alias="range")] = None,
) -> StreamingResponse:
    """Serve the recording as audio on the transcript's own timeline.

    The editor plays this instead of the raw media, so the highlighted line and
    the sound cannot drift apart whatever container or codec the recording is
    in (see :mod:`tiktuv.media.playback`). Built on first use.
    """
    state = get_state(request)
    _authorise_media_request(request)

    job = state.jobs.get(job_id)
    source = Path(job.media.path if job.media else "")
    if not source.is_file():
        source = state.jobs.get_working_path(job_id)
    if not source.is_file():
        raise TiktuvError("media file is missing", code=ErrorCode.MEDIA_CORRUPT)
    if not is_within(source, state.settings.media_dir):
        raise TiktuvError(
            "media path is outside the managed directory", code=ErrorCode.PATH_NOT_ALLOWED
        )

    copy = ensure_playback_copy(
        state.require_tools(), source, state.settings.temp_dir, job_id
    )
    return _ranged_response(copy, range_header, "audio/wav")


def _ranged_response(
    resolved: Path, range_header: str | None, media_type: str
) -> StreamingResponse:
    """Stream a file with HTTP range support."""
    size = resolved.stat().st_size

    start, end = 0, size - 1
    status = 200
    if range_header and range_header.startswith("bytes="):
        raw = range_header.removeprefix("bytes=").split(",")[0]
        begin, _, finish = raw.partition("-")
        try:
            if begin:
                start = int(begin)
                end = int(finish) if finish else size - 1
            elif finish:  # suffix range: last N bytes
                start = max(0, size - int(finish))
        except ValueError:
            start, end = 0, size - 1
        start = max(0, min(start, size - 1))
        end = max(start, min(end, size - 1))
        status = 206

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    return StreamingResponse(
        _iter_file(resolved, start, end),
        status_code=status,
        media_type=media_type,
        headers=headers,
    )
