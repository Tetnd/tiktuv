"""Native folder-picker plumbing.

No GUI is involved: the picker is a registered callable, so these exercise the
registry, the guards around it, and the API contract.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tiktuv import dialogs


@pytest.fixture(autouse=True)
def _clear_picker() -> Iterator[None]:
    dialogs.register_folder_picker(None)
    yield
    dialogs.register_folder_picker(None)


class TestRegistry:
    def test_unavailable_by_default(self) -> None:
        assert dialogs.is_available() is False
        assert dialogs.pick_folder() is None

    def test_registering_makes_it_available(self, tmp_path: Path) -> None:
        dialogs.register_folder_picker(lambda _initial: str(tmp_path))
        assert dialogs.is_available() is True
        assert dialogs.pick_folder() == str(tmp_path)

    def test_clearing_removes_it(self, tmp_path: Path) -> None:
        dialogs.register_folder_picker(lambda _initial: str(tmp_path))
        dialogs.register_folder_picker(None)
        assert dialogs.is_available() is False

    def test_cancellation_returns_none(self) -> None:
        dialogs.register_folder_picker(lambda _initial: None)
        assert dialogs.pick_folder() is None

    def test_empty_selection_is_treated_as_cancel(self) -> None:
        dialogs.register_folder_picker(lambda _initial: "")
        assert dialogs.pick_folder() is None


class TestInitialDirectory:
    def test_existing_directory_is_passed_through(self, tmp_path: Path) -> None:
        seen: list[str | None] = []
        dialogs.register_folder_picker(lambda initial: (seen.append(initial), str(tmp_path))[1])
        dialogs.pick_folder(str(tmp_path))
        assert seen == [str(tmp_path)]

    def test_a_file_path_falls_back_to_its_parent(self, tmp_path: Path) -> None:
        target = tmp_path / "some-file.txt"
        target.write_text("x", encoding="utf-8")
        seen: list[str | None] = []
        dialogs.register_folder_picker(lambda initial: (seen.append(initial), "")[1])
        dialogs.pick_folder(str(target))
        assert seen == [str(tmp_path)]

    def test_a_missing_path_is_dropped(self, tmp_path: Path) -> None:
        seen: list[str | None] = []
        dialogs.register_folder_picker(lambda initial: (seen.append(initial), "")[1])
        dialogs.pick_folder(str(tmp_path / "nope" / "deeper"))
        assert seen == [None]

    def test_hebrew_path_survives(self, tmp_path: Path) -> None:
        hebrew = tmp_path / "תמלולים"
        hebrew.mkdir()
        dialogs.register_folder_picker(lambda _initial: str(hebrew))
        assert dialogs.pick_folder(str(hebrew)) == str(hebrew)


class TestRobustness:
    def test_a_failing_picker_does_not_raise(self) -> None:
        def _explode(_initial: str | None) -> str | None:
            raise RuntimeError("the dialog exploded")

        dialogs.register_folder_picker(_explode)
        assert dialogs.pick_folder() is None

    def test_only_one_dialog_at_a_time(self) -> None:
        """A second request while one is open must not open another."""
        opened = threading.Event()
        release = threading.Event()
        results: list[str | None] = []

        def _slow(_initial: str | None) -> str | None:
            opened.set()
            release.wait(timeout=5)
            return "C:/chosen"

        dialogs.register_folder_picker(_slow)
        worker = threading.Thread(target=lambda: results.append(dialogs.pick_folder()))
        worker.start()
        assert opened.wait(timeout=5)

        # While the first is open, a second call is refused rather than queued.
        assert dialogs.pick_folder() is None

        release.set()
        worker.join(timeout=5)
        assert results == [str(Path("C:/chosen"))]


class TestApi:
    def test_availability_endpoint(self, client: TestClient) -> None:
        response = client.get("/api/system/pick-folder")
        assert response.status_code == 200
        assert response.json()["available"] is False

    def test_availability_reflects_registration(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        dialogs.register_folder_picker(lambda _initial: str(tmp_path))
        assert client.get("/api/system/pick-folder").json()["available"] is True

    def test_picking_returns_the_path(self, client: TestClient, tmp_path: Path) -> None:
        chosen = tmp_path / "יעד"
        chosen.mkdir()
        dialogs.register_folder_picker(lambda _initial: str(chosen))

        response = client.post("/api/system/pick-folder", json={"initial": None})
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True
        assert body["path"] == str(chosen)

    def test_picking_without_a_host_reports_unavailable(self, client: TestClient) -> None:
        body = client.post("/api/system/pick-folder", json={"initial": None}).json()
        assert body["available"] is False
        assert body["path"] is None

    def test_cancel_returns_a_null_path(self, client: TestClient) -> None:
        dialogs.register_folder_picker(lambda _initial: None)
        body = client.post("/api/system/pick-folder", json={"initial": None}).json()
        assert body["available"] is True
        assert body["path"] is None

    def test_requires_authentication(self, unauthenticated_client: TestClient) -> None:
        assert unauthenticated_client.get("/api/system/pick-folder").status_code == 401
        assert (
            unauthenticated_client.post("/api/system/pick-folder", json={}).status_code == 401
        )

    def test_rejects_unknown_fields(self, client: TestClient) -> None:
        response = client.post("/api/system/pick-folder", json={"surprise": True})
        assert response.status_code == 422
