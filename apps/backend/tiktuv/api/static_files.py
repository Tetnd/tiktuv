"""Serve the built frontend from the backend.

In the Tauri build the shell loads the UI from its own bundle and passes the
session token in over an IPC command. When Tiktuv runs as a standalone local
app — the desktop-shortcut launcher — there is no shell, so the backend serves
``apps/frontend/dist`` itself and injects the token into ``index.html``.

Injecting the token into the served page is safe here because:

* the socket is bound to loopback and a middleware rejects routable peers, and
* CORS is restricted to a fixed origin list, so a page on another origin cannot
  read this response even though it can request it.

If the frontend has not been built, this mounts nothing and the API still
works — the launcher checks for the build and tells the user how to make it.
"""

from __future__ import annotations

import html
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings
from ..logging_conf import get_logger

log = get_logger("api.static")

#: Where the Vite build lands, relative to this file in a source checkout.
_DIST_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "frontend" / "dist",
    Path(__file__).resolve().parents[4] / "apps" / "frontend" / "dist",
)


def find_frontend_dist(override: str | Path | None = None) -> Path | None:
    """Locate the built frontend, or ``None`` if it has not been built."""
    candidates = [Path(override)] if override else list(_DIST_CANDIDATES)
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


#: The frontend reads the token from this tag. See ``main.tsx``.
TOKEN_META_NAME = "tiktuv-token"


def _inject_runtime(index_html: str, settings: Settings) -> str:
    """Insert the connection details the UI needs, before its own bundle runs.

    A ``<meta>`` tag rather than an inline ``<script>``: ``index.html`` sets a
    Content-Security-Policy of ``script-src 'self'``, which blocks inline
    scripts outright — an injected script tag is silently never executed, the
    UI comes up with no token, and every request returns 401. Meta tags are not
    subject to ``script-src``, so this survives the strict policy instead of
    forcing us to weaken it with ``'unsafe-inline'`` or a nonce.

    No ``baseUrl`` is emitted: the page is served from the API's own origin, so
    relative URLs already resolve correctly.
    """
    token = html.escape(settings.auth_token, quote=True)
    snippet = f'<meta name="{TOKEN_META_NAME}" content="{token}" />'

    marker = "</head>"
    if marker in index_html:
        return index_html.replace(marker, f"    {snippet}\n{marker}", 1)
    return snippet + index_html


def mount_frontend(app: FastAPI, settings: Settings, dist: Path | None = None) -> bool:
    """Mount the built UI at ``/``. Returns False when there is no build."""
    # An explicitly supplied directory is still validated: mounting one without
    # an index.html would return True here and then 500 on every page load.
    dist = find_frontend_dist(dist) if dist is not None else find_frontend_dist()
    if dist is None:
        log.info("no frontend build found; serving the API only")
        return False

    index_path = dist / "index.html"

    @app.get("/", response_class=HTMLResponse)
    async def _index(request: Request) -> HTMLResponse:  # noqa: ARG001
        try:
            source = index_path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - build removed at runtime
            log.error("could not read index.html", extra={"error": str(exc)})
            return HTMLResponse(
                "<h1>" + html.escape("ממשק המשתמש אינו מותקן.") + "</h1>",
                status_code=500,
            )
        return HTMLResponse(
            _inject_runtime(source, settings),
            headers={"Cache-Control": "no-store"},
        )

    # response_model=None: the union return type is not a Pydantic field, and
    # FastAPI would otherwise try to build a response model from it.
    @app.get("/favicon.ico", response_model=None)
    async def _favicon() -> FileResponse | HTMLResponse:
        icon = dist / "favicon.ico"
        if icon.is_file():
            return FileResponse(icon)
        return HTMLResponse("", status_code=404)

    # Hashed asset filenames, so they can be cached hard.
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    log.info("serving the frontend", extra={"dist": str(dist)})
    return True
