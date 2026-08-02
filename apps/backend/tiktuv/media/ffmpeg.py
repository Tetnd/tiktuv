"""FFmpeg / ffprobe discovery and invocation.

Every command is built as an **argument array** and executed with
``shell=False``. No user-controlled string is ever concatenated into a shell
command line, so filenames containing quotes, ampersands, spaces or Hebrew are
passed through verbatim and cannot inject arguments.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..errors import ErrorCode, FFmpegError, FFmpegMissingError
from ..logging_conf import get_logger

log = get_logger("media.ffmpeg")

#: Extra places to look on Windows when the binaries are not on PATH.
_WINDOWS_HINTS = (
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
)


def _hint_dirs() -> list[Path]:
    """Directories to search when the binaries are not on ``PATH``.

    The winget case matters in practice: ``winget install Gyan.FFmpeg`` unpacks
    into a *version-stamped* directory and appends it to the user's PATH, which
    existing processes do not see until they are restarted. Without this, a
    user who just installed FFmpeg is told it is missing.
    """
    dirs: list[Path] = []
    if os.name != "nt":
        return dirs

    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        dirs.append(winget / "Links")
        packages = winget / "Packages"
        if packages.is_dir():
            try:
                for package in packages.iterdir():
                    if not package.is_dir() or "ffmpeg" not in package.name.lower():
                        continue
                    # Layout: Packages/<id>/ffmpeg-<version>-full_build/bin
                    dirs.extend(sorted(package.glob("*/bin"), reverse=True))
                    dirs.append(package)
            except OSError:  # pragma: no cover - unreadable profile directory
                pass

    dirs.extend(Path(p) for p in _WINDOWS_HINTS)
    return dirs


def _find_binary(name: str, override: str | None) -> Path | None:
    if override:
        candidate = Path(override)
        if candidate.is_dir():
            candidate = candidate / (f"{name}.exe" if os.name == "nt" else name)
        if candidate.is_file():
            return candidate
        log.warning("configured %s path not found", name, extra={"configured": override})

    found = shutil.which(name)
    if found:
        return Path(found)

    exe = f"{name}.exe" if os.name == "nt" else name
    for directory in _hint_dirs():
        candidate = directory / exe
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class FFmpegTools:
    ffmpeg: Path
    ffprobe: Path
    version: str


def locate_tools(settings: Settings) -> FFmpegTools:
    """Locate both binaries or raise :class:`FFmpegMissingError`."""
    ffmpeg = _find_binary("ffmpeg", settings.ffmpeg_path)
    ffprobe = _find_binary("ffprobe", settings.ffprobe_path)

    missing = [n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if p is None]
    if missing:
        raise FFmpegMissingError(
            f"could not locate: {', '.join(missing)}",
            context={"searched_path": True, "missing": missing},
        )
    assert ffmpeg is not None and ffprobe is not None
    return FFmpegTools(ffmpeg=ffmpeg, ffprobe=ffprobe, version=_probe_version(ffmpeg))


def _probe_version(ffmpeg: Path) -> str:
    try:
        completed = run_command([str(ffmpeg), "-version"], timeout=15)
    except FFmpegError:
        return "unknown"
    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    match = re.search(r"ffmpeg version (\S+)", first_line)
    return match.group(1) if match else "unknown"


def run_command(
    args: list[str],
    *,
    timeout: float | None = None,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run an argument array with ``shell=False`` and no console window."""
    if not args or not isinstance(args, list):
        raise FFmpegError("command must be a non-empty argument list")

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    log.debug("running ffmpeg command", extra={"argv0": args[0], "argc": len(args)})
    try:
        completed = subprocess.run(  # noqa: S603 - argument array, shell=False
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        raise FFmpegMissingError(f"binary not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"ffmpeg timed out after {timeout}s") from exc
    except OSError as exc:
        raise FFmpegError(f"failed to launch ffmpeg: {exc}") from exc

    if expect_success and completed.returncode != 0:
        raise FFmpegError(
            _summarise_stderr(completed.stderr),
            code=_classify_stderr(completed.stderr),
            context={"returncode": completed.returncode},
        )
    return completed


def _summarise_stderr(stderr: str, *, max_lines: int = 6) -> str:
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    # The interesting part of FFmpeg's output is always at the end.
    return " | ".join(lines[-max_lines:]) or "ffmpeg produced no diagnostics"


def _classify_stderr(stderr: str) -> ErrorCode:
    lowered = (stderr or "").lower()
    if "no space left" in lowered or "disk full" in lowered:
        return ErrorCode.INSUFFICIENT_DISK
    if "decoder" in lowered and "not found" in lowered:
        return ErrorCode.UNSUPPORTED_CODEC
    if "unknown encoder" in lowered or "unsupported codec" in lowered:
        return ErrorCode.UNSUPPORTED_CODEC
    if any(
        token in lowered
        for token in ("invalid data found", "moov atom not found", "error while decoding")
    ):
        return ErrorCode.MEDIA_CORRUPT
    return ErrorCode.FFMPEG_FAILED


def build_decode_args(
    ffmpeg: Path,
    source: Path,
    *,
    sample_rate: int,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
) -> list[str]:
    """Build the streaming decode command: media in, raw f32le mono out.

    Notes on the choices here:

    * ``-vn`` drops video without touching it — no transcoding of the picture.
    * ``-ac 1`` downmixes stereo to mono, which is what the model consumes.
    * ``-ar`` resamples **only** because Whisper's feature extractor requires
      16 kHz; if the source is already 16 kHz FFmpeg makes this a no-op.
    * output is ``pipe:1``, so nothing is written to disk and the caller reads
      the stream incrementally. A multi-hour file never lands in RAM.
    * ``-nostdin`` prevents FFmpeg from consuming the parent's stdin.
    """
    args = [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
    ]
    if start_seconds > 0:
        # Before -i: fast keyframe-accurate seek, cheap on long files.
        args += ["-ss", f"{start_seconds:.3f}"]
    args += ["-i", str(source)]
    if duration_seconds is not None:
        args += ["-t", f"{duration_seconds:.3f}"]
    args += [
        "-vn",
        "-sn",
        "-dn",
        "-map", "0:a:0",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "pipe:1",
    ]
    return args


def build_probe_args(ffprobe: Path, source: Path) -> list[str]:
    return [
        str(ffprobe),
        "-hide_banner",
        "-loglevel", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(source),
    ]
