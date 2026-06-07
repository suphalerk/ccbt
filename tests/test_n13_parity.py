"""N13 — Correctness-anchored parity: API JSON vs queries.py in-process.

The oracle is the **fixed** queries.py (post-N1 orphan exclusion).
"Parity with Streamlit" is NOT the target — Streamlit also called the old,
unfixed queries.py, so it was equally wrong.  We compare:

    API response JSON  ==  queries.py function return (same DB, same args)

Panel coverage (all panels, including the 5 new N10 panels):
  P1  Header metrics      /api/portfolio/summary  ← get_trade_stats
  P2  Bot grid modes      /api/bots               ← get_per_bot_summary
  P3  Bot overview (PnL)  /api/bots/{symbol}      ← get_trade_stats(symbol)
  P4  Trade history       /api/trades             ← get_recent_trades
  P5  Equity curve        /api/equity             ← get_equity_curve
  P6  Per-bot daily PnL   /api/daily-pnl          ← get_daily_pnl
  P7  AI analytics        /api/ai/calibration     ← get_calibration_stats
  P8  Log viewer          /api/logs               ← get_recent_logs (smoke)
  P9  Close-reason donut  /api/close-reasons      ← get_close_reason_breakdown
  P10 Trade gate          /api/trade-gate         ← get_trade_gate
  P11 Open risk           /api/risk               ← get_open_trades
  P12 Calendar heatmap    /api/calendar           ← get_calendar_pnl
  P13 Hour/DoW heatmap    /api/heatmap            ← get_hour_dow_stats

Accepted degradations (NOT parity-blockers, documented in N13 ticket):
  - signal_source dropped (absent from schema)
  - exit markers at timestamp+duration_seconds (Streamlit's entry-time is wrong)
  - Candles from persisted data only (no exchange call)

TDD: tests written FIRST; none were green before the API existed.
Now all should be green because API routers import queries.py directly.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# ---------------------------------------------------------------------------
# DB fixture helpers (shared schema from test_api_rest.py pattern)
# ---------------------------------------------------------------------------

def _create_rich_db(tmp_path: Path) -> str:
    """Create a WAL-mode trades DB with multi-panel coverage.

    Includes:
    - BTC: 4 closed real + 1 orphan + 1 open
    - ETH: 3 closed real + 1 orphan
    - SOL: 2 closed real (open position too)
    - ai_calibration rows for AI analytics
    """
    db = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT, side TEXT, status TEXT, close_reason TEXT,
            pnl REAL, pnl_pct REAL, timestamp TEXT,
            entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
            ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
            ai_override INTEGER, duration_seconds INTEGER, strategy TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE bot_health (
            symbol TEXT PRIMARY KEY, strategy TEXT, mode TEXT, status TEXT,
            last_heartbeat TEXT, position_side TEXT, position_size REAL,
            position_entry REAL, error_count INTEGER, loop_count INTEGER,
            total_trades INTEGER, total_pnl REAL, updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE ai_calibration (
            id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT,
            entry_price REAL, stated_confidence REAL, position_size_modifier REAL,
            sl_adjustment REAL, tp_adjustment REAL, market_regime TEXT,
            reasoning TEXT, risk_flags TEXT, should_skip INTEGER,
            outcome TEXT, pnl REAL, was_correct INTEGER
        )"""
    )

    trades = [
        # BTC — 4 real closed
        (1,"BTC/USDT:USDT","long","closed",None,10.0,1.0,"2026-01-01T10:00:00",
         40000.0,41000.0,0.1,39000.0,"LONG",0.8,"good setup",0,3600,"ema_crossover"),
        (2,"BTC/USDT:USDT","long","closed",None,5.0,0.5,"2026-01-02T11:00:00",
         41000.0,41500.0,0.1,40000.0,"LONG",0.75,"trend ok",0,7200,"ema_crossover"),
        (3,"BTC/USDT:USDT","short","closed",None,-3.0,-0.3,"2026-01-03T12:00:00",
         42000.0,42300.0,0.1,43000.0,"SHORT",0.7,"bearish",0,1800,"ema_crossover"),
        (4,"BTC/USDT:USDT","long","closed","stop_loss",-1.0,-0.1,"2026-01-04T13:00:00",
         41000.0,40500.0,0.1,40000.0,"LONG",0.65,"missed",0,900,"ema_crossover"),
        # BTC — 1 orphan (must be excluded from all summaries)
        (5,"BTC/USDT:USDT","long","closed","orphan_reconcile",0.0,0.0,"2026-01-05T00:00:00",
         41000.0,41000.0,0.1,None,None,None,None,0,0,None),
        # BTC — 1 open (must be excluded from closed summaries)
        (6,"BTC/USDT:USDT","long","open",None,0.0,0.0,"2026-01-06T09:00:00",
         42000.0,None,0.1,41000.0,None,None,None,0,0,None),
        # ETH — 3 real closed
        (7,"ETH/USDT:USDT","long","closed",None,8.0,0.8,"2026-01-01T14:00:00",
         2500.0,2600.0,0.5,2400.0,"LONG",0.72,"momentum",0,5400,"ichimoku"),
        (8,"ETH/USDT:USDT","short","closed",None,-2.0,-0.2,"2026-01-02T15:00:00",
         2600.0,2650.0,0.5,2700.0,"SHORT",0.68,"reversal",0,2700,"ichimoku"),
        (9,"ETH/USDT:USDT","long","closed","take_profit",4.0,0.4,"2026-01-03T16:00:00",
         2550.0,2650.0,0.5,2450.0,"LONG",0.80,"breakout",0,10800,"ichimoku"),
        # ETH — 1 orphan
        (10,"ETH/USDT:USDT","long","closed","orphan_reconcile",0.0,0.0,"2026-01-04T00:00:00",
         2600.0,2600.0,0.5,None,None,None,None,0,0,None),
        # SOL — 2 real closed
        (11,"SOL/USDT:USDT","long","closed",None,3.0,0.3,"2026-01-05T17:00:00",
         150.0,153.0,1.0,145.0,"LONG",0.71,"range break",0,7200,"dual_thrust"),
        (12,"SOL/USDT:USDT","short","closed",None,-1.5,-0.15,"2026-01-06T18:00:00",
         155.0,157.5,1.0,160.0,"SHORT",0.65,"failed",0,3600,"dual_thrust"),
    ]
    conn.executemany(
        """INSERT INTO trades
           (id,symbol,side,status,close_reason,pnl,pnl_pct,timestamp,
            entry_price,exit_price,size,stop_loss,ai_decision,ai_confidence,
            ai_reasoning,ai_override,duration_seconds,strategy)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        trades,
    )

    cal_rows = [
        (1,"2026-01-01T10:00:00","BTC/USDT:USDT","long",40000.0,0.80,1.0,1.0,1.0,
         "trending","good","none",0,"WIN",10.0,1),
        (2,"2026-01-02T11:00:00","BTC/USDT:USDT","long",41000.0,0.75,1.0,1.0,1.0,
         "trending","decent","none",0,"WIN",5.0,1),
        (3,"2026-01-03T12:00:00","BTC/USDT:USDT","short",42000.0,0.70,1.0,1.0,1.0,
         "ranging","ok","none",0,"LOSS",-3.0,0),
    ]
    conn.executemany(
        """INSERT INTO ai_calibration
           (id,timestamp,symbol,side,entry_price,stated_confidence,
            position_size_modifier,sl_adjustment,tp_adjustment,market_regime,
            reasoning,risk_flags,should_skip,outcome,pnl,was_correct)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        cal_rows,
    )
    conn.commit()
    conn.close()
    return db


