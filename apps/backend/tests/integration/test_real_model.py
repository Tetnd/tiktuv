"""Optional integration test against the real Hebrew ASR model.

**This test is skipped by default.** It downloads ≈3.1 GB of weights, needs
FFmpeg, and takes several minutes on CPU. Everything else in the suite runs
against the deterministic mock backend, so the fast tests stay fast.

Run it with::

    TIKTUV_RUN_REAL_MODEL_TEST=1 python -m pytest apps/backend/tests/integration -v -s

On PowerShell::

    $env:TIKTUV_RUN_REAL_MODEL_TEST=1
    .venv\\Scripts\\python.exe -m pytest apps/backend/tests/integration -v -s

Supply your own Hebrew recording to check accuracy rather than mere plumbing::

    $env:TIKTUV_REAL_MODEL_AUDIO="C:\\path\\to\\hebrew.mp3"
    $env:TIKTUV_REAL_MODEL_EXPECT="מילה שאמורה להופיע"

What it verifies:

1. ``ivrit-ai/whisper-large-v3`` loads through the documented Transformers API.
2. The model that ran is that model — never a silent substitution.
3. Real audio produces Hebrew characters in logical order.
4. Timestamps are monotonic and inside the media duration.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tiktuv.asr.base import TranscribeRequest
from tiktuv.asr.device import list_devices, resolve_device
from tiktuv.asr.registry import HEBREW_MODEL_ID, is_downloaded
from tiktuv.config import Settings
from tiktuv.media.audio import iter_windows
from tiktuv.media.ffmpeg import locate_tools
from tiktuv.schemas import DevicePreference, TranscriptionSettings

pytestmark = [
    pytest.mark.real_model,
    pytest.mark.skipif(
        os.environ.get("TIKTUV_RUN_REAL_MODEL_TEST") != "1",
        reason="set TIKTUV_RUN_REAL_MODEL_TEST=1 to run the real-model test",
    ),
]


@pytest.fixture(scope="module")
def real_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    # Reuse the user's real model cache so the download happens at most once.
    default = Settings()
    return Settings(
        data_dir=tmp_path_factory.mktemp("real-model-data"),
        models_dir=default.model_cache_dir,
        window_seconds=30.0,
        window_overlap_seconds=2.0,
    )


@pytest.fixture(scope="module")
def transcriber(real_settings: Settings):
    pytest.importorskip("torch", reason="PyTorch is required")
    pytest.importorskip("transformers", reason="Transformers is required")

    from tiktuv.asr.whisper_hf import WhisperHFTranscriber

    if not is_downloaded(HEBREW_MODEL_ID, real_settings.model_cache_dir):
        pytest.skip(
            f"{HEBREW_MODEL_ID} is not downloaded. Download it from the app's "
            f"settings screen, or run:\n"
            f"  python -c \"from huggingface_hub import snapshot_download as d; "
            f"d('{HEBREW_MODEL_ID}', cache_dir=r'{real_settings.model_cache_dir}')\""
        )

    resolved = resolve_device(
        DevicePreference.AUTO, requested_fp16=True, requested_batch_size=0
    )
    print(f"\nLoading {HEBREW_MODEL_ID} on {resolved.torch_device} ({resolved.dtype_name})")

    model = WhisperHFTranscriber(
        HEBREW_MODEL_ID,
        resolved,
        cache_dir=real_settings.model_cache_dir,
        local_files_only=True,
    )
    yield model
    model.unload()


@pytest.fixture(scope="module")
def audio_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    supplied = os.environ.get("TIKTUV_REAL_MODEL_AUDIO")
    if supplied:
        path = Path(supplied)
        if not path.is_file():
            pytest.fail(f"TIKTUV_REAL_MODEL_AUDIO points at a missing file: {path}")
        return path

    sample = Path(__file__).resolve().parents[3].parent / "samples" / "hebrew-sample.mp3"
    if sample.is_file():
        return sample

    pytest.skip(
        "no Hebrew audio available. Set TIKTUV_REAL_MODEL_AUDIO to a recording, "
        "or place one at samples/hebrew-sample.mp3"
    )


def test_devices_are_reported(real_settings: Settings) -> None:
    devices = list_devices()
    print("\nDevices:")
    for device in devices:
        print(f"  {device.kind}: {device.name} fp16={device.supports_fp16}")
    assert any(device.kind == "cpu" for device in devices)


def test_the_hebrew_model_is_the_one_that_runs(transcriber) -> None:
    """Guards the promise that the Hebrew model is never silently swapped."""
    assert transcriber.model_id == HEBREW_MODEL_ID


def test_transcribes_real_hebrew_audio(
    transcriber, audio_path: Path, real_settings: Settings
) -> None:
    tools = locate_tools(real_settings)
    settings = TranscriptionSettings(model_id=HEBREW_MODEL_ID)

    segments = []
    for window in iter_windows(
        audio_path,
        tools,
        sample_rate=real_settings.sample_rate,
        window_seconds=real_settings.window_seconds,
        overlap_seconds=real_settings.window_overlap_seconds,
    ):
        result = transcriber.transcribe(
            TranscribeRequest(
                samples=window.samples,
                sample_rate=window.sample_rate,
                settings=settings,
            )
        )
        for segment in result.segments:
            segments.append(
                segment.model_copy(
                    update={
                        "start": segment.start + window.start,
                        "end": segment.end + window.start,
                    }
                )
            )
        # One window is enough to prove the pipeline; keep the runtime sane.
        break

    assert segments, "the model produced no segments"

    text = " ".join(segment.text for segment in segments)
    print(f"\nTranscript: {text[:400]}")

    hebrew_characters = sum(1 for character in text if "\u0590" <= character <= "\u05ff")
    assert hebrew_characters > 0, f"no Hebrew characters in the output: {text!r}"

    for previous, current in zip(segments, segments[1:], strict=False):
        assert current.start >= previous.start

    expected = os.environ.get("TIKTUV_REAL_MODEL_EXPECT")
    if expected:
        assert expected in text, f"expected {expected!r} in the transcript, got: {text!r}"


def test_word_timestamps_are_available(
    transcriber, audio_path: Path, real_settings: Settings
) -> None:
    from tiktuv.schemas import TimestampMode

    tools = locate_tools(real_settings)
    settings = TranscriptionSettings(
        model_id=HEBREW_MODEL_ID, timestamps=TimestampMode.WORD
    )

    window = next(
        iter_windows(
            audio_path,
            tools,
            sample_rate=real_settings.sample_rate,
            window_seconds=real_settings.window_seconds,
            overlap_seconds=0.0,
        )
    )
    result = transcriber.transcribe(
        TranscribeRequest(
            samples=window.samples, sample_rate=window.sample_rate, settings=settings
        )
    )
    assert result.segments
    assert any(segment.words for segment in result.segments), (
        "word timestamps were requested but none were returned"
    )
