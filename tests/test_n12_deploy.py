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

    def test_abort_not_warn_on_empty_token_nonlocalhost(self) -> None:
        """When CCBT_DASH_HOST != 127.0.0.1 and CCBT_DASH_TOKEN is unset the script
        must call 'exit 1' — a WARNING is insufficient because the process starts
        anyway and exposes the unauthenticated API to network peers.

        We verify the shell source contains a conditional block that:
          1. Compares CCBT_DASH_HOST against 127.0.0.1 / localhost with != (not just
             the default-assignment line like CCBT_DASH_HOST="${CCBT_DASH_HOST:-127.0.0.1}"),
          2. AND contains 'exit 1' within that same guard block (not just in a
             different pre-flight check).
        """
        txt = _script_text()

        # Must have an inequality comparison of CCBT_DASH_HOST against 127.0.0.1/localhost
        # inside a conditional (i.e. [[ ... != ... ]] or [ ... != ... ]).
        # The default-assignment form "CCBT_DASH_HOST:-127.0.0.1" must NOT match.
        has_inequality_guard = bool(
            re.search(r'\[\[.*CCBT_DASH_HOST.*!=.*127', txt)
            or re.search(r'\[\[.*127.*!=.*CCBT_DASH_HOST', txt)
            or re.search(r'\[\[.*CCBT_DASH_HOST.*!=.*localhost', txt, re.IGNORECASE)
            or re.search(r'\[.*CCBT_DASH_HOST.*!=.*127', txt)
            or re.search(r'if.*\[.*CCBT_DASH_HOST.*!=.*127', txt)
        )
        assert has_inequality_guard, (
            "start-dashboard-v2.sh must include a conditional [[ $CCBT_DASH_HOST != 127.0.0.1 ]] "
            "(or similar) guard that aborts with exit 1 when the host is non-localhost and "
            "CCBT_DASH_TOKEN is empty.  The current code only WARNs and continues."
        )

        # Within the guard region, exit 1 must appear
        # (the existing exit 1 calls are for port-bound / venv-missing checks,
        # not for the token-missing-on-non-localhost case)
        # Find all if-blocks that reference CCBT_DASH_HOST != 127 AND CCBT_DASH_TOKEN
        has_token_exit = bool(
            re.search(
                r'CCBT_DASH_HOST[^\n]*!=.*\n(?:[^\n]*\n){0,8}.*CCBT_DASH_TOKEN[^\n]*\n(?:[^\n]*\n){0,5}.*exit 1',
                txt,
            )
            or re.search(
                r'CCBT_DASH_TOKEN[^\n]*\n(?:[^\n]*\n){0,3}.*exit 1',
                txt,
            )
        )
        assert has_token_exit, (
            "start-dashboard-v2.sh must call 'exit 1' when CCBT_DASH_TOKEN is empty "
            "on a non-localhost binding — currently it only emits a WARNING."
        )

    def test_token_exported_to_fastapi(self) -> None:
        """CCBT_DASH_TOKEN must be exported so FastAPI can read it from os.environ.

        The allowlist currently exports CCBT_DASH_HOST, CCBT_DASH_PORT,
        CCBT_DASH_POLL_S — but NOT CCBT_DASH_TOKEN.  Without an explicit
        'export CCBT_DASH_TOKEN' the variable is present in the parent shell but
        NOT passed to the uvicorn child process, so FastAPI token validation
        silently sees an empty string and accepts every request.
        """
        txt = _script_text()
        # Accept "export CCBT_DASH_TOKEN" on its own line, or
        # "export CCBT_DASH_TOKEN=..." assignment form.
        has_export = bool(re.search(r"export\s+CCBT_DASH_TOKEN", txt))
        assert has_export, (
            "start-dashboard-v2.sh must 'export CCBT_DASH_TOKEN' so uvicorn inherits it. "
            "The current allowlist omits it, so FastAPI token validation always sees ''."
        )

    def test_uvicorn_no_access_log_flag(self) -> None:
        """The uvicorn exec line must include --no-access-log.

        The WebSocket URL contains a ?token=<secret> query parameter.  Without
        --no-access-log, uvicorn writes the full URL — including the token — to
        stdout on every WS connect and disconnect.  On a Mac Mini that output is
        captured by launchd and written to the log file in plain text, leaking
        the session token to anyone with read access to the log.
        """
        txt = _script_text()
        assert "--no-access-log" in txt, (
            "start-dashboard-v2.sh uvicorn exec line must include --no-access-log.\n"
            "Without it, every WS connect logs the full ?token= URL to the\n"
            "launchd log file, leaking the session token in plain text."
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

    def test_ws_block_has_auth_basic(self) -> None:
        """The /ws location block itself must carry auth_basic — not just the /v2 block.

        A WebSocket upgrade request arrives as an HTTP GET to /ws *before* the
        Upgrade handshake.  nginx evaluates the enclosing location block for
        auth_basic — if it's absent there, an unauthenticated browser can open
        an unlimited number of WS connections bypassing the .htpasswd gate.
        Passing X-Dash-Token downstream is not a substitute: the token is
        validated only by the FastAPI app, which the nginx layer never reaches
        if the connection is dropped upstream first.  auth_basic must appear
        *inside* the /ws block (not just /v2).
        """
        txt = _nginx_text()

        # Extract only the text of the /ws location block.
        # Strategy: find "location /ws {" then collect lines until the matching "}"
        in_ws_block = False
        brace_depth = 0
        ws_block_lines: list[str] = []
        for line in txt.splitlines():
            stripped = line.strip()
            if not in_ws_block:
                if re.match(r"location\s+/ws\b", stripped):
                    in_ws_block = True
                    brace_depth = stripped.count("{") - stripped.count("}")
                    ws_block_lines.append(line)
            else:
                ws_block_lines.append(line)
                brace_depth += stripped.count("{") - stripped.count("}")
                if brace_depth <= 0:
                    break

        assert ws_block_lines, "Could not locate 'location /ws' block in nginx snippet"
        ws_block = "\n".join(ws_block_lines)

        assert "auth_basic" in ws_block, (
            "The /ws location block must contain 'auth_basic' — "
            "WebSocket upgrade requests must be gated by the same .htpasswd "
            "as /v2, otherwise any unauthenticated client can open WS connections."
        )

    def test_no_forbidden_secrets(self) -> None:
        txt = _nginx_text()
        forbidden = ["MAINNET", "ANTHROPIC_API_KEY", "OANDA_API_TOKEN", "TELEGRAM_BOT_TOKEN"]
        for secret in forbidden:
            assert secret not in txt, f"nginx snippet must not reference secret {secret!r}"

    def test_no_access_log_off_on_ws(self) -> None:
        """The /ws location must have 'access_log off' — /ws?token= would leak the
        token to the access log on every connect otherwise."""
        txt = _nginx_text()
        ws_block = _extract_location_block(txt, r"location\s+/ws\b")
        assert ws_block, "Could not locate 'location /ws' block"
        assert "access_log off" in ws_block, (
            "The /ws location must set 'access_log off'. "
            "The WS URL includes ?token=<secret> — writing it to the access log on every "
            "connect would leak the token to the launchd/system log file."
        )


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


# ---------------------------------------------------------------------------
# Deployment topology tests — verify the nginx /v2 + root-asset topology is
# correct so that the SPA loads in production (blank-page regression guard).
#
# Root cause: the Vite bundle emits root-absolute asset URLs (/assets/...,
# /favicon.svg) not prefixed with /v2.  The browser fetches them directly at
# root, bypassing the /v2 location.  Without explicit /assets/ + /favicon.svg
# location blocks in nginx the assets 404 and the SPA loads a blank page.
# ---------------------------------------------------------------------------

class TestNginxDeploymentTopology:
    """nginx /v2 + root-asset topology must be correct to prevent blank SPA."""

    def test_assets_location_exists(self) -> None:
        """nginx must have a root-level /assets/ location block.

        The Vite bundle emits <script src="/assets/index-HASH.js"> with a
        root-absolute path.  The browser fetches /assets/... directly — NOT
        /v2/assets/... — so the /v2 location block (with its /v2 prefix rewrite)
        never handles these requests.  A root /assets/ location is required.
        """
        txt = _nginx_text()
        assert re.search(r"location\s+/assets/", txt), (
            "deploy/nginx-ws-v2.conf must have a 'location /assets/' block.\n"
            "The Vite bundle uses root-absolute asset URLs (/assets/...) so the\n"
            "browser fetches them at root, not at /v2/assets/.  Without this\n"
            "location block assets 404 and the SPA shows a blank page."
        )

    def test_favicon_location_exists(self) -> None:
        """nginx must have a root-level location for /favicon.svg.

        Vite's default config emits <link rel='icon' href='/favicon.svg'> —
        a root-absolute path that the browser fetches at root, not /v2/favicon.svg.
        """
        txt = _nginx_text()
        assert re.search(r"location\s+=\s*/favicon\.svg", txt) or re.search(r"location\s+/favicon", txt), (
            "deploy/nginx-ws-v2.conf must have a root-level location for /favicon.svg.\n"
            "Vite emits href='/favicon.svg' (root-relative) in index.html — the\n"
            "browser fetches it at root, not /v2/favicon.svg."
        )

    def test_icons_location_exists(self) -> None:
        """nginx must have a root-level location for /icons.svg."""
        txt = _nginx_text()
        assert re.search(r"location\s+=\s*/icons\.svg", txt) or re.search(r"location\s+/icons", txt), (
            "deploy/nginx-ws-v2.conf must have a root-level location for /icons.svg."
        )

    def test_assets_location_proxies_to_v2_backend(self) -> None:
        """The /assets/ location must proxy to the same v2 backend (port 8601).

        If /assets/ is not proxied to ccbt_v2_backend the JS bundle request
        returns 404 (or the wrong server) and the SPA never loads.
        """
        txt = _nginx_text()

        # Extract only the /assets/ location block text
        assets_block = _extract_location_block(txt, r"location\s+/assets/")
        assert assets_block, (
            "Could not locate 'location /assets/' block — add it to nginx-ws-v2.conf"
        )
        assert "ccbt_v2_backend" in assets_block or "8601" in assets_block, (
            "/assets/ location must proxy_pass to ccbt_v2_backend (port 8601)."
        )

    def test_assets_location_has_auth_basic(self) -> None:
        """The root /assets/ location must carry auth_basic (same gate as /v2).

        Without auth_basic on /assets/ an unauthenticated client can fetch the
        entire JS bundle by requesting /assets/index-HASH.js directly, bypassing
        the /v2 password gate.
        """
        txt = _nginx_text()
        assets_block = _extract_location_block(txt, r"location\s+/assets/")
        assert assets_block, "Could not locate 'location /assets/' block"
        assert "auth_basic" in assets_block, (
            "/assets/ location must include 'auth_basic' — otherwise the Vite\n"
            "JS bundle is publicly accessible at /assets/index-HASH.js regardless\n"
            "of the /v2 basic-auth gate."
        )

    def test_v2_rewrite_strips_prefix(self) -> None:
        """The /v2 location must strip the /v2 prefix via rewrite before proxying.

        Without the prefix strip, FastAPI sees /v2/ instead of / and the
        spa_catch_all handler cannot match the path to web/dist/index.html.
        """
        txt = _nginx_text()
        v2_block = _extract_location_block(txt, r"location\s+/v2\b")
        assert v2_block, "Could not locate 'location /v2' block"
        # Must have a rewrite rule that strips /v2
        has_rewrite = bool(
            re.search(r"rewrite\s+\^/v2", v2_block)
        )
        assert has_rewrite, (
            "/v2 location must rewrite the /v2 prefix away before proxying.\n"
            "FastAPI's spa_catch_all serves paths relative to web/dist/ with no\n"
            "prefix, so nginx must strip /v2 before forwarding."
        )

    def test_api_location_exists(self) -> None:
        """nginx must have a root-level /api/ location block.

        The Vite SPA makes REST calls to root-absolute paths like /api/bots.
        Without this block those requests hit nginx root and either 404 or
        fall through to the legacy Streamlit proxy (which returns HTML, not JSON).
        """
        txt = _nginx_text()
        assert re.search(r"location\s+/api/", txt), (
            "deploy/nginx-ws-v2.conf must have a 'location /api/' block.\n"
            "The SPA fetches /api/bots, /api/health, /api/portfolio at root.\n"
            "Without this block they 404 or fall through to legacy Streamlit."
        )

    def test_api_location_proxies_to_v2_backend(self) -> None:
        """/api/ location must proxy to ccbt_v2_backend (port 8601)."""
        txt = _nginx_text()
        api_block = _extract_location_block(txt, r"location\s+/api/")
        assert api_block, (
            "Could not locate 'location /api/' block — add it to nginx-ws-v2.conf"
        )
        assert "ccbt_v2_backend" in api_block or "8601" in api_block, (
            "/api/ location must proxy_pass to ccbt_v2_backend (port 8601)."
        )

    def test_api_location_has_auth_basic(self) -> None:
        """/api/ location must carry auth_basic — same gate as /v2 and /assets/.

        Without auth_basic, unauthenticated clients can call any REST endpoint
        directly (e.g. GET /api/bots) bypassing the nginx password gate.
        """
        txt = _nginx_text()
        api_block = _extract_location_block(txt, r"location\s+/api/")
        assert api_block, "Could not locate 'location /api/' block"
        assert "auth_basic" in api_block, (
            "/api/ location must include 'auth_basic' — otherwise REST endpoints\n"
            "are reachable without a password by any client that knows the URL."
        )

    def test_api_location_no_immutable_cache_control(self) -> None:
        """/api/ location must NOT set immutable Cache-Control.

        API responses carry live trading state and must never be cached.
        The /assets/ location uses 'immutable' (correct for hashed bundles);
        /api/ must NOT (it returns live data).
        """
        txt = _nginx_text()
        api_block = _extract_location_block(txt, r"location\s+/api/")
        assert api_block, "Could not locate 'location /api/' block"
        assert "immutable" not in api_block, (
            "/api/ location must NOT set Cache-Control immutable — "
            "API responses carry live trading state and must not be cached."
        )

    def test_fastapi_serves_assets_at_root_no_prefix(self) -> None:
        """FastAPI must serve /assets/<bundle>.js at the root path (no /v2 prefix).

        This is the server-side counterpart to the nginx topology test: FastAPI's
        spa_catch_all must handle GET /assets/index-HASH.js → 200 JS.  The nginx
        /assets/ location strips nothing (no rewrite) — it proxies the exact path,
        so FastAPI must serve /assets/... from web/dist/assets/ directly.

        This test exercises the REAL web/dist bundle filename.
        """
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO))
        from api.main import app

        assets_dir = REPO / "web" / "dist" / "assets"
        if not assets_dir.exists():
            pytest.skip("web/dist/assets not built — run: npm --prefix web run build")

        # Find actual main JS file
        main_js = None
        for f in sorted(assets_dir.iterdir()):
            if f.name.startswith("index-") and f.suffix == ".js" and ".map" not in f.name:
                main_js = f.name
                break
        if main_js is None:
            pytest.skip("No index-*.js found in web/dist/assets/")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/assets/{main_js}")
        assert resp.status_code == 200, (
            f"FastAPI must serve /assets/{main_js} at 200, got {resp.status_code}.\n"
            "This is the path nginx proxies without a /v2 prefix — if FastAPI\n"
            "can't serve it the SPA will load a blank page in production."
        )
        ct = resp.headers.get("content-type", "")
        assert "javascript" in ct, (
            f"FastAPI served /assets/{main_js} with content-type={ct!r} — "
            "expected 'javascript'.  The browser will refuse to execute it."
        )


def _extract_location_block(nginx_txt: str, location_re: str) -> str:
    """Extract the text of a single nginx location block matching location_re.

    Returns the block text (including braces) or an empty string if not found.
    """
    in_block = False
    brace_depth = 0
    lines: list[str] = []
    for line in nginx_txt.splitlines():
        stripped = line.strip()
        if not in_block:
            if re.match(location_re, stripped):
                in_block = True
                brace_depth = stripped.count("{") - stripped.count("}")
                lines.append(line)
        else:
            lines.append(line)
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0:
                break
    return "\n".join(lines)
