"""N12 deploy tests — validate start-dashboard-v2.sh, plist, nginx /ws snippet.

All checks are file-based (no subprocesses that touch the live bot):
- Shell script: exists, has correct shebang, correct port, secret hygiene assertions
- Plist: valid XML, correct label, references start-dashboard-v2.sh, port 8601
- nginx /ws snippet: exists, has proxy_read_timeout 0, dedicated rate-limit zone,
  Upgrade/Connection headers
- Both files: do NOT reference forbidden secrets (MAINNET, ANTHROPIC, OANDA)
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

SCRIPT = REPO / "deploy" / "macos" / "start-dashboard-v2.sh"
PLIST  = REPO / "deploy" / "macos" / "com.ccbt.dashboard-v2.plist"
NGINX_SNIPPET = REPO / "deploy" / "nginx-ws-v2.conf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _script_text() -> str:
    return SCRIPT.read_text()


def _plist_text() -> str:
    return PLIST.read_text()


def _nginx_text() -> str:
    return NGINX_SNIPPET.read_text()


# ---------------------------------------------------------------------------
# start-dashboard-v2.sh tests
# ---------------------------------------------------------------------------

class TestStartScript:
    def test_file_exists(self) -> None:
        assert SCRIPT.exists(), f"Missing: {SCRIPT}"

    def test_shebang(self) -> None:
        first = SCRIPT.read_text().splitlines()[0]
        assert first.startswith("#!/"), f"Bad shebang: {first!r}"

    def test_port_8601(self) -> None:
        txt = _script_text()
        assert "8601" in txt, "Script must bind to port 8601 (distinct from Streamlit 8501)"

    def test_host_localhost_only(self) -> None:
        txt = _script_text()
        # Must bind to 127.0.0.1, not 0.0.0.0
        assert "127.0.0.1" in txt, "Script must bind to 127.0.0.1 (not 0.0.0.0)"
        assert "0.0.0.0" not in txt, "Script must NOT bind to 0.0.0.0"

    def test_activates_venv_dash(self) -> None:
        txt = _script_text()
        assert ".venv-dash" in txt, "Script must activate .venv-dash"

    def test_runs_uvicorn(self) -> None:
        txt = _script_text()
        assert "uvicorn" in txt, "Script must launch uvicorn"
        assert "api.main:app" in txt, "Script must reference api.main:app"

    def test_no_set_a_source_env(self) -> None:
        """Must NOT do 'set -a; source .env' — that exports ALL secrets."""
        txt = _script_text()
        # Reject the wholesale set -a / set -o allexport pattern
        forbidden_patterns = [
            r"set\s+-a",
            r"set\s+-o\s+allexport",
        ]
        for pat in forbidden_patterns:
            assert not re.search(pat, txt), (
                f"Script uses forbidden pattern {pat!r} which exports all .env secrets. "
                "Only allowlist CCBT_DASH_* + BOT_DATA_DIR."
            )

    def test_no_forbidden_secrets_exported(self) -> None:
        """Must not export MAINNET, ANTHROPIC, OANDA, TELEGRAM API keys."""
        txt = _script_text()
        forbidden = ["MAINNET_API_KEY", "MAINNET_SECRET_KEY", "ANTHROPIC_API_KEY",
                     "OANDA_API_TOKEN", "OANDA_ACCOUNT_ID", "TELEGRAM_BOT_TOKEN"]
        for secret in forbidden:
            # OK to appear in a comment, but not as an export= or variable assignment
            non_comment_lines = [
                line for line in txt.splitlines()
                if secret in line and not line.lstrip().startswith("#")
            ]
            assert not non_comment_lines, (
                f"Script must not export {secret}. Found in: {non_comment_lines}"
            )

    def test_unsets_socks_proxy(self) -> None:
        txt = _script_text()
        assert "CCBT_SOCKS_PROXY" in txt, "Script must explicitly unset CCBT_SOCKS_PROXY"
        assert "unset" in txt, "Script must call 'unset' on CCBT_SOCKS_PROXY"

    def test_asserts_web_dist_exists(self) -> None:
        txt = _script_text()
        # Must check for web/dist/index.html before starting
        assert "web/dist" in txt or "web/dist/index.html" in txt, (
            "Script must assert web/dist/index.html exists (bundle-freshness check)"
        )

    def test_fails_fast_if_port_bound(self) -> None:
        txt = _script_text()
        # Must have a port-bound check (lsof or nc or similar)
        assert any(tok in txt for tok in ["lsof", "nc -z", "ss -ltn"]), (
            "Script must fail fast if port 8601 is already bound (lsof / nc / ss)"
        )

    def test_allowlists_ccbt_dash_vars(self) -> None:
        txt = _script_text()
        # Must export at least CCBT_DASH_PORT and CCBT_DASH_TOKEN or reference them
        assert "CCBT_DASH_" in txt or "CCBT_DASH_TOKEN" in txt, (
            "Script must allowlist-export CCBT_DASH_* variables"
        )

    def test_token_required_when_not_localhost(self) -> None:
        """Script references assert_startup_safety or CCBT_DASH_TOKEN requirement."""
        txt = _script_text()
        assert "CCBT_DASH_TOKEN" in txt, (
            "Script must reference CCBT_DASH_TOKEN (required when not behind localhost)"
        )


# ---------------------------------------------------------------------------
# com.ccbt.dashboard-v2.plist tests
# ---------------------------------------------------------------------------

class TestPlist:
    def test_file_exists(self) -> None:
        assert PLIST.exists(), f"Missing: {PLIST}"

    def test_valid_xml(self) -> None:
        try:
            ET.fromstring(_plist_text())
        except ET.ParseError as exc:
            pytest.fail(f"Plist is not valid XML: {exc}")

    def test_label_is_dashboard_v2(self) -> None:
        txt = _plist_text()
        assert "com.ccbt.dashboard-v2" in txt, (
            "Plist Label must be 'com.ccbt.dashboard-v2' (not 'com.ccbt.dashboard')"
        )

    def test_does_not_collide_with_old_label(self) -> None:
        txt = _plist_text()
        # Must not use the old label
        root = ET.fromstring(txt)
        for i, key in enumerate(root[0]):
            if key.text == "Label":
                val = list(root[0])[i + 1]
                assert val.text == "com.ccbt.dashboard-v2", (
                    f"Label must be 'com.ccbt.dashboard-v2', got {val.text!r}"
                )
                break

    def test_references_start_dashboard_v2(self) -> None:
        txt = _plist_text()
        assert "start-dashboard-v2.sh" in txt, (
            "Plist must reference start-dashboard-v2.sh (not start-dashboard.sh)"
        )

    def test_log_paths_are_v2(self) -> None:
        txt = _plist_text()
        # Log files must be named differently from the old dashboard
        assert "dashboard-v2" in txt or "dashboard_v2" in txt, (
            "Plist log paths must be distinct from the old dashboard (use dashboard-v2)"
        )

    def test_run_at_load(self) -> None:
        txt = _plist_text()
        assert "RunAtLoad" in txt, "Plist must include <key>RunAtLoad</key>"

    def test_keep_alive(self) -> None:
        txt = _plist_text()
        assert "KeepAlive" in txt, "Plist must include <key>KeepAlive</key>"

    def test_throttle_interval(self) -> None:
        txt = _plist_text()
        assert "ThrottleInterval" in txt, "Plist must include <key>ThrottleInterval</key>"

    def test_working_directory(self) -> None:
        txt = _plist_text()
        assert "WorkingDirectory" in txt, "Plist must set WorkingDirectory"
        assert "ccbt" in txt, "WorkingDirectory must reference the ccbt project"


# ---------------------------------------------------------------------------
# nginx /ws snippet tests
# ---------------------------------------------------------------------------

class TestNginxWsSnippet:
    def test_file_exists(self) -> None:
        assert NGINX_SNIPPET.exists(), f"Missing: {NGINX_SNIPPET}"

    def test_ws_location_block(self) -> None:
        txt = _nginx_text()
        assert "location /ws" in txt, "nginx snippet must have 'location /ws' block"

    def test_proxy_read_timeout_zero(self) -> None:
        txt = _nginx_text()
        # proxy_read_timeout 0 keeps WebSocket connections alive indefinitely
        assert "proxy_read_timeout 0" in txt, (
            "nginx /ws location must set proxy_read_timeout 0 (WebSocket never times out)"
        )

    def test_upgrade_headers(self) -> None:
        txt = _nginx_text()
        assert "Upgrade" in txt, "nginx snippet must pass Upgrade header for WebSocket"
        assert "Connection" in txt, "nginx snippet must pass Connection header for WebSocket"

    def test_dedicated_rate_limit_zone(self) -> None:
        txt = _nginx_text()
        # Must define a separate zone (not re-use the dashboard zone) for WS connections
        assert "limit_req_zone" in txt or "zone=ws" in txt or "limit_conn_zone" in txt, (
            "nginx snippet must define a dedicated rate-limit zone for /ws"
        )

    def test_proxy_pass_to_8601(self) -> None:
        txt = _nginx_text()
        assert "8601" in txt, "nginx snippet must proxy to port 8601 (v2 backend)"

    def test_basic_auth_on_proxy(self) -> None:
        """Token or basic auth must be mentioned for the VPS path."""
        txt = _nginx_text()
        has_auth = (
            "auth_basic" in txt
            or "X-Dash-Token" in txt
            or "proxy_set_header X-Dash-Token" in txt
        )
        assert has_auth, (
            "nginx snippet must require basic auth or pass X-Dash-Token "
            "(localhost peer-check is vacuous behind nginx)"
        )

    def test_no_forbidden_secrets(self) -> None:
        txt = _nginx_text()
        forbidden = ["MAINNET", "ANTHROPIC_API_KEY", "OANDA_API_TOKEN", "TELEGRAM_BOT_TOKEN"]
        for secret in forbidden:
            assert secret not in txt, f"nginx snippet must not reference secret {secret!r}"


# ---------------------------------------------------------------------------
# FastAPI serves web/dist integration smoke test
# ---------------------------------------------------------------------------

class TestFastAPIServesBundle:
    def test_web_dist_index_exists(self) -> None:
        """Build artifact must exist — the start script checks this at launch."""
        assert (REPO / "web" / "dist" / "index.html").exists(), (
            "web/dist/index.html must exist (run: npm --prefix web run build)"
        )

    def test_api_health_200(self) -> None:
        """FastAPI /api/health returns 200 with the TestClient (no real DB needed)."""
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO))
        from api.main import app

        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_spa_index_served_at_root(self) -> None:
        """FastAPI must serve web/dist/index.html at GET /."""
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO))
        from api.main import app

        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/")
        assert resp.status_code == 200
        # Must be HTML, not JSON
        assert "text/html" in resp.headers.get("content-type", ""), (
            f"Expected HTML at /, got content-type={resp.headers.get('content-type')!r}"
        )
