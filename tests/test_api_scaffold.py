"""N0 — Scaffold & API Contract tests (TDD — written before implementation).

Tests:
1. App imports without error (api.main imports cleanly)
2. /api/health returns 200
3. /openapi.json lists every contracted endpoint path
4. Static openapi.json exists on disk and matches app-generated spec
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Make sure REPO is on sys.path for api/ imports
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Expected endpoint paths from the N0 contract
# ---------------------------------------------------------------------------
CONTRACTED_PATHS = {
    "/api/health",
    "/api/portfolio/summary",
    "/api/bots",
    "/api/bots/{symbol}",
    "/api/trades",
    "/api/equity",
    "/api/daily-pnl",
    "/api/ai/calibration",
    "/api/logs",
    # N9 endpoints
    "/api/close-reasons",
    "/api/trade-gate",
    "/api/risk",
    "/api/calendar",
    "/api/heatmap",
    # N8 bot control
    "/api/bots/{symbol}/mode",
    "/api/bots/mode/bulk",
}


def _get_client():
    """Create a TestClient for the FastAPI app."""
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


class TestAppImports:
    """App should import cleanly."""

    def test_api_main_importable(self):
        """api.main imports without error."""
        import api.main  # noqa: F401
        assert True

    def test_api_models_importable(self):
        """api.models imports without error."""
        import api.models  # noqa: F401
        assert True

    def test_api_deps_importable(self):
        """api.deps imports without error."""
        import api.deps  # noqa: F401
        assert True


class TestHealthEndpoint:
    """Basic health check must return 200."""

    def test_health_200(self):
        client = _get_client()
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_health_response_has_status(self):
        client = _get_client()
        resp = client.get("/api/health")
        data = resp.json()
        assert "status" in data


class TestOpenAPIContract:
    """/openapi.json must declare every contracted endpoint path."""

    def test_openapi_accessible(self):
        client = _get_client()
        resp = client.get("/openapi.json")
        assert resp.status_code == 200

    def test_openapi_lists_all_contracted_paths(self):
        client = _get_client()
        spec = client.get("/openapi.json").json()
        spec_paths = set(spec.get("paths", {}).keys())
        missing = CONTRACTED_PATHS - spec_paths
        assert not missing, (
            f"Contracted paths missing from OpenAPI spec: {sorted(missing)}"
        )

    def test_committed_openapi_json_exists(self):
        """A static openapi.json must be committed so codegen is reproducible."""
        static_path = REPO / "openapi.json"
        assert static_path.exists(), (
            "openapi.json not found in repo root. Run: python scripts/export_openapi.py"
        )

    def test_committed_openapi_matches_app(self):
        """Committed openapi.json must match what the app generates (drift check)."""
        static_path = REPO / "openapi.json"
        if not static_path.exists():
            pytest.skip("openapi.json not yet committed")
        client = _get_client()
        app_spec = client.get("/openapi.json").json()
        static_spec = json.loads(static_path.read_text())
        # Compare paths only (version/title may differ)
        assert set(app_spec.get("paths", {}).keys()) == set(
            static_spec.get("paths", {}).keys()
        ), "Committed openapi.json has different paths from running app — re-run export_openapi.py"
