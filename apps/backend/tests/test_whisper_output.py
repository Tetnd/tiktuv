"""Normalising the Transformers ASR pipeline's output.

These cover the shapes real Whisper actually returns — including the awkward
ones observed running ``ivrit-ai/whisper-large-v3``: a missing end timestamp
("Whisper did not predict an ending timestamp"), and a run of words that all
carry the same timestamp.

No model is loaded here; these functions are pure.
"""

from __future__ import annotations

from typing import Any

import pytest

from tiktuv.asr.whisper_hf import (
    _chunk_bounds,
    _clear_forced_decoder_ids,
    _ensure_duration,
    _redistribute_words,
    _segments_from_chunks,
    _segments_from_words,
)
from tiktuv.schemas import Segment, Word


def chunk(text: str, start: float | None, end: float | None) -> dict[str, Any]:
    return {"text": text, "timestamp": (start, end)}


class TestChunkBounds:
    def test_reads_a_complete_timestamp(self) -> None:
        assert _chunk_bounds(chunk("שלום", 1.0, 2.5), 0.0) == (1.0, 2.5)

    def test_missing_end_falls_back_to_the_start(self) -> None:
        assert _chunk_bounds(chunk("שלום", 1.0, None), 0.0) == (1.0, 1.0)

    def test_missing_start_uses_the_cursor(self) -> None:
        assert _chunk_bounds(chunk("שלום", None, 3.0), 2.0) == (2.0, 3.0)

    def test_missing_both_uses_the_cursor(self) -> None:
        assert _chunk_bounds(chunk("שלום", None, None), 4.0) == (4.0, 4.0)

    def test_absent_timestamp_key(self) -> None:
        assert _chunk_bounds({"text": "שלום"}, 1.5) == (1.5, 1.5)

    def test_end_before_start_is_clamped(self) -> None:
        assert _chunk_bounds(chunk("שלום", 5.0, 2.0), 0.0) == (5.0, 5.0)


class TestEnsureDuration:
    def test_leaves_a_valid_segment_alone(self) -> None:
        segment = Segment(id=0, start=1.0, end=2.0, text="שלום")
        assert _ensure_duration(segment) is segment

    def test_expands_a_zero_length_segment(self) -> None:
        repaired = _ensure_duration(Segment(id=0, start=3.0, end=3.0, text="שלום"))
        assert repaired.end > repaired.start

    def test_respects_the_window_limit(self) -> None:
        repaired = _ensure_duration(
            Segment(id=0, start=5.98, end=5.98, text="שלום"), limit=6.0
        )
        assert repaired.end <= 6.0
        assert repaired.end > repaired.start

    def test_extends_backwards_at_the_very_end_of_a_window(self) -> None:
        repaired = _ensure_duration(
            Segment(id=0, start=6.0, end=6.0, text="שלום"), limit=6.0
        )
        assert repaired.end <= 6.0
        assert repaired.end > repaired.start
        assert repaired.start >= 0.0


class TestRedistributeWords:
    def test_leaves_increasing_timestamps_alone(self) -> None:
        segment = Segment(
            id=0, start=0.0, end=2.0, text="שלום עולם",
            words=[
                Word(text="שלום", start=0.0, end=1.0),
                Word(text="עולם", start=1.0, end=2.0),
            ],
        )
        assert _redistribute_words(segment) is segment

    def test_spreads_identical_timestamps_across_the_segment(self) -> None:
        segment = Segment(
            id=0, start=4.0, end=6.0, text="תודה רבה",
            words=[
                Word(text="תודה", start=6.0, end=6.0),
                Word(text="רבה", start=6.0, end=6.0),
            ],
        )
        repaired = _redistribute_words(segment)
        starts = [w.start for w in repaired.words]
        assert starts == sorted(starts)
        assert starts[0] == pytest.approx(4.0)
        assert repaired.words[-1].end == pytest.approx(6.0)

    def test_single_word_is_untouched(self) -> None:
        segment = Segment(
            id=0, start=0.0, end=1.0, text="שלום",
            words=[Word(text="שלום", start=0.0, end=0.0)],
        )
        assert _redistribute_words(segment) is segment


