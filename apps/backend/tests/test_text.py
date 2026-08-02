"""Hebrew post-processing, bidi handling and subtitle shaping."""

from __future__ import annotations

import unicodedata

import pytest

from tiktuv.schemas import Segment
from tiktuv.text.hebrew import (
    LRM,
    RLM,
    format_clock,
    format_timestamp,
    has_hebrew,
    is_rtl_dominant,
    normalize_punctuation,
    normalize_unicode,
    paragraph_text,
    post_process_segments,
    protect_neutrals,
    split_paragraphs,
    strip_fillers,
    wrap_bidi_numbers,
)
from tiktuv.text.subtitles import build_cues, split_segment, wrap_lines


def seg(start: float, end: float, text: str, ident: int = 0, speaker: str | None = None) -> Segment:
    return Segment(id=ident, start=start, end=end, text=text, speaker=speaker)


# --------------------------------------------------------------------------
# The no-reversal guarantee
# --------------------------------------------------------------------------
class TestNoStringReversal:
    """Hebrew must be stored in logical order everywhere except PDF painting."""

    HEBREW = "שלום עולם"

    def test_normalize_preserves_character_order(self) -> None:
        assert normalize_unicode(self.HEBREW) == self.HEBREW

    def test_punctuation_normalization_preserves_order(self) -> None:
        assert normalize_punctuation(self.HEBREW) == self.HEBREW

    def test_first_character_stays_first(self) -> None:
        # If anything reversed the string, 'ש' would no longer lead.
        assert normalize_punctuation("שלום, עולם.")[0] == "ש"

    def test_post_processing_preserves_order(self) -> None:
        segments = post_process_segments(
            [seg(0, 1, "אני הולך הביתה")], preserve_fillers=True, normalize=True
        )
        assert segments[0].text == "אני הולך הביתה"

    def test_marks_are_added_not_substituted(self) -> None:
        """protect_neutrals appends a mark; it must not alter the letters."""
        result = protect_neutrals("שלום עולם.")
        assert result.replace(RLM, "") == "שלום עולם."


class TestHebrewDetection:
    def test_detects_hebrew(self) -> None:
        assert has_hebrew("שלום")
        assert not has_hebrew("hello 123")

    def test_rtl_dominance(self) -> None:
        assert is_rtl_dominant("שלום עולם")
        assert is_rtl_dominant("שלום test")  # 4 vs 4 — a tie goes to RTL
        assert not is_rtl_dominant("hello world שלום")


class TestNormalizeUnicode:
    def test_composes_to_nfc(self) -> None:
        decomposed = unicodedata.normalize("NFD", "שָׁלוֹם")
        assert normalize_unicode(decomposed) == unicodedata.normalize("NFC", "שָׁלוֹם")

    def test_strips_bidi_control_characters(self) -> None:
        for control in "‪‫‬‭‮⁦⁧⁨⁩":
            assert control not in normalize_unicode(f"שלום{control}עולם")

    def test_keeps_the_letters_when_stripping_controls(self) -> None:
        assert normalize_unicode("שלום‮עולם") == "שלוםעולם"


class TestNormalizePunctuation:
    def test_removes_space_before_punctuation(self) -> None:
        assert normalize_punctuation("שלום , עולם .") == "שלום, עולם."

    def test_adds_space_after_punctuation(self) -> None:
        assert normalize_punctuation("שלום,עולם") == "שלום, עולם"

    def test_collapses_repeated_punctuation(self) -> None:
        assert normalize_punctuation("מה קורה????") == "מה קורה?"

    def test_collapses_whitespace(self) -> None:
        assert normalize_punctuation("שלום     עולם") == "שלום עולם"

    def test_strips_a_leading_comma(self) -> None:
        assert normalize_punctuation(", שלום") == "שלום"

    def test_does_not_split_decimal_numbers(self) -> None:
        assert normalize_punctuation("המחיר 12.50 שקלים") == "המחיר 12.50 שקלים"

    def test_handles_empty_input(self) -> None:
        assert normalize_punctuation("") == ""