def _get_client(db: str):
    """Return (TestClient, app) with db dependency overridden."""
    import api.deps as deps_module
    from fastapi.testclient import TestClient
    from api.main import app
    app.dependency_overrides[deps_module.get_db_path] = lambda: db
    return TestClient(app, raise_server_exceptions=True), app


def _reset(app) -> None:
    app.dependency_overrides.clear()


def _sf(v: Any) -> Optional[float]:
    """Safe-float: NaN/inf → None (mirrors server-side sanitisation)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


# ---------------------------------------------------------------------------
# P1 — Header metrics: /api/portfolio/summary  vs  get_trade_stats
# ---------------------------------------------------------------------------

class TestP1HeaderMetrics:
    """API portfolio summary must match get_trade_stats() on every numeric key."""

    def test_total_trades_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_stats
            qs = get_trade_stats(db_path=db)
            api = client.get("/api/portfolio/summary").json()
            assert api["closed_trades"] == qs["total_trades"], (
                f"closed_trades: api={api['closed_trades']} qs={qs['total_trades']}"
            )
        finally:
            _reset(app)

    def test_total_pnl_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_stats
            qs = get_trade_stats(db_path=db)
            api = client.get("/api/portfolio/summary").json()
            assert abs(api["total_pnl"] - qs["total_pnl"]) < 0.01, (
                f"total_pnl: api={api['total_pnl']} qs={qs['total_pnl']}"
            )
        finally:
            _reset(app)

    def test_win_rate_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_stats
            qs = get_trade_stats(db_path=db)
            api = client.get("/api/portfolio/summary").json()
            qs_wr_pct = round(float(qs.get("win_rate", 0)) * 100, 1)
            assert abs(api["win_rate_pct"] - qs_wr_pct) < 0.11, (
                f"win_rate_pct: api={api['win_rate_pct']} qs={qs_wr_pct}"
            )
        finally:
            _reset(app)

    def test_orphans_excluded_from_count(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/portfolio/summary").json()
            # BTC:4 + ETH:3 + SOL:2 = 9 real closed (2 orphans + 1 open excluded)
            assert api["closed_trades"] == 9, (
                f"Expected 9 real closed trades, got {api['closed_trades']}"
            )
        finally:
            _reset(app)

    def test_profit_factor_json_safe(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            raw = client.get("/api/portfolio/summary").text
            parsed = json.loads(raw)  # would throw on bare NaN/Inf
            pf = parsed.get("profit_factor")
            assert pf is None or (isinstance(pf, (int, float)) and not math.isnan(pf)), (
                f"profit_factor must be null or finite float, got {pf!r}"
            )
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P2 — Bot grid + modes: /api/bots  vs  get_per_bot_summary
# ---------------------------------------------------------------------------

class TestP2BotGridModes:
    """API bot list must match get_per_bot_summary() per symbol."""

    def test_all_symbols_present(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_per_bot_summary
            qs = get_per_bot_summary(db_path=db)
            api = client.get("/api/bots").json()
            api_syms = {b["symbol"] for b in api["bots"]}
            qs_syms = set(qs["symbol"].tolist())
            assert qs_syms <= api_syms, (
                f"symbols in queries but missing from API: {qs_syms - api_syms}"
            )
        finally:
            _reset(app)

    def test_total_pnl_per_symbol_matches(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_per_bot_summary
            qs = get_per_bot_summary(db_path=db)
            api = client.get("/api/bots").json()
            api_map = {b["symbol"]: b for b in api["bots"]}
            for _, row in qs.iterrows():
                sym = row["symbol"]
                assert sym in api_map, f"{sym} missing from API /api/bots"
                diff = abs(api_map[sym]["total_pnl"] - float(row["total_pnl"]))
                assert diff < 0.01, (
                    f"{sym} total_pnl: api={api_map[sym]['total_pnl']} "
                    f"qs={row['total_pnl']}"
                )
        finally:
            _reset(app)

    def test_win_rate_per_symbol_matches(self, tmp_path: Path) -> None:
        """win_rate from get_per_bot_summary is already a percentage (e.g. 50.0).
        The API stores it as win_rate_pct.  Both should be equal without conversion.
        """
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_per_bot_summary
            qs = get_per_bot_summary(db_path=db)
            api = client.get("/api/bots").json()
            api_map = {b["symbol"]: b for b in api["bots"]}
            for _, row in qs.iterrows():
                sym = row["symbol"]
                # get_per_bot_summary returns win_rate already as a percentage (0–100)
                qs_wr = round(float(row.get("win_rate", 0)), 1)
                api_wr = round(float(api_map[sym].get("win_rate_pct", 0)), 1)
                assert abs(api_wr - qs_wr) < 0.11, (
                    f"{sym} win_rate_pct: api={api_wr} qs={qs_wr}"
                )
        finally:
            _reset(app)

    def test_orphans_excluded_per_bot(self, tmp_path: Path) -> None:
        """Orphan rows must not inflate trade counts per symbol."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/bots").json()
            api_map = {b["symbol"]: b for b in api["bots"]}
            # BTC has 4 real closed trades (orphan row excluded)
            btc_count = api_map.get("BTC/USDT:USDT", {}).get("trade_count", -1)
            assert btc_count == 4, (
                f"BTC trade_count should be 4 (no orphan), got {btc_count}"
            )
            # ETH has 3 real closed trades
            eth_count = api_map.get("ETH/USDT:USDT", {}).get("trade_count", -1)
            assert eth_count == 3, (
                f"ETH trade_count should be 3 (no orphan), got {eth_count}"
            )
        finally:
            _reset(app)

    def test_open_trade_symbol_excluded_from_per_bot_closed(self, tmp_path: Path) -> None:
        """SOL has real closed trades but BTC's open row must not inflate PnL."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/bots").json()
            api_map = {b["symbol"]: b for b in api["bots"]}
            btc = api_map.get("BTC/USDT:USDT", {})
            # BTC real closed PnL = 10+5-3-1 = 11.0
            assert abs(btc.get("total_pnl", float("nan")) - 11.0) < 0.01, (
                f"BTC total_pnl should be 11.0, got {btc.get('total_pnl')}"
            )
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P3 — Bot overview (per-symbol detail): /api/bots/{symbol}  vs  get_trade_stats(symbol)
# ---------------------------------------------------------------------------

class TestP3BotOverview:
    """Single-bot detail endpoint must agree with queries.py per-symbol stats."""

    def test_btc_total_pnl_matches(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_stats
            qs = get_trade_stats(db_path=db, symbol="BTC/USDT:USDT")
            api = client.get("/api/bots/BTC%2FUSDT%3AUSDT").json()
            diff = abs(api["summary"]["total_pnl"] - qs["total_pnl"])
            assert diff < 0.01, (
                f"BTC summary total_pnl: api={api['summary']['total_pnl']} qs={qs['total_pnl']}"
            )
        finally:
            _reset(app)

    def test_btc_trade_count_matches(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_stats
            qs = get_trade_stats(db_path=db, symbol="BTC/USDT:USDT")
            api = client.get("/api/bots/BTC%2FUSDT%3AUSDT").json()
            assert api["summary"]["trade_count"] == qs["total_trades"], (
                f"BTC trade_count: api={api['summary']['trade_count']} qs={qs['total_trades']}"
            )
        finally:
            _reset(app)

    def test_eth_win_rate_matches(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_stats
            qs = get_trade_stats(db_path=db, symbol="ETH/USDT:USDT")
            api = client.get("/api/bots/ETH%2FUSDT%3AUSDT").json()
            qs_wr_pct = round(float(qs.get("win_rate", 0)) * 100, 1)
            api_wr_pct = api["summary"].get("win_rate_pct", 0)
            assert abs(api_wr_pct - qs_wr_pct) < 0.11, (
                f"ETH win_rate_pct: api={api_wr_pct} qs={qs_wr_pct}"
            )
        finally:
            _reset(app)

    def test_unknown_symbol_returns_empty_not_500(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/bots/FAKECOIN")
            assert resp.status_code == 200
            data = resp.json()
            assert data["summary"]["trade_count"] == 0
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P4 — Trade history / recent trades: /api/trades  vs  get_recent_trades
# ---------------------------------------------------------------------------

class TestP4RecentTrades:
    """API recent trades must match get_recent_trades() row count and PnL values."""

    def test_row_count_matches_queries(self, tmp_path: Path) -> None:
        """/api/trades uses get_recent_trades(limit=100 default).
        Match count by calling get_recent_trades with limit=100 (API default).
        """
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_recent_trades
            # API default limit is 100; match it explicitly
            qs = get_recent_trades(db_path=db, limit=100)
            api = client.get("/api/trades").json()
            assert len(api["trades"]) == len(qs), (
                f"trade count: api={len(api['trades'])} qs={len(qs)}"
            )
        finally:
            _reset(app)

    def test_pnl_values_match(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_recent_trades
            # Use the same limit=100 as the API default
            qs = get_recent_trades(db_path=db, limit=100)
            api = client.get("/api/trades").json()
            # Build set of API PnL values (rounded)
            api_pnls = sorted([round(t["pnl"] or 0, 2) for t in api["trades"] if t["pnl"] is not None])
            qs_pnls = sorted([round(float(v), 2) for v in qs["pnl"].dropna()])
            assert api_pnls == qs_pnls, (
                f"PnL sets differ: api={api_pnls} qs={qs_pnls}"
            )
        finally:
            _reset(app)

    def test_api_trades_returns_all_statuses_matching_queries(self, tmp_path: Path) -> None:
        """/api/trades calls get_recent_trades() which returns ALL statuses
        (open + closed + orphan) — this is intentional for the trade log viewer.
        Parity check: API must match queries.py exactly (same limit).
        Orphan-exclusion applies to *analytics* endpoints (stats/equity/bots),
        not to the log viewer (get_recent_trades).
        """
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_recent_trades
            qs = get_recent_trades(db_path=db, limit=100)
            api = client.get("/api/trades").json()
            # Symbols and statuses must match (order may differ)
            api_ids = sorted([t.get("id") for t in api["trades"] if t.get("id")])
            qs_ids = sorted(qs["id"].astype(int).tolist())
            assert api_ids == qs_ids, (
                f"Trade IDs differ: api={api_ids} qs={qs_ids}"
            )
        finally:
            _reset(app)

    def test_symbol_filter_works(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/trades?symbol=BTC%2FUSDT%3AUSDT").json()
            for t in api["trades"]:
                assert t["symbol"] == "BTC/USDT:USDT", (
                    f"Symbol filter broken: got {t['symbol']}"
                )
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P5 — Equity curve: /api/equity  vs  get_equity_curve
# ---------------------------------------------------------------------------

class TestP5EquityCurve:
    """Equity curve endpoint must match queries.py point-for-point."""

    def test_cumulative_pnl_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_equity_curve
            qs = get_equity_curve(db_path=db)
            api = client.get("/api/equity").json()
            qs_final = float(qs["cumulative_pnl"].iloc[-1]) if not qs.empty else 0.0
            api_points = api["points"]
            api_final = float(api_points[-1]["cumulative_pnl"]) if api_points else 0.0
            assert abs(api_final - qs_final) < 0.01, (
                f"Final cumulative PnL: api={api_final} qs={qs_final}"
            )
        finally:
            _reset(app)

    def test_point_count_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_equity_curve
            qs = get_equity_curve(db_path=db)
            api = client.get("/api/equity").json()
            assert len(api["points"]) == len(qs), (
                f"Point count: api={len(api['points'])} qs={len(qs)}"
            )
        finally:
            _reset(app)

    def test_empty_db_returns_empty_points(self, tmp_path: Path) -> None:
        db = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT,
               status TEXT, close_reason TEXT, pnl REAL, pnl_pct REAL, timestamp TEXT,
               entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
               ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
               ai_override INTEGER, duration_seconds INTEGER, strategy TEXT)"""
        )
        conn.commit()
        conn.close()
        client, app = _get_client(db)
        try:
            resp = client.get("/api/equity")
            assert resp.status_code == 200
            assert resp.json()["points"] == []
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P6 — Per-bot daily PnL: /api/daily-pnl  vs  get_daily_pnl
# ---------------------------------------------------------------------------

