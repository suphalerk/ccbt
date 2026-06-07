"""Round 2 NIT regression tests.

Covers:
- NIT 1: spa_catch_all guard is segment-exact: /apikeys → SPA index.html;
  /api/<x> → 404 JSON.  Same for ws/wstools.
- NIT 2: index.html response carries Cache-Control: no-store so browsers never
  serve a stale token injection.
- WS snapshot parity: _build_snapshot() bots list equals GET /api/bots bots
  list for the same DB (residual #1 guard — ensures WS and REST share one
  source of truth).

All tests that need web/dist are skipped when the bundle is absent.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Generator

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_DIST = REPO / "web" / "dist"
_DIST_EXISTS = _DIST.exists() and (_DIST / "index.html").exists()

requires_dist = pytest.mark.skipif(
    not _DIST_EXISTS,
    reason="web/dist not built — run 'npm --prefix web run build' first",
)


@contextlib.contextmanager
def _token_ctx(token: str | None) -> Generator[None, None, None]:
    """Temporarily set api.deps.CCBT_DASH_TOKEN; always restore."""
    import api.deps as _deps
    original = _deps.CCBT_DASH_TOKEN
    _deps.CCBT_DASH_TOKEN = token
    try:
        yield
    finally:
        _deps.CCBT_DASH_TOKEN = original


# ---------------------------------------------------------------------------
# Helper: seed a minimal trades + bot_health DB
# ---------------------------------------------------------------------------

def _seed_db(tmp_path: Path, n_bots: int = 3) -> str:
    """Seed a WAL trades.db with n_bots worth of closed trades + bot_health rows."""
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'long',
            entry_price REAL NOT NULL DEFAULT 1.0,
            exit_price REAL,
            size REAL NOT NULL DEFAULT 1.0,
            stop_loss REAL,
            take_profit REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'closed',
            close_reason TEXT,
            strategy TEXT,
            ai_decision TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bot_health (
            symbol TEXT PRIMARY KEY,
            strategy TEXT,
            status TEXT,
            mode TEXT,
            position_side TEXT,
            position_size REAL,
            last_updated TEXT
        )"""
    )
    symbols = [f"COIN{i}USDT" for i in range(n_bots)]
    for i, sym in enumerate(symbols):
        conn.execute(
            "INSERT INTO trades (id,timestamp,symbol,pnl,status,close_reason,strategy)"
            " VALUES (?,?,?,?,?,?,?)",
            (i + 1, f"2026-0{i+1}-01T00:00:00+00:00", sym, float(i + 1) * 5.0,
             "closed", "take_profit", "ema_crossover"),
        )
        conn.execute(
            "INSERT INTO bot_health (symbol,strategy,status,mode,position_side,position_size)"
            " VALUES (?,?,?,?,?,?)",
            (sym, "ema_crossover", "running", "NORMAL", None, None),
        )
    conn.commit()
    conn.close()
    return db_path


# ===========================================================================
# NIT 1 — segment-exact guard: /apikeys → SPA; /api/<x> → 404 JSON
# ===========================================================================

