"""The single-instance lock, and surviving the locks a crash leaves behind."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tiktuv import single_instance
from tiktuv.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(data_dir=tmp_path)
    s.ensure_dirs()
    return s


def write_lock(settings: Settings, pid: int) -> Path:
    path = settings.data_dir / single_instance.LOCK_FILENAME
    path.write_text(
        json.dumps({"pid": pid, "port": 8756, "token": "t"}), encoding="utf-8"
    )
    return path


def a_dead_pid() -> int:
    """A PID that is certainly not running.

    Windows recycles PIDs, so this searches upward for one nothing holds rather
    than assuming a fixed number is free.
    """
    import psutil

    for candidate in range(60_000, 65_000, 7):
        if not psutil.pid_exists(candidate):
            return candidate
    raise RuntimeError("could not find an unused pid")  # pragma: no cover


class TestProcessAlive:
    def test_recognises_this_process(self) -> None:
        assert single_instance._process_alive(os.getpid()) is True

    def test_reports_a_dead_pid_without_raising(self) -> None:
        """Regression: this used to escape as SystemError on Windows.

        ``os.kill(pid, 0)`` against an exited PID normally raises OSError
        (WinError 87), but can instead raise ``SystemError``, which is not an
        OSError — so the guard did not catch it and startup died on any stale
        lock file. Seen for real when launching the portable build.
        """
        assert single_instance._process_alive(a_dead_pid()) is False

    @pytest.mark.parametrize("pid", [0, -1, -12345])
    def test_rejects_impossible_pids(self, pid: int) -> None:
        assert single_instance._process_alive(pid) is False

    @pytest.mark.parametrize("blowup", [SystemError, OSError, RuntimeError])
    def test_a_failing_probe_never_takes_startup_down(
        self, blowup: type[BaseException], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The original bug in one line: a probe raising something that is not
        an OSError propagated all the way out of main() and the app refused to
        start. Whatever the probe does, the answer is 'assume it is gone'."""

        def explode(pid: int) -> bool:
            raise blowup("probe failed")

        monkeypatch.setattr(single_instance.psutil, "pid_exists", explode)
        assert single_instance._process_alive(4321) is False

    def test_a_failing_probe_still_clears_a_stale_lock(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(pid: int) -> bool:
            raise SystemError("returned a result with an exception set")

        monkeypatch.setattr(single_instance.psutil, "pid_exists", explode)
        path = write_lock(settings, 4321)
        assert single_instance.read_lock(settings) is None
        assert not path.exists()


class TestReadLock:
    def test_no_lock_file_means_no_instance(self, settings: Settings) -> None:
        assert single_instance.read_lock(settings) is None

    def test_reports_a_live_instance(self, settings: Settings) -> None:
        write_lock(settings, os.getpid())
        running = single_instance.read_lock(settings)
        assert running is not None
        assert running.pid == os.getpid()
        assert running.base_url == "http://127.0.0.1:8756"

    def test_clears_a_lock_left_by_a_crash(self, settings: Settings) -> None:
        """The whole point: a stale lock must not block startup forever."""
        path = write_lock(settings, a_dead_pid())
        assert single_instance.read_lock(settings) is None
        assert not path.exists()

    def test_clears_an_unreadable_lock(self, settings: Settings) -> None:
        path = settings.data_dir / single_instance.LOCK_FILENAME
        path.write_text("not json at all", encoding="utf-8")
        assert single_instance.read_lock(settings) is None
        assert not path.exists()

    def test_clears_a_lock_missing_fields(self, settings: Settings) -> None:
        path = settings.data_dir / single_instance.LOCK_FILENAME
        path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
        assert single_instance.read_lock(settings) is None
        assert not path.exists()


class TestAcquireAndRelease:
    def test_acquire_writes_our_own_pid(self, settings: Settings) -> None:
        single_instance.acquire(settings)
        payload = json.loads(
            (settings.data_dir / single_instance.LOCK_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        assert payload["pid"] == os.getpid()
        assert payload["token"] == settings.auth_token

    def test_release_removes_our_lock(self, settings: Settings) -> None:
        single_instance.acquire(settings)
        single_instance.release(settings)
        assert not (settings.data_dir / single_instance.LOCK_FILENAME).exists()

    def test_release_leaves_someone_elses_lock_alone(self, settings: Settings) -> None:
        path = write_lock(settings, os.getpid() + 1)
        single_instance.release(settings)
        assert path.exists()

    def test_acquire_succeeds_over_a_stale_lock(self, settings: Settings) -> None:
        write_lock(settings, a_dead_pid())
        single_instance.read_lock(settings)  # what startup does first
        single_instance.acquire(settings)
        payload = json.loads(
            (settings.data_dir / single_instance.LOCK_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        assert payload["pid"] == os.getpid()
