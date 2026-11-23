"""Pydantic contracts shared by the API, the worker and the exporters.

``apps/desktop-gpui/src/models.rs`` mirrors these in Rust by hand; change the
two together.
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
    """User-visible stage. Maps 1:1 to a Hebrew label in the desktop app."""

    PENDING = "pending"              # ממתין בתור
    PREPARING_MEDIA = "preparing_media"        # מכין את המדיה
    DOWNLOADING_MODEL = "downloading_model"    # מוריד את המודל
    LOADING_MODEL = "loading_model"            # טוען מודל
    TRANSCRIBING = "transcribing"              # מתמלל
    DIARIZING = "diarizing"                    # מזהה דוברים
    PROOFREADING = "proofreading"              # מגיה את התמליל
    ANALYZING = "analyzing"                    # מנתח את התמליל
    GENERATING_SUBTITLES = "generating_subtitles"  # מייצר כתוביות
    EXPORTING = "exporting"                    # מייצא
    DONE = "done"                              # הושלם


class Language(StrEnum):
    HEBREW = "he"
    ENGLISH = "en"
    AUTO = "auto"


class SpeakerMethod(StrEnum):
    """How speakers are told apart (see :mod:`tiktuv.speakers`)."""

    #: DeepSeek when a key exists. Never uploads audio on its own.
    AUTO = "auto"
    #: Acoustic: the recording is analysed by Soniox (uploads the audio).
    SONIOX = "soniox"
    #: Semantic: DeepSeek reads the transcript text.
    DEEPSEEK = "deepseek"


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


class ExportSection(StrEnum):
    """One exportable part of a job: the transcript or an analysis tab."""

    TRANSCRIPT = "transcript"
    SUMMARY = "summary"
    KEY_POINTS = "key_points"
    ACTION_ITEMS = "action_items"
    NAMED_ENTITIES = "named_entities"
    SPEAKER_SUMMARIES = "speaker_summaries"
    SPEAKER_QUOTES = "speaker_quotes"


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

    #: Fetch the model automatically when it is missing, instead of failing.
    #: The UI always shows the size and destination before bytes are fetched;
    #: this only removes the extra click.
    auto_download_model: bool = True

    # Speaker recognition — see tiktuv/speakers.py. On by default: a job is
    # only "done" once the whole pipeline has run, speakers included. Without a
    # DeepSeek or Soniox key the step is skipped with a note, never an error.
    enable_diarization: bool = True
    speaker_method: SpeakerMethod = SpeakerMethod.AUTO
    #: How many people speak, when the user knows — sharpens the text method.
    expected_speakers: Annotated[int, Field(ge=1, le=10)] | None = None
    #: Use a real name instead of "דובר N" whenever the transcript says one
    #: (needs DeepSeek; without a key speakers simply keep their numbers).
    suggest_speaker_names: bool = True

    #: Hand the finished transcript to DeepSeek, acting as a professional
    #: Hebrew copy-editor, to fix transcription mistakes. Needs a DeepSeek API
    #: key; without one the stage is skipped with a warning rather than
    #: failing the job.
    enable_proofreading: bool = True
    #: The DeepSeek model that proofreads. Empty means the module default.
    proofread_model: str = Field(default="", max_length=120)
    #: Have DeepSeek write the AI analysis (summary, key points, tasks, entities,
    #: speaker summaries, quotes) as the last step, so it is ready when the job
    #: finishes instead of on first opening a tab. Needs a DeepSeek key.
    enable_analysis: bool = True

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
    #: True when DeepSeek corrected this segment during proofreading.
    proofread: bool = False
    #: The raw ASR text, kept whenever proofreading or an edit replaced it, so
    #: the change stays reviewable and reversible in the editor.
    original_text: str | None = None

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
    #: The DeepSeek model that proofread this transcript, when one did.
    proofread_model: str = ""
    #: How many segments proofreading corrected.
    proofread_corrections: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def full_text(self) -> str:
        return "\n".join(segment.text.strip() for segment in self.segments if segment.text.strip())


# --------------------------------------------------------------------------
# AI analysis (DeepSeek)
# --------------------------------------------------------------------------
class ActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    #: "דובר 1", "דובר 2", ... or None when not attributable.
    assignee: str | None = None


class NamedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    #: person / organization / location / date / other
    type: str = "other"


class SpeakerSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str
    summary: str


class SpeakerQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str
    quotes: list[str] = Field(default_factory=list)


class TranscriptAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    named_entities: list[NamedEntity] = Field(default_factory=list)
    speaker_summaries: list[SpeakerSummary] = Field(default_factory=list)
    speaker_quotes: list[SpeakerQuote] = Field(default_factory=list)
    #: The LLM model id that produced this.
    model: str = ""
    generated_at: datetime | None = None


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


class UpdateNamedEntitiesRequest(BaseModel):
    """A hand-edited entity list for one analysis.

    Only the entities are replaceable: other fields came from the model or
    from other editors, so sending the whole analysis would let a stale client
    overwrite them.
    """

    model_config = ConfigDict(extra="forbid")

    named_entities: list[NamedEntity]


class UpdateKeyPointsRequest(BaseModel):
    """A hand-edited key-point list for one analysis.

    Editing does not touch ``generated_at`` or ``model`` — no new generation
    happened, the list was just changed by the user.
    """

    model_config = ConfigDict(extra="forbid")

    key_points: list[str]


class UpdateSpeakerQuotesRequest(BaseModel):
    """A hand-edited speaker-quote list for one analysis.

    Quotes are edited the same way as the transcript text: the editor owns
    the list, and whatever it last sent is what gets exported.
    """

    model_config = ConfigDict(extra="forbid")

    speaker_quotes: list[SpeakerQuote]


class UpdateSpeakerSummariesRequest(BaseModel):
    """A hand-edited speaker-summary list for one analysis.

    Only the speaker summaries are replaceable, for the same reason as
    :class:`UpdateNamedEntitiesRequest`: everything else in the analysis came
    from the model and must not be overwritten by a stale editor.
    """

    model_config = ConfigDict(extra="forbid")

    speaker_summaries: list[SpeakerSummary]


class UpdateActionItemsRequest(BaseModel):
    """A hand-edited action-item list for one analysis.

    Only the action items are replaceable, for the same reason as
    :class:`UpdateNamedEntitiesRequest`: everything else in the analysis came
    from the model and must not be overwritten by a stale editor.
    """

    model_config = ConfigDict(extra="forbid")

    action_items: list[ActionItem]


class UpdateSummaryRequest(BaseModel):
    """A hand-edited summary for one analysis.

    Only the summary is replaceable, for the same reason as
    :class:`UpdateNamedEntitiesRequest`: everything else in the analysis came
    from the model and must not be overwritten by a stale editor.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formats: list[ExportFormat] = Field(min_length=1)
    output_dir: str | None = None
    #: Which parts of the job to write. Each section becomes its own file per
    #: format, so a caller can export one analysis tab on its own. Defaults to
    #: the transcript alone, which is what older clients expect.
    sections: list[ExportSection] = Field(
        default_factory=lambda: [ExportSection.TRANSCRIPT]
    )


class ExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: ExportFormat
    path: str
    size_bytes: int
    section: ExportSection = ExportSection.TRANSCRIPT


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
    #: True when transcription happens on a remote service (Soniox) rather than
    #: a locally-downloaded model.
    is_cloud: bool = False


class UrlFetchState(StrEnum):
    IDLE = "idle"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UrlFetchStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: UrlFetchState = UrlFetchState.IDLE
    #: Meeting topic, once known.
    title: str = ""
    percent: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    eta_seconds: float | None = None
    error: str | None = None
    #: Present once the download finished and was validated.
    media: MediaInfo | None = None
    #: Token to pass to POST /api/jobs, same as an uploaded file.
    media_token: str | None = None


class ModelDownloadState(StrEnum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ModelDownloadStatus(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    state: ModelDownloadState = ModelDownloadState.IDLE
    model_id: str | None = None
    percent: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    files_done: int = 0
    files_total: int = 0
    current_file: str = ""
    eta_seconds: float | None = None
    error: str | None = None


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


# --------------------------------------------------------------------------
# Speaker recognition on a finished transcript
# --------------------------------------------------------------------------
class IdentifySpeakersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: SpeakerMethod = SpeakerMethod.AUTO
    suggest_names: bool = True
    #: Optional hint when the user knows how many people are speaking.
    expected_speakers: Annotated[int, Field(ge=1, le=10)] | None = None


class IdentifySpeakersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript: Transcript
    method: SpeakerMethod
    speaker_count: int
    notes: list[str] = Field(default_factory=list)