class TestSpaCatchAllSegmentGuard:
    """spa_catch_all guard must use segment-exact matching.

    Before the fix: startswith("api") incorrectly returned 404 for /apikeys,
    /apistuff, etc. — any SPA route that happened to begin with 'api' or 'ws'
    got a JSON 404 instead of index.html.

    After the fix: only the exact prefix /api/ (or bare /api) and /ws/ (or bare
    /ws) trigger the JSON 404 guard; all other paths fall through to the SPA.
    """

    @requires_dist
    def test_apikeys_route_returns_spa_html(self):
        """/apikeys must return the SPA index.html (HTML), NOT a 404 JSON.

        This is the BLOCKER regression: the old startswith("api") guard
        returned 404 JSON for /apikeys because "apikeys".startswith("api") is True.
        The fix tightens the guard to startswith("api/") or == "api".
        """
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/apikeys")
        # Must be HTML (SPA), not 404 JSON
        assert resp.status_code == 200, (
            f"GET /apikeys returned {resp.status_code} — expected 200 (SPA route). "
            "The old startswith('api') guard wrongly catches /apikeys. "
            "Fix: use startswith('api/') or == 'api'."
        )
        ct = resp.headers.get("content-type", "")
        assert "html" in ct, (
            f"GET /apikeys content-type={ct!r} — expected HTML (SPA). Got JSON means the "
            "guard is still over-broad."
        )

    @requires_dist
    def test_wstools_route_returns_spa_html(self):
        """/wstools must return the SPA index.html, NOT a 404 JSON.

        Same logic as /apikeys: "wstools".startswith("ws") was True under the
        old guard but "wstools".startswith("ws/") is False under the new guard.
        """
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/wstools")
        assert resp.status_code == 200, (
            f"GET /wstools returned {resp.status_code} — expected 200 (SPA route). "
            "The old startswith('ws') guard wrongly catches /wstools."
        )
        ct = resp.headers.get("content-type", "")
        assert "html" in ct, (
            f"GET /wstools content-type={ct!r} — expected HTML (SPA)."
        )

    @requires_dist
    def test_api_nonexistent_still_returns_404_json(self):
        """/api/<nonexistent> must STILL return 404 JSON after the guard tightening."""
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/this_route_does_not_exist_xyz")
        assert resp.status_code == 404, (
            f"GET /api/this_route_does_not_exist_xyz returned {resp.status_code} — "
            "expected 404. The guard tightening must not break the /api/* catch."
        )
        ct = resp.headers.get("content-type", "")
        assert "json" in ct, (
            f"Expected JSON 404 for /api/*, got content-type={ct!r}"
        )

    @requires_dist
    def test_ws_bogus_still_returns_404_json(self):
        """/ws/<bogus-sub-path> must STILL return 404 JSON after the guard tightening."""
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/ws/bogus_channel_xyz_notareal_path")
        assert resp.status_code == 404, (
            f"GET /ws/bogus_channel_xyz returned {resp.status_code} — expected 404."
        )
        ct = resp.headers.get("content-type", "")
        assert "json" in ct, (
            f"Expected JSON 404 for /ws/*, got content-type={ct!r}"
        )

    @requires_dist
    def test_bare_api_segment_returns_404_json(self):
        """/api (no trailing slash) must return 404 JSON."""
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api")
        # Could be 307/404 depending on trailing-slash redirect; 200 HTML is wrong
        assert resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""), (
            "GET /api returned 200 HTML — it should be a 404 or redirect, not SPA index."
        )

    @requires_dist
    def test_deeply_nested_spa_route_returns_html(self):
        """/dashboard/bot/BTCUSDT (deep SPA route) must return index.html."""
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/dashboard/bot/BTCUSDT")
        assert resp.status_code == 200, (
            f"GET /dashboard/bot/BTCUSDT returned {resp.status_code} — expected 200 (SPA)."
        )
        ct = resp.headers.get("content-type", "")
        assert "html" in ct, (
            f"GET /dashboard/bot/BTCUSDT content-type={ct!r} — expected HTML (SPA)."
        )


# ===========================================================================
# NIT 2 — Cache-Control: no-store on injected index.html
# ===========================================================================

class TestIndexHtmlCacheControl:
    """index.html must be served with Cache-Control: no-store.

    Without this header, browsers cache the injected HTML (with the embedded
    token) indefinitely. If the token rotates the cached page would silently
    use a stale (revoked) token for all WS connections.
    """

    @requires_dist
    def test_root_index_has_no_store_cache_control(self):
        """GET / must include Cache-Control: no-store in the response headers."""
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc.lower(), (
            f"GET / Cache-Control header is {cc!r} — expected 'no-store'. "
            "Without no-store, browsers cache the injected token. "
            "When the token rotates the cached page silently uses the stale token."
        )

    @requires_dist
    def test_spa_deep_link_has_no_store_cache_control(self):
        """SPA deep-link /dashboard/settings must also carry Cache-Control: no-store."""
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/dashboard/settings")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc.lower(), (
            f"GET /dashboard/settings Cache-Control={cc!r} — expected 'no-store'."
        )


# ===========================================================================
# WS snapshot parity — _build_snapshot bots == GET /api/bots bots
# ===========================================================================

