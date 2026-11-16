"""System information, first-run preflight, model management and cleanup."""

from __future__ import annotations

import platform
import sys

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from .. import __version__, dialogs
from ..asr import registry
from ..asr.device import cuda_version, list_devices, torch_version
from ..errors import ErrorCode, FFmpegMissingError
from ..jobs.store import directory_size, free_disk_bytes, purge_temp
from ..logging_conf import get_logger
from ..schemas import (
    CheckStatus,
    DeviceInfo,
    ModelDownloadStatus,
    ModelInfo,
    PreflightCheck,
    StorageUsage,
    SystemInfo,
)
from .deps import AuthDep, StateDep

log = get_logger("api.system")

router = APIRouter(prefix="/api", tags=["system"])

#: The Hebrew model alone is ~6.2 GB; leave room for it plus media and
#: scratch files. The Hugging Face cache can also hold a second copy when
#: the filesystem does not support links.
_RECOMMENDED_FREE_BYTES = 15 * 1024 * 1024 * 1024


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    version: str


class CleanupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reclaimed_bytes: int
    detail: str = ""


class DownloadRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    model_id: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Unauthenticated liveness probe used by the desktop shell at startup."""
    return HealthResponse(status="ok", version=__version__)


def _storage(state: object) -> StorageUsage:
    settings = state.settings  # type: ignore[attr-defined]
    return StorageUsage(
        data_dir=str(settings.data_dir),
        models_dir=str(settings.model_cache_dir),
        temp_dir=str(settings.temp_dir),
        temp_bytes=directory_size(settings.temp_dir),
        models_bytes=directory_size(settings.model_cache_dir),
        media_bytes=directory_size(settings.media_dir),
        transcripts_bytes=directory_size(settings.transcripts_dir),
        exports_bytes=directory_size(settings.exports_dir),
        free_disk_bytes=free_disk_bytes(settings.data_dir),
    )


def _run_checks(state: object) -> list[PreflightCheck]:
    settings = state.settings  # type: ignore[attr-defined]
    checks: list[PreflightCheck] = []

    # -- OS ---------------------------------------------------------------
    is_windows = sys.platform == "win32"
    checks.append(
        PreflightCheck(
            id="os",
            label_he="מערכת ההפעלה",
            status=CheckStatus.OK if is_windows else CheckStatus.WARNING,
            detail=f"{platform.system()} {platform.release()}",
            remedy="" if is_windows else "Windows 10/11 היא הפלטפורמה הנתמכת רשמית.",
        )
    )

    # -- Python -----------------------------------------------------------
    ok_python = sys.version_info >= (3, 12)
    checks.append(
        PreflightCheck(
            id="python",
            label_he="סביבת Python",
            status=CheckStatus.OK if ok_python else CheckStatus.ERROR,
            detail=platform.python_version(),
            remedy="" if ok_python else "נדרשת גרסת Python 3.12 ומעלה.",
        )
    )

    # -- FFmpeg -----------------------------------------------------------
    try:
        tools = state.require_tools()  # type: ignore[attr-defined]
        checks.append(
            PreflightCheck(
                id="ffmpeg",
                label_he="FFmpeg",
                status=CheckStatus.OK,
                detail=f"גרסה {tools.version}",
            )
        )
    except FFmpegMissingError as exc:
        checks.append(
            PreflightCheck(
                id="ffmpeg",
                label_he="FFmpeg",
                status=CheckStatus.ERROR,
                detail=exc.message_he,
                remedy=exc.remedy_he,
            )
        )

    # -- PyTorch / GPU ----------------------------------------------------
    torch_v = torch_version()
    if torch_v is None:
        checks.append(
            PreflightCheck(
                id="torch",
                label_he="מנוע ההסקה (PyTorch)",
                status=CheckStatus.ERROR,
                detail="PyTorch אינו מותקן",
                remedy="התקינו PyTorch לפי docs/cpu-only-setup.md או docs/gpu-cuda-setup.md.",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                id="torch",
                label_he="מנוע ההסקה (PyTorch)",
                status=CheckStatus.OK,
                detail=f"גרסה {torch_v}",
            )
        )

    devices = list_devices()
    gpus = [device for device in devices if device.kind == "cuda"]
    if gpus:
        gpu = gpus[0]
        total_gib = (gpu.total_memory_bytes or 0) / (1 << 30)
        enough_vram = total_gib >= 6.0
        checks.append(
            PreflightCheck(
                id="gpu",
                label_he="כרטיס מסך (CUDA)",
                status=CheckStatus.OK if enough_vram else CheckStatus.WARNING,
                detail=f"{gpu.name} — {total_gib:.1f} GiB, CUDA {cuda_version() or 'לא ידוע'}",
                remedy=""
                if enough_vram
                else "פחות מ-6 GiB זיכרון. שקלו הרצה על המעבד או הקטנת גודל האצווה.",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                id="gpu",
                label_he="כרטיס מסך (CUDA)",
                status=CheckStatus.WARNING,
                detail="לא זוהה כרטיס מסך נתמך",
                remedy="התמלול יפעל על המעבד. זה יעבוד, אך יהיה איטי יותר.",
            )
        )

    # -- Model ------------------------------------------------------------
    entry = registry.get_entry(settings.default_model_id)
    downloaded = registry.is_downloaded(entry.id, settings.model_cache_dir)
    checks.append(
        PreflightCheck(
            id="model",
            label_he="מודל התמלול העברי",
            status=CheckStatus.OK if downloaded else CheckStatus.WARNING,
            detail=entry.id
            if downloaded
            else f"טרם הורד (~{entry.approx_download_bytes >> 20} MiB)",
            remedy="" if downloaded else f"ההורדה תישמר בתיקייה {settings.model_cache_dir}",
        )
    )

    # -- Soniox (cloud) --------------------------------------------------
    soniox_configured = state.credentials.get() is not None  # type: ignore[attr-defined]
    checks.append(
        PreflightCheck(
            id="soniox",
            label_he="Soniox (תמלול בענן)",
            status=CheckStatus.OK if soniox_configured else CheckStatus.WARNING,
            detail="מפתח API מוגדר" if soniox_configured else "לא הוגדר מפתח API",
            remedy=""
            if soniox_configured
            else "הגדירו מפתח API כדי לתמלל דרך Soniox.",
        )
    )

    # -- DeepSeek (AI analysis) -----------------------------------------
    deepseek_configured = state.deepseek.get() is not None  # type: ignore[attr-defined]
    checks.append(
        PreflightCheck(
            id="deepseek",
            label_he="DeepSeek (ניתוח AI)",
            status=CheckStatus.OK if deepseek_configured else CheckStatus.WARNING,
            detail="מפתח API מוגדר" if deepseek_configured else "לא הוגדר מפתח API",
            remedy=""
            if deepseek_configured
            else "הגדירו מפתח API כדי ליצור תקציר, נקודות מפתח, משימות וניתוח דוברים.",
        )
    )

    # -- Disk -------------------------------------------------------------
    free = free_disk_bytes(settings.data_dir)
    if free >= _RECOMMENDED_FREE_BYTES:
        disk_status = CheckStatus.OK
    elif free >= settings.min_free_disk_bytes:
        disk_status = CheckStatus.WARNING
    else:
        disk_status = CheckStatus.ERROR
    checks.append(
        PreflightCheck(
            id="disk",
            label_he="מקום פנוי בדיסק",
            status=disk_status,
            detail=f"{free / (1 << 30):.1f} GiB פנויים",
            remedy=""
            if disk_status is CheckStatus.OK
            else "מומלץ להשאיר לפחות 15 GiB פנויים עבור המודל וקבצי הביניים.",
        )
    )

    # -- Writable directories --------------------------------------------
    unwritable: list[str] = []
    for directory in settings.managed_dirs():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".tiktuv-write-test"
            probe.touch()
            probe.unlink()
        except OSError:
            unwritable.append(directory.name)
    checks.append(
        PreflightCheck(
            id="directories",
            label_he="הרשאות כתיבה",
            status=CheckStatus.OK if not unwritable else CheckStatus.ERROR,
            detail=str(settings.data_dir)
            if not unwritable
            else f"אין הרשאה: {', '.join(unwritable)}",
            remedy="" if not unwritable else "בדקו את הרשאות התיקייה או בחרו תיקיית נתונים אחרת.",
        )
    )
    return checks


@router.get("/system", response_model=SystemInfo)
def system_info(state: StateDep, _auth: AuthDep) -> SystemInfo:
    ffmpeg_v: str | None = None
    try:
        ffmpeg_v = state.require_tools().version
    except FFmpegMissingError:
        ffmpeg_v = None

    return SystemInfo(
        app_version=__version__,
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        python_version=platform.python_version(),
        torch_version=torch_version(),
        cuda_version=cuda_version(),
        ffmpeg_version=ffmpeg_v,
        devices=list_devices(),
        checks=_run_checks(state),
        storage=_storage(state),
    )


@router.get("/system/devices", response_model=list[DeviceInfo])
def devices(_auth: AuthDep) -> list[DeviceInfo]:
    return list_devices()


@router.get("/system/storage", response_model=StorageUsage)
def storage(state: StateDep, _auth: AuthDep) -> StorageUsage:
    return _storage(state)


@router.get("/models", response_model=list[ModelInfo])
def list_models(state: StateDep, _auth: AuthDep) -> list[ModelInfo]:
    return registry.list_models(state.settings)


@router.post("/models/download", response_model=ModelDownloadStatus)
def download_model(
    payload: DownloadRequest, state: StateDep, _auth: AuthDep
) -> ModelDownloadStatus:
    """Start downloading a model in the background.

    This is the one place Tiktuv touches the network. The UI shows the size and
    the destination folder before calling it, and reports progress from
    ``GET /api/models/download`` and the ``model.status`` event stream.

    Calling it again while the same model is downloading is a no-op, so a
    first-run auto-start and a manual click cannot race into two downloads.
    """
    return state.downloader.start(payload.model_id)


@router.get("/models/download", response_model=ModelDownloadStatus)
def download_status(state: StateDep, _auth: AuthDep) -> ModelDownloadStatus:
    """Current download progress. Safe to poll."""
    return state.downloader.status()


@router.delete("/models/download", response_model=ModelDownloadStatus)
def cancel_download(state: StateDep, _auth: AuthDep) -> ModelDownloadStatus:
    """Stop the running download after the file in flight finishes.

    Partly-fetched files stay in the cache and are resumed on the next attempt.
    """
    state.downloader.cancel()
    return state.downloader.status()


@router.delete("/models/{model_id:path}", response_model=CleanupResult)
def delete_model(model_id: str, state: StateDep, _auth: AuthDep) -> CleanupResult:
    if state.models.loaded_model_id == model_id:
        state.models.release()
    reclaimed = registry.delete_model(model_id, state.settings)
    return CleanupResult(reclaimed_bytes=reclaimed, detail=f"deleted {model_id}")


class PickFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Where the dialog should start. Ignored if it no longer exists.
    initial: str | None = None


class PickFolderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: True when this session can show a native dialog at all.
    available: bool
    #: The chosen directory, or null if the user cancelled.
    path: str | None = None


@router.get("/system/pick-folder", response_model=PickFolderResponse)
def can_pick_folder(_auth: AuthDep) -> PickFolderResponse:
    """Whether a native folder dialog can be shown in this session.

    The UI uses this to decide whether to offer a browse button, so that the
    browser-fallback session shows a plain path field instead of a button that
    would do nothing.
    """
    return PickFolderResponse(available=dialogs.is_available())


@router.post("/system/pick-folder", response_model=PickFolderResponse)
def pick_folder(
    payload: PickFolderRequest, _auth: AuthDep
) -> PickFolderResponse:
    """Open a native folder chooser and return what the user picked.

    Blocks until the dialog closes. Cancelling is not an error — it returns a
    null path, and the caller leaves the current value alone.
    """
    if not dialogs.is_available():
        return PickFolderResponse(available=False)
    return PickFolderResponse(available=True, path=dialogs.pick_folder(payload.initial))


@router.post("/system/cleanup/temp", response_model=CleanupResult)
def cleanup_temp(state: StateDep, _auth: AuthDep) -> CleanupResult:
    if state.queue.running_count:
        from ..errors import JobError

        raise JobError(
            "cannot clear temporary files while a job is running",
            code=ErrorCode.JOB_INVALID_STATE,
        )
    reclaimed = purge_temp(state.settings)
    return CleanupResult(reclaimed_bytes=reclaimed, detail="temporary files removed")
