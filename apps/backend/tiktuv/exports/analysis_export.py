"""Exports for the analysis tabs — summary, key points, actions, entities …

Each tab is its own document. The transcript exporters take a timeline and
produce one file per format; an analysis tab has no timeline, just headings
and lines, so these writers render that shape instead. Timed formats (SRT,
VTT) have nothing to say about an analysis tab and are skipped rather than
failing the export.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import ErrorCode, ExportError
from ..schemas import ExportFormat, ExportSection, TranscriptAnalysis
from ..security import sanitize_filename
from ..text.hebrew import RLM, protect_neutrals
from .base import ENCODING, write_text
from .docx_export import write_simple_docx
from .pdf_export import write_simple_pdf

#: Hebrew tab names, used for the document title and the filename suffix.
SECTION_LABELS: dict[ExportSection, str] = {
    ExportSection.TRANSCRIPT: "תמלול",
    ExportSection.SUMMARY: "תקציר",
    ExportSection.KEY_POINTS: "נקודות מפתח",
    ExportSection.ACTION_ITEMS: "משימות",
    ExportSection.NAMED_ENTITIES: "יישויות",
    ExportSection.SPEAKER_SUMMARIES: "סיכום דוברים",
    ExportSection.SPEAKER_QUOTES: "ציטוטים",
}

#: Everything except the transcript.
ANALYSIS_SECTIONS: tuple[ExportSection, ...] = tuple(
    section for section in ExportSection if section is not ExportSection.TRANSCRIPT
)

#: Formats an analysis tab can be written in. ``TXT_TIMESTAMPED`` collapses
#: onto plain text — there are no timestamps to add.
ANALYSIS_FORMATS: tuple[ExportFormat, ...] = (
    ExportFormat.TXT,
    ExportFormat.TXT_TIMESTAMPED,
    ExportFormat.DOCX,
    ExportFormat.PDF,
    ExportFormat.JSON,
)

BULLET = "• "

#: Hebrew labels for the entity types DeepSeek returns.
_ENTITY_TYPES: dict[str, str] = {
    "person": "אנשים",
    "organization": "ארגונים",
    "location": "מקומות",
    "date": "תאריכים",
    "other": "אחר",
}

#: Shown instead of an empty document when a tab produced nothing.
_EMPTY: dict[ExportSection, str] = {
    ExportSection.SUMMARY: "לא הופק תקציר.",
    ExportSection.KEY_POINTS: "לא נמצאו נקודות מפתח.",
    ExportSection.ACTION_ITEMS: "לא נמצאו משימות או פריטי פעולה.",
    ExportSection.NAMED_ENTITIES: "לא נמצאו ישויות בעלות שם.",
    ExportSection.SPEAKER_SUMMARIES: "לא זוהו דוברים בתמליל.",
    ExportSection.SPEAKER_QUOTES: "לא זוהו דוברים בתמליל.",
}

#: (heading or None, lines) — the only structure these documents have.
Block = tuple[str | None, list[str]]


def section_label(section: ExportSection) -> str:
    return SECTION_LABELS.get(section, section.value)


def supports(section: ExportSection, fmt: ExportFormat) -> bool:
    """Can ``section`` be written as ``fmt``?"""
    if section is ExportSection.TRANSCRIPT:
        return True
    return fmt in ANALYSIS_FORMATS


def section_blocks(section: ExportSection, analysis: TranscriptAnalysis) -> list[Block]:
    """Render one analysis tab as headings and lines, in logical order."""
    if section is ExportSection.SUMMARY:
        paragraphs = [p.strip() for p in analysis.summary.splitlines() if p.strip()]
        return [(None, paragraphs)] if paragraphs else []

    if section is ExportSection.KEY_POINTS:
        points = [p.strip() for p in analysis.key_points if p.strip()]
        return [(None, [f"{BULLET}{p}" for p in points])] if points else []

    if section is ExportSection.ACTION_ITEMS:
        lines = []
        for item in analysis.action_items:
            task = item.task.strip()
            if not task:
                continue
            if item.assignee:
                lines.append(f"{BULLET}{task} — {item.assignee}")
            else:
                lines.append(f"{BULLET}{task}")
        return [(None, lines)] if lines else []

    if section is ExportSection.NAMED_ENTITIES:
        grouped: dict[str, list[str]] = {}
        for entity in analysis.named_entities:
            name = entity.name.strip()
            if not name:
                continue
            grouped.setdefault(entity.type or "other", []).append(name)
        blocks: list[Block] = []
        # Keep the known types in a fixed order, then anything unexpected.
        order = list(_ENTITY_TYPES) + [k for k in grouped if k not in _ENTITY_TYPES]
        for kind in order:
            names = grouped.get(kind)
            if not names:
                continue
            blocks.append((_ENTITY_TYPES.get(kind, kind), [f"{BULLET}{n}" for n in names]))
        return blocks

    if section is ExportSection.SPEAKER_SUMMARIES:
        return [
            (s.speaker, [s.summary.strip()])
            for s in analysis.speaker_summaries
            if s.summary.strip()
        ]

    if section is ExportSection.SPEAKER_QUOTES:
        blocks = []
        for speaker in analysis.speaker_quotes:
            quotes = [q.strip() for q in speaker.quotes if q.strip()]
            if quotes:
                blocks.append((speaker.speaker, [f"{BULLET}„{q}”" for q in quotes]))
        return blocks

    raise ExportError(
        f"{section.value!r} is not an analysis section",
        code=ErrorCode.UNSUPPORTED_EXPORT_FORMAT,
    )


def section_payload(section: ExportSection, analysis: TranscriptAnalysis) -> object:
    """The raw value behind a tab, for the JSON export."""
    return getattr(analysis, section.value)


def section_stem(stem: str, section: ExportSection) -> str:
    """``recording`` + summary -> ``recording - תקציר`` (sanitized)."""
    if section is ExportSection.TRANSCRIPT:
        return stem
    return sanitize_filename(f"{stem} - {section_label(section)}", fallback="analysis")


def _meta(analysis: TranscriptAnalysis) -> list[str]:
    lines: list[str] = []
    if analysis.model:
        lines.append(f"נוצר בעזרת DeepSeek {analysis.model}")
    if analysis.generated_at is not None:
        lines.append(f"תאריך הניתוח: {analysis.generated_at:%d/%m/%Y %H:%M}")
    return lines


def write_section(
    analysis: TranscriptAnalysis,
    section: ExportSection,
    fmt: ExportFormat,
    *,
    output_dir: Path,
    stem: str,
) -> Path:
    """Write one analysis tab as one file and return its path."""
    if not supports(section, fmt):
        raise ExportError(
            f"{section.value!r} cannot be exported as {fmt.value!r}",
            code=ErrorCode.UNSUPPORTED_EXPORT_FORMAT,
            context={"supported": [f.value for f in ANALYSIS_FORMATS]},
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    title = section_label(section)
    blocks = section_blocks(section, analysis)
    meta = _meta(analysis)

    if fmt is ExportFormat.JSON:
        path = output_dir / f"{stem}.json"
        document = {
            "job_id": analysis.job_id,
            "section": section.value,
            "model": analysis.model,
            "generated_at": (
                analysis.generated_at.isoformat() if analysis.generated_at else None
            ),
            section.value: json.loads(
                json.dumps(section_payload(section, analysis), default=_jsonable)
            ),
        }
        try:
            path.write_text(
                json.dumps(document, ensure_ascii=False, indent=2), encoding=ENCODING
            )
        except OSError as exc:
            raise ExportError(f"could not write {path.name}: {exc}") from exc
        return path

    if not blocks:
        blocks = [(None, [_EMPTY.get(section, "אין תוכן.")])]

    if fmt is ExportFormat.DOCX:
        return write_simple_docx(
            output_dir / f"{stem}.docx", title=title, meta=meta, blocks=blocks
        )
    if fmt is ExportFormat.PDF:
        return write_simple_pdf(
            output_dir / f"{stem}.pdf", title=title, meta=meta, blocks=blocks
        )

    # TXT (and TXT_TIMESTAMPED, which has no timestamps to show here).
    lines: list[str] = [title, ""]
    lines.extend(meta)
    if meta:
        lines.append("")
    for heading, body in blocks:
        if heading:
            lines.append(f"{heading}:")
        lines.extend(protect_neutrals(line) for line in body)
        lines.append("")
    content = RLM + "\r\n".join(lines).rstrip("\r\n") + "\r\n"
    return write_text(output_dir / f"{stem}.txt", content)


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[attr-defined]
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[attr-defined]
    return str(value)


__all__ = [
    "ANALYSIS_FORMATS",
    "ANALYSIS_SECTIONS",
    "SECTION_LABELS",
    "section_blocks",
    "section_label",
    "section_stem",
    "supports",
    "write_section",
]
