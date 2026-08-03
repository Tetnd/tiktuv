"""Desktop window host: icon lookup, port selection, orphan cleanup.

No GUI is started here — these cover the parts that decide *whether* a window
can appear, which is where the failures actually were.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from tiktuv import app as desktop_app
from tiktuv.config import Settings


class TestFindIcon:
    def test_finds_the_checkout_icon(self) -> None:
        # Regression: an off-by-one in parents[] meant this silently returned
        # None and the window kept the default icon.
        icon = desktop_app.find_icon()
        assert icon is not None, f"searched {desktop_app._ICON_CANDIDATES}"
        assert icon.suffix == ".ico"
        assert icon.is_file()

    def test_candidates_are_absolute(self) -> None:
        for candidate in desktop_app._ICON_CANDIDATES:
            assert candidate.is_absolute()


class TestFindFreePort:
    def test_returns_the_preferred_port_when_free(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        assert desktop_app.find_free_port(free) == free

    def test_moves_on_when_the_port_is_taken(self) -> None:
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen(1)
            port = taken.getsockname()[1]
            chosen = desktop_app.find_free_port(port)
            assert chosen != port
            assert port < chosen <= port + 20

    def test_raises_when_nothing_is_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Busy:
            def __enter__(self) -> _Busy:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def bind(self, _address: tuple[str, int]) -> None:
                raise OSError("in use")

        monkeypatch.setattr(socket, "socket", lambda *_a, **_k: _Busy())
        with pytest.raises(RuntimeError, match="no free port"):
            desktop_app.find_free_port(9000)


class _FakeProcess:
    def __init__(self, pid: int, name: str, cmdline: list[str]) -> None:
        self.pid = pid
        self.info = {"name": name, "cmdline": cmdline}
        self.killed = False

    def kill(self) -> None:
        self.killed = True


class TestClearOrphanedWebviews:
    """The dangerous failure mode here is killing someone else's browser."""

    def _run(
        self, monkeypatch: pytest.MonkeyPatch, storage: Path, processes: list[_FakeProcess]
    ) -> int:
        psutil = pytest.importorskip("psutil")
        monkeypatch.setattr(psutil, "process_iter", lambda _attrs=None: list(processes))
        monkeypatch.setattr(desktop_app.os, "name", "nt")
        return desktop_app.clear_orphaned_webviews(storage)

    def test_kills_only_hosts_using_our_profile(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = tmp_path / "webview"
        ours = _FakeProcess(
            1,
            "msedgewebview2.exe",
            ["msedgewebview2.exe", f"--user-data-dir={storage}\\EBWebView"],
        )
        someone_else = _FakeProcess(
            2,
            "msedgewebview2.exe",
            ["msedgewebview2.exe", r"--user-data-dir=C:\Users\X\AppData\Local\Other\EBWebView"],
        )
        unrelated = _FakeProcess(3, "chrome.exe", ["chrome.exe", f"--user-data-dir={storage}"])

        killed = self._run(monkeypatch, storage, [ours, someone_else, unrelated])

        assert killed == 1
        assert ours.killed is True
        assert someone_else.killed is False, "must never touch another app's WebView2"
        assert unrelated.killed is False, "must only target WebView2 hosts"

    def test_matches_regardless_of_case(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = tmp_path / "webview"
        shouty = _FakeProcess(
            1, "MSEdgeWebView2.EXE", ["x", f"--user-data-dir={str(storage).upper()}\\EBWEBVIEW"]
        )
        assert self._run(monkeypatch, storage, [shouty]) == 1

    def test_no_processes_is_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        assert self._run(monkeypatch, tmp_path / "webview", []) == 0

    def test_survives_a_process_vanishing_mid_scan(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        psutil = pytest.importorskip("psutil")
        storage = tmp_path / "webview"

        class _Vanishing(_FakeProcess):
            def kill(self) -> None:
                raise psutil.NoSuchProcess(self.pid)

        gone = _Vanishing(1, "msedgewebview2.exe", ["x", f"--user-data-dir={storage}"])
        survivor = _FakeProcess(2, "msedgewebview2.exe", ["x", f"--user-data-dir={storage}"])

        # A process exiting between listing and killing must not abort the scan.
        assert self._run(monkeypatch, storage, [gone, survivor]) == 1
        assert survivor.killed is True

    def test_is_a_no_op_off_windows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(desktop_app.os, "name", "posix")
        assert desktop_app.clear_orphaned_webviews(tmp_path) == 0


class TestHealthProbe:
    def test_reports_an_unreachable_instance(self) -> None:
        from tiktuv.single_instance import RunningInstance

        # Port 1 is never a Tiktuv backend.
        assert desktop_app.health_ok(RunningInstance(pid=1, port=1, token="x")) is False


class TestStorageLayout:
    def test_webview_profile_lives_under_the_data_directory(
        self, settings: Settings
    ) -> None:
        """It must be ours alone, so orphan cleanup can identify it."""
        storage: Any = settings.data_dir / desktop_app.WEBVIEW_STORAGE_DIRNAME
        assert storage.parent == settings.data_dir
