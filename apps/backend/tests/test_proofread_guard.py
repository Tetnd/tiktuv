"""The word-level guard that decides which DeepSeek edits to a Hebrew segment to trust."""

from __future__ import annotations

import json

import httpx
import pytest

from tiktuv import proofread
from tiktuv.errors import ModelError
from tiktuv.proofread import parse_corrections, proofread_segments
from tiktuv.proofread_guard import key, reconcile, similarity, vocabulary_keys
from tiktuv.schemas import Segment


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proofread, "_RETRY_DELAY_SECONDS", 0.0)


def _segment(text: str, id: int = 0) -> Segment:
    return Segment(id=id, start=float(id), end=float(id) + 1, text=text)


# ------------------------------------------------------------ accepted fixes
@pytest.mark.parametrize(
    ("original", "corrected"),
    [
        # Homophone: אל/על.
        ("אני רוצה לדבר אל התקציב", "אני רוצה לדבר על התקציב"),
        # Homophone: אם/עם.
        ("הלכתי אם החברים לים", "הלכתי עם החברים לים"),
        # Sound-alike letters: ק/כ.
        ("הוא אמר שקל אחד מאיתנו חשוב", "הוא אמר שכל אחד מאיתנו חשוב"),
        # A prefix glued to the wrong place.
        ("הגענו ברכבת ה מהירה", "הגענו ברכבת המהירה"),
        # Two words fused.
        ("אמרתי בוקרטוב לכולם", "אמרתי בוקר טוב לכולם"),
        # Punctuation only.
        ("שלום אני רוצה לדבר", "שלום, אני רוצה לדבר."),
        # A missing letter.
        ("זה היה יום מדהם", "זה היה יום מדהים"),
    ],
)
def test_accepts_genuine_transcription_fixes(original: str, corrected: str) -> None:
    assert reconcile(original, corrected) == corrected


def test_accepts_the_users_spelling_even_when_it_sounds_nothing_alike() -> None:
    vocabulary = vocabulary_keys("איכילוב, נתניהו")
    assert reconcile("נפגשנו בבית חולים איקלוב", "נפגשנו בבית חולים איכילוב", vocabulary) == (
        "נפגשנו בבית חולים איכילוב"
    )


# ------------------------------------------------------------ rejected edits
@pytest.mark.parametrize(
    ("original", "corrected"),
    [
        # Meaning flipped.
        ("זה לא נכון", "זה כן נכון"),
        # A synonym, not a fix.
        ("הוא קנה מכונית חדשה", "הוא רכש מכונית חדשה"),
        # A spoken number turned into digits.
        ("הוא שילם שלוש מאות שקל", "הוא שילם 300 שקל"),
        # A different number.
        ("נפגש ב-15 בחודש", "נפגש ב-16 בחודש"),
        # Translation / script change.
        ("קניתי אייפון חדש", "קניתי iPhone חדש"),
        # A word nobody said.
        ("אני הולך הביתה", "אני הולך הביתה עכשיו"),
        # A word dropped.
        ("אני אני מת מרעב", "אני מת מרעב"),
        # A reorder: judged as delete + insert, both refused.
        ("אני הולך הביתה עכשיו", "עכשיו אני הולך הביתה"),
    ],
)
def test_rejects_edits_that_are_not_transcription_fixes(original: str, corrected: str) -> None:
    assert reconcile(original, corrected) == original


def test_rejects_a_large_rewritten_block() -> None:
    original = "אנחנו חושבים שהתוכנית הזאת טובה מאוד לכולם"
    corrected = "לדעתנו המערך המוצע מתאים לציבור כולו בשלמותו"
    assert reconcile(original, corrected) == original


def test_keeps_the_good_edit_and_drops_the_bad_one_in_the_same_segment() -> None:
    original = "אני רוצה לדבר אל התקציב והוא קנה מכונית חדשה"
    corrected = "אני רוצה לדבר על התקציב והוא רכש מכונית חדשה"
    assert reconcile(original, corrected) == "אני רוצה לדבר על התקציב והוא קנה מכונית חדשה"


