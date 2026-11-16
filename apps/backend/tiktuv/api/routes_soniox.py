"""Soniox API-key management.

The key is stored locally in the data directory (see :mod:`tiktuv.credentials`)
and only ever leaves the machine inside ``Authorization`` headers to Soniox's
servers when a Soniox transcription runs. The API here only ever returns a
masked form of it.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ErrorCode, TiktuvError
from .deps import AuthDep, StateDep

router = APIRouter(prefix="/api/soniox", tags=["soniox"])


class SonioxStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    masked_key: str


class SetSonioxKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=512)


@router.get("/status", response_model=SonioxStatus)
def status(state: StateDep, _auth: AuthDep) -> SonioxStatus:
    return SonioxStatus(
        configured=state.credentials.get() is not None,
        masked_key=state.credentials.masked(),
    )


@router.put("/key", response_model=SonioxStatus)
def set_key(payload: SetSonioxKeyRequest, state: StateDep, _auth: AuthDep) -> SonioxStatus:
    key = payload.api_key.strip()
    if not key:
        raise TiktuvError("empty API key", code=ErrorCode.INVALID_REQUEST)
    state.credentials.set(key)
    return SonioxStatus(configured=True, masked_key=state.credentials.masked())


@router.delete("/key", response_model=SonioxStatus)
def clear_key(state: StateDep, _auth: AuthDep) -> SonioxStatus:
    state.credentials.clear()
    return SonioxStatus(configured=False, masked_key="")
