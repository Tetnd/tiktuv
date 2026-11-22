"""AI analysis of a transcript (summary, key points, actions, entities, …)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ..analysis import generate_analysis
from ..errors import ErrorCode, ModelError, TiktuvError
from ..schemas import (
    TranscriptAnalysis,
    UpdateActionItemsRequest,
    UpdateKeyPointsRequest,
    UpdateNamedEntitiesRequest,
    UpdateSpeakerQuotesRequest,
    UpdateSpeakerSummariesRequest,
    UpdateSummaryRequest,
)
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


@router.put("/named-entities", response_model=TranscriptAnalysis)
def update_named_entities(
    job_id: str,
    body: UpdateNamedEntitiesRequest,
    state: StateDep,
    _auth: AuthDep,
) -> TranscriptAnalysis:
    """Replace the entity list of an existing analysis.

    The editor lets the user add, rename and remove entities; everything else
    in the analysis is left exactly as the model wrote it. Editing does not
    touch ``generated_at`` or ``model`` — no new generation happened.
    """
    analysis = state.transcripts.load_analysis(job_id)
    if analysis is None:
        raise TiktuvError(
            "no analysis has been generated yet", code=ErrorCode.TRANSCRIPT_NOT_FOUND
        )

    analysis.named_entities = body.named_entities
    state.transcripts.save_analysis(analysis)
    return analysis


@router.put("/key-points", response_model=TranscriptAnalysis)
def update_key_points(
    job_id: str,
    body: UpdateKeyPointsRequest,
    state: StateDep,
    _auth: AuthDep,
) -> TranscriptAnalysis:
    """Replace the key-point list of an existing analysis.

    The editor lets the user add, edit and remove key points; everything else
    in the analysis is left exactly as the model wrote it, and editing does not
    touch ``generated_at`` or ``model`` — no new generation happened.
    """
    analysis = state.transcripts.load_analysis(job_id)
    if analysis is None:
        raise TiktuvError(
            "no analysis has been generated yet", code=ErrorCode.TRANSCRIPT_NOT_FOUND
        )

    analysis.key_points = body.key_points
    state.transcripts.save_analysis(analysis)
    return analysis


@router.put("/speaker-quotes", response_model=TranscriptAnalysis)
def update_speaker_quotes(
    job_id: str,
    body: UpdateSpeakerQuotesRequest,
    state: StateDep,
    _auth: AuthDep,
) -> TranscriptAnalysis:
    """Replace the speaker-quote list of an existing analysis.

    The editor lets the user add, edit and remove quotes; everything else in
    the analysis is left exactly as the model wrote it, and editing does not
    touch ``generated_at`` or ``model`` — no new generation happened.
    """
    analysis = state.transcripts.load_analysis(job_id)
    if analysis is None:
        raise TiktuvError(
            "no analysis has been generated yet", code=ErrorCode.TRANSCRIPT_NOT_FOUND
        )

    analysis.speaker_quotes = body.speaker_quotes
    state.transcripts.save_analysis(analysis)
    return analysis


@router.put("/speaker-summaries", response_model=TranscriptAnalysis)
def update_speaker_summaries(
    job_id: str,
    body: UpdateSpeakerSummariesRequest,
    state: StateDep,
    _auth: AuthDep,
) -> TranscriptAnalysis:
    """Replace the speaker summaries of an existing analysis.

    The editor lets the user rewrite each speaker's summary; everything else in
    the analysis is left exactly as the model wrote it, and editing does not
    touch ``generated_at`` or ``model`` — no new generation happened.
    """
    analysis = state.transcripts.load_analysis(job_id)
    if analysis is None:
        raise TiktuvError(
            "no analysis has been generated yet", code=ErrorCode.TRANSCRIPT_NOT_FOUND
        )

    analysis.speaker_summaries = body.speaker_summaries
    state.transcripts.save_analysis(analysis)
    return analysis


@router.put("/action-items", response_model=TranscriptAnalysis)
def update_action_items(
    job_id: str,
    body: UpdateActionItemsRequest,
    state: StateDep,
    _auth: AuthDep,
) -> TranscriptAnalysis:
    """Replace the action-item list of an existing analysis.

    The editor lets the user add, edit and remove tasks; everything else in
    the analysis is left exactly as the model wrote it, and editing does not
    touch ``generated_at`` or ``model`` — no new generation happened.
    """
    analysis = state.transcripts.load_analysis(job_id)
    if analysis is None:
        raise TiktuvError(
            "no analysis has been generated yet", code=ErrorCode.TRANSCRIPT_NOT_FOUND
        )

    analysis.action_items = body.action_items
    state.transcripts.save_analysis(analysis)
    return analysis


@router.put("/summary", response_model=TranscriptAnalysis)
def update_summary(
    job_id: str,
    body: UpdateSummaryRequest,
    state: StateDep,
    _auth: AuthDep,
) -> TranscriptAnalysis:
    """Replace the summary of an existing analysis.

    The editor lets the user rewrite the summary; everything else in the
    analysis is left exactly as the model wrote it, and editing does not touch
    ``generated_at`` or ``model`` — no new generation happened.
    """
    analysis = state.transcripts.load_analysis(job_id)
    if analysis is None:
        raise TiktuvError(
            "no analysis has been generated yet", code=ErrorCode.TRANSCRIPT_NOT_FOUND
        )

    analysis.summary = body.summary
    state.transcripts.save_analysis(analysis)
    return analysis


@router.delete("", response_model=AnalysisStatus)
def delete_analysis(job_id: str, state: StateDep, _auth: AuthDep) -> AnalysisStatus:
    return AnalysisStatus(exists=state.transcripts.delete_analysis(job_id))
