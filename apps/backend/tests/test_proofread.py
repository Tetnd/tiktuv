"""DeepSeek proofreading: batching, correction validation, and the pipeline stage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tiktuv.asr.registry import ModelManager
from tiktuv.config import Settings
from tiktuv.credentials import DeepSeekCredentials
from tiktuv.errors import ModelError
from tiktuv.jobs.pipeline import TranscriptionPipeline
from tiktuv.jobs.store import TranscriptStore
from tiktuv.media.audio import CancelToken
from tiktuv.media.ffmpeg import FFmpegTools
from tiktuv.proofread import (
    BATCH_SEGMENTS,
    _batches,
    apply_correction,
    build_batch_payload,
    parse_corrections,
    proofread_segments,
)
from tiktuv.schemas import JobStage, JobStatus, Segment, Word

from .conftest import requires_ffmpeg
from .test_api import import_media, wait_for_status
from .test_jobs import _Progress, make_job


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tiktuv.proofread._RETRY_DELAY_SECONDS", 0.0)


def _segments() -> list[Segment]:
    return [
        Segment(id=0, start=0.0, end=3.0, text="שלום, אני רוצה לדבר על התקציב", speaker="1"),
        Segment(id=1, start=3.0, end=6.0, text="בטח, אני מקשיב", speaker="2"),
    ]


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _reply(corrections: list[dict]) -> _FakeResponse:
    content = json.dumps({"corrections": corrections}, ensure_ascii=False)
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


# ---------------------------------------------------------------- batching
def test_batches_split_on_segment_count() -> None:
    segments = [Segment(id=i, start=i, end=i + 1, text="מילה") for i in range(BATCH_SEGMENTS + 5)]
    batches = list(_batches(segments))
    assert [len(batch) for batch in batches] == [BATCH_SEGMENTS, 5]


def test_batch_payload_carries_ids_and_speaker_labels() -> None:
    payload = json.loads(build_batch_payload(_segments(), {"1": "דובר 1", "2": "דובר 2"}))
    assert [item["id"] for item in payload["segments"]] == [0, 1]
    assert payload["segments"][0]["speaker"] == "דובר 1"
    assert payload["segments"][0]["text"] == "שלום, אני רוצה לדבר על התקציב"


# ------------------------------------------------------- correction parsing
def test_parse_corrections_accepts_a_fix() -> None:
    batch = _segments()
    content = '{"corrections": [{"id": 1, "text": "בטח, אני מקשיב."}]}'
    assert parse_corrections(content, batch) == {1: "בטח, אני מקשיב."}


def test_parse_corrections_rejects_a_summary() -> None:
    """A reply that shortens a segment beyond recognition is a rewrite, not a fix."""
    batch = _segments()
    content = '{"corrections": [{"id": 0, "text": "תקציב"}]}'
    assert parse_corrections(content, batch) == {}


def test_parse_corrections_rejects_padding_and_unknown_ids() -> None:
    batch = _segments()
    content = json.dumps(
        {
            "corrections": [
                # An id we never sent.
                {"id": 99, "text": "משהו אחר לגמרי"},
                # Three times the length: the model added content.
                {"id": 1, "text": "בטח, אני מקשיב " * 6},
                # Empty.
                {"id": 0, "text": "   "},
                # Not an object.
                "nonsense",
            ]
        },
        ensure_ascii=False,
    )
    assert parse_corrections(content, batch) == {}


def test_parse_corrections_ignores_unchanged_text() -> None:
    batch = _segments()
    content = json.dumps({"corrections": [{"id": 0, "text": batch[0].text}]}, ensure_ascii=False)
    assert parse_corrections(content, batch) == {}


def test_parse_corrections_survives_a_non_json_reply() -> None:
    assert parse_corrections("I'm sorry, I cannot do that.", _segments()) == {}


# ------------------------------------------------------- applying a fix
def test_apply_correction_keeps_timings_and_records_the_original() -> None:
    segment = Segment(
        id=0,
        start=1.0,
        end=4.0,
        text="אין לי",
        words=[Word(text="אין", start=1.0, end=2.0), Word(text="לי", start=2.0, end=4.0)],
        speaker="1",
    )
    fixed = apply_correction(segment, "אן לי")
    assert (fixed.start, fixed.end, fixed.id, fixed.speaker) == (1.0, 4.0, 0, "1")
    assert fixed.text == "אן לי"
    assert fixed.original_text == "אין לי"
    assert fixed.proofread is True
    # Same word count, so the per-word timings still line up.
    assert [(w.text, w.start) for w in fixed.words] == [("אן", 1.0), ("לי", 2.0)]


def test_apply_correction_drops_word_timings_when_the_word_count_changes() -> None:
    segment = Segment(
        id=0,
        start=0.0,
        end=2.0,
        text="בוקרטוב",
        words=[Word(text="בוקרטוב", start=0.0, end=2.0)],
    )
    fixed = apply_correction(segment, "בוקר טוב")
    assert fixed.words == []
    assert fixed.text == "בוקר טוב"


# ------------------------------------------------------- end to end
def test_proofread_segments_applies_corrections(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict] = []

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        sent.append(kwargs["json"])  # type: ignore[arg-type]
        return _reply([{"id": 1, "text": "בטח, אני מקשיב."}])

    monkeypatch.setattr("tiktuv.proofread.httpx.post", fake_post)

    segments, changed = proofread_segments(
        "key", _segments(), {"1": "דובר 1", "2": "דובר 2"}, vocabulary="תקציב"
    )

    assert changed == 1
    assert segments[0].text == "שלום, אני רוצה לדבר על התקציב"
    assert segments[0].proofread is False
    assert segments[1].text == "בטח, אני מקשיב."
    assert segments[1].original_text == "בטח, אני מקשיב"
    # The vocabulary hints ride along with the batch.
    assert "תקציב" in sent[0]["messages"][1]["content"]
    assert sent[0]["temperature"] == 0.0


def test_proofread_segments_reports_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tiktuv.proofread.httpx.post", lambda url, **kw: _reply([]))
    seen: list[tuple[int, int]] = []
    proofread_segments(
        "key", _segments(), on_progress=lambda done, total: seen.append((done, total))
    )
    assert seen == [(2, 2)]


def test_proofread_segments_needs_a_key() -> None:
    with pytest.raises(ModelError):
        proofread_segments("  ", _segments())


def test_proofread_segments_raises_on_an_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tiktuv.proofread.httpx.post", lambda url, **kw: _FakeResponse(402, {}))
    with pytest.raises(ModelError):
        proofread_segments("key", _segments())


def test_proofread_segments_stops_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        calls["n"] += 1
        return _reply([])

    monkeypatch.setattr("tiktuv.proofread.httpx.post", fake_post)
    segments, changed = proofread_segments(
        "key", _segments(), should_cancel=lambda: True
    )
    assert calls["n"] == 0
    assert changed == 0
    assert segments[0].text == "שלום, אני רוצה לדבר על התקציב"


def test_proofread_segments_skips_an_empty_transcript() -> None:
    segments, changed = proofread_segments("key", [])
    assert (segments, changed) == ([], 0)


# ------------------------------------------------------- the pipeline stage
@requires_ffmpeg
class TestPipelineStage:
    """The stage runs inside a real job, and never costs the user a transcript."""

    def _pipeline(
        self,
        settings: Settings,
        tools: FFmpegTools,
        *,
        key: str | None,
    ) -> TranscriptionPipeline:
        deepseek = DeepSeekCredentials(settings)
        if key:
            deepseek.set(key)
        return TranscriptionPipeline(
            settings,
            ModelManager(settings),
            TranscriptStore(settings),
            tools=tools,
            deepseek=deepseek,
        )

    def test_corrects_the_transcript_and_records_the_stage(
        self,
        settings: Settings,
        tools: FFmpegTools,
        sample_mp3: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fake_post(url: str, **kwargs: object) -> _FakeResponse:
            sent = json.loads(kwargs["json"]["messages"][1]["content"])  # type: ignore[index]
            return _reply(
                [{"id": item["id"], "text": f"{item['text']}."} for item in sent["segments"]]
            )

        monkeypatch.setattr("tiktuv.proofread.httpx.post", fake_post)

        _store, _t, job, working = make_job(
            settings, tools, sample_mp3, enable_diarization=False, enable_analysis=False
        )
        progress = _Progress()
        result = self._pipeline(settings, tools, key="k").run(
            job, working, cancel=CancelToken(), on_progress=progress
        )

        assert JobStage.PROOFREADING in progress.stages
        assert result.transcript.proofread_corrections == len(result.transcript.segments)
        assert result.transcript.proofread_model
        for segment in result.transcript.segments:
            assert segment.proofread is True
            assert segment.text == f"{segment.original_text}."

    def test_skips_the_stage_without_a_key(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path
    ) -> None:
        _store, _t, job, working = make_job(
            settings, tools, sample_mp3, enable_diarization=False, enable_analysis=False
        )
        result = self._pipeline(settings, tools, key=None).run(
            job, working, cancel=CancelToken(), on_progress=_Progress()
        )
        assert result.transcript.segments
        assert result.transcript.proofread_corrections == 0
        assert all(not segment.proofread for segment in result.transcript.segments)

    def test_a_deepseek_failure_does_not_fail_the_job(
        self,
        settings: Settings,
        tools: FFmpegTools,
        sample_mp3: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "tiktuv.proofread.httpx.post",
            lambda url, **kw: _FakeResponse(500, {}),
        )
        _store, _t, job, working = make_job(
            settings, tools, sample_mp3, enable_diarization=False, enable_analysis=False
        )
        result = self._pipeline(settings, tools, key="k").run(
            job, working, cancel=CancelToken(), on_progress=_Progress()
        )
        assert result.transcript.segments
        assert result.transcript.proofread_corrections == 0

    def test_the_stage_can_be_turned_off(
        self,
        settings: Settings,
        tools: FFmpegTools,
        sample_mp3: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = {"n": 0}

        def fake_post(url: str, **kwargs: object) -> _FakeResponse:
            called["n"] += 1
            return _reply([])

        monkeypatch.setattr("tiktuv.proofread.httpx.post", fake_post)
        _store, _t, job, working = make_job(
            settings,
            tools,
            sample_mp3,
            enable_proofreading=False,
            enable_diarization=False,
            enable_analysis=False,
        )
        result = self._pipeline(settings, tools, key="k").run(
            job, working, cancel=CancelToken(), on_progress=_Progress()
        )
        assert called["n"] == 0
        assert result.transcript.segments


# ------------------------------------------------------- the on-demand route
@requires_ffmpeg
@pytest.mark.usefixtures("client")
class TestProofreadApi:
    def test_proofread_route_corrects_a_stored_transcript(
        self, client: TestClient, sample_mp3: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = import_media(client, sample_mp3)
        job_id = client.post(
            "/api/jobs",
            json={"media_token": token, "settings": {"enable_proofreading": False}},
        ).json()["id"]
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)

        # Without a key the user gets the same clear error as the analysis tab.
        response = client.post(f"/api/jobs/{job_id}/proofread")
        assert response.status_code == 422
        assert response.json()["code"] == "DEEPSEEK_KEY_MISSING"

        client.put("/api/deepseek/key", json={"api_key": "sk-deepseek-test"})

        def fake_post(url: str, **kwargs: object) -> _FakeResponse:
            sent = json.loads(kwargs["json"]["messages"][1]["content"])  # type: ignore[index]
            return _reply(
                [{"id": item["id"], "text": f"{item['text']}."} for item in sent["segments"]]
            )

        monkeypatch.setattr("tiktuv.proofread.httpx.post", fake_post)

        corrected = client.post(f"/api/jobs/{job_id}/proofread")
        assert corrected.status_code == 200
        body = corrected.json()
        assert body["proofread_corrections"] == len(body["segments"])
        assert all(segment["proofread"] for segment in body["segments"])

        # The correction is what a later read of the transcript returns.
        stored = client.get(f"/api/jobs/{job_id}/transcript").json()
        assert stored["segments"] == body["segments"]

    def test_proofread_route_404s_without_a_transcript(self, client: TestClient) -> None:
        assert client.post("/api/jobs/does-not-exist/proofread").status_code == 404
