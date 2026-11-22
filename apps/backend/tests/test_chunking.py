"""Chunk merging, boundary de-duplication and timestamp reconstruction."""

from __future__ import annotations

import pytest

from tiktuv.asr.chunking import (
    TranscriptAccumulator,
    _longest_repeat,
    clamp_words_to_segment,
    normalize_token,
    shift_segments,
    tokenize,
)
from tiktuv.schemas import Segment, Word


def seg(start: float, end: float, text: str, ident: int = 0, **kwargs: object) -> Segment:
    return Segment(id=ident, start=start, end=end, text=text, **kwargs)  # type: ignore[arg-type]


class TestNormalization:
    def test_strips_niqqud(self) -> None:
        assert normalize_token("שָׁלוֹם") == normalize_token("שלום")

    def test_strips_punctuation(self) -> None:
        assert normalize_token("שלום,") == "שלום"
        assert normalize_token("שלום.") == "שלום"
        assert normalize_token('"שלום"') == "שלום"

    def test_strips_geresh_and_gershayim(self) -> None:
        assert normalize_token("צה״ל") == "צהל"
        assert normalize_token("ג׳ון") == "גון"

    def test_folds_latin_case(self) -> None:
        assert normalize_token("Hello") == normalize_token("hello")

    def test_keeps_digits(self) -> None:
        assert normalize_token("2026") == "2026"

    def test_tokenize_drops_empties(self) -> None:
        assert tokenize("שלום ,  עולם !") == ["שלום", "עולם"]


class TestShiftSegments:
    def test_moves_segments_onto_the_absolute_timeline(self) -> None:
        shifted = shift_segments([seg(0.0, 2.0, "א"), seg(2.0, 4.0, "ב")], 100.0)
        assert [(s.start, s.end) for s in shifted] == [(100.0, 102.0), (102.0, 104.0)]

    def test_moves_word_timestamps_too(self) -> None:
        source = seg(0.0, 2.0, "שלום עולם")
        source = source.model_copy(
            update={
                "words": [
                    Word(text="שלום", start=0.0, end=1.0),
                    Word(text="עולם", start=1.0, end=2.0),
                ]
            }
        )
        shifted = shift_segments([source], 50.0)[0]
        assert [(w.start, w.end) for w in shifted.words] == [(50.0, 51.0), (51.0, 52.0)]

    def test_does_not_mutate_the_input(self) -> None:
        original = seg(0.0, 2.0, "א")
        shift_segments([original], 10.0)
        assert original.start == 0.0


class TestLongestRepeat:
    def test_finds_a_repeated_run(self) -> None:
        head = ["א", "ב", "ג", "ד"]
        tail = ["ג", "ד", "ה", "ו"]
        assert _longest_repeat(head, tail) == 2

    def test_returns_zero_without_a_repeat(self) -> None:
        assert _longest_repeat(["א", "ב"], ["ג", "ד"]) == 0

    def test_ignores_a_single_token_coincidence(self) -> None:
        """One shared common word is not evidence of a duplicated run."""
        assert _longest_repeat(["של"], ["של"]) == 0

    def test_prefers_the_longest_run(self) -> None:
        head = ["א", "ב", "ג"]
        tail = ["א", "ב", "ג", "ד"]
        assert _longest_repeat(head, tail) == 3


class TestAccumulatorBasics:
    def test_first_window_is_kept_verbatim(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=2.0)
        acc.add_window([seg(0.0, 3.0, "שלום")], window_start=0.0, overlap_lead=0.0)
        assert [s.text for s in acc.segments()] == ["שלום"]

    def test_windows_without_overlap_are_concatenated(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=0.0)
        acc.add_window([seg(0.0, 3.0, "ראשון")], window_start=0.0, overlap_lead=0.0)
        acc.add_window([seg(3.0, 6.0, "שני")], window_start=3.0, overlap_lead=0.0)
        assert [s.text for s in acc.segments()] == ["ראשון", "שני"]

    def test_ids_are_renumbered_densely(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=0.0)
        acc.add_window(
            [seg(0.0, 1.0, "א", ident=7), seg(1.0, 2.0, "ב", ident=99)],
            window_start=0.0,
            overlap_lead=0.0,
        )
        assert [s.id for s in acc.segments()] == [0, 1]

    def test_empty_segments_are_dropped(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=0.0)
        acc.add_window(
            [seg(0.0, 1.0, "  "), seg(1.0, 2.0, "טקסט")], window_start=0.0, overlap_lead=0.0
        )
        assert [s.text for s in acc.segments()] == ["טקסט"]

    def test_output_is_sorted_and_monotonic(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=0.0)
        acc.add_window(
            [seg(5.0, 6.0, "מאוחר"), seg(1.0, 2.0, "מוקדם")],
            window_start=0.0,
            overlap_lead=0.0,
        )
        segments = acc.segments()
        assert [s.text for s in segments] == ["מוקדם", "מאוחר"]
        for previous, current in zip(segments, segments[1:], strict=False):
            assert current.start >= previous.end - 1e-9


