"""Measure how well DeepSeek proofreads Hebrew, using the real API.

Run from ``apps/backend``::

    $env:DEEPSEEK_API_KEY = "sk-..."
    ..\\..\\.venv\\Scripts\\python.exe scripts\\eval_proofread.py

It sends a small labelled sample through exactly the code the app uses
(``tiktuv.proofread``) and scores the result against the answers below:

* ``errors``  — sentences with a known transcription mistake. Scored as
  fixed (matches the gold text), missed (left unchanged) or wrong (changed,
  but not to the gold text).
* ``clean``   — sentences that are already right, several of them traps
  (spoken numbers, "לא", colloquial repetition). Any change is a false edit.

It also shows what DeepSeek *proposed* before the safety guard, so you can see
what the guard stopped. Only the sample text below is sent to DeepSeek.

``--dry-run`` replays the gold answers through a fake API to check the script
itself, without a key or any network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tiktuv import proofread  # noqa: E402
from tiktuv.proofread_guard import key  # noqa: E402
from tiktuv.schemas import Segment  # noqa: E402

VOCABULARY = "איכילוב, הרצליה"

#: (what the ASR heard, what was actually said)
ERRORS: list[tuple[str, str]] = [
    ("אני רוצה לדבר אל התקציב של השנה", "אני רוצה לדבר על התקציב של השנה"),
    ("הלכתי אם החברים לקולנוע אתמול", "הלכתי עם החברים לקולנוע אתמול"),
    ("הוא אמר שקל אחד צריך לעזור", "הוא אמר שכל אחד צריך לעזור"),
    ("הגענו לתל אביב ברכבת ה מהירה", "הגענו לתל אביב ברכבת המהירה"),
    ("אמרתי בוקרטוב לכולם בכניסה", "אמרתי בוקר טוב לכולם בכניסה"),
    ("זה היה יום מדהם ממש", "זה היה יום מדהים ממש"),
    ("הוא עובד בבית חולים איקלוב כבר שנים", "הוא עובד בבית חולים איכילוב כבר שנים"),
    ("היא ביקשה ממני לחשוב אל הנושא עוד קצת", "היא ביקשה ממני לחשוב על הנושא עוד קצת"),
    ("הוא רוצה לדעת קול פרט קטן בתוכנית", "הוא רוצה לדעת כל פרט קטן בתוכנית"),
    ("בוא נפגש מחר בבקר ב עשר", "בוא נפגש מחר בבוקר בעשר"),
    ("תסגרו בבקשה את הדלת אחרי כם", "תסגרו בבקשה את הדלת אחריכם"),
    ("היא קנתה סמלה חדשה לחתונה של אחותה", "היא קנתה שמלה חדשה לחתונה של אחותה"),
]

#: Already correct. Several are traps for an over-eager editor.
CLEAN: list[str] = [
    "הוא שילם שלוש מאות שקל על הכרטיס",
    "זה לא נכון בכלל",
    "יאללה בוא נלך כבר אני אני מת מרעב",
    "הבנתי שאת רוצה לעבור דירה",
    "אתם מוזמנים להצטרף אלינו לארוחת ערב בשבת",
    "הפגישה עם הלקוח תהיה ביום שלישי בעשר בבוקר",
    "אנחנו צריכים לשלוח את החשבונית לרואה החשבון",
    "המערכת לא עבדה כי השרת התרסק באמצע היום",
    "ביקרנו בירושלים ואכלנו בשוק מחנה יהודה",
    "אני מקווה שנצליח לסיים לפני סוף הרבעון",
    "היא סיפרה לי שהיא עובדת בחברת הייטק גדולה בהרצליה",
    "אני לא יודע אם זה נכון אבל שמעתי שהם סוגרים את הסניף",
    "הם החליטו להשקיע בפרויקט החדש למרות ההתנגדות",
    "אנחנו משתמשים ב-Slack כל יום ובזום פעם בשבוע",
    "אני חושב שצריך לקבל החלטה עכשיו כי אין לנו הרבה זמן",
    "הוא הגיע מאוחר כי הרכבת הייתה מעוכבת",
    "תכתוב לי מתי נוח לך ונתאם את זה",
    "היא אמרה שיש לה עשרים וחמש שנות ניסיון בתחום",
]


def _words(text: str) -> list[str]:
    return [key(token) for token in text.split() if key(token)]


def _same(a: str, b: str) -> bool:
    """Equal as words, ignoring punctuation and spacing noise."""
    return _words(a) == _words(b)


def _build() -> tuple[list[Segment], dict[int, str], dict[int, str]]:
    segments: list[Segment] = []
    gold: dict[int, str] = {}
    for heard, said in ERRORS:
        gold[len(segments)] = said
        segments.append(Segment(id=len(segments), start=0.0, end=1.0, text=heard))
    for text in CLEAN:
        gold[len(segments)] = text
        segments.append(Segment(id=len(segments), start=0.0, end=1.0, text=text))
    kinds = {i: ("error" if i < len(ERRORS) else "clean") for i in gold}
    return segments, gold, kinds


class _DryResponse:
    """A fake API that answers with the gold text, to test the script offline."""

    status_code = 200

    def __init__(self, gold_by_original: dict[str, str], request: dict) -> None:
        sent = json.loads(request["messages"][1]["content"].split("\n\n")[-1])
        corrections = [
            {"id": s["id"], "text": gold_by_original[s["text"]]}
            for s in sent["segments"]
            if gold_by_original[s["text"]] != s["text"]
        ]
        content = json.dumps({"corrections": corrections}, ensure_ascii=False)
        self._body = {"choices": [{"message": {"content": content}}]}

    def json(self) -> dict:
        return self._body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default=proofread.PROOFREAD_MODEL)
    parser.add_argument("--dry-run", action="store_true", help="use a fake API (no key needed)")
    parser.add_argument("--no-vocabulary", action="store_true", help="do not send the term list")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if args.dry_run:
        api_key = "dry-run"
    if not api_key.strip():
        print("Set DEEPSEEK_API_KEY first (or use --dry-run).", file=sys.stderr)
        return 2

    segments, gold, kinds = _build()
    originals = {s.id: s.text for s in segments}

    if args.dry_run:
        by_original = {originals[i]: gold[i] for i in gold}
        proofread.httpx.post = lambda url, **kw: _DryResponse(by_original, kw["json"])  # type: ignore[assignment]

    # Record what the model proposed, before the guard trimmed it.
    proposed: dict[str, str] = {}
    real_reconcile = proofread.reconcile

    def spy(original: str, corrected: str, vocabulary=()):  # type: ignore[no-untyped-def]
        proposed[" ".join(original.split())] = corrected
        return real_reconcile(original, corrected, vocabulary)

    proofread.reconcile = spy  # type: ignore[assignment]

    print(f"Model: {args.model} | {len(ERRORS)} error cases, {len(CLEAN)} clean cases\n")
    result, _changed = proofread.proofread_segments(
        api_key,
        segments,
        model=args.model,
        vocabulary="" if args.no_vocabulary else VOCABULARY,
    )

    fixed = missed = wrong = untouched = false_edits = blocked = 0
    for segment in result:
        i = segment.id
        before, after, want = originals[i], segment.text, gold[i]
        raw = proposed.get(" ".join(before.split()))
        if raw is not None and not _same(raw, after):
            blocked += 1
        if kinds[i] == "error":
            if _same(after, want):
                fixed += 1
                status = "FIXED "
            elif _same(after, before):
                missed += 1
                status = "MISSED"
            else:
                wrong += 1
                status = "WRONG "
        elif _same(after, before):
            untouched += 1
            status = ""
        else:
            false_edits += 1
            status = "FALSE EDIT"
        if status:
            print(f"[{status}] {before}\n          -> {after}\n          want: {want}")
            if raw is not None and not _same(raw, after):
                print(f"          model proposed (guard trimmed it): {raw}")

    print(
        f"\nErrors : {fixed}/{len(ERRORS)} fixed, {missed} missed, {wrong} changed wrongly"
        f"\nClean  : {untouched}/{len(CLEAN)} untouched, {false_edits} falsely edited"
        f"\nGuard  : trimmed or blocked {blocked} proposed edit(s)"
    )
    # What matters most: never damage correct text.
    return 1 if (false_edits or wrong) else 0


if __name__ == "__main__":
    raise SystemExit(main())
