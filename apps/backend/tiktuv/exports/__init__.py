"""Export registry.

One entry point — :func:`export_transcript` — dispatches to the exporter for
each requested format and returns the files that were written.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ErrorCode, ExportError
from ..logging_conf import get_logger
from ..schemas import ExportFormat, ExportResult, Transcript, TranscriptionSettings
from ..security import sanitize_filename
from .base import ExportContext, Exporter
from .docx_export import DocxExporter
from .pdf_export import PdfExporter
from .text_formats import (
    JsonExporter,
    SrtExporter,
    TimestampedTxtExporter,
    TxtExporter,
    VttExporter,
)

log = get_logger("exports")


def _build_registry() -> dict[ExportFormat, Exporter]:
    exporters: list[Exporter] = [
        TxtExporter(),
        TimestampedTxtExporter(),
        SrtExporter(),
        VttExporter(),
        JsonExporter(),
        DocxExporter(),
        PdfExporter(),
    ]
    return {exporter.format: exporter for exporter in exporters}


REGISTRY: dict[ExportFormat, Exporter] = _build_registry()

SUPPORTED_FORMATS: tuple[ExportFormat, ...] = tuple(REGISTRY)


def get_exporter(fmt: ExportFormat) -> Exporter:
    # Accept a raw string too — this is reachable from API input, and it must
    # produce a typed error rather than an AttributeError on ``fmt.value``.
    try:
        key = ExportFormat(fmt)
    except ValueError:
        raise ExportError(
            f"no exporter registered for {fmt!r}",
            code=ErrorCode.UNSUPPORTED_EXPORT_FORMAT,
            context={"supported": [f.value for f in REGISTRY]},
        ) from None

    exporter = REGISTRY.get(key)
    if exporter is None:  # pragma: no cover - registry covers every member
        raise ExportError(
            f"no exporter registered for {key.value!r}",
            code=ErrorCode.UNSUPPORTED_EXPORT_FORMAT,
        )
    return exporter


def export_transcript(
    transcript: Transcript,
    settings: TranscriptionSettings,
    *,
    formats: list[ExportFormat],
    output_dir: Path,
    stem: str,
) -> list[ExportResult]:
    """Write ``transcript`` in each requested format.

    A failure in one format does not abort the others — the caller gets the
    files that succeeded plus a raised error describing what did not, which is
    more useful than losing five good exports because a font was missing.
    """
    if not formats:
        return []

    context = ExportContext(
        transcript=transcript,
        settings=settings,
        stem=sanitize_filename(stem, fallback="transcript").removesuffix(".txt"),
        output_dir=output_dir,
    )

    results: list[ExportResult] = []
    failures: list[str] = []

    # Deduplicate while preserving the caller's order.
    for fmt in dict.fromkeys(formats):
        try:
            path = get_exporter(fmt).write(context)
        except ExportError as exc:
            log.error("export failed", extra={"format": fmt.value, "error": exc.detail})
            failures.append(f"{fmt.value}: {exc.detail}")
            continue
        except Exception as exc:  # noqa: BLE001 - normalised, never swallowed
            log.exception("unexpected export failure", extra={"format": fmt.value})
            failures.append(f"{fmt.value}: {exc}")
            continue

        results.append(
            ExportResult(format=fmt, path=str(path), size_bytes=path.stat().st_size)
        )
        log.info("exported", extra={"format": fmt.value, "bytes": path.stat().st_size})

    if failures and not results:
        raise ExportError("; ".join(failures))
    if failures:
        raise ExportError(
            "; ".join(failures),
            context={"succeeded": [r.format.value for r in results]},
        )
    return results


__all__ = [
    "ExportContext",
    "REGISTRY",
    "SUPPORTED_FORMATS",
    "export_transcript",
    "get_exporter",
]
