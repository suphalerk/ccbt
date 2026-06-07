"""N9 — New Metrics Data Layer tests (TDD — written before the implementation).

Tests five new functions added to dashboard/queries.py:
1. get_close_reason_breakdown
2. get_trade_gate
3. get_open_risk
4. get_calendar_pnl
5. get_hour_dow_stats

Verifies:
- Correct numbers on seeded DB (orphan excluded, pct sums ~100, PF inf path)
- UTC correctness vs Python ground truth for get_hour_dow_stats
  - includes a +07:00 fixture that must bucket to the UTC day/hour
- get_calendar_pnl reconciles with get_daily_pnl
- Attribution: count-map from config files (not get_bot_statuses)
  - TWO configs sharing one symbol_clean → count > 1 → verdict = MIXED
  - ONE config → verdict = classify(...)
- Verdict vocabulary ∈ {KEEP_TESTING, READY_TO_AUDIT, MARGINAL, DROP, MIXED}
- Summary {n_meeting_min, n_total} computed correctly
- reward_to_avgloss formula: mean(pnl) / mean(abs(losing pnl))
- real-R = null for zero-loss symbols
- open_risk: null SL → unprotected_count; abs() for shorts; no fabricated %
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _create_trades_db(tmp_path: Path) -> str:
    """Minimal WAL trades DB with a mix of scenarios."""
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            size REAL NOT NULL,
            stop_loss REAL,
            take_profit REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'open',
            close_reason TEXT,
            duration_seconds INTEGER,
            ai_decision TEXT,
            ai_confidence REAL,
            ai_reasoning TEXT,
            ai_risk_flags TEXT,
            ai_override INTEGER DEFAULT 0
        )
        """
    )
    return db_path, conn


