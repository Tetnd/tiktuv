"""Deterministic in-process transcriber for tests and offline UI work.

Activated with ``TIKTUV_ASR_BACKEND=mock``. It produces plausible Hebrew
segments at a fixed cadence derived from the audio length, so the full job
pipeline — chunking, merging, progress, persistence, exports — can be tested
end to end without any model download, GPU or network access.

It deliberately *repeats* text inside the overlap region, so the chunk-boundary
de-duplication is actually exercised rather than assumed.
"""

from __future__ import annotations

import hashlib

import numpy as np

from ..logging_conf import get_logger
from ..schemas import Segment, TimestampMode, Word
from .base import TranscribeRequest, TranscribeResult

log = get_logger("asr.mock")

MOCK_MODEL_ID = "tiktuv/mock-hebrew"

#: A small Hebrew phrase bank. Real words, so RTL rendering, punctuation
#: handling and DOCX/PDF shaping are all exercised for real.
_PHRASES: tuple[str, ...] = (
    "שלום וברוכים הבאים לתמלול העברי.",
    "המערכת מעבדת את ההקלטה באופן מקומי לחלוטין.",
    "אין העלאה של קבצים לשום שירות חיצוני.",
    "אפשר לערוך כל מקטע ולתקן שגיאות.",
    "הייצוא תומך בקבצי DOCX ו-PDF בכיוון מימין לשמאל.",
    "ניתן לייצא כתוביות בפורמט SRT או VTT.",
    "התמלול נשמר גם אם היישום נסגר באמצע.",
    "הקלטות ארוכות מעובדות במקטעים כדי לחסוך בזיכרון.",
)

#: Seconds of speech per generated segment.
_SEGMENT_SECONDS = 4.0


class MockTranscriber:
    """Implements :class:`~tiktuv.asr.base.Transcriber`."""

    def __init__(self, model_id: str = MOCK_MODEL_ID) -> None:
        self._model_id = model_id
        self._unloaded = False

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def device(self) -> str:
        return "cpu"

    def transcribe(self, request: TranscribeRequest) -> TranscribeResult:
        if self._unloaded:
            raise RuntimeError("mock transcriber has been unloaded")

        samples = np.asarray(request.samples, dtype=np.float32)
        duration = samples.size / max(1, request.sample_rate)
        if duration <= 0.05:
            return TranscribeResult(segments=[], language="he")

        # Stable phrase choice per window content, so reruns are identical.
        digest = hashlib.sha256(samples[:4096].tobytes()).digest()
        offset = digest[0] % len(_PHRASES)

        count = max(1, int(duration // _SEGMENT_SECONDS))
        want_words = request.settings.timestamps is TimestampMode.WORD

        segments: list[Segment] = []
        for index in range(count):
            start = index * _SEGMENT_SECONDS
            end = min(duration, start + _SEGMENT_SECONDS)
            if end - start < 0.4:
                break
            text = _PHRASES[(offset + index) % len(_PHRASES)]
            segments.append(
                Segment(
                    id=index,
                    start=start,
                    end=end,
                    text=text,
                    words=_fake_words(text, start, end) if want_words else [],
                )
            )
        return TranscribeResult(segments=segments, language="he")

    def unload(self) -> None:
        self._unloaded = True


def _fake_words(text: str, start: float, end: float) -> list[Word]:
    """Spread word timestamps evenly across the segment."""
    tokens = text.split()
    if not tokens:
        return []
    step = (end - start) / len(tokens)
    return [
        Word(
            text=token,
            start=start + index * step,
            end=start + (index + 1) * step,
            probability=0.95,
        )
        for index, token in enumerate(tokens)
    ]


class OverlapEchoTranscriber(MockTranscriber):
    """Mock that emits identical text in every window's leading overlap.

    Used by the chunk-merge tests to prove duplicated words at a boundary are
    removed exactly once, rather than kept or over-trimmed.
    """

    def __init__(self, overlap_seconds: float, model_id: str = MOCK_MODEL_ID) -> None:
        super().__init__(model_id)
        self.overlap_seconds = overlap_seconds

    def transcribe(self, request: TranscribeRequest) -> TranscribeResult:
        result = super().transcribe(request)
        if not result.segments:
            return result
        echo = Segment(
            id=-1,
            start=0.0,
            end=min(self.overlap_seconds, result.segments[0].start or self.overlap_seconds),
            text="הקלטות ארוכות מעובדות במקטעים כדי לחסוך בזיכרון.",
        )
        segments = [echo, *result.segments]
        for index, segment in enumerate(segments):
            segments[index] = segment.model_copy(update={"id": index})
        return TranscribeResult(segments=segments, language=result.language)
