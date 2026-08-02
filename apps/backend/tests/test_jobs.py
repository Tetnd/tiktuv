"""Job pipeline, persistence, cancellation and crash recovery."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tiktuv.asr.registry import ModelManager
from tiktuv.config import Settings
from tiktuv.errors import ErrorCode, JobCancelled, JobError
from tiktuv.jobs.pipeline import STAGE_WEIGHTS, TranscriptionPipeline, import_media_file
from tiktuv.jobs.store import JobStore, TranscriptStore, purge_temp
from tiktuv.media.audio import CancelToken
from tiktuv.media.ffmpeg import FFmpegTools
from tiktuv.media.probe import probe_media
from tiktuv.schemas import (
    ExportFormat,
    JobStage,
    JobStatus,
    Segment,
    TimestampMode,
    Transcript,
    TranscriptionSettings,
)

from .conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


@pytest.fixture
def pipeline(settings: Settings, tools: FFmpegTools) -> TranscriptionPipeline:
    transcripts = TranscriptStore(settings)
    return TranscriptionPipeline(settings, ModelManager(settings), transcripts, tools=tools)


def make_job(
    settings: Settings, tools: FFmpegTools, media: Path, **overrides: object
) -> tuple[JobStore, TranscriptStore, object, Path]:
    from tiktuv.db import init_engine

    init_engine(settings.db_path)
    transcripts = TranscriptStore(settings)
    store = JobStore(settings, transcripts)

    working = import_media_file(media, settings.media_dir / media.name)
    info = probe_media(working, tools, settings)
    job_settings = TranscriptionSettings(**overrides)  # type: ignore[arg-type]
    job = store.create(
        source_path=media, working_path=working, media=info, settings=job_settings
    )
    return store, transcripts, job, working


class _Progress:
    """Records every progress callback for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[JobStage, float, float]] = []

    def __call__(
        self, stage: JobStage, percent: float, processed: float, message: str | None
    ) -> None:
        self.calls.append((stage, percent, processed))

    @property
    def stages(self) -> list[JobStage]:
        seen: list[JobStage] = []
        for stage, _p, _s in self.calls:
            if not seen or seen[-1] is not stage:
                seen.append(stage)
        return seen

    @property
    def percents(self) -> list[float]:
        return [p for _stage, p, _s in self.calls]


# --------------------------------------------------------------------------
class TestStageWeights:
    def test_weights_are_ordered_and_cover_the_full_bar(self) -> None:
        spans = list(STAGE_WEIGHTS.values())
        assert spans[0][0] == 0.0
        assert spans[-1][1] == 1.0
        for (_, end), (next_start, _) in zip(spans, spans[1:], strict=False):
            assert end == next_start


