"""Serving the built frontend and handing it the session token.

Regression cover for a bug that made the whole app unusable: the token was
injected as an inline ``<script>``, but ``index.html`` sets a CSP of
``script-src 'self'``, so the browser silently refused to run it. The UI loaded
with no token and every request came back 401 ("גישה נדחתה") — including file
uploads, which looked like a model problem rather than an auth one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tiktuv.api.static_files import (
    TOKEN_META_NAME,
    _inject_runtime,
    find_frontend_dist,
    mount_frontend,
)
from tiktuv.config import Settings

#: Mirrors the real index.html, including the strict policy that caused the bug.
INDEX_HTML = """<!doctype html>
<html lang="he" dir="rtl">
  <head>
    <meta charset="UTF-8" />
    <meta http-equiv="Content-Security-Policy"
          content="default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'" />
    <title>תכתוב</title>
    <script type="module" crossorigin src="/assets/index-abc123.js"></script>
  </head>
  <body><div id="root"></div></body>
</html>
"""


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    directory = tmp_path / "dist"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (directory / "assets" / "index-abc123.js").write_text("export default 1;", encoding="utf-8")
    return directory


class TestInjectRuntime:
    def test_injects_the_token(self, settings: Settings) -> None:
        result = _inject_runtime(INDEX_HTML, settings)
        assert settings.auth_token in result

    def test_uses_a_meta_tag_not_an_inline_script(self, settings: Settings) -> None:
        """The CSP blocks inline scripts, so the token must not travel in one."""
        result = _inject_runtime(INDEX_HTML, settings)
        assert f'name="{TOKEN_META_NAME}"' in result

        # No inline <script> may carry the token: that is exactly what the
        # browser refuses to execute.
        inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", result, re.S)
        for body in inline_scripts:
            assert settings.auth_token not in body

    def test_injected_before_head_closes(self, settings: Settings) -> None:
        result = _inject_runtime(INDEX_HTML, settings)
        assert result.index(TOKEN_META_NAME) < result.index("</head>")

    def test_does_not_disturb_the_module_script(self, settings: Settings) -> None:
        result = _inject_runtime(INDEX_HTML, settings)
        assert '<script type="module" crossorigin src="/assets/index-abc123.js">' in result

    def test_escapes_the_content_attribute(self, settings: Settings) -> None:
        hostile = settings.model_copy(update={"auth_token": 'a"><script>bad()</script>'})
        result = _inject_runtime(INDEX_HTML, hostile)
        assert "<script>bad()" not in result
        assert "&quot;" in result or "&gt;" in result

    def test_falls_back_when_there_is_no_head(self, settings: Settings) -> None:
        result = _inject_runtime("<div>no head here</div>", settings)
        assert settings.auth_token in result

    def test_leaves_the_csp_untouched(self, settings: Settings) -> None:
        """The fix must not have been 'weaken the policy'."""
        result = _inject_runtime(INDEX_HTML, settings)
        script_src = re.search(r"script-src([^;\"]*)", result)
        assert script_src is not None
        assert "'self'" in script_src.group(1)
        assert "unsafe-inline" not in script_src.group(1)
        assert "unsafe-eval" not in script_src.group(1)


class TestMountFrontend:
    def _client(self, settings: Settings, dist: Path) -> TestClient:
        app = FastAPI()
        assert mount_frontend(app, settings, dist) is True
        return TestClient(app)

    def test_serves_the_index(self, settings: Settings, dist: Path) -> None:
        response = self._client(settings, dist).get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_index_carries_a_usable_token(self, settings: Settings, dist: Path) -> None:
        body = self._client(settings, dist).get("/").text
        match = re.search(
            rf'<meta name="{TOKEN_META_NAME}" content="([^"]+)"', body
        )
        assert match is not None, "no token meta tag in the served page"
        assert match.group(1) == settings.auth_token

    def test_index_is_not_cached(self, settings: Settings, dist: Path) -> None:
        """A cached page would carry a token from a previous run."""
        response = self._client(settings, dist).get("/")
        assert response.headers.get("cache-control") == "no-store"

    def test_serves_assets(self, settings: Settings, dist: Path) -> None:
        response = self._client(settings, dist).get("/assets/index-abc123.js")
        assert response.status_code == 200

    def test_missing_favicon_is_404_not_an_error(self, settings: Settings, dist: Path) -> None:
        assert self._client(settings, dist).get("/favicon.ico").status_code == 404

    def test_mounting_is_skipped_without_a_build(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        app = FastAPI()
        assert mount_frontend(app, settings, tmp_path / "nope") is False

    def test_find_dist_returns_none_for_an_empty_directory(self, tmp_path: Path) -> None:
        assert find_frontend_dist(tmp_path) is None

    def test_find_dist_accepts_a_directory_with_an_index(self, dist: Path) -> None:
        assert find_frontend_dist(dist) == dist
