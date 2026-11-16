"""DeepSeek AI analysis: key management, prompt building, and JSON parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tiktuv.analysis import build_transcript_text, generate_analysis
from tiktuv.config import Settings
from tiktuv.credentials import DeepSeekCredentials
from tiktuv.schemas import JobStatus, Segment, Transcript, TranscriptAnalysis

from .test_api import import_media, wait_for_status


def _transcript() -> Transcript:
    return Transcript(
        job_id="j1",
        language="he",
        model_id="soniox/stt-async-v6",
        duration=10.0,
        segments=[
            Segment(id=0, start=0.0, end=4.0, text="שלום, מה שלומך?", speaker="1"),
            Segment(id=1, start=4.0, end=8.0, text="מצוין, תודה.", speaker="2"),
        ],
        speakers={"1": "דובר 1", "2": "דובר 2"},
    )


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def test_build_transcript_text_labels_speakers() -> None:
    text = build_transcript_text(_transcript(), {"1": "דובר 1", "2": "דובר 2"})
    assert "דובר 1: שלום, מה שלומך?" in text
    assert "דובר 2: מצוין, תודה." in text


def test_generate_analysis_parses_deepseek_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        captured.update(kwargs)
        content = (
            '{"summary": "סיכום", "key_points": ["נקודה"], '
            '"action_items": [{"task": "לשלוח מייל", "assignee": "דובר 1"}]}'
        )
        return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("tiktuv.analysis.httpx.post", fake_post)

    analysis = generate_analysis("key", _transcript(), {"1": "דובר 1", "2": "דובר 2"})

    assert analysis.summary == "סיכום"
    assert analysis.key_points == ["נקודה"]
    assert analysis.action_items[0].task == "לשלוח מייל"
    assert analysis.action_items[0].assignee == "דובר 1"
    assert analysis.model == "deepseek-flash"
    assert captured["json"]["model"] == "deepseek-flash"


def test_generate_analysis_tolerates_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        return _FakeResponse(200, {"choices": [{"message": {"content": "לא JSON בכלל"}}]})

    monkeypatch.setattr("tiktuv.analysis.httpx.post", fake_post)
    analysis = generate_analysis("key", _transcript(), {})
    assert analysis.summary == ""
    assert analysis.key_points == []


def test_deepseek_credentials_roundtrip(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", auth_token="test")
    settings.ensure_dirs()
    credentials = DeepSeekCredentials(settings)

    assert credentials.get() is None
    credentials.set("sk-deepseek-abcdef12345678")
    assert credentials.get() == "sk-deepseek-abcdef12345678"
    assert credentials.masked() == "••••5678"
    credentials.clear()
    assert credentials.get() is None


def test_deepseek_key_endpoints(client: TestClient) -> None:
    assert client.get("/api/deepseek/status").json() == {
        "configured": False,
        "masked_key": "",
    }
    response = client.put("/api/deepseek/key", json={"api_key": "sk-deepseek-12345678"})
    assert response.json() == {"configured": True, "masked_key": "••••5678"}
    assert client.delete("/api/deepseek/key").json() == {
        "configured": False,
        "masked_key": "",
    }


@pytest.mark.usefixtures("client")
class TestAnalysisApi:
    def test_analysis_lifecycle(
        self,
        client: TestClient,
        sample_mp3: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        token = import_media(client, sample_mp3)
        job_id = client.post("/api/jobs", json={"media_token": token}).json()["id"]
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)

        # No analysis yet.
        assert client.get(f"/api/jobs/{job_id}/analysis").status_code == 404

        # Without a key the generation fails with a clear error.
        response = client.post(f"/api/jobs/{job_id}/analysis")
        assert response.status_code == 422
        assert response.json()["code"] == "DEEPSEEK_KEY_MISSING"

        client.put("/api/deepseek/key", json={"api_key": "sk-deepseek-test"})
        from tiktuv.api import routes_analysis

        fake = TranscriptAnalysis(job_id=job_id, summary="סיכום", model="deepseek-flash")
        monkeypatch.setattr(routes_analysis, "generate_analysis", lambda *a, **k: fake)

        created = client.post(f"/api/jobs/{job_id}/analysis")
        assert created.status_code == 200
        assert created.json()["summary"] == "סיכום"

        cached = client.get(f"/api/jobs/{job_id}/analysis")
        assert cached.status_code == 200
        assert cached.json()["summary"] == "סיכום"
