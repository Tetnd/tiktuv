"""A playback copy of a recording, on exactly the timeline the transcript uses.

The transcript's timestamps come from decoding the recording with FFmpeg. The
editor then *plays* the recording with a different decoder, and the two do not
always agree: containers carry edit lists, encoder delay and start offsets that
one decoder honours and the other ignores, VBR files with a stale header seek to
the wrong place, and some formats the player cannot open at all. Any of those
shows up as the highlighted line drifting away from what is being heard.

The reliable fix is not to make two decoders agree, but to stop using two: the
player is handed audio that FFmpeg decoded *the same way the transcription did*
(first audio stream, mono, 16 kHz), stored as plain PCM WAV. Position *t* in
that file is position *t* in the transcript, by construction, and a WAV seeks to
the exact sample in every player.

The copy is made once per recording, kept in ``temp/`` (so "delete temporary
files" reclaims it — it is simply rebuilt on the next open), and rebuilt when
the recording changes.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from ..errors import ErrorCode, FFmpegError, MediaError
from ..logging_conf import get_logger
from .ffmpeg import FFmpegTools

log = get_logger("media.playback")

#: Same rate the transcription decodes at. Speech is fully intelligible here,
#: and it keeps the file small: about 1.9 MB per minute.
PLAYBACK_SAMPLE_RATE = 16000

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def playback_path(temp_dir: Path, job_id: str) -> Path:
    return temp_dir / "playback" / f"{job_id}.wav"


def build_playback_args(ffmpeg: Path, source: Path, destination: Path) -> list[str]:
    """FFmpeg arguments that mirror :func:`tiktuv.media.ffmpeg.build_decode_args`."""
    return [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-vn",
        "-sn",
        "-dn",
        "-map", "0:a:0",
        "-ac", "1",
        "-ar", str(PLAYBACK_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        "-f", "wav",
        str(destination),
    ]


def ensure_playback_copy(
    tools: FFmpegTools, source: Path, temp_dir: Path, job_id: str
) -> Path:
    """Return the playback WAV for ``source``, making it if it is missing or stale."""
    target = playback_path(temp_dir, job_id)
    with _lock_for(job_id):
        if _is_fresh(target, source):
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part")
        partial.unlink(missing_ok=True)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            result = subprocess.run(  # noqa: S603 - argument array, shell=False
                build_playback_args(tools.ffmpeg, source, partial),
                capture_output=True,
                stdin=subprocess.DEVNULL,
                shell=False,
                creationflags=creationflags,
                check=False,
            )
        except OSError as exc:
            raise FFmpegError(f"failed to launch ffmpeg: {exc}") from exc

        if result.returncode != 0 or not partial.is_file() or partial.stat().st_size < 64:
            partial.unlink(missing_ok=True)
            detail = result.stderr.decode("utf-8", "replace").strip()[-300:]
            raise MediaError(
                f"could not prepare the recording for playback: {detail}",
                code=ErrorCode.MEDIA_CORRUPT,
            )
        # Atomic: a half-written file is never served.
        os.replace(partial, target)
        log.info(
            "playback copy ready",
            extra={"job_id": job_id, "bytes": target.stat().st_size},
        )
        return target


def _is_fresh(target: Path, source: Path) -> bool:
    try:
        return target.is_file() and target.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False
