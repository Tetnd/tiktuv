"""Media import.

Two ways in, both ending at the same place — a validated copy inside our media
directory, addressed by an opaque token:

* ``POST /api/media/import`` — the desktop shell sends a local path the user
  picked in a native dialog. No bytes cross the API.
* ``POST /api/media/upload`` — the browser sends the file. Used by the web dev
  server, where no native picker exists. Still entirely local.

Neither path lets the client name the destination, and neither trusts the
extension: every file is inspected by ffprobe before it is accepted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ErrorCode, MediaError, TiktuvError
from ..importers import zoom
from ..jobs.pipeline import import_media_file
from ..logging_conf import get_logger
from ..media.probe import probe_media
from ..schemas import MediaInfo, UrlFetchStatus
from ..security import sanitize_filename, unique_filename
from .deps import AuthDep, StateDep

log = get_logger("api.media")

router = APIRouter(prefix="/api/media", tags=["media"])

#: Read uploads in chunks so a 4 GB MP4 never lands in memory.
_UPLOAD_CHUNK = 4 * 1024 * 1024


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


class ImportedMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Filename inside the media directory. Used as ``media_token`` when
    #: creating a job. Opaque to the client: it cannot address anything else.
    token: str
    media: MediaInfo


def _finalise(state: object, working: Path, original: Path) -> ImportedMedia:
    """Probe the imported copy and clean it up if it turns out to be invalid."""
    try:
        media = probe_media(working, state.require_tools(), state.settings)  # type: ignore[attr-defined]
    except TiktuvError:
        working.unlink(missing_ok=True)
        raise
    # Report the user's own filename and path, not our internal copy's.
    media = media.model_copy(
        update={"filename": original.name or working.name, "path": str(working)}
    )
    log.info(
        "media imported",
        extra={"duration": round(media.duration, 2), "container": media.container},
    )
    return ImportedMedia(token=working.name, media=media)


@router.post("/import", response_model=ImportedMedia)
def import_local_file(
    payload: ImportRequest, state: StateDep, _auth: AuthDep
) -> ImportedMedia:
    """Import a file the user chose in a native dialog.

    The path is used to *read* one file and nothing else: it is never joined
    with client input, never written to, and never echoed back unredacted.
    """
    source = Path(payload.path).expanduser()
    try:
        source = source.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise MediaError(
            f"cannot open the selected file: {exc}", code=ErrorCode.MEDIA_CORRUPT
        ) from exc

    if not source.is_file():
        raise MediaError("the selected path is not a file", code=ErrorCode.MEDIA_CORRUPT)

    destination = unique_filename(state.settings.media_dir, source.name)
    working = import_media_file(source, destination)
    return _finalise(state, working, source)


@router.post("/upload", response_model=ImportedMedia)
async def upload_file(
    state: StateDep,
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    filename: Annotated[str | None, Form()] = None,
) -> ImportedMedia:
    """Accept a browser upload and stream it to disk."""
    raw_name = filename or file.filename or "media"
    safe_name = sanitize_filename(raw_name)
    destination = unique_filename(state.settings.media_dir, safe_name)

    limit = state.settings.max_upload_bytes
    written = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await file.read(_UPLOAD_CHUNK):
                written += len(chunk)
                if written > limit:
                    handle.close()
                    destination.unlink(missing_ok=True)
                    raise MediaError(
                        f"upload exceeds the {limit} byte limit",
                        code=ErrorCode.MEDIA_TOO_LARGE,
                        context={"limit_bytes": limit},
                    )
                handle.write(chunk)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise TiktuvError(
            f"could not write the uploaded file: {exc}", code=ErrorCode.INSUFFICIENT_DISK
        ) from exc
    finally:
        await file.close()

    if written == 0:
        destination.unlink(missing_ok=True)
        raise MediaError("the uploaded file is empty", code=ErrorCode.MEDIA_CORRUPT)

    return _finalise(state, destination, Path(raw_name))


# --------------------------------------------------------------------------
# Import from a URL (Zoom cloud recordings)
# --------------------------------------------------------------------------
class FromUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=4096)
    #: Only needed when the recording is passcode-protected.
    passcode: str | None = Field(default=None, max_length=200)


@router.post("/from-url", response_model=UrlFetchStatus)
def import_from_url(
    payload: FromUrlRequest, state: StateDep, _auth: AuthDep
) -> UrlFetchStatus:
    """Download a cloud recording into the media directory, in the background.

    Returns immediately. Poll ``GET /api/media/from-url`` or listen for
    ``media.fetch`` events for progress; when it reports ``ready`` its
    ``media_token`` can be passed straight to ``POST /api/jobs``.
    """
    url = payload.url.strip()
    if not zoom.is_zoom_url(url):
        raise MediaError(
            "only Zoom recording links are supported at the moment",
            code=ErrorCode.INVALID_REQUEST,
            context={"expected": "https://<company>.zoom.us/rec/share/… or /rec/play/…"},
        )
    return state.fetcher.start(url, payload.passcode)


@router.get("/from-url", response_model=UrlFetchStatus)
def url_import_status(state: StateDep, _auth: AuthDep) -> UrlFetchStatus:
    """Progress of the current or most recent URL import. Safe to poll."""
    return state.fetcher.status()


@router.delete("/from-url", response_model=UrlFetchStatus)
def cancel_url_import(state: StateDep, _auth: AuthDep) -> UrlFetchStatus:
    """Stop the running download and discard the partial file."""
    state.fetcher.cancel()
    return state.fetcher.status()
