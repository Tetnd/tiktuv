"""The transcription pipeline.

Runs one job from media to exports, reporting progress at every stage and
checking for cancellation between windows. Written as a plain synchronous
function so it can be unit-tested directly, without a queue or an event loop.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..asr.base import TranscribeRequest
from ..asr.chunking import TranscriptAccumulator, shift_segments
from ..asr.downloader import ModelDownloader
from ..asr.registry import ModelManager, is_downloaded, use_mock_backend
from ..config import Settings
from ..diarization import assign_speakers, build_speaker_map, get_backend
from ..errors import ErrorCode, JobCancelled, ModelError, ResourceError, TiktuvError
from ..exports import export_transcript
from ..logging_conf import get_logger
from ..media.audio import AudioArray, CancelToken, estimate_window_count, iter_windows
from ..media.ffmpeg import FFmpegTools, locate_tools
from ..schemas import (
    ExportFormat,
    JobDetail,
    JobStage,
    Segment,
    Transcript,
    TranscriptionSettings,
)
from ..security import validate_output_dir
from ..text.hebrew import post_process_segments
from .store import TranscriptStore, free_disk_bytes

log = get_logger("jobs.pipeline")

#: Fraction of the overall progress bar each stage owns.
STAGE_WEIGHTS: dict[JobStage, tuple[float, float]] = {
    JobStage.PREPARING_MEDIA: (0.00, 0.02),
    # A missing model is fetched here. When it is already present this stage
    # is instantaneous, so it costs the progress bar almost nothing.
    JobStage.DOWNLOADING_MODEL: (0.02, 0.03),
    JobStage.LOADING_MODEL: (0.03, 0.10),
    JobStage.TRANSCRIBING: (0.10, 0.88),
    JobStage.DIARIZING: (0.88, 0.92),
    JobStage.GENERATING_SUBTITLES: (0.92, 0.94),
    JobStage.EXPORTING: (0.94, 1.00),
}

#: Flush a partial transcript at least this often, so a crash costs at most
#: one window's worth of work.
_PARTIAL_FLUSH_EVERY_WINDOWS = 1

ProgressCallback = Callable[[JobStage, float, float, str | None], None]


def stage_progress(stage: JobStage, fraction: float) -> float:
    """Map a within-stage fraction onto the overall 0–1 progress bar."""
    start, end = STAGE_WEIGHTS.get(stage, (0.0, 1.0))
    return start + (end - start) * max(0.0, min(1.0, fraction))


@dataclass(frozen=True, slots=True)
class PipelineResult:
    transcript: Transcript
    exports: dict[str, str]


class TranscriptionPipeline:
    def __init__(
        self,
        settings: Settings,
        model_manager: ModelManager,
        transcripts: TranscriptStore,
        *,
        tools: FFmpegTools | None = None,
        downloader: ModelDownloader | None = None,
    ) -> None:
        self._settings = settings
        self._models = model_manager
        self._transcripts = transcripts
        self._tools = tools
        self._downloader = downloader

    def _ensure_tools(self) -> FFmpegTools:
        if self._tools is None:
            self._tools = locate_tools(self._settings)
        return self._tools

    # ---------------------------------------------------------------- run
    def run(
        self,
        job: JobDetail,
        media_path: Path,
        *,
        cancel: CancelToken,
        on_progress: ProgressCallback,
        resume_from: float = 0.0,
    ) -> PipelineResult:
        settings = job.settings
        started = datetime.now(UTC)

        # -- 1. prepare media ------------------------------------------
        on_progress(
            JobStage.PREPARING_MEDIA, stage_progress(JobStage.PREPARING_MEDIA, 0.1), 0.0, None
        )
        tools = self._ensure_tools()
        self._check_disk(media_path)
        cancel.raise_if_cancelled()
        on_progress(
            JobStage.PREPARING_MEDIA, stage_progress(JobStage.PREPARING_MEDIA, 1.0), 0.0, None
        )

        # -- 2. fetch the model if it is missing -------------------------
        self._ensure_model(settings, cancel=cancel, on_progress=on_progress)

        # -- 3. load model -----------------------------------------------
        on_progress(JobStage.LOADING_MODEL, stage_progress(JobStage.LOADING_MODEL, 0.1), 0.0, None)
        transcriber = self._models.acquire(settings)
        cancel.raise_if_cancelled()
        on_progress(JobStage.LOADING_MODEL, stage_progress(JobStage.LOADING_MODEL, 1.0), 0.0, None)

        # -- 4. transcribe ----------------------------------------------
        duration = job.duration
        accumulator = TranscriptAccumulator(overlap_seconds=self._settings.window_overlap_seconds)

        # Anything already committed before the crash is reloaded, so a resumed
        # job keeps its earlier text instead of transcribing it twice.
        if resume_from > 0:
            existing = self._transcripts.load(job.id)
            if existing is not None:
                kept = [s for s in existing.segments if s.end <= resume_from + 0.001]
                if kept:
                    accumulator.add_window(kept, window_start=0.0, overlap_lead=0.0)
                    log.info(
                        "resuming job",
                        extra={"job_id": job.id, "from": round(resume_from, 2), "kept": len(kept)},
                    )
                else:
                    resume_from = 0.0
            else:
                resume_from = 0.0

        total_windows = max(
            1,
            estimate_window_count(
                duration,
                self._settings.window_seconds,
                self._settings.window_overlap_seconds,
                resume_from,
            ),
        )
        language = settings.language.value
        previous_text = ""
        processed = resume_from

        for window in iter_windows(
            media_path,
            tools,
            sample_rate=self._settings.sample_rate,
            window_seconds=self._settings.window_seconds,
            overlap_seconds=self._settings.window_overlap_seconds,
            start_at=resume_from,
            total_duration=duration,
            cancel=cancel,
        ):
            cancel.raise_if_cancelled()

            result = transcriber.transcribe(
                TranscribeRequest(
                    samples=window.samples,
                    sample_rate=window.sample_rate,
                    settings=settings,
                    previous_text=previous_text,
                )
            )
            language = result.language

            # Window-relative timestamps become absolute here; everything
            # downstream works on the source timeline.
            absolute = shift_segments(result.segments, window.start)
            accumulator.add_window(
                absolute, window_start=window.start, overlap_lead=window.overlap_lead
            )

            if absolute:
                previous_text = absolute[-1].text

            processed = min(duration, window.end)
            fraction = (window.index + 1) / total_windows
            on_progress(
                JobStage.TRANSCRIBING,
                stage_progress(JobStage.TRANSCRIBING, fraction),
                processed,
                None,
            )

            if (window.index + 1) % _PARTIAL_FLUSH_EVERY_WINDOWS == 0:
                self._transcripts.save_partial(
                    job.id,
                    accumulator.segments(),
                    language=language,
                    model_id=transcriber.model_id,
                    duration=duration,
                )

        cancel.raise_if_cancelled()
        segments = accumulator.segments()

        # -- 5. text post-processing -------------------------------------
        segments = post_process_segments(
            segments,
            preserve_fillers=settings.preserve_filler_words,
            normalize=settings.normalize_punctuation,
        )

        # -- 6. optional diarization -------------------------------------
        speakers: dict[str, str] = {}
        if settings.enable_diarization:
            segments, speakers = self._diarize(
                job, media_path, tools, segments, cancel=cancel, on_progress=on_progress
            )

        transcript = Transcript(
            job_id=job.id,
            language=language,
            model_id=transcriber.model_id,
            duration=duration,
            segments=segments,
            speakers=speakers,
            partial=False,
            created_at=started,
        )
        self._transcripts.save(transcript)

        # -- 7. exports ---------------------------------------------------
        exports = self._export(job, transcript, settings, on_progress=on_progress)

        on_progress(JobStage.DONE, 1.0, duration, None)
        return PipelineResult(transcript=transcript, exports=exports)

    # ------------------------------------------------------------- stages
    def _ensure_model(
        self,
        settings: TranscriptionSettings,
        *,
        cancel: CancelToken,
        on_progress: ProgressCallback,
    ) -> None:
        """Download the model on demand so a first run just works.

        Without this, a fresh install fails the first job with
        MODEL_NOT_DOWNLOADED and sends the user hunting through settings. The
        download is still announced — the UI shows size and destination, and
        reports progress through this stage — it just does not need a click.
        """
        if use_mock_backend():
            # The deterministic test backend has no weights to fetch. Without
            # this the whole test suite would try to reach the network.
            return
        if is_downloaded(settings.model_id, self._settings.model_cache_dir):
            return
        if not settings.auto_download_model or self._downloader is None:
            raise ModelError(
                f"model {settings.model_id!r} has not been downloaded yet",
                code=ErrorCode.MODEL_NOT_DOWNLOADED,
                context={"model_id": settings.model_id},
            )

        log.info("fetching a missing model before transcription",
                 extra={"model_id": settings.model_id})
        self._downloader.start(settings.model_id)

        while self._downloader.is_running:
            if cancel.cancelled:
                self._downloader.cancel()
                raise JobCancelled("cancelled while downloading the model")
            status = self._downloader.status()
            on_progress(
                JobStage.DOWNLOADING_MODEL,
                stage_progress(JobStage.DOWNLOADING_MODEL, status.percent),
                0.0,
                f"מוריד את המודל — {int(status.percent * 100)}%",
            )
            time.sleep(0.5)

        status = self._downloader.status()
        if not is_downloaded(settings.model_id, self._settings.model_cache_dir):
            raise ModelError(
                status.error or "the model download did not complete",
                code=ErrorCode.MODEL_DOWNLOAD_FAILED,
                context={"model_id": settings.model_id},
            )
        on_progress(
            JobStage.DOWNLOADING_MODEL,
            stage_progress(JobStage.DOWNLOADING_MODEL, 1.0),
            0.0,
            None,
        )

    def _check_disk(self, media_path: Path) -> None:
        free = free_disk_bytes(self._settings.data_dir)
        required = self._settings.min_free_disk_bytes
        if free and free < required:
            raise ResourceError(
                f"only {free // (1 << 20)} MiB free, need at least {required // (1 << 20)} MiB",
                code=ErrorCode.INSUFFICIENT_DISK,
                context={"free_bytes": free, "required_bytes": required},
            )
        if not media_path.is_file():
            raise TiktuvError(
                f"working media file is missing: {media_path}",
                code=ErrorCode.MEDIA_CORRUPT,
            )

    def _diarize(
        self,
        job: JobDetail,
        media_path: Path,
        tools: FFmpegTools,
        segments: list[Segment],
        *,
        cancel: CancelToken,
        on_progress: ProgressCallback,
    ) -> tuple[list[Segment], dict[str, str]]:
        backend = get_backend()
        if backend is None:
            # Requested but unavailable: proceed without speakers rather than
            # throwing away a completed transcription. Surfaced in the UI.
            log.warning("diarization requested but no backend is registered")
            return segments, {}

        on_progress(JobStage.DIARIZING, stage_progress(JobStage.DIARIZING, 0.1), 0.0, None)
        import numpy as np

        chunks: list[AudioArray] = []
        for window in iter_windows(
            media_path,
            tools,
            sample_rate=self._settings.sample_rate,
            window_seconds=self._settings.window_seconds,
            overlap_seconds=0.0,
            cancel=cancel,
        ):
            chunks.append(window.samples)
        samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")

        turns = backend.diarize(samples, self._settings.sample_rate)
        segments = assign_speakers(segments, turns)
        speakers = build_speaker_map(segments)
        on_progress(JobStage.DIARIZING, stage_progress(JobStage.DIARIZING, 1.0), 0.0, None)
        return segments, speakers

    def _export(
        self,
        job: JobDetail,
        transcript: Transcript,
        settings: TranscriptionSettings,
        *,
        on_progress: ProgressCallback,
    ) -> dict[str, str]:
        formats = list(settings.export_formats)
        if not formats:
            return {}

        subtitle_formats = {ExportFormat.SRT, ExportFormat.VTT}
        if set(formats) & subtitle_formats:
            on_progress(
                JobStage.GENERATING_SUBTITLES,
                stage_progress(JobStage.GENERATING_SUBTITLES, 1.0),
                transcript.duration,
                None,
            )

        on_progress(
            JobStage.EXPORTING, stage_progress(JobStage.EXPORTING, 0.1), transcript.duration, None
        )
        output_dir = validate_output_dir(settings.output_dir, self._settings.exports_dir)
        stem = Path(job.filename).stem or "transcript"

        results = export_transcript(
            transcript,
            settings,
            formats=formats,
            output_dir=output_dir,
            stem=stem,
        )
        on_progress(
            JobStage.EXPORTING, stage_progress(JobStage.EXPORTING, 1.0), transcript.duration, None
        )
        return {result.format.value: result.path for result in results}


def import_media_file(source: Path, destination: Path) -> Path:
    """Copy the user's file into our media directory.

    A copy — never a move and never an in-place edit — so the original is
    untouched no matter what happens next, and so a file on a removable drive
    stays transcribable after the drive is unplugged.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        code = (
            ErrorCode.INSUFFICIENT_DISK
            if getattr(exc, "errno", None) == 28
            else ErrorCode.MEDIA_CORRUPT
        )
        raise TiktuvError(f"could not import media: {exc}", code=code) from exc
    return destination


__all__ = [
    "JobCancelled",
    "PipelineResult",
    "STAGE_WEIGHTS",
    "TranscriptionPipeline",
    "import_media_file",
    "stage_progress",
]
