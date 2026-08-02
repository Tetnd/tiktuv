"""Pydantic contracts shared by the API, the worker and the exporters.

``packages/shared-types`` mirrors these for TypeScript; keep the two in sync
(``scripts/check-shared-types.ps1`` diffs the enum members).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(StrEnum):
    """User-visible stage. Maps 1:1 to a Hebrew label in the frontend."""

    PENDING = "pending"              # ממתין בתור
    PREPARING_MEDIA = "preparing_media"        # מכין את המדיה
    LOADING_MODEL = "loading_model"            # טוען מודל
    TRANSCRIBING = "transcribing"              # מתמלל
    DIARIZING = "diarizing"                    # מזהה דוברים
    GENERATING_SUBTITLES = "generating_subtitles"  # מייצר כתוביות
    EXPORTING = "exporting"                    # מייצא
    DONE = "done"                              # הושלם


class Language(StrEnum):
    HEBREW = "he"
    ENGLISH = "en"
    AUTO = "auto"


class DevicePreference(StrEnum):
    AUTO = "auto"
    CUDA = "cuda"
    CPU = "cpu"


class QualityPreset(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    ACCURATE = "accurate"


class TimestampMode(StrEnum):
    NONE = "none"
    SEGMENT = "segment"
    WORD = "word"


class ExportFormat(StrEnum):
    TXT = "txt"
    TXT_TIMESTAMPED = "txt_timestamped"
    DOCX = "docx"
    PDF = "pdf"
    SRT = "srt"
    VTT = "vtt"
    JSON = "json"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
#: Decoding parameters per quality preset.
#:
#: These are passed straight through to ``model.generate`` as the Transformers
#: ASR pipeline's ``generate_kwargs``. Deliberately limited to parameters that
#: are valid on the *chunked* (non-sequential) long-form path we use — the
#: temperature-fallback and ``condition_on_prev_tokens`` options only apply to
#: Whisper's sequential path, so they are not set here.
PRESET_DECODING: dict[QualityPreset, dict[str, Any]] = {
    QualityPreset.FAST: {"num_beams": 1, "temperature": 0.0},
    QualityPreset.BALANCED: {"num_beams": 2, "temperature": 0.0},
    QualityPreset.ACCURATE: {"num_beams": 5, "temperature": 0.0},
}

#: Seconds of audio the model consumes per forward pass. 30 s is Whisper's
#: native receptive field; anything else degrades quality.
MODEL_CHUNK_SECONDS = 30.0


class TranscriptionSettings(BaseModel):
    """Everything the user can tune for a job. Persisted with the job."""

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    language: Language = Language.HEBREW
    model_id: str = "ivrit-ai/whisper-large-v3"
    device: DevicePreference = DevicePreference.AUTO
    quality: QualityPreset = QualityPreset.BALANCED
    timestamps: TimestampMode = TimestampMode.SEGMENT

    #: 0 lets the backend choose from available VRAM. Whisper long-form
    #: batching is per 30 s window inside one media window.
    batch_size: Annotated[int, Field(ge=0, le=64)] = 0
    #: Half precision. Ignored (with a warning) on devices that cannot do it.
    use_fp16: bool = True
    #: Load weights in 8-bit to cut VRAM. Requires bitsandbytes; if it is not
    #: installed the job fails with a clear message rather than silently
    #: falling back to full precision.
    quantize_8bit: bool = False

    # Subtitle shaping
    subtitle_max_line_length: Annotated[int, Field(ge=16, le=120)] = 42
    subtitle_max_lines: Annotated[int, Field(ge=1, le=4)] = 2

    # Text post-processing
    preserve_filler_words: bool = False
    normalize_punctuation: bool = True
    split_paragraphs: bool = True
    detect_long_silences: bool = True
    #: A silence at least this long starts a new paragraph.
    long_silence_seconds: Annotated[float, Field(ge=0.3, le=30.0)] = 2.5

    #: Names, jargon and spelling hints. Fed to Whisper as the initial prompt.
    vocabulary_prompt: str = Field(default="", max_length=2000)

    # Optional module — see tiktuv/diarization.
    enable_diarization: bool = False

    output_dir: str | None = None
    export_formats: list[ExportFormat] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_word_timestamps(self) -> TranscriptionSettings:
        if self.timestamps is TimestampMode.WORD and self.quality is QualityPreset.FAST:
            # Word timestamps need cross-attention alignment, which greedy
            # single-beam decoding still supports — this is just a nudge, not
            # an error, so we leave the settings untouched.
            pass
        return self

    def decoding_kwargs(self) -> dict[str, Any]:
        return dict(PRESET_DECODING[self.quality])


# --------------------------------------------------------------------------
# Transcript
# --------------------------------------------------------------------------
class Word(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    start: float
    end: float
    probability: float | None = None


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)
    speaker: str | None = None
    #: True when the user edited this segment in the built-in editor.
    edited: bool = False
    #: Set on segments that were reconstructed across a chunk boundary.
    boundary_merged: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class Transcript(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    job_id: str
    language: str
    model_id: str
    duration: float
    segments: list[Segment] = Field(default_factory=list)
    speakers: dict[str, str] = Field(default_factory=dict)
    #: True while the job is still running and this is a partial snapshot.
    partial: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def full_text(self) -> str:
        return "\n".join(segment.text.strip() for segment in self.segments if segment.text.strip())


# --------------------------------------------------------------------------
# Media
# --------------------------------------------------------------------------
class MediaInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    filename: str
    size_bytes: int
    duration: float
    container: str
    audio_codec: str | None = None
    video_codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bit_rate: int | None = None
    has_audio: bool = True
    has_video: bool = False


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
class JobProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: JobStage = JobStage.PENDING
    #: 0.0 – 1.0 across the whole job, not just the current stage.
    percent: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    processed_seconds: float = 0.0
    total_seconds: float = 0.0
    eta_seconds: float | None = None
    message: str | None = None


class ErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    remedy: str
    detail: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class JobSummary(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    id: str
    filename: str
    source_path: str
    size_bytes: int
    duration: float
    status: JobStatus
    progress: JobProgress
    model_id: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_opened_at: datetime | None = None
    error: ErrorPayload | None = None
    output_paths: dict[str, str] = Field(default_factory=dict)
    has_transcript: bool = False


class JobDetail(JobSummary):
    settings: TranscriptionSettings
    media: MediaInfo | None = None


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Server-side path produced by ``POST /api/media/import``.
    media_token: str
    settings: TranscriptionSettings = Field(default_factory=TranscriptionSettings)
    #: Queue the job without starting it, so the user can review settings.
    autostart: bool = True


class UpdateSegmentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments: list[Segment]
    speakers: dict[str, str] | None = None


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formats: list[ExportFormat] = Field(min_length=1)
    output_dir: str | None = None


class ExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: ExportFormat
    path: str
    size_bytes: int


# --------------------------------------------------------------------------
# System / preflight
# --------------------------------------------------------------------------
class CheckStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label_he: str
    status: CheckStatus
    detail: str = ""
    remedy: str = ""


class DeviceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["cuda", "cpu"]
    name: str
    index: int | None = None
    total_memory_bytes: int | None = None
    free_memory_bytes: int | None = None
    supports_fp16: bool = False
    compute_capability: str | None = None


class ModelInfo(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    id: str
    label_he: str
    hebrew_specialised: bool
    approx_download_bytes: int
    downloaded: bool
    cache_path: str
    license: str
    notes_he: str = ""


class StorageUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: str
    models_dir: str
    temp_dir: str
    temp_bytes: int
    models_bytes: int
    media_bytes: int
    transcripts_bytes: int
    exports_bytes: int
    free_disk_bytes: int


class SystemInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_version: str
    platform: str
    python_version: str
    torch_version: str | None = None
    cuda_version: str | None = None
    ffmpeg_version: str | None = None
    devices: list[DeviceInfo] = Field(default_factory=list)
    checks: list[PreflightCheck] = Field(default_factory=list)
    storage: StorageUsage | None = None
