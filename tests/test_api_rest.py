"""N1-rest TDD — FastAPI REST endpoint parity tests.

Tests are written FIRST (before the real implementations in the routers).
Each endpoint must:
1. Return 200 with the contracted Pydantic shape.
2. Return 200 + empty arrays for an empty DB (never 500).
3. Respect symbol filter query params.
4. Serialize NaN/inf safely (null / string, never a crash).
5. Parity: API JSON == (fixed) queries.py function output.

Python 3.10+ venv; 'from __future__ import annotations' for forward refs.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Shared DB fixture helpers
# ---------------------------------------------------------------------------

def _create_test_db(tmp_path: Path) -> str:
    """Create a WAL-mode trades DB seeded with representative rows."""
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            status TEXT,
            close_reason TEXT,
            pnl REAL,
            pnl_pct REAL,
            timestamp TEXT,
            entry_price REAL,
            exit_price REAL,
            size REAL,
            stop_loss REAL,
            ai_decision TEXT,
            ai_confidence REAL,
            ai_reasoning TEXT,
            ai_override INTEGER,
            duration_seconds INTEGER,
            strategy TEXT
        )
        """
    )
    # bot_health table (optional — get_bot_health gracefully absent)
    conn.execute(
        """
        CREATE TABLE bot_health (
            symbol TEXT PRIMARY KEY,
            strategy TEXT,
            mode TEXT,
            status TEXT,
            last_heartbeat TEXT,
            position_side TEXT,
            position_size REAL,
            position_entry REAL,
            error_count INTEGER,
            loop_count INTEGER,
            total_trades INTEGER,
            total_pnl REAL,
            updated_at TEXT
        )
        """
    )
    # ai_calibration table
    conn.execute(
        """
        CREATE TABLE ai_calibration (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            symbol TEXT,
            side TEXT,
            entry_price REAL,
            stated_confidence REAL,
            position_size_modifier REAL,
            sl_adjustment REAL,
            tp_adjustment REAL,
            market_regime TEXT,
            reasoning TEXT,
            risk_flags TEXT,
            should_skip INTEGER,
            outcome TEXT,
            pnl REAL,
            was_correct INTEGER
        )
        """
    )
    rows = [
        # BTC: 3 real closed trades + 1 orphan
        (1, "BTC/USDT:USDT", "long",  "closed", None,               10.0,  1.0,
         "2026-01-01T01:00:00", 40000.0, 41000.0, 0.1, 39000.0, None, None, None, 0, 3600, "ema_crossover"),
        (2, "BTC/USDT:USDT", "long",  "closed", None,                5.0,  0.5,
         "2026-01-02T01:00:00", 41000.0, 41500.0, 0.1, 40000.0, None, None, None, 0, 7200, "ema_crossover"),
        (3, "BTC/USDT:USDT", "short", "closed", None,               -3.0, -0.3,
         "2026-01-03T01:00:00", 42000.0, 42300.0, 0.1, 43000.0, None, None, None, 0, 1800, "ema_crossover"),
        (4, "BTC/USDT:USDT", "long",  "closed", "orphan_reconcile",  0.0,  0.0,
         "2026-01-04T01:00:00", 41000.0, 41000.0, 0.1,    None, None, None, None, 0,    0, None),
        # ETH: 2 real closed + 1 orphan
        (5, "ETH/USDT:USDT", "long",  "closed", None,                8.0,  0.8,
         "2026-01-05T01:00:00", 2500.0,  2600.0,  0.5, 2400.0, None, None, None, 0, 5400, "ichimoku"),
        (6, "ETH/USDT:USDT", "short", "closed", None,               -2.0, -0.2,
         "2026-01-06T01:00:00", 2600.0,  2650.0,  0.5, 2700.0, None, None, None, 0, 2700, "ichimoku"),
        (7, "ETH/USDT:USDT", "long",  "closed", "orphan_reconcile",  0.0,  0.0,
         "2026-01-07T01:00:00", 2600.0,  2600.0,  0.5,    None, None, None, None, 0,    0, None),
        # SOL: open trade (never in closed summaries)
        (8, "SOL/USDT:USDT", "long",  "open",   None,                0.0,  0.0,
         "2026-01-08T01:00:00", 150.0,      None, 1.0,  140.0, None, None, None, 0,    0, None),
    ]
    conn.executemany(
        """
        INSERT INTO trades (id,symbol,side,status,close_reason,pnl,pnl_pct,timestamp,
                            entry_price,exit_price,size,stop_loss,ai_decision,
                            ai_confidence,ai_reasoning,ai_override,duration_seconds,strategy)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def _create_empty_db(tmp_path: Path) -> str:
    """Empty but schema-valid WAL DB."""
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT, side TEXT, status TEXT, close_reason TEXT,
            pnl REAL, pnl_pct REAL, timestamp TEXT,
            entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
            ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT, ai_override INTEGER,
            duration_seconds INTEGER, strategy TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _get_client(db_path: str):
    """Build a TestClient whose get_db_path dependency returns db_path."""
    import api.deps as deps_module
    from fastapi.testclient import TestClient
    from api.main import app
    app.dependency_overrides[deps_module.get_db_path] = lambda: db_path
    client = TestClient(app, raise_server_exceptions=True)
    return client, app


def _reset_overrides(app) -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. /api/portfolio/summary
# ---------------------------------------------------------------------------

class TestPortfolioSummary:
    """GET /api/portfolio/summary."""

    def test_200_with_data(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/portfolio/summary")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)

    def test_schema_shape(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/portfolio/summary").json()
            assert "total_trades" in data
            assert "closed_trades" in data
            assert "win_rate_pct" in data
            assert "profit_factor" in data
            assert "total_pnl" in data
            assert "active_bots" in data
        finally:
            _reset_overrides(app)

    def test_orphan_excluded(self, tmp_path):
        """Orphan rows must not appear in portfolio summary trades count."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/portfolio/summary").json()
            # 3 BTC + 2 ETH = 5 real closed trades (orphans excluded)
            assert data["closed_trades"] == 5, (
                f"Expected 5 closed trades (no orphans), got {data['closed_trades']}"
            )
        finally:
            _reset_overrides(app)

    def test_empty_db_200_not_500(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/portfolio/summary")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_trades"] == 0
            assert data["closed_trades"] == 0
        finally:
            _reset_overrides(app)

    def test_missing_db_200_not_500(self, tmp_path):
        client, app = _get_client(str(tmp_path / "nonexistent.db"))
        try:
            resp = client.get("/api/portfolio/summary")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)

    def test_parity_with_queries(self, tmp_path):
        """API closed_trades + total_pnl must match get_trade_stats() output."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_stats
            qs = get_trade_stats(db_path=db)
            api_data = client.get("/api/portfolio/summary").json()
            assert api_data["closed_trades"] == qs["total_trades"]
            assert abs(api_data["total_pnl"] - qs["total_pnl"]) < 0.01
        finally:
            _reset_overrides(app)

    def test_profit_factor_inf_safe(self, tmp_path):
        """If PF is inf (no losses), serialize as a JSON number or null — never crash."""
        db_path = str(tmp_path / "win_only.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT,
               status TEXT, close_reason TEXT, pnl REAL, pnl_pct REAL, timestamp TEXT,
               entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
               ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT, ai_override INTEGER,
               duration_seconds INTEGER, strategy TEXT)"""
        )
        conn.execute(
            "INSERT INTO trades (id,symbol,side,status,close_reason,pnl,pnl_pct,timestamp)"
            " VALUES (1,'BTC/USDT:USDT','long','closed',NULL,10.0,1.0,'2026-01-01T01:00:00')"
        )
        conn.commit()
        conn.close()
        client, app = _get_client(db_path)
        try:
            resp = client.get("/api/portfolio/summary")
            assert resp.status_code == 200
            raw_text = resp.text
            # Valid JSON (no NaN literal)
            parsed = json.loads(raw_text)
            pf = parsed["profit_factor"]
            # Must be a finite number OR null — never a bare Python float("inf") crash
            assert pf is None or isinstance(pf, (int, float))
            if isinstance(pf, float):
                assert not math.isnan(pf)
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 2. /api/bots
# ---------------------------------------------------------------------------

