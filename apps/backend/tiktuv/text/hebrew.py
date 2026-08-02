"""Hebrew text post-processing.

Nothing in this module reverses a string. Hebrew is stored, edited and
exported in **logical order** — the order the characters were spoken and typed
— and rendering is left to the Unicode bidirectional algorithm in the browser,
in Word, and (via an explicit shaping step) in the PDF exporter.

Reversing text would produce files that look right in one viewer and are
garbage in every other, and would break copy-paste and search entirely.
"""

from __future__ import annotations

import re
import unicodedata

from ..schemas import Segment

#: Right-to-left mark. Used sparingly, only where a trailing neutral character
#: would otherwise be pulled to the wrong side of an RTL run.
RLM = "‏"
LRM = "‎"

HEBREW_LETTERS = re.compile(r"[א-ת]")
LATIN_LETTERS = re.compile(r"[A-Za-z]")

#: Fillers people say in Hebrew speech. Removed only when the user asks.
FILLER_WORDS: frozenset[str] = frozenset(
    {
        "אה", "אהה", "אההה", "אמ", "אממ", "אמממ", "המ", "המם",
        "עמ", "אוו", "אווו", "כאילו", "יעני", "נו",
        "אוקיי", "אוקי",
        "um", "uh", "erm", "hmm", "like",
    }
)

#: Punctuation that closes a sentence in Hebrew text.
SENTENCE_END = ".!?׃…"

_MULTISPACE = re.compile(r"[ \t ]+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?׃…])")
_MISSING_SPACE_AFTER_PUNCT = re.compile(r"([,;:!?׃])(?=[^\s\d\"'\)\]])")
_REPEATED_PUNCT = re.compile(r"([,.!?])\1{2,}")
_SPACE_INSIDE_QUOTES = re.compile(r'"\s+([^"]*?)\s+"')


def has_hebrew(text: str) -> bool:
    return bool(HEBREW_LETTERS.search(text))


def is_rtl_dominant(text: str) -> bool:
    """True when Hebrew letters outnumber Latin ones."""
    return len(HEBREW_LETTERS.findall(text)) >= len(LATIN_LETTERS.findall(text))


def normalize_unicode(text: str) -> str:
    """NFC-normalise and strip characters that should never reach a file.

    Bidi *control* characters coming out of the model are removed: we control
    directionality through markup and paragraph properties, so stray embedded
    overrides only cause damage. The RLM/LRM *marks* we add ourselves are
    applied after this step.
    """
    text = unicodedata.normalize("NFC", text)
    return "".join(ch for ch in text if ch not in "‪‫‬‭‮⁦⁧⁨⁩")


def normalize_punctuation(text: str) -> str:
    """Tidy spacing and repeated punctuation without changing wording."""
    text = normalize_unicode(text)
    text = _MULTISPACE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    # Order matters: collapse runs like "????" *before* inserting spaces after
    # punctuation, or the run becomes "? ? ? ?" and can no longer be collapsed.
    text = _REPEATED_PUNCT.sub(r"\1", text)
    text = _MISSING_SPACE_AFTER_PUNCT.sub(r"\1 ", text)
    text = _SPACE_INSIDE_QUOTES.sub(r'"\1"', text)
    # Whisper likes to open a segment with a comma or a dangling dash.
    text = re.sub(r"^[\s,،\-–—]+", "", text)
    return text.strip()


def strip_fillers(text: str) -> str:
    """Remove filler words, then repair the spacing they leave behind."""
    kept: list[str] = []
    for token in text.split():
        bare = token.strip(".,!?;:\"'()[]…־-")
        if bare and bare.casefold() in FILLER_WORDS:
            continue
        kept.append(token)
    result = " ".join(kept)
    result = _SPACE_BEFORE_PUNCT.sub(r"\1", result)
    result = re.sub(r"^[\s,]+", "", result)
    return result.strip()