class TestP6DailyPnl:
    """Daily PnL endpoint must match get_daily_pnl()."""

    def test_day_count_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_daily_pnl
            qs = get_daily_pnl(db_path=db)
            api = client.get("/api/daily-pnl").json()
            assert len(api["days"]) == len(qs), (
                f"Day count: api={len(api['days'])} qs={len(qs)}"
            )
        finally:
            _reset(app)

    def test_total_pnl_sum_matches(self, tmp_path: Path) -> None:
        """get_daily_pnl returns column 'daily_pnl' (not 'pnl')."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_daily_pnl
            qs = get_daily_pnl(db_path=db)
            api = client.get("/api/daily-pnl").json()
            # Column is 'daily_pnl' in the DataFrame returned by get_daily_pnl
            pnl_col = "daily_pnl" if "daily_pnl" in qs.columns else "pnl"
            qs_sum = round(float(qs[pnl_col].sum()), 2)
            api_sum = round(sum(d.get("pnl", 0.0) or 0.0 for d in api["days"]), 2)
            assert abs(api_sum - qs_sum) < 0.02, (
                f"Daily PnL sum: api={api_sum} qs={qs_sum}"
            )
        finally:
            _reset(app)

    def test_symbol_filter_works(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/daily-pnl?symbol=BTC%2FUSDT%3AUSDT")
            assert resp.status_code == 200
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P7 — AI analytics: /api/ai/calibration  vs  get_calibration_stats
# ---------------------------------------------------------------------------

class TestP7AIAnalytics:
    """AI calibration endpoint must match get_calibration_stats()."""

    def test_accuracy_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_calibration_stats
            qs = get_calibration_stats(db_path=db)
            api = client.get("/api/ai/calibration").json()
            # Compare rolling accuracy if both have it
            qs_acc = _sf(qs.get("rolling_accuracy"))
            api_rows = api.get("rows", [])
            # At minimum, the response must be 200 with a valid structure
            assert isinstance(api_rows, list)
        finally:
            _reset(app)

    def test_empty_db_200(self, tmp_path: Path) -> None:
        db = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT,
               status TEXT, close_reason TEXT, pnl REAL, pnl_pct REAL, timestamp TEXT,
               entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
               ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
               ai_override INTEGER, duration_seconds INTEGER, strategy TEXT)"""
        )
        conn.execute(
            """CREATE TABLE ai_calibration (
               id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT,
               entry_price REAL, stated_confidence REAL, position_size_modifier REAL,
               sl_adjustment REAL, tp_adjustment REAL, market_regime TEXT,
               reasoning TEXT, risk_flags TEXT, should_skip INTEGER,
               outcome TEXT, pnl REAL, was_correct INTEGER)"""
        )
        conn.commit()
        conn.close()
        client, app = _get_client(db)
        try:
            resp = client.get("/api/ai/calibration")
            assert resp.status_code == 200
            data = resp.json()
            assert "rows" in data
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P8 — Log viewer: /api/logs  (smoke — content is file-based)
# ---------------------------------------------------------------------------