class TestSegmentsFromChunks:
    def test_builds_segments(self) -> None:
        output = {"chunks": [chunk("שלום.", 0.0, 1.5), chunk("עולם.", 1.5, 3.0)]}
        segments = _segments_from_chunks(output, 3.0)
        assert [s.text for s in segments] == ["שלום.", "עולם."]
        assert segments[0].start == 0.0
        assert segments[-1].end == 3.0

    def test_text_only_output_spans_the_window(self) -> None:
        segments = _segments_from_chunks({"text": "שלום עולם"}, 6.0)
        assert len(segments) == 1
        assert segments[0].start == 0.0
        assert segments[0].end == 6.0

    def test_empty_output(self) -> None:
        assert _segments_from_chunks({"text": "   "}, 6.0) == []
        assert _segments_from_chunks({}, 6.0) == []

    def test_every_segment_has_a_positive_duration(self) -> None:
        """Regression: a missing end timestamp produced start == end."""
        output = {"chunks": [chunk("תודה רבה.", 5.98, None)]}
        segments = _segments_from_chunks(output, 6.0)
        assert segments
        for segment in segments:
            assert segment.end > segment.start

    def test_blank_chunks_are_skipped(self) -> None:
        output = {"chunks": [chunk("  ", 0.0, 1.0), chunk("שלום", 1.0, 2.0)]}
        assert [s.text for s in _segments_from_chunks(output, 2.0)] == ["שלום"]


class TestSegmentsFromWords:
    def test_groups_words_into_a_segment(self) -> None:
        output = {
            "chunks": [
                chunk(" שלום", 0.0, 0.5),
                chunk(" עולם", 0.5, 1.0),
            ]
        }
        segments = _segments_from_words(output, 6.0)
        assert len(segments) == 1
        assert segments[0].text == "שלום עולם"
        assert len(segments[0].words) == 2

    def test_sentence_punctuation_ends_a_segment(self) -> None:
        output = {
            "chunks": [
                chunk(" שלום.", 0.0, 0.5),
                chunk(" עולם", 0.6, 1.0),
            ]
        }
        assert len(_segments_from_words(output, 6.0)) == 2

    def test_a_long_pause_ends_a_segment(self) -> None:
        output = {
            "chunks": [
                chunk(" שלום", 0.0, 0.5),
                chunk(" עולם", 3.0, 3.5),
            ]
        }
        assert len(_segments_from_words(output, 6.0)) == 2

    def test_degenerate_timestamps_still_produce_a_usable_segment(self) -> None:
        """Regression: every word at 5.98 gave a 5.98-5.98 segment."""
        output = {
            "chunks": [
                chunk(" תודה", 5.98, None),
                chunk(" רבה.", 5.98, None),
            ]
        }
        segments = _segments_from_words(output, 6.0)
        assert len(segments) == 1
        segment = segments[0]
        assert segment.end > segment.start
        assert segment.end <= 6.0
        starts = [w.start for w in segment.words]
        assert starts == sorted(starts)

    def test_falls_back_when_there_are_no_chunks(self) -> None:
        segments = _segments_from_words({"text": "שלום"}, 4.0)
        assert len(segments) == 1
        assert segments[0].end == 4.0


class TestClearForcedDecoderIds:
    class _Holder:
        def __init__(self, ids: object) -> None:
            self.forced_decoder_ids = ids

    class _Model:
        def __init__(self, ids: object) -> None:
            self.generation_config = TestClearForcedDecoderIds._Holder(ids)
            self.config = TestClearForcedDecoderIds._Holder(ids)

    def test_clears_baked_in_ids(self) -> None:
        model = self._Model([[1, None], [2, 50360]])
        _clear_forced_decoder_ids(model)
        assert model.generation_config.forced_decoder_ids is None
        assert model.config.forced_decoder_ids is None

    def test_is_a_no_op_when_already_unset(self) -> None:
        model = self._Model(None)
        _clear_forced_decoder_ids(model)
        assert model.generation_config.forced_decoder_ids is None

    def test_tolerates_a_model_without_the_attributes(self) -> None:
        _clear_forced_decoder_ids(object())