class TestFillers:
    def test_removes_hebrew_fillers(self) -> None:
        assert strip_fillers("אה אני חושב אמ שכן") == "אני חושב שכן"

    def test_removes_fillers_with_trailing_punctuation(self) -> None:
        assert "כאילו" not in strip_fillers("זה, כאילו, מסובך")

    def test_keeps_real_words(self) -> None:
        assert strip_fillers("אני הולך הביתה") == "אני הולך הביתה"

    def test_preserve_option_keeps_them(self) -> None:
        result = post_process_segments(
            [seg(0, 1, "אה אני חושב")], preserve_fillers=True, normalize=True
        )
        assert result[0].text == "אה אני חושב"

    def test_removal_option_drops_them(self) -> None:
        result = post_process_segments(
            [seg(0, 1, "אה אני חושב")], preserve_fillers=False, normalize=True
        )
        assert result[0].text == "אני חושב"

    def test_segments_that_become_empty_are_dropped(self) -> None:
        result = post_process_segments(
            [seg(0, 1, "אה אמ"), seg(1, 2, "טקסט אמיתי")],
            preserve_fillers=False,
            normalize=True,
        )
        assert [s.text for s in result] == ["טקסט אמיתי"]
        assert [s.id for s in result] == [0]


class TestBidiMarks:
    def test_appends_rlm_after_a_trailing_period(self) -> None:
        assert protect_neutrals("שלום עולם.").endswith(RLM)

    def test_does_not_mark_plain_latin(self) -> None:
        assert protect_neutrals("hello world.") == "hello world."

    def test_does_not_mark_a_hebrew_final_letter(self) -> None:
        assert protect_neutrals("שלום עולם") == "שלום עולם"

    def test_isolates_latin_runs_inside_hebrew(self) -> None:
        result = wrap_bidi_numbers("המערכת משתמשת ב-Whisper לתמלול")
        assert LRM in result
        assert result.replace(LRM, "") == "המערכת משתמשת ב-Whisper לתמלול"

    def test_isolates_version_numbers(self) -> None:
        result = wrap_bidi_numbers("גרסה 3.5 של המודל")
        assert result.replace(LRM, "") == "גרסה 3.5 של המודל"

    def test_leaves_pure_latin_text_alone(self) -> None:
        assert wrap_bidi_numbers("hello world") == "hello world"


class TestTimestampFormatting:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(0.0, "00:00:00,000"), (1.5, "00:00:01,500"), (61.25, "00:01:01,250"),
         (3661.007, "01:01:01,007")],
    )
    def test_srt_format(self, seconds: float, expected: str) -> None:
        assert format_timestamp(seconds, separator=",") == expected

    def test_vtt_uses_a_period(self) -> None:
        assert format_timestamp(1.5, separator=".") == "00:00:01.500"

    def test_negative_time_clamps_to_zero(self) -> None:
        assert format_timestamp(-5.0) == "00:00:00,000"

    def test_rounds_rather_than_truncates(self) -> None:
        assert format_timestamp(1.9999) == "00:00:02,000"

    @pytest.mark.parametrize(
        ("seconds", "expected"), [(0, "00:00"), (65, "01:05"), (3725, "1:02:05")]
    )
    def test_clock_format(self, seconds: float, expected: str) -> None:
        assert format_clock(seconds) == expected


class TestParagraphs:
    def test_splits_on_a_long_silence(self) -> None:
        segments = [
            seg(0.0, 3.0, "משפט ראשון."),
            seg(9.0, 12.0, "משפט שני."),
        ]
        paragraphs = split_paragraphs(segments, silence_seconds=2.5)
        assert len(paragraphs) == 2

    def test_keeps_close_segments_together(self) -> None:
        segments = [seg(0.0, 3.0, "חלק אחד."), seg(3.2, 6.0, "חלק שני.")]
        assert len(split_paragraphs(segments, silence_seconds=2.5)) == 1

    def test_splits_on_a_speaker_change(self) -> None:
        segments = [
            seg(0.0, 3.0, "שלום.", speaker="s0"),
            seg(3.1, 6.0, "שלום גם לך.", speaker="s1"),
        ]
        assert len(split_paragraphs(segments)) == 2

    def test_silence_detection_can_be_disabled(self) -> None:
        segments = [seg(0.0, 3.0, "אחד."), seg(30.0, 33.0, "שתיים.")]
        assert len(split_paragraphs(segments, use_silence=False)) == 1

    def test_empty_input(self) -> None:
        assert split_paragraphs([]) == []

    def test_paragraph_text_joins_with_spaces(self) -> None:
        assert paragraph_text([seg(0, 1, "שלום"), seg(1, 2, "עולם")]) == "שלום עולם"


