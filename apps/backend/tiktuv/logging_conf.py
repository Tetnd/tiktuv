"""Structured logging.

JSON lines by default so the desktop shell can tail and parse them; plain text
when ``TIKTUV_LOG_JSON=0`` for readable local development.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from .errors import redact_paths

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with ``extra=`` fields merged in."""

    def __init__(self, *, redact: bool = True) -> None:
        super().__init__()
        self.redact = redact

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_paths(message) if self.redact else message,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            text = self.formatException(record.exc_info)
            payload["exc"] = redact_paths(text) if self.redact else text
        return json.dumps(payload, ensure_ascii=False)


def _safe(value: Any) -> Any:
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict | list | tuple):
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return repr(value)
        return value
    return repr(value)


def configure_logging(
    *, level: str = "INFO", json_output: bool = True, log_dir: Path | None = None,
    redact: bool = True,
) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter: logging.Formatter = (
        JsonFormatter(redact=redact)
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )

    # stderr, so stdout stays clean for the desktop shell's handshake line.
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            log_dir / "tiktuv.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        root.addHandler(rotating)

    # uvicorn duplicates records through its own handlers otherwise.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"tiktuv.{name}" if not name.startswith("tiktuv") else name)