class TestBoundaryDeduplication:
    """The core guarantee: a word spoken once appears once."""

    def test_identical_overlap_text_is_not_duplicated(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=4.0)
        # Window 1 covers 0–20 s.
        acc.add_window(
            [seg(10.0, 14.0, "המשפט הזה נמצא בחפיפה"), seg(14.0, 18.0, "וזה סוף החלון")],
            window_start=0.0,
            overlap_lead=0.0,
        )
        # Window 2 starts at 16 s and re-hears 16–20 s.
        acc.add_window(
            [seg(16.0, 18.0, "וזה סוף החלון"), seg(18.0, 22.0, "וזה טקסט חדש")],
            window_start=16.0,
            overlap_lead=4.0,
        )
        text = " ".join(s.text for s in acc.segments())
        assert text.count("וזה סוף החלון") == 1
        assert "וזה טקסט חדש" in text

    def test_partial_word_overlap_is_removed_once(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=4.0)
        acc.add_window([seg(14.0, 19.0, "אחת שתיים שלוש ארבע")], window_start=0.0, overlap_lead=0.0)
        acc.add_window(
            [seg(17.0, 22.0, "שלוש ארבע חמש שש")], window_start=16.0, overlap_lead=4.0
        )
        tokens = tokenize(" ".join(s.text for s in acc.segments()))
        assert tokens.count("שלוש") == 1
        assert tokens.count("ארבע") == 1
        assert "חמש" in tokens and "שש" in tokens

    def test_distinct_text_across_a_boundary_is_all_kept(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=4.0)
        acc.add_window([seg(10.0, 17.0, "טקסט ראשון לגמרי")], window_start=0.0, overlap_lead=0.0)
        acc.add_window([seg(19.0, 24.0, "טקסט שני שונה")], window_start=16.0, overlap_lead=4.0)
        text = " ".join(s.text for s in acc.segments())
        assert "טקסט ראשון לגמרי" in text
        assert "טקסט שני שונה" in text

    def test_seam_falls_in_the_middle_of_the_overlap(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=4.0)
        # Centre 17.0 -> before the 18.0 seam, so it belongs to window 1.
        acc.add_window([seg(16.0, 18.0, "שייך לחלון הראשון")], window_start=0.0, overlap_lead=0.0)
        # Centre 19.0 -> after the seam, so it belongs to window 2.
        acc.add_window(
            [seg(16.2, 17.0, "כפילות שיש להסיר"), seg(18.0, 20.0, "שייך לחלון השני")],
            window_start=16.0,
            overlap_lead=4.0,
        )
        texts = [s.text for s in acc.segments()]
        assert "שייך לחלון הראשון" in texts
        assert "שייך לחלון השני" in texts
        assert "כפילות שיש להסיר" not in texts

    def test_boundary_segments_are_flagged(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=4.0)
        acc.add_window([seg(10.0, 17.0, "ראשון")], window_start=0.0, overlap_lead=0.0)
        acc.add_window([seg(19.0, 24.0, "שני")], window_start=16.0, overlap_lead=4.0)
        assert any(s.boundary_merged for s in acc.segments())

    def test_three_windows_chain_correctly(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=4.0)
        acc.add_window([seg(2.0, 18.0, "חלק אחד")], window_start=0.0, overlap_lead=0.0)
        acc.add_window([seg(20.0, 34.0, "חלק שתיים")], window_start=16.0, overlap_lead=4.0)
        acc.add_window([seg(36.0, 50.0, "חלק שלוש")], window_start=32.0, overlap_lead=4.0)
        texts = [s.text for s in acc.segments()]
        assert texts == ["חלק אחד", "חלק שתיים", "חלק שלוש"]

    def test_word_timestamps_survive_trimming(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=4.0)
        acc.add_window([seg(14.0, 18.0, "אחת שתיים")], window_start=0.0, overlap_lead=0.0)

        trimmed = seg(17.0, 21.0, "אחת שתיים שלוש")
        trimmed = trimmed.model_copy(
            update={
                "words": [
                    Word(text="אחת", start=17.0, end=18.0),
                    Word(text="שתיים", start=18.0, end=19.0),
                    Word(text="שלוש", start=19.0, end=21.0),
                ]
            }
        )
        acc.add_window([trimmed], window_start=16.0, overlap_lead=4.0)

        remaining = [s for s in acc.segments() if "שלוש" in s.text]
        assert remaining
        # The two duplicated words are gone and the start moved to the third.
        assert remaining[0].text == "שלוש"
        assert remaining[0].start == pytest.approx(19.0)

    def test_resume_offset_tracks_the_committed_end(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=2.0)
        assert acc.last_committed_time() == 0.0
        acc.add_window([seg(0.0, 12.5, "טקסט")], window_start=0.0, overlap_lead=0.0)
        assert acc.last_committed_time() == pytest.approx(12.5)

    def test_a_window_that_produced_nothing_is_harmless(self) -> None:
        acc = TranscriptAccumulator(overlap_seconds=4.0)
        acc.add_window([seg(0.0, 10.0, "טקסט")], window_start=0.0, overlap_lead=0.0)
        acc.add_window([], window_start=16.0, overlap_lead=4.0)
        assert [s.text for s in acc.segments()] == ["טקסט"]


