"""Soniox cloud speech-to-text backend.

Soniox transcribes whole audio files through its HTTP API: upload the file,
create a transcription, poll until it finishes, then fetch the transcript.

Unlike the local Whisper model this backend needs a network connection, a
Soniox project API key, and sends audio to Soniox's servers. It still
implements the :class:`~tiktuv.asr.base.Transcriber` protocol, so the rest of
the pipeline — chunking, overlap repair, persistence and exports — works
unchanged. Each pipeline window becomes one Soniox upload.

Soniox numbers speakers per request, so the backend asks the pipeline for long
windows (see :attr:`SonioxTranscriber.preferred_window_seconds`): fewer
requests means fewer places where speaker numbering can drift, and the
pipeline links the remaining seams through the shared overlap audio.
"""

from __future__ import annotations

import contextlib
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

from ..diarization import SpeakerTurn, assign_speakers
from ..errors import ErrorCode, ModelError
from ..logging_conf import get_logger
from ..schemas import Language, Segment, TimestampMode, TranscriptionSettings, Word
from .base import TranscribeRequest, TranscribeResult

log = get_logger("asr.soniox")

SONIOX_MODEL_ID = "soniox/stt-async-v6"
#: The model id Soniox's API expects. Distinct from ``SONIOX_MODEL_ID``, which
#: is Tiktuv's internal catalogue id (kept unique from the Hugging Face ids).
SONIOX_API_MODEL = "stt-async-v6"
SONIOX_API_BASE = "https://api.soniox.com/v1"

_POLL_INTERVAL_SECONDS = 2.0
_MAX_POLL_SECONDS = 3600.0
_MIN_SEGMENT_SECONDS = 0.2

#: Soniox accepts up to 300 minutes per file. One hour keeps an upload around
#: 115 MB of 16 kHz PCM while covering most recordings in a single request,
#: so speaker numbering stays consistent end to end.
SONIOX_WINDOW_SECONDS = 3600.0
#: A generous overlap gives the pipeline enough shared speech to match the
#: speakers of one window to the next.
SONIOX_OVERLAP_SECONDS = 30.0

#: A segment is closed after a silence this long, even mid-sentence.
_MAX_PAUSE_SECONDS = 1.5
#: ...or once it grows this long, so subtitle cues stay readable.
_MAX_SEGMENT_SECONDS = 20.0
#: Control tokens such as ``<end>`` carry no transcript text.
_CONTROL_TOKEN = re.compile(r"^<\w+>$")
#: Punctuation that ends a sentence when it closes a word.
_SENTENCE_END = (".", "!", "?", "׃", "…")

#: Sentence-final punctuation after which a new segment starts.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?׃…])\s+")

#: The language hint used when the user has not overridden it.
_HEBREW_HINT = ["he"]


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    start: float
    end: float
    confidence: float | None
    speaker: str | None = None


