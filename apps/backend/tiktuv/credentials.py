"""Persistent storage for the Soniox API key.

The key lives in a small JSON file inside the data directory, next to the
database and the transcripts. On POSIX it is chmod 0600; on Windows it inherits
the user's profile ACL, which is already private to the account — the same
reasoning as the desktop handshake file in ``__main__.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import Settings
from .logging_conf import get_logger

log = get_logger("credentials")

_FILENAME = "soniox.json"


class SonioxCredentials:
    """Read/write the Soniox API key, and never log it."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = settings.data_dir / _FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> str | None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        value = payload.get("api_key") if isinstance(payload, dict) else None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def set(self, api_key: str) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("api key must not be empty")
        self._settings.ensure_dirs()
        self._path.write_text(json.dumps({"api_key": key}), encoding="utf-8")
        if os.name != "nt":
            self._path.chmod(0o600)
        log.info("API key saved", extra={"path": self._path.name})

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
        log.info("API key removed", extra={"path": self._path.name})

    def masked(self) -> str:
        """Last four characters of the key, for display only."""
        key = self.get()
        if not key:
            return ""
        if len(key) <= 6:
            return "•" * len(key)
        return f"••••{key[-4:]}"


class DeepSeekCredentials(SonioxCredentials):
    """Read/write the DeepSeek API key. Identical mechanics, separate file."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = settings.data_dir / "deepseek.json"