class TestClampWords:
    def test_pulls_words_back_inside_the_segment(self) -> None:
        segment = seg(1.0, 3.0, "שלום עולם").model_copy(
            update={
                "words": [
                    Word(text="שלום", start=0.5, end=2.0),
                    Word(text="עולם", start=2.0, end=3.4),
                ]
            }
        )
        clamped = clamp_words_to_segment(segment)
        assert clamped.words[0].start == 1.0
        assert clamped.words[-1].end == 3.0

    def test_leaves_a_segment_without_words_alone(self) -> None:
        segment = seg(1.0, 3.0, "שלום")
        assert clamp_words_to_segment(segment) is segment


def test_segment_merged_differently_across_seam_is_not_lost() -> None:
    """Real Soniox case: window 1 split a passage in two, window 2 merged it.

    The merged segment's centre falls before the seam and window 1's second
    piece's centre after it, so a naive seam dropped that speech entirely.
    """
    from tiktuv.asr.chunking import TranscriptAccumulator
    from tiktuv.schemas import Segment

    def seg(start: float, end: float, text: str) -> Segment:
        return Segment(id=0, start=start, end=end, text=text)

    accumulator = TranscriptAccumulator(overlap_seconds=30.0)
    accumulator.add_window(
        [
            seg(287.55, 294.99, "ודיברנו גם שלכל פחת"),
            seg(295.11, 299.67, "ודיברנו שמפסק פחת כזה צריך להיות מוגן, בסדר?"),
            seg(299.79, 302.79, "עד עוצמת זרמים של 6 קילו אמפר."),
            seg(303.39, 303.87, "זה מהלך."),
        ],
        window_start=0.0,
        overlap_lead=0.0,
    )
    accumulator.add_window(
        [
            seg(285.33, 295.05, "ודיברנו גם שלכל פחת"),
            seg(
                295.11,
                302.79,
                "ודיברנו שמפסק פחת כזה צריך להיות מוגן, בסדר, עד עוצמת זרמים של 6 קילו אמפר.",
            ),
            seg(303.39, 303.87, "במהלך—"),
        ],
        window_start=285.0,
        overlap_lead=30.0,
    )
    texts = [s.text for s in accumulator.segments()]
    joined = " ".join(texts)
    assert "6 קילו אמפר" in joined
    assert joined.count("ודיברנו שמפסק") == 1
    assert joined.count("ודיברנו גם") == 1
