"""Server-Sent Events stream for live progress.

SSE rather than WebSockets: the traffic is one-directional and it needs no
extra protocol handling in the desktop app, which reads the stream with a
plain HTTP client.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .deps import get_state, require_token

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Stream job progress until the client disconnects.

    ``EventSource`` cannot send headers, so the token is accepted as a query
    parameter here and validated the same way.
    """
    token = request.query_params.get("token")
    require_token(
        request,
        authorization=f"Bearer {token}" if token else request.headers.get("authorization"),
        x_tiktuv_token=request.headers.get("x-tiktuv-token"),
    )
    state = get_state(request)

    return StreamingResponse(
        state.bus.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Defensive: stops any intermediary from buffering the stream.
            "X-Accel-Buffering": "no",
        },
    )
