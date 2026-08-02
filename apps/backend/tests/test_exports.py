"""Export generation for all six formats, with real file inspection."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tiktuv.errors import ExportError
from tiktuv.exports import SUPPORTED_FORMATS, export_transcript, get_exporter
from tiktuv.exports.base import ExportContext
from tiktuv.exports.pdf_export import PdfExporter, register_fonts, shape_rtl
from tiktuv.schemas import (
    ExportFormat,
    Segment,
    TimestampMode,
    Transcript,
    TranscriptionSettings,
    Word,
)

HEBREW_LINES = [
    "שלום וברוכים הבאים לישיבת ההנהלה.",
    "היום נדון בתקציב לשנת 2026 ובתוכנית העבודה.",
    "מנהל הכספים יציג את הנתונים בעוד מספר דקות.",
    "יש לנו גם עדכון על מערכת Whisper החדשה.",
]


@pytest.fixture
def transcript() -> Transcript:
    segments = [
        Segment(
            id=index,
            start=index * 5.0,
            end=index * 5.0 + 4.5,
            text=text,
            words=[
                Word(text=word, start=index * 5.0 + i * 0.5, end=index * 5.0 + (i + 1) * 0.5)
                for i, word in enumerate(text.split())
            ],
        )
        for index, text in enumerate(HEBREW_LINES)
    ]
    return Transcript(
        job_id="test-job-0001",
        language="he",
        model_id="ivrit-ai/whisper-large-v3",
        duration=20.0,
        segments=segments,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def context(transcript: Transcript, tmp_path: Path) -> ExportContext:
    return ExportContext(
        transcript=transcript,
        settings=TranscriptionSettings(),
        stem="ישיבת הנהלה",
        output_dir=tmp_path / "out",
    )


def _timestamped(context: ExportContext) -> ExportContext:
    return ExportContext(
        transcript=context.transcript,
        settings=context.settings.model_copy(update={"timestamps": TimestampMode.SEGMENT}),
        stem=context.stem,
        output_dir=context.output_dir,
    )


# --------------------------------------------------------------------------
class TestRegistry:
    def test_every_declared_format_has_an_exporter(self) -> None:
        assert set(SUPPORTED_FORMATS) == set(ExportFormat)

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(ExportError):
            get_exporter("nope")  # type: ignore[arg-type]


class TestTxt:
    def test_writes_utf8_hebrew(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.TXT).write(context)
        content = path.read_text(encoding="utf-8-sig")
        assert "שלום וברוכים הבאים" in content

    def test_text_is_in_logical_order(self, context: ExportContext) -> None:
        """The exact source string must appear — proof nothing was reversed."""
        path = get_exporter(ExportFormat.TXT).write(context)
        content = path.read_text(encoding="utf-8-sig")
        for line in HEBREW_LINES:
            assert line in content

    def test_hebrew_filename_is_preserved(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.TXT).write(context)
        assert "ישיבת הנהלה" in path.name

    def test_has_a_utf8_bom_for_notepad(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.TXT).write(context)
        assert path.read_bytes().startswith(b"\xef\xbb\xbf")


class TestTimestampedTxt:
    def test_includes_clock_stamps(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.TXT_TIMESTAMPED).write(context)
        content = path.read_text(encoding="utf-8-sig")
        assert "[00:00]" in content
        assert "[00:05]" in content

    def test_uses_a_distinct_filename(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.TXT_TIMESTAMPED).write(context)
        assert path.name.endswith(".timestamped.txt")


class TestSrt:
    def test_structure_is_valid(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.SRT).write(context)
        # Read the raw bytes: text mode would translate CRLF to LF and hide
        # the very line endings this format depends on.
        content = path.read_bytes().decode("utf-8")
        blocks = [b for b in content.split("\r\n\r\n") if b.strip()]
        assert blocks
        first = blocks[0].split("\r\n")
        assert first[0] == "1"
        assert " --> " in first[1]
        assert first[2].strip()

    def test_timestamps_use_srt_comma_format(self, context: ExportContext) -> None:
        content = get_exporter(ExportFormat.SRT).write(context).read_text(encoding="utf-8")
        assert "00:00:00,000 --> " in content

    def test_no_bom(self, context: ExportContext) -> None:
        """Some hardware players reject a BOM in SRT."""
        path = get_exporter(ExportFormat.SRT).write(context)
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_uses_crlf(self, context: ExportContext) -> None:
        assert b"\r\n" in get_exporter(ExportFormat.SRT).write(context).read_bytes()

    def test_cue_times_increase(self, context: ExportContext) -> None:
        content = get_exporter(ExportFormat.SRT).write(context).read_text(encoding="utf-8")
        starts = [line.split(" --> ")[0] for line in content.splitlines() if " --> " in line]
        assert starts == sorted(starts)

    def test_is_valid_unicode(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.SRT).write(context)
        path.read_bytes().decode("utf-8")  # raises if invalid


class TestVtt:
    def test_starts_with_the_webvtt_header(self, context: ExportContext) -> None:
        content = get_exporter(ExportFormat.VTT).write(context).read_text(encoding="utf-8")
        assert content.startswith("WEBVTT")

    def test_timestamps_use_vtt_period_format(self, context: ExportContext) -> None:
        content = get_exporter(ExportFormat.VTT).write(context).read_text(encoding="utf-8")
        assert "00:00:00.000 --> " in content

    def test_declares_rtl_direction(self, context: ExportContext) -> None:
        content = get_exporter(ExportFormat.VTT).write(context).read_text(encoding="utf-8")
        assert "direction:rl" in content


class TestJson:
    def test_is_valid_json_with_unescaped_hebrew(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.JSON).write(context)
        raw = path.read_text(encoding="utf-8")
        assert "שלום" in raw  # not של...
        payload = json.loads(raw)
        assert payload["schema"] == "tiktuv.transcript/1"

    def test_includes_segments_and_words(self, context: ExportContext) -> None:
        payload = json.loads(
            get_exporter(ExportFormat.JSON).write(context).read_text(encoding="utf-8")
        )
        assert len(payload["segments"]) == len(HEBREW_LINES)
        assert payload["segments"][0]["words"]

    def test_round_trips_the_text_exactly(self, context: ExportContext) -> None:
        payload = json.loads(
            get_exporter(ExportFormat.JSON).write(context).read_text(encoding="utf-8")
        )
        assert [s["text"] for s in payload["segments"]] == HEBREW_LINES


class TestDocx:
    def test_produces_a_valid_docx_zip(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.DOCX).write(context)
        with zipfile.ZipFile(path) as archive:
            assert "word/document.xml" in archive.namelist()

    def _document_xml(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            return archive.read("word/document.xml").decode("utf-8")

    def test_section_is_rtl(self, context: ExportContext) -> None:
        xml = self._document_xml(get_exporter(ExportFormat.DOCX).write(context))
        assert "<w:bidi" in xml

    def test_runs_are_marked_as_complex_script(self, context: ExportContext) -> None:
        """Without w:rtl, Word renders Hebrew punctuation on the wrong side."""
        xml = self._document_xml(get_exporter(ExportFormat.DOCX).write(context))
        assert "<w:rtl" in xml

    def test_sets_a_complex_script_font(self, context: ExportContext) -> None:
        xml = self._document_xml(get_exporter(ExportFormat.DOCX).write(context))
        assert 'w:cs="David"' in xml

    def test_paragraphs_are_right_aligned(self, context: ExportContext) -> None:
        xml = self._document_xml(get_exporter(ExportFormat.DOCX).write(context))
        assert 'w:val="right"' in xml

    def test_text_is_stored_in_logical_order(self, context: ExportContext) -> None:
        xml = self._document_xml(get_exporter(ExportFormat.DOCX).write(context))
        for line in HEBREW_LINES:
            assert line in xml

    def test_timestamped_mode_emits_a_bidi_table(self, context: ExportContext) -> None:
        xml = self._document_xml(get_exporter(ExportFormat.DOCX).write(_timestamped(context)))
        assert "<w:tbl>" in xml
        assert "<w:bidiVisual" in xml


class TestPdf:
    def test_produces_a_pdf(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.PDF).write(context)
        assert path.read_bytes().startswith(b"%PDF-")
        assert path.stat().st_size > 1000

    def test_registers_a_hebrew_font(self) -> None:
        regular, bold = register_fonts()
        assert regular and bold

    def test_timestamped_mode_works(self, context: ExportContext) -> None:
        path = get_exporter(ExportFormat.PDF).write(_timestamped(context))
        assert path.read_bytes().startswith(b"%PDF-")

    def test_long_transcript_paginates(self, tmp_path: Path) -> None:
        segments = [
            Segment(id=i, start=i * 3.0, end=i * 3.0 + 2.5, text=HEBREW_LINES[i % 4])
            for i in range(200)
        ]
        transcript = Transcript(
            job_id="long",
            language="he",
            model_id="m",
            duration=600.0,
            segments=segments,
        )
        path = PdfExporter().write(
            ExportContext(
                transcript=transcript,
                settings=TranscriptionSettings(),
                stem="ארוך",
                output_dir=tmp_path,
            )
        )
        from pypdf import PdfReader

        assert len(PdfReader(str(path)).pages) > 1

    def test_extracted_text_contains_the_hebrew_characters(
        self, context: ExportContext
    ) -> None:
        """The PDF must embed real Hebrew glyphs, not boxes or mojibake.

        Extraction returns *visual* order (that is inherent to PDF), so the
        assertion is on character content, not on ordering.
        """
        from pypdf import PdfReader

        path = get_exporter(ExportFormat.PDF).write(context)
        text = "".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        for word in ("שלום", "ההנהלה", "תקציב"):
            assert set(word) <= set(text), f"{word!r} characters missing from the PDF"


class TestShapeRtl:
    def test_reorders_for_visual_painting(self) -> None:
        """PDF has no bidi engine, so this step is required — and is the only
        place in Tiktuv where character order changes."""
        assert shape_rtl("שלום") == "םולש"

    def test_is_reversible_for_a_pure_hebrew_run(self) -> None:
        assert shape_rtl(shape_rtl("שלום")) == "שלום"

    def test_keeps_latin_runs_readable(self) -> None:
        assert "Whisper" in shape_rtl("מערכת Whisper החדשה")

    def test_keeps_numbers_readable(self) -> None:
        assert "2026" in shape_rtl("שנת 2026")

    def test_empty_input(self) -> None:
        assert shape_rtl("") == ""


class TestExportTranscript:
    def test_writes_every_requested_format(
        self, transcript: Transcript, tmp_path: Path
    ) -> None:
        results = export_transcript(
            transcript,
            TranscriptionSettings(),
            formats=list(ExportFormat),
            output_dir=tmp_path,
            stem="ישיבה",
        )
        assert len(results) == len(ExportFormat)
        for result in results:
            path = Path(result.path)
            assert path.is_file()
            assert path.stat().st_size > 0
            assert result.size_bytes == path.stat().st_size

    def test_deduplicates_repeated_formats(
        self, transcript: Transcript, tmp_path: Path
    ) -> None:
        results = export_transcript(
            transcript,
            TranscriptionSettings(),
            formats=[ExportFormat.TXT, ExportFormat.TXT, ExportFormat.SRT],
            output_dir=tmp_path,
            stem="a",
        )
        assert len(results) == 2

    def test_empty_format_list_is_a_no_op(
        self, transcript: Transcript, tmp_path: Path
    ) -> None:
        assert export_transcript(
            transcript, TranscriptionSettings(), formats=[], output_dir=tmp_path, stem="a"
        ) == []

    def test_creates_the_output_directory(
        self, transcript: Transcript, tmp_path: Path
    ) -> None:
        target = tmp_path / "חדש" / "תמלולים"
        export_transcript(
            transcript,
            TranscriptionSettings(),
            formats=[ExportFormat.TXT],
            output_dir=target,
            stem="a",
        )
        assert target.is_dir()

    def test_sanitizes_a_hostile_stem(self, transcript: Transcript, tmp_path: Path) -> None:
        results = export_transcript(
            transcript,
            TranscriptionSettings(),
            formats=[ExportFormat.TXT],
            output_dir=tmp_path,
            stem="../../evil",
        )
        written = Path(results[0].path)
        assert written.parent == tmp_path
        assert ".." not in written.name

    def test_empty_transcript_still_produces_files(self, tmp_path: Path) -> None:
        empty = Transcript(job_id="e", language="he", model_id="m", duration=0.0, segments=[])
        results = export_transcript(
            empty,
            TranscriptionSettings(),
            formats=[ExportFormat.TXT, ExportFormat.SRT, ExportFormat.JSON],
            output_dir=tmp_path,
            stem="ריק",
        )
        assert len(results) == 3