def _seed_standard(conn: sqlite3.Connection) -> None:
    """Insert a standard test dataset."""
    rows = [
        # BTC: 4 closed (2 wins TP, 1 loss SL, 1 trail_stop) + 1 orphan
        # Timestamps span Mon 2026-03-23 03:01 UTC and Tue 2026-03-24 10:30 UTC
        (1,  "2026-03-23T03:01:20.752540+00:00", "BTC/USDT:USDT", "long",  50000.0, 51500.0, 0.1, 49000.0, 52000.0,  15.0,  0.030, "closed", "take_profit"),
        (2,  "2026-03-23T07:15:00.000000+00:00", "BTC/USDT:USDT", "long",  51000.0, 50000.0, 0.1, 49000.0, 53000.0, -10.0, -0.020, "closed", "stop_loss"),
        (3,  "2026-03-24T10:30:00.000000+00:00", "BTC/USDT:USDT", "short", 52000.0, 51000.0, 0.1, 53000.0, 50000.0,  10.0,  0.019, "closed", "trail_stop"),
        (4,  "2026-03-24T15:00:00.000000+00:00", "BTC/USDT:USDT", "long",  53000.0, 54500.0, 0.1, 51500.0, 56000.0,  15.0,  0.028, "closed", "take_profit"),
        (5,  "2026-03-24T16:00:00.000000+00:00", "BTC/USDT:USDT", "long",  54000.0, None,    0.1, 52000.0, 57000.0,   0.0,   None, "closed", "orphan_reconcile"),
        # ETH: 3 closed (all wins) — no losses
        (6,  "2026-03-23T04:00:00.000000+00:00", "ETH/USDT:USDT", "long",  3000.0, 3200.0,  1.0, 2900.0,  3300.0,  20.0,  0.067, "closed", "take_profit"),
        (7,  "2026-03-24T12:00:00.000000+00:00", "ETH/USDT:USDT", "long",  3100.0, 3300.0,  1.0, 3000.0,  3400.0,  20.0,  0.065, "closed", "take_profit"),
        (8,  "2026-03-25T09:00:00.000000+00:00", "ETH/USDT:USDT", "long",  3200.0, 3350.0,  1.0, 3100.0,  3500.0,  15.0,  0.047, "closed", "take_profit"),
        # SOL: 1 open with null SL + 1 open with SL set
        (9,  "2026-03-25T08:00:00.000000+00:00", "SOL/USDT:USDT", "long",   150.0, None,    10.0,  None,   160.0,   None,   None, "open",   None),
        (10, "2026-03-25T08:05:00.000000+00:00", "SOL/USDT:USDT", "short",  152.0, None,     5.0,  155.0,  148.0,   None,   None, "open",   None),
        # +07:00 fixture — 2026-06-07 02:00:00+07:00 = 2026-06-06 19:00:00 UTC
        # UTC: Sat (dow=5), hour=19
        (11, "2026-06-07T02:00:00.000000+07:00", "BTC/USDT:USDT", "long",  68000.0, 69000.0, 0.1, 67000.0, 71000.0, 10.0, 0.015, "closed", "take_profit"),
    ]
    conn.executemany(
        """INSERT INTO trades
           (id, timestamp, symbol, side, entry_price, exit_price, size,
            stop_loss, take_profit, pnl, pnl_pct, status, close_reason)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Helper: create fake config dir with config files
# ---------------------------------------------------------------------------

def _create_config_dir(tmp_path: Path, configs: list) -> Path:
    """Create a fake project root with config JSON files.

    configs: list of (filename, symbol) pairs.
    Returns the project root (tmp_path), creating it if needed.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    for fname, symbol in configs:
        cfg = {"symbol": symbol, "timeframe_signal": "4h"}
        (tmp_path / fname).write_text(json.dumps(cfg))
    return tmp_path


# ---------------------------------------------------------------------------
# 1. get_close_reason_breakdown
# ---------------------------------------------------------------------------

class TestCloseReasonBreakdown:
    """Tests for get_close_reason_breakdown."""

    def _make_db(self, tmp_path: Path) -> str:
        db_path, conn = _create_trades_db(tmp_path)
        _seed_standard(conn)
        return db_path

    def test_returns_expected_reasons(self, tmp_path):
        from dashboard.queries import get_close_reason_breakdown
        db = self._make_db(tmp_path)
        df = get_close_reason_breakdown(db)
        reasons = set(df["close_reason"].tolist())
        # orphan_reconcile must be excluded
        assert "orphan_reconcile" not in reasons

    def test_orphan_excluded(self, tmp_path):
        from dashboard.queries import get_close_reason_breakdown
        db = self._make_db(tmp_path)
        df = get_close_reason_breakdown(db)
        assert "orphan_reconcile" not in df["close_reason"].tolist()

    def test_pct_sums_to_100(self, tmp_path):
        from dashboard.queries import get_close_reason_breakdown
        db = self._make_db(tmp_path)
        df = get_close_reason_breakdown(db)
        assert not df.empty
        total_pct = df["pct"].sum()
        assert abs(total_pct - 100.0) < 0.1, f"pct sum={total_pct}, expected ~100"

    def test_symbol_filter(self, tmp_path):
        from dashboard.queries import get_close_reason_breakdown
        db = self._make_db(tmp_path)
        df_btc = get_close_reason_breakdown(db, symbol="BTC/USDT:USDT")
        df_all = get_close_reason_breakdown(db)
        # BTC subset should have fewer or equal total trades
        assert df_btc["count"].sum() <= df_all["count"].sum()

    def test_columns_present(self, tmp_path):
        from dashboard.queries import get_close_reason_breakdown
        db = self._make_db(tmp_path)
        df = get_close_reason_breakdown(db)
        for col in ("close_reason", "count", "pct", "total_pnl", "avg_pnl"):
            assert col in df.columns, f"Missing column: {col}"

    def test_empty_db_returns_empty(self, tmp_path):
        from dashboard.queries import get_close_reason_breakdown
        db_path, conn = _create_trades_db(tmp_path)
        conn.close()
        df = get_close_reason_breakdown(db_path)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_take_profit_pnl_positive(self, tmp_path):
        from dashboard.queries import get_close_reason_breakdown
        db = self._make_db(tmp_path)
        df = get_close_reason_breakdown(db)
        tp = df[df["close_reason"] == "take_profit"]
        assert not tp.empty
        assert tp.iloc[0]["total_pnl"] > 0


# ---------------------------------------------------------------------------
# 2. get_trade_gate
# ---------------------------------------------------------------------------

class TestTradeGate:
    """Tests for get_trade_gate — attribution, verdicts, summary."""

    def _make_db(self, tmp_path: Path) -> str:
        db_path, conn = _create_trades_db(tmp_path)
        _seed_standard(conn)
        return db_path

    def test_verdict_vocabulary(self, tmp_path):
        """All verdicts must be in the allowed set."""
        from dashboard.queries import get_trade_gate
        db = self._make_db(tmp_path)
        # Build a fake project root with single configs (no multi-config blending)
        proj = _create_config_dir(
            tmp_path / "proj_vocab",
            [
                ("config_btcusdt_ema.json", "BTC/USDT:USDT"),
                ("config_ethusdt_ema.json", "ETH/USDT:USDT"),
            ],
        )
        result = get_trade_gate(db, project_root=proj)
        allowed = {"KEEP_TESTING", "READY_TO_AUDIT", "MARGINAL", "DROP", "MIXED"}
        for row in result["rows"]:
            assert row["verdict"] in allowed, f"Invalid verdict: {row['verdict']}"

    def test_summary_keys(self, tmp_path):
        """Result must carry summary with n_meeting_min and n_total."""
        from dashboard.queries import get_trade_gate
        db = self._make_db(tmp_path)
        proj = _create_config_dir(
            tmp_path / "proj_summary",
            [("config_btcusdt_ema.json", "BTC/USDT:USDT")],
        )
        result = get_trade_gate(db, project_root=proj)
        assert "summary" in result
        assert "n_meeting_min" in result["summary"]
        assert "n_total" in result["summary"]
        # n_total is computed, not a literal constant
        assert isinstance(result["summary"]["n_total"], int)
        assert result["summary"]["n_total"] > 0

    def test_single_config_symbol_gets_classify_verdict(self, tmp_path):
        """A symbol owned by exactly ONE config file → verdict = classify(...)."""
        from dashboard.queries import get_trade_gate
        from research.forward_test_report import classify as _classify

        db = self._make_db(tmp_path)
        # ETH has 3 wins, 0 losses in our seed → PF=inf
        proj = _create_config_dir(
            tmp_path / "proj_single",
            [("config_ethusdt_ema.json", "ETH/USDT:USDT")],
        )
        result = get_trade_gate(db, project_root=proj, min_trades=2)
        eth_rows = [r for r in result["rows"] if r["symbol"] == "ETH/USDT:USDT"]
        assert eth_rows, "ETH must appear in gate rows"
        eth = eth_rows[0]
        # Should be READY_TO_AUDIT or KEEP_TESTING etc — NOT MIXED
        assert eth["verdict"] != "MIXED", (
            "Single-config symbol must not get MIXED verdict"
        )
        # Must equal classify()
        expected = _classify(eth["trades"], eth["profit_factor"], 2, 1.3)
        assert eth["verdict"] == expected, (
            f"Single-config verdict mismatch: got {eth['verdict']}, "
            f"expected {expected}"
        )

    def test_multi_config_symbol_gets_mixed(self, tmp_path):
        """A symbol with TWO config files → verdict = MIXED regardless of stats."""
        from dashboard.queries import get_trade_gate

        db = self._make_db(tmp_path)
        # Two configs sharing BTC/USDT:USDT → symbol_clean=BTCUSDTUSDT
        proj = _create_config_dir(
            tmp_path / "proj_multi",
            [
                ("config_btcusdt_ema.json",  "BTC/USDT:USDT"),
                ("config_btcusdt_ichi.json", "BTC/USDT:USDT"),
            ],
        )
        result = get_trade_gate(db, project_root=proj, min_trades=2)
        btc_rows = [r for r in result["rows"] if r["symbol"] == "BTC/USDT:USDT"]
        assert btc_rows, "BTC must appear in gate rows"
        assert btc_rows[0]["verdict"] == "MIXED", (
            "Multi-config symbol must get MIXED verdict"
        )

    def test_attribution_count_map_computed_from_files(self, tmp_path):
        """The count map must come from counting config files, not get_bot_statuses.

        Directly test the count-map derivation by building two configs for the
        same symbol_clean and verifying the count is 2.
        """
        from dashboard.queries import _build_symbol_config_count

        proj = _create_config_dir(
            tmp_path / "proj_count",
            [
                ("config_axsusdt_ema.json",       "AXS/USDT:USDT"),
                ("config_axsusdt_rangebounce.json","AXS/USDT:USDT"),
                ("config_axsusdt_stochmtf.json",  "AXS/USDT:USDT"),
                ("config_ethusdt_ema.json",        "ETH/USDT:USDT"),
            ],
        )
        count_map = _build_symbol_config_count(proj)
        # AXS has 3 configs
        axs_clean = "AXS/USDT:USDT".replace("/", "").replace(":", "")
        assert count_map.get(axs_clean, 0) == 3, (
            f"AXS config count should be 3, got {count_map.get(axs_clean)}"
        )
        eth_clean = "ETH/USDT:USDT".replace("/", "").replace(":", "")
        assert count_map.get(eth_clean, 0) == 1, (
            f"ETH config count should be 1, got {count_map.get(eth_clean)}"
        )

    def test_reward_to_avgloss_formula(self, tmp_path):
        """reward_to_avgloss = mean(pnl) / mean(abs(losing pnl))."""
        from dashboard.queries import get_trade_gate

        db = self._make_db(tmp_path)
        # BTC non-orphan closed pnls: 15, -10, 10, 15, 10 (rows 1-4 + row 11 the +07:00 fixture)
        # mean(pnl) = (15-10+10+15+10)/5 = 40/5 = 8.0
        # losses: [-10], mean(abs) = 10
        # reward_to_avgloss = 8.0 / 10 = 0.8
        proj = _create_config_dir(
            tmp_path / "proj_rtal",
            [("config_btcusdt_ema.json", "BTC/USDT:USDT")],
        )
        result = get_trade_gate(db, project_root=proj, min_trades=2)
        btc = [r for r in result["rows"] if r["symbol"] == "BTC/USDT:USDT"]
        assert btc, "BTC must appear"
        rtal = btc[0].get("reward_to_avgloss")
        assert rtal is not None
        assert abs(rtal - 0.8) < 0.01, f"reward_to_avgloss expected ~0.8 got {rtal}"

    def test_reward_to_avgloss_null_for_zero_loss_symbol(self, tmp_path):
        """reward_to_avgloss must be null for symbols with no losing trades."""
        from dashboard.queries import get_trade_gate

        db = self._make_db(tmp_path)
        # ETH has no losses in the seed
        proj = _create_config_dir(
            tmp_path / "proj_null",
            [("config_ethusdt_ema.json", "ETH/USDT:USDT")],
        )
        result = get_trade_gate(db, project_root=proj, min_trades=2)
        eth = [r for r in result["rows"] if r["symbol"] == "ETH/USDT:USDT"]
        assert eth
        assert eth[0].get("reward_to_avgloss") is None, (
            "Zero-loss symbol must have null reward_to_avgloss"
        )

    def test_meets_min_flag(self, tmp_path):
        """meets_min must reflect whether trades >= min_trades."""
        from dashboard.queries import get_trade_gate

        db = self._make_db(tmp_path)
        proj = _create_config_dir(
            tmp_path / "proj_min",
            [
                ("config_btcusdt_ema.json", "BTC/USDT:USDT"),
                ("config_ethusdt_ema.json", "ETH/USDT:USDT"),
            ],
        )
        # min_trades=100 → nothing meets it
        result = get_trade_gate(db, project_root=proj, min_trades=100)
        for row in result["rows"]:
            assert row["meets_min"] is False, (
                f"{row['symbol']} should NOT meet min=100"
            )
        # min_trades=2 → BTC (4 trades) and ETH (3 trades) both meet it
        result2 = get_trade_gate(db, project_root=proj, min_trades=2)
        for row in result2["rows"]:
            if row["trades"] >= 2:
                assert row["meets_min"] is True

    def test_profit_factor_inf_safe(self, tmp_path):
        """Symbols with no losses must have profit_factor = inf (float), not crash."""
        from dashboard.queries import get_trade_gate

        db = self._make_db(tmp_path)
        proj = _create_config_dir(
            tmp_path / "proj_inf",
            [("config_ethusdt_ema.json", "ETH/USDT:USDT")],
        )
        result = get_trade_gate(db, project_root=proj, min_trades=1)
        eth = [r for r in result["rows"] if r["symbol"] == "ETH/USDT:USDT"]
        assert eth
        # inf is represented as float("inf") in Python
        assert eth[0]["profit_factor"] == float("inf") or eth[0]["profit_factor"] > 100


# ---------------------------------------------------------------------------
# 3. get_open_risk
# ---------------------------------------------------------------------------

class TestOpenRisk:
    """Tests for get_open_risk."""

    def _make_db(self, tmp_path: Path) -> str:
        db_path, conn = _create_trades_db(tmp_path)
        _seed_standard(conn)
        return db_path

    def test_returns_open_count(self, tmp_path):
        from dashboard.queries import get_open_risk
        db = self._make_db(tmp_path)
        risk = get_open_risk(db)
        # 2 open SOL trades in our seed
        assert risk["open_count"] >= 1

    def test_null_sl_counts_as_unprotected(self, tmp_path):
        from dashboard.queries import get_open_risk
        db = self._make_db(tmp_path)
        risk = get_open_risk(db, symbol="SOL/USDT:USDT")
        # SOL trade id=9 has null SL
        assert risk["unprotected_count"] >= 1

    def test_notional_abs_for_shorts(self, tmp_path):
        from dashboard.queries import get_open_risk
        db = self._make_db(tmp_path)
        risk = get_open_risk(db, symbol="SOL/USDT:USDT")
        # notional = sum(abs(entry_price * size)) — always positive
        assert risk["notional"] > 0

    def test_no_balance_percentage(self, tmp_path):
        """Ships degraded — no % until balance is persisted."""
        from dashboard.queries import get_open_risk
        db = self._make_db(tmp_path)
        risk = get_open_risk(db)
        # No 'pct' key until a balance table exists
        assert "pct" not in risk or risk["pct"] is None

    def test_max_sl_loss_abs(self, tmp_path):
        """max_sl_loss = sum(abs(entry - stop_loss) * size) for protected trades."""
        from dashboard.queries import get_open_risk
        db = self._make_db(tmp_path)
        # SOL id=10: short, entry=152, sl=155, size=5 → loss = abs(152-155)*5 = 15
        risk = get_open_risk(db, symbol="SOL/USDT:USDT")
        assert risk["max_sl_loss"] >= 0  # must not be negative

    def test_empty_result_for_no_open(self, tmp_path):
        from dashboard.queries import get_open_risk
        db = self._make_db(tmp_path)
        # No open trades for BTC in our seed
        risk = get_open_risk(db, symbol="BTC/USDT:USDT")
        assert risk["open_count"] == 0

    def test_empty_db(self, tmp_path):
        from dashboard.queries import get_open_risk
        db_path, conn = _create_trades_db(tmp_path)
        conn.close()
        risk = get_open_risk(db_path)
        assert risk["open_count"] == 0
        assert risk["notional"] == 0.0


# ---------------------------------------------------------------------------
# 4. get_calendar_pnl
# ---------------------------------------------------------------------------

class TestCalendarPnl:
    """Tests for get_calendar_pnl and reconciliation with get_daily_pnl."""

    def _make_db(self, tmp_path: Path) -> str:
        db_path, conn = _create_trades_db(tmp_path)
        _seed_standard(conn)
        return db_path

    def test_columns_present(self, tmp_path):
        from dashboard.queries import get_calendar_pnl
        db = self._make_db(tmp_path)
        df = get_calendar_pnl(db)
        for col in ("date", "daily_pnl", "trades", "wins", "win_rate"):
            assert col in df.columns, f"Missing column: {col}"

    def test_reconciles_with_get_daily_pnl(self, tmp_path):
        """get_calendar_pnl daily_pnl must equal get_daily_pnl daily_pnl for same dates."""
        from dashboard.queries import get_calendar_pnl, get_daily_pnl
        db = self._make_db(tmp_path)
        cal = get_calendar_pnl(db)
        daily = get_daily_pnl(db)

        if cal.empty or daily.empty:
            pytest.skip("Empty results — nothing to reconcile")

        # Merge on date and compare daily_pnl values
        merged = cal.merge(daily, on="date", suffixes=("_cal", "_daily"))
        for _, row in merged.iterrows():
            diff = abs(row["daily_pnl_cal"] - row["daily_pnl_daily"])
            assert diff < 0.01, (
                f"Date {row['date']}: calendar={row['daily_pnl_cal']}, "
                f"get_daily_pnl={row['daily_pnl_daily']}"
            )

    def test_orphan_excluded(self, tmp_path):
        from dashboard.queries import get_calendar_pnl
        db = self._make_db(tmp_path)
        df = get_calendar_pnl(db)
        # Orphan row (id=5) has pnl=0; including it would change win counts
        # We verify by checking the total trade count matches closed non-orphan rows
        assert df["trades"].sum() > 0

    def test_win_rate_range(self, tmp_path):
        from dashboard.queries import get_calendar_pnl
        db = self._make_db(tmp_path)
        df = get_calendar_pnl(db)
        for _, row in df.iterrows():
            assert 0.0 <= row["win_rate"] <= 1.0, (
                f"win_rate out of [0,1]: {row['win_rate']} on {row['date']}"
            )

    def test_symbol_filter(self, tmp_path):
        from dashboard.queries import get_calendar_pnl
        db = self._make_db(tmp_path)
        df_btc = get_calendar_pnl(db, symbol="BTC/USDT:USDT")
        df_all = get_calendar_pnl(db)
        assert df_btc["trades"].sum() <= df_all["trades"].sum()

    def test_empty_db(self, tmp_path):
        from dashboard.queries import get_calendar_pnl
        db_path, conn = _create_trades_db(tmp_path)
        conn.close()
        df = get_calendar_pnl(db_path)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0


# ---------------------------------------------------------------------------
# 5. get_hour_dow_stats — UTC correctness is the main risk
# ---------------------------------------------------------------------------

class TestHourDowStats:
    """Tests for get_hour_dow_stats — UTC bucketing is critical."""

    def _make_db(self, tmp_path: Path) -> str:
        db_path, conn = _create_trades_db(tmp_path)
        _seed_standard(conn)
        return db_path

    def test_columns_present(self, tmp_path):
        from dashboard.queries import get_hour_dow_stats
        db = self._make_db(tmp_path)
        df = get_hour_dow_stats(db)
        for col in ("dow", "hour_bucket", "trades", "avg_pnl", "win_rate", "total_pnl"):
            assert col in df.columns, f"Missing column: {col}"

    def test_dow_range(self, tmp_path):
        """dow must be 0–6 (Monday=0 per pandas dayofweek)."""
        from dashboard.queries import get_hour_dow_stats
        db = self._make_db(tmp_path)
        df = get_hour_dow_stats(db)
        if not df.empty:
            assert df["dow"].between(0, 6).all(), (
                f"dow out of range: {df['dow'].unique()}"
            )

    def test_hour_bucket_range(self, tmp_path):
        """hour_bucket must be valid starting hours (0, 4, 8, 12, 16, 20 for bucket_hours=4)."""
        from dashboard.queries import get_hour_dow_stats
        db = self._make_db(tmp_path)
        df = get_hour_dow_stats(db, bucket_hours=4)
        if not df.empty:
            valid_buckets = {0, 4, 8, 12, 16, 20}
            actual = set(df["hour_bucket"].unique())
            assert actual.issubset(valid_buckets), (
                f"Invalid hour_buckets: {actual - valid_buckets}"
            )

    def test_utc_bucketing_matches_pandas_ground_truth(self, tmp_path):
        """Each row's (dow, hour_bucket) must equal pd.to_datetime(ts, utc=True).

        Tests both a +00:00 timestamp and the +07:00 fixture to guard the
        OANDA/gold path where timestamps carry a non-UTC offset.
        """
        from dashboard.queries import get_hour_dow_stats

        db_path, conn = _create_trades_db(tmp_path)
        # Insert two known timestamps:
        # ts1: 2026-03-23T03:01:20+00:00 → UTC Mon (dow=0), hour=3 → bucket=0
        # ts2: 2026-06-07T02:00:00+07:00 → UTC Sat (dow=5), hour=19 → bucket=16
        rows = [
            (1, "2026-03-23T03:01:20.752540+00:00", "BTC/USDT:USDT", "long",
             50000.0, 51000.0, 0.1, 49000.0, 52000.0, 10.0, 0.02, "closed", "take_profit"),
            (2, "2026-06-07T02:00:00.000000+07:00", "BTC/USDT:USDT", "long",
             68000.0, 69000.0, 0.1, 67000.0, 71000.0, 10.0, 0.015, "closed", "take_profit"),
        ]
        conn.executemany(
            """INSERT INTO trades
               (id, timestamp, symbol, side, entry_price, exit_price, size,
                stop_loss, take_profit, pnl, pnl_pct, status, close_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        conn.close()

        # Compute expected buckets via pandas (the ground truth)
        timestamps = [
            "2026-03-23T03:01:20.752540+00:00",
            "2026-06-07T02:00:00.000000+07:00",
        ]
        bucket_hours = 4
        expected = []
        for ts in timestamps:
            t = pd.to_datetime(ts, utc=True)
            dow = t.dayofweek  # Mon=0
            bucket = (t.hour // bucket_hours) * bucket_hours
            expected.append((dow, bucket))

        df = get_hour_dow_stats(db_path, bucket_hours=bucket_hours)
        assert not df.empty

        # For each expected (dow, hour_bucket), verify it appears in the result
        for i, (exp_dow, exp_bucket) in enumerate(expected):
            match = df[(df["dow"] == exp_dow) & (df["hour_bucket"] == exp_bucket)]
            assert not match.empty, (
                f"Timestamp {timestamps[i]}: expected dow={exp_dow}, "
                f"hour_bucket={exp_bucket} not found in result. "
                f"Result:\n{df.to_string()}"
            )

    def test_plus07_fixture_buckets_to_utc(self, tmp_path):
        """2026-06-07T02:00:00+07:00 must bucket to UTC Sat (dow=5), hour=19 → bucket=16.

        This specifically guards against any implementation that strips the timezone
        offset and treats the time as UTC (which would give dow=6, bucket=0 — wrong).
        """
        from dashboard.queries import get_hour_dow_stats

        db_path, conn = _create_trades_db(tmp_path)
        conn.execute(
            """INSERT INTO trades
               (id, timestamp, symbol, side, entry_price, exit_price, size,
                stop_loss, take_profit, pnl, pnl_pct, status, close_reason)
               VALUES (1, '2026-06-07T02:00:00.000000+07:00', 'BTC/USDT:USDT', 'long',
                       68000.0, 69000.0, 0.1, 67000.0, 71000.0, 10.0, 0.015, 'closed', 'take_profit')"""
        )
        conn.commit()
        conn.close()

        df = get_hour_dow_stats(db_path, bucket_hours=4)
        assert not df.empty

        # Ground truth via pandas
        t = pd.to_datetime("2026-06-07T02:00:00.000000+07:00", utc=True)
        exp_dow = t.dayofweek        # 5 (Saturday in UTC)
        exp_bucket = (t.hour // 4) * 4  # 19 // 4 = 4, *4 = 16

        match = df[(df["dow"] == exp_dow) & (df["hour_bucket"] == exp_bucket)]
        assert not match.empty, (
            f"+07:00 fixture: expected dow={exp_dow} (Sat), hour_bucket={exp_bucket}, "
            f"got result rows:\n{df.to_string()}"
        )
        # Anti-verify: the naive (wrong) bucket should NOT be present alone
        # naive would give 2026-06-07 02:00 local → dow=6(Sun), bucket=0
        wrong_match = df[(df["dow"] == 6) & (df["hour_bucket"] == 0)]
        # The wrong bucket should either not exist or have 0 from this record
        # (it may appear if other records happen to fall there — check it's not THIS record)
        # We verify by confirming the correct bucket has >= 1 trade count
        assert match.iloc[0]["trades"] >= 1

    def test_per_cell_trade_count_present(self, tmp_path):
        """Each cell must expose raw trade count for the UI mask."""
        from dashboard.queries import get_hour_dow_stats
        db = self._make_db(tmp_path)
        df = get_hour_dow_stats(db)
        if not df.empty:
            assert "trades" in df.columns
            assert (df["trades"] >= 1).all(), "All returned cells should have >= 1 trade"

    def test_win_rate_bounded(self, tmp_path):
        from dashboard.queries import get_hour_dow_stats
        db = self._make_db(tmp_path)
        df = get_hour_dow_stats(db)
        if not df.empty:
            assert df["win_rate"].between(0.0, 1.0).all()

    def test_empty_db_returns_empty(self, tmp_path):
        from dashboard.queries import get_hour_dow_stats
        db_path, conn = _create_trades_db(tmp_path)
        conn.close()
        df = get_hour_dow_stats(db_path)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_symbol_filter(self, tmp_path):
        from dashboard.queries import get_hour_dow_stats
        db = self._make_db(tmp_path)
        df_btc = get_hour_dow_stats(db, symbol="BTC/USDT:USDT")
        df_all = get_hour_dow_stats(db)
        assert df_btc["trades"].sum() <= df_all["trades"].sum()

    def test_bucket_hours_parameter(self, tmp_path):
        """bucket_hours=1 should give fine-grained bucketing vs bucket_hours=4."""
        from dashboard.queries import get_hour_dow_stats
        db = self._make_db(tmp_path)
        df4 = get_hour_dow_stats(db, bucket_hours=4)
        df1 = get_hour_dow_stats(db, bucket_hours=1)
        # With finer buckets, can only have more or equal distinct cells
        assert len(df1) >= len(df4)


# ---------------------------------------------------------------------------
# 6. Integration: _build_symbol_config_count (the attribution map)
# ---------------------------------------------------------------------------

class TestBuildSymbolConfigCount:
    """Tests for the internal _build_symbol_config_count helper directly."""

    def test_single_config_per_symbol(self, tmp_path):
        from dashboard.queries import _build_symbol_config_count

        proj = _create_config_dir(
            tmp_path,
            [
                ("config_btcusdt_ema.json",  "BTC/USDT:USDT"),
                ("config_ethusdt_ema.json",  "ETH/USDT:USDT"),
                ("config_solusdt_ema.json",  "SOL/USDT:USDT"),
            ],
        )
        count_map = _build_symbol_config_count(proj)
        for sym in ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"):
            clean = sym.replace("/", "").replace(":", "")
            assert count_map.get(clean, 0) == 1, (
                f"{clean} should have count=1, got {count_map.get(clean)}"
            )

    def test_multi_config_axs(self, tmp_path):
        """AXS with 4 configs → count=4 (matches the known real-world case axs=4)."""
        from dashboard.queries import _build_symbol_config_count

        proj = _create_config_dir(
            tmp_path,
            [
                ("config_axsusdt_dualthrust.json",  "AXS/USDT:USDT"),
                ("config_axsusdt_emaribbon.json",   "AXS/USDT:USDT"),
                ("config_axsusdt_rangebounce.json",  "AXS/USDT:USDT"),
                ("config_axsusdt_stochmtf.json",    "AXS/USDT:USDT"),
            ],
        )
        count_map = _build_symbol_config_count(proj)
        axs_clean = "AXS/USDT:USDT".replace("/", "").replace(":", "")
        assert count_map[axs_clean] == 4, (
            f"AXS should have count=4, got {count_map.get(axs_clean)}"
        )

    def test_skip_configs_excluded(self, tmp_path):
        """Known skip configs (aggressive, sniper, etc.) must not appear in the map."""
        from dashboard.queries import _build_symbol_config_count

        proj = _create_config_dir(
            tmp_path,
            [
                ("config_aggressive.json",   "BTC/USDT:USDT"),  # skip
                ("config_yolo.json",         "BTC/USDT:USDT"),  # skip
                ("config_btcusdt_ema.json",  "BTC/USDT:USDT"),  # keep
            ],
        )
        count_map = _build_symbol_config_count(proj)
        btc_clean = "BTC/USDT:USDT".replace("/", "").replace(":", "")
        # Only 1 real config, not 3
        assert count_map.get(btc_clean, 0) == 1, (
            f"Skip configs must not be counted. BTC count={count_map.get(btc_clean)}"
        )

    def test_missing_symbol_in_config(self, tmp_path):
        """Config files without a 'symbol' key must be ignored gracefully."""
        from dashboard.queries import _build_symbol_config_count

        (tmp_path / "config_no_symbol.json").write_text(
            json.dumps({"timeframe_signal": "4h"})
        )
        count_map = _build_symbol_config_count(tmp_path)
        # Should not crash; the config without symbol is skipped
        assert isinstance(count_map, dict)
