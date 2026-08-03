"""Automatic model download: status, cancellation, and the job-pipeline stage.

The Hugging Face client is stubbed — these tests must not touch the network.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tiktuv.asr.downloader import ModelDownloader, _directory_size
from tiktuv.config import Settings
from tiktuv.errors import ErrorCode, ModelError
from tiktuv.schemas import ModelDownloadState

HEBREW = "ivrit-ai/whisper-large-v3"


class _Sibling:
    def __init__(self, name: str, size: int) -> None:
        self.rfilename = name
        self.size = size


class _Info:
    def __init__(self, siblings: list[_Sibling]) -> None:
        self.siblings = siblings


def _make_cache(settings: Settings, model_id: str) -> Path:
    """Create the directory layout ``is_downloaded`` looks for."""
    snapshot = (
        settings.model_cache_dir
        / f"models--{model_id.replace('/', '--')}"
        / "snapshots"
        / "abc123"
    )
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"\0" * 32)
    return snapshot


@pytest.fixture
def downloader(settings: Settings) -> ModelDownloader:
    return ModelDownloader(settings)


class TestStatus:
    def test_starts_idle(self, downloader: ModelDownloader) -> None:
        status = downloader.status()
        assert status.state is ModelDownloadState.IDLE
        assert status.percent == 0.0
        assert not downloader.is_running

    def test_rejects_an_unknown_model(self, downloader: ModelDownloader) -> None:
        with pytest.raises(ModelError) as info:
            downloader.start("evil/model")
        assert info.value.code is ErrorCode.UNKNOWN_MODEL

    def test_already_downloaded_reports_ready_without_network(
        self, downloader: ModelDownloader, settings: Settings
    ) -> None:
        _make_cache(settings, HEBREW)
        status = downloader.start(HEBREW)
        assert status.state is ModelDownloadState.READY
        assert not downloader.is_running

    def test_cancel_when_idle_is_false(self, downloader: ModelDownloader) -> None:
        assert downloader.cancel() is False


class TestDownloadRun:
    """Drives the worker with a stubbed Hugging Face client."""

    def _install_stub(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        *,
        files: list[tuple[str, int]],
        on_download: Any = None,
        fail: Exception | None = None,
    ) -> list[str]:
        fetched: list[str] = []
        module = pytest.importorskip("huggingface_hub", reason="huggingface_hub required")

        class _Api:
            def model_info(self, repo_id: str, files_metadata: bool = False) -> _Info:
                if fail is not None:
                    raise fail
                return _Info([_Sibling(name, size) for name, size in files])

        def _download(repo_id: str, filename: str, cache_dir: str, **_: object) -> str:
            fetched.append(filename)
            if on_download is not None:
                on_download(filename)
            return str(Path(cache_dir) / filename)

        monkeypatch.setattr(module, "HfApi", _Api, raising=False)
        monkeypatch.setattr(module, "hf_hub_download", _download, raising=False)
        return fetched

    def test_downloads_every_matching_file(
        self, downloader: ModelDownloader, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fetched = self._install_stub(
            monkeypatch,
            settings,
            files=[("config.json", 10), ("model.safetensors", 100), ("README.md", 5)],
            on_download=lambda name: _make_cache(settings, HEBREW),
        )
        downloader.start(HEBREW)
        downloader.wait(timeout=15)

        # README.md does not match ALLOW_PATTERNS and must be skipped.
        assert "README.md" not in fetched
        assert {"config.json", "model.safetensors"} <= set(fetched)
        assert downloader.status().state is ModelDownloadState.READY

    def test_reports_progress_and_totals(
        self, downloader: ModelDownloader, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_stub(
            monkeypatch,
            settings,
            files=[("config.json", 40), ("model.safetensors", 60)],
            on_download=lambda name: _make_cache(settings, HEBREW),
        )
        downloader.start(HEBREW)
        downloader.wait(timeout=15)

        status = downloader.status()
        assert status.total_bytes == 100
        assert status.files_total == 2
        assert status.files_done == 2
        assert status.percent == pytest.approx(1.0)

    def test_failure_is_reported_not_raised(
        self, downloader: ModelDownloader, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_stub(
            monkeypatch, settings, files=[], fail=RuntimeError("network is down")
        )
        downloader.start(HEBREW)
        downloader.wait(timeout=15)

        status = downloader.status()
        assert status.state is ModelDownloadState.FAILED
        assert status.error and "network is down" in status.error

    def test_incomplete_download_is_a_failure(
        self, downloader: ModelDownloader, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Files fetched but the cache still not valid must not report ready."""
        self._install_stub(
            monkeypatch, settings, files=[("config.json", 10)], on_download=None
        )
        downloader.start(HEBREW)
        downloader.wait(timeout=15)
        assert downloader.status().state is ModelDownloadState.FAILED

    def test_cancellation_stops_between_files(
        self, downloader: ModelDownloader, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _slow(_name: str) -> None:
            downloader.cancel()
            time.sleep(0.05)

        fetched = self._install_stub(
            monkeypatch,
            settings,
            files=[(f"shard-{i}.safetensors", 10) for i in range(8)],
            on_download=_slow,
        )
        downloader.start(HEBREW)
        downloader.wait(timeout=15)

        assert downloader.status().state is ModelDownloadState.CANCELLED
        # Stopped early rather than fetching everything.
        assert len(fetched) < 8

    def test_second_start_for_the_same_model_is_a_no_op(
        self, downloader: ModelDownloader, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A first-run auto-start and a manual click must not race."""
        self._install_stub(
            monkeypatch,
            settings,
            files=[("model.safetensors", 10)],
            on_download=lambda name: time.sleep(0.3) or _make_cache(settings, HEBREW),
        )
        first = downloader.start(HEBREW)
        second = downloader.start(HEBREW)
        assert first.model_id == second.model_id
        downloader.wait(timeout=15)

    def test_listeners_receive_updates(
        self, downloader: ModelDownloader, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[ModelDownloadState] = []
        downloader.add_listener(lambda status: seen.append(status.state))
        self._install_stub(
            monkeypatch,
            settings,
            files=[("model.safetensors", 10)],
            on_download=lambda name: _make_cache(settings, HEBREW),
        )
        downloader.start(HEBREW)
        downloader.wait(timeout=15)
        assert ModelDownloadState.READY in seen

    def test_a_broken_listener_does_not_break_the_download(
        self, downloader: ModelDownloader, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _explode(_status: object) -> None:
            raise RuntimeError("listener blew up")

        downloader.add_listener(_explode)
        self._install_stub(
            monkeypatch,
            settings,
            files=[("model.safetensors", 10)],
            on_download=lambda name: _make_cache(settings, HEBREW),
        )
        downloader.start(HEBREW)
        downloader.wait(timeout=15)
        assert downloader.status().state is ModelDownloadState.READY


class TestDirectorySize:
    def test_missing_directory_is_zero(self, tmp_path: Path) -> None:
        assert _directory_size(tmp_path / "nope") == 0

    def test_sums_files(self, tmp_path: Path) -> None:
        (tmp_path / "a").write_bytes(b"x" * 10)
        (tmp_path / "b").write_bytes(b"y" * 5)
        assert _directory_size(tmp_path) == 15
