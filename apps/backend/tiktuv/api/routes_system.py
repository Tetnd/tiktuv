"""System information, first-run preflight, model management and cleanup."""

from __future__ import annotations

import platform
import sys

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, ConfigDict

from .. import __version__
from ..asr import registry
from ..asr.device import cuda_version, list_devices, torch_version
from ..errors import ErrorCode, FFmpegMissingError, ModelError
from ..events import Event, EventType
from ..jobs.store import directory_size, free_disk_bytes, purge_temp
from ..logging_conf import get_logger
from ..schemas import (
    CheckStatus,
    DeviceInfo,
    ModelInfo,
    PreflightCheck,
    StorageUsage,
    SystemInfo,
)
from .deps import AuthDep, StateDep

log = get_logger("api.system")

router = APIRouter(prefix="/api", tags=["system"])

#: Enough for the largest model plus scratch space.
_RECOMMENDED_FREE_BYTES = 8 * 1024 * 1024 * 1024


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
            else "מומלץ להשאיר לפחות 8 GiB פנויים עבור המודל וקבצי הביניים.",
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


@router.post("/models/download", response_model=ModelInfo)
def download_model(
    payload: DownloadRequest,
    background: BackgroundTasks,
    state: StateDep,
    _auth: AuthDep,
) -> ModelInfo:
    """Start downloading a model.

    Called only after the UI has shown the user the approximate size and the
    destination folder — this is the one place Tiktuv touches the network, and
    it never happens without an explicit click.
    """
    entry = registry.get_entry(payload.model_id)

    def _download() -> None:
        try:
            registry.download_model(entry.id, state.settings)
            state.bus.publish_threadsafe(
                Event(EventType.MODEL_STATUS, {"model_id": entry.id, "status": "ready"})
            )
        except ModelError as exc:
            state.bus.publish_threadsafe(
                Event(
                    EventType.MODEL_STATUS,
                    {"model_id": entry.id, "status": "failed", "error": exc.to_payload()},
                )
            )

    background.add_task(_download)
    state.bus.publish_threadsafe(
        Event(EventType.MODEL_STATUS, {"model_id": entry.id, "status": "downloading"})
    )
    return next(m for m in registry.list_models(state.settings) if m.id == entry.id)


@router.delete("/models/{model_id:path}", response_model=CleanupResult)
def delete_model(model_id: str, state: StateDep, _auth: AuthDep) -> CleanupResult:
    if state.models.loaded_model_id == model_id:
        state.models.release()
    reclaimed = registry.delete_model(model_id, state.settings)
    return CleanupResult(reclaimed_bytes=reclaimed, detail=f"deleted {model_id}")


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