class TestWsSnapshotBotsParity:
    """The WS snapshot 'bots' list must equal the REST GET /api/bots list.

    Root cause of residual #1: _build_snapshot() was emitting a lesser/empty
    payload that clobbered the REST-loaded list when useLiveSnapshot wrote it
    into the TanStack Query cache.

    These tests use the REAL _build_snapshot() and REAL portfolio.list_bots()
    against the SAME seeded DB to assert identical structure.  No mocks.
    """

    def test_ws_snapshot_bots_equal_rest_bots_for_same_db(self, tmp_path):
        """_build_snapshot()['data']['bots'] must have the same symbols as GET /api/bots.

        Both paths must return the same set of symbols for a seeded DB.
        Structural keys (strategy, status, mode, win_rate_pct, trade_count, etc.)
        must be present in WS bots — not just symbols.
        """
        db_path = _seed_db(tmp_path, n_bots=3)

        # --- REST path ---
        from api.main import app
        import api.deps as _deps
        from fastapi.testclient import TestClient

        original_db = None
        # Override the db dependency to point at our seeded DB
        original_override = app.dependency_overrides.copy()
        app.dependency_overrides[_deps.get_db_path] = lambda: db_path
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/bots")
            assert resp.status_code == 200, (
                f"GET /api/bots returned {resp.status_code}: {resp.text[:300]}"
            )
            rest_payload = resp.json()
            rest_bots = rest_payload.get("bots", [])
        finally:
            app.dependency_overrides = original_override

        # --- WS path ---
        from api.ws import ChangeDetector
        detector = ChangeDetector(
            db_path=db_path,
            data_dir=str(tmp_path),
            poll_interval=0.05,
        )
        snap = detector._build_snapshot()
        ws_bots = snap.get("data", {}).get("bots", [])

        # Both must produce the same set of symbols
        rest_symbols = {b["symbol"] for b in rest_bots}
        ws_symbols = {b["symbol"] for b in ws_bots}

        assert rest_symbols == ws_symbols, (
            f"WS snapshot bots symbols {ws_symbols} != REST bots symbols {rest_symbols}. "
            "The WS _build_snapshot() must use the same data source as GET /api/bots."
        )

    def test_ws_snapshot_bots_have_full_row_shape(self, tmp_path):
        """WS snapshot bots must contain the full BotRow fields (not a lesser shape).

        A 'lesser' WS payload (missing strategy, status, mode, etc.) would
        clobber the richer REST-loaded data in the TanStack Query cache.
        Required fields: symbol, strategy, status, mode, win_rate_pct, trade_count.
        """
        db_path = _seed_db(tmp_path, n_bots=2)

        from api.ws import ChangeDetector
        detector = ChangeDetector(
            db_path=db_path,
            data_dir=str(tmp_path),
            poll_interval=0.05,
        )
        snap = detector._build_snapshot()
        ws_bots = snap.get("data", {}).get("bots", [])

        assert len(ws_bots) >= 1, (
            "WS snapshot returned no bots for a seeded DB — _build_snapshot() is empty."
        )

        required_keys = {"symbol", "strategy", "status", "mode", "win_rate_pct", "trade_count"}
        for bot in ws_bots:
            missing = required_keys - set(bot.keys())
            assert not missing, (
                f"WS snapshot bot {bot.get('symbol', '?')} is missing keys: {missing}. "
                "The WS payload is a 'lesser shape' that would clobber the REST-loaded list."
            )

    def test_ws_snapshot_bots_list_is_a_list(self, tmp_path):
        """WS snapshot data.bots must be a list (not a raw array at top level).

        Root cause of residual #1 (frontend): useLiveSnapshot wrote the WS
        bots payload as a raw array into the ['bots'] TanStack cache key.
        Consumers reading data?.bots got undefined (arrays have no .bots property).

        The backend contract: data.bots is a list[dict], same shape as
        BotListResponse.bots so the frontend can write it as {bots: ws_bots}.
        """
        db_path = _seed_db(tmp_path, n_bots=2)

        from api.ws import ChangeDetector
        detector = ChangeDetector(
            db_path=db_path,
            data_dir=str(tmp_path),
            poll_interval=0.05,
        )
        snap = detector._build_snapshot()

        data = snap.get("data", {})
        assert isinstance(data, dict), (
            f"WS snapshot 'data' must be a dict, got {type(data).__name__}"
        )
        bots = data.get("bots")
        assert bots is not None, (
            "WS snapshot data.bots is missing — must always be present (even if empty list)"
        )
        assert isinstance(bots, list), (
            f"WS snapshot data.bots must be a list, got {type(bots).__name__}. "
            "If bots is a raw list at the top level rather than nested under 'data.bots', "
            "the frontend cannot write it as {{bots: ws_bots}} without data?.bots being undefined."
        )

    def test_ws_snapshot_envelope_shape(self, tmp_path):
        """WS snapshot must have envelope: {type, ts, data: {portfolio, bots}}.

        This is the contract that useLiveSnapshot reads. If data.portfolio or
        data.bots is missing the frontend hydration fails silently.
        """
        db_path = _seed_db(tmp_path, n_bots=2)

        from api.ws import ChangeDetector
        detector = ChangeDetector(
            db_path=db_path,
            data_dir=str(tmp_path),
            poll_interval=0.05,
        )
        snap = detector._build_snapshot()

        assert snap.get("type") == "snapshot", (
            f"WS envelope type must be 'snapshot', got {snap.get('type')!r}"
        )
        assert "ts" in snap, "WS envelope must have 'ts' field"
        assert "data" in snap, "WS envelope must have 'data' field"

        data = snap["data"]
        assert "portfolio" in data, (
            "WS snapshot data must have 'portfolio' key — frontend PortfolioSummary reads this"
        )
        assert "bots" in data, (
            "WS snapshot data must have 'bots' key — frontend bot grid reads this"
        )

    def test_ws_snapshot_empty_db_no_crash(self, tmp_path):
        """_build_snapshot() must not crash on an empty (no-trades) DB.

        Returns the envelope with empty lists, not an exception.
        """
        # Create an empty WAL DB with no trades
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT,
                pnl REAL, status TEXT, close_reason TEXT
            )"""
        )
        conn.commit()
        conn.close()

        from api.ws import ChangeDetector
        detector = ChangeDetector(
            db_path=db_path,
            data_dir=str(tmp_path),
            poll_interval=0.05,
        )
        # Must not raise
        snap = detector._build_snapshot()
        assert "data" in snap, "Snapshot envelope must have 'data' key even for empty DB"
        bots = snap["data"].get("bots")
        assert isinstance(bots, list), (
            f"data.bots must be a list for empty DB, got {type(bots).__name__ if bots is not None else None!r}"
        )
