"""Keeping speaker ids consistent across transcription windows."""

from __future__ import annotations

from tiktuv.diarization import link_window_speakers
from tiktuv.schemas import Segment


def _seg(start: float, end: float, speaker: str | None, text: str = "x") -> Segment:
    return Segment(id=0, start=start, end=end, text=text, speaker=speaker)


def test_swapped_numbering_is_matched_through_overlap() -> None:
    previous = [_seg(0, 100, "1"), _seg(100, 120, "2")]
    # The new window starts at 100 and heard the same person (speaker "2"
    # globally) during the overlap, but numbered them "1".
    incoming = [_seg(100, 120, "1"), _seg(120, 200, "2")]
    linked = link_window_speakers(incoming, previous, overlap_start=100, overlap_end=120)
    assert [s.speaker for s in linked] == ["2", "1"]


def test_silent_speaker_reuses_earlier_unclaimed_speaker() -> None:
    previous = [_seg(0, 50, "1"), _seg(50, 100, "2")]
    # Only one person talks in the overlap; the other new-window speaker is
    # someone already heard, not a third person.
    incoming = [_seg(95, 110, "1"), _seg(110, 150, "2")]
    linked = link_window_speakers(incoming, previous, overlap_start=95, overlap_end=110)
    assert [s.speaker for s in linked] == ["2", "1"]


def test_newcomer_gets_fresh_id_when_nobody_to_reuse() -> None:
    previous = [_seg(0, 100, "1")]
    incoming = [_seg(95, 110, "1"), _seg(110, 150, "2"), _seg(150, 160, "3")]
    linked = link_window_speakers(incoming, previous, overlap_start=95, overlap_end=110)
    assert linked[0].speaker == "1"
    assert linked[1].speaker == "2"
    assert linked[2].speaker == "3"


def test_first_window_and_speakerless_segments_untouched() -> None:
    incoming = [_seg(0, 5, None)]
    assert link_window_speakers(incoming, [], overlap_start=0, overlap_end=0) == incoming

    first = [_seg(0, 5, "1"), _seg(5, 9, "2")]
    linked = link_window_speakers(first, [], overlap_start=0, overlap_end=0)
    assert [s.speaker for s in linked] == ["1", "2"]