class TestPipelineHappyPath:
    def test_transcribes_an_mp3(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        sample_mp3: Path,
    ) -> None:
        _store, transcripts, job, working = make_job(settings, tools, sample_mp3)
        progress = _Progress()

        result = pipeline.run(job, working, cancel=CancelToken(), on_progress=progress)

        assert result.transcript.segments
        assert not result.transcript.partial
        assert transcripts.exists(job.id)

    def test_extracts_audio_from_an_mp4(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        sample_mp4: Path,
    ) -> None:
        _store, _t, job, working = make_job(settings, tools, sample_mp4)
        result = pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())
        assert result.transcript.segments

    def test_handles_a_hebrew_filename_and_path(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        hebrew_named_mp3: Path,
    ) -> None:
        _store, _t, job, working = make_job(settings, tools, hebrew_named_mp3)
        assert "הקלטה" in job.filename
        result = pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())
        assert result.transcript.segments

    def test_progress_advances_monotonically_to_one(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        sample_mp3: Path,
    ) -> None:
        _s, _t, job, working = make_job(settings, tools, sample_mp3)
        progress = _Progress()
        pipeline.run(job, working, cancel=CancelToken(), on_progress=progress)

        assert progress.percents == sorted(progress.percents)
        assert progress.percents[-1] == pytest.approx(1.0)
        assert all(0.0 <= p <= 1.0 for p in progress.percents)

    def test_reports_the_expected_stages_in_order(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        sample_mp3: Path,
    ) -> None:
        _s, _t, job, working = make_job(settings, tools, sample_mp3)
        progress = _Progress()
        pipeline.run(job, working, cancel=CancelToken(), on_progress=progress)

        stages = progress.stages
        assert stages[0] is JobStage.PREPARING_MEDIA
        assert JobStage.LOADING_MODEL in stages
        assert JobStage.TRANSCRIBING in stages
        assert stages[-1] is JobStage.DONE

    def test_timestamps_are_absolute_and_ordered(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        long_mp3: Path,
    ) -> None:
        _s, _t, job, working = make_job(settings, tools, long_mp3)
        result = pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())

        segments = result.transcript.segments
        assert len(segments) > 1
        assert segments[0].start == pytest.approx(0.0, abs=1.0)
        # The last segment must reach into the second half of a 70 s file,
        # proving later windows were merged rather than dropped.
        assert segments[-1].end > 35.0
        for previous, current in zip(segments, segments[1:], strict=False):
            assert current.start >= previous.start
            assert current.start >= previous.end - 1e-6

    def test_long_recording_produces_multiple_windows_worth_of_text(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        long_mp3: Path,
    ) -> None:
        _s, _t, job, working = make_job(settings, tools, long_mp3)
        result = pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())
        assert result.transcript.duration == pytest.approx(70.0, abs=1.0)
        assert len(result.transcript.segments) >= 5

    def test_word_timestamps_are_produced_when_requested(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        sample_mp3: Path,
    ) -> None:
        _s, _t, job, working = make_job(
            settings, tools, sample_mp3, timestamps=TimestampMode.WORD
        )
        result = pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())
        assert any(segment.words for segment in result.transcript.segments)

    def test_exports_are_written_when_configured(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        sample_mp3: Path,
    ) -> None:
        _s, _t, job, working = make_job(
            settings,
            tools,
            sample_mp3,
            export_formats=[ExportFormat.TXT, ExportFormat.SRT, ExportFormat.JSON],
        )
        result = pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())
        assert set(result.exports) == {"txt", "srt", "json"}
        for path in result.exports.values():
            assert Path(path).is_file()


class TestPartialResultsAndResume:
    def test_partial_transcript_is_flushed_during_the_run(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        long_mp3: Path,
    ) -> None:
        _s, transcripts, job, working = make_job(settings, tools, long_mp3)
        seen_partial: list[bool] = []

        def watcher(stage: JobStage, percent: float, processed: float, msg: str | None) -> None:
            if stage is JobStage.TRANSCRIBING:
                loaded = transcripts.load(job.id)
                if loaded is not None:
                    seen_partial.append(loaded.partial)

        pipeline.run(job, working, cancel=CancelToken(), on_progress=watcher)

        assert any(seen_partial), "no partial snapshot was written while transcribing"
        final = transcripts.load(job.id)
        assert final is not None and not final.partial

    def test_cancelled_job_leaves_a_usable_partial_transcript(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        long_mp3: Path,
    ) -> None:
        _s, transcripts, job, working = make_job(settings, tools, long_mp3)
        token = CancelToken()

        def cancel_midway(
            stage: JobStage, percent: float, processed: float, msg: str | None
        ) -> None:
            if stage is JobStage.TRANSCRIBING and processed >= 20.0:
                token.cancel()

        with pytest.raises(JobCancelled):
            pipeline.run(job, working, cancel=token, on_progress=cancel_midway)

        partial = transcripts.load(job.id)
        assert partial is not None
        assert partial.partial
        assert partial.segments

    def test_resume_keeps_earlier_text_and_continues(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        long_mp3: Path,
    ) -> None:
        _s, transcripts, job, working = make_job(settings, tools, long_mp3)

        # Simulate a crash: a partial transcript covering the first 20 seconds.
        transcripts.save(
            Transcript(
                job_id=job.id,
                language="he",
                model_id="tiktuv/mock-hebrew",
                duration=70.0,
                partial=True,
                segments=[
                    Segment(id=0, start=0.0, end=10.0, text="טקסט שנשמר לפני הקריסה"),
                    Segment(id=1, start=10.0, end=20.0, text="עוד טקסט שנשמר"),
                ],
            )
        )

        result = pipeline.run(
            job, working, cancel=CancelToken(), on_progress=_Progress(), resume_from=20.0
        )

        texts = [segment.text for segment in result.transcript.segments]
        assert "טקסט שנשמר לפני הקריסה" in texts
        assert result.transcript.segments[-1].end > 40.0
        assert not result.transcript.partial

    def test_resume_from_zero_is_a_normal_run(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        sample_mp3: Path,
    ) -> None:
        _s, _t, job, working = make_job(settings, tools, sample_mp3)
        result = pipeline.run(
            job, working, cancel=CancelToken(), on_progress=_Progress(), resume_from=0.0
        )
        assert result.transcript.segments


