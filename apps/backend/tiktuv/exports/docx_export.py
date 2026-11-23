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
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
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


# Word is strict about child order inside ``pPr``/``rPr``/``tblPr``/``sectPr``:
# an element in the wrong place is ignored or the file is flagged as corrupt.
# Each tuple lists the elements that must come *after* the one being inserted.
_PPR_AFTER = (
    "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
    "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection",
    "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl", "w:divId",
    "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)
_RPR_RTL_AFTER = ("w:cs", "w:em", "w:lang", "w:eastAsianLayout", "w:specVanish", "w:oMath")
_RPR_SZCS_AFTER = (
    "w:highlight", "w:u", "w:effect", "w:bdr", "w:shd", "w:fitText", "w:vertAlign",
    "w:rtl", *_RPR_RTL_AFTER,
)
_RPR_BCS_AFTER = (
    "w:i", "w:iCs", "w:caps", "w:smallCaps", "w:strike", "w:dstrike", "w:outline",
    "w:shadow", "w:emboss", "w:imprint", "w:noProof", "w:snapToGrid", "w:vanish",
    "w:webHidden", "w:color", "w:spacing", "w:w", "w:kern", "w:position", "w:sz",
    "w:szCs", *_RPR_SZCS_AFTER,
)
_RPR_LANG_AFTER = ("w:eastAsianLayout", "w:specVanish", "w:oMath")
_TBLPR_BIDI_AFTER = (
    "w:tblStyleRowBandSize", "w:tblStyleColBandSize", "w:tblW", "w:jc",
    "w:tblCellSpacing", "w:tblInd", "w:tblBorders", "w:shd", "w:tblLayout",
    "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription",
)
_SECTPR_BIDI_AFTER = ("w:rtlGutter", "w:docGrid", "w:printerSettings", "w:sectPrChange")


def _put(parent: Any, tag: str, after: tuple[str, ...], value: str | None = "1") -> Any:
    """Insert ``<tag w:val=value>`` at its schema position, replacing any old one."""
    for existing in parent.findall(qn(tag)):
        parent.remove(existing)
    child = OxmlElement(tag)
    if value is not None:
        child.set(qn("w:val"), value)
    parent.insert_element_before(child, *after)
    return child


def set_section_rtl(section: object) -> None:
    """Make the section right-to-left (affects tables and columns)."""
    _put(section._sectPr, "w:bidi", _SECTPR_BIDI_AFTER)  # type: ignore[attr-defined]


def set_paragraph_rtl(paragraph: Paragraph, *, align: int | None = None) -> None:
    """Make a paragraph RTL.

    In a ``w:bidi`` paragraph, ``w:jc`` is *logical*: ``left`` means the start
    edge, which is the right side. Writing ``jc="right"`` — what python-docx
    does for ``WD_ALIGN_PARAGRAPH.RIGHT`` — pushes the text to the *left*. So
    the default is to write no ``jc`` at all and let the paragraph start on the
    right; only an explicit alignment such as centre is written.
    """
    p_pr = paragraph._p.get_or_add_pPr()
    _put(p_pr, "w:bidi", _PPR_AFTER)
    for existing in p_pr.findall(qn("w:jc")):
        p_pr.remove(existing)
    if align is not None:
        paragraph.alignment = cast(WD_ALIGN_PARAGRAPH, align)


def set_run_rtl(run: Run, *, size_pt: int = BODY_SIZE_PT) -> None:
    """Mark a run as complex-script (Hebrew) with matching fonts, size and weight."""
    r_pr = run._r.get_or_add_rPr()

    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, fonts)
    fonts.set(qn("w:cs"), HEBREW_CS_FONT)
    fonts.set(qn("w:ascii"), HEBREW_ASCII_FONT)
    fonts.set(qn("w:hAnsi"), HEBREW_ASCII_FONT)

    run.font.size = Pt(size_pt)
    # Hebrew is complex script: Word reads w:bCs / w:szCs for it, not w:b / w:sz.
    if run.bold:
        _put(r_pr, "w:bCs", _RPR_BCS_AFTER, None)
    _put(r_pr, "w:szCs", _RPR_SZCS_AFTER, str(size_pt * 2))  # half-points
    _put(r_pr, "w:rtl", _RPR_RTL_AFTER, None)
    lang = _put(r_pr, "w:lang", _RPR_LANG_AFTER, None)
    lang.set(qn("w:bidi"), "he-IL")


