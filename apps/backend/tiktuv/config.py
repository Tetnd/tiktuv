"""Runtime configuration and the on-disk data layout.

Everything Tiktuv writes lives under a single, user-visible data directory so
the privacy screen can show exactly one place and the cleanup buttons have a
well-defined scope.
"""

from __future__ import annotations

import os
import secrets
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "Tiktuv"
DEFAULT_PORT = 8756


def _default_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":  # pragma: no cover - not packaged yet
        base = Path.home() / "Library" / "Application Support"
    else:  # pragma: no cover - not packaged yet
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


class Settings(BaseSettings):
    """Backend settings. Every field is overridable via ``TIKTUV_*`` env vars."""

    model_config = SettingsConfigDict(
        env_prefix="TIKTUV_",
        env_file=".env",
        extra="ignore",
    )

    # --- Networking -------------------------------------------------------
    # Bound to loopback only. Changing this is a deliberate, validated act.
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    auth_token: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    #: Origins allowed to call the API.
    #:
    #: The desktop app is a native HTTP client and sends no Origin header, so
    #: this list exists for the browser cases only: a page opened against a
    #: backend started by hand, or the Docker setup in docker-compose.yml.
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:8756",
            "http://localhost:8756",
        ]
    )

    # --- Storage ----------------------------------------------------------
    data_dir: Path = Field(default_factory=_default_data_dir)
    #: Where Hugging Face caches model weights. Surfaced in the privacy screen.
    models_dir: Path | None = None
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None

    # --- Limits -----------------------------------------------------------
    max_upload_bytes: int = 8 * 1024 * 1024 * 1024  # 8 GiB
    #: Free space required before a job starts, on top of the media size.
    min_free_disk_bytes: int = 2 * 1024 * 1024 * 1024
    #: One at a time by default — concurrent jobs exhaust GPU memory.
    max_concurrent_jobs: int = 1
    max_queue_length: int = 200

    # --- Inference defaults ----------------------------------------------
    default_model_id: str = "ivrit-ai/whisper-large-v3"
    #: Seconds of audio per outer processing window (not the model's own 30s).
    window_seconds: float = 300.0
    #: Overlap between consecutive windows, used to repair boundary words.
    window_overlap_seconds: float = 5.0
    #: Target sample rate. Whisper feature extraction requires 16 kHz mono.
    sample_rate: int = 16_000

    log_level: str = "INFO"
    log_json: bool = True
    #: Strip absolute paths out of error payloads leaving the process.
    redact_paths_in_errors: bool = True

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        allowed = {"127.0.0.1", "localhost", "::1"}
        if value not in allowed:
            raise ValueError(
                f"Tiktuv binds to loopback only; refusing host {value!r}. "
                f"Allowed: {sorted(allowed)}"
            )
        return value

    # --- Derived paths ----------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "tiktuv.sqlite3"

    @property
    def media_dir(self) -> Path:
        """Imported source media (copies; originals are never modified)."""
        return self.data_dir / "media"

    @property
    def temp_dir(self) -> Path:
        """Scratch space. Safe to wipe at any time the queue is idle."""
        return self.data_dir / "temp"

    @property
    def transcripts_dir(self) -> Path:
        """Canonical JSON transcripts, including partial results."""
        return self.data_dir / "transcripts"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def model_cache_dir(self) -> Path:
        return self.models_dir or (self.data_dir / "models")

    def managed_dirs(self) -> list[Path]:
        return [
            self.data_dir,
            self.media_dir,
            self.temp_dir,
            self.transcripts_dir,
            self.exports_dir,
            self.logs_dir,
            self.model_cache_dir,
        ]

    def ensure_dirs(self) -> None:
        for directory in self.managed_dirs():
            directory.mkdir(parents=True, exist_ok=True)

    def writable_roots(self) -> list[Path]:
        """Directories the API is permitted to read from and write to.

        Used by :func:`tiktuv.security.resolve_within` to block path traversal
        and arbitrary local file reads through API parameters.
        """
        return [
            self.media_dir.resolve(),
            self.transcripts_dir.resolve(),
            self.exports_dir.resolve(),
            self.temp_dir.resolve(),
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """Test helper — drops the cached singleton."""
    get_settings.cache_clear()
