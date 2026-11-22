"""Chunk merging and timestamp reconstruction.

Each window is transcribed independently, so two windows both "hear" the
shared overlap region and both transcribe it. Naively concatenating produces
duplicated words at every boundary. This module joins windows so that:

* every segment carries an absolute timestamp on the source timeline,
* the seam falls in the middle of the overlap, where a word is least likely to
  be cut, and
* any text the two windows genuinely repeated is removed exactly once.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..logging_conf import get_logger
from ..schemas import Segment, Word

log = get_logger("asr.chunking")

#: How many tokens on either side of a seam we compare when hunting for a
#: repeated run. Whisper rarely repeats more than a short clause.
MAX_DEDUPE_TOKENS = 24
#: Below this, a "match" is more likely to be a common word than a real repeat.
MIN_DEDUPE_TOKENS = 2
#: Timestamps from two windows differ by a few tens of milliseconds.
_SEAM_TOLERANCE = 0.25

_NIQQUD = re.compile(r"[֑-ׇ]")
_NON_WORD = re.compile(r"[^\w֐-׿]+", re.UNICODE)


def normalize_token(token: str) -> str:
    """Fold a token to its comparison form.

    Niqqud, punctuation, Hebrew geresh/gershayim and case are all dropped:
    Whisper is not deterministic about them across windows, and none of them
    should stop us recognising that the same word was transcribed twice.
    """
    token = unicodedata.normalize("NFKD", token)
    token = _NIQQUD.sub("", token)
    token = token.replace("׳", "").replace("״", "")
    token = _NON_WORD.sub("", token)
    return token.casefold()


def tokenize(text: str) -> list[str]:
    return [normalized for raw in text.split() if (normalized := normalize_token(raw))]


def _center(segment: Segment) -> float:
    return (segment.start + segment.end) / 2.0


def shift_segments(segments: list[Segment], offset: float) -> list[Segment]:
    """Move segments (and their words) onto the absolute source timeline."""
    shifted: list[Segment] = []
    for segment in segments:
        shifted.append(
            segment.model_copy(
                update={
                    "start": segment.start + offset,
                    "end": segment.end + offset,
                    "words": [
                        word.model_copy(
                            update={"start": word.start + offset, "end": word.end + offset}
                        )
                        for word in segment.words
                    ],
                }
            )
        )
    return shifted


def _longest_repeat(head_tokens: list[str], tail_tokens: list[str]) -> int:
    """Length of the longest run that ends ``head`` and begins ``tail``."""
    limit = min(len(head_tokens), len(tail_tokens), MAX_DEDUPE_TOKENS)
    for length in range(limit, MIN_DEDUPE_TOKENS - 1, -1):
        if head_tokens[-length:] == tail_tokens[:length]:
            return length
    return 0


def _drop_leading_tokens(segment: Segment, count: int) -> Segment | None:
    """Remove ``count`` leading words from ``segment``, fixing its start time.

    Returns ``None`` when nothing meaningful is left.
    """
    if count <= 0:
        return segment

    raw_words = segment.text.split()
    if count >= len(raw_words):
        return None

    remaining_text = " ".join(raw_words[count:]).strip()
    if not remaining_text:
        return None

    if segment.words and count < len(segment.words):
        # Word timestamps give us an exact new start.
        remaining_words = segment.words[count:]
        new_start = remaining_words[0].start
    else:
        remaining_words = []
        # Otherwise assume words are evenly spread across the segment.
        fraction = count / max(1, len(raw_words))
        new_start = segment.start + (segment.end - segment.start) * fraction

    return segment.model_copy(
        update={
            "text": remaining_text,
            "start": min(new_start, segment.end),
            "words": remaining_words,
            "boundary_merged": True,
        }
    )


@dataclass
class TranscriptAccumulator:
    """Collects per-window segments into one de-duplicated transcript.

    Usage: call :meth:`add_window` once per window in timeline order, then read
    :meth:`segments`. It is safe to read partial results between calls — that
    is exactly what the crash-safe partial snapshots do.
    """

    #: Seconds of overlap between consecutive windows.
    overlap_seconds: float
    _segments: list[Segment] = field(default_factory=list)
    _windows_added: int = 0

    def add_window(
        self,
        window_segments: list[Segment],
        *,
        window_start: float,
        overlap_lead: float,
    ) -> None:
        """Merge one window's segments (already on the absolute timeline)."""
        incoming = sorted(window_segments, key=lambda s: (s.start, s.end))
        incoming = [s for s in incoming if s.text.strip()]

        if not self._segments:
            self._segments = incoming
            self._windows_added += 1
            return

        if overlap_lead <= 0:
            self._segments.extend(incoming)
            self._windows_added += 1
            return

        overlap_start = window_start
        overlap_end = window_start + overlap_lead
        cut = (overlap_start + overlap_end) / 2.0

        # Everything whose centre is before the seam belongs to the previous
        # window; everything after belongs to the new one.
        head = [s for s in self._segments if _center(s) < cut]
        tail = [s for s in incoming if _center(s) >= cut]

        # The two windows may split the overlap differently: the new window
        # can merge into one segment (centre before the seam) speech the old
        # window kept as a separate segment (centre after it). Both sides
        # would then drop it. Keep such a straddling segment and let it
        # replace the head segments it covers.
        head_end = max((s.end for s in head), default=cut)
        straddlers = [
            s for s in incoming if _center(s) < cut and s.end > head_end + _SEAM_TOLERANCE
        ]
        if straddlers:
            first_start = min(s.start for s in straddlers)
            head = [s for s in head if s.start < first_start - _SEAM_TOLERANCE]
            tail = sorted(straddlers + tail, key=lambda s: (s.start, s.end))

        dropped = len(self._segments) - len(head) + len(incoming) - len(tail)

        # Even with a mid-overlap seam, a word can be split across it. Compare
        # the tokens either side and delete a genuine repeated run.
        if head and tail:
            head_tokens = tokenize(" ".join(s.text for s in head[-3:]))
            tail_tokens = tokenize(" ".join(s.text for s in tail[:3]))
            repeat = _longest_repeat(head_tokens, tail_tokens)
            if repeat:
                tail = self._trim_tail(tail, repeat)
                log.debug(
                    "removed repeated run at chunk boundary",
                    extra={"tokens": repeat, "boundary": round(cut, 2)},
                )

        if tail:
            tail[0] = tail[0].model_copy(update={"boundary_merged": True})

        self._segments = head + tail
        self._windows_added += 1
        log.debug(
            "merged window",
            extra={
                "window_start": round(window_start, 2),
                "kept": len(self._segments),
                "dropped_at_seam": dropped,
            },
        )

    @staticmethod
    def _trim_tail(tail: list[Segment], token_count: int) -> list[Segment]:
        """Delete ``token_count`` tokens from the front of ``tail``."""
        remaining = token_count
        result = list(tail)
        while remaining > 0 and result:
            first = result[0]
            available = len(tokenize(first.text))
            if available == 0:
                result.pop(0)
                continue
            if available <= remaining:
                remaining -= available
                result.pop(0)
                continue
            trimmed = _drop_leading_tokens(first, remaining)
            remaining = 0
            if trimmed is None:
                result.pop(0)
            else:
                result[0] = trimmed
        return result

    def segments(self) -> list[Segment]:
        """Return the merged segments, renumbered and monotonic."""
        ordered = sorted(self._segments, key=lambda s: (s.start, s.end))
        result: list[Segment] = []
        previous_end = 0.0
        for index, segment in enumerate(ordered):
            start = max(segment.start, 0.0)
            # Never let a segment start before the previous one ended: players
            # and subtitle renderers treat that as a malformed cue.
            start = max(start, previous_end)
            end = max(segment.end, start)
            previous_end = end
            result.append(segment.model_copy(update={"id": index, "start": start, "end": end}))
        return result

    def last_committed_time(self) -> float:
        """Timeline position that is safe to resume from after a crash."""
        return max((s.end for s in self._segments), default=0.0)


def clamp_words_to_segment(segment: Segment) -> Segment:
    """Ensure word timestamps stay inside their segment.

    Whisper's word alignment occasionally reports a word ending a few
    milliseconds past its segment. Left alone this produces subtitle cues that
    overlap; players then flicker between them.
    """
    if not segment.words:
        return segment
    words: list[Word] = []
    for word in segment.words:
        start = min(max(word.start, segment.start), segment.end)
        end = min(max(word.end, start), segment.end)
        words.append(word.model_copy(update={"start": start, "end": end}))
    return segment.model_copy(update={"words": words})
