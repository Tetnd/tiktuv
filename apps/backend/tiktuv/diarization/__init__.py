"""Speaker recognition building blocks.

Speakers are recognised by two engines, orchestrated in :mod:`tiktuv.speakers`:

* **Soniox** — acoustic. Tells voices apart in the audio; its turns are aligned
  onto the transcript by :mod:`tiktuv.diarization.turns`, which also smooths
  flicker and splits a segment where the speaker changes mid-line.
* **DeepSeek** — semantic. :mod:`tiktuv.diarization.deepseek` reads the
  transcript and decides who speaks, using only text.

This module holds what both share: the :class:`SpeakerTurn` type, the
Hebrew display labels ("דובר 1"), and :func:`link_window_speakers`, which keeps
speaker ids stable across the windows of a long recording.

A local diarization model can still be plugged in through
:func:`register_backend`; none is bundled, because the reliable open-source one
(pyannote) is a gated Hugging Face repository that needs an account and token,
which does not fit an application that works without sign-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..logging_conf import get_logger
from ..media.audio import AudioArray
from ..schemas import Segment

log = get_logger("diarization")


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    """One contiguous span attributed to a single speaker."""

    start: float
    end: float
    speaker: str


@runtime_checkable
class DiarizationBackend(Protocol):
    """Anything that can attribute spans of audio to speakers."""

    @property
    def name(self) -> str: ...

    def diarize(self, samples: AudioArray, sample_rate: int) -> list[SpeakerTurn]: ...


_backend: DiarizationBackend | None = None


def register_backend(backend: DiarizationBackend | None) -> None:
    global _backend
    _backend = backend
    log.info(
        "diarization backend registered" if backend else "diarization backend cleared",
        extra={"backend": getattr(backend, "name", None)},
    )


def get_backend() -> DiarizationBackend | None:
    return _backend


def is_available() -> bool:
    return _backend is not None


#: Hebrew display names. ``speaker_0`` becomes "דובר 1"; the user can rename
#: any of them in the editor, which overrides this map per transcript.
def default_speaker_label(index: int) -> str:
    return f"דובר {index + 1}"


def assign_speakers(segments: list[Segment], turns: list[SpeakerTurn]) -> list[Segment]:
    """Attach a speaker id to each segment by maximum temporal overlap.

    Pure and backend-independent, so it is unit-testable without any model.
    """
    if not turns:
        return segments

    result: list[Segment] = []
    for segment in segments:
        best_speaker: str | None = None
        best_overlap = 0.0
        for turn in turns:
            overlap = min(segment.end, turn.end) - max(segment.start, turn.start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = turn.speaker
        result.append(
            segment.model_copy(update={"speaker": best_speaker}) if best_speaker else segment
        )
    return result


def build_speaker_map(segments: list[Segment]) -> dict[str, str]:
    """Map raw speaker ids to Hebrew labels, in order of first appearance."""
    order: list[str] = []
    for segment in segments:
        if segment.speaker and segment.speaker not in order:
            order.append(segment.speaker)
    return {speaker: default_speaker_label(index) for index, speaker in enumerate(order)}


def link_window_speakers(
    incoming: list[Segment],
    previous: list[Segment],
    *,
    overlap_start: float,
    overlap_end: float,
) -> list[Segment]:
    """Rename one window's speaker ids so they match the windows before it.

    A cloud transcriber numbers speakers per request, so "1" in one window
    need not be "1" in the next. Both windows transcribed the shared overlap
    audio, so whoever speaks there in the new window is matched to whoever
    the earlier windows heard at the same moment (greedy, by seconds of
    shared speech, one-to-one). A speaker with no match takes over an earlier
    speaker nobody in this window claimed; only when none is left does it get
    a fresh id that collides with nothing already used.
    """
    local_ids = _ordered_speakers(incoming)
    if not local_ids:
        return incoming

    shared: dict[tuple[str, str], float] = {}
    if overlap_end > overlap_start:
        for new in incoming:
            if not new.speaker:
                continue
            for old in previous:
                if not old.speaker:
                    continue
                lo = max(new.start, old.start, overlap_start)
                hi = min(new.end, old.end, overlap_end)
                if hi > lo:
                    key = (new.speaker, old.speaker)
                    shared[key] = shared.get(key, 0.0) + (hi - lo)

    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for (local, known), _seconds in sorted(shared.items(), key=lambda kv: -kv[1]):
        if local in mapping or known in taken:
            continue
        mapping[local] = known
        taken.add(known)

    # A speaker silent during the overlap is most likely someone already
    # heard, not a newcomer: reuse unclaimed earlier speakers, most recently
    # active first, before minting a new id.
    last_heard: dict[str, float] = {}
    for old in previous:
        if old.speaker:
            last_heard[old.speaker] = max(last_heard.get(old.speaker, 0.0), old.end)
    reusable = [
        known
        for known, _end in sorted(last_heard.items(), key=lambda kv: -kv[1])
        if known not in taken
    ]

    used = set(last_heard) | taken
    next_index = 1
    for local in local_ids:
        if local in mapping:
            continue
        if reusable:
            mapping[local] = reusable.pop(0)
            continue
        while str(next_index) in used:
            next_index += 1
        mapping[local] = str(next_index)
        used.add(mapping[local])

    return [
        segment.model_copy(update={"speaker": mapping[segment.speaker]})
        if segment.speaker
        else segment
        for segment in incoming
    ]


def _ordered_speakers(segments: list[Segment]) -> list[str]:
    order: list[str] = []
    for segment in segments:
        if segment.speaker and segment.speaker not in order:
            order.append(segment.speaker)
    return order


__all__ = [
    "DiarizationBackend",
    "SpeakerTurn",
    "assign_speakers",
    "build_speaker_map",
    "default_speaker_label",
    "get_backend",
    "is_available",
    "link_window_speakers",
    "register_backend",
]