class SonioxTranscriber:
    """A stateless Soniox client. Construct once, reuse across jobs."""

    def __init__(
        self,
        api_key: str,
        model_id: str = SONIOX_MODEL_ID,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._model_id = model_id
        self._client = client or httpx.Client(
            base_url=SONIOX_API_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=300.0, pool=30.0),
        )

    # -- protocol ----------------------------------------------------------
    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def device(self) -> str:
        return "cloud"

    @property
    def preferred_window_seconds(self) -> float:
        return SONIOX_WINDOW_SECONDS

    @property
    def preferred_overlap_seconds(self) -> float:
        return SONIOX_OVERLAP_SECONDS

    def transcribe(self, request: TranscribeRequest) -> TranscribeResult:
        samples = np.asarray(request.samples, dtype=np.float32)
        if samples.size == 0:
            return TranscribeResult(segments=[], language=_language_code(request.settings))

        window_duration = samples.size / max(1, request.sample_rate)
        wav_bytes = _encode_wav(samples, request.sample_rate)

        file_id = self._upload(wav_bytes)
        transcription_id: str | None = None
        try:
            transcription_id = self._create(file_id, request.settings)
            text, tokens = self._await_result(transcription_id)
            segments = _segments_from_tokens(
                tokens, window_duration=window_duration, settings=request.settings
            )
            if not segments:
                # No usable token timings: fall back to spreading the text.
                segments = _segments_from_text(
                    text, tokens, window_duration=window_duration, settings=request.settings
                )
                turns = _speaker_turns(tokens)
                if segments and turns:
                    segments = assign_speakers(segments, turns)
            return TranscribeResult(segments=segments, language=_language_code(request.settings))
        finally:
            self._cleanup(transcription_id, file_id)

    def unload(self) -> None:
        with contextlib.suppress(Exception):  # pragma: no cover - best effort
            self._client.close()

    # -- Soniox API --------------------------------------------------------
    def _upload(self, wav_bytes: bytes) -> str:
        try:
            response = self._client.post(
                "/files",
                files={"file": ("tiktuv-window.wav", wav_bytes, "audio/wav")},
            )
        except httpx.HTTPError as exc:
            raise ModelError(_network_detail(exc), code=ErrorCode.SONIOX_API_ERROR) from exc
        if response.status_code >= 400:
            raise ModelError(_api_detail(response), code=ErrorCode.SONIOX_API_ERROR)

        file_id = response.json().get("id")
        if not isinstance(file_id, str) or not file_id:
            raise ModelError("Soniox returned no file id", code=ErrorCode.SONIOX_API_ERROR)
        log.info("uploaded audio window to Soniox", extra={"file_id": file_id})
        return file_id

    def _create(self, file_id: str, settings: TranscriptionSettings) -> str:
        payload = {
            "model": SONIOX_API_MODEL,
            "file_id": file_id,
            "language_hints": _language_hints(settings),
            "enable_language_identification": False,
            # Speaker labels are part of the core transcription (no extra cost),
            # so they are always on — the UI shows "דובר 1"/"דובר 2" when present.
            "enable_speaker_diarization": True,
        }
        try:
            response = self._client.post("/transcriptions", json=payload)
        except httpx.HTTPError as exc:
            raise ModelError(_network_detail(exc), code=ErrorCode.SONIOX_API_ERROR) from exc
        if response.status_code >= 400:
            raise ModelError(_api_detail(response), code=ErrorCode.SONIOX_API_ERROR)

        transcription_id = response.json().get("id")
        if not isinstance(transcription_id, str) or not transcription_id:
            raise ModelError("Soniox returned no transcription id", code=ErrorCode.SONIOX_API_ERROR)
        log.info("created Soniox transcription", extra={"transcription_id": transcription_id})
        return transcription_id

    def _await_result(self, transcription_id: str) -> tuple[str, list[_Token]]:
        deadline = time.monotonic() + _MAX_POLL_SECONDS
        while time.monotonic() < deadline:
            try:
                response = self._client.get(f"/transcriptions/{transcription_id}")
            except httpx.HTTPError as exc:
                raise ModelError(_network_detail(exc), code=ErrorCode.SONIOX_API_ERROR) from exc
            if response.status_code >= 400:
                raise ModelError(_api_detail(response), code=ErrorCode.SONIOX_API_ERROR)

            data = response.json()
            status = data.get("status")
            if status == "completed":
                return self._fetch_transcript(transcription_id)
            if status == "error":
                raise ModelError(
                    str(data.get("error_message") or "Soniox transcription failed"),
                    code=ErrorCode.SONIOX_API_ERROR,
                )
            time.sleep(_POLL_INTERVAL_SECONDS)

        raise ModelError("Soniox transcription timed out", code=ErrorCode.SONIOX_API_ERROR)

    def _fetch_transcript(self, transcription_id: str) -> tuple[str, list[_Token]]:
        try:
            response = self._client.get(f"/transcriptions/{transcription_id}/transcript")
        except httpx.HTTPError as exc:
            raise ModelError(_network_detail(exc), code=ErrorCode.SONIOX_API_ERROR) from exc
        if response.status_code >= 400:
            raise ModelError(_api_detail(response), code=ErrorCode.SONIOX_API_ERROR)

        data = response.json()
        text = str(data.get("text") or "").strip()
        tokens: list[_Token] = []
        for token in data.get("tokens") or []:
            if not isinstance(token, dict):
                continue
            raw = str(token.get("text") or "")
            if not raw.strip():
                continue
            start_ms = token.get("start_ms") or 0
            end_ms = token.get("end_ms") or 0
            tokens.append(
                _Token(
                    text=raw,
                    start=_as_float(start_ms) / 1000.0,
                    end=_as_float(end_ms) / 1000.0,
                    confidence=_as_float_or_none(token.get("confidence")),
                    speaker=_as_speaker(token.get("speaker")),
                )
            )
        return text, tokens

    def _cleanup(self, transcription_id: str | None, file_id: str) -> None:
        # The transcription and the uploaded file are transient and billable,
        # so always try to remove them — but a cleanup failure must never fail
        # a completed job.
        if transcription_id is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 - best effort
                self._client.delete(f"/transcriptions/{transcription_id}")
        with contextlib.suppress(Exception):  # noqa: BLE001 - best effort
            self._client.delete(f"/files/{file_id}")


