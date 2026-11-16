"""DeepSeek API-key management (the engine behind the AI analysis tabs)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ErrorCode, TiktuvError
from .deps import AuthDep, StateDep

router = APIRouter(prefix="/api/deepseek", tags=["deepseek"])


class DeepSeekStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    masked_key: str


class SetDeepSeekKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=512)


@router.get("/status", response_model=DeepSeekStatus)
def status(state: StateDep, _auth: AuthDep) -> DeepSeekStatus:
    return DeepSeekStatus(
        configured=state.deepseek.get() is not None,
        masked_key=state.deepseek.masked(),
    )


@router.put("/key", response_model=DeepSeekStatus)
def set_key(payload: SetDeepSeekKeyRequest, state: StateDep, _auth: AuthDep) -> DeepSeekStatus:
    key = payload.api_key.strip()
    if not key:
        raise TiktuvError("empty API key", code=ErrorCode.INVALID_REQUEST)
    state.deepseek.set(key)
    return DeepSeekStatus(configured=True, masked_key=state.deepseek.masked())


@router.delete("/key", response_model=DeepSeekStatus)
def clear_key(state: StateDep, _auth: AuthDep) -> DeepSeekStatus:
    state.deepseek.clear()
    return DeepSeekStatus(configured=False, masked_key="")
