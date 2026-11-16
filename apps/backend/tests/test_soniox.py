"""Soniox cloud ASR backend and its wiring."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tiktuv.asr.base import TranscribeRequest
from tiktuv.asr.registry import SONIOX_MODEL_ID, ModelManager, is_downloaded, list_models
from tiktuv.asr.soniox import (
    SonioxTranscriber,
    _encode_wav,
    _segments_from_text,
    _speaker_turns,
    _Token,
)
from tiktuv.config import Settings
from tiktuv.credentials import SonioxCredentials
from tiktuv.errors import ErrorCode
from tiktuv.schemas import TimestampMode, TranscriptionSettings

# --------------------------------------------------------------------------
# Credentials store
# --------------------------------------------------------------------------


def test_credentials_roundtrip(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", auth_token="test")
    settings.ensure_dirs()
    credentials = SonioxCredentials(settings)

    assert credentials.get() is None
    assert credentials.masked() == ""

    credentials.set("soniox-secret-key-1234")
    assert credentials.get() == "soniox-secret-key-1234"
    assert credentials.masked() == "••••1234"

    credentials.clear()
    assert credentials.get() is None


def test_credentials_short_key_is_fully_masked(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", auth_token="test")
    settings.ensure_dirs()
    credentials = SonioxCredentials(settings)
    credentials.set("abc")
    assert credentials.masked() == "•••"


# --------------------------------------------------------------------------
# Output shaping
# --------------------------------------------------------------------------


def test_encode_wav_is_valid_pcm(tmp_path: Path) -> None:
    import io
    import wave

    samples = (np.sin(np.linspace(0, 6.28, 1600)) * 0.5).astype(np.float32)
    data = _encode_wav(samples, 16000)

    with wave.open(io.BytesIO(data), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert handle.getnframes() == 1600


def test_segments_from_text_splits_sentences_and_spreads_timestamps() -> None:
    settings = TranscriptionSettings(timestamps=TimestampMode.SEGMENT)
    text = "שלום. מה שלומך? טוב מאוד."
    segments = _segments_from_text(text, [], window_duration=6.0, settings=settings)

    assert [s.text for s in segments] == ["שלום.", "מה שלומך?", "טוב מאוד."]
    # Timestamps are monotonic and cover the window.
    assert segments[0].start == 0.0
    assert segments[-1].end == pytest.approx(6.0)
    for previous, current in zip(segments, segments[1:], strict=False):
        assert current.start == pytest.approx(previous.end)
    # Segment mode attaches no word timestamps.
    assert all(not s.words for s in segments)


def test_segments_word_mode_attaches_words() -> None:
    settings = TranscriptionSettings(timestamps=TimestampMode.WORD)
    text = "שלום עולם."
    segments = _segments_from_text(text, [], window_duration=2.0, settings=settings)

    assert len(segments) == 1
    assert [w.text for w in segments[0].words] == ["שלום", "עולם."]


# --------------------------------------------------------------------------
# Transcriber against a fake HTTP client
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, responses: list[tuple[str, str, _FakeResponse]]) -> None:
        self._responses = responses
        self.requests: list[tuple[str, str]] = []
        self.closed = False

    def _dispatch(self, method: str, url: str) -> _FakeResponse:
        self.requests.append((method, url))
        for expected_method, expected_url, response in self._responses:
            if method == expected_method and url == expected_url:
                return response
        return _FakeResponse(500, {"error": "unexpected request"})

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        return self._dispatch("POST", url)

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        return self._dispatch("GET", url)

    def delete(self, url: str, **kwargs: object) -> _FakeResponse:
        return self._dispatch("DELETE", url)

    def close(self) -> None:
        self.closed = True


def _make_transcriber(
    responses: list[tuple[str, str, _FakeResponse]],
) -> tuple[SonioxTranscriber, _FakeClient]:
    client = _FakeClient(responses)
    transcriber = SonioxTranscriber("soniox-key", client=client)  # type: ignore[arg-type]
    return transcriber, client


def test_transcribe_uploads_polls_and_returns_hebrew() -> None:
    responses = [
        ("POST", "/files", _FakeResponse(200, {"id": "f1"})),
        ("POST", "/transcriptions", _FakeResponse(201, {"id": "t1"})),
        ("GET", "/transcriptions/t1", _FakeResponse(200, {"status": "completed"})),
        (
            "GET",
            "/transcriptions/t1/transcript",
            _FakeResponse(200, {"text": "שלום. מה שלומך?", "tokens": []}),
        ),
        ("DELETE", "/transcriptions/t1", _FakeResponse(200, {})),
        ("DELETE", "/files/f1", _FakeResponse(200, {})),
    ]
    transcriber, client = _make_transcriber(responses)

    samples = np.zeros(16000, dtype=np.float32)
    request = TranscribeRequest(
        samples=samples, sample_rate=16000, settings=TranscriptionSettings()
    )
    result = transcriber.transcribe(request)

    assert result.language == "he"
    assert [s.text for s in result.segments] == ["שלום.", "מה שלומך?"]
    assert client.requests == [
        ("POST", "/files"),
        ("POST", "/transcriptions"),
        ("GET", "/transcriptions/t1"),
        ("GET", "/transcriptions/t1/transcript"),
        ("DELETE", "/transcriptions/t1"),
        ("DELETE", "/files/f1"),
    ]


def test_transcribe_uses_hebrew_language_hint() -> None:
    captured: dict = {}

    class _CapturingClient(_FakeClient):
        def post(self, url: str, **kwargs: object) -> _FakeResponse:  # type: ignore[override]
            captured["json"] = kwargs.get("json")
            return super().post(url, **kwargs)

    responses = [
        ("POST", "/files", _FakeResponse(200, {"id": "f1"})),
        ("POST", "/transcriptions", _FakeResponse(201, {"id": "t1"})),
        ("GET", "/transcriptions/t1", _FakeResponse(200, {"status": "completed"})),
        (
            "GET",
            "/transcriptions/t1/transcript",
            _FakeResponse(200, {"text": "שלום", "tokens": []}),
        ),
        ("DELETE", "/transcriptions/t1", _FakeResponse(200, {})),
        ("DELETE", "/files/f1", _FakeResponse(200, {})),
    ]
    transcriber = SonioxTranscriber("soniox-key", client=_CapturingClient(responses))  # type: ignore[arg-type]
    request = TranscribeRequest(
        samples=np.zeros(16000, np.float32),
        sample_rate=16000,
        settings=TranscriptionSettings(),
    )
    transcriber.transcribe(request)

    assert captured["json"]["model"] == "stt-async-v6"  # type: ignore[index]
    assert captured["json"]["language_hints"] == ["he"]  # type: ignore[index]
    assert captured["json"]["enable_speaker_diarization"] is True  # type: ignore[index]


def test_transcribe_error_status_raises() -> None:
    responses = [
        ("POST", "/files", _FakeResponse(200, {"id": "f1"})),
        ("POST", "/transcriptions", _FakeResponse(201, {"id": "t1"})),
        (
            "GET",
            "/transcriptions/t1",
            _FakeResponse(200, {"status": "error", "error_message": "boom"}),
        ),
        ("DELETE", "/files/f1", _FakeResponse(200, {})),
    ]
    transcriber, _client = _make_transcriber(responses)

    with pytest.raises(Exception) as exc_info:
        transcriber.transcribe(
            TranscribeRequest(
                samples=np.zeros(16000, np.float32),
                sample_rate=16000,
                settings=TranscriptionSettings(),
            )
        )
    assert exc_info.value.code == ErrorCode.SONIOX_API_ERROR  # type: ignore[attr-defined]


def test_speaker_turns_group_consecutive_tokens() -> None:
    tokens = [
        _Token("a", 0.0, 1.0, None, speaker="1"),
        _Token("b", 1.0, 2.0, None, speaker="1"),
        _Token("c", 2.0, 3.0, None, speaker="2"),
        _Token("d", 3.0, 4.0, None, speaker="1"),
    ]
    turns = _speaker_turns(tokens)
    assert [(t.start, t.end, t.speaker) for t in turns] == [
        (0.0, 2.0, "1"),
        (2.0, 3.0, "2"),
        (3.0, 4.0, "1"),
    ]


# --------------------------------------------------------------------------
# Registry wiring
# --------------------------------------------------------------------------


def test_soniox_model_is_cloud_and_always_downloaded(tmp_path: Path) -> None:
    assert is_downloaded(SONIOX_MODEL_ID, tmp_path) is True

    settings = Settings(data_dir=tmp_path / "data", auth_token="test")
    entry = next(model for model in list_models(settings) if model.id == SONIOX_MODEL_ID)
    assert entry.downloaded is True
    assert entry.approx_download_bytes == 0
    assert entry.hebrew_specialised is True


def test_model_manager_requires_soniox_key(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", auth_token="test")
    settings.ensure_dirs()

    manager = ModelManager(settings)
    with pytest.raises(Exception) as exc_info:
        manager._api_key()
    assert exc_info.value.code == ErrorCode.SONIOX_KEY_MISSING  # type: ignore[attr-defined]

    SonioxCredentials(settings).set("soniox-key")
    assert manager._api_key() == "soniox-key"


def test_model_manager_acquires_cloud_transcriber(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", auth_token="test")
    settings.ensure_dirs()
    SonioxCredentials(settings).set("soniox-key")

    manager = ModelManager(settings, credentials=SonioxCredentials(settings))
    from tiktuv.asr.registry import get_entry

    transcriber = manager._acquire_cloud(get_entry(SONIOX_MODEL_ID))
    assert transcriber.model_id == SONIOX_MODEL_ID
    assert transcriber.device == "cloud"
    transcriber.unload()


# --------------------------------------------------------------------------
# API endpoints
# --------------------------------------------------------------------------


def test_soniox_key_endpoints(client: object) -> None:
    get = client.get  # type: ignore[attr-defined]
    put = client.put  # type: ignore[attr-defined]
    delete = client.delete  # type: ignore[attr-defined]

    assert get("/api/soniox/status").json() == {"configured": False, "masked_key": ""}

    response = put("/api/soniox/key", json={"api_key": "soniox-secret-12345678"})
    assert response.status_code == 200
    assert response.json() == {"configured": True, "masked_key": "••••5678"}
    assert get("/api/soniox/status").json() == {"configured": True, "masked_key": "••••5678"}

    assert delete("/api/soniox/key").json() == {"configured": False, "masked_key": ""}


def test_soniox_key_rejects_empty(client: object) -> None:
    response = client.put("/api/soniox/key", json={"api_key": "   "})  # type: ignore[attr-defined]
    assert response.status_code == 422
