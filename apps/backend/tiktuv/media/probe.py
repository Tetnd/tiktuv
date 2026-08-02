"""Media inspection and validation.

File type is decided by what ffprobe finds inside the file, never by the
extension. A ``.mp3`` that is really a ZIP is rejected here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import ErrorCode, MediaError
from ..logging_conf import get_logger
from ..schemas import MediaInfo
from .ffmpeg import FFmpegTools, build_probe_args, run_command

log = get_logger("media.probe")

#: Extensions the UI offers. Used only to filter the file dialog — validation
#: is always done by inspection.
SUPPORTED_EXTENSIONS = frozenset(
    {".mp3", ".mp4", ".wav", ".m4a", ".mov", ".mkv", ".webm", ".aac", ".flac", ".ogg", ".opus"}
)

#: Container names ffprobe reports for the formats we accept.
ALLOWED_FORMAT_TOKENS = frozenset(
    {
        "mp3", "mp4", "mov", "m4a", "3gp", "3g2", "mj2", "wav", "matroska",
        "webm", "aac", "flac", "ogg", "opus", "adts", "asf", "avi",
    }
)


def probe_media(path: Path, tools: FFmpegTools, settings: Settings) -> MediaInfo:
    """Inspect ``path`` with ffprobe and return validated :class:`MediaInfo`.

    Raises :class:`MediaError` with a specific code for every rejection reason
    so the UI can show an actionable Hebrew message.
    """
    if not path.exists():
        raise MediaError(f"file does not exist: {path}", code=ErrorCode.MEDIA_CORRUPT)
    if not path.is_file():
        raise MediaError(f"not a regular file: {path}", code=ErrorCode.MEDIA_CORRUPT)

    size = path.stat().st_size
    if size == 0:
        raise MediaError("file is empty", code=ErrorCode.MEDIA_CORRUPT)
    if size > settings.max_upload_bytes:
        raise MediaError(
            f"file is {size} bytes, limit is {settings.max_upload_bytes}",
            code=ErrorCode.MEDIA_TOO_LARGE,
            context={"size_bytes": size, "limit_bytes": settings.max_upload_bytes},
        )

    completed = run_command(
        build_probe_args(tools.ffprobe, path), timeout=120, expect_success=False
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise MediaError(
            f"ffprobe could not read the file: {completed.stderr.strip()[:400]}",
            code=ErrorCode.MEDIA_CORRUPT,
        )

    try:
        data: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError(
            f"ffprobe returned invalid JSON: {exc}", code=ErrorCode.MEDIA_CORRUPT
        ) from exc

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    if not streams:
        raise MediaError("no media streams found", code=ErrorCode.MEDIA_CORRUPT)

    format_name = str(fmt.get("format_name") or "")
    tokens = {token.strip() for token in format_name.split(",") if token.strip()}
    if not tokens & ALLOWED_FORMAT_TOKENS:
        raise MediaError(
            f"container {format_name!r} is not a supported media format",
            code=ErrorCode.UNSUPPORTED_FORMAT,
            context={"container": format_name},
        )

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if audio is None:
        raise MediaError("file contains no audio stream", code=ErrorCode.MEDIA_NO_AUDIO)

    duration = _duration_of(fmt, audio)
    if duration <= 0:
        # Some MKV/WEBM files omit duration in the header. Fall back to a
        # decode-only pass, which is exact but costs a full read.
        duration = _measure_duration(path, tools)
    if duration <= 0:
        raise MediaError("could not determine media duration", code=ErrorCode.MEDIA_CORRUPT)

    info = MediaInfo(
        path=str(path),
        filename=path.name,
        size_bytes=size,
        duration=duration,
        container=format_name or "unknown",
        audio_codec=str(audio.get("codec_name")) if audio.get("codec_name") else None,
        video_codec=str(video.get("codec_name")) if video and video.get("codec_name") else None,
        sample_rate=_int_or_none(audio.get("sample_rate")),
        channels=_int_or_none(audio.get("channels")),
        bit_rate=_int_or_none(fmt.get("bit_rate")),
        has_audio=True,
        has_video=video is not None,
    )
    log.info(
        "probed media",
        extra={
            "container": info.container,
            "audio_codec": info.audio_codec,
            "duration": round(info.duration, 2),
            "channels": info.channels,
            "sample_rate": info.sample_rate,
        },
    )
    return info


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _duration_of(fmt: dict[str, Any], audio: dict[str, Any]) -> float:
    for source in (fmt, audio):
        raw = source.get("duration")
        if raw is None:
            continue
        try:
            duration = float(raw)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    return 0.0


def _measure_duration(path: Path, tools: FFmpegTools) -> float:
    """Exact duration by decoding to null. Used only when headers lie."""
    completed = run_command(
        [
            str(tools.ffmpeg), "-nostdin", "-hide_banner", "-i", str(path),
            "-map", "0:a:0", "-f", "null", "-",
        ],
        timeout=1800,
        expect_success=False,
    )
    # ffmpeg reports "time=HH:MM:SS.ss" on its final progress line.
    best = 0.0
    for chunk in completed.stderr.split("time=")[1:]:
        stamp = chunk.split(" ")[0].strip()
        parts = stamp.split(":")
        try:
            if len(parts) == 3:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            else:
                seconds = float(stamp)
        except ValueError:
            continue
        best = max(best, seconds)
    return best