class TestBotList:
    """GET /api/bots."""

    def test_200_with_data(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/bots")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)

    def test_schema_has_bots_array(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/bots").json()
            assert "bots" in data
            assert isinstance(data["bots"], list)
        finally:
            _reset_overrides(app)

    def test_bot_row_schema(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/bots").json()
            if data["bots"]:
                row = data["bots"][0]
                assert "symbol" in row
                assert "total_pnl" in row
                assert "win_rate_pct" in row
                assert "profit_factor" in row
                assert "trade_count" in row
        finally:
            _reset_overrides(app)

    def test_orphan_excluded_from_bots(self, tmp_path):
        """Orphan rows must not inflate trade_count in /api/bots."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/bots").json()
            bots = {b["symbol"]: b for b in data["bots"]}
            assert "BTC/USDT:USDT" in bots
            assert bots["BTC/USDT:USDT"]["trade_count"] == 3
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/bots")
            assert resp.status_code == 200
            assert resp.json()["bots"] == []
        finally:
            _reset_overrides(app)

    def test_parity_with_per_bot_summary(self, tmp_path):
        """API total_pnl per symbol must match get_per_bot_summary()."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_per_bot_summary
            qs_df = get_per_bot_summary(db_path=db)
            api_data = client.get("/api/bots").json()
            api_map = {b["symbol"]: b for b in api_data["bots"]}
            for _, row in qs_df.iterrows():
                sym = row["symbol"]
                assert sym in api_map, f"{sym} in queries but not in /api/bots"
                assert abs(api_map[sym]["total_pnl"] - float(row["total_pnl"])) < 0.01, (
                    f"{sym}: api total_pnl={api_map[sym]['total_pnl']} "
                    f"vs queries={row['total_pnl']}"
                )
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 3. /api/bots/{symbol}
# ---------------------------------------------------------------------------

class TestBotDetail:
    """GET /api/bots/{symbol}."""

    def test_200_for_known_symbol(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/bots/BTC%2FUSDT%3AUSDT")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)

    def test_schema_shape(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/bots/BTC%2FUSDT%3AUSDT").json()
            assert "symbol" in data
            assert "summary" in data
            assert "recent_trades" in data
            assert isinstance(data["recent_trades"], list)
        finally:
            _reset_overrides(app)

    def test_unknown_symbol_200_empty(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/bots/NOSUCHSYMBOL")
            # Must return 200 with empty/default data — not 404 (mirror queries.py)
            assert resp.status_code == 200
            data = resp.json()
            assert data["summary"]["trade_count"] == 0
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/bots/BTC%2FUSDT%3AUSDT")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 4. /api/trades
# ---------------------------------------------------------------------------

class TestTradesList:
    """GET /api/trades."""

    def test_200_returns_trades_array(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/trades")
            assert resp.status_code == 200
            data = resp.json()
            assert "trades" in data
            assert "total" in data
            assert isinstance(data["trades"], list)
        finally:
            _reset_overrides(app)

    def test_symbol_filter(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/trades?symbol=BTC%2FUSDT%3AUSDT")
            data = resp.json()
            symbols = {t["symbol"] for t in data["trades"] if t.get("symbol")}
            # Only BTC rows returned when filtered
            assert symbols.issubset({"BTC/USDT:USDT"}), f"Unexpected symbols: {symbols}"
        finally:
            _reset_overrides(app)

    def test_limit_respected(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/trades?limit=2")
            data = resp.json()
            assert len(data["trades"]) <= 2
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/trades")
            assert resp.status_code == 200
            data = resp.json()
            assert data["trades"] == []
            assert data["total"] == 0
        finally:
            _reset_overrides(app)

    def test_trade_row_schema(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/trades").json()
            if data["trades"]:
                row = data["trades"][0]
                assert "symbol" in row
                assert "pnl" in row
                assert "close_reason" in row
        finally:
            _reset_overrides(app)

    def test_parity_with_get_recent_trades(self, tmp_path):
        """API trade count must match get_recent_trades() with same limit."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_recent_trades
            qs_df = get_recent_trades(limit=50, db_path=db)
            api_data = client.get("/api/trades?limit=50").json()
            assert len(api_data["trades"]) == len(qs_df), (
                f"API returned {len(api_data['trades'])} trades, "
                f"queries returned {len(qs_df)}"
            )
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 5. /api/equity
# ---------------------------------------------------------------------------

class TestEquityCurve:
    """GET /api/equity."""

    def test_200_returns_points(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/equity")
            assert resp.status_code == 200
            data = resp.json()
            assert "points" in data
            assert isinstance(data["points"], list)
        finally:
            _reset_overrides(app)

    def test_equity_point_schema(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/equity").json()
            if data["points"]:
                pt = data["points"][0]
                assert "timestamp" in pt
                assert "equity" in pt
                assert "cumulative_pnl" in pt
        finally:
            _reset_overrides(app)

    def test_symbol_filter(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/equity?symbol=BTC%2FUSDT%3AUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["symbol"] == "BTC/USDT:USDT"
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/equity")
            assert resp.status_code == 200
            assert resp.json()["points"] == []
        finally:
            _reset_overrides(app)

    def test_parity_with_get_equity_curve(self, tmp_path):
        """API equity points count must match get_equity_curve() rows."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_equity_curve
            qs_df = get_equity_curve(db_path=db)
            api_data = client.get("/api/equity").json()
            assert len(api_data["points"]) == len(qs_df), (
                f"API points={len(api_data['points'])}, queries rows={len(qs_df)}"
            )
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 6. /api/daily-pnl
# ---------------------------------------------------------------------------

class TestDailyPnl:
    """GET /api/daily-pnl."""

    def test_200_returns_days(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/daily-pnl")
            assert resp.status_code == 200
            data = resp.json()
            assert "days" in data
            assert isinstance(data["days"], list)
        finally:
            _reset_overrides(app)

    def test_day_point_schema(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/daily-pnl").json()
            if data["days"]:
                day = data["days"][0]
                assert "date" in day
                assert "pnl" in day
                assert "trade_count" in day
        finally:
            _reset_overrides(app)

    def test_symbol_filter(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/daily-pnl?symbol=ETH%2FUSDT%3AUSDT")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/daily-pnl")
            assert resp.status_code == 200
            assert resp.json()["days"] == []
        finally:
            _reset_overrides(app)

    def test_parity_with_get_daily_pnl(self, tmp_path):
        """API days count must match get_daily_pnl() rows."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_daily_pnl
            qs_df = get_daily_pnl(db_path=db)
            api_data = client.get("/api/daily-pnl").json()
            assert len(api_data["days"]) == len(qs_df), (
                f"API days={len(api_data['days'])}, queries rows={len(qs_df)}"
            )
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 7. /api/ai/calibration
# ---------------------------------------------------------------------------

class TestAICalibration:
    """GET /api/ai/calibration."""

    def test_200_returns_rows(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/ai/calibration")
            assert resp.status_code == 200
            data = resp.json()
            assert "rows" in data
            assert isinstance(data["rows"], list)
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/ai/calibration")
            assert resp.status_code == 200
            assert resp.json()["rows"] == []
        finally:
            _reset_overrides(app)

    def test_missing_db_200(self, tmp_path):
        client, app = _get_client(str(tmp_path / "no.db"))
        try:
            resp = client.get("/api/ai/calibration")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 8. /api/logs
# ---------------------------------------------------------------------------

class TestLogs:
    """GET /api/logs."""

    def test_200_returns_lines(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/logs")
            assert resp.status_code == 200
            data = resp.json()
            assert "lines" in data
            assert "total_returned" in data
            assert isinstance(data["lines"], list)
        finally:
            _reset_overrides(app)

    def test_empty_log_file_200(self, tmp_path):
        """If the log file is missing, must return 200 with empty lines."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/logs")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)

    def test_limit_param(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/logs?limit=10")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["lines"]) <= 10
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 9. /api/close-reasons (N9)
# ---------------------------------------------------------------------------

class TestCloseReasons:
    """GET /api/close-reasons."""

    def test_200_returns_breakdown(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/close-reasons")
            assert resp.status_code == 200
            data = resp.json()
            assert "breakdown" in data
            assert isinstance(data["breakdown"], list)
        finally:
            _reset_overrides(app)

    def test_breakdown_item_schema(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/close-reasons").json()
            if data["breakdown"]:
                item = data["breakdown"][0]
                assert "reason" in item
                assert "count" in item
                assert "total_pnl" in item
                assert "pnl_positive" in item
        finally:
            _reset_overrides(app)

    def test_symbol_filter(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/close-reasons?symbol=BTC%2FUSDT%3AUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["symbol"] == "BTC/USDT:USDT"
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/close-reasons")
            assert resp.status_code == 200
            assert resp.json()["breakdown"] == []
        finally:
            _reset_overrides(app)

    def test_parity_with_get_close_reason_breakdown(self, tmp_path):
        """API breakdown count must match get_close_reason_breakdown() rows."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_close_reason_breakdown
            qs_df = get_close_reason_breakdown(db_path=db)
            api_data = client.get("/api/close-reasons").json()
            assert len(api_data["breakdown"]) == len(qs_df), (
                f"API breakdown={len(api_data['breakdown'])}, "
                f"queries rows={len(qs_df)}"
            )
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 10. /api/trade-gate (N9)
# ---------------------------------------------------------------------------

class TestTradeGate:
    """GET /api/trade-gate."""

    def test_200_returns_rows(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/trade-gate")
            assert resp.status_code == 200
            data = resp.json()
            assert "rows" in data
            assert "summary" in data
        finally:
            _reset_overrides(app)

    def test_summary_schema(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/trade-gate").json()
            s = data["summary"]
            assert "n_meeting_min" in s
            assert "n_total" in s
            assert "min_trades_threshold" in s
        finally:
            _reset_overrides(app)

    def test_row_schema(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/trade-gate").json()
            if data["rows"]:
                row = data["rows"][0]
                assert "symbol" in row
                assert "verdict" in row
                assert "meets_min_trades" in row
                assert "trade_count" in row
                assert "profit_factor" in row
                assert "win_rate_pct" in row
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/trade-gate")
            assert resp.status_code == 200
            data = resp.json()
            assert data["rows"] == []
            assert data["summary"]["n_total"] == 0
        finally:
            _reset_overrides(app)

    def test_parity_with_get_trade_gate(self, tmp_path):
        """API n_total must match get_trade_gate() when both use the same since= filter.

        The endpoint loads since= from research/forward_test_cohort.json (parity with
        forward_test_report CLI). The oracle here must pass the same since= so both
        sides of the comparison are filtered identically.
        """
        import json as _json
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_gate
            # Read the same since= the endpoint will use
            manifest_path = Path(__file__).resolve().parent.parent / "research" / "forward_test_cohort.json"
            try:
                since = _json.loads(manifest_path.read_text()).get("added") or None
            except Exception:
                since = None
            # Use tmp_path as project_root (no live configs) and match the endpoint's since=
            qs = get_trade_gate(db_path=db, project_root=tmp_path, since=since)
            api_data = client.get("/api/trade-gate").json()
            assert api_data["summary"]["n_total"] == qs["summary"]["n_total"], (
                f"API n_total={api_data['summary']['n_total']}, "
                f"queries n_total={qs['summary']['n_total']} (since={since!r})"
            )
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 11. /api/risk (N9)
# ---------------------------------------------------------------------------

class TestOpenRisk:
    """GET /api/risk."""

    def test_200_returns_rows(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/risk")
            assert resp.status_code == 200
            data = resp.json()
            assert "rows" in data
            assert "unprotected_count" in data
        finally:
            _reset_overrides(app)

    def test_sol_open_trade_appears(self, tmp_path):
        """SOL has an open trade in test DB — should appear in risk rows."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/risk").json()
            symbols = [r["symbol"] for r in data["rows"]]
            assert "SOL/USDT:USDT" in symbols
        finally:
            _reset_overrides(app)

    def test_symbol_filter(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/risk?symbol=SOL%2FUSDT%3AUSDT")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/risk")
            assert resp.status_code == 200
            data = resp.json()
            assert data["rows"] == []
            assert data["unprotected_count"] == 0
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 12. /api/calendar (N9)
# ---------------------------------------------------------------------------

class TestCalendar:
    """GET /api/calendar."""

    def test_200_returns_cells(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/calendar?year=2026&month=1")
            assert resp.status_code == 200
            data = resp.json()
            assert "cells" in data
            assert "year" in data
            assert "month" in data
        finally:
            _reset_overrides(app)

    def test_cell_schema(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/calendar?year=2026&month=1").json()
            if data["cells"]:
                cell = data["cells"][0]
                assert "date" in cell
                assert "pnl" in cell
                assert "trade_count" in cell
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/calendar?year=2026&month=1")
            assert resp.status_code == 200
            assert resp.json()["cells"] == []
        finally:
            _reset_overrides(app)

    def test_parity_with_get_calendar_pnl(self, tmp_path):
        """API cells count must match get_calendar_pnl() rows (for same month)."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_calendar_pnl
            qs_df = get_calendar_pnl(db_path=db)
            # Filter to 2026-01
            jan = qs_df[qs_df["date"].str.startswith("2026-01")] if not qs_df.empty else qs_df
            api_data = client.get("/api/calendar?year=2026&month=1").json()
            assert len(api_data["cells"]) == len(jan), (
                f"API cells={len(api_data['cells'])}, queries rows={len(jan)}"
            )
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 13. /api/heatmap (N9)
# ---------------------------------------------------------------------------

class TestHeatmap:
    """GET /api/heatmap."""

    def test_200_returns_cells(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/heatmap")
            assert resp.status_code == 200
            data = resp.json()
            assert "cells" in data
            assert "bucket_hours" in data
        finally:
            _reset_overrides(app)

    def test_cell_schema(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/heatmap").json()
            if data["cells"]:
                cell = data["cells"][0]
                assert "hour" in cell
                assert "dow" in cell
                assert "pnl" in cell
                assert "trade_count" in cell
                assert "win_rate_pct" in cell
        finally:
            _reset_overrides(app)

    def test_bucket_hours_param(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/heatmap?bucket_hours=4")
            assert resp.status_code == 200
            data = resp.json()
            assert data["bucket_hours"] == 4
        finally:
            _reset_overrides(app)

    def test_empty_db_200(self, tmp_path):
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/heatmap")
            assert resp.status_code == 200
            assert resp.json()["cells"] == []
        finally:
            _reset_overrides(app)

    def test_parity_with_get_hour_dow_stats(self, tmp_path):
        """API cells count must match get_hour_dow_stats() rows."""
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_hour_dow_stats
            qs_df = get_hour_dow_stats(db_path=db, bucket_hours=1)
            api_data = client.get("/api/heatmap?bucket_hours=1").json()
            assert len(api_data["cells"]) == len(qs_df), (
                f"API cells={len(api_data['cells'])}, queries rows={len(qs_df)}"
            )
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# 14. NaN / inf serialisation safety (cross-endpoint)
# ---------------------------------------------------------------------------

class TestNaNInfSerialization:
    """No endpoint should return invalid JSON (NaN / Infinity literals)."""

    def _check_no_nan_inf(self, text: str) -> None:
        """Assert the response text has no bare NaN/Infinity."""
        assert "NaN" not in text, f"Response contains NaN: {text[:200]}"
        assert "Infinity" not in text, f"Response contains Infinity: {text[:200]}"
        # Also valid JSON
        json.loads(text)

    def test_portfolio_summary_valid_json(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/portfolio/summary")
            self._check_no_nan_inf(resp.text)
        finally:
            _reset_overrides(app)

    def test_bots_valid_json(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/bots")
            self._check_no_nan_inf(resp.text)
        finally:
            _reset_overrides(app)

    def test_equity_valid_json(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/equity")
            self._check_no_nan_inf(resp.text)
        finally:
            _reset_overrides(app)

    def test_trade_gate_valid_json(self, tmp_path):
        db = _create_test_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/trade-gate")
            self._check_no_nan_inf(resp.text)
        finally:
            _reset_overrides(app)