# --------------------------------------------------------------------------
# Output shaping
# --------------------------------------------------------------------------
def _language_code(settings: TranscriptionSettings) -> str:
    return "auto" if settings.language is Language.AUTO else settings.language.value


def _language_hints(settings: TranscriptionSettings) -> list[str]:
    if settings.language is Language.AUTO:
        return []
    return [settings.language.value]


def _segments_from_text(
    text: str,
    tokens: list[_Token],
    *,
    window_duration: float,
    settings: TranscriptionSettings,
) -> list[Segment]:
    """Turn Soniox output into segments on the window timeline.

    Soniox's authoritative output is the ``text`` field; ``tokens`` are
    sub-word pieces whose word boundaries are not exposed, so they cannot be
    joined back into words reliably. We therefore split ``text`` into sentences
    and spread their timestamps across the window in proportion to their
    length. Word mode attaches evenly-spaced words per sentence, matching the
    behaviour of the mock backend.
    """
    if not text:
        # Fall back to the raw tokens when no full text was returned.
        text = " ".join(token.text for token in tokens if token.text).strip()
        if not text:
            return []

    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    if not sentences:
        sentences = [text]

    total_chars = float(max(1, sum(len(s) for s in sentences)))
    duration = max(window_duration, 0.0)
    want_words = settings.timestamps is TimestampMode.WORD

    segments: list[Segment] = []
    cursor = 0.0
    for sentence in sentences:
        span = duration * (len(sentence) / total_chars)
        start = cursor
        end = min(duration, cursor + span)
        if end - start < _MIN_SEGMENT_SECONDS:
            end = min(duration, start + _MIN_SEGMENT_SECONDS)
        cursor = end
        segments.append(
            Segment(
                id=len(segments),
                start=start,
                end=max(end, start + _MIN_SEGMENT_SECONDS),
                text=sentence,
                words=_spread_words(sentence, start, end) if want_words else [],
            )
        )
    return segments


@dataclass(frozen=True, slots=True)
class _TokenWord:
    text: str
    start: float
    end: float
    speaker: str | None


