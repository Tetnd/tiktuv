"""Shared route dependencies: authentication and state access."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from ..app_state import AppState
from ..errors import ErrorCode, TiktuvError
from ..security import constant_time_equals

#: Endpoints reachable without the bearer token. ``/api/health`` exists so the
#: desktop shell can wait for the backend before it has read the token file.
PUBLIC_PATHS = frozenset({"/api/health"})


def get_state(request: Request) -> AppState:
    state: AppState | None = getattr(request.app.state, "tiktuv", None)
    if state is None:  # pragma: no cover - only if startup failed
        raise TiktuvError("application state is not initialised", code=ErrorCode.INTERNAL)
    return state


def require_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_tiktuv_token: Annotated[str | None, Header()] = None,
) -> None:
    """Validate the per-session bearer token.

    The token is generated at startup and handed to the frontend by the desktop
    shell. It stops any other local process — including a random web page in
    the user's browser — from driving the API, which can read and write files.
    """
    state = get_state(request)
    expected = state.settings.auth_token

    presented: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    elif x_tiktuv_token:
        presented = x_tiktuv_token.strip()

    if not presented or not constant_time_equals(presented, expected):
        raise TiktuvError("missing or invalid token", code=ErrorCode.UNAUTHORIZED)


StateDep = Annotated[AppState, Depends(get_state)]
AuthDep = Annotated[None, Depends(require_token)]
