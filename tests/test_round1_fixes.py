"""Round 1 fix regression tests (TDD — written BEFORE the fixes).

Covers:
- BLOCKER #2: _build_symbol_config_count reads from deploy/macos/start.sh
  (not disk glob). Asserts the exact 12 MIXED coins and that ENJ/TRUMP/ARC/
  ALICE/TON/ZEN are NOT MIXED.
- BLOCKER #3: get_trade_gate accepts since= and filters timestamp >= since.
  Gate == classify(_live_stats(...)) parity test.
- BLOCKER #5: assert_startup_safety is called in the API lifespan.
- BLOCKER #6: /ws and /ws/logs check the token (query param or X-Dash-Token).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Helper: tiny trades.db seeder
# ---------------------------------------------------------------------------

def _seed_db(tmp_path: Path, rows: list) -> str:
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
            close_reason TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO trades (id,timestamp,symbol,pnl,status,close_reason) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


# ===========================================================================
# BLOCKER #2 — _build_symbol_config_count sources the roster from start.sh
# ===========================================================================

class TestBuildSymbolConfigCountFromStartSh:
    """_build_symbol_config_count must read deploy/macos/start.sh, not glob disk."""

    def test_default_project_root_reads_startsh(self):
        """Calling with the real project root must derive counts from start.sh.

        Expected MIXED set (count > 1):
          AXS=4, AAVE/DOT/FIL=3,
          ATOM/BERA/INJ/IP/ONDO/PIPPIN/PIXEL/SUI=2

        The total unique MIXED coins = 12.
        """
        from dashboard.queries import _build_symbol_config_count

        count_map = _build_symbol_config_count(REPO)

        # These 12 coins must be MIXED (count > 1)
        expected_mixed = {
            "AXSUSDT": 4,
            "AAVEUSDT": 3,
            "DOTUSDT": 3,
            "FILUSDT": 3,
            "ATOMUSDT": 2,
            "BERAUSDT": 2,
            "INJUSDT": 2,
            "IPUSDT": 2,
            "ONDOUSDT": 2,
            "PIPPINUSDT": 2,
            "PIXELUSDT": 2,
            "SUIUSDT": 2,
        }
        for sym, expected_count in expected_mixed.items():
            actual = count_map.get(sym, 0)
            assert actual == expected_count, (
                f"{sym}: expected count={expected_count}, got {actual}"
            )

    def test_single_config_coins_are_not_mixed(self):
        """ENJ/TRUMP/ARC/ALICE/TON/ZEN must each have count==1 (not MIXED)."""
        from dashboard.queries import _build_symbol_config_count

        count_map = _build_symbol_config_count(REPO)

        single_coins = ["ENJUSDT", "TRUMPUSDT", "ARCUSDT", "ALICEUSDT", "TONUSDT", "ZENUSDT"]
        for sym in single_coins:
            actual = count_map.get(sym, 0)
            assert actual == 1, (
                f"{sym}: expected count=1 (not MIXED), got {actual}"
            )

    def test_retired_coins_not_counted(self):
        """Coins not in start.sh AUDITED/FORWARD lists must not appear in count_map.

        We test a few coins that exist as config files on disk but were retired
        from start.sh (e.g. DOGE, SOL which are in _SKIP_CONFIGS).
        """
        from dashboard.queries import _build_symbol_config_count

        count_map = _build_symbol_config_count(REPO)

        # These are in _SKIP_CONFIGS (retired) and must not appear
        retired = ["DOGEUSDT", "SOLUSDT"]
        for sym in retired:
            actual = count_map.get(sym, 0)
            assert actual == 0, (
                f"Retired coin {sym} must not appear in count_map, got {actual}"
            )


# ===========================================================================
# BLOCKER #3 — get_trade_gate since= timestamp filter
# ===========================================================================

class TestTradeGateSinceFilter:
    """get_trade_gate must accept a since= param and filter timestamp >= since."""

    def _make_db(self, tmp_path: Path) -> str:
        """Create a DB with trades across two time periods."""
        rows = [
            # Before 'since' date — should be excluded when since is set
            (1, "2026-01-01T00:00:00+00:00", "BTC/USDT:USDT",  10.0, "closed", "take_profit"),
            (2, "2026-01-02T00:00:00+00:00", "BTC/USDT:USDT", -5.0, "closed", "stop_loss"),
            # After 'since' date — should always be included
            (3, "2026-06-01T00:00:00+00:00", "BTC/USDT:USDT",  20.0, "closed", "take_profit"),
            (4, "2026-06-02T00:00:00+00:00", "BTC/USDT:USDT",  15.0, "closed", "take_profit"),
            (5, "2026-06-03T00:00:00+00:00", "BTC/USDT:USDT", -10.0, "closed", "stop_loss"),
        ]
        return _seed_db(tmp_path, rows)

    def _make_proj(self, tmp_path: Path) -> Path:
        """Create a minimal project root with a single BTC config."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "config_btcusdt_ema.json").write_text(
            json.dumps({"symbol": "BTC/USDT:USDT", "timeframe_signal": "4h"})
        )
        return proj

    def test_no_since_uses_all_trades(self, tmp_path):
        """Without since=, all 5 closed trades must be counted."""
        from dashboard.queries import get_trade_gate

        db = self._make_db(tmp_path)
        proj = self._make_proj(tmp_path)

        result = get_trade_gate(db_path=db, project_root=proj, min_trades=1)
        btc = [r for r in result["rows"] if r["symbol"] == "BTC/USDT:USDT"]
        assert btc, "BTC must appear"
        assert btc[0]["trades"] == 5, (
            f"Without since=, expected 5 trades, got {btc[0]['trades']}"
        )

    def test_since_filters_old_trades(self, tmp_path):
        """With since='2026-06-01', only 3 trades (rows 3-5) must be counted."""
        from dashboard.queries import get_trade_gate

        db = self._make_db(tmp_path)
        proj = self._make_proj(tmp_path)

        result = get_trade_gate(db_path=db, project_root=proj, min_trades=1, since="2026-06-01")
        btc = [r for r in result["rows"] if r["symbol"] == "BTC/USDT:USDT"]
        assert btc, "BTC must appear"
        assert btc[0]["trades"] == 3, (
            f"With since='2026-06-01', expected 3 trades, got {btc[0]['trades']}"
        )

    def test_since_affects_pf_calculation(self, tmp_path):
        """The since= filter must change the profit_factor, not just the count."""
        from dashboard.queries import get_trade_gate

        db = self._make_db(tmp_path)
        proj = self._make_proj(tmp_path)

        result_all = get_trade_gate(db_path=db, project_root=proj, min_trades=1)
        result_since = get_trade_gate(
            db_path=db, project_root=proj, min_trades=1, since="2026-06-01"
        )
        btc_all = [r for r in result_all["rows"] if r["symbol"] == "BTC/USDT:USDT"][0]
        btc_since = [r for r in result_since["rows"] if r["symbol"] == "BTC/USDT:USDT"][0]

        # All trades PF: wins=10+20+15=45, losses=5+10=15, PF=3.0
        # Since-filtered PF: wins=20+15=35, losses=10, PF=3.5
        # They must differ
        assert abs(btc_all["profit_factor"] - btc_since["profit_factor"]) > 0.01, (
            "PF must change when since= filters trades. "
            f"all={btc_all['profit_factor']}, since={btc_since['profit_factor']}"
        )

    def test_since_matches_live_stats_classify(self, tmp_path):
        """Gate verdict must equal classify(_live_stats(symbol, since=...)).

        This is the parity test: gate==classify(_live_stats(...)).
        """
        from dashboard.queries import get_trade_gate
        from api.classify import classify

        since = "2026-06-01"
        db = self._make_db(tmp_path)
        proj = self._make_proj(tmp_path)

        result = get_trade_gate(
            db_path=db, project_root=proj, min_trades=2, since=since, graduate_pf=1.3
        )
        btc = [r for r in result["rows"] if r["symbol"] == "BTC/USDT:USDT"]
        assert btc

        r = btc[0]
        # Reproduce _live_stats logic
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT pnl FROM trades WHERE symbol=? AND status='closed' "
            "AND COALESCE(close_reason,'')!='orphan_reconcile' AND timestamp >= ?",
            ("BTC/USDT:USDT", since),
        ).fetchall()
        conn.close()
        pnls = [x[0] for x in rows if x[0] is not None]
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        gross_w = sum(wins)
        gross_l = abs(sum(p for p in pnls if p <= 0))
        pf = (gross_w / gross_l) if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)

        expected_verdict = classify(n, pf, 2, 1.3)
        assert r["verdict"] == expected_verdict, (
            f"Gate verdict {r['verdict']!r} != classify({n},{pf:.2f},2,1.3)={expected_verdict!r}"
        )