def _words_from_tokens(tokens: list[_Token]) -> list[_TokenWord]:
    """Join Soniox's sub-word tokens back into words.

    Concatenating token texts reproduces the transcript, and a token that
    begins a new word carries the leading space — that is the word boundary.
    """
    words: list[_TokenWord] = []
    pieces: list[str] = []
    start = end = 0.0
    speaker: str | None = None

    def flush() -> None:
        text = "".join(pieces).strip()
        if text:
            words.append(_TokenWord(text=text, start=start, end=end, speaker=speaker))
        pieces.clear()

    for token in tokens:
        if _CONTROL_TOKEN.match(token.text.strip()):
            continue
        starts_word = not pieces or token.text[:1].isspace()
        # A speaker change mid-word is a Soniox artefact; keep the word whole
        # and let its first piece decide the speaker.
        if starts_word:
            flush()
            start = token.start
            speaker = token.speaker
        pieces.append(token.text)
        end = token.end if starts_word else max(end, token.end)
    flush()
    return words


def _segments_from_tokens(
    tokens: list[_Token],
    *,
    window_duration: float,
    settings: TranscriptionSettings,
) -> list[Segment]:
    """Build segments on Soniox's real token timings.

    A new segment starts whenever the speaker changes, so each line belongs
    to exactly one person, and also after sentence-final punctuation, a long
    pause, or once a segment gets too long. Returns ``[]`` when the tokens
    carry no usable timings, so the caller can fall back to the text.
    """
    words = _words_from_tokens(tokens)
    if not words or all(word.end <= 0.0 for word in words):
        return []

    want_words = settings.timestamps is TimestampMode.WORD
    limit = window_duration if window_duration > 0 else None
    segments: list[Segment] = []
    current: list[_TokenWord] = []

    def close() -> None:
        if not current:
            return
        start = max(0.0, current[0].start)
        end = max(current[-1].end, start + _MIN_SEGMENT_SECONDS)
        if limit is not None:
            end = min(end, max(limit, start + _MIN_SEGMENT_SECONDS))
        segments.append(
            Segment(
                id=len(segments),
                start=start,
                end=end,
                text=" ".join(word.text for word in current),
                speaker=current[0].speaker,
                words=(
                    [
                        Word(text=word.text, start=word.start, end=max(word.end, word.start))
                        for word in current
                    ]
                    if want_words
                    else []
                ),
            )
        )
        current.clear()

    for word in words:
        if current:
            previous = current[-1]
            if (
                word.speaker != previous.speaker
                or word.start - previous.end > _MAX_PAUSE_SECONDS
                or word.end - current[0].start > _MAX_SEGMENT_SECONDS
            ):
                close()
        current.append(word)
        if word.text.endswith(_SENTENCE_END):
            close()
    close()
    return segments


def _speaker_turns(tokens: list[_Token]) -> list[SpeakerTurn]:
    """Group consecutive same-speaker tokens into contiguous speaker turns."""
    turns: list[SpeakerTurn] = []
    current: str | None = None
    start = 0.0
    end = 0.0
    for token in tokens:
        if token.speaker is None:
            continue
        if token.speaker != current:
            if current is not None:
                turns.append(SpeakerTurn(start=start, end=end, speaker=current))
            current = token.speaker
            start = token.start
        end = token.end
    if current is not None:
        turns.append(SpeakerTurn(start=start, end=end, speaker=current))
    return turns


def _spread_words(text: str, start: float, end: float) -> list[Word]:
    words = text.split()
    if not words:
        return []
    step = (end - start) / len(words)
    return [
        Word(text=word, start=start + index * step, end=start + (index + 1) * step)
        for index, word in enumerate(words)
    ]


def _encode_wav(samples: np.ndarray[Any, np.dtype[np.float32]], sample_rate: int) -> bytes:
    """Encode mono float32 samples as a 16-bit PCM WAV in memory."""
    import io
    import wave

    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _as_float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_speaker(value: object) -> str | None:
    if value is None:
        return None
    speaker = str(value).strip()
    return speaker or None


def _network_detail(exc: httpx.HTTPError) -> str:
    return f"could not reach Soniox: {exc}"


def _api_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"Soniox returned HTTP {response.status_code}"
    message = data.get("error_message") or data.get("message") or data.get("error")
    if message:
        return f"Soniox error: {message}"
    return f"Soniox returned HTTP {response.status_code}"