class TestCancellation:
    def test_cancel_before_start_raises_immediately(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        sample_mp3: Path,
    ) -> None:
        _s, _t, job, working = make_job(settings, tools, sample_mp3)
        token = CancelToken()
        token.cancel()
        with pytest.raises(JobCancelled):
            pipeline.run(job, working, cancel=token, on_progress=_Progress())

    def test_cancel_token_is_thread_safe(self) -> None:
        token = CancelToken()
        assert not token.cancelled
        threading.Thread(target=token.cancel).start()
        for _ in range(100):
            if token.cancelled:
                break
            time.sleep(0.01)
        assert token.cancelled


class TestErrorHandling:
    def test_missing_media_file_is_reported_clearly(
        self, settings: Settings, tools: FFmpegTools, pipeline: TranscriptionPipeline,
        sample_mp3: Path,
    ) -> None:
        from tiktuv.errors import TiktuvError

        _s, _t, job, working = make_job(settings, tools, sample_mp3)
        working.unlink()
        with pytest.raises(TiktuvError) as info:
            pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())
        assert info.value.code is ErrorCode.MEDIA_CORRUPT
        assert info.value.message_he
        assert info.value.remedy_he

    def test_insufficient_disk_is_detected(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path
    ) -> None:
        from tiktuv.errors import TiktuvError

        strict = settings.model_copy(update={"min_free_disk_bytes": 1 << 62})
        _s, transcripts, job, working = make_job(settings, tools, sample_mp3)
        strict_pipeline = TranscriptionPipeline(
            strict, ModelManager(strict), transcripts, tools=tools
        )
        with pytest.raises(TiktuvError) as info:
            strict_pipeline.run(job, working, cancel=CancelToken(), on_progress=_Progress())
        assert info.value.code is ErrorCode.INSUFFICIENT_DISK


