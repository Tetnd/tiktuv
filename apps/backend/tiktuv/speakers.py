"""Speaker recognition: choosing a method and running it.

Two engines can tell speakers apart, and they work very differently:

``soniox``
    **Acoustic.** The audio goes to Soniox, which separates voices and returns
    who spoke when; those turns are aligned onto the transcript (splitting
    segments at hand-overs). This is the accurate method — it hears the people
    rather than guessing from their words — but it uploads the recording, so it
    is only ever used when the user chose it (or the transcript itself came from
    Soniox, which already uploaded it).

``deepseek``
    **Semantic.** Only the transcript text goes to DeepSeek, which reads the
    conversation and decides who is speaking. Nothing about the audio is needed,
    so it works after any local transcription, and it is already the privacy
    trade-off proofreading makes. Strong on roles (interview, call, meeting),
    weaker on similar speakers trading one-word replies.

``auto`` picks the privacy-preserving one that is possible: DeepSeek when a key
exists. Soniox is never chosen implicitly for a local transcript.

Either way, real names are only added when the transcript itself says them (see
:func:`tiktuv.diarization.deepseek.suggest_names`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .asr.soniox import SONIOX_OVERLAP_SECONDS, SONIOX_WINDOW_SECONDS, SonioxTranscriber
from .diarization import (
    SpeakerTurn,
    build_speaker_map,
    default_speaker_label,
    link_window_speakers,
)
from .diarization.deepseek import SPEAKER_MODEL, label_speakers, suggest_names
from .diarization.turns import apply_turns
from .errors import ErrorCode, ModelError
from .logging_conf import get_logger
from .media.audio import iter_windows
from .media.ffmpeg import FFmpegTools
from .schemas import Segment, SpeakerMethod, TranscriptAnalysis, TranscriptionSettings

log = get_logger("speakers")

ProgressCallback = Callable[[float, str], None]

#: "דובר 1" — the placeholder label, as opposed to a name someone chose or found.
_DEFAULT_LABEL = re.compile(r"^\s*דובר\s*\d+\s*$")


def is_default_label(name: str | None) -> bool:
    """True for ``"דובר N"`` (or an empty name): nobody has named this speaker."""
    return not name or bool(_DEFAULT_LABEL.match(name))


def carry_over_names(
    old_segments: list[Segment],
    old_names: dict[str, str],
    new_segments: list[Segment],
    new_names: dict[str, str],
) -> tuple[dict[str, str], int]:
    """Keep names people already gave when speakers are recognised again.

    Re-labelling renumbers speakers, so a new "1" need not be the old "1". Each
    new speaker is matched to the old speaker who spoke over the same stretches
    of the recording, and keeps that speaker's name when it is a real one (typed
    by the user or found earlier) and the match is clear. Returns the merged
    names and how many were carried over.
    """
    real = {k: v for k, v in old_names.items() if not is_default_label(v)}
    if not real:
        return new_names, 0

    overlap: dict[tuple[str, str], float] = {}
    new_total: dict[str, float] = {}
    for new in new_segments:
        if not new.speaker:
            continue
        new_total[new.speaker] = new_total.get(new.speaker, 0.0) + max(0.0, new.end - new.start)
        for old in old_segments:
            if not old.speaker or old.speaker not in real:
                continue
            lo, hi = max(new.start, old.start), min(new.end, old.end)
            if hi > lo:
                key = (new.speaker, old.speaker)
                overlap[key] = overlap.get(key, 0.0) + (hi - lo)

    merged = dict(new_names)
    used_old: set[str] = set()
    carried = 0
    for (new_id, old_id), seconds in sorted(overlap.items(), key=lambda kv: -kv[1]):
        if old_id in used_old or not is_default_label(merged.get(new_id)):
            continue
        # The old speaker must hold most of the new one, or it is a guess.
        if seconds < 0.6 * new_total.get(new_id, 0.0):
            continue
        merged[new_id] = real[old_id]
        used_old.add(old_id)
        carried += 1
    return merged, carried


@dataclass(frozen=True, slots=True)
class SpeakerOutcome:
    segments: list[Segment]
    #: ``{"1": "דובר 1"}`` — or a real name where one was found.
    speakers: dict[str, str]
    method: SpeakerMethod
    notes: list[str] = field(default_factory=list)

    @property
    def speaker_count(self) -> int:
        return len({s.speaker for s in self.segments if s.speaker})


def resolve_method(
    requested: SpeakerMethod, *, soniox_key: str | None, deepseek_key: str | None
) -> SpeakerMethod:
    """The method that will actually run, or an error saying what is missing."""
    if requested is SpeakerMethod.SONIOX:
        if not soniox_key:
            raise ModelError(
                "the Soniox API key has not been configured",
                code=ErrorCode.SONIOX_KEY_MISSING,
            )
        return SpeakerMethod.SONIOX
    if requested is SpeakerMethod.DEEPSEEK:
        if not deepseek_key:
            raise ModelError(
                "the DeepSeek API key has not been configured",
                code=ErrorCode.DEEPSEEK_KEY_MISSING,
            )
        return SpeakerMethod.DEEPSEEK
    # AUTO: never upload audio without being asked.
    if deepseek_key:
        return SpeakerMethod.DEEPSEEK
    raise ModelError(
        "speaker recognition needs a DeepSeek or Soniox API key",
        code=ErrorCode.DEEPSEEK_KEY_MISSING,
    )


def identify_speakers(
    segments: list[Segment],
    *,
    method: SpeakerMethod,
    settings: TranscriptionSettings,
    soniox_key: str | None,
    deepseek_key: str | None,
    media_path: Path | None = None,
    tools: FFmpegTools | None = None,
    duration: float = 0.0,
    sample_rate: int = 16000,
    suggest_names_too: bool = False,
    expected_speakers: int | None = None,
    deepseek_model: str = SPEAKER_MODEL,
    previous_names: dict[str, str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: ProgressCallback | None = None,
) -> SpeakerOutcome:
    """Label ``segments`` with speakers using ``method``.

    ``previous_names`` are the names the transcript had before: any real name
    (typed or found earlier) is carried over to the matching new speaker, and
    only speakers still unnamed are searched for names.
    """
    chosen = resolve_method(method, soniox_key=soniox_key, deepseek_key=deepseek_key)
    notes: list[str] = []

    def report(fraction: float, message: str) -> None:
        if on_progress is not None:
            on_progress(fraction, message)

    if chosen is SpeakerMethod.SONIOX:
        assert soniox_key is not None
        if media_path is None or tools is None or not media_path.is_file():
            raise ModelError(
                "the recording is no longer available, so its voices cannot be analysed",
                code=ErrorCode.MEDIA_CORRUPT,
            )
        report(0.02, "מזהה דוברים לפי הקול — Soniox")
        turns = soniox_turns_for_media(
            soniox_key,
            media_path,
            tools,
            settings,
            sample_rate=sample_rate,
            duration=duration,
            should_cancel=should_cancel,
            on_progress=lambda f: report(0.02 + 0.8 * f, "מזהה דוברים לפי הקול — Soniox"),
        )
        if not turns:
            raise ModelError(
                "Soniox did not find any speakers in the recording",
                code=ErrorCode.SONIOX_API_ERROR,
            )
        labelled = apply_turns(segments, turns)
    else:
        assert deepseek_key is not None
        report(0.02, "מזהה דוברים לפי התוכן — DeepSeek")
        labelled = label_speakers(
            deepseek_key,
            segments,
            model=deepseek_model,
            expected_speakers=expected_speakers,
            on_progress=lambda done, total: report(
                0.02 + 0.8 * done / max(1, total), "מזהה דוברים לפי התוכן — DeepSeek"
            ),
            should_cancel=should_cancel,
        )

    speakers = build_speaker_map(labelled)
    count = len(speakers)
    notes.append(f"זוהו {count} דוברים" if count != 1 else "זוהה דובר אחד")

    if previous_names:
        speakers, carried = carry_over_names(segments, previous_names, labelled, speakers)
        if carried:
            notes.append(f"נשמרו {carried} שמות שהיו קודם")

    if suggest_names_too and deepseek_key and count:
        report(0.9, "מחפש שמות של הדוברים")
        found = _find_names(labelled, speakers, deepseek_key, deepseek_model)
        speakers.update(found)
        notes.append(
            f"נמצאו שמות ל-{len(found)} דוברים" if found else "לא נאמרו שמות ברורים בהקלטה"
        )

    report(1.0, "זיהוי הדוברים הושלם")
    return SpeakerOutcome(segments=labelled, speakers=speakers, method=chosen, notes=notes)


def _find_names(
    segments: list[Segment],
    speakers: dict[str, str],
    deepseek_key: str,
    deepseek_model: str,
) -> dict[str, str]:
    """Names for the speakers that still carry a "דובר N" label. Never raises."""
    wanted = {sid for sid, label in speakers.items() if is_default_label(label)}
    if not wanted:
        return {}
    taken = {label for sid, label in speakers.items() if sid not in wanted}
    try:
        return suggest_names(
            deepseek_key, segments, model=deepseek_model, wanted=wanted, taken=taken
        )
    except ModelError as exc:
        log.warning("speaker names failed", extra={"error": str(exc)})
        return {}


def name_existing_speakers(
    segments: list[Segment],
    speakers: dict[str, str],
    *,
    deepseek_key: str,
    deepseek_model: str = SPEAKER_MODEL,
) -> tuple[dict[str, str], list[str]]:
    """Find names for speakers already separated, without re-recognising them.

    Names someone typed (anything but "דובר N") are left exactly as they are.
    """
    merged = {**build_speaker_map(segments), **speakers}
    found = _find_names(segments, merged, deepseek_key, deepseek_model)
    merged.update(found)
    notes = [f"נמצאו שמות ל-{len(found)} דוברים"] if found else ["לא נאמרו שמות ברורים בהקלטה"]
    return merged, notes


def drop_lone_speaker(
    segments: list[Segment], speakers: dict[str, str]
) -> tuple[list[Segment], dict[str, str]]:
    """A recording with one speaker needs no speaker labels.

    Tagging every line of a lecture "דובר 1" is noise. When only one speaker was
    found and nobody has a real name for them, the labels are removed; a named
    sole speaker keeps theirs, because then the name says something.
    """
    ids = {s.speaker for s in segments if s.speaker}
    if len(ids) != 1:
        return segments, speakers
    only = next(iter(ids))
    if not is_default_label(speakers.get(only)):
        return segments, speakers
    return [s.model_copy(update={"speaker": None}) for s in segments], {}


#: Hebrew one-letter prefixes that attach to the word after them: "לדובר 1".
_PREFIXES = "ובכלמשה"


def replace_label(text: str, old: str, new: str) -> str:
    """Replace a speaker label inside free text, only where it is that label.

    "דובר 1" must not be touched inside "דובר 12", and it should still be found
    behind a Hebrew prefix ("לדובר 1" becomes "לדנה"). Anything else — a word
    that merely ends with the label's letters — is left alone.
    """
    if not old or old == new or old not in text:
        return text
    pattern = re.compile(
        rf"(?<![\u0590-\u05FFA-Za-z0-9])([{_PREFIXES}]{{0,2}}){re.escape(old)}(?![0-9])"
    )
    return pattern.sub(lambda m: m.group(1) + new, text)


def relabel_analysis(
    analysis: TranscriptAnalysis, renames: dict[str, str]
) -> TranscriptAnalysis:
    """Rewrite speaker labels inside a stored analysis after speakers are renamed.

    The analysis was written with whatever the speakers were called then
    ("דובר 1"); once they have names it should say "דנה" everywhere — in the
    speaker summaries and quotes, in who a task is assigned to, and in the
    running text of the summary, key points and tasks — not point at a label
    that no longer exists anywhere else.
    """
    if not renames:
        return analysis

    def fix(label: str | None) -> str | None:
        return renames.get(label, label) if label else label

    def text(value: str) -> str:
        for old, new in renames.items():
            value = replace_label(value, old, new)
        return value

    return analysis.model_copy(
        update={
            "summary": text(analysis.summary),
            "key_points": [text(point) for point in analysis.key_points],
            "speaker_summaries": [
                s.model_copy(
                    update={"speaker": fix(s.speaker) or s.speaker, "summary": text(s.summary)}
                )
                for s in analysis.speaker_summaries
            ],
            "speaker_quotes": [
                q.model_copy(update={"speaker": fix(q.speaker) or q.speaker})
                for q in analysis.speaker_quotes
            ],
            "action_items": [
                a.model_copy(update={"assignee": fix(a.assignee), "task": text(a.task)})
                for a in analysis.action_items
            ],
        }
    )


def label_changes(
    previous: dict[str, str], current: dict[str, str]
) -> dict[str, str]:
    """``{old label: new label}`` for every speaker whose name changed.

    A speaker with no stored name was shown as "דובר N", so that is what the
    analysis called them.
    """
    changes: dict[str, str] = {}
    for speaker_id, name in current.items():
        new = name.strip()
        old = (previous.get(speaker_id) or default_label_for(speaker_id)).strip()
        if new and old and new != old:
            changes[old] = new
    return changes


def default_label_for(speaker_id: str) -> str:
    return f"דובר {speaker_id}" if speaker_id.isdigit() else speaker_id


def soniox_turns_for_media(
    api_key: str,
    media_path: Path,
    tools: FFmpegTools,
    settings: TranscriptionSettings,
    *,
    sample_rate: int,
    duration: float,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> list[SpeakerTurn]:
    """Speaker turns for a whole recording, numbered consistently end to end."""
    client = SonioxTranscriber(api_key)
    hop = SONIOX_WINDOW_SECONDS - SONIOX_OVERLAP_SECONDS
    expected = max(1, math.ceil(max(duration - SONIOX_OVERLAP_SECONDS, 1.0) / hop))

    turns_out: list[SpeakerTurn] = []
    seen: list[Segment] = []
    try:
        for window in iter_windows(
            media_path,
            tools,
            sample_rate=sample_rate,
            window_seconds=SONIOX_WINDOW_SECONDS,
            overlap_seconds=SONIOX_OVERLAP_SECONDS,
            total_duration=duration,
        ):
            if should_cancel is not None and should_cancel():
                break
            local = client.speaker_turns(window.samples, window.sample_rate, settings)
            absolute = [
                Segment(
                    id=index,
                    start=turn.start + window.start,
                    end=turn.end + window.start,
                    text="",
                    speaker=turn.speaker,
                )
                for index, turn in enumerate(local)
            ]
            # Soniox numbers speakers per request: match this window's "1" to
            # whoever was "1" in the audio both windows share.
            absolute = link_window_speakers(
                absolute,
                seen,
                overlap_start=window.start,
                overlap_end=window.start + window.overlap_lead,
            )
            cutoff = window.start + window.overlap_lead / 2 if window.index > 0 else 0.0
            for seg in absolute:
                if seg.end <= cutoff or not seg.speaker:
                    continue
                turns_out.append(
                    SpeakerTurn(max(seg.start, cutoff), seg.end, seg.speaker)
                )
            seen.extend(absolute)
            if on_progress is not None:
                on_progress(min(1.0, (window.index + 1) / expected))
    finally:
        client.unload()
    return turns_out


__all__ = [
    "SpeakerOutcome",
    "default_speaker_label",
    "identify_speakers",
    "carry_over_names",
    "drop_lone_speaker",
    "is_default_label",
    "label_changes",
    "replace_label",
    "name_existing_speakers",
    "relabel_analysis",
    "resolve_method",
    "soniox_turns_for_media",
]
