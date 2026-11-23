"""DeepSeek API-key management (the engine behind the AI analysis tabs)."""

from __future__ import annotations

import httpx
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


class DeepSeekBalanceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    total_balance: str
    granted_balance: str = "0"
    topped_up_balance: str = "0"


class DeepSeekBalance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_available: bool
    balances: list[DeepSeekBalanceItem]


@router.get("/balance", response_model=DeepSeekBalance)
def balance(state: StateDep, _auth: AuthDep) -> DeepSeekBalance:
    """Remaining DeepSeek credit, read live from DeepSeek's balance endpoint."""
    key = state.deepseek.get()
    if key is None:
        raise TiktuvError("DeepSeek key not configured", code=ErrorCode.INVALID_REQUEST)
    try:
        response = httpx.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=httpx.Timeout(15.0),
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TiktuvError(
            "could not read DeepSeek balance", code=ErrorCode.INVALID_REQUEST
        ) from exc
    return DeepSeekBalance(
        is_available=bool(data.get("is_available", False)),
        balances=[
            DeepSeekBalanceItem(
                currency=str(b.get("currency", "")),
                total_balance=str(b.get("total_balance", "0")),
                granted_balance=str(b.get("granted_balance", "0")),
                topped_up_balance=str(b.get("topped_up_balance", "0")),
            )
            for b in data.get("balance_infos", [])
        ],
    )