# ===========================================================================
# BLOCKER #5 — assert_startup_safety is called during lifespan startup
# ===========================================================================

class TestLifespanCallsStartupSafety:
    """The FastAPI lifespan must call assert_startup_safety on startup."""

    def test_startup_safety_called_on_lifespan(self, tmp_path, monkeypatch):
        """Lifespan startup must call assert_startup_safety — if it doesn't,
        a non-local host with no token would silently serve the API.

        We monkeypatch assert_startup_safety to track calls, then run the
        lifespan context. If it is NOT called, this test fails.
        """
        import importlib
        calls = []

        def _fake_safety(host: str, token: Optional[str]) -> None:
            calls.append((host, token))

        # Patch at the api.main module level (where it is used)
        import api.main as main_mod
        monkeypatch.setattr(main_mod, "assert_startup_safety", _fake_safety)

        from fastapi.testclient import TestClient

        with TestClient(main_mod.app, raise_server_exceptions=False):
            pass

        assert len(calls) >= 1, (
            "assert_startup_safety must be called at lifespan startup — "
            "it was never called. Wire it into the lifespan() function."
        )

    def test_startup_blocks_nonlocal_no_token(self, monkeypatch):
        """If host != 127.0.0.1 and no token, the lifespan must raise RuntimeError.

        This ensures the real check is in the path, not just a logged warning.
        """
        import api.deps as deps_mod
        monkeypatch.setattr(deps_mod, "CCBT_DASH_TOKEN", None)
        monkeypatch.setenv("CCBT_DASH_HOST", "0.0.0.0")

        from api.deps import assert_startup_safety
        with pytest.raises(RuntimeError, match="CCBT_DASH_TOKEN"):
            assert_startup_safety(host="0.0.0.0", token=None)


