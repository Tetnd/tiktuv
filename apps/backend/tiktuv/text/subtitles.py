"""Subtitle cue shaping.

Turns transcript segments into cues that respect the user's maximum line
length and line count, splitting segments that are too long and re-timing the
halves. Word timestamps are used for the split point when available; otherwise
the split is proportional to character count.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schemas import Segment
from .hebrew import protect_neutrals

#: Never emit a cue shorter than this — it would flash by unreadably.
MIN_CUE_SECONDS = 0.7
#: Nor longer than this; subtitle conventions cap a cue at ~7 s.
MAX_CUE_SECONDS = 7.0


@dataclass(frozen=True, slots=True)
class Cue:
    index: int
    start: float
    end: float
    lines: list[str]
    speaker: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def wrap_lines(text: str, max_length: int, max_lines: int) -> list[str]:
    """Greedy word wrap that never breaks a word.

    Returns at most ``max_lines`` lines; the caller is responsible for having
    split the text first if it does not fit.
    """
    words = text.split()
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_length or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return lines


def _fits(text: str, max_length: int, max_lines: int) -> bool:
    lines = wrap_lines(text, max_length, max_lines)
    return bool(lines) and " ".join(lines).split() == text.split()


def _split_point(segment: Segment, max_chars: int) -> int:
    """Index in ``segment.text.split()`` at which to break."""
    words = segment.text.split()
    if len(words) < 2:
        return 0

    # Prefer breaking after punctuation near the middle.
    middle = len(words) // 2
    for distance in range(0, middle):
        for index in (middle - distance, middle + distance):
            if 0 < index < len(words) and words[index - 1].endswith((",", ".", ";", ":", "?", "!")):
                return index

    # Otherwise break where the accumulated characters cross the budget.
    budget = max_chars
    used = 0
    for index, word in enumerate(words):
        used += len(word) + 1
        if used > budget and index > 0:
            return index
    return middle


def _time_at_word(segment: Segment, word_index: int) -> float:
    """Timeline position of the ``word_index``-th word."""
    if segment.words and word_index < len(segment.words):
        return segment.words[word_index].start
    words = segment.text.split()
    if not words:
        return segment.end
    fraction = word_index / len(words)
    return segment.start + (segment.end - segment.start) * fraction


def split_segment(segment: Segment, max_length: int, max_lines: int) -> list[Segment]:
    """Recursively split a segment until each part fits the cue budget."""
    text = segment.text.strip()
    if not text:
        return []
    capacity = max_length * max_lines
    if len(text) <= capacity and _fits(text, max_length, max_lines):
        return [segment]

    words = text.split()
    if len(words) < 2:
        return [segment]

    index = _split_point(segment, capacity)
    if index <= 0 or index >= len(words):
        return [segment]

    boundary = _time_at_word(segment, index)
    boundary = min(max(boundary, segment.start + 0.05), segment.end - 0.05)

    first = segment.model_copy(
        update={
            "text": " ".join(words[:index]),
            "end": boundary,
            "words": segment.words[:index] if segment.words else [],
        }
    )
    second = segment.model_copy(
        update={
            "text": " ".join(words[index:]),
            "start": boundary,
            "words": segment.words[index:] if segment.words else [],
        }
    )
    return split_segment(first, max_length, max_lines) + split_segment(
        second, max_length, max_lines
    )


def build_cues(
    segments: list[Segment],
    *,
    max_line_length: int = 42,
    max_lines: int = 2,
    speakers: dict[str, str] | None = None,
    protect_bidi: bool = True,
) -> list[Cue]:
    """Convert segments into timed, wrapped, non-overlapping cues."""
    speakers = speakers or {}
    cues: list[Cue] = []
    previous_end = 0.0

    for segment in segments:
        if not segment.text.strip():
            continue
        for part in split_segment(segment, max_line_length, max_lines):
            text = part.text.strip()
            if not text:
                continue

            label = speakers.get(part.speaker or "", part.speaker or "")
            if label:
                text = f"{label}: {text}"
            if protect_bidi:
                text = protect_neutrals(text)

            lines = wrap_lines(text, max_line_length, max_lines)
            if not lines:
                continue

            start = max(part.start, previous_end)
            end = max(part.end, start + MIN_CUE_SECONDS)
            end = min(end, start + MAX_CUE_SECONDS)
            previous_end = end

            cues.append(
                Cue(
                    index=len(cues) + 1,
                    start=start,
                    end=end,
                    lines=lines,
                    speaker=part.speaker,
                )
            )
    return cues
