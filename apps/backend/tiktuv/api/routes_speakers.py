"""Recognise the speakers of a stored transcript, on demand.

A job recognises speakers as it runs when ``enable_diarization`` is on. This is
for everything else: transcripts made before that was possible, or with it off,
and the cases where the first attempt was wrong and the user wants the other
method. It is what the editor's "זיהוי דוברים" action calls.

The two methods differ in what leaves the machine, and the caller picks one
knowingly: ``deepseek`` sends the transcript text; ``soniox`` sends the
recording.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from ..diarization import build_speaker_map
from ..diarization.deepseek import SPEAKER_MODEL
from ..errors import ErrorCode, ModelError, TiktuvError
from ..logging_conf import get_logger
from ..schemas import IdentifySpeakersRequest, IdentifySpeakersResponse, SpeakerMethod
from ..speakers import identify_speakers, name_existing_speakers, relabel_analysis
from .deps import AuthDep, StateDep

log = get_logger("api.speakers")

router = APIRouter(prefix="/api/jobs/{job_id}/speakers", tags=["speakers"])


@router.post("", response_model=IdentifySpeakersResponse)
def run_identify_speakers(
    job_id: str,
    payload: IdentifySpeakersRequest,
    state: StateDep,
    _auth: AuthDep,
) -> IdentifySpeakersResponse:
    transcript = state.transcripts.load(job_id)
    if transcript is None:
        raise TiktuvError("this job has no transcript", code=ErrorCode.TRANSCRIPT_NOT_FOUND)
    if transcript.partial:
        raise TiktuvError(
            "the transcript is still being produced", code=ErrorCode.JOB_INVALID_STATE
        )

    job = state.jobs.get(job_id)
    soniox_key = state.credentials.get()
    deepseek_key = state.deepseek.get()

    media_path = None
    tools = None
    if payload.method.value == "soniox":
        media_path = state.jobs.get_working_path(job_id)
        tools = state.require_tools()

    # Unlike the pipeline stage, a failure here is the user's to see: they
    # asked for this directly, so the error reaches the UI.
    outcome = identify_speakers(
        transcript.segments,
        method=payload.method,
        settings=job.settings,
        soniox_key=soniox_key,
        deepseek_key=deepseek_key,
        media_path=media_path,
        tools=tools,
        duration=transcript.duration,
        sample_rate=state.settings.sample_rate,
        suggest_names_too=payload.suggest_names,
        expected_speakers=payload.expected_speakers,
        deepseek_model=SPEAKER_MODEL,
        # Names someone typed (or that were found before) survive a re-run.
        previous_names=transcript.speakers,
    )

    updated = transcript.model_copy(
        update={
            "segments": outcome.segments,
            "speakers": outcome.speakers,
            "updated_at": datetime.now(UTC),
        }
    )
    state.transcripts.save(updated)
    log.info(
        "speakers recognised on request",
        extra={
            "job_id": job_id,
            "method": outcome.method.value,
            "speakers": outcome.speaker_count,
        },
    )
    return IdentifySpeakersResponse(
        transcript=updated,
        method=outcome.method,
        speaker_count=outcome.speaker_count,
        notes=outcome.notes,
    )


@router.post("/names", response_model=IdentifySpeakersResponse)
def find_speaker_names(job_id: str, state: StateDep, _auth: AuthDep) -> IdentifySpeakersResponse:
    """Look for the real names of speakers who are still "דובר N".

    Speakers stay exactly as they are — nothing is re-recognised, no line is
    moved. Only speakers that lack a real name are searched, so a name the user
    typed is never overwritten. A name is used only when the transcript itself
    says it, with a quote to prove it. Anything written with the old labels (the
    AI analysis) is updated to the new names.
    """
    transcript = state.transcripts.load(job_id)
    if transcript is None:
        raise TiktuvError("this job has no transcript", code=ErrorCode.TRANSCRIPT_NOT_FOUND)
    if transcript.partial:
        raise TiktuvError(
            "the transcript is still being produced", code=ErrorCode.JOB_INVALID_STATE
        )
    if not any(segment.speaker for segment in transcript.segments):
        raise TiktuvError(
            "the transcript has no speakers yet — recognise them first",
            code=ErrorCode.INVALID_REQUEST,
        )
    deepseek_key = state.deepseek.get()
    if not deepseek_key:
        raise ModelError(
            "the DeepSeek API key has not been configured",
            code=ErrorCode.DEEPSEEK_KEY_MISSING,
        )

    before = {**build_speaker_map(transcript.segments), **transcript.speakers}
    names, notes = name_existing_speakers(
        transcript.segments, before, deepseek_key=deepseek_key, deepseek_model=SPEAKER_MODEL
    )
    renames = {before[k]: v for k, v in names.items() if before.get(k) and before[k] != v}

    updated = transcript.model_copy(
        update={"speakers": names, "updated_at": datetime.now(UTC)}
    )
    state.transcripts.save(updated)
    analysis = state.transcripts.load_analysis(job_id)
    if analysis is not None and renames:
        state.transcripts.save_analysis(relabel_analysis(analysis, renames))
    log.info("speaker names searched", extra={"job_id": job_id, "found": len(renames)})
    return IdentifySpeakersResponse(
        transcript=updated,
        method=SpeakerMethod.DEEPSEEK,
        speaker_count=len({s.speaker for s in updated.segments if s.speaker}),
        notes=notes,
    )
