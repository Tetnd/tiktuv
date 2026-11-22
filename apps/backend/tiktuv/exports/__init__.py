"""Export registry.

One entry point — :func:`export_transcript` — dispatches to the exporter for
each requested format and returns the files that were written.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ErrorCode, ExportError
from ..logging_conf import get_logger
from ..schemas import (
    ExportFormat,
    ExportResult,
    ExportSection,
    Transcript,
    TranscriptAnalysis,
    TranscriptionSettings,
)
from ..security import sanitize_filename
from .analysis_export import (
    ANALYSIS_FORMATS,
    ANALYSIS_SECTIONS,
    section_label,
    section_stem,
    supports,
    write_section,
)
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


def export_job(
    transcript: Transcript,
    settings: TranscriptionSettings,
    *,
    sections: list[ExportSection],
    formats: list[ExportFormat],
    output_dir: Path,
    stem: str,
    analysis: TranscriptAnalysis | None = None,
) -> list[ExportResult]:
    """Write each requested section in each requested format.

    Every section becomes its own file, so one analysis tab can be exported on
    its own. A format that makes no sense for a section — subtitles for a
    summary — is skipped silently rather than failing the whole export. As in
    :func:`export_transcript`, one failure never costs the caller the files
    that did succeed.
    """
    if not formats or not sections:
        return []

    base_stem = sanitize_filename(stem, fallback="transcript").removesuffix(".txt")
    results: list[ExportResult] = []
    failures: list[str] = []

    for section in dict.fromkeys(sections):
        if section is not ExportSection.TRANSCRIPT and analysis is None:
            failures.append(f"{section_label(section)}: no analysis has been generated")
            continue

        # Deduplicate per section: both text formats land on the same analysis
        # file, so asking for both must not write it twice.
        wanted: list[ExportFormat] = []
        for fmt in formats:
            if not supports(section, fmt):
                continue
            if section is not ExportSection.TRANSCRIPT and fmt is ExportFormat.TXT_TIMESTAMPED:
                fmt = ExportFormat.TXT
            if fmt not in wanted:
                wanted.append(fmt)

        if section is ExportSection.TRANSCRIPT:
            section_results = export_transcript_quietly(
                transcript,
                settings,
                formats=wanted,
                output_dir=output_dir,
                stem=base_stem,
                failures=failures,
            )
            results.extend(section_results)
            continue

        assert analysis is not None  # guarded above
        target_stem = section_stem(base_stem, section)
        for fmt in wanted:
            try:
                path = write_section(
                    analysis, section, fmt, output_dir=output_dir, stem=target_stem
                )
            except ExportError as exc:
                log.error(
                    "analysis export failed",
                    extra={"section": section.value, "format": fmt.value, "error": exc.detail},
                )
                failures.append(f"{section_label(section)}/{fmt.value}: {exc.detail}")
                continue
            except Exception as exc:  # noqa: BLE001 - normalised, never swallowed
                log.exception(
                    "unexpected analysis export failure",
                    extra={"section": section.value, "format": fmt.value},
                )
                failures.append(f"{section_label(section)}/{fmt.value}: {exc}")
                continue
            results.append(
                ExportResult(
                    format=fmt,
                    path=str(path),
                    size_bytes=path.stat().st_size,
                    section=section,
                )
            )
            log.info(
                "exported",
                extra={
                    "section": section.value,
                    "format": fmt.value,
                    "bytes": path.stat().st_size,
                },
            )

    if failures and not results:
        raise ExportError("; ".join(failures))
    if failures:
        raise ExportError(
            "; ".join(failures),
            context={"succeeded": [r.path for r in results]},
        )
    return results


def export_transcript_quietly(
    transcript: Transcript,
    settings: TranscriptionSettings,
    *,
    formats: list[ExportFormat],
    output_dir: Path,
    stem: str,
    failures: list[str],
) -> list[ExportResult]:
    """:func:`export_transcript`, collecting failures instead of raising.

    One format at a time, so a format that fails is the only one reported and
    the rest still come back.
    """
    results: list[ExportResult] = []
    for fmt in formats:
        try:
            results.extend(
                export_transcript(
                    transcript,
                    settings,
                    formats=[fmt],
                    output_dir=output_dir,
                    stem=stem,
                )
            )
        except ExportError as exc:
            failures.append(exc.detail)
    return results


__all__ = [
    "ANALYSIS_FORMATS",
    "ANALYSIS_SECTIONS",
    "ExportContext",
    "REGISTRY",
    "SUPPORTED_FORMATS",
    "export_job",
    "export_transcript",
    "section_label",
    "get_exporter",
]