def protect_neutrals(text: str) -> str:
    """Pin a trailing neutral character to the Hebrew run it belongs to.

    A Hebrew sentence ending in ``.`` or ``?`` renders correctly on its own,
    but inside a mixed line — or beside a Latin word or a number — the neutral
    can jump to the visually wrong end. An RLM after it removes the ambiguity
    without altering the text's logical order.
    """
    if not text or not has_hebrew(text):
        return text
    if text[-1] in SENTENCE_END + ",;:)]\"'":
        return text + RLM
    return text


def wrap_bidi_numbers(text: str) -> str:
    """Keep a Latin/number run inside a Hebrew sentence reading left-to-right.

    Numbers and Latin words are already handled correctly by the bidi
    algorithm; the ambiguous case is a *neutral* between two such runs (for
    example ``version 3.5 build 2``). Marking the boundaries with LRM keeps
    the run intact without changing any character.
    """
    if not has_hebrew(text):
        return text

    def _mark(match: re.Match[str]) -> str:
        return f"{LRM}{match.group(0)}{LRM}"

    return re.sub(r"[A-Za-z0-9][A-Za-z0-9.\-_/:]*[A-Za-z0-9]", _mark, text)


def post_process_segment_text(
    text: str,
    *,
    preserve_fillers: bool,
    normalize: bool,
) -> str:
    text = normalize_unicode(text).strip()
    if not preserve_fillers:
        text = strip_fillers(text)
    if normalize:
        text = normalize_punctuation(text)
    return text


def post_process_segments(
    segments: list[Segment],
    *,
    preserve_fillers: bool,
    normalize: bool,
) -> list[Segment]:
    """Apply text post-processing, dropping segments that end up empty."""
    result: list[Segment] = []
    for segment in segments:
        text = post_process_segment_text(
            segment.text, preserve_fillers=preserve_fillers, normalize=normalize
        )
        if not text:
            continue
        result.append(segment.model_copy(update={"id": len(result), "text": text}))
    return result


def split_paragraphs(
    segments: list[Segment],
    *,
    silence_seconds: float = 2.5,
    max_paragraph_seconds: float = 90.0,
    use_silence: bool = True,
) -> list[list[Segment]]:
    """Group segments into paragraphs.

    A paragraph break is placed when there is a long gap between segments
    (a pause in speech), when the speaker changes, or when a paragraph has run
    long enough that a wall of text would be unreadable.
    """
    if not segments:
        return []

    paragraphs: list[list[Segment]] = []
    current: list[Segment] = [segments[0]]

    for previous, segment in zip(segments, segments[1:], strict=False):
        gap = segment.start - previous.end
        elapsed = segment.end - current[0].start
        speaker_changed = segment.speaker != previous.speaker

        long_pause = use_silence and gap >= silence_seconds
        ends_sentence = previous.text.rstrip().endswith(tuple(SENTENCE_END))

        if speaker_changed or long_pause or (elapsed >= max_paragraph_seconds and ends_sentence):
            paragraphs.append(current)
            current = [segment]
        else:
            current.append(segment)

    paragraphs.append(current)
    return paragraphs


def paragraph_text(paragraph: list[Segment]) -> str:
    return " ".join(segment.text.strip() for segment in paragraph if segment.text.strip()).strip()


def format_timestamp(seconds: float, *, separator: str = ",", hours: bool = True) -> str:
    """Format seconds as ``HH:MM:SS,mmm`` (SRT) or ``HH:MM:SS.mmm`` (VTT)."""
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    s = total_seconds % 60
    m = (total_seconds // 60) % 60
    h = total_seconds // 3600
    if hours or h:
        return f"{h:02d}:{m:02d}:{s:02d}{separator}{ms:03d}"
    return f"{m:02d}:{s:02d}{separator}{ms:03d}"


def format_clock(seconds: float) -> str:
    """Short ``[MM:SS]`` / ``[H:MM:SS]`` stamp for timestamped text exports."""
    seconds = max(0.0, seconds)
    total = int(seconds)
    h, m, s = total // 3600, (total // 60) % 60, total % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
