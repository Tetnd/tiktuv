"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import (
    routes_events,
    routes_export,
    routes_jobs,
    routes_media,
    routes_system,
    routes_transcript,
    static_files,
)
from .app_state import build_state, start_state, stop_state
from .config import Settings, get_settings
from .errors import TiktuvError
from .events import EventBus
from .events import bus as default_bus
from .logging_conf import configure_logging, get_logger

log = get_logger("main")


class Utf8JSONResponse(JSONResponse):
    """JSON responses that declare ``charset=utf-8`` explicitly.

    Starlette only appends a charset to ``text/*`` media types, so JSON goes
    out as a bare ``application/json``. RFC 8259 says JSON is always UTF-8 and
    most clients assume it — but some do not, and default to Latin-1, which
    turns every Hebrew character into mojibake. (Windows PowerShell 5.1's
    ``Invoke-RestMethod`` is one such client.) Declaring the charset is
    permitted and costs nothing.
    """

    media_type = "application/json"

    def init_headers(self, headers: Mapping[str, str] | None = None) -> None:
        super().init_headers(headers)
        self.raw_headers = [
            (b"content-type", b"application/json; charset=utf-8")
            if key.lower() == b"content-type"
            else (key, value)
            for key, value in self.raw_headers
        ]


def _is_loopback_peer(host: str) -> bool:
    """True when ``host`` is loopback, or is not a network address at all."""
    if host in ("localhost", ""):
        return True
    try:
        # IPv4-mapped IPv6 ("::ffff:127.0.0.1") normalises correctly here.
        address = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP: an in-process ASGI transport, not a socket peer.
        return True
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def create_app(settings: Settings | None = None, bus: EventBus | None = None) -> FastAPI:
    settings = settings or get_settings()
    bus = bus or default_bus

    configure_logging(
        level=settings.log_level,
        json_output=settings.log_json,
        log_dir=settings.logs_dir,
        redact=settings.redact_paths_in_errors,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        bus.bind_loop(asyncio.get_running_loop())
        state = build_state(settings, bus)
        application.state.tiktuv = state
        start_state(state)
        log.info(
            "backend ready",
            extra={
                "version": __version__,
                "host": settings.host,
                "port": settings.port,
                "data_dir": str(settings.data_dir),
            },
        )
        try:
            yield
        finally:
            stop_state(state)
            log.info("backend stopped")

    app = FastAPI(
        title="Tiktuv",
        version=__version__,
        description="Local-first Hebrew speech-to-text. Loopback only.",
        lifespan=lifespan,
        default_response_class=Utf8JSONResponse,
        # No docs in a shipped desktop app; they are an extra unauthenticated
        # surface on a port that is open to every local process.
        docs_url="/api/docs" if settings.log_level.upper() == "DEBUG" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.log_level.upper() == "DEBUG" else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Tiktuv-Token", "Range"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
    )

    @app.middleware("http")
    async def _reject_non_loopback(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Refuse any request that arrived from a routable network address.

        The socket is already bound to loopback (``Settings.host`` validates
        that), so this is defence in depth against a misconfiguration or a
        forwarding proxy that rewrites the peer.

        Only a peer that parses as an IP address is judged. A non-IP peer means
        there is no network socket at all — an in-process ASGI transport — and
        nothing to defend against, so it passes.
        """
        client = request.client
        if client is not None and not _is_loopback_peer(client.host):
            log.warning("rejected non-loopback request", extra={"client": client.host})
            return Utf8JSONResponse(
                status_code=403,
                content={
                    "code": "PATH_NOT_ALLOWED",
                    "message": "גישה נדחתה.",
                    "remedy": "היישום מקבל בקשות מהמחשב המקומי בלבד.",
                    "detail": "non-loopback client",
                    "context": {},
                },
            )
        return await call_next(request)

    @app.exception_handler(TiktuvError)
    async def _tiktuv_error_handler(request: Request, exc: TiktuvError) -> JSONResponse:
        log.warning(
            "request failed",
            extra={"path": request.url.path, "code": exc.code.value, "detail": exc.detail},
        )
        return Utf8JSONResponse(
            status_code=exc.http_status,
            content=exc.to_payload(redact=settings.redact_paths_in_errors),
        )

    for module in (
        routes_system,
        routes_media,
        routes_jobs,
        routes_transcript,
        routes_export,
        routes_events,
    ):
        app.include_router(module.router)

    # Serve the built UI when there is one, so Tiktuv can run as a standalone
    # local app with no desktop shell. Mounted last: the API routes above must
    # win over the static catch-all.
    static_files.mount_frontend(app, settings)

    return app


app = create_app
