"""Speaker recognition from the *text*, with DeepSeek.

Acoustic diarization (Soniox) tells voices apart. This module does something
different and complementary: it reads the conversation. Who asks and who
answers, who is addressed by name, who agrees with whom, which line finishes
which sentence — a language model follows that the way a person reading a
transcript does, and it works with **any** transcription engine because it
needs no audio. Nothing but the transcript text leaves the machine, which is
what proofreading and analysis already send.

It is strongest on interviews, meetings and phone calls with distinct roles,
and weakest on two similar speakers trading very short replies — say so
honestly in the UI rather than pretend otherwise.

How it stays reliable on a recording of any length:

* The transcript goes out in batches, each with a few already-labelled lines
  before it and a short *profile* of every speaker found so far ("המראיין —
  שואל שאלות"), so "speaker 2" in batch five is the person who was "speaker 2"
  in batch one.
* A reply is checked, not trusted: a label for a segment that was not sent is
  ignored, a missing label inherits the previous speaker, speaker ids are
  normalised, and a *split* (one segment that holds two people) is accepted
  only when the parts are exactly the original words — the model may not
  rewrite anything while it is at it.
* Names are only proposed with quoted evidence, and the quote must really occur
  in the transcript (see :func:`suggest_names`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from ..analysis import _parse_json
from ..errors import ErrorCode, ModelError
from ..logging_conf import get_logger
from ..proofread import _post_with_retry
from ..schemas import Segment

log = get_logger("diarization.deepseek")

#: Speaker attribution is a reasoning task; the reasoner follows a long
#: conversation far better than the chat model does.
SPEAKER_MODEL = "deepseek-reasoner"

#: Segments per request, and the character budget (whichever hits first).
BATCH_SEGMENTS = 45
BATCH_CHARS = 5000
#: Already-labelled lines shown before each batch, read-only.
CONTEXT_SEGMENTS = 6
#: More speakers than this in one recording is almost certainly a mistake.
MAX_SPEAKERS = 10

_SYSTEM_PROMPT = (
    "אתה מומחה בניתוח שיחות בעברית. מקבל תמלול אוטומטי של הקלטה, ללא סימון של מי "
    "דיבר. תפקידך לקבוע לכל מקטע מי הדובר, לפי התוכן בלבד.\n\n"
    "סימנים שאפשר להסתמך עליהם:\n"
    "— שאלה ואחריה תשובה: בדרך כלל דוברים שונים. תגובה קצרה כמו ״כן״, ״נכון״, ״בדיוק״, "
    "״אה, אוקיי״ היא כמעט תמיד של מי שלא דיבר ברצף הקודם.\n"
    "— פנייה בשם (״תודה דנה״, ״מה דעתך, יוסי?״): הפונה והנפנה הם אנשים שונים.\n"
    "— הצגה עצמית (״אני דנה״, ״קוראים לי…״) מזהה את הדובר הנוכחי.\n"
    "— מגדר ונטייה: ״הלכתי… אני עייפה״ מול ״אני עייף״ — לרוב שני דוברים שונים.\n"
    "— תפקיד קבוע: מראיין שואל, מרואיין עונה; מרצה מדבר ברצף ומשתתפים מפריעים בקצרה.\n"
    "— הפסקה ארוכה לפני מקטע (gap) מרמזת על חילופי דוברים. מקטע שממשיך משפט שנחתך "
    "במקטע הקודם הוא של אותו דובר.\n\n"
    "כללים:\n"
    "1. אל תמציא דוברים. השתמש במספר הקטן ביותר שמסביר את השיחה. הרצאה או מונולוג של "
    "אדם אחד — כל המקטעים לדובר 1.\n"
    "2. מזהי הדוברים הם מספרים בלבד: \"1\", \"2\", \"3\"… דובר חדש מקבל את המספר הבא.\n"
    "3. כשאי אפשר לדעת, שייך לדובר של המקטע הקודם. עדיף להשאיר דובר רצוף מאשר להחליף "
    "בלי סיבה.\n"
    "4. אל תשנה את הטקסט, אל תתקן, אל תסכם.\n"
    "5. מקטע שמכיל בבירור שני דוברים (למשל ״כן. ומה לגבי התקציב?״ שהחלק הראשון תשובה "
    "והשני שאלה חדשה) אפשר לפצל ב-splits. החלקים חייבים להיות בדיוק המילים של המקטע "
    "המקורי, לפי הסדר, בלי שינוי כלשהו.\n\n"
    'הקלט JSON: "speakers" (דוברים שזוהו עד כה ופרופיל קצר של כל אחד), '
    '"context_before" (מקטעים קודמים שכבר סומנו — להקשר בלבד), ו-"segments" (המקטעים '
    "לסימון: id, gap בשניות מסוף המקטע הקודם, text).\n"
    "פלט: JSON תקין בלבד, בלי טקסט נוסף ובלי ```, במבנה:\n"
    '{"speakers": [{"id": "1", "profile": "תיאור קצר: תפקיד/סגנון"}],\n'
    ' "labels": [{"id": 12, "speaker": "1"}],\n'
    ' "splits": [{"id": 14, "parts": [{"speaker": "1", "text": "..."}, '
    '{"speaker": "2", "text": "..."}]}]}\n'
    "חובה להחזיר label לכל מקטע ב-segments. splits רק כשבאמת צריך."
)

_NAMES_PROMPT = (
    "אתה מקבל תמלול של שיחה בעברית שבו כל מקטע מסומן במספר דובר. מצא את השמות האמיתיים "
    "של הדוברים — אבל רק כשהם נאמרו בבירור בתמלול עצמו:\n"
    "— הצגה עצמית: ״אני דנה״, ״קוראים לי יוסי״, ״כאן משה מ…״.\n"
    "— פנייה אל דובר אחר ותשובתו: ״תודה דנה״ ואז הדובר הבא עונה — הדובר הבא הוא דנה.\n"
    "אסור לנחש. אסור להסיק שם משם משפחה בהקשר אחר, משם של מי שמדברים עליו, או משם של "
    "מוצר. אם לא בטוח — אל תחזיר שם לדובר הזה.\n"
    "לכל שם חובה ציטוט (evidence): קטע טקסט קצר שמופיע בתמלול בדיוק, שמכיל את השם.\n"
    'פלט JSON בלבד: {"names": [{"speaker": "1", "name": "דנה", "evidence": "אני דנה"}]}\n'
    'אם אין שמות ברורים: {"names": []}'
)


ProgressCallback = Callable[[int, int], None]


# --------------------------------------------------------------------------
# Labelling
# --------------------------------------------------------------------------
def label_speakers(
    api_key: str,
    segments: list[Segment],
    *,
    model: str = SPEAKER_MODEL,
    expected_speakers: int | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[Segment]:
    """Return ``segments`` with a speaker on each (and splits where needed)."""
    if not segments:
        return segments

    work = [seg for seg in segments]
    profiles: dict[str, str] = {}
    labels: dict[int, str] = {}
    splits: dict[int, list[tuple[str, str]]] = {}
    batches = list(_batches(work))
    failed = 0

    for number, batch in enumerate(batches, start=1):
        if should_cancel is not None and should_cancel():
            break
        context = _context_before(work, batch[0].id, labels)
        first = next((i for i, s in enumerate(work) if s.id == batch[0].id), 0)
        previous_end = work[first - 1].end if first > 0 else None
        try:
            reply = _label_batch(
                api_key,
                batch,
                context,
                profiles,
                model=model,
                expected=expected_speakers,
                previous_end=previous_end,
            )
        except ModelError as exc:
            if exc.code is ErrorCode.DEEPSEEK_API_ERROR and not labels and number == 1:
                # Nothing worked and the first batch already failed: surface it
                # (bad key, no balance) instead of silently doing nothing.
                raise
            failed += 1
            log.warning("a speaker batch failed", extra={"batch": number, "error": str(exc)})
            continue
        for key, value in reply.labels.items():
            labels[key] = value
        splits.update(reply.splits)
        profiles.update(reply.profiles)
        if on_progress is not None:
            on_progress(number, len(batches))

    if not labels:
        raise ModelError(
            "DeepSeek returned no speaker labels", code=ErrorCode.DEEPSEEK_API_ERROR
        )

    result = _apply(work, labels, splits)
    log.info(
        "speakers labelled by DeepSeek",
        extra={
            "segments": len(segments),
            "speakers": len({s.speaker for s in result if s.speaker}),
            "splits": len(splits),
            "failed_batches": failed,
        },
    )
    return result


class _Reply:
    __slots__ = ("labels", "profiles", "splits")

    def __init__(self) -> None:
        self.labels: dict[int, str] = {}
        self.profiles: dict[str, str] = {}
        self.splits: dict[int, list[tuple[str, str]]] = {}


def _batches(segments: list[Segment]) -> list[list[Segment]]:
    out: list[list[Segment]] = []
    batch: list[Segment] = []
    chars = 0
    for segment in segments:
        length = len(segment.text)
        if batch and (len(batch) >= BATCH_SEGMENTS or chars + length > BATCH_CHARS):
            out.append(batch)
            batch, chars = [], 0
        batch.append(segment)
        chars += length
    if batch:
        out.append(batch)
    return out


def _context_before(
    segments: list[Segment], first_id: int, labels: dict[int, str]
) -> list[dict[str, Any]]:
    index = next((i for i, s in enumerate(segments) if s.id == first_id), 0)
    tail = segments[max(0, index - CONTEXT_SEGMENTS) : index]
    return [
        {"id": s.id, "speaker": labels.get(s.id), "text": s.text.strip()}
        for s in tail
        if labels.get(s.id) is not None
    ]


def build_payload(
    batch: list[Segment],
    context: list[dict[str, Any]],
    profiles: dict[str, str],
    *,
    previous_end: float | None,
    expected: int | None,
) -> str:
    rows: list[dict[str, Any]] = []
    last_end = previous_end
    for seg in batch:
        gap = None if last_end is None else round(max(0.0, seg.start - last_end), 1)
        row: dict[str, Any] = {"id": seg.id, "text": seg.text.strip()}
        if gap is not None:
            row["gap"] = gap
        rows.append(row)
        last_end = seg.end
    body: dict[str, Any] = {
        "speakers": [{"id": k, "profile": v} for k, v in sorted(profiles.items())],
        "context_before": context,
        "segments": rows,
    }
    if expected:
        body["expected_speaker_count"] = expected
    return json.dumps(body, ensure_ascii=False)


def _label_batch(
    api_key: str,
    batch: list[Segment],
    context: list[dict[str, Any]],
    profiles: dict[str, str],
    *,
    model: str,
    expected: int | None,
    previous_end: float | None,
) -> _Reply:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_payload(
                    batch, context, profiles, previous_end=previous_end, expected=expected
                ),
            },
        ],
        "stream": False,
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
    return parse_reply(content, batch, profiles)


_DIGITS = re.compile(r"\d+")


def normalise_speaker(value: object) -> str | None:
    """``"S2"``, ``"speaker_2"``, ``2`` and ``"דובר 2"`` all become ``"2"``."""
    match = _DIGITS.search(str(value))
    if match is None:
        return None
    number = int(match.group())
    if not 1 <= number <= MAX_SPEAKERS:
        return None
    return str(number)


def parse_reply(content: str, batch: list[Segment], known: dict[str, str]) -> _Reply:
    reply = _Reply()
    raw = _parse_json(content)
    if not isinstance(raw, dict):
        return reply
    originals = {seg.id: seg.text for seg in batch}

    for item in raw.get("speakers") or []:
        if isinstance(item, dict):
            speaker = normalise_speaker(item.get("id"))
            profile = str(item.get("profile") or "").strip()
            if speaker and profile:
                reply.profiles[speaker] = profile[:160]

    for item in raw.get("labels") or []:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        speaker = normalise_speaker(item.get("speaker"))
        if segment_id in originals and speaker:
            reply.labels[segment_id] = speaker

    for item in raw.get("splits") or []:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        original = originals.get(segment_id)
        parts = _valid_parts(original, item.get("parts"))
        if parts:
            reply.splits[segment_id] = parts
            # A split carries its own labels; the segment label is its first.
            reply.labels[segment_id] = parts[0][0]

    # A segment the model forgot inherits the speaker before it.
    previous: str | None = None
    for seg in batch:
        if seg.id in reply.labels:
            previous = reply.labels[seg.id]
        elif previous is not None:
            reply.labels[seg.id] = previous
    return reply


def _compact(text: str) -> str:
    return "".join(text.split())


def _valid_parts(original: str | None, parts: object) -> list[tuple[str, str]] | None:
    """Accept a split only if its parts are exactly the original words."""
    if original is None or not isinstance(parts, list) or len(parts) < 2:
        return None
    cleaned: list[tuple[str, str]] = []
    for part in parts:
        if not isinstance(part, dict):
            return None
        speaker = normalise_speaker(part.get("speaker"))
        text = " ".join(str(part.get("text") or "").split())
        if not speaker or not text:
            return None
        cleaned.append((speaker, text))
    if _compact(" ".join(t for _, t in cleaned)) != _compact(original):
        return None
    # Merge neighbours with the same speaker.
    merged: list[tuple[str, str]] = []
    for speaker, text in cleaned:
        if merged and merged[-1][0] == speaker:
            merged[-1] = (speaker, f"{merged[-1][1]} {text}")
        else:
            merged.append((speaker, text))
    return merged if len(merged) >= 2 else None


def _apply(
    segments: list[Segment],
    labels: dict[int, str],
    splits: dict[int, list[tuple[str, str]]],
) -> list[Segment]:
    out: list[Segment] = []
    last: str | None = None
    for seg in segments:
        parts = splits.get(seg.id)
        if parts:
            total = sum(max(1, len(text)) for _, text in parts)
            cursor = seg.start
            span = max(seg.end - seg.start, 0.0)
            for index, (speaker, text) in enumerate(parts):
                share = span * (max(1, len(text)) / total)
                end = seg.end if index == len(parts) - 1 else cursor + share
                out.append(
                    seg.model_copy(
                        update={
                            "text": text,
                            "start": cursor,
                            "end": end,
                            "speaker": speaker,
                            "words": [],
                            "original_text": None,
                        }
                    )
                )
                cursor = end
            last = parts[-1][0]
            continue
        chosen = labels.get(seg.id) or last
        if chosen:
            last = chosen
        out.append(seg.model_copy(update={"speaker": chosen}) if chosen else seg)
    for index, seg in enumerate(out):
        out[index] = seg.model_copy(update={"id": index})
    return out


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------
#: Lines that tend to carry a name: introductions, greetings, thanks, and being
#: addressed. Their neighbours are sent too, because the reply to "תודה דנה" is
#: what says who דנה is.
_NAME_CUE = re.compile(
    r"קוראים לי|שמי|השם שלי|אני\s+[\u0590-\u05FF]{2,}|כאן\s+[\u0590-\u05FF]{2,}|"
    r"נעים מאוד|תודה|שלום|היי|בוקר טוב|ערב טוב|מה דעתך|מה שלומך|בבקשה|ברוכים"
)
#: Characters of transcript sent for name finding. A name is said in a line or
#: two, so this is about choosing the right lines, not sending everything.
_NAME_BUDGET = 9000
_NAME_OPENING = 30


def name_context(segments: list[Segment], budget: int = _NAME_BUDGET) -> list[str]:
    """The lines worth reading for names: the opening, then every cue.

    Introductions are usually at the start, but a name can come up anywhere
    ("תודה יוסי" at the end of an interview), so a long recording is searched
    for cue lines instead of being cut off after the first few minutes.
    """
    labelled = [s for s in segments if s.speaker]
    chosen: list[int] = list(range(min(_NAME_OPENING, len(labelled))))
    seen = set(chosen)
    for index in range(_NAME_OPENING, len(labelled)):
        if _NAME_CUE.search(labelled[index].text):
            for near in (index - 1, index, index + 1):
                if 0 <= near < len(labelled) and near not in seen:
                    chosen.append(near)
                    seen.add(near)
    chosen.sort()

    lines: list[str] = []
    previous = -2
    for index in chosen:
        seg = labelled[index]
        row = f"[{seg.speaker}] {seg.text.strip()}"
        if budget - len(row) < 0:
            break
        if index != previous + 1 and lines:
            lines.append("…")
        lines.append(row)
        budget -= len(row)
        previous = index
    return lines


def suggest_names(
    api_key: str,
    segments: list[Segment],
    *,
    model: str = SPEAKER_MODEL,
    wanted: set[str] | None = None,
    taken: set[str] | None = None,
) -> dict[str, str]:
    """Real names for speakers, only where the transcript itself says them.

    Returns ``{speaker_id: name}``. A name is accepted when its evidence quote
    occurs in the transcript and contains the name — a model that guesses
    cannot produce a quote that is both real and contains its guess.

    ``wanted`` limits the search to speakers that still lack a real name (a name
    the user typed is never second-guessed), and ``taken`` lists names already
    in use so two speakers are never given the same one.
    """
    labelled = [s for s in segments if s.speaker]
    if not labelled:
        return {}
    ids = {s.speaker for s in labelled if s.speaker}
    targets = ids if wanted is None else ids & wanted
    if not targets:
        return {}

    lines = name_context(segments)
    header = "דוברים שצריך למצוא להם שם: " + ", ".join(sorted(targets)) + "\n\n"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _NAMES_PROMPT},
            {"role": "user", "content": header + "\n".join(lines)},
        ],
        "stream": False,
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = _post_with_retry(payload, headers)
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        return {}
    return parse_names(content, segments, wanted=targets, taken=taken)


_NAME_OK = re.compile(r"^[֐-׿A-Za-z][֐-׿A-Za-z'\"׳״\- ]{0,28}$")


def parse_names(
    content: str,
    segments: list[Segment],
    *,
    wanted: set[str] | None = None,
    taken: set[str] | None = None,
) -> dict[str, str]:
    raw = _parse_json(content)
    if not isinstance(raw, dict):
        return {}
    haystack = " ".join(" ".join(s.text.split()) for s in segments)
    speakers = {s.speaker for s in segments if s.speaker}
    names: dict[str, str] = {}
    used: set[str] = set(taken or ())
    for item in raw.get("names") or []:
        if not isinstance(item, dict):
            continue
        speaker = normalise_speaker(item.get("speaker"))
        name = " ".join(str(item.get("name") or "").split())
        evidence = " ".join(str(item.get("evidence") or "").split())
        if not speaker or speaker not in speakers or speaker in names:
            continue
        if wanted is not None and speaker not in wanted:
            continue
        if not name or not _NAME_OK.match(name) or name in used:
            continue
        if not evidence or evidence not in haystack or name not in evidence:
            log.info("rejected a speaker name without verifiable evidence")
            continue
        names[speaker] = name
        used.add(name)
    return names
