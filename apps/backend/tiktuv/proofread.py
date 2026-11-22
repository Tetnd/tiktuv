"""DeepSeek proofreading of a finished transcription.

Whisper (and Soniox) hear Hebrew well but not perfectly: homophones come out
wrong, proper nouns get mangled, prefixes glue to the wrong word, and
punctuation lands where no editor would put it. This module hands the raw
segments to DeepSeek with one job — act as a professional Hebrew copy-editor
and *correct* the text, without rewriting, summarising or translating it.

Design constraints that the rest of the pipeline depends on:

* Timings are never touched. Segments go out and come back one-to-one, keyed by
  id, so the player, the subtitles and the editor stay aligned with the audio.
* Corrections are validated before they are accepted, word by word (see
  :mod:`tiktuv.proofread_guard`). A change that is not a plausible
  transcription fix — a synonym, a different number, an added or dropped word,
  a flipped "לא" — is discarded and the original words kept, so a proofreader
  that "helpfully" rewrites cannot damage a transcript.
* Failures are the caller's to absorb. A transient failure (timeout, 429, 5xx)
  is retried; a batch that still fails is skipped without losing the others.
  :func:`proofread_segments` raises :class:`ModelError` only when nothing could
  be proofread at all, and the pipeline treats even that as a warning, so a
  three-hour transcription is never lost to a flaky network.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Sequence

import httpx

from .analysis import DEEPSEEK_API_BASE, _parse_json
from .errors import ErrorCode, ModelError
from .logging_conf import get_logger
from .proofread_guard import reconcile, vocabulary_keys
from .schemas import Segment, Word

log = get_logger("proofread")

#: The reasoning model: proofreading Hebrew is a judgement task, and the
#: cheaper chat model happily "improves" text it was asked only to correct.
PROOFREAD_MODEL = "deepseek-reasoner"

#: Segments per request. Enough context for the model to catch a word that is
#: only wrong given the sentence before it, small enough that one bad reply
#: costs little and that progress moves visibly on a long recording.
BATCH_SEGMENTS = 40

#: Characters per request, whichever limit is reached first.
BATCH_CHARS = 4000

#: Neighbouring segments sent beside each batch, read-only, so a sentence cut
#: by a batch boundary is still understood.
CONTEXT_SEGMENTS = 4

#: A correction shorter than this fraction of the original — or longer than its
#: reciprocal — is rejected outright before the word-level check even runs.
_MIN_LENGTH_RATIO = 0.5
_MAX_LENGTH_RATIO = 2.0

#: Attempts per batch for transient failures, and the pause before each retry.
_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 2.0

#: Statuses where retrying is pointless: a bad key, no balance, a bad request.
_FATAL_STATUSES = frozenset({400, 401, 402, 403, 422})

_SYSTEM_PROMPT = (
    "אתה עורך לשון ומגיה מקצועי, דובר עברית ילידי. "
    "אתה מתקן תמלול אוטומטי (זיהוי דיבור) של הקלטה בעברית.\n"
    "המכונה שמעה לא נכון במקומות בודדים. "
    "תפקידך להחזיר את מה שנאמר בפועל — לא לשפר את מה שנאמר.\n\n"
    "מותר לתקן, ורק כשאתה בטוח לחלוטין לפי ההקשר:\n"
    "1. מילה שזוהתה כמילה אחרת הנשמעת דומה (אם/עם, את/אתה, אל/על, כל/קול, שם/סם).\n"
    "2. שם פרטי, שם מקום, מונח מקצועי או מילה לועזית שנכתבו בכתיב שגוי — "
    "בעיקר לפי רשימת המונחים אם ניתנה.\n"
    "3. מילים שנדבקו או נחתכו, ואותיות שימוש (ה, ב, ל, מ, ש, ו, כ) "
    "שנדבקו למילה הלא נכונה.\n"
    "4. כתיב שגוי: אות חסרה או מיותרת.\n"
    '5. פיסוק (נקודה, פסיק, סימן שאלה, גרשיים כמו צה"ל וג\'ינס) — רק כשברור.\n\n'
    "אסור בהחלט:\n"
    "— להחליף מילה במילה נרדפת או במילה שנשמעת שונה. תיקון הוא תמיד מילה שנשמעת כמו המקורית.\n"
    "— לשנות מספרים: ספרות נשארות ספרות, מילים נשארות מילים. לא להפוך 'לא' ל'כן' או להפך.\n"
    "— להוסיף מילה שלא נאמרה, למחוק מילה שנאמרה, לשנות סדר מילים, לסכם או לקצר.\n"
    "— ללטש דיבור: שפה מדוברת, סלנג, מילות מילוי, חזרות ומשפטים לא גמורים נשארים כמו שהם.\n"
    "— לתרגם או לתעתק: מילה באנגלית נשארת באנגלית, ומילה בעברית נשארת בעברית.\n"
    "— לאחד או לפצל מקטעים, או לשנות מספרי מקטעים.\n"
    "מילה עברית תקינה שמתאימה להקשר — לא נוגעים בה. כשיש ספק, השאר כמו שהיה. "
    "עדיף לפספס תיקון מאשר לטעות.\n\n"
    'הקלט הוא JSON: "segments" הם המקטעים לתיקון (id, text, ולעיתים speaker). '
    '"context_before" ו-"context_after" הם מקטעים סמוכים שנועדו להבנת ההקשר בלבד — '
    "לעולם אל תחזיר אותם.\n"
    "אם מקטע תקין — אל תחזיר אותו בכלל. "
    "החזר רק מקטעים שתיקנת, עם הטקסט המלא של המקטע אחרי התיקון.\n"
    "פלט: JSON תקין בלבד, ללא טקסט נוסף וללא ```, במבנה:\n"
    '{"corrections": [{"id": 0, "text": "הטקסט המתוקן"}]}\n'
    'אם אין מה לתקן: {"corrections": []}\n\n'
    "דוגמאות:\n"
    '"אני רוצה לדבר אל התקציב של השנה" ← "אני רוצה לדבר על התקציב של השנה"\n'
    '"הגענו לתל אביב ברכבת ה מהירה" ← "הגענו לתל אביב ברכבת המהירה"\n'
    '"יאללה בוא נלך כבר אני אני מת מרעב" ← ללא שינוי (דיבור טבעי, חזרה מכוונת)\n'
    '"הוא שילם שלוש מאות שקל" ← ללא שינוי (לא להפוך למספר)\n'
    '"זה לא נכון" ← ללא שינוי (לא להחליף במילה הפוכה או נרדפת)'
)

#: ``(corrected_so_far, total)`` after each batch, for the progress bar.
ProgressCallback = Callable[[int, int], None]


class _FatalApiError(ModelError):
    """An API failure that retrying cannot fix (bad key, no balance)."""


def _batches(segments: list[Segment]) -> Iterator[list[Segment]]:
    """Group segments into request-sized batches."""
    batch: list[Segment] = []
    chars = 0
    for segment in segments:
        length = len(segment.text)
        if batch and (len(batch) >= BATCH_SEGMENTS or chars + length > BATCH_CHARS):
            yield batch
            batch = []
            chars = 0
        batch.append(segment)
        chars += length
    if batch:
        yield batch


def build_batch_payload(
    segments: list[Segment],
    speakers: dict[str, str],
    before: Sequence[Segment] = (),
    after: Sequence[Segment] = (),
) -> str:
    """Render one batch as the JSON the prompt describes."""
    items: list[dict[str, object]] = []
    for segment in segments:
        item: dict[str, object] = {"id": segment.id, "text": segment.text}
        label = speakers.get(segment.speaker) if segment.speaker else None
        if label:
            item["speaker"] = label
        items.append(item)
    payload: dict[str, object] = {"segments": items}
    if before:
        payload["context_before"] = [segment.text for segment in before]
    if after:
        payload["context_after"] = [segment.text for segment in after]
    return json.dumps(payload, ensure_ascii=False)


def proofread_segments(
    api_key: str,
    segments: list[Segment],
    speakers: dict[str, str] | None = None,
    *,
    model: str = PROOFREAD_MODEL,
    vocabulary: str = "",
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[list[Segment], int]:
    """Proofread the segments with DeepSeek.

    Returns the segments — corrected ones carry ``original_text`` and
    ``proofread=True`` — and how many were changed. Timings, ids, speakers and
    the segment count are identical to the input.
    """
    speakers = speakers or {}
    if not api_key.strip():
        raise ModelError(
            "the DeepSeek API key has not been configured",
            code=ErrorCode.DEEPSEEK_KEY_MISSING,
        )

    candidates = [segment for segment in segments if segment.text.strip()]
    total = len(candidates)
    if total == 0:
        return list(segments), 0

    corrections: dict[int, str] = {}
    done = 0
    attempted = 0
    failed = 0
    last_error: ModelError | None = None
    for batch in _batches(candidates):
        if should_cancel is not None and should_cancel():
            break
        first = done
        before = candidates[max(0, first - CONTEXT_SEGMENTS) : first]
        after = candidates[first + len(batch) : first + len(batch) + CONTEXT_SEGMENTS]
        attempted += 1
        try:
            corrections.update(
                _proofread_batch(api_key, batch, speakers, model, vocabulary, before, after)
            )
        except _FatalApiError:
            raise
        except ModelError as exc:
            # One bad batch must not cost the corrections of the others.
            failed += 1
            last_error = exc
            log.warning("a proofreading batch failed", extra={"error": str(exc)})
        done += len(batch)
        if on_progress is not None:
            on_progress(done, total)

    if attempted and failed == attempted and last_error is not None:
        raise last_error

    result: list[Segment] = []
    changed = 0
    for segment in segments:
        text = corrections.get(segment.id)
        if text is None or text == segment.text:
            result.append(segment)
            continue
        result.append(apply_correction(segment, text))
        changed += 1

    log.info(
        "proofreading finished",
        extra={
            "segments": len(segments),
            "corrected": changed,
            "failed_batches": failed,
            "model": model,
        },
    )
    return result, changed


def apply_correction(segment: Segment, text: str) -> Segment:
    """Replace a segment's text, keeping word timings only where they still fit.

    Word timings are per *word* of the original audio. When the correction has
    the same number of words they still line up and only the spelling changes;
    when it does not, the list is dropped rather than left pointing at the
    wrong word — subtitle splitting then interpolates over the segment, which
    is approximate, where a stale list would be simply wrong.
    """
    words: list[Word] = []
    new_words = text.split()
    if segment.words and len(new_words) == len(segment.words):
        words = [
            word.model_copy(update={"text": replacement})
            for word, replacement in zip(segment.words, new_words, strict=True)
        ]
    return segment.model_copy(
        update={
            "text": text,
            "words": words,
            "original_text": segment.original_text or segment.text,
            "proofread": True,
        }
    )


def _proofread_batch(
    api_key: str,
    batch: list[Segment],
    speakers: dict[str, str],
    model: str,
    vocabulary: str,
    before: Sequence[Segment] = (),
    after: Sequence[Segment] = (),
) -> dict[int, str]:
    user_content = build_batch_payload(batch, speakers, before, after)
    if vocabulary.strip():
        # The same hints the user gave the ASR model. Names and jargon are
        # exactly what an ASR model gets wrong and a proofreader cannot guess.
        user_content = (
            "שמות ומונחים שמופיעים בהקלטה (בכתיב הנכון):\n"
            f"{vocabulary.strip()}\n\n{user_content}"
        )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        # Proofreading is not a creative task: the most likely correction is
        # the one we want, every time.
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    response = _post_with_retry(payload, headers)
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ModelError(
            "DeepSeek returned an unexpected response", code=ErrorCode.DEEPSEEK_API_ERROR
        ) from None

    return parse_corrections(content, batch, vocabulary)


def _post_with_retry(payload: dict[str, object], headers: dict[str, str]) -> httpx.Response:
    """POST to DeepSeek, retrying timeouts, rate limits and server errors."""
    last: ModelError | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(_RETRY_DELAY_SECONDS * (attempt - 1))
        try:
            response = httpx.post(
                f"{DEEPSEEK_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
                timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0),
            )
        except httpx.HTTPError as exc:
            last = ModelError(
                f"could not reach DeepSeek: {exc}", code=ErrorCode.DEEPSEEK_API_ERROR
            )
            continue
        if response.status_code < 400:
            return response
        if response.status_code in _FATAL_STATUSES:
            raise _FatalApiError(
                f"DeepSeek returned HTTP {response.status_code}",
                code=ErrorCode.DEEPSEEK_API_ERROR,
            )
        last = ModelError(
            f"DeepSeek returned HTTP {response.status_code}", code=ErrorCode.DEEPSEEK_API_ERROR
        )
    assert last is not None
    raise last


def parse_corrections(
    content: str, batch: list[Segment], vocabulary: str = ""
) -> dict[int, str]:
    """Pull accepted corrections out of a reply, discarding anything suspect.

    Each reply is cut down to the changes that look like transcription fixes
    (see :func:`tiktuv.proofread_guard.reconcile`); the rest keep the
    original words.
    """
    raw = _parse_json(content)
    if not isinstance(raw, dict):
        return {}
    originals = {segment.id: segment.text for segment in batch}
    terms = vocabulary_keys(vocabulary)

    accepted: dict[int, str] = {}
    for item in raw.get("corrections") or []:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        original = originals.get(segment_id)
        if original is None:
            # Not a segment we sent: the model invented an id.
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        text = " ".join(text.split())
        if not _is_plausible_correction(original, text):
            log.warning(
                "rejected an implausible correction",
                extra={
                    "segment_id": segment_id,
                    "original_chars": len(original),
                    "corrected_chars": len(text),
                },
            )
            continue
        vetted = reconcile(original, text, terms)
        if vetted != text:
            log.info(
                "dropped untrustworthy edits from a correction",
                extra={"segment_id": segment_id},
            )
        if vetted != " ".join(original.split()):
            accepted[segment_id] = vetted
    return accepted


def _is_plausible_correction(original: str, text: str) -> bool:
    """True when the reply looks like a fix rather than a rewrite."""
    if not text:
        return False
    baseline = len(original.strip())
    if baseline == 0:
        return False
    ratio = len(text) / baseline
    return _MIN_LENGTH_RATIO <= ratio <= _MAX_LENGTH_RATIO


__all__ = [
    "BATCH_CHARS",
    "BATCH_SEGMENTS",
    "PROOFREAD_MODEL",
    "apply_correction",
    "build_batch_payload",
    "parse_corrections",
    "proofread_segments",
]
