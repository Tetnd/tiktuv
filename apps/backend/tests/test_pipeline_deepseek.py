"""A job is not done until every DeepSeek step has run.

Transcription, then speakers (and their names), proofreading and the AI
analysis — all inside the one job, so the job only completes once all of it has.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tiktuv.asr.registry import ModelManager
from tiktuv.config import Settings
from tiktuv.credentials import DeepSeekCredentials
from tiktuv.jobs.pipeline import TranscriptionPipeline
from tiktuv.jobs.store import TranscriptStore
from tiktuv.media.audio import CancelToken
from tiktuv.media.ffmpeg import FFmpegTools
from tiktuv.schemas import JobStage

from .conftest import requires_ffmpeg
from .test_jobs import _Progress, make_job

pytestmark = requires_ffmpeg


class _Response:
    status_code = 200

    def __init__(self, content: object) -> None:
        self._content = content

    def json(self) -> dict:
        content = json.dumps(self._content, ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}]}


class FakeDeepSeek:
    """Answers each kind of request by what its system prompt is about."""

    def __init__(self, *, speakers: int = 2, analysis_fails: bool = False) -> None:
        self.speakers = speakers
        self.analysis_fails = analysis_fails
        self.asked: list[str] = []

    def __call__(self, url: str, **kwargs: object) -> _Response:
        messages = kwargs["json"]["messages"]  # type: ignore[index]
        system, user = messages[0]["content"], messages[1]["content"]
        if "מגיה מקצועי" in system:
            self.asked.append("proofread")
            return _Response({"corrections": []})
        if "מומחה בניתוח שיחות" in system:
            self.asked.append("speakers")
            body = json.loads(user)
            return _Response({"labels": [
                {"id": s["id"], "speaker": str(s["id"] % self.speakers + 1)}
                for s in body["segments"]
            ]})
        if "השמות האמיתיים" in system:
            self.asked.append("names")
            return _Response({"names": []})
        self.asked.append("analysis")
        if self.analysis_fails:
            raise AssertionError("boom")
        return _Response({
            "summary": "סיכום קצר של ההקלטה.",
            "key_points": ["נקודה ראשונה"],
            "action_items": [],
            "named_entities": [],
            "speaker_summaries": [],
            "speaker_quotes": [],
        })


def _pipeline(settings: Settings, tools: FFmpegTools, *, key: str | None) -> TranscriptionPipeline:
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


def test_one_click_runs_every_step_and_finishes_last(
    settings: Settings, tools: FFmpegTools, long_mp3: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDeepSeek(speakers=2)
    monkeypatch.setattr("tiktuv.proofread.httpx.post", fake)
    _s, transcripts, job, working = make_job(settings, tools, long_mp3)  # all defaults
    progress = _Progress()
    pipeline = _pipeline(settings, tools, key="k")
    pipeline._transcripts = transcripts  # share the store the job was written to

    result = pipeline.run(job, working, cancel=CancelToken(), on_progress=progress)

    # Every DeepSeek step was asked for, in the order they build on each other.
    assert fake.asked[0] == "speakers"
    assert fake.asked.index("proofread") < fake.asked.index("analysis")
    assert fake.asked[-1] == "analysis"
    stages = progress.stages
    for stage in (JobStage.DIARIZING, JobStage.PROOFREADING, JobStage.ANALYZING):
        assert stage in stages
    assert stages.index(JobStage.ANALYZING) > stages.index(JobStage.PROOFREADING)
    assert stages[-1] is JobStage.DONE

    # And the results are all there when the job ends.
    assert {s.speaker for s in result.transcript.segments} == {"1", "2"}
    assert result.transcript.speakers["1"] == "דובר 1"
    analysis = transcripts.load_analysis(job.id)
    assert analysis is not None and analysis.summary == "סיכום קצר של ההקלטה."


def test_a_lecture_with_one_speaker_gets_no_speaker_labels(
    settings: Settings, tools: FFmpegTools, sample_mp3: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tiktuv.proofread.httpx.post", FakeDeepSeek(speakers=1))
    _s, transcripts, job, working = make_job(settings, tools, sample_mp3)
    pipeline = _pipeline(settings, tools, key="k")
    pipeline._transcripts = transcripts
    result = pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())
    assert all(s.speaker is None for s in result.transcript.segments)
    assert result.transcript.speakers == {}


def test_a_failing_analysis_never_loses_the_transcript(
    settings: Settings, tools: FFmpegTools, sample_mp3: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down(FakeDeepSeek):
        def __call__(self, url: str, **kwargs: object) -> _Response:
            if "תקציר" in kwargs["json"]["messages"][0]["content"]:  # type: ignore[index]
                raise OSError("network down")
            return super().__call__(url, **kwargs)

    monkeypatch.setattr("tiktuv.proofread.httpx.post", Down())
    _s, transcripts, job, working = make_job(settings, tools, sample_mp3)
    pipeline = _pipeline(settings, tools, key="k")
    pipeline._transcripts = transcripts
    progress = _Progress()
    result = pipeline.run(job, working, cancel=CancelToken(), on_progress=progress)
    assert result.transcript.segments
    assert progress.stages[-1] is JobStage.DONE
    assert transcripts.load_analysis(job.id) is None


def test_without_a_key_the_job_still_completes(
    settings: Settings, tools: FFmpegTools, sample_mp3: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDeepSeek()
    monkeypatch.setattr("tiktuv.proofread.httpx.post", fake)
    _s, transcripts, job, working = make_job(settings, tools, sample_mp3)
    pipeline = _pipeline(settings, tools, key=None)
    pipeline._transcripts = transcripts
    progress = _Progress()
    result = pipeline.run(job, working, cancel=CancelToken(), on_progress=progress)
    assert fake.asked == []  # nothing was sent anywhere
    assert result.transcript.segments
    assert progress.stages[-1] is JobStage.DONE


def test_analysis_can_be_switched_off(
    settings: Settings, tools: FFmpegTools, sample_mp3: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDeepSeek()
    monkeypatch.setattr("tiktuv.proofread.httpx.post", fake)
    _s, transcripts, job, working = make_job(settings, tools, sample_mp3, enable_analysis=False)
    pipeline = _pipeline(settings, tools, key="k")
    pipeline._transcripts = transcripts
    pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())
    assert "analysis" not in fake.asked
