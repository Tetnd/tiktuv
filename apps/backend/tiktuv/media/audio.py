"""Streaming audio decode.

A single FFmpeg process decodes the media to raw 32-bit float mono PCM on
stdout. We read it in small blocks and hand out overlapping windows. Peak
memory is one window plus the overlap tail — a five-hour file costs the same
as a five-minute one.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..errors import ErrorCode, FFmpegError, FFmpegMissingError, JobCancelled
from ..logging_conf import get_logger
from .ffmpeg import FFmpegTools, _classify_stderr, _summarise_stderr, build_decode_args

log = get_logger("media.audio")

#: Mono 32-bit float PCM. Spelled out once so every module that passes
#: audio around agrees on the element type.
AudioArray = np.ndarray[Any, np.dtype[np.float32]]

BYTES_PER_SAMPLE = 4  # float32
#: stdout read size. 1 MiB ≈ 6.5 s of 16 kHz mono float32.
_READ_BLOCK_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class AudioWindow:
    """One decoded window of audio, in media-timeline seconds."""

    index: int
    #: Absolute start of ``samples`` in the source timeline.
    start: float
    #: Absolute end of ``samples`` in the source timeline.
    end: float
    samples: AudioArray
    sample_rate: int
    #: Seconds at the head of this window that also appeared in the previous
    #: window. Segments fully inside this region are candidates for de-duping.
    overlap_lead: float
    is_last: bool

    @property
    def duration(self) -> float:
        return self.end - self.start


class CancelToken:
    """Cooperative cancellation shared between the API and the worker."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise JobCancelled("cancelled by user")