def _setup_page(document: DocumentObject) -> None:
    """A4 with even margins (python-docx defaults to US Letter)."""
    for section in document.sections:
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.left_margin = section.right_margin = Cm(2.0)
        set_section_rtl(section)


def _make_style_rtl(document: DocumentObject) -> None:
    """Make Normal RTL, and give the whole document Hebrew language defaults."""
    normal = document.styles["Normal"]
    normal.font.name = HEBREW_ASCII_FONT
    normal.font.size = Pt(BODY_SIZE_PT)
    _put(normal.element.get_or_add_pPr(), "w:bidi", _PPR_AFTER)
    r_pr = normal.element.get_or_add_rPr()
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is not None:
        fonts.set(qn("w:cs"), HEBREW_CS_FONT)
    _put(r_pr, "w:szCs", _RPR_SZCS_AFTER, str(BODY_SIZE_PT * 2))
    _put(r_pr, "w:rtl", _RPR_RTL_AFTER, None)
    lang = _put(r_pr, "w:lang", _RPR_LANG_AFTER, None)
    lang.set(qn("w:bidi"), "he-IL")


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

        _setup_page(document)

        # Make the built-in Normal style RTL so any paragraph Word adds later
        # (for example when the user presses Enter) inherits the direction.
        _make_style_rtl(document)

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

        # Mirror the section setting onto the table itself; Word honours the
        # table-level property even if a section is later changed. With it the
        # table starts at the right margin, so no explicit alignment is set
        # (``jc`` is logical here too, and ``right`` would flip it left).
        _put(table._tbl.tblPr, "w:bidiVisual", _TBLPR_BIDI_AFTER)

        table.autofit = False
        table.columns[0].width = Cm(14.8)
        table.columns[1].width = Cm(2.2)

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

            time_cell.width = Cm(2.2)
            text_cell.width = Cm(14.8)

            time_paragraph = time_cell.paragraphs[0]
            set_paragraph_rtl(time_paragraph, align=WD_ALIGN_PARAGRAPH.CENTER)
            stamp_run = time_paragraph.add_run(format_clock(segment.start))
            stamp_run.font.size = Pt(TIMESTAMP_SIZE_PT)
            stamp_run.font.color.rgb = RGBColor(0x60, 0x60, 0x60)


def write_simple_docx(
    path: Path,
    *,
    title: str,
    meta: list[str],
    blocks: list[tuple[str | None, list[str]]],
) -> Path:
    """Write a plain right-to-left DOCX made of headings and paragraphs.

    Used by the analysis exports, which have no timeline and therefore need
    none of the transcript exporter's segment or timestamp machinery — just
    the same RTL document setup, so Word opens them right-aligned.
    """
    try:
        document = Document()
    except Exception as exc:  # noqa: BLE001 - python-docx raises broadly
        raise ExportError(f"could not create DOCX document: {exc}") from exc

    _setup_page(document)
    _make_style_rtl(document)

    add_rtl_paragraph(document, title, style="Title", size_pt=20, bold=True)
    for line in meta:
        add_rtl_paragraph(document, line, size_pt=10, color=RGBColor(0x60, 0x60, 0x60))
    add_rtl_paragraph(document, "")

    for heading, lines in blocks:
        if heading:
            add_rtl_paragraph(document, heading, size_pt=13, bold=True)
        for line in lines:
            add_rtl_paragraph(document, line)
        add_rtl_paragraph(document, "")

    try:
        document.save(str(path))
    except OSError as exc:
        raise ExportError(f"could not write {path.name}: {exc}") from exc
    return path


__all__ = [
    "DocxExporter",
    "write_simple_docx",
    "add_rtl_paragraph",
    "set_paragraph_rtl",
    "set_run_rtl",
    "set_section_rtl",
    "WD_SECTION",
]
