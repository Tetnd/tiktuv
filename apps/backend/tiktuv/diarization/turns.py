"""Turning speaker *turns* into speaker-labelled *segments*.

A diarizer (Soniox, here) answers "who spoke when" as spans of time. A
transcript is a list of segments whose boundaries came from the speech
recogniser and know nothing about speakers. This module joins the two, and the
join is where most of the quality is won or lost:

* **Smoothing.** Acoustic diarization flickers: a half-second of "speaker B"
  inside a long run of speaker A is almost always a mistake, not a person who
  said one syllable. Such flips are removed before anything is assigned.
* **Majority, not first-overlap.** A segment is labelled by the speaker who
  holds most of it, and only counts a second speaker when they hold a
  meaningful stretch.
* **Splitting.** Recognisers often return a segment that spans a hand-over
  ("...כן. ומה לגבי התקציב?"). Giving that whole line to one person is wrong
  for half of it, so such a segment is cut at the turn boundary — on a word
  boundary, preferring a sentence end — and each part gets its own speaker.

Everything here is pure and model-free so it is unit-tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schemas import Segment, Word
from . import SpeakerTurn

#: A speaker flip shorter than this, between two spans of the same other
#: speaker, is treated as noise and absorbed.
MIN_FLIP_SECONDS = 1.0
#: A piece of a segment shorter than this is not worth its own line.
MIN_PIECE_SECONDS = 0.9
#: Within this many words of the proportional cut point, prefer a sentence end.
_SNAP_WORDS = 3
#: ...as long as snapping moves the cut by no more than this many seconds.
_SNAP_SECONDS = 1.5
_SENTENCE_END = (".", "!", "?", "׃", "…", ",")


def smooth_turns(
    turns: list[SpeakerTurn], *, min_flip: float = MIN_FLIP_SECONDS
) -> list[SpeakerTurn]:
    """Merge same-speaker neighbours and absorb implausibly short interjections."""
    ordered = sorted((t for t in turns if t.end > t.start), key=lambda t: (t.start, t.end))
    merged = _merge_adjacent(ordered)

    changed = True
    while changed and len(merged) >= 3:
        changed = False
        for index in range(1, len(merged) - 1):
            before, here, after = merged[index - 1], merged[index], merged[index + 1]
            if (
                before.speaker == after.speaker
                and here.speaker != before.speaker
                and here.end - here.start < min_flip
            ):
                merged[index] = SpeakerTurn(here.start, here.end, before.speaker)
                changed = True
        merged = _merge_adjacent(merged)
    return merged


def _merge_adjacent(turns: list[SpeakerTurn]) -> list[SpeakerTurn]:
    out: list[SpeakerTurn] = []
    for turn in turns:
        if out and out[-1].speaker == turn.speaker and turn.start - out[-1].end <= 1.0:
            out[-1] = SpeakerTurn(out[-1].start, max(out[-1].end, turn.end), turn.speaker)
        else:
            out.append(turn)
    return out


@dataclass(slots=True)
class _Piece:
    start: float
    end: float
    speaker: str

    @property
    def length(self) -> float:
        return self.end - self.start


def apply_turns(
    segments: list[Segment],
    turns: list[SpeakerTurn],
    *,
    min_piece: float = MIN_PIECE_SECONDS,
) -> list[Segment]:
    """Label every segment with its speaker, splitting where speakers change.

    Segments are renumbered from zero because splitting adds lines. A segment
    no turn reaches keeps the speaker of the nearest labelled neighbour, so a
    stretch the diarizer skipped does not appear as a gap in the conversation.
    """
    turns = smooth_turns(turns)
    if not turns:
        return segments

    result: list[Segment] = []
    for segment in segments:
        pieces = _pieces_for(segment, turns, min_piece)
        if not pieces:
            result.append(segment)
            continue
        if len(pieces) == 1:
            result.append(segment.model_copy(update={"speaker": pieces[0].speaker}))
            continue
        result.extend(_split_segment(segment, pieces))

    # Fill unlabelled segments from their neighbours, then number them.
    last: str | None = None
    for index, segment in enumerate(result):
        if segment.speaker:
            last = segment.speaker
        elif last is not None:
            result[index] = segment.model_copy(update={"speaker": last})
    for index, segment in enumerate(result):
        result[index] = segment.model_copy(update={"id": index})
    return result


def _pieces_for(segment: Segment, turns: list[SpeakerTurn], min_piece: float) -> list[_Piece]:
    raw: list[_Piece] = []
    for turn in turns:
        start = max(turn.start, segment.start)
        end = min(turn.end, segment.end)
        if end > start:
            raw.append(_Piece(start, end, turn.speaker))
    if not raw:
        return []
    raw.sort(key=lambda p: p.start)

    # Close the gaps between pieces at their midpoint and stretch the ends to
    # the segment edges: the segment is one continuous utterance.
    for left, right in zip(raw, raw[1:], strict=False):
        middle = (left.end + right.start) / 2
        left.end = right.start = middle
    raw[0].start = segment.start
    raw[-1].end = segment.end

    pieces = _merge_pieces(raw)
    # Absorb pieces too short to stand alone into the longer neighbour.
    while len(pieces) > 1:
        shortest = min(range(len(pieces)), key=lambda i: pieces[i].length)
        if pieces[shortest].length >= min_piece:
            break
        neighbours = [i for i in (shortest - 1, shortest + 1) if 0 <= i < len(pieces)]
        target = max(neighbours, key=lambda i: pieces[i].length)
        pieces[target].start = min(pieces[target].start, pieces[shortest].start)
        pieces[target].end = max(pieces[target].end, pieces[shortest].end)
        del pieces[shortest]
        pieces = _merge_pieces(pieces)

    # Total ownership decides a single-speaker segment: when one speaker holds
    # nearly all of it, do not split for a sliver.
    return pieces


def _merge_pieces(pieces: list[_Piece]) -> list[_Piece]:
    out: list[_Piece] = []
    for piece in pieces:
        if out and out[-1].speaker == piece.speaker:
            out[-1].end = piece.end
        else:
            out.append(_Piece(piece.start, piece.end, piece.speaker))
    return out


def _split_segment(segment: Segment, pieces: list[_Piece]) -> list[Segment]:
    """Cut one segment into one segment per piece, on word boundaries."""
    tokens = segment.text.split()
    if len(tokens) < len(pieces):
        # Not enough words to split: the longest speaker keeps the line.
        owner = max(pieces, key=lambda p: p.length)
        return [segment.model_copy(update={"speaker": owner.speaker})]

    # Word timings, real when the recogniser gave them, evenly spread otherwise.
    times = _word_times(segment, tokens)
    cuts: list[int] = []  # index of the first token of each later piece
    position = 0
    for piece in pieces[:-1]:
        cut = _cut_index(tokens, times, piece.end, lower=position + 1,
                         upper=len(tokens) - (len(pieces) - len(cuts) - 1))
        cuts.append(cut)
        position = cut

    bounds = [0, *cuts, len(tokens)]
    out: list[Segment] = []
    for piece, begin, finish in zip(pieces, bounds[:-1], bounds[1:], strict=True):
        part = tokens[begin:finish]
        if not part:
            continue
        start = times[begin][0] if begin < len(times) else piece.start
        end = times[finish - 1][1] if finish - 1 < len(times) else piece.end
        words = (
            [Word(text=w.text, start=w.start, end=w.end) for w in segment.words[begin:finish]]
            if len(segment.words) == len(tokens)
            else []
        )
        out.append(
            segment.model_copy(
                update={
                    "text": " ".join(part),
                    "start": max(segment.start, min(start, piece.end)),
                    "end": min(segment.end, max(end, piece.start)),
                    "speaker": piece.speaker,
                    "words": words,
                    "boundary_merged": False,
                    "original_text": None,
                }
            )
        )
    return out


def _word_times(segment: Segment, tokens: list[str]) -> list[tuple[float, float]]:
    if len(segment.words) == len(tokens):
        return [(w.start, w.end) for w in segment.words]
    total = max(segment.end - segment.start, 0.001)
    weights = [max(1, len(token)) for token in tokens]
    scale = total / sum(weights)
    times: list[tuple[float, float]] = []
    cursor = segment.start
    for weight in weights:
        step = weight * scale
        times.append((cursor, cursor + step))
        cursor += step
    return times


def _cut_index(
    tokens: list[str],
    times: list[tuple[float, float]],
    boundary: float,
    *,
    lower: int,
    upper: int,
) -> int:
    """The token index at which the next speaker's words begin."""
    best = lower
    best_gap = float("inf")
    for index in range(lower, max(lower, upper) + 1):
        if index >= len(times):
            break
        gap = abs(times[index][0] - boundary)
        if gap < best_gap:
            best, best_gap = index, gap

    # Prefer to cut just after a sentence end if one is close by.
    for index in range(max(lower, best - _SNAP_WORDS), min(upper, best + _SNAP_WORDS) + 1):
        if index < 1 or index >= len(tokens):
            continue
        distance = abs(times[index][0] - boundary)
        if (
            tokens[index - 1].endswith(_SENTENCE_END)
            and distance <= _SNAP_SECONDS
        ):
            return index
    return best
