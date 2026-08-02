"""Hebrew DOCX export with genuine right-to-left document structure.

Word decides direction from three independent places, and all three must be
set or the file opens left-aligned or with punctuation on the wrong side:

* ``w:sectPr/w:bidi`` — section level: column and table order run right-to-left.
* ``w:pPr/w:bidi``   — paragraph level: the paragraph is an RTL paragraph.
* ``w:rPr/w:rtl``    — run level: the text is complex-script text, so Word uses
  the complex-script font (``w:rFonts/@w:cs``) and size (``w:szCs``).

Text is written in logical order. Nothing is reversed — Word runs the Unicode
bidirectional algorithm itself, which is why copy-paste and search work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from ..errors import ExportError
from ..schemas import ExportFormat, TimestampMode
from ..text.hebrew import format_clock, paragraph_text, split_paragraphs
from .base import ExportContext, target_path

#: Complex-script font. David is the standard Hebrew serif on Windows; Word
#: substitutes automatically if it is absent.
HEBREW_CS_FONT = "David"
HEBREW_ASCII_FONT = "Arial"
BODY_SIZE_PT = 12
TIMESTAMP_SIZE_PT = 9


def _set(element: object, tag: str, value: str = "1") -> None:
    child = OxmlElement(tag)
    child.set(qn("w:val"), value)
    element.append(child)  # type: ignore[attr-defined]


def set_section_rtl(section: object) -> None:
    """Make the section right-to-left (affects tables and columns)."""
    sect_pr = section._sectPr  # type: ignore[attr-defined]
    for existing in sect_pr.findall(qn("w:bidi")):
        sect_pr.remove(existing)
    _set(sect_pr, "w:bidi")


def set_paragraph_rtl(paragraph: Paragraph, *, align: int | None = None) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    for existing in p_pr.findall(qn("w:bidi")):
        p_pr.remove(existing)
    _set(p_pr, "w:bidi")
    # The setter accepts a raw alignment value; the getter is typed as the
    # enum, so the cast documents that this is intentional.
    paragraph.alignment = cast(
        WD_ALIGN_PARAGRAPH, WD_ALIGN_PARAGRAPH.RIGHT if align is None else align
    )


def set_run_rtl(run: Run, *, size_pt: int = BODY_SIZE_PT) -> None:
    r_pr = run._r.get_or_add_rPr()
    for existing in r_pr.findall(qn("w:rtl")):
        r_pr.remove(existing)
    _set(r_pr, "w:rtl")

    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, fonts)
    fonts.set(qn("w:cs"), HEBREW_CS_FONT)
    fonts.set(qn("w:ascii"), HEBREW_ASCII_FONT)
    fonts.set(qn("w:hAnsi"), HEBREW_ASCII_FONT)

    # Complex-script size is a separate property from w:sz.
    sz_cs = OxmlElement("w:szCs")
    sz_cs.set(qn("w:val"), str(size_pt * 2))  # half-points
    r_pr.append(sz_cs)

    lang = OxmlElement("w:lang")
    lang.set(qn("w:bidi"), "he-IL")
    r_pr.append(lang)

    run.font.size = Pt(size_pt)


def add_rtl_paragraph(
    document: DocumentObject,
    text: str = "",
    *,
    style: str | None = None,
    size_pt: int = BODY_SIZE_PT,
    bold: bool = False,
    color: RGBColor | None = None,
) -> Paragraph:
    paragraph = document.add_paragraph(style=style)
    set_paragraph_rtl(paragraph)
    if text:
        run = paragraph.add_run(text)
        run.bold = bold
        if color is not None:
            run.font.color.rgb = color
        set_run_rtl(run, size_pt=size_pt)
    return paragraph


class DocxExporter:
    format = ExportFormat.DOCX
    extension = "docx"

    def write(self, context: ExportContext) -> Path:
        settings = context.settings

        try:
            document = Document()
        except Exception as exc:  # noqa: BLE001 - python-docx raises broadly
            raise ExportError(f"could not create DOCX document: {exc}") from exc

        for section in document.sections:
            set_section_rtl(section)

        # Make the built-in Normal style RTL so any paragraph Word adds later
        # (for example when the user presses Enter) inherits the direction.
        normal = document.styles["Normal"]
        normal.font.name = HEBREW_ASCII_FONT
        normal.font.size = Pt(BODY_SIZE_PT)
        style_p_pr = normal.element.get_or_add_pPr()
        _set(style_p_pr, "w:bidi")

        self._write_header(document, context)

        if settings.timestamps is TimestampMode.NONE:
            self._write_flowing(document, context)
        else:
            self._write_timestamped_table(document, context)

        path = target_path(context, self.extension)
        try:
            document.save(str(path))
        except OSError as exc:
            raise ExportError(f"could not write {path.name}: {exc}") from exc
        return path

    # -- sections ----------------------------------------------------------
    def _write_header(self, document: DocumentObject, context: ExportContext) -> None:
        title = add_rtl_paragraph(document, context.stem, style="Title", size_pt=20, bold=True)
        set_paragraph_rtl(title)

        meta = [
            f"משך ההקלטה: {format_clock(context.transcript.duration)}",
            f"מודל: {context.transcript.model_id}",
            f"מספר מקטעים: {len(context.transcript.segments)}",
        ]
        if context.transcript.partial:
            meta.append("שימו לב: זהו תמליל חלקי — העבודה לא הושלמה.")
        for line in meta:
            add_rtl_paragraph(document, line, size_pt=10, color=RGBColor(0x60, 0x60, 0x60))

        add_rtl_paragraph(document, "")

    def _write_flowing(self, document: DocumentObject, context: ExportContext) -> None:
        settings = context.settings
        paragraphs = (
            split_paragraphs(
                context.transcript.segments,
                silence_seconds=settings.long_silence_seconds,
                use_silence=settings.detect_long_silences,
            )
            if settings.split_paragraphs
            else [context.transcript.segments]
        )
        for group in paragraphs:
            text = paragraph_text(group)
            if not text:
                continue
            speaker = group[0].speaker
            if speaker:
                label = context.transcript.speakers.get(speaker, speaker)
                paragraph = document.add_paragraph()
                set_paragraph_rtl(paragraph)
                name_run = paragraph.add_run(f"{label}: ")
                name_run.bold = True
                set_run_rtl(name_run)
                body_run = paragraph.add_run(text)
                set_run_rtl(body_run)
            else:
                add_rtl_paragraph(document, text)

    def _write_timestamped_table(
        self, document: DocumentObject, context: ExportContext
    ) -> None:
        """Two-column table: timestamp and text.

        With ``w:sectPr/w:bidi`` set, Word lays the columns out right-to-left,
        so the *first* column defined here appears on the right — which is
        where a Hebrew reader starts.
        """
        table: Table = document.add_table(rows=0, cols=2)
        table.style = "Table Grid"  # type: ignore[assignment]  # setter takes a name
        table.alignment = WD_TABLE_ALIGNMENT.RIGHT

        # Mirror the section setting onto the table itself; Word honours the
        # table-level property even if a section is later changed.
        tbl_pr = table._tbl.tblPr
        _set(tbl_pr, "w:bidiVisual")

        speakers = context.transcript.speakers
        for segment in context.transcript.segments:
            text = segment.text.strip()
            if not text:
                continue
            if segment.speaker:
                label = speakers.get(segment.speaker, segment.speaker)
                text = f"{label}: {text}"

            # python-docx's Table.add_row is untyped in the shipped stubs.
            row: Any = table.add_row()  # type: ignore[no-untyped-call]
            text_cell, time_cell = row.cells[0], row.cells[1]

            text_paragraph = text_cell.paragraphs[0]
            set_paragraph_rtl(text_paragraph)
            set_run_rtl(text_paragraph.add_run(text))

            time_paragraph = time_cell.paragraphs[0]
            set_paragraph_rtl(time_paragraph, align=WD_ALIGN_PARAGRAPH.CENTER)
            stamp_run = time_paragraph.add_run(format_clock(segment.start))
            stamp_run.font.size = Pt(TIMESTAMP_SIZE_PT)
            stamp_run.font.color.rgb = RGBColor(0x60, 0x60, 0x60)


__all__ = [
    "DocxExporter",
    "add_rtl_paragraph",
    "set_paragraph_rtl",
    "set_run_rtl",
    "set_section_rtl",
    "WD_SECTION",
]
