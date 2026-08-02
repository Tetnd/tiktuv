"""Shared test fixtures.

Every test runs against a temporary data directory and the deterministic mock
ASR backend, so the suite needs no model download, no GPU and no network. Real
FFmpeg *is* used — the media layer is a large part of what can break, and
mocking it would test nothing.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Must be set before tiktuv.asr.registry decides which backend to build.
os.environ["TIKTUV_ASR_BACKEND"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from tiktuv.app_state import AppState, build_state, start_state, stop_state  # noqa: E402
from tiktuv.config import Settings, reset_settings_cache  # noqa: E402
from tiktuv.db import dispose_engine  # noqa: E402
from tiktuv.errors import FFmpegMissingError  # noqa: E402
from tiktuv.events import EventBus  # noqa: E402
from tiktuv.main import create_app  # noqa: E402
from tiktuv.media.ffmpeg import FFmpegTools, locate_tools  # noqa: E402

#: A Hebrew filename with a space and a period — exercises Unicode paths,
#: sanitization and extension handling in one go.
HEBREW_FILENAME = "הקלטה של ישיבה 2026.mp3"


def _discover_tools() -> FFmpegTools | None:
    """Locate FFmpeg using the *same* discovery code the application uses.

    Deliberately not ``shutil.which``: winget installs FFmpeg into a directory
    that is not always on a fresh shell's PATH, and production handles that in
    ``locate_tools``. Using it here means the tests exercise that path too,
    instead of silently skipping on a machine where the app would work.
    """
    try:
        return locate_tools(Settings(data_dir=Path(tempfile.gettempdir()) / "tiktuv-pytest"))
    except FFmpegMissingError:
        return None


_TOOLS = _discover_tools()


def _ffmpeg_available() -> bool:
    return _TOOLS is not None


requires_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_available(), reason="FFmpeg is not installed on this machine"
)


@pytest.fixture(scope="session")
def tools() -> FFmpegTools:
    if _TOOLS is None:
        pytest.skip("FFmpeg is not installed")
    return _TOOLS


# --------------------------------------------------------------------------
# Media fixtures
# --------------------------------------------------------------------------
def _run_ffmpeg(args: list[str]) -> None:
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"fixture generation failed: {completed.stderr[-500:]}")


@pytest.fixture(scope="session")
def media_factory(tmp_path_factory: pytest.TempPathFactory) -> MediaFactory:
    return MediaFactory(tmp_path_factory.mktemp("fixtures"))


class MediaFactory:
    """Generates small synthetic media files with FFmpeg."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._cache: dict[tuple[str, float, str], Path] = {}

    def audio(self, seconds: float = 6.0, name: str = "sample.mp3") -> Path:
        return self._make(name, seconds, kind="audio")

    def video(self, seconds: float = 6.0, name: str = "sample.mp4") -> Path:
        return self._make(name, seconds, kind="video")

    def _make(self, name: str, seconds: float, *, kind: str) -> Path:
        key = (name, seconds, kind)
        if key in self._cache:
            return self._cache[key]

        path = self.directory / name
        assert _TOOLS is not None
        ffmpeg = str(_TOOLS.ffmpeg)

        # A varying tone rather than silence, so RMS-based logic has something
        # to work with and the decoder produces non-trivial samples.
        audio_source = f"sine=frequency=440:sample_rate=16000:duration={seconds}"
        if kind == "audio":
            args = [ffmpeg, "-y", "-f", "lavfi", "-i", audio_source, "-ac", "1", str(path)]
        else:
            args = [
                ffmpeg, "-y",
                "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={seconds}",
                "-f", "lavfi", "-i", audio_source,
                "-shortest", "-pix_fmt", "yuv420p", str(path),
            ]
        _run_ffmpeg(args)
        self._cache[key] = path
        return path


@pytest.fixture
def sample_mp3(media_factory: MediaFactory) -> Path:
    if not _ffmpeg_available():
        pytest.skip("FFmpeg is not installed")
    return media_factory.audio(6.0, "sample.mp3")


@pytest.fixture
def hebrew_named_mp3(media_factory: MediaFactory) -> Path:
    if not _ffmpeg_available():
        pytest.skip("FFmpeg is not installed")
    return media_factory.audio(4.0, HEBREW_FILENAME)


@pytest.fixture
def sample_mp4(media_factory: MediaFactory) -> Path:
    if not _ffmpeg_available():
        pytest.skip("FFmpeg is not installed")
    return media_factory.video(5.0, "sample.mp4")


@pytest.fixture
def long_mp3(media_factory: MediaFactory) -> Path:
    """Long enough to force several processing windows."""
    if not _ffmpeg_available():
        pytest.skip("FFmpeg is not installed")
    return media_factory.audio(70.0, "long.mp3")


@pytest.fixture
def corrupt_file(tmp_path: Path) -> Path:
    """A file whose extension lies about its contents."""
    path = tmp_path / "not-really-audio.mp3"
    path.write_bytes(b"PK\x03\x04this is a zip file pretending to be an mp3" * 20)
    return path


# --------------------------------------------------------------------------
# Application fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    reset_settings_cache()
    value = Settings(
        data_dir=tmp_path / "data",
        auth_token="test-token-do-not-use-in-production",
        # Small windows so a 70-second fixture produces several of them.
        window_seconds=20.0,
        window_overlap_seconds=2.0,
        max_concurrent_jobs=1,
        log_json=False,
        log_level="WARNING",
    )
    value.ensure_dirs()
    yield value
    dispose_engine()
    reset_settings_cache()


@pytest.fixture
def state(settings: Settings) -> Iterator[AppState]:
    bus = EventBus()
    app_state = build_state(settings, bus)
    start_state(app_state)
    yield app_state
    stop_state(app_state)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, EventBus())
    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {settings.auth_token}"})
        yield test_client


@pytest.fixture
def unauthenticated_client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, EventBus())
    with TestClient(app) as test_client:
        yield test_client
