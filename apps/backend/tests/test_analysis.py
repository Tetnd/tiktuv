"""DeepSeek AI analysis: key management, prompt building, and JSON parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tiktuv.analysis import build_transcript_text, generate_analysis
from tiktuv.config import Settings
from tiktuv.credentials import DeepSeekCredentials
from tiktuv.schemas import (
    JobStatus,
    NamedEntity,
    Segment,
    SpeakerQuote,
    SpeakerSummary,
    Transcript,
    TranscriptAnalysis,
)

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

    def test_entities_can_be_added_renamed_and_removed(self, client: TestClient) -> None:
        """The editor's add/remove goes through this endpoint, so the list on
        disk has to be exactly what it last sent."""
        job_id = "job-with-entities"
        client.app.state.tiktuv.transcripts.save_analysis(
            TranscriptAnalysis(
                job_id=job_id,
                summary="סיכום מקורי",
                key_points=["נקודה"],
                named_entities=[NamedEntity(name="יעל", type="person")],
                model="deepseek-flash",
            )
        )

        added = client.put(
            f"/api/jobs/{job_id}/analysis/named-entities",
            json={
                "named_entities": [
                    {"name": "יעל", "type": "person"},
                    {"name": "ישראל", "type": "location"},
                ]
            },
        )
        assert added.status_code == 200, added.text
        assert [e["name"] for e in added.json()["named_entities"]] == ["יעל", "ישראל"]
        # The rest of the analysis is not the editor's to replace.
        assert added.json()["summary"] == "סיכום מקורי"
        assert added.json()["key_points"] == ["נקודה"]
        assert added.json()["model"] == "deepseek-flash"

        stored = client.get(f"/api/jobs/{job_id}/analysis").json()
        assert stored["named_entities"][1] == {"name": "ישראל", "type": "location"}

        removed = client.put(
            f"/api/jobs/{job_id}/analysis/named-entities",
            json={"named_entities": [{"name": "יעל", "type": "organization"}]},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["named_entities"] == [
            {"name": "יעל", "type": "organization"}
        ]

    def test_editing_entities_without_an_analysis_is_404(self, client: TestClient) -> None:
        response = client.put(
            "/api/jobs/never-analysed/analysis/named-entities",
            json={"named_entities": [{"name": "יעל", "type": "person"}]},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "TRANSCRIPT_NOT_FOUND"

    def test_speaker_summaries_can_be_edited(self, client: TestClient) -> None:
        """The editor rewrites each speaker's summary through this endpoint, so
        the list on disk has to be exactly what it last sent."""
        job_id = "job-with-speaker-summaries"
        client.app.state.tiktuv.transcripts.save_analysis(
            TranscriptAnalysis(
                job_id=job_id,
                summary="סיכום מקורי",
                key_points=["נקודה"],
                speaker_summaries=[
                    SpeakerSummary(speaker="דובר 1", summary="פתח את הישיבה."),
                ],
                model="deepseek-flash",
            )
        )

        edited = client.put(
            f"/api/jobs/{job_id}/analysis/speaker-summaries",
            json={
                "speaker_summaries": [
                    {"speaker": "דובר 1", "summary": "פתח את הישיבה והציג את סדר היום."},
                    {"speaker": "דובר 2", "summary": "סקר את התקציב."},
                ]
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["speaker_summaries"] == [
            {"speaker": "דובר 1", "summary": "פתח את הישיבה והציג את סדר היום."},
            {"speaker": "דובר 2", "summary": "סקר את התקציב."},
        ]
        # The rest of the analysis is not the editor's to replace.
        assert edited.json()["summary"] == "סיכום מקורי"
        assert edited.json()["key_points"] == ["נקודה"]
        assert edited.json()["model"] == "deepseek-flash"

        stored = client.get(f"/api/jobs/{job_id}/analysis").json()
        assert stored["speaker_summaries"][1] == {
            "speaker": "דובר 2",
            "summary": "סקר את התקציב.",
        }

    def test_editing_speaker_summaries_without_an_analysis_is_404(
        self, client: TestClient
    ) -> None:
        response = client.put(
            "/api/jobs/never-analysed/analysis/speaker-summaries",
            json={"speaker_summaries": [{"speaker": "דובר 1", "summary": "סיכום"}]},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "TRANSCRIPT_NOT_FOUND"

    def test_unknown_speaker_summary_fields_are_rejected(self, client: TestClient) -> None:
        job_id = "job-strict-speaker-summaries"
        client.app.state.tiktuv.transcripts.save_analysis(
            TranscriptAnalysis(job_id=job_id)
        )
        response = client.put(
            f"/api/jobs/{job_id}/analysis/speaker-summaries",
            json={
                "speaker_summaries": [
                    {"speaker": "דובר 1", "summary": "סיכום", "note": "x"}
                ]
            },
        )
        assert response.status_code == 422

    def test_key_points_can_be_added_edited_and_removed(self, client: TestClient) -> None:
        """The editor's add/edit/remove goes through this endpoint, so the list
        on disk has to be exactly what it last sent."""
        job_id = "job-with-key-points"
        client.app.state.tiktuv.transcripts.save_analysis(
            TranscriptAnalysis(
                job_id=job_id,
                summary="סיכום מקורי",
                key_points=["נקודה אחת"],
                named_entities=[NamedEntity(name="יעל", type="person")],
                model="deepseek-flash",
            )
        )

        edited = client.put(
            f"/api/jobs/{job_id}/analysis/key-points",
            json={"key_points": ["נקודה אחת מתוקנת", "נקודה שנייה"]},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["key_points"] == ["נקודה אחת מתוקנת", "נקודה שנייה"]
        # The rest of the analysis is not the editor's to replace.
        assert edited.json()["summary"] == "סיכום מקורי"
        assert edited.json()["model"] == "deepseek-flash"

        stored = client.get(f"/api/jobs/{job_id}/analysis").json()
        assert stored["key_points"] == ["נקודה אחת מתוקנת", "נקודה שנייה"]
        # A removal is just the list without the dropped point.
        removed = client.put(
            f"/api/jobs/{job_id}/analysis/key-points",
            json={"key_points": []},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["key_points"] == []

    def test_speaker_quotes_can_be_added_edited_and_removed(self, client: TestClient) -> None:
        """The editor's add/edit/remove goes through this endpoint, so the list
        on disk has to be exactly what it last sent."""
        job_id = "job-with-speaker-quotes"
        client.app.state.tiktuv.transcripts.save_analysis(
            TranscriptAnalysis(
                job_id=job_id,
                summary="סיכום מקורי",
                key_points=["נקודה"],
                speaker_quotes=[
                    SpeakerQuote(speaker="דובר 1", quotes=["נתחיל בתקציב."]),
                ],
                model="deepseek-flash",
            )
        )

        edited = client.put(
            f"/api/jobs/{job_id}/analysis/speaker-quotes",
            json={
                "speaker_quotes": [
                    {"speaker": "דובר 1", "quotes": ["נתחיל בתקציב, מיד."]},
                    {"speaker": "דובר 2", "quotes": ["מסכים."]},
                ]
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["speaker_quotes"] == [
            {"speaker": "דובר 1", "quotes": ["נתחיל בתקציב, מיד."]},
            {"speaker": "דובר 2", "quotes": ["מסכים."]},
        ]
        # The rest of the analysis is not the editor's to replace.
        assert edited.json()["summary"] == "סיכום מקורי"
        assert edited.json()["key_points"] == ["נקודה"]
        assert edited.json()["model"] == "deepseek-flash"

        stored = client.get(f"/api/jobs/{job_id}/analysis").json()
        assert stored["speaker_quotes"][1] == {"speaker": "דובר 2", "quotes": ["מסכים."]}

        removed = client.put(
            f"/api/jobs/{job_id}/analysis/speaker-quotes",
            json={"speaker_quotes": []},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["speaker_quotes"] == []

    def test_editing_key_points_without_an_analysis_is_404(self, client: TestClient) -> None:
        response = client.put(
            "/api/jobs/never-analysed/analysis/key-points",
            json={"key_points": ["נקודה"]},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "TRANSCRIPT_NOT_FOUND"

    def test_editing_speaker_quotes_without_an_analysis_is_404(self, client: TestClient) -> None:
        response = client.put(
            "/api/jobs/never-analysed/analysis/speaker-quotes",
            json={"speaker_quotes": [{"speaker": "דובר 1", "quotes": ["ציטוט"]}]},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "TRANSCRIPT_NOT_FOUND"

    def test_summary_can_be_edited(self, client: TestClient) -> None:
        """The editor rewrites the summary through this endpoint, so what is on
        disk has to be exactly what it last sent."""
        job_id = "job-with-summary"
        client.app.state.tiktuv.transcripts.save_analysis(
            TranscriptAnalysis(
                job_id=job_id,
                summary="סיכום מקורי",
                key_points=["נקודה"],
                named_entities=[NamedEntity(name="יעל", type="person")],
                model="deepseek-flash",
            )
        )

        edited = client.put(
            f"/api/jobs/{job_id}/analysis/summary",
            json={"summary": "סיכום מתוקן."},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["summary"] == "סיכום מתוקן."
        # The rest of the analysis is not the editor's to replace.
        assert edited.json()["key_points"] == ["נקודה"]
        assert edited.json()["model"] == "deepseek-flash"

        stored = client.get(f"/api/jobs/{job_id}/analysis").json()
        assert stored["summary"] == "סיכום מתוקן."

    def test_editing_summary_without_an_analysis_is_404(self, client: TestClient) -> None:
        response = client.put(
            "/api/jobs/never-analysed/analysis/summary",
            json={"summary": "סיכום"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "TRANSCRIPT_NOT_FOUND"

    def test_unknown_entity_fields_are_rejected(self, client: TestClient) -> None:
        job_id = "job-strict-entities"
        client.app.state.tiktuv.transcripts.save_analysis(
            TranscriptAnalysis(job_id=job_id)
        )
        response = client.put(
            f"/api/jobs/{job_id}/analysis/named-entities",
            json={"named_entities": [{"name": "יעל", "type": "person", "note": "x"}]},
        )
        assert response.status_code == 422


def test_format_summary_splits_single_block_into_paragraphs():
    from tiktuv.analysis import _format_summary

    text = "משפט אחד. משפט שני. משפט שלישי. משפט רביעי. משפט חמישי."
    out = _format_summary(text)
    assert out.count("\n\n") == 2
    assert out.replace("\n\n", " ") == text


def test_format_summary_keeps_existing_structure():
    from tiktuv.analysis import _format_summary

    text = "פתיחה.\n\n• א\n• ב\n\n\n\nסיום."
    assert _format_summary(text) == "פתיחה.\n\n• א\n• ב\n\nסיום."
