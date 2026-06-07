"""N1-prereq TDD — orphan exclusion in get_per_bot_summary + read-only _get_connection.

Tests written FIRST (before the fix) to fail red, then fixed by editing queries.py.

Invariants:
1. orphan rows (close_reason='orphan_reconcile') must be excluded from get_per_bot_summary
2. per-symbol total_pnl from get_per_bot_summary must equal sum of get_closed_trades pnl
3. _get_connection must open in read-only mode (mode=ro) + PRAGMA query_only=1
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_db(tmp_path: Path) -> str:
    """Create a minimal trades DB with a mix of normal and orphan rows."""
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
            ai_decision TEXT,
            ai_confidence REAL,
            ai_reasoning TEXT,
            ai_override INTEGER
        )
        """
    )
    rows = [
        # BTC: 3 real closed trades (win 10, win 5, loss -3 = +12 net) + 1 orphan (pnl=0)
        (1, "BTC/USDT:USDT", "long", "closed", None,       10.0,  1.0, "2026-01-01T01:00:00"),
        (2, "BTC/USDT:USDT", "long", "closed", None,        5.0,  0.5, "2026-01-02T01:00:00"),
        (3, "BTC/USDT:USDT", "short","closed", None,       -3.0, -0.3, "2026-01-03T01:00:00"),
        (4, "BTC/USDT:USDT", "long", "closed", "orphan_reconcile", 0.0, 0.0, "2026-01-04T01:00:00"),
        # ETH: 2 real closed trades (win 8, loss -2 = +6 net) + 1 orphan
        (5, "ETH/USDT:USDT", "long", "closed", None,        8.0,  0.8, "2026-01-05T01:00:00"),
        (6, "ETH/USDT:USDT", "short","closed", None,       -2.0, -0.2, "2026-01-06T01:00:00"),
        (7, "ETH/USDT:USDT", "long", "closed", "orphan_reconcile", 0.0, 0.0, "2026-01-07T01:00:00"),
        # SOL: 1 open trade (should never appear in closed summaries)
        (8, "SOL/USDT:USDT", "long", "open",   None,        0.0,  0.0, "2026-01-08T01:00:00"),
    ]
    conn.executemany(
        "INSERT INTO trades (id,symbol,side,status,close_reason,pnl,pnl_pct,timestamp) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# 1. Orphan exclusion in get_per_bot_summary
# ---------------------------------------------------------------------------

class TestOrphanExclusion:
    """get_per_bot_summary must exclude orphan_reconcile rows, matching get_closed_trades."""

    def test_orphan_rows_excluded_from_trade_count(self, tmp_path):
        """Orphan rows must NOT count in the trades column."""
        from dashboard.queries import get_per_bot_summary
        db = _create_test_db(tmp_path)
        df = get_per_bot_summary(db)

        btc = df[df["symbol"] == "BTC/USDT:USDT"]
        assert not btc.empty, "BTC must appear in summary"
        assert btc.iloc[0]["trades"] == 3, (
            f"BTC should have 3 real trades; orphan must be excluded. Got {btc.iloc[0]['trades']}"
        )

    def test_orphan_rows_excluded_from_pnl(self, tmp_path):
        """Orphan rows (pnl=0) must not inflate or deflate total_pnl."""
        from dashboard.queries import get_per_bot_summary
        db = _create_test_db(tmp_path)
        df = get_per_bot_summary(db)

        eth = df[df["symbol"] == "ETH/USDT:USDT"]
        assert not eth.empty, "ETH must appear in summary"
        # 8 + (-2) = 6.0; orphan pnl=0 shouldn't change this
        assert abs(eth.iloc[0]["total_pnl"] - 6.0) < 0.01, (
            f"ETH total_pnl should be 6.0; got {eth.iloc[0]['total_pnl']}"
        )

    def test_orphan_rows_excluded_from_losses_count(self, tmp_path):
        """Orphan pnl=0 must NOT count as a loss (pnl <= 0 condition)."""
        from dashboard.queries import get_per_bot_summary
        db = _create_test_db(tmp_path)
        df = get_per_bot_summary(db)

        btc = df[df["symbol"] == "BTC/USDT:USDT"]
        assert not btc.empty
        # BTC real: 2 wins, 1 loss — orphan must not count as an extra loss
        assert btc.iloc[0]["losses"] == 1, (
            f"BTC losses should be 1 (1 real loss); got {btc.iloc[0]['losses']}. "
            "Orphan row (pnl=0) must be excluded."
        )

    def test_orphan_symbol_not_appearing_alone(self, tmp_path):
        """A symbol with ONLY orphan rows must NOT appear in the summary at all."""
        from dashboard.queries import get_per_bot_summary
        db_path = str(tmp_path / "orphan_only.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, status TEXT,
                close_reason TEXT, pnl REAL, pnl_pct REAL, timestamp TEXT,
                entry_price REAL, exit_price REAL,
                ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT, ai_override INTEGER
            )"""
        )
        conn.execute(
            "INSERT INTO trades (id,symbol,side,status,close_reason,pnl,pnl_pct,timestamp) "
            "VALUES (1,'XRP/USDT:USDT','long','closed','orphan_reconcile',0.0,0.0,'2026-01-01T00:00:00')"
        )
        conn.commit()
        conn.close()
        df = get_per_bot_summary(db_path)
        assert "XRP/USDT:USDT" not in (df["symbol"].tolist() if not df.empty else []), (
            "Symbol with only orphan rows must not appear in summary"
        )

    def test_open_trades_not_in_summary(self, tmp_path):
        """Open trades (status='open') must never appear in the per-bot summary."""
        from dashboard.queries import get_per_bot_summary
        db = _create_test_db(tmp_path)
        df = get_per_bot_summary(db)
        assert "SOL/USDT:USDT" not in (df["symbol"].tolist() if not df.empty else []), (
            "SOL only has open trades; must not appear in per-bot summary"
        )


# ---------------------------------------------------------------------------
# 2. Cross-function invariant: per-bot total_pnl == sum(get_closed_trades pnl)
# ---------------------------------------------------------------------------

class TestCrossFunctionInvariant:
    """get_per_bot_summary.total_pnl must equal sum of get_closed_trades(symbol).pnl.

    This is the key reconciliation test that proved the orphan leak was real.
    """

    def test_btc_total_pnl_matches_closed_trades(self, tmp_path):
        """BTC total_pnl in summary must equal sum of pnl in get_closed_trades for BTC."""
        from dashboard.queries import get_per_bot_summary, get_closed_trades
        db = _create_test_db(tmp_path)

        summary = get_per_bot_summary(db)
        btc_summary = summary[summary["symbol"] == "BTC/USDT:USDT"]
        assert not btc_summary.empty

        closed = get_closed_trades(db, symbol="BTC/USDT:USDT")
        expected_pnl = float(closed["pnl"].sum())
        actual_pnl = float(btc_summary.iloc[0]["total_pnl"])

        assert abs(actual_pnl - expected_pnl) < 0.001, (
            f"BTC total_pnl mismatch: summary={actual_pnl}, "
            f"get_closed_trades sum={expected_pnl}. "
            "Orphan leak detected: get_per_bot_summary is including rows that "
            "get_closed_trades excludes."
        )

    def test_eth_total_pnl_matches_closed_trades(self, tmp_path):
        """ETH total_pnl in summary must equal sum of pnl in get_closed_trades for ETH."""
        from dashboard.queries import get_per_bot_summary, get_closed_trades
        db = _create_test_db(tmp_path)

        summary = get_per_bot_summary(db)
        eth_summary = summary[summary["symbol"] == "ETH/USDT:USDT"]
        assert not eth_summary.empty

        closed = get_closed_trades(db, symbol="ETH/USDT:USDT")
        expected_pnl = float(closed["pnl"].sum())
        actual_pnl = float(eth_summary.iloc[0]["total_pnl"])

        assert abs(actual_pnl - expected_pnl) < 0.001, (
            f"ETH total_pnl mismatch: summary={actual_pnl}, "
            f"get_closed_trades sum={expected_pnl}"
        )

    def test_all_symbols_pnl_invariant(self, tmp_path):
        """For every symbol in summary, total_pnl must equal get_closed_trades pnl sum."""
        from dashboard.queries import get_per_bot_summary, get_closed_trades
        db = _create_test_db(tmp_path)

        summary = get_per_bot_summary(db)
        if summary.empty:
            pytest.skip("No summary rows to check")

        failures = []
        for _, row in summary.iterrows():
            symbol = row["symbol"]
            closed = get_closed_trades(db, symbol=symbol)
            if closed.empty:
                # If no closed trades for this symbol, summary row must not exist
                failures.append(f"{symbol}: in summary but get_closed_trades empty")
                continue
            expected = float(closed["pnl"].sum())
            actual = float(row["total_pnl"])
            if abs(actual - expected) > 0.001:
                failures.append(
                    f"{symbol}: summary total_pnl={actual}, "
                    f"get_closed_trades sum={expected}"
                )

        assert not failures, (
            "Cross-function invariant violated for:\n" + "\n".join(failures)
        )

    def test_trade_count_invariant(self, tmp_path):
        """For every symbol in summary, trades count must equal len(get_closed_trades)."""
        from dashboard.queries import get_per_bot_summary, get_closed_trades
        db = _create_test_db(tmp_path)

        summary = get_per_bot_summary(db)
        if summary.empty:
            pytest.skip("No summary rows to check")

        failures = []
        for _, row in summary.iterrows():
            symbol = row["symbol"]
            closed = get_closed_trades(db, symbol=symbol)
            expected = len(closed)
            actual = int(row["trades"])
            if actual != expected:
                failures.append(
                    f"{symbol}: summary trades={actual}, "
                    f"get_closed_trades count={expected}"
                )

        assert not failures, (
            "Trade count invariant violated for:\n" + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# 3. _get_connection must be read-only (mode=ro, query_only=1)
# ---------------------------------------------------------------------------

class TestReadOnlyConnection:
    """_get_connection must open the DB in read-only mode.

    This protects the live bot: a dashboard bug can never corrupt trades.db.
    """

    def test_connection_rejects_writes(self, tmp_path):
        """A connection opened via _get_connection must reject INSERT/UPDATE."""
        from dashboard.queries import _get_connection

        # Create a real WAL db first (so mode=ro doesn't fail on missing file)
        db_path = str(tmp_path / "ro_test.db")
        setup = sqlite3.connect(db_path)
        setup.execute("PRAGMA journal_mode=WAL")
        setup.execute("CREATE TABLE t (x INT)")
        setup.execute("INSERT INTO t VALUES (1)")
        setup.commit()
        setup.close()

        conn = _get_connection(db_path)
        try:
            with pytest.raises(Exception) as exc_info:
                conn.execute("INSERT INTO t VALUES (99)")
                conn.commit()
            # SQLite raises OperationalError or DatabaseError for read-only attempts
            err = str(exc_info.value).lower()
            assert any(kw in err for kw in ("read-only", "readonly", "read only", "not allowed")), (
                f"Expected a read-only error but got: {exc_info.value}"
            )
        finally:
            conn.close()

    def test_connection_rejects_ddl(self, tmp_path):
        """A read-only connection must reject CREATE TABLE (DDL)."""
        from dashboard.queries import _get_connection

        db_path = str(tmp_path / "ro_ddl.db")
        setup = sqlite3.connect(db_path)
        setup.execute("PRAGMA journal_mode=WAL")
        setup.execute("CREATE TABLE existing (x INT)")
        setup.commit()
        setup.close()

        conn = _get_connection(db_path)
        try:
            with pytest.raises(Exception):
                conn.execute("CREATE TABLE new_table (y INT)")
                conn.commit()
        finally:
            conn.close()

    def test_connection_allows_reads(self, tmp_path):
        """A read-only connection must still allow SELECT queries."""
        from dashboard.queries import _get_connection

        db_path = str(tmp_path / "ro_read.db")
        setup = sqlite3.connect(db_path)
        setup.execute("PRAGMA journal_mode=WAL")
        setup.execute("CREATE TABLE t (x INT)")
        setup.execute("INSERT INTO t VALUES (42)")
        setup.commit()
        setup.close()

        conn = _get_connection(db_path)
        try:
            row = conn.execute("SELECT x FROM t").fetchone()
            assert row is not None
            assert row[0] == 42
        finally:
            conn.close()

    def test_connection_query_only_pragma(self, tmp_path):
        """PRAGMA query_only must return 1 on the connection."""
        from dashboard.queries import _get_connection

        db_path = str(tmp_path / "ro_pragma.db")
        setup = sqlite3.connect(db_path)
        setup.execute("PRAGMA journal_mode=WAL")
        setup.execute("CREATE TABLE t (x INT)")
        setup.commit()
        setup.close()

        conn = _get_connection(db_path)
        try:
            row = conn.execute("PRAGMA query_only").fetchone()
            assert row is not None
            assert row[0] == 1, (
                f"PRAGMA query_only should be 1 (read-only enforced), got {row[0]}"
            )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 4. Empty DB / edge cases
# ---------------------------------------------------------------------------

class TestEmptyAndEdgeCases:
    """get_per_bot_summary must gracefully handle empty or missing DB."""

    def test_empty_db_returns_empty_dataframe(self, tmp_path):
        """Empty DB → get_per_bot_summary returns empty DataFrame, not an exception."""
        from dashboard.queries import get_per_bot_summary
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, status TEXT,
                close_reason TEXT, pnl REAL, pnl_pct REAL, timestamp TEXT,
                entry_price REAL, exit_price REAL,
                ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT, ai_override INTEGER
            )"""
        )
        conn.commit()
        conn.close()

        df = get_per_bot_summary(db_path)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_nonexistent_db_returns_empty_dataframe(self, tmp_path):
        """Missing DB → get_per_bot_summary returns empty DataFrame."""
        from dashboard.queries import get_per_bot_summary
        df = get_per_bot_summary(str(tmp_path / "nonexistent.db"))
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0
