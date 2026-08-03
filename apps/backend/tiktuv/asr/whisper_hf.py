"""Hugging Face Transformers backend for ``ivrit-ai/whisper-large-v3``.

This is the real inference path. It uses the Transformers ASR pipeline exactly
as the model card documents:

    pipeline("automatic-speech-recognition", model="ivrit-ai/whisper-large-v3")

with the model, tokenizer and feature extractor loaded explicitly so we control
dtype, device, cache directory and offline behaviour.

The Hebrew model is never substituted automatically. If it cannot be loaded the
job fails with :class:`~tiktuv.errors.ModelError`; choosing a fallback is an
explicit user action in the settings screen.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from ..errors import ErrorCode, ModelError
from ..logging_conf import get_logger
from ..schemas import (
    MODEL_CHUNK_SECONDS,
    Language,
    Segment,
    TimestampMode,
    TranscriptionSettings,
    Word,
)
from .base import TranscribeRequest, TranscribeResult
from .chunking import clamp_words_to_segment
from .device import ResolvedDevice, empty_cuda_cache

log = get_logger("asr.whisper_hf")

#: A pause at least this long, or a sentence-final punctuation mark, ends a
#: segment when we are grouping word timestamps back into segments.
_WORD_GROUP_PAUSE = 0.6
_SENTENCE_ENDINGS = frozenset(".!?׃…")
_MAX_SEGMENT_SECONDS = 12.0


def _require_transformers() -> tuple[Any, Any, Any]:
    try:
        from transformers import (
            AutoModelForSpeechSeq2Seq,
            AutoProcessor,
            pipeline,
        )
    except ImportError as exc:
        raise ModelError(
            "the transformers inference extras are not installed",
            code=ErrorCode.ASR_BACKEND_MISSING,
            context={"install": "pip install -e apps/backend[asr]"},
        ) from exc
    return AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline


def _clear_forced_decoder_ids(model: Any) -> None:
    """Drop the checkpoint's baked-in ``forced_decoder_ids``.

    ``ivrit-ai/whisper-large-v3`` ships a generation config with
    ``forced_decoder_ids`` that pins a language token. We always pass
    ``language`` and ``task`` explicitly, and Transformers 4.47 warns that the
    two "create a conflict" before resolving it in favour of ours.

    Relying on that tie-break is fragile: if a future version preferred the
    baked-in ids, every job would silently decode in whatever language the
    checkpoint happens to name. Clearing them makes our explicit setting the
    only source of truth, and removes the warning.
    """
    for holder in (getattr(model, "generation_config", None), getattr(model, "config", None)):
        if holder is not None and getattr(holder, "forced_decoder_ids", None):
            holder.forced_decoder_ids = None


def _require_torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise ModelError(
            "PyTorch is not installed",
            code=ErrorCode.ASR_BACKEND_MISSING,
            context={"install": "see docs/cpu-only-setup.md"},
        ) from exc
    return torch


class WhisperHFTranscriber:
    """A loaded Whisper model. Construct once, reuse for every job."""

    def __init__(
        self,
        model_id: str,
        resolved: ResolvedDevice,
        *,
        cache_dir: Path,
        quantize_8bit: bool = False,
        local_files_only: bool = False,
    ) -> None:
        self._model_id = model_id
        self._resolved = resolved
        self._cache_dir = cache_dir
        # Generation is not thread-safe; one job at a time is the default, but
        # the lock makes a raised concurrency limit safe rather than corrupt.
        self._lock = threading.Lock()
        self._pipe: Any = None
        self._processor: Any = None
        self._unloaded = False
        self._load(quantize_8bit=quantize_8bit, local_files_only=local_files_only)

    # -- properties --------------------------------------------------------
    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def device(self) -> str:
        return self._resolved.torch_device

    @property
    def resolved_device(self) -> ResolvedDevice:
        return self._resolved

    # -- loading -----------------------------------------------------------
    def _load(self, *, quantize_8bit: bool, local_files_only: bool) -> None:
        AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline = _require_transformers()
        torch = _require_torch()

        dtype = torch.float16 if self._resolved.use_fp16 else torch.float32
        model_kwargs: dict[str, Any] = {
            "cache_dir": str(self._cache_dir),
            "low_cpu_mem_usage": True,
            "use_safetensors": True,
            "local_files_only": local_files_only,
        }

        if quantize_8bit:
            if self._resolved.kind != "cuda":
                raise ModelError(
                    "8-bit quantization requires a CUDA device",
                    code=ErrorCode.MODEL_LOAD_FAILED,
                )
            try:
                import bitsandbytes  # noqa: F401
                from transformers import BitsAndBytesConfig
            except ImportError as exc:
                raise ModelError(
                    "8-bit quantization needs the 'bitsandbytes' package, which is not "
                    "installed. Install it or turn quantization off — Tiktuv will not "
                    "silently fall back to full precision.",
                    code=ErrorCode.MODEL_LOAD_FAILED,
                ) from exc
            model_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        else:
            model_kwargs["torch_dtype"] = dtype

        log.info(
            "loading ASR model",
            extra={
                "model_id": self._model_id,
                "device": self._resolved.torch_device,
                "dtype": self._resolved.dtype_name,
                "quantized": quantize_8bit,
                "local_files_only": local_files_only,
            },
        )

        try:
            model = AutoModelForSpeechSeq2Seq.from_pretrained(self._model_id, **model_kwargs)
            if not quantize_8bit:
                # bitsandbytes places the model itself; moving it would fail.
                model = model.to(self._resolved.torch_device)
            model.eval()
            _clear_forced_decoder_ids(model)
            processor = AutoProcessor.from_pretrained(
                self._model_id,
                cache_dir=str(self._cache_dir),
                local_files_only=local_files_only,
            )
        except OSError as exc:
            raise ModelError(
                f"model files for {self._model_id!r} are not available locally: {exc}",
                code=ErrorCode.MODEL_NOT_DOWNLOADED,
                context={"model_id": self._model_id},
            ) from exc
        except (RuntimeError, ValueError) as exc:
            message = str(exc)
            code = (
                ErrorCode.INSUFFICIENT_VRAM
                if "out of memory" in message.lower()
                else ErrorCode.MODEL_LOAD_FAILED
            )
            raise ModelError(message, code=code, context={"model_id": self._model_id}) from exc

        pipe_kwargs: dict[str, Any] = {
            "model": model,
            "tokenizer": processor.tokenizer,
            "feature_extractor": processor.feature_extractor,
        }
        if not quantize_8bit:
            pipe_kwargs["torch_dtype"] = dtype
            pipe_kwargs["device"] = self._resolved.torch_device

        self._processor = processor
        self._pipe = pipeline("automatic-speech-recognition", **pipe_kwargs)
        log.info("ASR model ready", extra={"model_id": self._model_id})

    # -- inference ---------------------------------------------------------
    def transcribe(self, request: TranscribeRequest) -> TranscribeResult:
        if self._unloaded or self._pipe is None:
            raise ModelError("transcriber has been unloaded", code=ErrorCode.MODEL_LOAD_FAILED)

        settings = request.settings
        samples = np.asarray(request.samples, dtype=np.float32)
        if samples.size == 0:
            return TranscribeResult(segments=[], language=_language_code(settings))

        word_level = settings.timestamps is TimestampMode.WORD
        return_timestamps: bool | str = "word" if word_level else True

        generate_kwargs = self._build_generate_kwargs(settings, request.previous_text)

        pipe_kwargs: dict[str, Any] = {
            "chunk_length_s": MODEL_CHUNK_SECONDS,
            "return_timestamps": return_timestamps,
            "generate_kwargs": generate_kwargs,
        }
        # batch_size > 1 only helps when the window is longer than one model
        # chunk; otherwise it just allocates.
        batch = max(1, self._resolved.batch_size)
        if batch > 1 and samples.size > MODEL_CHUNK_SECONDS * request.sample_rate:
            pipe_kwargs["batch_size"] = batch

        payload = {"raw": samples, "sampling_rate": request.sample_rate}

        try:
            with self._lock:
                output = self._pipe(payload, **pipe_kwargs)
        except Exception as exc:  # noqa: BLE001 - normalised immediately below
            message = str(exc)
            lowered = message.lower()
            if "out of memory" in lowered or "cuda error" in lowered:
                empty_cuda_cache()
                raise ModelError(
                    message,
                    code=ErrorCode.INSUFFICIENT_VRAM,
                    context={"batch_size": batch, "device": self.device},
                ) from exc
            raise ModelError(
                message, code=ErrorCode.MODEL_LOAD_FAILED, context={"stage": "generate"}
            ) from exc

        window_seconds = samples.size / max(1, request.sample_rate)
        segments = (
            _segments_from_words(output, window_seconds)
            if word_level
            else _segments_from_chunks(output, window_seconds)
        )
        return TranscribeResult(segments=segments, language=_language_code(settings))

    def _build_generate_kwargs(
        self, settings: TranscriptionSettings, previous_text: str
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = dict(settings.decoding_kwargs())
        kwargs["task"] = "transcribe"

        # Language: Whisper wants an ISO code. "auto" means "let the model
        # detect", which is expressed by simply not forcing one.
        if settings.language is not Language.AUTO:
            kwargs["language"] = settings.language.value

        prompt = _build_prompt(settings.vocabulary_prompt, previous_text)
        if prompt and self._processor is not None:
            try:
                kwargs["prompt_ids"] = self._processor.get_prompt_ids(
                    prompt, return_tensors="pt"
                ).to(self._resolved.torch_device)
                # The prompt is context, not output — strip it from the text.
                kwargs["prompt_condition_type"] = "first-segment"
            except (AttributeError, ValueError) as exc:
                # Never fail a job because a hint could not be tokenised.
                log.warning("could not apply vocabulary prompt", extra={"error": str(exc)})
        return kwargs

    def unload(self) -> None:
        with self._lock:
            self._pipe = None
            self._processor = None
            self._unloaded = True
        empty_cuda_cache()
        log.info("ASR model unloaded", extra={"model_id": self._model_id})


# --------------------------------------------------------------------------
# Output normalisation
# --------------------------------------------------------------------------
def _language_code(settings: TranscriptionSettings) -> str:
    return "auto" if settings.language is Language.AUTO else settings.language.value


#: How much preceding text to feed back as context. Whisper's prompt window is
#: half its 448-token context; a few hundred characters stays well inside it.
_PREVIOUS_TEXT_CHARS = 300


def _build_prompt(vocabulary: str, previous_text: str) -> str:
    parts = [
        part.strip()
        for part in (vocabulary, previous_text[-_PREVIOUS_TEXT_CHARS:])
        if part.strip()
    ]
    return " ".join(parts)


def _chunk_bounds(chunk: dict[str, Any], fallback_start: float) -> tuple[float, float]:
    """Read a chunk's ``(start, end)``, tolerating the ``None``s Whisper emits.

    Whisper omits an end timestamp when the audio is cut mid-word, and can omit
    both when it did not emit timestamp tokens at all — it warns about exactly
    this ("did not predict an ending timestamp"). Treating a missing end as
    equal to the start yields zero-length segments, which then never match the
    editor's "currently playing" test and collapse subtitle cues.
    """
    timestamp = chunk.get("timestamp") or (None, None)
    start, end = (timestamp + (None, None))[:2] if isinstance(timestamp, tuple) else (None, None)
    start_f = float(start) if start is not None else fallback_start
    end_f = float(end) if end is not None else start_f
    return start_f, max(end_f, start_f)


#: Minimum length given to a segment whose timestamps came back degenerate.
_MIN_SEGMENT_SECONDS = 0.2


def _ensure_duration(segment: Segment, *, limit: float | None = None) -> Segment:
    """Give a zero-length segment a usable span.

    ``limit`` caps the end at the length of the window, so a repair can never
    push a segment past the audio it came from.
    """
    if segment.end > segment.start:
        return segment
    end = segment.start + _MIN_SEGMENT_SECONDS
    if limit is not None:
        end = min(end, limit)
        if end <= segment.start:
            # Already at the very end of the window: extend backwards instead.
            start = max(0.0, end - _MIN_SEGMENT_SECONDS)
            return segment.model_copy(update={"start": start, "end": end})
    return segment.model_copy(update={"end": end})


def _segments_from_chunks(
    output: dict[str, Any], window_seconds: float | None = None
) -> list[Segment]:
    """Turn the pipeline's segment-level ``chunks`` into :class:`Segment`."""
    chunks = output.get("chunks") or []
    if not chunks:
        text = str(output.get("text") or "").strip()
        if not text:
            return []
        # No timestamps at all: the text covers the whole window.
        return [Segment(id=0, start=0.0, end=window_seconds or _MIN_SEGMENT_SECONDS, text=text)]

    segments: list[Segment] = []
    cursor = 0.0
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("text") or "").strip()
        start, end = _chunk_bounds(chunk, cursor)
        cursor = end
        if not text:
            continue
        segments.append(
            _ensure_duration(
                Segment(id=index, start=start, end=end, text=text), limit=window_seconds
            )
        )
    return segments


