"""AI analysis of transcripts, powered by DeepSeek.

One call turns a finished transcript into a structured :class:`TranscriptAnalysis`
— summary, key points, action items, named entities, and per-speaker summary
and quotes. DeepSeek is OpenAI-compatible, so this is a thin wrapper over its
``/chat/completions`` endpoint using :mod:`httpx`.

The generated JSON is defensive: the model is asked for a strict schema, and
anything malformed or missing falls back to an empty section rather than
failing the whole request.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import httpx

from .errors import ErrorCode, ModelError
from .logging_conf import get_logger
from .schemas import (
    ActionItem,
    NamedEntity,
    SpeakerQuote,
    SpeakerSummary,
    Transcript,
    TranscriptAnalysis,
)

log = get_logger("analysis")

DEEPSEEK_API_BASE = "https://api.deepseek.com"
#: Fast/cheap model, good enough for summarisation and extraction.
DEEPSEEK_MODEL = "deepseek-flash"

_SYSTEM_PROMPT = (
    "אתה מנתח תמלילי שיחות בעברית. החזר JSON תקין בלבד, ללא טקסט נוסף וללא "
    "סימון קוד (ללא ```), במבנה הבא בדיוק:\n"
    "{\n"
    '  "summary": "תקציר השיחה",\n'
    '  "key_points": ["נקודה מרכזית"],\n'
    '  "action_items": [{"task": "משימה", "assignee": "דובר 1" | null}],\n'
    '  "named_entities": [{"name": "שם", "type": "person|organization|location|date|other"}],\n'
    '  "speaker_summaries": [{"speaker": "דובר 1", "summary": "סיכום"}],\n'
    '  "speaker_quotes": [{"speaker": "דובר 1", "quotes": ["ציטוט"]}]\n'
    "}\n"
    "כתוב הכול בעברית. היה נאמן לנאמר ואל תמציא פרטים. אם אין דוברים מזוהים, "
    "השאר את speaker_summaries ו-speaker_quotes ריקים.\n"
    "עיצוב השדה summary: אל תכתוב גוש טקסט אחד. כתוב משפט פתיחה קצר שמתאר במה "
    "עוסקת השיחה, ואחריו פסקאות קצרות (2-3 משפטים כל אחת) המופרדות בשורה ריקה "
    "(\\n\\n). כשיש רשימה של נושאים או שלבים, כתוב אותם כשורות נפרדות שמתחילות "
    "ב-\"• \" (כל שורה מופרדת ב-\\n). שמור על משפטים קצרים וברורים."
)


def build_transcript_text(transcript: Transcript, speakers: dict[str, str]) -> str:
    """Render the transcript as text, with speaker labels where available."""
    lines: list[str] = []
    for segment in transcript.segments:
        text = segment.text.strip()
        if not text:
            continue
        speaker = segment.speaker
        label = speakers.get(speaker) if speaker else None
        lines.append(f"{label}: {text}" if label else text)
    return "\n".join(lines)


def generate_analysis(
    api_key: str,
    transcript: Transcript,
    speakers: dict[str, str],
    *,
    model: str = DEEPSEEK_MODEL,
) -> TranscriptAnalysis:
    """Run DeepSeek over the transcript and return a structured analysis."""
    text = build_transcript_text(transcript, speakers)
    if not text.strip():
        return TranscriptAnalysis(job_id=transcript.job_id, model=model)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"התמליל:\n\n{text}"},
        ],
        "stream": False,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = httpx.post(
            f"{DEEPSEEK_API_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=60.0, pool=30.0),
        )
    except httpx.HTTPError as exc:
        raise ModelError(
            f"could not reach DeepSeek: {exc}", code=ErrorCode.DEEPSEEK_API_ERROR
        ) from exc

    if response.status_code >= 400:
        raise ModelError(_api_detail(response), code=ErrorCode.DEEPSEEK_API_ERROR)

    content = ""
    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ModelError(
            "DeepSeek returned an unexpected response", code=ErrorCode.DEEPSEEK_API_ERROR
        ) from None

    return _parse_analysis(content, transcript, model)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SENTENCES_PER_PARAGRAPH = 2


def _format_summary(text: str) -> str:
    """Make sure the summary is broken into readable paragraphs.

    The model is asked to structure it, but if it still returns one block of
    text (no line breaks at all), split it into short paragraphs by sentence.
    """
    text = text.replace("\r\n", "\n").strip()
    if "\n" in text:
        # Already structured: just collapse runs of blank lines.
        return re.sub(r"\n{3,}", "\n\n", text)
    sentences = [s for s in _SENTENCE_END.split(text) if s]
    if len(sentences) <= _SENTENCES_PER_PARAGRAPH:
        return text
    paragraphs = [
        " ".join(sentences[i : i + _SENTENCES_PER_PARAGRAPH])
        for i in range(0, len(sentences), _SENTENCES_PER_PARAGRAPH)
    ]
    return "\n\n".join(paragraphs)


def _parse_analysis(content: str, transcript: Transcript, model: str) -> TranscriptAnalysis:
    raw = _parse_json(content)
    if not isinstance(raw, dict):
        raw = {}

    actions: list[ActionItem] = []
    for item in raw.get("action_items") or []:
        action = _parse_action(item)
        if action is not None:
            actions.append(action)

    entities: list[NamedEntity] = []
    for item in raw.get("named_entities") or []:
        entity = _parse_entity(item)
        if entity is not None:
            entities.append(entity)

    summaries: list[SpeakerSummary] = []
    for item in raw.get("speaker_summaries") or []:
        summary = _parse_speaker_summary(item)
        if summary is not None:
            summaries.append(summary)

    quotes: list[SpeakerQuote] = []
    for item in raw.get("speaker_quotes") or []:
        quote = _parse_speaker_quote(item)
        if quote is not None:
            quotes.append(quote)

    return TranscriptAnalysis(
        job_id=transcript.job_id,
        summary=_format_summary(str(raw.get("summary") or "")),
        key_points=[str(item) for item in raw.get("key_points") or [] if str(item).strip()],
        action_items=actions,
        named_entities=entities,
        speaker_summaries=summaries,
        speaker_quotes=quotes,
        model=model,
        generated_at=datetime.now(UTC),
    )


def _parse_action(item: object) -> ActionItem | None:
    if not isinstance(item, dict):
        return None
    task = str(item.get("task") or "").strip()
    if not task:
        return None
    assignee = item.get("assignee")
    return ActionItem(
        task=task,
        assignee=str(assignee).strip() if isinstance(assignee, str) and assignee.strip() else None,
    )


def _parse_entity(item: object) -> NamedEntity | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    entity_type = str(item.get("type") or "other").strip() or "other"
    return NamedEntity(name=name, type=entity_type)


def _parse_speaker_summary(item: object) -> SpeakerSummary | None:
    if not isinstance(item, dict):
        return None
    speaker = str(item.get("speaker") or "").strip()
    summary = str(item.get("summary") or "").strip()
    if not speaker or not summary:
        return None
    return SpeakerSummary(speaker=speaker, summary=summary)


def _parse_speaker_quote(item: object) -> SpeakerQuote | None:
    if not isinstance(item, dict):
        return None
    speaker = str(item.get("speaker") or "").strip()
    quotes = [str(q).strip() for q in item.get("quotes") or [] if str(q).strip()]
    if not speaker or not quotes:
        return None
    return SpeakerQuote(speaker=speaker, quotes=quotes)


def _parse_json(text: str) -> object:
    text = text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except ValueError:
        log.warning("could not parse DeepSeek JSON", extra={"content": text[:500]})
        return {}


def _api_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"DeepSeek returned HTTP {response.status_code}"
    message = data.get("error", {})
    if isinstance(message, dict):
        message = message.get("message") or data.get("message")
    return f"DeepSeek error: {message or response.status_code}"


__all__ = [
    "DEEPSEEK_API_BASE",
    "DEEPSEEK_MODEL",
    "build_transcript_text",
    "generate_analysis",
]