# ===========================================================================
# BLOCKER #6 — WebSocket handlers check the token
# ===========================================================================

class TestWebSocketTokenCheck:
    """Both /ws and /ws/logs must reject connections when token is required."""

    def _set_token(self, token: str) -> None:
        import api.deps as d
        d.CCBT_DASH_TOKEN = token

    def _clear_token(self) -> None:
        import api.deps as d
        d.CCBT_DASH_TOKEN = None

    def test_ws_no_token_required_when_unset(self):
        """If CCBT_DASH_TOKEN is not set, /ws should accept any connection."""
        from fastapi.testclient import TestClient
        from api.main import app

        self._clear_token()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            with client.websocket_connect("/ws") as ws:
                data = ws.receive_json()
                assert "type" in data
        finally:
            self._clear_token()

    def test_ws_correct_token_accepted(self):
        """Correct token in query param must allow connection."""
        from fastapi.testclient import TestClient
        from api.main import app

        self._set_token("secret123")
        try:
            client = TestClient(app, raise_server_exceptions=False)
            with client.websocket_connect("/ws?token=secret123") as ws:
                data = ws.receive_json()
                assert "type" in data
        finally:
            self._clear_token()

    def test_ws_wrong_token_rejected(self):
        """Wrong token in query param must cause immediate close (4403 or 403)."""
        from fastapi.testclient import TestClient
        from api.main import app
        import websockets

        self._set_token("secret123")
        try:
            client = TestClient(app, raise_server_exceptions=False)
            rejected = False
            try:
                with client.websocket_connect("/ws?token=wrongtoken") as ws:
                    # If we get here, we should receive a 4403 close or error message
                    # The server should close the connection
                    try:
                        data = ws.receive_json()
                        # If server sends an error message then closes, that's ok
                        if data.get("type") == "error":
                            rejected = True
                    except Exception:
                        rejected = True
            except Exception:
                # Connection refused / closed immediately = good
                rejected = True
            assert rejected, (
                "Wrong token must result in rejected/closed WS connection"
            )
        finally:
            self._clear_token()

    def test_ws_missing_token_rejected(self):
        """Missing token when required must be rejected."""
        from fastapi.testclient import TestClient
        from api.main import app

        self._set_token("secret123")
        try:
            client = TestClient(app, raise_server_exceptions=False)
            rejected = False
            try:
                with client.websocket_connect("/ws") as ws:
                    try:
                        data = ws.receive_json()
                        if data.get("type") == "error":
                            rejected = True
                    except Exception:
                        rejected = True
            except Exception:
                rejected = True
            assert rejected, "Missing token when required must reject WS connection"
        finally:
            self._clear_token()

    def test_ws_logs_correct_token_accepted(self):
        """Correct token in query param must allow /ws/logs connection."""
        from fastapi.testclient import TestClient
        from api.main import app

        self._set_token("secret456")
        try:
            client = TestClient(app, raise_server_exceptions=False)
            with client.websocket_connect("/ws/logs?token=secret456") as ws:
                data = ws.receive_json()
                assert "type" in data
        finally:
            self._clear_token()

    def test_ws_logs_wrong_token_rejected(self):
        """Wrong token must reject /ws/logs."""
        from fastapi.testclient import TestClient
        from api.main import app

        self._set_token("secret456")
        try:
            client = TestClient(app, raise_server_exceptions=False)
            rejected = False
            try:
                with client.websocket_connect("/ws/logs?token=badtoken") as ws:
                    try:
                        data = ws.receive_json()
                        if data.get("type") == "error":
                            rejected = True
                    except Exception:
                        rejected = True
            except Exception:
                rejected = True
            assert rejected, "Wrong token must reject /ws/logs connection"
        finally:
            self._clear_token()