def _redistribute_words(segment: Segment) -> Segment:
    """Spread word times evenly when the model returned them all identical.

    Whisper occasionally emits the same timestamp for every word in a run. Left
    alone, clicking a word seeks to the wrong place and no word ever highlights.
    """
    if len(segment.words) < 2:
        return segment
    if segment.words[-1].end > segment.words[0].start:
        return segment  # genuine, increasing timestamps

    span = max(segment.end - segment.start, _MIN_SEGMENT_SECONDS)
    step = span / len(segment.words)
    words = [
        word.model_copy(
            update={
                "start": segment.start + index * step,
                "end": segment.start + (index + 1) * step,
            }
        )
        for index, word in enumerate(segment.words)
    ]
    return segment.model_copy(update={"words": words})


def _segments_from_words(
    output: dict[str, Any], window_seconds: float | None = None
) -> list[Segment]:
    """Group word-level chunks back into readable segments.

    ``return_timestamps="word"`` gives one chunk per word and no segments, so
    we rebuild them: a new segment starts after sentence-final punctuation,
    after a pause of :data:`_WORD_GROUP_PAUSE` seconds, or once a segment has
    run for :data:`_MAX_SEGMENT_SECONDS`.
    """
    chunks = output.get("chunks") or []
    if not chunks:
        return _segments_from_chunks(output, window_seconds)

    segments: list[Segment] = []
    words: list[Word] = []
    cursor = 0.0

    def flush() -> None:
        nonlocal words
        if not words:
            return
        text = "".join(word.text for word in words).strip()
        if text:
            segment = Segment(
                id=len(segments),
                start=words[0].start,
                end=words[-1].end,
                text=text,
                words=[w.model_copy(update={"text": w.text.strip()}) for w in words],
            )
            segment = _ensure_duration(segment, limit=window_seconds)
            segments.append(_redistribute_words(clamp_words_to_segment(segment)))
        words = []

    for chunk in chunks:
        raw_text = str(chunk.get("text") or "")
        if not raw_text.strip():
            continue
        start, end = _chunk_bounds(chunk, cursor)
        gap = start - cursor if words else 0.0
        cursor = end

        if words and (
            gap >= _WORD_GROUP_PAUSE
            or (end - words[0].start) >= _MAX_SEGMENT_SECONDS
        ):
            flush()

        words.append(Word(text=raw_text, start=start, end=end))

        if raw_text.strip()[-1:] in _SENTENCE_ENDINGS:
            flush()

    flush()
    return segments
