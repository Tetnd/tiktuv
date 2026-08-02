"""Model catalogue and the process-wide loaded-model cache.

Two rules this module exists to enforce:

1. The Hebrew model is never swapped out silently. Fallbacks are in the
   catalogue, flagged ``hebrew_specialised=False``, and are only ever used
   when the user picks them by hand.
2. Weights are loaded once and reused across jobs. Reloading a 3 GB model per
   file would dominate the runtime.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..errors import ErrorCode, ModelError
from ..logging_conf import get_logger
from ..schemas import DevicePreference, ModelInfo, TranscriptionSettings
from .base import Transcriber
from .device import ResolvedDevice, empty_cuda_cache, resolve_device
from .mock import MOCK_MODEL_ID, MockTranscriber

log = get_logger("asr.registry")

HEBREW_MODEL_ID = "ivrit-ai/whisper-large-v3"


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    id: str
    label_he: str
    hebrew_specialised: bool
    approx_download_bytes: int
    license: str
    notes_he: str = ""


CATALOGUE: tuple[CatalogueEntry, ...] = (
    CatalogueEntry(
        id=HEBREW_MODEL_ID,
        label_he="עברית — ivrit.ai Whisper Large v3 (מומלץ)",
        hebrew_specialised=True,
        approx_download_bytes=3_100_000_000,
        license="Apache-2.0 (ראו docs/model-licensing.md)",
        notes_he="מודל שאומן במיוחד לעברית. זהו מודל ברירת המחדל.",
    ),
    CatalogueEntry(
        id="openai/whisper-large-v3",
        label_he="Whisper Large v3 (כללי — לא מותאם לעברית)",
        hebrew_specialised=False,
        approx_download_bytes=3_100_000_000,
        license="Apache-2.0",
        notes_he="מודל כללי. הדיוק בעברית נמוך יותר. בחרו רק אם המודל העברי אינו זמין.",
    ),
    CatalogueEntry(
        id="openai/whisper-small",
        label_he="Whisper Small (מהיר — דיוק נמוך)",
        hebrew_specialised=False,
        approx_download_bytes=970_000_000,
        license="Apache-2.0",
        notes_he="קטן ומהיר, לבדיקות בלבד. אינו מומלץ לתמלול עברי אמיתי.",
    ),
)

_BY_ID = {entry.id: entry for entry in CATALOGUE}


def get_entry(model_id: str) -> CatalogueEntry:
    entry = _BY_ID.get(model_id)
    if entry is None:
        raise ModelError(
            f"unknown model id: {model_id!r}",
            code=ErrorCode.UNKNOWN_MODEL,
            context={"known": sorted(_BY_ID)},
        )
    return entry


def _hub_dir(cache_dir: Path, model_id: str) -> Path:
    """Path Hugging Face uses for a repo inside a cache directory."""
    return cache_dir / f"models--{model_id.replace('/', '--')}"


def is_downloaded(model_id: str, cache_dir: Path) -> bool:
    """True when the repo has at least one materialised snapshot.

    Checked structurally rather than by calling the Hub, so this works with no
    network connection.
    """
    snapshots = _hub_dir(cache_dir, model_id) / "snapshots"
    if not snapshots.is_dir():
        return False
    for snapshot in snapshots.iterdir():
        if not snapshot.is_dir():
            continue
        has_config = (snapshot / "config.json").exists()
        has_weights = any(
            (snapshot / name).exists()
            for name in ("model.safetensors", "pytorch_model.bin", "model.safetensors.index.json")
        )
        if has_config and has_weights:
            return True
    return False


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:  # pragma: no cover - races with cleanup
            continue
    return total


def list_models(settings: Settings) -> list[ModelInfo]:
    cache = settings.model_cache_dir
    return [
        ModelInfo(
            id=entry.id,
            label_he=entry.label_he,
            hebrew_specialised=entry.hebrew_specialised,
            approx_download_bytes=entry.approx_download_bytes,
            downloaded=is_downloaded(entry.id, cache),
            cache_path=str(_hub_dir(cache, entry.id)),
            license=entry.license,
            notes_he=entry.notes_he,
        )
        for entry in CATALOGUE
    ]


def delete_model(model_id: str, settings: Settings) -> int:
    """Remove a downloaded model. Returns bytes reclaimed."""
    import shutil

    get_entry(model_id)  # validates the id
    target = _hub_dir(settings.model_cache_dir, model_id)
    if not target.exists():
        return 0
    size = directory_size(target)
    shutil.rmtree(target, ignore_errors=True)
    log.info("deleted model", extra={"model_id": model_id, "bytes": size})
    return size


def download_model(
    model_id: str,
    settings: Settings,
    *,
    progress: object | None = None,
) -> Path:
    """Download a model into the local cache. Requires network access.

    Only ever called from an explicit user action, after the UI has shown the
    approximate size and the destination folder.
    """
    get_entry(model_id)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ModelError(
            "huggingface_hub is not installed",
            code=ErrorCode.ASR_BACKEND_MISSING,
            context={"install": "pip install -e apps/backend[asr]"},
        ) from exc

    settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
    log.info(
        "downloading model",
        extra={"model_id": model_id, "cache_dir": str(settings.model_cache_dir)},
    )
    try:
        path = snapshot_download(
            repo_id=model_id,
            cache_dir=str(settings.model_cache_dir),
            allow_patterns=[
                "*.json", "*.txt", "*.safetensors", "*.model", "preprocessor_config.json",
            ],
        )
    except Exception as exc:  # noqa: BLE001 - hub raises many unrelated types
        raise ModelError(
            str(exc), code=ErrorCode.MODEL_DOWNLOAD_FAILED, context={"model_id": model_id}
        ) from exc
    return Path(path)


def use_mock_backend() -> bool:
    return os.environ.get("TIKTUV_ASR_BACKEND", "").lower() == "mock"


class ModelManager:
    """Holds at most one loaded transcriber and reuses it across jobs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._transcriber: Transcriber | None = None
        self._key: tuple[str, str, bool, int, bool] | None = None

    @property
    def loaded_model_id(self) -> str | None:
        return self._transcriber.model_id if self._transcriber is not None else None

    def acquire(self, settings: TranscriptionSettings) -> Transcriber:
        """Return a transcriber matching ``settings``, loading it if needed."""
        if use_mock_backend():
            with self._lock:
                if not isinstance(self._transcriber, MockTranscriber):
                    self._release_locked()
                    self._transcriber = MockTranscriber(MOCK_MODEL_ID)
                    self._key = (MOCK_MODEL_ID, "cpu", False, 1, False)
                return self._transcriber

        resolved = self._resolve(settings)
        key = (
            settings.model_id,
            resolved.torch_device,
            resolved.use_fp16,
            resolved.batch_size,
            settings.quantize_8bit,
        )
        with self._lock:
            if self._transcriber is not None and self._key == key:
                return self._transcriber

            # Different configuration: free the old weights before allocating.
            self._release_locked()

            entry = get_entry(settings.model_id)
            downloaded = is_downloaded(entry.id, self._settings.model_cache_dir)
            if not downloaded:
                raise ModelError(
                    f"model {entry.id!r} has not been downloaded yet "
                    f"(~{entry.approx_download_bytes // (1 << 20)} MiB)",
                    code=ErrorCode.MODEL_NOT_DOWNLOADED,
                    context={
                        "model_id": entry.id,
                        "approx_download_bytes": entry.approx_download_bytes,
                        "cache_path": str(self._settings.model_cache_dir),
                    },
                )

            from .whisper_hf import WhisperHFTranscriber

            self._transcriber = WhisperHFTranscriber(
                entry.id,
                resolved,
                cache_dir=self._settings.model_cache_dir,
                quantize_8bit=settings.quantize_8bit,
                # Already verified present: stay fully offline.
                local_files_only=True,
            )
            self._key = key
            return self._transcriber

    def _resolve(self, settings: TranscriptionSettings) -> ResolvedDevice:
        return resolve_device(
            DevicePreference(settings.device),
            requested_fp16=settings.use_fp16,
            requested_batch_size=settings.batch_size,
        )

    def _release_locked(self) -> None:
        if self._transcriber is not None:
            try:
                self._transcriber.unload()
            except Exception as exc:  # noqa: BLE001 - unload must never mask a job error
                log.warning("error while unloading model", extra={"error": str(exc)})
        self._transcriber = None
        self._key = None
        empty_cuda_cache()

    def release(self) -> None:
        with self._lock:
            self._release_locked()

    def cleanup_after_failure(self) -> None:
        """Free device memory after a failed job without dropping the model.

        A transient OOM should not force a 3 GB reload for the next job, but
        the allocator's cached blocks must go.
        """
        empty_cuda_cache()
