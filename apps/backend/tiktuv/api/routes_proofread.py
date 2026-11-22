"""Proofread a stored transcript with DeepSeek, on demand.

Jobs proofread themselves as they run (``enable_proofreading``). This route is
for the transcripts that did not: a job that ran before the key was set, one
where the stage was turned off, or one where the call failed. It is also what
the editor's "הגה מחדש" action calls.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from ..errors import ErrorCode, ModelError, TiktuvError
from ..logging_conf import get_logger
from ..proofread import PROOFREAD_MODEL, proofread_segments
from ..schemas import Transcript
from .deps import AuthDep, StateDep

log = get_logger("api.proofread")

router = APIRouter(prefix="/api/jobs/{job_id}/proofread", tags=["proofread"])


@router.post("", response_model=Transcript)
def run_proofread(job_id: str, state: StateDep, _auth: AuthDep) -> Transcript:
    transcript = state.transcripts.load(job_id)
    if transcript is None:
        raise TiktuvError("this job has no transcript", code=ErrorCode.TRANSCRIPT_NOT_FOUND)
    if transcript.partial:
        raise TiktuvError(
            "the transcript is still being produced", code=ErrorCode.JOB_INVALID_STATE
        )

    api_key = state.deepseek.get()
    if not api_key:
        raise ModelError(
            "the DeepSeek API key has not been configured",
            code=ErrorCode.DEEPSEEK_KEY_MISSING,
        )

    job = state.jobs.get(job_id)
    model = job.settings.proofread_model.strip() or PROOFREAD_MODEL

    # Unlike the pipeline stage, a failure here is the user's to see: they
    # asked for this one directly, so the error code reaches the UI.
    segments, changed = proofread_segments(
        api_key,
        transcript.segments,
        transcript.speakers,
        model=model,
        vocabulary=job.settings.vocabulary_prompt,
    )

    updated = transcript.model_copy(
        update={
            "segments": segments,
            "proofread_model": model,
            "proofread_corrections": transcript.proofread_corrections + changed,
            "updated_at": datetime.now(UTC),
        }
    )
    state.transcripts.save(updated)
    log.info("proofread on request", extra={"job_id": job_id, "corrected": changed})
    return updated