class TestJobStore:
    def test_create_and_read_back(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path
    ) -> None:
        store, _t, job, _w = make_job(settings, tools, sample_mp3)
        loaded = store.get(job.id)
        assert loaded.id == job.id
        assert loaded.status is JobStatus.QUEUED
        assert loaded.duration == pytest.approx(6.0, abs=0.5)

    def test_original_file_is_not_modified(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path
    ) -> None:
        before = sample_mp3.read_bytes()
        make_job(settings, tools, sample_mp3)
        assert sample_mp3.read_bytes() == before

    def test_status_transitions(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path
    ) -> None:
        store, _t, job, _w = make_job(settings, tools, sample_mp3)
        store.mark_running(job.id)
        assert store.get(job.id).status is JobStatus.RUNNING
        store.mark_completed(job.id, {"txt": "a.txt"})
        completed = store.get(job.id)
        assert completed.status is JobStatus.COMPLETED
        assert completed.progress.percent == 1.0
        assert completed.output_paths == {"txt": "a.txt"}

    def test_unknown_job_raises_not_found(self, settings: Settings) -> None:
        from tiktuv.db import init_engine

        init_engine(settings.db_path)
        store = JobStore(settings, TranscriptStore(settings))
        with pytest.raises(JobError) as info:
            store.get("does-not-exist")
        assert info.value.code is ErrorCode.JOB_NOT_FOUND

    def test_retry_resets_state_and_keeps_progress_when_resuming(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path
    ) -> None:
        store, _t, job, _w = make_job(settings, tools, sample_mp3)
        store.update_progress(
            job.id, stage=JobStage.TRANSCRIBING, percent=0.5, processed_seconds=3.0,
            resume_offset=3.0,
        )
        store.mark_cancelled(job.id)

        store.requeue(job.id, reset_progress=False)
        assert store.resume_offset(job.id) == pytest.approx(3.0)

        store.requeue(job.id, reset_progress=True)
        assert store.resume_offset(job.id) == 0.0

    def test_delete_removes_transcript_and_working_copy(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path
    ) -> None:
        store, transcripts, job, working = make_job(settings, tools, sample_mp3)
        transcripts.save(
            Transcript(job_id=job.id, language="he", model_id="m", duration=1.0, segments=[])
        )
        store.delete(job.id, delete_files=False)
        assert not transcripts.exists(job.id)
        assert not working.exists()

    def test_delete_outputs_keeps_the_history_entry(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path, tmp_path: Path
    ) -> None:
        store, _t, job, _w = make_job(settings, tools, sample_mp3)
        export = tmp_path / "out.txt"
        export.write_text("x", encoding="utf-8")
        store.set_outputs(job.id, {"txt": str(export)})

        assert store.delete_outputs(job.id) == 1
        assert not export.exists()
        assert store.get(job.id).output_paths == {}

    def test_clear_finished_removes_only_terminal_jobs(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path, sample_mp4: Path
    ) -> None:
        store, _t, done, _w = make_job(settings, tools, sample_mp3)
        _s2, _t2, queued, _w2 = make_job(settings, tools, sample_mp4)
        store.mark_completed(done.id)

        assert store.clear_finished() == 1
        remaining = [job.id for job in store.list_jobs()]
        assert remaining == [queued.id]

    def test_recover_orphans_marks_running_jobs_as_failed(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path
    ) -> None:
        store, _t, job, _w = make_job(settings, tools, sample_mp3)
        store.mark_running(job.id)
        store.update_progress(
            job.id, stage=JobStage.TRANSCRIBING, percent=0.4, processed_seconds=2.4,
            resume_offset=2.4,
        )

        recovered = store.recover_orphans()
        assert recovered == [job.id]

        after = store.get(job.id)
        assert after.status is JobStatus.FAILED
        assert after.error is not None
        assert "נסה שוב" in after.error.remedy
        # The resume point survives, so the retry does not start over.
        assert store.resume_offset(job.id) == pytest.approx(2.4)

    def test_touch_opened_records_the_time(
        self, settings: Settings, tools: FFmpegTools, sample_mp3: Path
    ) -> None:
        store, _t, job, _w = make_job(settings, tools, sample_mp3)
        assert store.get(job.id).last_opened_at is None
        store.touch_opened(job.id)
        assert store.get(job.id).last_opened_at is not None


class TestTranscriptStore:
    def test_save_and_load_round_trip(self, settings: Settings) -> None:
        store = TranscriptStore(settings)
        transcript = Transcript(
            job_id="abc",
            language="he",
            model_id="m",
            duration=5.0,
            segments=[Segment(id=0, start=0.0, end=5.0, text="שלום עולם")],
        )
        store.save(transcript)
        loaded = store.load("abc")
        assert loaded is not None
        assert loaded.segments[0].text == "שלום עולם"

    def test_load_missing_returns_none(self, settings: Settings) -> None:
        assert TranscriptStore(settings).load("nope") is None

    def test_corrupt_file_returns_none_rather_than_raising(self, settings: Settings) -> None:
        store = TranscriptStore(settings)
        store.path_for("bad").write_text("{not json", encoding="utf-8")
        assert store.load("bad") is None

    def test_save_is_atomic_leaving_no_temp_files(self, settings: Settings) -> None:
        store = TranscriptStore(settings)
        store.save(Transcript(job_id="a", language="he", model_id="m", duration=1.0))
        assert list(settings.transcripts_dir.glob("*.tmp")) == []

    def test_overwrite_replaces_content(self, settings: Settings) -> None:
        store = TranscriptStore(settings)
        store.save(Transcript(job_id="a", language="he", model_id="m", duration=1.0))
        store.save(
            Transcript(
                job_id="a",
                language="he",
                model_id="m",
                duration=2.0,
                segments=[Segment(id=0, start=0, end=1, text="חדש")],
            )
        )
        loaded = store.load("a")
        assert loaded is not None and loaded.duration == 2.0


class TestTempCleanup:
    def test_purge_temp_empties_the_directory(self, settings: Settings) -> None:
        (settings.temp_dir / "junk.bin").write_bytes(b"x" * 1024)
        (settings.temp_dir / "sub").mkdir(exist_ok=True)
        (settings.temp_dir / "sub" / "more.bin").write_bytes(b"y" * 512)

        reclaimed = purge_temp(settings)
        assert reclaimed >= 1536
        assert list(settings.temp_dir.iterdir()) == []
        assert settings.temp_dir.is_dir()