def _spawn_decoder(args: list[str]) -> subprocess.Popen[bytes]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        return subprocess.Popen(  # noqa: S603 - argument array, shell=False
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            shell=False,
            bufsize=_READ_BLOCK_BYTES,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        raise FFmpegMissingError(f"binary not found: {args[0]}") from exc
    except OSError as exc:
        raise FFmpegError(f"failed to launch ffmpeg: {exc}") from exc


def iter_windows(
    source: Path,
    tools: FFmpegTools,
    *,
    sample_rate: int,
    window_seconds: float,
    overlap_seconds: float,
    start_at: float = 0.0,
    total_duration: float | None = None,
    cancel: CancelToken | None = None,
    on_bytes: Callable[[int], None] | None = None,
) -> Iterator[AudioWindow]:
    """Yield overlapping :class:`AudioWindow` objects.

    Consecutive windows share ``overlap_seconds`` of audio: window *n+1* starts
    ``window_seconds - overlap_seconds`` after window *n*. That shared region
    is what lets :mod:`tiktuv.asr.chunking` repair words cut in half at a
    boundary.

    ``start_at`` resumes a partially-processed job; it maps directly to
    FFmpeg's ``-ss`` so no audio before that point is even decoded.
    """
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    if not 0 <= overlap_seconds < window_seconds:
        raise ValueError("overlap_seconds must be >= 0 and < window_seconds")

    window_samples = int(round(window_seconds * sample_rate))
    overlap_samples = int(round(overlap_seconds * sample_rate))
    hop_samples = window_samples - overlap_samples

    args = build_decode_args(
        tools.ffmpeg, source, sample_rate=sample_rate, start_seconds=start_at
    )
    process = _spawn_decoder(args)
    assert process.stdout is not None and process.stderr is not None

    # Drain stderr on a thread so a chatty FFmpeg can never deadlock on a full
    # pipe buffer while we are busy reading stdout.
    stderr_chunks: list[bytes] = []
    stderr_thread = threading.Thread(
        target=lambda: stderr_chunks.append(process.stderr.read() or b""),  # type: ignore[union-attr]
        daemon=True,
    )
    stderr_thread.start()

    buffer = bytearray()
    #: Sample index (relative to ``start_at``) of ``buffer[0]``.
    buffer_origin = 0
    emitted = 0
    tail = b""

    def _decode(raw: bytes) -> AudioArray:
        return np.frombuffer(raw, dtype="<f4").astype(np.float32, copy=False)

    def _make_window(chunk: bytes, origin_sample: int, is_last: bool) -> AudioWindow:
        nonlocal emitted
        samples = _decode(chunk)
        start = start_at + origin_sample / sample_rate
        window = AudioWindow(
            index=emitted,
            start=start,
            end=start + samples.size / sample_rate,
            samples=samples,
            sample_rate=sample_rate,
            overlap_lead=overlap_seconds if emitted > 0 else 0.0,
            is_last=is_last,
        )
        emitted += 1
        return window

    try:
        while True:
            if cancel is not None and cancel.cancelled:
                raise JobCancelled("cancelled during decode")

            block = process.stdout.read(_READ_BLOCK_BYTES)
            if not block:
                break
            if on_bytes is not None:
                on_bytes(len(block))

            # Guard against a torn float at a read boundary.
            block = tail + block
            usable = len(block) - (len(block) % BYTES_PER_SAMPLE)
            tail = block[usable:]
            buffer += block[:usable]

            while len(buffer) >= window_samples * BYTES_PER_SAMPLE:
                # One read block can hold several windows, so cancellation has
                # to be checked here too — otherwise a cancel is not honoured
                # until the next block, which can be seconds of extra work.
                if cancel is not None and cancel.cancelled:
                    raise JobCancelled("cancelled during decode")
                chunk = bytes(buffer[: window_samples * BYTES_PER_SAMPLE])
                yield _make_window(chunk, buffer_origin, is_last=False)
                del buffer[: hop_samples * BYTES_PER_SAMPLE]
                buffer_origin += hop_samples

        if cancel is not None and cancel.cancelled:
            raise JobCancelled("cancelled during decode")

        # Flush whatever is left. Silence-only remainders are dropped.
        if len(buffer) >= BYTES_PER_SAMPLE:
            final = _make_window(bytes(buffer), buffer_origin, is_last=True)
            if final.samples.size > sample_rate * 0.05:
                yield final
        elif emitted == 0:
            raise FFmpegError("decoder produced no audio", code=ErrorCode.MEDIA_NO_AUDIO)

        returncode = process.wait(timeout=60)
        stderr_thread.join(timeout=5)
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        if returncode != 0:
            raise FFmpegError(
                _summarise_stderr(stderr_text),
                code=_classify_stderr(stderr_text),
                context={"returncode": returncode},
            )
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                log.error("ffmpeg did not exit after kill")
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def estimate_window_count(
    duration: float, window_seconds: float, overlap_seconds: float, start_at: float = 0.0
) -> int:
    """How many windows :func:`iter_windows` will yield. Used for progress."""
    remaining = max(0.0, duration - start_at)
    if remaining <= 0:
        return 0
    hop = window_seconds - overlap_seconds
    if remaining <= window_seconds:
        return 1
    return 1 + int(np.ceil((remaining - window_seconds) / hop))


def peak_rms(samples: AudioArray) -> float:
    """RMS level of a window. Used to skip silent windows cheaply."""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def find_silences(
    samples: AudioArray,
    sample_rate: int,
    *,
    threshold_rms: float = 0.005,
    min_duration: float = 2.5,
) -> list[tuple[float, float]]:
    """Locate silent spans, in seconds relative to the start of ``samples``.

    Used for paragraph splitting. Deliberately simple and cheap: a 20 ms frame
    RMS gate, then run-length grouping.
    """
    if samples.size == 0:
        return []
    frame = max(1, int(sample_rate * 0.02))
    usable = samples.size - (samples.size % frame)
    if usable < frame:
        return []
    frames = samples[:usable].reshape(-1, frame)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    quiet = rms < threshold_rms

    spans: list[tuple[float, float]] = []
    run_start: int | None = None
    for index, is_quiet in enumerate(quiet):
        if is_quiet and run_start is None:
            run_start = index
        elif not is_quiet and run_start is not None:
            spans.append((run_start, index))
            run_start = None
    if run_start is not None:
        spans.append((run_start, len(quiet)))

    seconds_per_frame = frame / sample_rate
    return [
        (start * seconds_per_frame, end * seconds_per_frame)
        for start, end in spans
        if (end - start) * seconds_per_frame >= min_duration
    ]
