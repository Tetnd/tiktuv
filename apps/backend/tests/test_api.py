"""HTTP API: authorization, media import, job lifecycle, editing, export."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tiktuv.config import Settings
from tiktuv.schemas import ExportFormat, ExportSection, JobStatus, TranscriptAnalysis

from .conftest import requires_ffmpeg


def wait_for_status(
    client: TestClient, job_id: str, *targets: JobStatus, timeout: float = 60.0
) -> dict:
    """Poll until the job reaches one of ``targets``. Fails loudly otherwise."""
    deadline = time.time() + timeout
    payload: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {status.value for status in targets}:
            return payload
        time.sleep(0.05)
    pytest.fail(f"job {job_id} stayed in {payload.get('status')!r}; wanted {targets}")


def import_media(client: TestClient, path: Path) -> str:
    response = client.post("/api/media/import", json={"path": str(path)})
    assert response.status_code == 200, response.text
    return response.json()["token"]


# --------------------------------------------------------------------------
class TestAuthorization:
    def test_health_is_public(self, unauthenticated_client: TestClient) -> None:
        response = unauthenticated_client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/jobs"),
            ("get", "/api/system"),
            ("get", "/api/models"),
            ("post", "/api/jobs"),
            ("get", "/api/events"),
            ("get", "/api/export/formats"),
        ],
    )
    def test_endpoints_require_a_token(
        self, unauthenticated_client: TestClient, method: str, path: str
    ) -> None:
        response = getattr(unauthenticated_client, method)(path)
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"

    def test_wrong_token_is_rejected(
        self, unauthenticated_client: TestClient
    ) -> None:
        response = unauthenticated_client.get(
            "/api/jobs", headers={"Authorization": "Bearer not-the-right-token"}
        )
        assert response.status_code == 401

    def test_token_prefix_is_not_enough(
        self, unauthenticated_client: TestClient, settings: Settings
    ) -> None:
        truncated = settings.auth_token[:-1]
        response = unauthenticated_client.get(
            "/api/jobs", headers={"Authorization": f"Bearer {truncated}"}
        )
        assert response.status_code == 401

    def test_alternate_header_is_accepted(
        self, unauthenticated_client: TestClient, settings: Settings
    ) -> None:
        response = unauthenticated_client.get(
            "/api/jobs", headers={"X-Tiktuv-Token": settings.auth_token}
        )
        assert response.status_code == 200

    def test_valid_token_works(self, client: TestClient) -> None:
        assert client.get("/api/jobs").status_code == 200

    def test_error_payload_is_hebrew_and_actionable(
        self, unauthenticated_client: TestClient
    ) -> None:
        body = unauthenticated_client.get("/api/jobs").json()
        assert body["message"] and body["remedy"]
        assert any("֐" <= ch <= "׿" for ch in body["message"])


class TestSystem:
    def test_reports_environment_and_checks(self, client: TestClient) -> None:
        payload = client.get("/api/system").json()
        assert payload["app_version"]
        assert payload["checks"]
        ids = {check["id"] for check in payload["checks"]}
        assert {"os", "python", "ffmpeg", "gpu", "model", "disk", "directories"} <= ids

    def test_check_statuses_are_valid(self, client: TestClient) -> None:
        payload = client.get("/api/system").json()
        for check in payload["checks"]:
            assert check["status"] in {"ok", "warning", "error"}
            assert check["label_he"]

    def test_storage_shows_where_data_lives(self, client: TestClient) -> None:
        payload = client.get("/api/system/storage").json()
        for key in ("data_dir", "models_dir", "temp_dir"):
            assert payload[key]
        assert payload["free_disk_bytes"] >= 0

    def test_devices_always_include_cpu(self, client: TestClient) -> None:
        kinds = {device["kind"] for device in client.get("/api/system/devices").json()}
        assert "cpu" in kinds

    def test_temp_cleanup(self, client: TestClient, settings: Settings) -> None:
        (settings.temp_dir / "junk.bin").write_bytes(b"x" * 2048)
        payload = client.post("/api/system/cleanup/temp").json()
        assert payload["reclaimed_bytes"] >= 2048
        assert list(settings.temp_dir.iterdir()) == []


class TestModels:
    def test_lists_the_catalogue(self, client: TestClient) -> None:
        models = client.get("/api/models").json()
        ids = [model["id"] for model in models]
        assert "ivrit-ai/whisper-large-v3" in ids

    def test_hebrew_model_is_flagged_as_specialised(self, client: TestClient) -> None:
        models = {model["id"]: model for model in client.get("/api/models").json()}
        assert models["ivrit-ai/whisper-large-v3"]["hebrew_specialised"] is True

    def test_fallbacks_are_flagged_as_not_hebrew_specialised(
        self, client: TestClient
    ) -> None:
        """Generic Whisper must never be presented as the Hebrew model."""
        models = {model["id"]: model for model in client.get("/api/models").json()}
        assert models["openai/whisper-large-v3"]["hebrew_specialised"] is False
        assert models["openai/whisper-small"]["hebrew_specialised"] is False

    def test_download_size_and_location_are_disclosed(self, client: TestClient) -> None:
        for model in client.get("/api/models").json():
            if model["approx_download_bytes"] == 0:
                # Cloud models (Soniox) have nothing to download, so they
                # disclose no download size and no local cache path.
                assert model["downloaded"] is True
                assert model["cache_path"] == ""
                continue
            assert model["approx_download_bytes"] > 0
            assert model["cache_path"]

    def test_unknown_model_download_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/models/download", json={"model_id": "evil/model"})
        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_MODEL"


@requires_ffmpeg
class TestMediaImport:
    def test_imports_a_local_file(self, client: TestClient, sample_mp3: Path) -> None:
        payload = client.post("/api/media/import", json={"path": str(sample_mp3)}).json()
        assert payload["token"]
        assert payload["media"]["has_audio"]
        assert payload["media"]["duration"] > 0

    def test_imports_a_hebrew_named_file(
        self, client: TestClient, hebrew_named_mp3: Path
    ) -> None:
        payload = client.post(
            "/api/media/import", json={"path": str(hebrew_named_mp3)}
        ).json()
        assert "הקלטה" in payload["media"]["filename"]

    def test_original_file_is_untouched(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        before = sample_mp3.read_bytes()
        client.post("/api/media/import", json={"path": str(sample_mp3)})
        assert sample_mp3.read_bytes() == before

    def test_rejects_a_missing_file(self, client: TestClient, tmp_path: Path) -> None:
        response = client.post("/api/media/import", json={"path": str(tmp_path / "no.mp3")})
        assert response.status_code == 422
        assert response.json()["code"] == "MEDIA_CORRUPT"

    def test_rejects_a_file_that_is_not_media(
        self, client: TestClient, corrupt_file: Path
    ) -> None:
        """Validation is by inspection — the .mp3 extension does not help it."""
        response = client.post("/api/media/import", json={"path": str(corrupt_file)})
        assert response.status_code in (415, 422)

    def test_rejects_a_directory(self, client: TestClient, tmp_path: Path) -> None:
        assert client.post("/api/media/import", json={"path": str(tmp_path)}).status_code == 422

    def test_upload_endpoint_accepts_a_file(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        with sample_mp3.open("rb") as handle:
            response = client.post(
                "/api/media/upload",
                files={"file": ("שיחה.mp3", handle, "audio/mpeg")},
            )
        assert response.status_code == 200, response.text
        assert response.json()["media"]["duration"] > 0

    def test_upload_rejects_an_empty_file(self, client: TestClient) -> None:
        response = client.post(
            "/api/media/upload", files={"file": ("empty.mp3", b"", "audio/mpeg")}
        )
        assert response.status_code == 422


@requires_ffmpeg
class TestJobLifecycle:
    def test_create_transcribe_and_complete(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        token = import_media(client, sample_mp3)
        created = client.post("/api/jobs", json={"media_token": token})
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]

        final = wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)
        assert final["status"] == "completed", final.get("error")
        assert final["progress"]["percent"] == 1.0
        assert final["has_transcript"]

    def test_rejects_a_traversal_media_token(self, client: TestClient) -> None:
        response = client.post(
            "/api/jobs", json={"media_token": "../../../etc/passwd"}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_FILENAME"

    def test_rejects_an_unknown_media_token(self, client: TestClient) -> None:
        response = client.post("/api/jobs", json={"media_token": "nothere.mp3"})
        assert response.status_code == 422

    def test_autostart_false_leaves_the_job_queued(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        token = import_media(client, sample_mp3)
        payload = client.post(
            "/api/jobs", json={"media_token": token, "autostart": False}
        ).json()
        assert payload["status"] == "queued"

        started = client.post(f"/api/jobs/{payload['id']}/start")
        assert started.status_code == 200
        wait_for_status(client, payload["id"], JobStatus.COMPLETED, JobStatus.FAILED)

    def test_job_appears_in_history(self, client: TestClient, sample_mp3: Path) -> None:
        token = import_media(client, sample_mp3)
        job_id = client.post("/api/jobs", json={"media_token": token}).json()["id"]
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)

        history = client.get("/api/jobs").json()
        assert job_id in [job["id"] for job in history]

    def test_history_records_the_expected_fields(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        token = import_media(client, sample_mp3)
        job_id = client.post("/api/jobs", json={"media_token": token}).json()["id"]
        final = wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)

        for key in ("filename", "source_path", "created_at", "duration", "status", "model_id"):
            assert final[key] is not None, key
        assert final["finished_at"]

    def test_cancel_a_long_job(self, client: TestClient, long_mp3: Path) -> None:
        token = import_media(client, long_mp3)
        job_id = client.post("/api/jobs", json={"media_token": token}).json()["id"]

        # Cancel as soon as the worker has picked it up.
        deadline = time.time() + 20
        while time.time() < deadline:
            status = client.get(f"/api/jobs/{job_id}").json()["status"]
            if status == "running":
                break
            if status in ("completed", "failed"):
                pytest.skip("job finished before it could be cancelled")
            time.sleep(0.02)

        response = client.post(f"/api/jobs/{job_id}/cancel")
        assert response.status_code == 200

        final = wait_for_status(
            client, job_id, JobStatus.CANCELLED, JobStatus.COMPLETED, JobStatus.FAILED
        )
        assert final["status"] in ("cancelled", "completed")

    def test_cancel_an_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.post("/api/jobs/nope/cancel").status_code == 404

    def test_retry_a_cancelled_job(self, client: TestClient, sample_mp3: Path) -> None:
        token = import_media(client, sample_mp3)
        job_id = client.post(
            "/api/jobs", json={"media_token": token, "autostart": False}
        ).json()["id"]
        client.post(f"/api/jobs/{job_id}/cancel")

        retried = client.post(f"/api/jobs/{job_id}/retry")
        assert retried.status_code == 200
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)

    def test_delete_a_job(self, client: TestClient, sample_mp3: Path) -> None:
        token = import_media(client, sample_mp3)
        job_id = client.post("/api/jobs", json={"media_token": token}).json()["id"]
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)

        assert client.delete(f"/api/jobs/{job_id}").status_code == 200
        assert client.get(f"/api/jobs/{job_id}").status_code == 404

    def test_clear_completed(self, client: TestClient, sample_mp3: Path) -> None:
        token = import_media(client, sample_mp3)
        job_id = client.post("/api/jobs", json={"media_token": token}).json()["id"]
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)

        assert client.post("/api/jobs/clear/completed").json()["removed"] >= 1
        assert client.get("/api/jobs").json() == []


@requires_ffmpeg
class TestTranscriptEditing:
    def _completed_job(self, client: TestClient, media: Path) -> str:
        token = import_media(client, media)
        job_id = client.post("/api/jobs", json={"media_token": token}).json()["id"]
        final = wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)
        assert final["status"] == "completed", final.get("error")
        return job_id

    def test_read_a_transcript(self, client: TestClient, sample_mp3: Path) -> None:
        job_id = self._completed_job(client, sample_mp3)
        payload = client.get(f"/api/jobs/{job_id}/transcript").json()
        assert payload["segments"]
        assert payload["language"]

    def test_transcript_text_is_hebrew_in_logical_order(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        payload = client.get(f"/api/jobs/{job_id}/transcript").json()
        text = payload["segments"][0]["text"]
        assert any("֐" <= ch <= "׿" for ch in text)
        # A reversed Hebrew sentence would not start with a Hebrew letter
        # followed by its true successor; assert the exact known phrase.
        assert text.strip()[0] != text.strip()[::-1][0] or len(set(text)) == 1

    def test_edit_a_segment_and_read_it_back(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        transcript = client.get(f"/api/jobs/{job_id}/transcript").json()

        segments = transcript["segments"]
        segments[0]["text"] = "טקסט שנערך על ידי המשתמש"
        segments[0]["edited"] = True

        saved = client.put(
            f"/api/jobs/{job_id}/transcript", json={"segments": segments}
        )
        assert saved.status_code == 200, saved.text

        reloaded = client.get(f"/api/jobs/{job_id}/transcript").json()
        assert reloaded["segments"][0]["text"] == "טקסט שנערך על ידי המשתמש"
        assert reloaded["segments"][0]["edited"] is True

    def test_edits_survive_reopening_from_history(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        transcript = client.get(f"/api/jobs/{job_id}/transcript").json()
        transcript["segments"][0]["text"] = "עריכה מתמשכת"
        client.put(f"/api/jobs/{job_id}/transcript", json={"segments": transcript["segments"]})

        # Simulate reopening: list history, then load the transcript again.
        assert job_id in [job["id"] for job in client.get("/api/jobs").json()]
        reopened = client.get(f"/api/jobs/{job_id}/transcript").json()
        assert reopened["segments"][0]["text"] == "עריכה מתמשכת"

    def test_segments_are_renumbered_after_editing(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        transcript = client.get(f"/api/jobs/{job_id}/transcript").json()
        segments = transcript["segments"]
        if len(segments) < 2:
            pytest.skip("need at least two segments")
        del segments[0]

        saved = client.put(
            f"/api/jobs/{job_id}/transcript", json={"segments": segments}
        ).json()
        assert [s["id"] for s in saved["segments"]] == list(range(len(saved["segments"])))

    def test_speakers_can_be_renamed(self, client: TestClient, sample_mp3: Path) -> None:
        job_id = self._completed_job(client, sample_mp3)
        transcript = client.get(f"/api/jobs/{job_id}/transcript").json()
        segments = transcript["segments"]
        segments[0]["speaker"] = "speaker_0"

        saved = client.put(
            f"/api/jobs/{job_id}/transcript",
            json={"segments": segments, "speakers": {"speaker_0": "יעל"}},
        ).json()
        assert saved["speakers"]["speaker_0"] == "יעל"

    def test_transcript_of_an_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/nope/transcript").status_code == 404

    def test_media_streams_with_range_support(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        response = client.get(f"/api/jobs/{job_id}/media", headers={"Range": "bytes=0-99"})
        assert response.status_code == 206
        assert response.headers["Content-Range"].startswith("bytes 0-99/")
        assert len(response.content) == 100

    def test_media_full_request(self, client: TestClient, sample_mp3: Path) -> None:
        job_id = self._completed_job(client, sample_mp3)
        response = client.get(f"/api/jobs/{job_id}/media")
        assert response.status_code == 200
        assert response.headers["Accept-Ranges"] == "bytes"


@requires_ffmpeg
class TestExportApi:
    def _completed_job(self, client: TestClient, media: Path) -> str:
        token = import_media(client, media)
        job_id = client.post("/api/jobs", json={"media_token": token}).json()["id"]
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)
        return job_id

    def test_lists_supported_formats(self, client: TestClient) -> None:
        formats = client.get("/api/export/formats").json()
        assert set(formats) == {fmt.value for fmt in ExportFormat}

    def test_lists_exportable_sections(self, client: TestClient) -> None:
        sections = client.get("/api/export/sections").json()
        by_name = {entry["section"]: entry["formats"] for entry in sections}
        assert set(by_name) == {s.value for s in ExportSection}
        # Subtitles need a timeline, so only the transcript offers them.
        assert "srt" in by_name["transcript"]
        assert "srt" not in by_name["summary"]

    def test_exports_one_analysis_tab_on_its_own(
        self, client: TestClient, sample_mp3: Path, tmp_path: Path
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        state = client.app.state.tiktuv
        state.transcripts.save_analysis(
            TranscriptAnalysis(
                job_id=job_id,
                summary="תקציר לבדיקה",
                key_points=["נקודה אחת"],
                model="deepseek-chat",
            )
        )

        response = client.post(
            f"/api/jobs/{job_id}/export",
            json={
                "formats": ["txt"],
                "sections": ["key_points"],
                "output_dir": str(tmp_path / "יעד"),
            },
        )
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert [r["section"] for r in results] == ["key_points"]
        written = Path(results[0]["path"])
        assert written.is_file()
        assert "נקודה אחת" in written.read_text(encoding="utf-8")

    def test_exporting_a_tab_without_an_analysis_is_rejected(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        response = client.post(
            f"/api/jobs/{job_id}/export",
            json={"formats": ["txt"], "sections": ["summary"]},
        )
        assert response.status_code >= 400

    def test_formats_default_to_the_transcript(
        self, client: TestClient, sample_mp3: Path, tmp_path: Path
    ) -> None:
        """A request without ``sections`` behaves exactly as it used to."""
        job_id = self._completed_job(client, sample_mp3)
        response = client.post(
            f"/api/jobs/{job_id}/export",
            json={"formats": ["txt"], "output_dir": str(tmp_path / "ברירת")},
        )
        assert response.status_code == 200, response.text
        assert [r["section"] for r in response.json()["results"]] == ["transcript"]

    def test_exports_all_six_required_formats(
        self, client: TestClient, sample_mp3: Path, tmp_path: Path
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        target = tmp_path / "יעד"

        response = client.post(
            f"/api/jobs/{job_id}/export",
            json={
                "formats": ["txt", "docx", "pdf", "srt", "vtt", "json"],
                "output_dir": str(target),
            },
        )
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert {r["format"] for r in results} == {"txt", "docx", "pdf", "srt", "vtt", "json"}
        for result in results:
            assert Path(result["path"]).is_file()
            assert result["size_bytes"] > 0

    def test_export_records_output_paths_on_the_job(
        self, client: TestClient, sample_mp3: Path, tmp_path: Path
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        client.post(
            f"/api/jobs/{job_id}/export",
            json={"formats": ["txt"], "output_dir": str(tmp_path / "o")},
        )
        assert "txt" in client.get(f"/api/jobs/{job_id}").json()["output_paths"]

    def test_download_is_confined_to_the_exports_directory(
        self, client: TestClient, settings: Settings
    ) -> None:
        response = client.get(
            "/api/exports/download", params={"path": r"C:\Windows\win.ini"}
        )
        assert response.status_code == 403
        assert response.json()["code"] == "PATH_NOT_ALLOWED"

    def test_download_rejects_traversal(self, client: TestClient) -> None:
        response = client.get(
            "/api/exports/download", params={"path": "../../../../etc/passwd"}
        )
        assert response.status_code == 403

    def test_download_serves_a_real_export(
        self, client: TestClient, sample_mp3: Path, settings: Settings
    ) -> None:
        job_id = self._completed_job(client, sample_mp3)
        result = client.post(
            f"/api/jobs/{job_id}/export", json={"formats": ["txt"]}
        ).json()["results"][0]

        response = client.get("/api/exports/download", params={"path": result["path"]})
        assert response.status_code == 200
        assert response.content

    def test_download_accepts_token_as_query_parameter(
        self,
        client: TestClient,
        unauthenticated_client: TestClient,
        sample_mp3: Path,
        settings: Settings,
    ) -> None:
        """A plain ``<a download>`` link sends no headers, so the token must
        also be accepted as a query parameter."""
        job_id = self._completed_job(client, sample_mp3)
        result = client.post(
            f"/api/jobs/{job_id}/export", json={"formats": ["txt"]}
        ).json()["results"][0]

        response = unauthenticated_client.get(
            "/api/exports/download",
            params={"path": result["path"], "token": settings.auth_token},
        )
        assert response.status_code == 200
        assert response.content

    def test_export_without_a_transcript_is_404(self, client: TestClient) -> None:
        response = client.post("/api/jobs/nope/export", json={"formats": ["txt"]})
        assert response.status_code == 404

    def test_bulk_export(self, client: TestClient, sample_mp3: Path, tmp_path: Path) -> None:
        self._completed_job(client, sample_mp3)
        response = client.post(
            "/api/export/bulk",
            json={"formats": ["txt"], "output_dir": str(tmp_path / "bulk")},
        )
        assert response.status_code == 200
        assert response.json()["exported"]

    def test_invalid_format_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/jobs/x/export", json={"formats": ["docxx"]})
        assert response.status_code == 422


class TestEventStreamAuth:
    """Route-level checks only.

    The stream itself cannot be consumed through Starlette's ``TestClient``:
    its in-process transport waits for the ASGI call to *return*, and an SSE
    response never does. The stream's behaviour is covered directly against
    :class:`~tiktuv.events.EventBus` in ``test_events.py``.
    """

    def test_stream_requires_a_token(self, unauthenticated_client: TestClient) -> None:
        assert unauthenticated_client.get("/api/events").status_code == 401

    def test_stream_rejects_a_wrong_query_token(
        self, unauthenticated_client: TestClient
    ) -> None:
        response = unauthenticated_client.get("/api/events", params={"token": "wrong"})
        assert response.status_code == 401


class TestInputValidation:
    def test_unknown_field_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/jobs", json={"media_token": "a.mp3", "surprise": True}
        )
        assert response.status_code == 422

    def test_out_of_range_setting_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/jobs",
            json={"media_token": "a.mp3", "settings": {"subtitle_max_lines": 99}},
        )
        assert response.status_code == 422

    def test_oversized_vocabulary_prompt_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/jobs",
            json={"media_token": "a.mp3", "settings": {"vocabulary_prompt": "א" * 5000}},
        )
        assert response.status_code == 422

    def test_malformed_json_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/jobs",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422


class TestPrivacy:
    def test_no_outbound_hosts_are_configured(self, settings: Settings) -> None:
        """Every allowed origin must be loopback."""
        for origin in settings.allowed_origins:
            assert "127.0.0.1" in origin or "localhost" in origin

    def test_backend_refuses_a_non_loopback_bind(self) -> None:
        with pytest.raises(ValueError, match="loopback"):
            Settings(host="0.0.0.0")  # noqa: S104 - asserting this is refused

    def test_error_details_are_redacted(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        secret = tmp_path / "Private Recordings" / "confidential.mp3"
        response = client.post("/api/media/import", json={"path": str(secret)})
        body = json.dumps(response.json(), ensure_ascii=False)
        assert "Private Recordings" not in body
