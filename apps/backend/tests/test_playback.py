"""The playback copy must sit on exactly the timeline the transcript uses."""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tiktuv.media.ffmpeg import FFmpegTools, build_decode_args
from tiktuv.media.playback import PLAYBACK_SAMPLE_RATE, ensure_playback_copy, playback_path
from tiktuv.schemas import JobStatus

from .conftest import requires_ffmpeg
from .test_api import import_media, wait_for_status

pytestmark = requires_ffmpeg


def _make(tools: FFmpegTools, tmp_path: Path, name: str, extra: list[str]) -> Path:
    """A 5 s tone, slowed by 10% (so 5.556 s), in the container ``name`` implies."""
    out = tmp_path / name
    args = [
        str(tools.ffmpeg), "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=5",
        *extra,
        "-af", "atempo=0.9",
        str(out),
    ]
    subprocess.run(args, check=True, capture_output=True)
    return out


def _decode_frames(tools: FFmpegTools, source: Path) -> int:
    """How many 16 kHz samples the *transcription* gets from this file."""
    args = build_decode_args(tools.ffmpeg, source, sample_rate=PLAYBACK_SAMPLE_RATE)
    raw = subprocess.run(args, check=True, capture_output=True).stdout
    return len(raw) // 4  # f32le


@pytest.mark.parametrize(
    ("name", "extra"),
    [
        ("slow.mp3", ["-q:a", "4"]),
        ("slow.m4a", ["-c:a", "aac"]),
        ("slow.ogg", ["-c:a", "libvorbis"]),
        ("slow.flac", ["-c:a", "flac"]),
        ("slow.wav", []),
    ],
)
def test_the_copy_has_exactly_the_samples_the_transcription_decodes(
    tools: FFmpegTools, tmp_path: Path, name: str, extra: list[str]
) -> None:
    source = _make(tools, tmp_path, name, extra)
    copy = ensure_playback_copy(tools, source, tmp_path / "temp", "job1")

    with wave.open(str(copy)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == PLAYBACK_SAMPLE_RATE
        assert handle.getsampwidth() == 2
        frames = handle.getnframes()

    assert frames == _decode_frames(tools, source)
    # A 5 s recording slowed by 10% is about 5.56 s long, not 5.
    assert frames / PLAYBACK_SAMPLE_RATE == pytest.approx(5 / 0.9, abs=0.15)


def test_a_video_with_slowed_audio_plays_its_audio_on_the_same_timeline(
    tools: FFmpegTools, tmp_path: Path
) -> None:
    source = tmp_path / "slow.mp4"
    subprocess.run(
        [
            str(tools.ffmpeg), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=160x90:r=25:d=5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=5",
            "-filter_complex", "[0:v]setpts=PTS/0.9[v];[1:a]atempo=0.9[a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", str(source),
        ],
        check=True,
        capture_output=True,
    )
    copy = ensure_playback_copy(tools, source, tmp_path / "temp", "video")
    with wave.open(str(copy)) as handle:
        assert handle.getnframes() == _decode_frames(tools, source)


def test_the_copy_is_reused_until_the_recording_changes(
    tools: FFmpegTools, tmp_path: Path
) -> None:
    source = _make(tools, tmp_path, "a.wav", [])
    temp = tmp_path / "temp"
    first = ensure_playback_copy(tools, source, temp, "j")
    stamp = first.stat().st_mtime_ns
    assert ensure_playback_copy(tools, source, temp, "j") == first
    assert first.stat().st_mtime_ns == stamp  # not rebuilt

    source.touch()  # the recording is newer than the copy
    ensure_playback_copy(tools, source, temp, "j")
    assert playback_path(temp, "j").stat().st_mtime_ns != stamp


def test_a_file_without_audio_is_refused_cleanly(tools: FFmpegTools, tmp_path: Path) -> None:
    from tiktuv.errors import MediaError

    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            str(tools.ffmpeg), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=160x90:r=25:d=2",
            "-c:v", "libx264", "-preset", "ultrafast", str(silent),
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(MediaError):
        ensure_playback_copy(tools, silent, tmp_path / "temp", "none")


@pytest.mark.usefixtures("client")
def test_the_playback_route_serves_the_wav_with_ranges(
    client: TestClient, sample_mp3: Path
) -> None:
    token = import_media(client, sample_mp3)
    job_id = client.post(
        "/api/jobs", json={"media_token": token, "settings": {"enable_proofreading": False}}
    ).json()["id"]
    wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)

    whole = client.get(f"/api/jobs/{job_id}/playback")
    assert whole.status_code == 200
    assert whole.headers["content-type"] == "audio/wav"
    assert whole.content[:4] == b"RIFF"

    part = client.get(f"/api/jobs/{job_id}/playback", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.content) == 100

    # Players cannot send headers: the token may ride in the query string.
    anonymous = TestClient(client.app)
    token_value = client.headers["Authorization"].removeprefix("Bearer ")
    assert anonymous.get(f"/api/jobs/{job_id}/playback").status_code == 401
    assert anonymous.get(f"/api/jobs/{job_id}/playback?token={token_value}").status_code == 200
