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

IMPORTANT — no importlib.reload() in this file.
Using importlib.reload(api.deps) poisons the Depends() override identity for
any test module that imports api.deps before the reload: the FastAPI app holds
a reference to the original api.deps.verify_token Depends() object, but after
reload() the re-imported module is a DIFFERENT object — making the identity
check that FastAPI uses for dependency_overrides fail for every test that runs
after this one in the same process.

The correct pattern (see tests/test_round1_fixes.py:708-725) is to import
api.deps once and mutate its CCBT_DASH_TOKEN attribute directly, then restore
it in a finally block.  The spa_catch_all handler reads CCBT_DASH_TOKEN at
call-time via `from api.deps import CCBT_DASH_TOKEN` inside the handler body,
so a direct attribute assignment is picked up correctly.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Generator

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import api.deps as _deps_module  # imported once — never reloaded
from api.main import app  # same object used by all tests
from fastapi.testclient import TestClient

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


@contextlib.contextmanager
def _token_ctx(token: str | None) -> Generator[TestClient, None, None]:
    """Context manager: temporarily set api.deps.CCBT_DASH_TOKEN and yield a TestClient.

    Uses direct attribute mutation — NOT importlib.reload() — so the FastAPI
    Depends() identity stays intact for other test modules running in the same
    process.  Always restores the original value in the finally block.
    """
    original = _deps_module.CCBT_DASH_TOKEN
    _deps_module.CCBT_DASH_TOKEN = token
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        _deps_module.CCBT_DASH_TOKEN = original


def _get_client(token: str | None = None) -> TestClient:
    """Return a FastAPI TestClient with the given CCBT_DASH_TOKEN value set.

    IMPORTANT: This function does NOT restore the token after returning the
    client, so callers must use _token_ctx() when they need cleanup.  Use this
    helper only in tests that don't need guaranteed cleanup (i.e. tests that
    always pass token=None last, restoring the safe default).

    For tests with a non-None token, prefer _token_ctx() to guarantee cleanup.
    """
    _deps_module.CCBT_DASH_TOKEN = token
    return TestClient(app, raise_server_exceptions=False)


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
        with _token_ctx(sentinel) as client:
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
        with _token_ctx(secret) as client:
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
        with _token_ctx(secret) as client:
            resp = client.get("/ws/bogus_path")
        assert secret not in resp.text, (
            "SECURITY: token value leaked in /ws/bogus_path response body!"
        )


# ---------------------------------------------------------------------------
# Guard test — SPA tests must not poison api.deps for other test modules.
#
# This test runs AFTER all other SPA tests in the file (alphabetical class
# order: A < G < T < W < Z).  It verifies that api.deps.CCBT_DASH_TOKEN has
# been restored to None (the safe default), so that a default-order pytest
# run of test_spa_asset_serving + test_api_control (or test_trade_gate) does
# not see a stale token causing spurious 401s on unauthenticated control tests.
# ---------------------------------------------------------------------------

class TestZNoReloadPoisonGuard:
    """Ensure SPA tests leave api.deps in a clean state for downstream tests."""

    def test_ccbt_dash_token_is_none_after_spa_tests(self):
        """api.deps.CCBT_DASH_TOKEN must be None after all SPA tests complete.

        If this test fails it means one of the SPA tests set a non-None token
        and failed to restore it — the _token_ctx() context manager must be
        used for every test that sets a non-None token.
        """
        assert _deps_module.CCBT_DASH_TOKEN is None, (
            f"api.deps.CCBT_DASH_TOKEN was left as {_deps_module.CCBT_DASH_TOKEN!r} "
            "after SPA tests.  A prior test set a non-None token without restoring it. "
            "Use _token_ctx() for any test that needs a non-None token."
        )

    @requires_dist
    def test_control_endpoint_works_after_spa_tests(self):
        """POST /api/bots/*/mode must work without auth when token is None.

        This is a cross-module integration guard: verifies that no SPA test
        left the module in a state where all POST endpoints return 401.
        """
        from api.deps import get_roster
        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found — skip control endpoint guard")

        sym = next(iter(roster))
        import tempfile, os as _os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            with patch("api.routers.control._get_data_dir", return_value=tmp):
                client = _get_client(token=None)
                resp = client.post(
                    f"/api/bots/{sym}/mode",
                    json={"mode": "graceful_stop"},
                )
        assert resp.status_code == 200, (
            f"POST /api/bots/{sym}/mode returned {resp.status_code} after SPA tests "
            "— api.deps.CCBT_DASH_TOKEN was not restored to None. "
            "This is the reload-poison guard test described in the Round 5 blocker."
        )
