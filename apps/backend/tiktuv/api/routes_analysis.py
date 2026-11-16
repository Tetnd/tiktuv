"""AI analysis of a transcript (summary, key points, actions, entities, …)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ..analysis import generate_analysis
from ..errors import ErrorCode, ModelError, TiktuvError
from ..schemas import TranscriptAnalysis
from .deps import AuthDep, StateDep

router = APIRouter(prefix="/api/jobs/{job_id}/analysis", tags=["analysis"])


class AnalysisStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exists: bool


@router.get("", response_model=TranscriptAnalysis)
def get_analysis(job_id: str, state: StateDep, _auth: AuthDep) -> TranscriptAnalysis:
    analysis = state.transcripts.load_analysis(job_id)
    if analysis is None:
        raise TiktuvError("no analysis has been generated yet", code=ErrorCode.TRANSCRIPT_NOT_FOUND)
    return analysis


@router.post("", response_model=TranscriptAnalysis)
def create_analysis(job_id: str, state: StateDep, _auth: AuthDep) -> TranscriptAnalysis:
    transcript = state.transcripts.load(job_id)
    if transcript is None:
        raise TiktuvError("this job has no transcript", code=ErrorCode.TRANSCRIPT_NOT_FOUND)

    api_key = state.deepseek.get()
    if not api_key:
        raise ModelError(
            "the DeepSeek API key has not been configured",
            code=ErrorCode.DEEPSEEK_KEY_MISSING,
        )

    analysis = generate_analysis(api_key, transcript, transcript.speakers)
    state.transcripts.save_analysis(analysis)
    return analysis


@router.delete("", response_model=AnalysisStatus)
def delete_analysis(job_id: str, state: StateDep, _auth: AuthDep) -> AnalysisStatus:
    return AnalysisStatus(exists=state.transcripts.delete_analysis(job_id))
