"""Shared exporter plumbing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..schemas import ExportFormat, Transcript, TranscriptionSettings

#: All text exports are UTF-8. DOCX/PDF handle their own encoding.
ENCODING = "utf-8"
#: A BOM makes Windows Notepad and Excel treat the file as UTF-8 rather than
#: guessing the system code page, which would mangle Hebrew.
BOM = "﻿"


@dataclass(frozen=True, slots=True)
class ExportContext:
    transcript: Transcript
    settings: TranscriptionSettings
    #: Base filename without extension, already sanitized.
    stem: str
    output_dir: Path


class Exporter(Protocol):
    format: ExportFormat
    extension: str

    def write(self, context: ExportContext) -> Path: ...


def target_path(context: ExportContext, extension: str) -> Path:
    context.output_dir.mkdir(parents=True, exist_ok=True)
    return context.output_dir / f"{context.stem}.{extension.lstrip('.')}"


def write_text(path: Path, content: str, *, bom: bool = True) -> Path:
    # newline="" keeps the exact line endings we generated; SRT in particular
    # is specified with CRLF and some players are strict about it.
    with path.open("w", encoding=ENCODING, newline="") as handle:
        handle.write((BOM if bom else "") + content)
    return path