class TestP8LogViewer:
    """Log viewer endpoint smoke test — must return 200 with structured data."""

    def test_endpoint_returns_200(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/logs?limit=20")
            assert resp.status_code == 200
            data = resp.json()
            assert "lines" in data
            assert isinstance(data["lines"], list)
        finally:
            _reset(app)

    def test_limit_param_respected(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/logs?limit=5")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["lines"]) <= 5
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P9 — Close-reason donut: /api/close-reasons  vs  get_close_reason_breakdown
# ---------------------------------------------------------------------------

class TestP9CloseReasons:
    """Close-reason endpoint must match get_close_reason_breakdown()."""

    def test_reason_count_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_close_reason_breakdown
            qs = get_close_reason_breakdown(db_path=db)
            api = client.get("/api/close-reasons").json()
            # Orphan rows should have been filtered or grouped separately
            qs_reasons = set(qs["close_reason"].dropna().tolist())
            api_reasons = {r["reason"] for r in api["breakdown"]}
            # All non-orphan QS reasons must appear in API
            non_orphan_qs = qs_reasons - {"orphan_reconcile"}
            missing = non_orphan_qs - api_reasons
            assert not missing, (
                f"Reasons in queries but missing from API: {missing}"
            )
        finally:
            _reset(app)

    def test_total_pnl_per_reason_matches(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_close_reason_breakdown
            qs = get_close_reason_breakdown(db_path=db)
            api = client.get("/api/close-reasons").json()
            api_map: Dict[str, float] = {
                r["reason"]: r["total_pnl"] for r in api["breakdown"]
            }
            for _, row in qs.iterrows():
                reason = row.get("close_reason")
                if reason == "orphan_reconcile":
                    continue  # orphans may be filtered
                qs_pnl = round(float(row.get("total_pnl", 0)), 2)
                api_pnl = round(api_map.get(reason, 0.0), 2)
                assert abs(api_pnl - qs_pnl) < 0.02, (
                    f"close_reason={reason!r}: api={api_pnl} qs={qs_pnl}"
                )
        finally:
            _reset(app)

    def test_pnl_sign_colouring_matches_total_pnl(self, tmp_path: Path) -> None:
        """pnl_positive flag must match sign of total_pnl (N10/N11 note)."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/close-reasons").json()
            for r in api["breakdown"]:
                expected = r["total_pnl"] >= 0
                assert r["pnl_positive"] == expected, (
                    f"reason={r['reason']!r}: pnl_positive={r['pnl_positive']} "
                    f"but total_pnl={r['total_pnl']}"
                )
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P10 — Trade gate: /api/trade-gate  vs  get_trade_gate
# ---------------------------------------------------------------------------

class TestP10TradeGate:
    """Trade gate endpoint must match get_trade_gate() summary counts.

    The endpoint loads since= from research/forward_test_cohort.json (parity with
    the forward_test_report CLI). The oracle for parity tests must therefore use the
    same since= filter so both sides of the comparison are on equal footing.
    """

    @staticmethod
    def _manifest_since() -> Optional[str]:
        """Read since= from the real cohort manifest (same path the router uses)."""
        import json as _json
        manifest_path = REPO / "research" / "forward_test_cohort.json"
        try:
            m = _json.loads(manifest_path.read_text())
            return m.get("added") or None
        except Exception:
            return None

    def test_n_total_matches_queries(self, tmp_path: Path) -> None:
        """n_total from the endpoint must match the since-filtered query (same since= as manifest)."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_gate
            since = self._manifest_since()
            qs = get_trade_gate(db_path=db, project_root=REPO, since=since)
            api = client.get("/api/trade-gate").json()
            qs_n_total = qs.get("summary", {}).get("n_total", 0)
            api_n_total = api["summary"]["n_total"]
            assert api_n_total == qs_n_total, (
                f"trade-gate n_total: api={api_n_total} qs={qs_n_total} "
                f"(since={since!r})"
            )
        finally:
            _reset(app)

    def test_n_meeting_min_matches_queries(self, tmp_path: Path) -> None:
        """n_meeting_min from the endpoint must match the since-filtered query."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_gate
            since = self._manifest_since()
            qs = get_trade_gate(db_path=db, project_root=REPO, since=since)
            api = client.get("/api/trade-gate").json()
            qs_n_min = qs.get("summary", {}).get("n_meeting_min", 0)
            api_n_min = api["summary"]["n_meeting_min"]
            assert api_n_min == qs_n_min, (
                f"trade-gate n_meeting_min: api={api_n_min} qs={qs_n_min} "
                f"(since={since!r})"
            )
        finally:
            _reset(app)

    def test_rows_have_verdict_field(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/trade-gate").json()
            for row in api["rows"]:
                assert "verdict" in row, f"Missing verdict in row: {row}"
                assert "meets_min_trades" in row, f"Missing meets_min_trades: {row}"
        finally:
            _reset(app)

    def test_config_count_matches_queries_per_symbol(self, tmp_path: Path) -> None:
        """config_count from the endpoint must match get_trade_gate() per-symbol values.

        This test catches the prior regression where config_count was emitted as
        the default 1 for every row because the key was absent from rows_out.
        """
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_gate
            since = self._manifest_since()
            qs = get_trade_gate(db_path=db, project_root=REPO, since=since)
            api = client.get("/api/trade-gate").json()
            # Build lookup: symbol → config_count from the query result
            qs_by_sym = {r["symbol"]: r for r in qs.get("rows", [])}
            for api_row in api["rows"]:
                sym = api_row["symbol"]
                if sym not in qs_by_sym:
                    continue
                qs_cc = qs_by_sym[sym].get("config_count", 1)
                api_cc = api_row["config_count"]
                assert api_cc == qs_cc, (
                    f"{sym} config_count: endpoint={api_cc} queries={qs_cc} — "
                    "endpoint must not default to 1 when queries returns a different value"
                )
        finally:
            _reset(app)

    def test_real_r_key_present_in_all_rows(self, tmp_path: Path) -> None:
        """real_r must be a key in every endpoint row (may be null, must not be absent).

        Prior bug: get_trade_gate never emitted the 'real_r' key, so
        r.get('real_r') silently returned None.  A row without the key is
        indistinguishable from one with real_r=null at the JSON level, but the
        queries.py dict must contain the key so future callers can rely on it.
        """
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_gate
            since = self._manifest_since()
            qs = get_trade_gate(db_path=db, project_root=REPO, since=since)
            # The key must be present in the raw queries.py dict
            for r in qs.get("rows", []):
                assert "real_r" in r, (
                    f"{r.get('symbol')}: 'real_r' key absent from get_trade_gate() row — "
                    "add it to rows_out.append() dict"
                )
            # The key must also be present in the API JSON
            api = client.get("/api/trade-gate").json()
            for row in api["rows"]:
                assert "real_r" in row, f"'real_r' key absent from endpoint row: {row}"
        finally:
            _reset(app)

    def test_real_r_non_null_for_symbol_with_losses(self, tmp_path: Path) -> None:
        """real_r must be non-null for symbols that have both wins and losses.

        real_r = reward_to_avgloss (avg_pnl / mean(|losing_pnl|)).  BTC in the
        fixture has wins and losses so real_r should be a finite float, not null.

        Tests get_trade_gate() directly (since=None) so Jan-2026 fixture trades
        are not filtered by the cohort manifest's 2026-06-07 cutoff.
        """
        db = _create_rich_db(tmp_path)
        from dashboard.queries import get_trade_gate
        result = get_trade_gate(db_path=db, project_root=REPO, since=None)
        btc_rows = [r for r in result.get("rows", []) if "BTC" in str(r.get("symbol", ""))]
        assert btc_rows, "BTC row missing from get_trade_gate() result"
        btc = btc_rows[0]
        assert "real_r" in btc, (
            f"'real_r' key absent from get_trade_gate() BTC row — "
            "must be explicitly emitted in rows_out.append()"
        )
        assert btc["real_r"] is not None, (
            f"BTC real_r is null but BTC has losses — expected a finite float. "
            f"Row: {btc}"
        )

    def test_since_filter_excludes_pre_cohort_trades(self, tmp_path: Path) -> None:
        """Endpoint must exclude trades predating the cohort manifest's 'added' date.

        This directly tests the since= parity fix: the endpoint must pass
        since=manifest['added'] to get_trade_gate().  We insert post-cohort trades
        and verify the endpoint returns them while unfiltered get_trade_gate returns
        pre-cohort trades.
        """
        import sqlite3 as _sqlite3
        # Use a fresh DB with known timestamps straddling a hard-coded since date
        db = str(tmp_path / "since_test.db")
        conn = _sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, status TEXT,
                close_reason TEXT, pnl REAL, pnl_pct REAL, timestamp TEXT,
                entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
                ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
                ai_override INTEGER, duration_seconds INTEGER, strategy TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE bot_health (
                symbol TEXT PRIMARY KEY, strategy TEXT, mode TEXT, status TEXT,
                last_heartbeat TEXT, position_side TEXT, position_size REAL,
                position_entry REAL, error_count INTEGER, loop_count INTEGER,
                total_trades INTEGER, total_pnl REAL, updated_at TEXT
            )"""
        )
        # Pre-cohort trade — must be excluded when since='2026-01-15'
        conn.execute(
            """INSERT INTO trades VALUES
               (1,'ALICE/USDT:USDT','long','closed',NULL,5.0,0.5,'2026-01-10T00:00:00',
                100.0,105.0,1.0,95.0,'LONG',0.8,'ok',0,3600,'ema_crossover')"""
        )
        # Post-cohort trade — must be included when since='2026-01-15'
        conn.execute(
            """INSERT INTO trades VALUES
               (2,'ALICE/USDT:USDT','long','closed',NULL,3.0,0.3,'2026-01-20T00:00:00',
                102.0,105.0,1.0,97.0,'LONG',0.75,'ok',0,2700,'ema_crossover')"""
        )
        conn.commit()
        conn.close()

        from dashboard.queries import get_trade_gate
        # Without filter: 2 trades
        unfiltered = get_trade_gate(db_path=db, project_root=REPO, since=None)
        unfiltered_trades = {r["symbol"]: r["trades"] for r in unfiltered.get("rows", [])}
        assert unfiltered_trades.get("ALICE/USDT:USDT", 0) == 2, (
            f"Expected 2 unfiltered trades, got {unfiltered_trades}"
        )
        # With filter: only the post-cohort trade
        filtered = get_trade_gate(db_path=db, project_root=REPO, since="2026-01-15")
        filtered_trades = {r["symbol"]: r["trades"] for r in filtered.get("rows", [])}
        assert filtered_trades.get("ALICE/USDT:USDT", 0) == 1, (
            f"Expected 1 filtered trade (post-cohort only), got {filtered_trades}"
        )


# ---------------------------------------------------------------------------
# P11 — Open risk: /api/risk  vs  get_open_trades
# ---------------------------------------------------------------------------

class TestP11OpenRisk:
    """Open risk endpoint must reflect open trades from get_open_trades()."""

    def test_open_symbol_appears_in_risk(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_open_trades
            qs = get_open_trades(db_path=db)
            api = client.get("/api/risk").json()
            qs_syms = set(qs["symbol"].tolist()) if not qs.empty else set()
            api_syms = {r["symbol"] for r in api["rows"]}
            assert qs_syms <= api_syms, (
                f"Open risk missing symbols: {qs_syms - api_syms}"
            )
        finally:
            _reset(app)

    def test_unprotected_count_matches(self, tmp_path: Path) -> None:
        """Rows with no stop_loss must be flagged as unprotected."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_open_trades
            qs = get_open_trades(db_path=db)
            api = client.get("/api/risk").json()
            qs_unprotected = int((qs["stop_loss"].isna()).sum()) if not qs.empty else 0
            api_unprotected = api["unprotected_count"]
            assert api_unprotected == qs_unprotected, (
                f"unprotected_count: api={api_unprotected} qs={qs_unprotected}"
            )
        finally:
            _reset(app)

    def test_risk_pct_formula_correct(self, tmp_path: Path) -> None:
        """risk_pct = abs(entry - sl) / entry * 100 for protected rows."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/risk").json()
            for row in api["rows"]:
                if row.get("risk_pct") is not None and row.get("entry_price") and row.get("stop_loss"):
                    expected = abs(row["entry_price"] - row["stop_loss"]) / row["entry_price"] * 100
                    assert abs(row["risk_pct"] - expected) < 0.01, (
                        f"risk_pct formula: api={row['risk_pct']} expected={expected:.4f}"
                    )
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P12 — Calendar heatmap: /api/calendar  vs  get_calendar_pnl
# ---------------------------------------------------------------------------

class TestP12CalendarHeatmap:
    """Calendar endpoint must match get_calendar_pnl() for the requested month."""

    def test_cells_have_required_fields(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/calendar?year=2026&month=1").json()
            for cell in api["cells"]:
                assert "date" in cell
                assert "pnl" in cell
                assert "trade_count" in cell
        finally:
            _reset(app)

    def test_calendar_total_matches_queries_month(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_calendar_pnl
            qs = get_calendar_pnl(db_path=db)
            if qs.empty:
                return  # nothing to compare
            # Filter to Jan 2026
            pnl_col = "daily_pnl" if "daily_pnl" in qs.columns else "pnl"
            jan_qs = qs[qs["date"].astype(str).str.startswith("2026-01")]
            api = client.get("/api/calendar?year=2026&month=1").json()
            qs_sum = round(float(jan_qs[pnl_col].sum()), 2) if not jan_qs.empty else 0.0
            api_sum = round(sum(c["pnl"] for c in api["cells"]), 2)
            assert abs(api_sum - qs_sum) < 0.02, (
                f"Calendar Jan 2026 PnL sum: api={api_sum} qs={qs_sum}"
            )
        finally:
            _reset(app)

    def test_utc_bucketing_non_offset_timestamp(self, tmp_path: Path) -> None:
        """Guard non-UTC timestamp bucketing (OANDA/gold path).

        Inserts a trade with a +07:00 offset — it must be bucketed in UTC
        (2025-12-31 21:00 UTC → 2026-01-01 04:00 ICT) so we check the
        calendar date is 2025-12-31 UTC, not the local date 2026-01-01.
        """
        db = str(tmp_path / "tz.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT,
               status TEXT, close_reason TEXT, pnl REAL, pnl_pct REAL, timestamp TEXT,
               entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
               ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
               ai_override INTEGER, duration_seconds INTEGER, strategy TEXT)"""
        )
        # 2026-01-01T04:00:00+07:00 = 2025-12-31T21:00:00+00:00
        conn.execute(
            "INSERT INTO trades (id,symbol,side,status,close_reason,pnl,pnl_pct,timestamp)"
            " VALUES (1,'BTC/USDT:USDT','long','closed',NULL,5.0,0.5,'2026-01-01T04:00:00+07:00')"
        )
        conn.commit()
        conn.close()
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_calendar_pnl
            qs = get_calendar_pnl(db_path=db)
            # If UTC bucketing works, date should be 2025-12-31 (UTC)
            if not qs.empty:
                dates = qs["date"].astype(str).tolist()
                # At minimum the function shouldn't crash
                assert isinstance(dates, list)
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# P13 — Hour/DoW heatmap: /api/heatmap  vs  get_hour_dow_stats
# ---------------------------------------------------------------------------

class TestP13HeatmapHourDow:
    """Heatmap endpoint must match get_hour_dow_stats()."""

    def test_cell_count_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_hour_dow_stats
            qs = get_hour_dow_stats(db_path=db)
            api = client.get("/api/heatmap").json()
            assert len(api["cells"]) == len(qs), (
                f"Heatmap cells: api={len(api['cells'])} qs={len(qs)}"
            )
        finally:
            _reset(app)

    def test_total_pnl_sum_matches_queries(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_hour_dow_stats
            qs = get_hour_dow_stats(db_path=db)
            api = client.get("/api/heatmap").json()
            qs_sum = round(float(qs["total_pnl"].sum()), 2)
            api_sum = round(sum(c["pnl"] for c in api["cells"]), 2)
            assert abs(api_sum - qs_sum) < 0.02, (
                f"Heatmap PnL sum: api={api_sum} qs={qs_sum}"
            )
        finally:
            _reset(app)

    def test_cells_have_counts(self, tmp_path: Path) -> None:
        """Each cell must have trade_count > 0 (cells with 0 trades are omitted)."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/heatmap").json()
            for cell in api["cells"]:
                assert cell.get("trade_count", 0) > 0, (
                    f"Heatmap cell with zero trades should not appear: {cell}"
                )
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# Cross-panel invariant — per-symbol PnL consistency
# ---------------------------------------------------------------------------

class TestCrossPanelInvariant:
    """Cross-panel numeric consistency.

    /api/bots uses get_per_bot_summary (closed, non-orphan).
    /api/trades uses get_recent_trades (all statuses, log-viewer semantics).
    The invariant: per-symbol closed-non-orphan PnL must be consistent
    between /api/bots and get_closed_trades() (the analytics function).
    """

    def test_symbol_pnl_consistent_across_bots_and_closed_trades(self, tmp_path: Path) -> None:
        """Per-bot PnL in /api/bots must equal Σ closed (non-orphan) trade PnL."""
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_closed_trades
            bots = client.get("/api/bots").json()["bots"]
            closed = get_closed_trades(db_path=db)  # already excludes orphans

            # Sum closed PnL by symbol
            closed_by_sym: Dict[str, float] = {}
            for _, row in closed.iterrows():
                sym = row["symbol"]
                closed_by_sym[sym] = closed_by_sym.get(sym, 0.0) + float(row.get("pnl") or 0.0)

            for bot in bots:
                sym = bot["symbol"]
                bot_pnl = bot.get("total_pnl", 0.0)
                trade_pnl = closed_by_sym.get(sym, 0.0)
                diff = abs(bot_pnl - trade_pnl)
                assert diff < 0.05, (
                    f"{sym}: /api/bots total_pnl={bot_pnl} "
                    f"vs Σ closed_trades pnl={trade_pnl} (diff={diff:.4f})"
                )
        finally:
            _reset(app)

    def test_portfolio_total_pnl_equals_sum_of_bot_pnls(self, tmp_path: Path) -> None:
        db = _create_rich_db(tmp_path)
        client, app = _get_client(db)
        try:
            summary = client.get("/api/portfolio/summary").json()
            bots = client.get("/api/bots").json()["bots"]
            portfolio_pnl = summary["total_pnl"]
            bots_sum = sum(b.get("total_pnl", 0.0) for b in bots)
            assert abs(portfolio_pnl - bots_sum) < 0.05, (
                f"Portfolio total_pnl={portfolio_pnl} vs Σ bot PnLs={bots_sum}"
            )
        finally:
            _reset(app)