# --------------------------------------------------------------------------
# Subtitles
# --------------------------------------------------------------------------
class TestWrapLines:
    def test_never_breaks_a_word(self) -> None:
        lines = wrap_lines("אחת שתיים שלוש ארבע חמש", 12, 2)
        assert all(len(line) <= 12 or " " not in line for line in lines)
        for line in lines:
            assert not line.startswith(" ") and not line.endswith(" ")

    def test_respects_the_line_limit(self) -> None:
        assert len(wrap_lines(" ".join(["מילה"] * 40), 20, 2)) <= 2

    def test_short_text_is_a_single_line(self) -> None:
        assert wrap_lines("שלום", 42, 2) == ["שלום"]

    def test_empty_text(self) -> None:
        assert wrap_lines("", 42, 2) == []

    def test_a_word_longer_than_the_limit_is_kept_whole(self) -> None:
        long_word = "א" * 60
        assert wrap_lines(long_word, 42, 2) == [long_word]


class TestSplitSegment:
    def test_short_segment_is_untouched(self) -> None:
        segment = seg(0.0, 3.0, "טקסט קצר")
        assert split_segment(segment, 42, 2) == [segment]

    def test_long_segment_is_split_and_retimed(self) -> None:
        text = " ".join(["מילה"] * 60)
        parts = split_segment(seg(0.0, 30.0, text), 42, 2)
        assert len(parts) > 1
        assert parts[0].start == 0.0
        assert parts[-1].end == 30.0
        for previous, current in zip(parts, parts[1:], strict=False):
            assert current.start >= previous.start
            assert previous.end <= current.start + 1e-9

    def test_no_words_are_lost_in_a_split(self) -> None:
        text = " ".join(f"מילה{i}" for i in range(50))
        parts = split_segment(seg(0.0, 30.0, text), 30, 2)
        assert " ".join(p.text for p in parts).split() == text.split()

    def test_empty_segment_produces_nothing(self) -> None:
        assert split_segment(seg(0.0, 1.0, "   "), 42, 2) == []


class TestBuildCues:
    def test_indices_are_one_based_and_sequential(self) -> None:
        cues = build_cues([seg(0, 2, "אחד"), seg(2, 4, "שתיים")])
        assert [cue.index for cue in cues] == [1, 2]

    def test_cues_never_overlap(self) -> None:
        cues = build_cues([seg(0, 3, "אחד"), seg(2.5, 5, "שתיים")])
        for previous, current in zip(cues, cues[1:], strict=False):
            assert current.start >= previous.end - 1e-9

    def test_enforces_a_minimum_duration(self) -> None:
        cues = build_cues([seg(0.0, 0.1, "קצר")])
        assert cues[0].end - cues[0].start >= 0.7 - 1e-9

    def test_enforces_a_maximum_duration(self) -> None:
        cues = build_cues([seg(0.0, 60.0, "ארוך")])
        assert cues[0].end - cues[0].start <= 7.0 + 1e-9

    def test_respects_the_line_budget(self) -> None:
        text = " ".join(["מילה"] * 40)
        cues = build_cues([seg(0, 20, text)], max_line_length=30, max_lines=2)
        for cue in cues:
            assert len(cue.lines) <= 2
            for line in cue.lines:
                assert len(line) <= 30 or " " not in line

    def test_speaker_label_is_prefixed(self) -> None:
        cues = build_cues([seg(0, 3, "שלום", speaker="s0")], speakers={"s0": "דובר 1"})
        assert "דובר 1" in cues[0].text

    def test_empty_segments_are_skipped(self) -> None:
        assert build_cues([seg(0, 2, "   ")]) == []

    def test_bidi_protection_can_be_disabled(self) -> None:
        cues = build_cues([seg(0, 2, "שלום.")], protect_bidi=False)
        assert RLM not in cues[0].text
