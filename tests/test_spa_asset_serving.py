"""SPA asset-serving regression tests (Round 4 blocker fix).

These tests exercise the REAL spa_catch_all route in api.main — no mocks, no
tmp-only paths.  They cover the exact failure modes that caused the SPA blocker:

1. GET /assets/<actual built bundle filename> must return Content-Type containing
   'javascript' (NOT text/html).  A wrong MIME type caused the browser to refuse
   to execute the script module, silently breaking the whole SPA.

2. GET / must return HTML that contains window.__CCBT_TOKEN__ injection when
   CCBT_DASH_TOKEN is set in the environment.

3. GET /api/<nonexistent> must return 404 JSON and must NOT include the token
   value in the response body (token leak guard).

4. GET /ws/<bogus> must return 404 JSON and must NOT include the token value
   in the response body.

All tests use the real web/dist bundle that was built with 'npm run build'.
They will be skipped if the bundle is absent (CI without a build step) but
they MUST pass in any environment where the bundle is present.

Run: .venv-dash/bin/python -m pytest tests/test_spa_asset_serving.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_DIST = REPO / "web" / "dist"
_ASSETS = _DIST / "assets"

# ---------------------------------------------------------------------------
# Determine the actual built JS bundle filename (content-hashed by Vite).
# We pick the main JS entry file — the one that contains the React app.
# ---------------------------------------------------------------------------

def _find_main_js() -> str | None:
    """Return the filename of the main JS bundle under web/dist/assets/, or None."""
    if not _ASSETS.exists():
        return None
    for f in sorted(_ASSETS.iterdir()):
        if f.name.startswith("index-") and f.suffix == ".js" and ".map" not in f.name:
            return f"assets/{f.name}"
    return None


_MAIN_JS = _find_main_js()
_DIST_EXISTS = _DIST.exists() and (_DIST / "index.html").exists()

requires_dist = pytest.mark.skipif(
    not _DIST_EXISTS,
    reason="web/dist not built — run 'npm --prefix web run build' first",
)

requires_main_js = pytest.mark.skipif(
    _MAIN_JS is None,
    reason="No index-*.js found in web/dist/assets/ — bundle not built",
)


def _get_client(token: str | None = None):
    """Return a FastAPI TestClient with an optional CCBT_DASH_TOKEN override."""
    # Patch the env before importing so api.deps picks up the value.
    if token is not None:
        os.environ["CCBT_DASH_TOKEN"] = token
    elif "CCBT_DASH_TOKEN" in os.environ:
        del os.environ["CCBT_DASH_TOKEN"]

    # Re-import with updated env (module-level constant in api.deps)
    import importlib
    import api.deps as _deps
    import api.main as _main

    importlib.reload(_deps)
    importlib.reload(_main)

    from fastapi.testclient import TestClient
    return TestClient(_main.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Test 1 — JS bundle served with correct MIME type
# ---------------------------------------------------------------------------

class TestAssetMimeType:
    """Static assets must be served as JavaScript, not as text/html."""

    @requires_dist
    @requires_main_js
    def test_js_bundle_returns_javascript_content_type(self):
        """GET /assets/<bundle>.js must return a Content-Type containing 'javascript'."""
        client = _get_client(token=None)
        resp = client.get(f"/{_MAIN_JS}")
        assert resp.status_code == 200, (
            f"Expected 200 for /{_MAIN_JS}, got {resp.status_code}"
        )
        ct = resp.headers.get("content-type", "")
        assert "javascript" in ct, (
            f"Expected content-type to contain 'javascript' for /{_MAIN_JS}, got: {ct!r}\n"
            "This is the SPA blocker: the browser will refuse to execute a script "
            "served as text/html."
        )

    @requires_dist
    @requires_main_js
    def test_js_bundle_not_html(self):
        """The JS bundle must NOT be served as text/html (which breaks ES module loading)."""
        client = _get_client(token=None)
        resp = client.get(f"/{_MAIN_JS}")
        ct = resp.headers.get("content-type", "")
        assert "text/html" not in ct, (
            f"JS bundle /{_MAIN_JS} was served as text/html — SPA will be broken."
        )


# ---------------------------------------------------------------------------
# Test 2 — index.html token injection
# ---------------------------------------------------------------------------

class TestTokenInjection:
    """GET / must inject window.__CCBT_TOKEN__ when the token env var is set."""

    @requires_dist
    def test_root_returns_html_with_token_injection(self):
        """GET / must return HTML containing window.__CCBT_TOKEN__ when token is configured."""
        sentinel = "test-spa-token-abc123"
        client = _get_client(token=sentinel)
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text
        assert "__CCBT_TOKEN__" in body, (
            "GET / did not include window.__CCBT_TOKEN__ in the HTML body. "
            "The WS channel will be dead behind any token-gated deployment."
        )
        assert sentinel in body, (
            f"Token sentinel {sentinel!r} not found in GET / HTML body — token injection broken."
        )

    @requires_dist
    def test_root_no_token_env_no_injection(self):
        """GET / without CCBT_DASH_TOKEN set must still return valid HTML (no crash)."""
        client = _get_client(token=None)
        resp = client.get("/")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "html" in ct


# ---------------------------------------------------------------------------
# Test 3 — /api/<nonexistent> returns 404 JSON without token in body
# ---------------------------------------------------------------------------

class TestApiNotFound:
    """Unknown /api/* paths must return 404 JSON and never leak the token."""

    @requires_dist
    def test_api_nonexistent_returns_404(self):
        """GET /api/nonexistent_path_xyz must return HTTP 404."""
        client = _get_client(token=None)
        resp = client.get("/api/nonexistent_path_xyz")
        assert resp.status_code == 404, (
            f"Expected 404 for /api/nonexistent_path_xyz, got {resp.status_code}"
        )

    @requires_dist
    def test_api_nonexistent_returns_json(self):
        """GET /api/nonexistent_path_xyz must return JSON, not HTML."""
        client = _get_client(token=None)
        resp = client.get("/api/nonexistent_path_xyz")
        ct = resp.headers.get("content-type", "")
        assert "json" in ct, (
            f"Expected JSON content-type for /api/nonexistent_path_xyz, got: {ct!r}"
        )

    @requires_dist
    def test_api_nonexistent_does_not_leak_token(self):
        """404 response for /api/* must NOT include the token value in the body."""
        secret = "do-not-leak-this-token-xyz987"
        client = _get_client(token=secret)
        resp = client.get("/api/nonexistent_path_xyz")
        assert secret not in resp.text, (
            "SECURITY: token value leaked in /api/nonexistent_path_xyz response body!"
        )


# ---------------------------------------------------------------------------
# Test 4 — /ws/<bogus> returns 404 JSON without token in body
# ---------------------------------------------------------------------------

class TestWsNotFound:
    """Unknown /ws/* sub-paths must return 404 JSON and never leak the token."""

    @requires_dist
    def test_ws_bogus_returns_404(self):
        """GET /ws/bogus_path must return HTTP 404."""
        client = _get_client(token=None)
        resp = client.get("/ws/bogus_path")
        assert resp.status_code == 404, (
            f"Expected 404 for /ws/bogus_path, got {resp.status_code}"
        )

    @requires_dist
    def test_ws_bogus_returns_json(self):
        """GET /ws/bogus_path must return JSON, not HTML."""
        client = _get_client(token=None)
        resp = client.get("/ws/bogus_path")
        ct = resp.headers.get("content-type", "")
        assert "json" in ct, (
            f"Expected JSON content-type for /ws/bogus_path, got: {ct!r}"
        )

    @requires_dist
    def test_ws_bogus_does_not_leak_token(self):
        """404 response for /ws/* must NOT include the token value in the body."""
        secret = "do-not-leak-ws-token-abc456"
        client = _get_client(token=secret)
        resp = client.get("/ws/bogus_path")
        assert secret not in resp.text, (
            "SECURITY: token value leaked in /ws/bogus_path response body!"
        )