def test_identical_text_is_unchanged() -> None:
    assert reconcile("שלום עולם", "שלום עולם") == "שלום עולם"


# ------------------------------------------------------------ helpers
def test_key_ignores_niqqud_punctuation_and_final_forms() -> None:
    assert key("שָׁלוֹם,") == key("שלום")
    assert key("צה\"ל") == "צהל"
    assert key("עם") == key("עם.")


def test_similarity_treats_sound_alike_letters_as_equal() -> None:
    assert similarity("קול", "כל") > similarity("קול", "דג")
    assert similarity("אל", "על") >= 0.5
    assert similarity("לא", "כן") < 0.5


# ------------------------------------------------------------ parse_corrections
def test_parse_corrections_returns_only_the_vetted_text() -> None:
    batch = [_segment("אני רוצה לדבר אל התקציב והוא קנה מכונית")]
    content = json.dumps(
        {"corrections": [{"id": 0, "text": "אני רוצה לדבר על התקציב והוא רכש מכונית"}]},
        ensure_ascii=False,
    )
    assert parse_corrections(content, batch) == {0: "אני רוצה לדבר על התקציב והוא קנה מכונית"}


def test_parse_corrections_drops_a_segment_whose_only_edit_is_rejected() -> None:
    batch = [_segment("זה לא נכון")]
    content = json.dumps({"corrections": [{"id": 0, "text": "זה כן נכון"}]}, ensure_ascii=False)
    assert parse_corrections(content, batch) == {}


# ------------------------------------------------------------ the request
class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _ok(corrections: list[dict]) -> _Response:
    content = json.dumps({"corrections": corrections}, ensure_ascii=False)
    return _Response(200, {"choices": [{"message": {"content": content}}]})


def test_neighbouring_segments_ride_along_as_context(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict] = []

    def fake_post(url: str, **kwargs: object) -> _Response:
        sent.append(json.loads(kwargs["json"]["messages"][1]["content"]))  # type: ignore[index]
        return _ok([])

    monkeypatch.setattr("tiktuv.proofread.httpx.post", fake_post)
    monkeypatch.setattr(proofread, "BATCH_SEGMENTS", 2)

    segments = [_segment(f"משפט מספר {n}", id=n) for n in range(5)]
    proofread_segments("key", segments)

    assert [item["id"] for item in sent[1]["segments"]] == [2, 3]
    assert sent[1]["context_before"] == ["משפט מספר 0", "משפט מספר 1"]
    assert sent[1]["context_after"] == ["משפט מספר 4"]
    assert "context_before" not in sent[0]


def test_a_transient_failure_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter([_Response(503), httpx.ReadTimeout("slow"), _ok([{"id": 0, "text": "שלום."}])])

    def fake_post(url: str, **kwargs: object) -> _Response:
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr("tiktuv.proofread.httpx.post", fake_post)
    segments, changed = proofread_segments("key", [_segment("שלום")])
    assert changed == 1
    assert segments[0].text == "שלום."


def test_a_bad_key_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_post(url: str, **kwargs: object) -> _Response:
        calls["n"] += 1
        return _Response(401)

    monkeypatch.setattr("tiktuv.proofread.httpx.post", fake_post)
    with pytest.raises(ModelError):
        proofread_segments("key", [_segment("שלום")])
    assert calls["n"] == 1


def test_one_failed_batch_does_not_lose_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proofread, "BATCH_SEGMENTS", 1)

    def fake_post(url: str, **kwargs: object) -> _Response:
        sent = json.loads(kwargs["json"]["messages"][1]["content"])  # type: ignore[index]
        item = sent["segments"][0]
        if item["id"] == 0:
            return _Response(500)
        return _ok([{"id": item["id"], "text": item["text"] + "."}])

    monkeypatch.setattr("tiktuv.proofread.httpx.post", fake_post)
    segments, changed = proofread_segments("key", [_segment("שלום", 0), _segment("להתראות", 1)])
    assert changed == 1
    assert segments[0].text == "שלום"
    assert segments[1].text == "להתראות."