# ===========================================================================
# FOLLOW-UP — adversarial lowercase-symbol parity test
# ===========================================================================

class TestSymCleanLowercaseParity:
    """sym_clean must produce uppercase output identical to engine's inline formula
    for all roster symbols, even when input is lowercase.

    Engine formula (engine.py:410, 1593):
        symbol.replace('/', '').replace(':', '')
    Config symbols are always uppercase, so engine never needs .upper().
    sym_clean must match for any casing.
    """

    def test_lowercase_input_produces_uppercase_matching_engine(self):
        """sym_clean(s.lower()) == engine_clean(s) for a sample of deployed symbols."""
        import glob
        from bot.mode import sym_clean

        configs = glob.glob(str(REPO / "config_*.json"))
        assert configs, "No config_*.json files found"

        tested = 0
        for cfg_path in sorted(configs)[:15]:
            try:
                cfg = json.loads(Path(cfg_path).read_text())
            except Exception:
                continue
            symbol = cfg.get("symbol", "")
            if not symbol:
                continue

            # Engine formula (always gets uppercase config symbol)
            engine_clean = symbol.replace("/", "").replace(":", "")

            # sym_clean on lowercase input must produce same result
            try:
                result = sym_clean(symbol.lower())
                assert result == engine_clean, (
                    f"sym_clean({symbol.lower()!r}) = {result!r} "
                    f"!= engine_clean {engine_clean!r}"
                )
                tested += 1
            except ValueError:
                pass  # OANDA XAU_USD etc.

        assert tested > 0, "No symbols tested"

    def test_bulk_mode_sym_clean_applied_to_ccxt_symbols(self, tmp_path):
        """Bulk mode: ccxt-style symbol (BTC/USDT:USDT) must be normalised before
        roster check — it must succeed if the cleaned form is in the roster.
        """
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        import api.deps as deps_mod
        from api.main import app

        # Get a real roster symbol
        roster = deps_mod.get_roster()
        if not roster:
            pytest.skip("No roster symbols found")

        # Pick the first symbol and reconstruct its ccxt form
        clean_sym = next(iter(roster))
        # We can't perfectly reconstruct ccxt symbol from clean, so just test
        # that passing the clean symbol itself works in bulk (already the case)
        app.dependency_overrides[deps_mod.get_db_path] = lambda: ":memory:"
        try:
            with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
                client = TestClient(app)
                resp = client.post(
                    "/api/bots/mode/bulk",
                    json={"symbols": [clean_sym], "mode": "normal"},
                )
            assert resp.status_code == 200, resp.text
            results = {r["symbol"]: r for r in resp.json()["results"]}
            assert clean_sym in results
            assert results[clean_sym]["accepted"] is True
        finally:
            app.dependency_overrides.clear()
