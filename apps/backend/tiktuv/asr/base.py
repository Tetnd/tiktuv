"""The transcription backend interface.

Anything that can turn a mono float32 array into segments implements
:class:`Transcriber`. The real Hugging Face backend and the deterministic test
backend are interchangeable behind it, which is what lets the entire job
pipeline be tested without downloading three gigabytes of weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..media.audio import AudioArray
from ..schemas import Segment, TranscriptionSettings


@dataclass(frozen=True, slots=True)
class TranscribeRequest:
    """One window of audio to transcribe.

    ``samples`` is mono float32 at ``sample_rate``. Timestamps in the result
    are **relative to this window**; the caller shifts them onto the source
    timeline.
    """

    samples: AudioArray
    sample_rate: int
    settings: TranscriptionSettings
    #: Tail of the preceding window's text. Whisper conditions on this to keep
    #: terminology and speaker style consistent across a boundary.
    previous_text: str = ""


@dataclass(frozen=True, slots=True)
class TranscribeResult:
    segments: list[Segment]
    language: str


@runtime_checkable
class Transcriber(Protocol):
    """A loaded, reusable ASR backend."""

    @property
    def model_id(self) -> str: ...

    @property
    def device(self) -> str: ...

    def transcribe(self, request: TranscribeRequest) -> TranscribeResult: ...

    def unload(self) -> None:
        """Release weights and device memory."""
        ...
