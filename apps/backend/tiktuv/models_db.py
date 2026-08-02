"""SQLAlchemy ORM models for local job history."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .schemas import (
    ErrorPayload,
    JobDetail,
    JobProgress,
    JobStage,
    JobStatus,
    JobSummary,
    MediaInfo,
    TranscriptionSettings,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class JobRecord(Base):
    """One transcription job. Survives restarts; see ``recover_orphans``."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    #: Path the user originally picked. Never written to.
    source_path: Mapped[str] = mapped_column(Text)
    #: Our own copy inside media/. This is what FFmpeg reads.
    working_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[str] = mapped_column(String(24), default=JobStatus.QUEUED.value, index=True)
    stage: Mapped[str] = mapped_column(String(32), default=JobStage.PENDING.value)
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0)
    processed_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_id: Mapped[str] = mapped_column(String(200))
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    media_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_paths_json: Mapped[str] = mapped_column(Text, default="{}")

    #: Seconds of audio already committed to the partial transcript. A resumed
    #: job restarts from the window boundary at or before this mark.
    resume_offset: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- JSON column helpers ------------------------------------------------
    @property
    def settings(self) -> TranscriptionSettings:
        return TranscriptionSettings.model_validate_json(self.settings_json or "{}")

    @settings.setter
    def settings(self, value: TranscriptionSettings) -> None:
        self.settings_json = value.model_dump_json()

    @property
    def media(self) -> MediaInfo | None:
        if not self.media_json:
            return None
        return MediaInfo.model_validate_json(self.media_json)

    @media.setter
    def media(self, value: MediaInfo | None) -> None:
        self.media_json = value.model_dump_json() if value is not None else None

    @property
    def error(self) -> ErrorPayload | None:
        if not self.error_json:
            return None
        return ErrorPayload.model_validate_json(self.error_json)

    @error.setter
    def error(self, value: ErrorPayload | None) -> None:
        self.error_json = value.model_dump_json() if value is not None else None

    @property
    def output_paths(self) -> dict[str, str]:
        try:
            data: Any = json.loads(self.output_paths_json or "{}")
        except json.JSONDecodeError:
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    @output_paths.setter
    def output_paths(self, value: dict[str, str]) -> None:
        self.output_paths_json = json.dumps(value, ensure_ascii=False)

    # -- Projections --------------------------------------------------------
    def _progress(self) -> JobProgress:
        return JobProgress(
            stage=JobStage(self.stage),
            percent=self.progress_percent,
            processed_seconds=self.processed_seconds,
            total_seconds=self.duration,
            message=self.progress_message,
        )

    def to_summary(self, *, has_transcript: bool = False) -> JobSummary:
        return JobSummary(
            id=self.id,
            filename=self.filename,
            source_path=self.source_path,
            size_bytes=self.size_bytes,
            duration=self.duration,
            status=JobStatus(self.status),
            progress=self._progress(),
            model_id=self.model_id,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            last_opened_at=self.last_opened_at,
            error=self.error,
            output_paths=self.output_paths,
            has_transcript=has_transcript,
        )

    def to_detail(self, *, has_transcript: bool = False) -> JobDetail:
        summary = self.to_summary(has_transcript=has_transcript)
        return JobDetail(
            **summary.model_dump(),
            settings=self.settings,
            media=self.media,
        )
