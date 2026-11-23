"""Hebrew PDF export with a right-to-left layout engine.

**Why this module reverses text, when nothing else in Tiktuv does.**

PDF has no concept of bidirectional text. A PDF text-showing operator paints
glyphs in the order they are given, left to right, at the position given. The
Unicode bidirectional algorithm therefore has to run *before* the glyphs are
placed — that is the renderer's job, and in a PDF the generator is the
renderer.

ReportLab 4.2.5 does not do this for us. Its optional ``rl_config.rtlSupport``
switch only wires up ``pyfribidi2`` inside ``pdfgen.textobject``, is off by
default, has no maintained Windows build, and is not consulted by ``Paragraph``
at all. So we run the bidi algorithm ourselves with ``python-bidi``.

The critical ordering constraint: **wrap first, shape second.** Line breaking
must happen on logical-order text, because a visual-order string breaks in the
wrong places. So the engine measures and wraps the logical string, and only
then converts each finished line to visual order for painting.

Everything upstream of this file — the transcript, the editor, TXT, DOCX, SRT,
VTT and JSON — stays in logical order.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from bidi import get_display
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas

from ..errors import ExportError
from ..logging_conf import get_logger
from ..schemas import ExportFormat, TimestampMode
from ..text.hebrew import format_clock, paragraph_text, split_paragraphs
from .base import ExportContext, target_path

log = get_logger("exports.pdf")

FONT_NAME = "TiktuvHebrew"
FONT_NAME_BOLD = "TiktuvHebrew-Bold"

#: Bundled fonts win; system fonts are the fallback so a fresh checkout still
#: produces a correct PDF on Windows without shipping a licensed font file.
_BUNDLED_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

_SYSTEM_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    # (regular, bold) — both must exist for the pair to be chosen.
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (r"C:\Windows\Fonts\david.ttf", r"C:\Windows\Fonts\davidbd.ttf"),
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
)

_registered = False


def _bundled_pair() -> tuple[Path, Path] | None:
    if not _BUNDLED_FONT_DIR.is_dir():
        return None
    regular = sorted(
        p for p in _BUNDLED_FONT_DIR.glob("*.ttf") if "bold" not in p.stem.lower()
    )
    if not regular:
        return None
    bold = sorted(p for p in _BUNDLED_FONT_DIR.glob("*.ttf") if "bold" in p.stem.lower())
    return regular[0], (bold[0] if bold else regular[0])


def register_fonts() -> tuple[str, str]:
    """Register a Hebrew-capable TTF pair with ReportLab. Idempotent."""
    global _registered
    if _registered:
        return FONT_NAME, FONT_NAME_BOLD

    pair = _bundled_pair()
    if pair is None:
        for regular, bold in _SYSTEM_FONT_CANDIDATES:
            if os.path.exists(regular):
                pair = (Path(regular), Path(bold if os.path.exists(bold) else regular))
                break

    if pair is None:
        raise ExportError(
            "no Hebrew-capable TrueType font was found. Place a .ttf in "
            f"{_BUNDLED_FONT_DIR} (see docs/export-formats.md#pdf-fonts) or install one.",
        )

    try:
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(pair[0])))
        pdfmetrics.registerFont(TTFont(FONT_NAME_BOLD, str(pair[1])))
        pdfmetrics.registerFontFamily(FONT_NAME, normal=FONT_NAME, bold=FONT_NAME_BOLD)
    except Exception as exc:  # noqa: BLE001 - reportlab raises broadly on bad TTFs
        raise ExportError(f"could not register PDF font {pair[0].name}: {exc}") from exc

    _registered = True
    log.info("registered PDF fonts", extra={"regular": pair[0].name, "bold": pair[1].name})
    return FONT_NAME, FONT_NAME_BOLD


_MIRROR_PAIRS = str.maketrans("()[]{}<>«»", ")(][}{><»«")


def _strong_direction(char: str) -> str | None:
    """``"L"``, ``"R"`` or ``None`` (neutral) for one character."""
    kind = unicodedata.bidirectional(char)
    if kind == "L":
        return "L"
    if kind in ("R", "AL"):
        return "R"
    if kind in ("EN", "AN"):
        return "N"  # digits take the direction of the text before them
    return None


def mirror_rtl_brackets(text: str) -> str:
    """Swap brackets that sit inside right-to-left runs.

    python-bidi 0.6 only reorders; it does not mirror paired characters, so a
    Hebrew ``(גרסה)`` would paint as ``)גרסה(``. A bracket reads right-to-left
    unless it is enclosed by left-to-right text on both sides (UAX #9, N1/N2,
    with the paragraph direction being RTL). Swapping it in logical order
    before reordering gives the mirrored glyph in the right place.
    """
    if not any(ch in "()[]{}<>«»" for ch in text):
        return text

    # Direction of the nearest strong character on each side of every index.
    before: list[str] = []
    last = "R"
    for ch in text:
        kind = _strong_direction(ch)
        if kind == "N":  # a digit continues an L context, otherwise it is R
            kind = "L" if last == "L" else "R"
        if kind is not None:
            last = kind
        before.append(last)

    after: list[str] = [""] * len(text)
    nxt = "R"
    for index in range(len(text) - 1, -1, -1):
        kind = _strong_direction(text[index])
        if kind == "N":
            kind = "L" if before[index] == "L" else "R"
        if kind is not None:
            nxt = kind
        after[index] = nxt

    out = []
    for index, ch in enumerate(text):
        if ch in "()[]{}<>«»" and not (before[index] == "L" and after[index] == "L"):
            ch = ch.translate(_MIRROR_PAIRS)
        out.append(ch)
    return "".join(out)


def shape_rtl(text: str) -> str:
    """Convert one *already wrapped* line from logical to visual order.

    Hebrew needs no glyph reshaping (unlike Arabic), so the bidi reordering is
    the whole job. Called once per rendered line, never on stored data.
    """
    if not text:
        return text
    shaped: str = get_display(mirror_rtl_brackets(text), base_dir="R")
    return shaped


@dataclass(frozen=True, slots=True)
class PageStyle:
    page_size: tuple[float, float] = A4
    margin_top: float = 22 * mm
    margin_bottom: float = 20 * mm
    margin_right: float = 20 * mm
    margin_left: float = 20 * mm
    body_size: float = 11.5
    line_height: float = 17.0
    title_size: float = 20.0
    meta_size: float = 9.0
    timestamp_size: float = 8.5
    paragraph_gap: float = 7.0
    text_color: Color = HexColor("#111827")
    muted_color: Color = HexColor("#6B7280")
    #: Width reserved on the left for the timestamp gutter (RTL: text starts
    #: at the right margin and the stamp sits at the far left).
    timestamp_gutter: float = 46.0


class _RtlCanvas:
    """Minimal RTL flow layout on a ReportLab canvas."""

    def __init__(self, path: Path, style: PageStyle, title: str) -> None:
        self.style = style
        self.title = title
        self.canvas = pdf_canvas.Canvas(str(path), pagesize=style.page_size)
        self.canvas.setTitle(title)
        self.canvas.setAuthor("Tiktuv")
        self.canvas.setSubject("Hebrew transcript")
        self.width, self.height = style.page_size
        self.right = self.width - style.margin_right
        self.left = style.margin_left
        self.y = self.height - style.margin_top
        self.page_number = 1

    # -- measurement ---------------------------------------------------
    @property
    def text_width(self) -> float:
        return self.right - self.left

    def wrap(self, text: str, font: str, size: float, max_width: float) -> list[str]:
        """Greedy wrap on **logical** text, measured with real glyph widths."""
        words = text.split()
        if not words:
            return []
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if pdfmetrics.stringWidth(candidate, font, size) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    # -- flow ----------------------------------------------------------
    def ensure_space(self, needed: float) -> None:
        if self.y - needed < self.style.margin_bottom:
            self.new_page()

    def new_page(self) -> None:
        self._draw_footer()
        self.canvas.showPage()
        self.page_number += 1
        self.y = self.height - self.style.margin_top

    def _draw_footer(self) -> None:
        self.canvas.setFont(FONT_NAME, self.style.meta_size)
        self.canvas.setFillColor(self.style.muted_color)
        # Page number in the outer corner; Latin digits stay LTR.
        self.canvas.drawString(self.left, self.style.margin_bottom - 12, str(self.page_number))
        self.canvas.drawRightString(
            self.right, self.style.margin_bottom - 12, shape_rtl("הופק באמצעות תכתוב")
        )

    def draw_line(
        self,
        text: str,
        *,
        font: str = FONT_NAME,
        size: float | None = None,
        color: Color | None = None,
        right_edge: float | None = None,
    ) -> None:
        size = size or self.style.body_size
        self.canvas.setFont(font, size)
        self.canvas.setFillColor(color or self.style.text_color)
        # drawRightString anchors at the right margin — the RTL starting edge.
        self.canvas.drawRightString(right_edge or self.right, self.y, shape_rtl(text))

    def advance(self, amount: float | None = None) -> None:
        self.y -= amount if amount is not None else self.style.line_height

    def paragraph(
        self,
        text: str,
        *,
        font: str = FONT_NAME,
        size: float | None = None,
        color: Color | None = None,
        max_width: float | None = None,
        right_edge: float | None = None,
        gap_after: float | None = None,
    ) -> None:
        size = size or self.style.body_size
        width = max_width if max_width is not None else self.text_width
        lines = self.wrap(text, font, size, width)
        for line in lines:
            self.ensure_space(self.style.line_height)
            self.draw_line(line, font=font, size=size, color=color, right_edge=right_edge)
            self.advance()
        self.advance(gap_after if gap_after is not None else self.style.paragraph_gap)

    def finish(self) -> None:
        self._draw_footer()
        self.canvas.save()


class PdfExporter:
    format = ExportFormat.PDF
    extension = "pdf"

    def __init__(self, style: PageStyle | None = None) -> None:
        self.style = style or PageStyle()

    def write(self, context: ExportContext) -> Path:
        register_fonts()
        path = target_path(context, self.extension)
        style = self.style

        try:
            page = _RtlCanvas(path, style, context.stem)
            self._header(page, context)
            if context.settings.timestamps is TimestampMode.NONE:
                self._flowing(page, context)
            else:
                self._timestamped(page, context)
            page.finish()
        except ExportError:
            raise
        except OSError as exc:
            raise ExportError(f"could not write {path.name}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - reportlab raises broadly
            raise ExportError(f"PDF generation failed: {exc}") from exc
        return path

    def _header(self, page: _RtlCanvas, context: ExportContext) -> None:
        style = self.style
        page.paragraph(
            context.stem, font=FONT_NAME_BOLD, size=style.title_size, gap_after=4.0
        )
        transcript = context.transcript
        meta = " · ".join(
            [
                f"משך: {format_clock(transcript.duration)}",
                f"מקטעים: {len(transcript.segments)}",
                f"מודל: {transcript.model_id}",
            ]
        )
        page.paragraph(meta, size=style.meta_size, color=style.muted_color, gap_after=2.0)
        if transcript.partial:
            page.paragraph(
                "שימו לב: זהו תמליל חלקי — העבודה לא הושלמה.",
                size=style.meta_size,
                color=HexColor("#B45309"),
                gap_after=2.0,
            )

        # Separator rule.
        page.canvas.setStrokeColor(HexColor("#E5E7EB"))
        page.canvas.setLineWidth(0.7)
        page.canvas.line(page.left, page.y, page.right, page.y)
        page.advance(14.0)

    def _flowing(self, page: _RtlCanvas, context: ExportContext) -> None:
        settings = context.settings
        groups = (
            split_paragraphs(
                context.transcript.segments,
                silence_seconds=settings.long_silence_seconds,
                use_silence=settings.detect_long_silences,
            )
            if settings.split_paragraphs
            else [context.transcript.segments]
        )
        for group in groups:
            text = paragraph_text(group)
            if not text:
                continue
            speaker = group[0].speaker
            if speaker:
                label = context.transcript.speakers.get(speaker, speaker)
                page.paragraph(
                    f"{label}:", font=FONT_NAME_BOLD, size=self.style.body_size, gap_after=1.0
                )
            page.paragraph(text)

    def _timestamped(self, page: _RtlCanvas, context: ExportContext) -> None:
        style = self.style
        gutter = style.timestamp_gutter
        # Text occupies everything except the timestamp gutter on the left.
        text_right = page.right
        text_width = page.text_width - gutter
        speakers = context.transcript.speakers

        for segment in context.transcript.segments:
            text = segment.text.strip()
            if not text:
                continue
            if segment.speaker:
                label = speakers.get(segment.speaker, segment.speaker)
                text = f"{label}: {text}"

            lines = page.wrap(text, FONT_NAME, style.body_size, text_width)
            if not lines:
                continue
            page.ensure_space(style.line_height * len(lines))

            # The stamp is Latin digits — draw it as-is, not shaped, at the
            # far left, aligned with the segment's first line.
            page.canvas.setFont(FONT_NAME, style.timestamp_size)
            page.canvas.setFillColor(style.muted_color)
            page.canvas.drawString(page.left, page.y, format_clock(segment.start))

            for line in lines:
                page.ensure_space(style.line_height)
                page.draw_line(line, size=style.body_size, right_edge=text_right)
                page.advance()
            page.advance(4.0)


def write_simple_pdf(
    path: Path,
    *,
    title: str,
    meta: list[str],
    blocks: list[tuple[str | None, list[str]]],
    style: PageStyle | None = None,
) -> Path:
    """Write a plain right-to-left PDF made of headings and paragraphs.

    Shares the transcript exporter's layout engine — wrap in logical order,
    shape each finished line — so Hebrew comes out correct here too.
    """
    register_fonts()
    style = style or PageStyle()
    try:
        page = _RtlCanvas(path, style, title)
        page.paragraph(title, font=FONT_NAME_BOLD, size=style.title_size, gap_after=4.0)
        for line in meta:
            page.paragraph(
                line, size=style.meta_size, color=style.muted_color, gap_after=2.0
            )
        page.canvas.setStrokeColor(HexColor("#E5E7EB"))
        page.canvas.setLineWidth(0.7)
        page.canvas.line(page.left, page.y, page.right, page.y)
        page.advance(14.0)

        for heading, lines in blocks:
            if heading:
                page.ensure_space(style.line_height * 2)
                page.paragraph(
                    heading, font=FONT_NAME_BOLD, size=style.body_size + 1.0, gap_after=2.0
                )
            for line in lines:
                page.paragraph(line)
        page.finish()
    except ExportError:
        raise
    except OSError as exc:
        raise ExportError(f"could not write {path.name}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - reportlab raises broadly
        raise ExportError(f"PDF generation failed: {exc}") from exc
    return path
