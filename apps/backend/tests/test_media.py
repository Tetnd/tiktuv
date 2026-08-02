"""FFmpeg command construction, media validation and streaming decode."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tiktuv.config import Settings
from tiktuv.errors import ErrorCode, FFmpegError, MediaError
from tiktuv.media.audio import (
    BYTES_PER_SAMPLE,
    CancelToken,
    estimate_window_count,
    find_silences,
    iter_windows,
    peak_rms,
)
from tiktuv.media.ffmpeg import (
    FFmpegTools,
    _classify_stderr,
    _summarise_stderr,
    build_decode_args,
    build_probe_args,
    locate_tools,
    run_command,
)
from tiktuv.media.probe import probe_media

from .conftest import requires_ffmpeg


# --------------------------------------------------------------------------
# Command construction — no FFmpeg needed
# --------------------------------------------------------------------------
class TestCommandConstruction:
    def _tools(self) -> FFmpegTools:
        return FFmpegTools(ffmpeg=Path("/bin/ffmpeg"), ffprobe=Path("/bin/ffprobe"), version="6.0")

    def test_decode_args_are_a_list_not_a_string(self) -> None:
        args = build_decode_args(Path("ffmpeg"), Path("a.mp3"), sample_rate=16000)
        assert isinstance(args, list)
        assert all(isinstance(part, str) for part in args)

    def test_decode_args_request_mono_f32le_at_the_model_rate(self) -> None:
        args = build_decode_args(Path("ffmpeg"), Path("a.mp3"), sample_rate=16000)
        assert args[args.index("-ac") + 1] == "1"
        assert args[args.index("-ar") + 1] == "16000"
        assert args[args.index("-f") + 1] == "f32le"
        assert args[-1] == "pipe:1"

    def test_decode_args_drop_video_and_subtitles(self) -> None:
        args = build_decode_args(Path("ffmpeg"), Path("a.mp4"), sample_rate=16000)
        assert "-vn" in args and "-sn" in args and "-dn" in args

    def test_seek_is_placed_before_input_for_fast_seeking(self) -> None:
        args = build_decode_args(Path("ffmpeg"), Path("a.mp3"), sample_rate=16000, start_seconds=90)
        assert args.index("-ss") < args.index("-i")
        assert args[args.index("-ss") + 1] == "90.000"

    def test_no_seek_flag_when_starting_at_zero(self) -> None:
        assert "-ss" not in build_decode_args(Path("f"), Path("a.mp3"), sample_rate=16000)

    def test_duration_limit_is_placed_after_input(self) -> None:
        args = build_decode_args(
            Path("ffmpeg"), Path("a.mp3"), sample_rate=16000, duration_seconds=30
        )
        assert args.index("-t") > args.index("-i")

    def test_hostile_filename_stays_one_argument(self) -> None:
        """A filename with shell metacharacters must not become extra args."""
        hostile = Path('a" & del C:\\ & echo ".mp3')
        args = build_decode_args(Path("ffmpeg"), hostile, sample_rate=16000)
        assert str(hostile) in args
        assert args.count(str(hostile)) == 1

    def test_hebrew_filename_is_passed_through_verbatim(self) -> None:
        path = Path("C:/media/ישיבת הנהלה.mp4")
        args = build_decode_args(Path("ffmpeg"), path, sample_rate=16000)
        assert str(path) in args

    def test_probe_args_request_json(self) -> None:
        args = build_probe_args(Path("ffprobe"), Path("a.mp3"))
        assert args[args.index("-print_format") + 1] == "json"
        assert "-show_streams" in args and "-show_format" in args

    def test_run_command_rejects_a_string(self) -> None:
        with pytest.raises(FFmpegError):
            run_command("ffmpeg -version")  # type: ignore[arg-type]

    def test_run_command_rejects_an_empty_list(self) -> None:
        with pytest.raises(FFmpegError):
            run_command([])


class TestStderrClassification:
    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            ("No space left on device", ErrorCode.INSUFFICIENT_DISK),
            ("Decoder (codec ac3) not found for input stream", ErrorCode.UNSUPPORTED_CODEC),
            ("Unknown encoder 'foo'", ErrorCode.UNSUPPORTED_CODEC),
            ("Invalid data found when processing input", ErrorCode.MEDIA_CORRUPT),
            ("moov atom not found", ErrorCode.MEDIA_CORRUPT),
            ("something else entirely", ErrorCode.FFMPEG_FAILED),
        ],
    )
    def test_maps_stderr_to_actionable_codes(self, stderr: str, expected: ErrorCode) -> None:
        assert _classify_stderr(stderr) is expected

    def test_summary_keeps_the_tail_of_the_output(self) -> None:
        stderr = "\n".join(f"line {i}" for i in range(50))
        summary = _summarise_stderr(stderr)
        assert "line 49" in summary
        assert "line 0" not in summary

    def test_summary_handles_empty_output(self) -> None:
        assert _summarise_stderr("") == "ffmpeg produced no diagnostics"


# --------------------------------------------------------------------------
# Probe — needs FFmpeg
# --------------------------------------------------------------------------
@requires_ffmpeg
class TestProbe:
    def test_reads_audio_properties(
        self, sample_mp3: Path, tools: FFmpegTools, settings: Settings
    ) -> None:
        info = probe_media(sample_mp3, tools, settings)
        assert info.has_audio
        assert not info.has_video
        assert info.duration == pytest.approx(6.0, abs=0.4)
        assert info.size_bytes > 0
        assert info.audio_codec

    def test_detects_video_stream(
        self, sample_mp4: Path, tools: FFmpegTools, settings: Settings
    ) -> None:
        info = probe_media(sample_mp4, tools, settings)
        assert info.has_video
        assert info.has_audio
        assert info.video_codec

    def test_accepts_hebrew_filename(
        self, hebrew_named_mp3: Path, tools: FFmpegTools, settings: Settings
    ) -> None:
        info = probe_media(hebrew_named_mp3, tools, settings)
        assert "הקלטה" in info.filename
        assert info.duration > 0

    def test_rejects_file_that_is_not_media_despite_extension(
        self, corrupt_file: Path, tools: FFmpegTools, settings: Settings
    ) -> None:
        with pytest.raises(MediaError) as info:
            probe_media(corrupt_file, tools, settings)
        assert info.value.code in (ErrorCode.MEDIA_CORRUPT, ErrorCode.UNSUPPORTED_FORMAT)

    def test_rejects_empty_file(
        self, tmp_path: Path, tools: FFmpegTools, settings: Settings
    ) -> None:
        empty = tmp_path / "empty.mp3"
        empty.touch()
        with pytest.raises(MediaError) as info:
            probe_media(empty, tools, settings)
        assert info.value.code is ErrorCode.MEDIA_CORRUPT

    def test_rejects_missing_file(
        self, tmp_path: Path, tools: FFmpegTools, settings: Settings
    ) -> None:
        with pytest.raises(MediaError):
            probe_media(tmp_path / "nope.mp3", tools, settings)

    def test_enforces_the_size_limit(
        self, sample_mp3: Path, tools: FFmpegTools, settings: Settings
    ) -> None:
        tiny_limit = settings.model_copy(update={"max_upload_bytes": 10})
        with pytest.raises(MediaError) as info:
            probe_media(sample_mp3, tools, tiny_limit)
        assert info.value.code is ErrorCode.MEDIA_TOO_LARGE

    def test_locate_tools_finds_both_binaries(self, settings: Settings) -> None:
        found = locate_tools(settings)
        assert found.ffmpeg.exists()
        assert found.ffprobe.exists()
        assert found.version != "unknown"


# --------------------------------------------------------------------------
# Streaming decode
# --------------------------------------------------------------------------
@requires_ffmpeg
class TestIterWindows:
    def test_yields_float32_mono_samples(self, sample_mp3: Path, tools: FFmpegTools) -> None:
        windows = list(
            iter_windows(
                sample_mp3, tools, sample_rate=16000, window_seconds=30.0, overlap_seconds=0.0
            )
        )
        assert windows
        assert windows[0].samples.dtype == np.float32
        assert windows[0].samples.ndim == 1

    def test_single_window_for_short_audio(self, sample_mp3: Path, tools: FFmpegTools) -> None:
        windows = list(
            iter_windows(
                sample_mp3, tools, sample_rate=16000, window_seconds=30.0, overlap_seconds=1.0
            )
        )
        assert len(windows) == 1
        assert windows[0].is_last
        assert windows[0].overlap_lead == 0.0

    def test_long_audio_produces_several_overlapping_windows(
        self, long_mp3: Path, tools: FFmpegTools
    ) -> None:
        windows = list(
            iter_windows(
                long_mp3, tools, sample_rate=16000, window_seconds=20.0, overlap_seconds=2.0
            )
        )
        assert len(windows) >= 3
        # Each window after the first declares its overlap and starts one hop
        # (window - overlap) after its predecessor.
        for previous, current in zip(windows, windows[1:], strict=False):
            assert current.overlap_lead == pytest.approx(2.0)
            assert current.start == pytest.approx(previous.start + 18.0, abs=0.05)

    def test_timeline_is_continuous_and_covers_the_recording(
        self, long_mp3: Path, tools: FFmpegTools
    ) -> None:
        windows = list(
            iter_windows(
                long_mp3, tools, sample_rate=16000, window_seconds=20.0, overlap_seconds=2.0
            )
        )
        assert windows[0].start == pytest.approx(0.0)
        assert windows[-1].end == pytest.approx(70.0, abs=0.5)
        for window in windows:
            assert window.end > window.start

    def test_start_at_skips_the_beginning(self, long_mp3: Path, tools: FFmpegTools) -> None:
        windows = list(
            iter_windows(
                long_mp3,
                tools,
                sample_rate=16000,
                window_seconds=20.0,
                overlap_seconds=2.0,
                start_at=40.0,
            )
        )
        assert windows[0].start == pytest.approx(40.0, abs=0.1)
        assert windows[-1].end == pytest.approx(70.0, abs=0.6)

    def test_memory_stays_bounded_by_the_window_not_the_file(
        self, long_mp3: Path, tools: FFmpegTools
    ) -> None:
        """No window may exceed its configured length, whatever the file size."""
        limit_samples = int(20.0 * 16000)
        for window in iter_windows(
            long_mp3, tools, sample_rate=16000, window_seconds=20.0, overlap_seconds=2.0
        ):
            assert window.samples.nbytes <= limit_samples * BYTES_PER_SAMPLE

    def test_cancellation_stops_the_decoder(self, long_mp3: Path, tools: FFmpegTools) -> None:
        from tiktuv.errors import JobCancelled

        token = CancelToken()
        produced = 0
        with pytest.raises(JobCancelled):
            for _window in iter_windows(
                long_mp3,
                tools,
                sample_rate=16000,
                window_seconds=5.0,
                overlap_seconds=0.5,
                cancel=token,
            ):
                produced += 1
                if produced == 2:
                    token.cancel()
        assert produced == 2

    def test_rejects_invalid_overlap(self, sample_mp3: Path, tools: FFmpegTools) -> None:
        with pytest.raises(ValueError):
            list(
                iter_windows(
                    sample_mp3, tools, sample_rate=16000, window_seconds=5.0, overlap_seconds=5.0
                )
            )

    def test_reports_a_clear_error_for_a_corrupt_file(
        self, corrupt_file: Path, tools: FFmpegTools
    ) -> None:
        with pytest.raises(FFmpegError):
            list(
                iter_windows(
                    corrupt_file, tools, sample_rate=16000, window_seconds=5.0, overlap_seconds=0.0
                )
            )


class TestWindowMath:
    @pytest.mark.parametrize(
        ("duration", "window", "overlap", "expected"),
        [
            (0.0, 30.0, 5.0, 0),
            (10.0, 30.0, 5.0, 1),
            (30.0, 30.0, 5.0, 1),
            (55.0, 30.0, 5.0, 2),
            (80.0, 30.0, 5.0, 3),
        ],
    )
    def test_estimate_window_count(
        self, duration: float, window: float, overlap: float, expected: int
    ) -> None:
        assert estimate_window_count(duration, window, overlap) == expected

    def test_estimate_respects_a_resume_offset(self) -> None:
        assert estimate_window_count(100.0, 30.0, 5.0, start_at=90.0) == 1


class TestSignalHelpers:
    def test_peak_rms_of_silence_is_zero(self) -> None:
        assert peak_rms(np.zeros(1000, dtype=np.float32)) == 0.0

    def test_peak_rms_of_a_constant_signal(self) -> None:
        assert peak_rms(np.full(1000, 0.5, dtype=np.float32)) == pytest.approx(0.5)

    def test_peak_rms_of_empty_input(self) -> None:
        assert peak_rms(np.zeros(0, dtype=np.float32)) == 0.0

    def test_find_silences_locates_a_quiet_span(self) -> None:
        rate = 16000
        loud = np.full(rate, 0.5, dtype=np.float32)
        quiet = np.zeros(3 * rate, dtype=np.float32)
        samples = np.concatenate([loud, quiet, loud])

        spans = find_silences(samples, rate, min_duration=2.0)
        assert len(spans) == 1
        start, end = spans[0]
        assert start == pytest.approx(1.0, abs=0.1)
        assert end == pytest.approx(4.0, abs=0.1)

    def test_find_silences_ignores_short_gaps(self) -> None:
        rate = 16000
        samples = np.concatenate(
            [
                np.full(rate, 0.5, dtype=np.float32),
                np.zeros(rate // 2, dtype=np.float32),
                np.full(rate, 0.5, dtype=np.float32),
            ]
        )
        assert find_silences(samples, rate, min_duration=2.0) == []

    def test_find_silences_on_empty_input(self) -> None:
        assert find_silences(np.zeros(0, dtype=np.float32), 16000) == []
