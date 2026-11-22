"""Word-level safety net for DeepSeek's Hebrew corrections.

The model is asked to fix transcription errors and nothing else, but a language
model asked to "correct" text will sometimes also reword it, swap a word for a
synonym, turn "שלוש" into "3", or flip "לא" into "כן". A length-ratio check
cannot see any of that. This module compares the original and the corrected
segment word by word and accepts each *change* only when it looks like what a
transcription error actually is:

* the same sounds, re-spelled — a homophone, a glued/split word, a missing
  letter — measured with a Hebrew-aware similarity that folds together letters
  that sound alike (ע/א, ט/ת, ק/כ/ח, ס/ש);
* never a different number, never a different script, never a bare insertion
  or deletion of words, never a large block of text.

Changes are judged individually, so one bad edit in a segment does not throw
away the good edits next to it: the rejected hunk keeps its original words.
Punctuation-only edits are always welcome.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from difflib import SequenceMatcher

#: A replaced block longer than this (either side) is a rewrite, not a fix.
MAX_HUNK_WORDS = 4

#: Minimum similarity between the old and new spelling of a changed block.
#: 0.5 admits a one-letter slip in a short word ("אם"/"עם") and rejects an
#: unrelated word ("לא"/"כן"), which is what a meaning-changing edit looks like.
MIN_SIMILARITY = 0.5

_NIQQUD = re.compile(r"[֑-ׇ]")
_FINAL_LETTERS = str.maketrans("ךםןףץ", "כמנפצ")
#: Letters that modern Hebrew speech does not distinguish, and so ASR confuses.
_PHONETIC = str.maketrans({"ע": "א", "ט": "ת", "ק": "כ", "ח": "כ", "ס": "ש"})
_VOCABULARY_SPLIT = re.compile(r"[\s,;:،]+")


def key(token: str) -> str:
    """A comparison key: no niqqud, punctuation or final-letter forms, lower-case."""
    text = _NIQQUD.sub("", token)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))
    return text.lower().translate(_FINAL_LETTERS)


def vocabulary_keys(vocabulary: str) -> frozenset[str]:
    """The user's own term list, as comparison keys."""
    return frozenset(
        k for k in (key(word) for word in _VOCABULARY_SPLIT.split(vocabulary)) if k
    )


def _scripts(text: str) -> frozenset[str]:
    scripts: set[str] = set()
    for ch in text:
        if ch.isalpha():
            scripts.add(unicodedata.name(ch, "UNKNOWN").split()[0])
    return frozenset(scripts)


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def similarity(a: str, b: str) -> float:
    """How alike two spellings are, counting sound-alike letters as equal."""
    plain = SequenceMatcher(None, a, b, autojunk=False).ratio()
    phonetic = SequenceMatcher(
        None, a.translate(_PHONETIC), b.translate(_PHONETIC), autojunk=False
    ).ratio()
    return max(plain, phonetic)


def _hunk_is_a_fix(old: list[str], new: list[str], vocabulary: Collection[str]) -> bool:
    """True when replacing ``old`` words with ``new`` words looks like a transcription fix."""
    if len(old) > MAX_HUNK_WORDS or len(new) > MAX_HUNK_WORDS:
        return False
    old_text = "".join(key(token) for token in old)
    new_text = "".join(key(token) for token in new)
    if not old_text or not new_text:
        # One side is bare punctuation. Swapping punctuation is fine; swapping
        # it for words (or words for it) is an insertion or a deletion.
        return not old_text and not new_text
    if _digits(old_text) != _digits(new_text):
        return False
    if _scripts(old_text) != _scripts(new_text):
        return False
    if old_text == new_text:
        # Only spacing or punctuation moved: a glued word split, or the reverse.
        return True
    if any(key(token) in vocabulary for token in new):
        # The user told us this spelling is right; trust it over sound-alike math.
        return True
    return similarity(old_text, new_text) >= MIN_SIMILARITY


def reconcile(original: str, corrected: str, vocabulary: Collection[str] = ()) -> str:
    """Return ``original`` with only the trustworthy parts of ``corrected`` applied."""
    old = original.split()
    new = corrected.split()
    matcher = SequenceMatcher(None, [key(t) for t in old], [key(t) for t in new], autojunk=False)

    result: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # Same words; take the corrected token so punctuation fixes land.
            result.extend(new[j1:j2])
        elif tag == "replace":
            fix = _hunk_is_a_fix(old[i1:i2], new[j1:j2], vocabulary)
            result.extend(new[j1:j2] if fix else old[i1:i2])
        elif tag == "delete":
            # Dropping a spoken word is never a fix; dropping stray punctuation is.
            result.extend(old[i1:i2] if any(key(t) for t in old[i1:i2]) else [])
        else:  # insert
            # Adding a word nobody said is never a fix; adding punctuation is.
            result.extend([] if any(key(t) for t in new[j1:j2]) else new[j1:j2])
    return " ".join(result)


__all__ = ["MAX_HUNK_WORDS", "MIN_SIMILARITY", "key", "reconcile", "similarity", "vocabulary_keys"]
