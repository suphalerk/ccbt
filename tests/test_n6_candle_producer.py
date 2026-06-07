"""N6 candle producer — TDD tests (failing first, then fixed by implementation).

Tests cover:
1. `upsert_candles()` writes correct schema rows into bot_ohlcv.
2. Column mapping: ema_fast→ema9, ema_slow→ema21, rsi→rsi14.
3. Bounded rows: after upsert with MAX_OHLCV_ROWS rows old ones are pruned.
4. Idempotency: upsert on same (symbol, timeframe, ts) doesn't duplicate rows.
5. NO ccxt/network import in the producer path.
6. Integration smoke: after upsert, /api/candles returns available=True with correct data.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# ---------------------------------------------------------------------------
# Sample dataframe factory — mimics what add_indicators() returns
# ---------------------------------------------------------------------------

def _make_indicator_df(n: int = 10, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """Build a minimal indicator-laden DataFrame like add_indicators() returns."""
    import numpy as np

    base_ts = 1_704_067_200_000  # 2024-01-01 00:00 UTC in ms
    tf_ms = 15 * 60 * 1000  # 15m

    closes = 40000.0 + np.arange(n, dtype=float) * 100
    df = pd.DataFrame({
        "timestamp": [base_ts + i * tf_ms for i in range(n)],
        "open":  closes - 50,
        "high":  closes + 100,
        "low":   closes - 100,
        "close": closes,
        "volume": [1000.0 + i * 10 for i in range(n)],
        # Indicator columns produced by add_indicators()
        "ema_fast": closes - 20,
        "ema_slow": closes - 40,
        "rsi":      [55.0 + i * 0.5 for i in range(n)],
        "atr":      [150.0] * n,
        "volume_ma":[900.0] * n,
    })
    return df


# ---------------------------------------------------------------------------
# Import the producer — this will fail before the code is written
# ---------------------------------------------------------------------------

def _import_producer():
    from bot.logger import upsert_candles
    return upsert_candles


# ---------------------------------------------------------------------------
# Test: producer is importable and callable
# ---------------------------------------------------------------------------

class TestProducerExists:
    def test_upsert_candles_importable(self):
        """bot.logger.upsert_candles must exist."""
        fn = _import_producer()
        assert callable(fn)

    def test_no_ccxt_in_logger(self):
        """bot/logger.py must NOT import ccxt (producer lives here)."""
        logger_path = REPO / "bot" / "logger.py"
        source = logger_path.read_text()
        assert "import ccxt" not in source, "bot/logger.py must not import ccxt"
        assert "ccxt." not in source, "bot/logger.py must not use ccxt"


# ---------------------------------------------------------------------------
# Test: schema and column mapping
# ---------------------------------------------------------------------------

class TestUpsertSchema:
    def test_creates_bot_ohlcv_table(self, tmp_path):
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")
        df = _make_indicator_df(5)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_ohlcv'"
        )
        assert cur.fetchone() is not None, "bot_ohlcv table must be created"
        conn.close()

    def test_rows_written(self, tmp_path):
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")
        df = _make_indicator_df(5)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM bot_ohlcv").fetchone()[0]
        conn.close()
        assert count == 5

    def test_column_mapping_ema9_ema21_rsi14(self, tmp_path):
        """ema_fast→ema9, ema_slow→ema21, rsi→rsi14."""
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")
        df = _make_indicator_df(3)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ema9, ema21, rsi14 FROM bot_ohlcv ORDER BY ts"
        ).fetchall()
        conn.close()

        assert len(rows) == 3
        for i, row in enumerate(rows):
            # Check ema9 = ema_fast value from df
            expected_ema9 = float(df["ema_fast"].iloc[i])
            expected_ema21 = float(df["ema_slow"].iloc[i])
            expected_rsi14 = float(df["rsi"].iloc[i])
            assert abs(row["ema9"] - expected_ema9) < 1e-6, f"ema9 mismatch at row {i}"
            assert abs(row["ema21"] - expected_ema21) < 1e-6, f"ema21 mismatch at row {i}"
            assert abs(row["rsi14"] - expected_rsi14) < 1e-6, f"rsi14 mismatch at row {i}"

    def test_ohlcv_columns_present(self, tmp_path):
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")
        df = _make_indicator_df(2)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM bot_ohlcv LIMIT 1").fetchone()
        conn.close()

        keys = set(row.keys())
        required = {"symbol", "timeframe", "ts", "open", "high", "low", "close", "volume", "ema9", "ema21", "rsi14"}
        assert required.issubset(keys), f"Missing columns: {required - keys}"

    def test_ts_is_epoch_ms(self, tmp_path):
        """ts column must be epoch milliseconds (large int, not seconds)."""
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")
        df = _make_indicator_df(1)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        conn = sqlite3.connect(db_path)
        ts_val = conn.execute("SELECT ts FROM bot_ohlcv").fetchone()[0]
        conn.close()
        # epoch ms for 2024 is > 1e12; epoch seconds is ~1.7e9
        assert ts_val > 1_000_000_000_000, f"ts should be epoch ms, got {ts_val}"


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------

class TestUpsertIdempotency:
    def test_double_upsert_no_duplicate(self, tmp_path):
        """Upserting same data twice must NOT create duplicate rows."""
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")
        df = _make_indicator_df(5)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM bot_ohlcv").fetchone()[0]
        conn.close()
        assert count == 5, f"Expected 5 rows, got {count} — possible duplicate on upsert"

    def test_upsert_overwrites_on_same_ts(self, tmp_path):
        """Upserting with updated indicator values for same ts must update row."""
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")
        df = _make_indicator_df(1)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        # Modify ema_fast and upsert again
        df2 = df.copy()
        df2["ema_fast"] = 99999.0
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df2)

        conn = sqlite3.connect(db_path)
        ema9 = conn.execute("SELECT ema9 FROM bot_ohlcv").fetchone()[0]
        conn.close()
        assert abs(ema9 - 99999.0) < 1e-6, "ema9 must be updated on upsert"


# ---------------------------------------------------------------------------
# Test: bounded rows (pruning)
# ---------------------------------------------------------------------------

class TestUpsertBounded:
    def test_prunes_old_rows_beyond_max(self, tmp_path):
        """After upsert, at most MAX_OHLCV_ROWS rows remain per (symbol, timeframe)."""
        from bot.logger import MAX_OHLCV_ROWS
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")

        # Insert MAX + 50 rows
        n = MAX_OHLCV_ROWS + 50
        df = _make_indicator_df(n)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM bot_ohlcv WHERE symbol='BTCUSDT' AND timeframe='15m'"
        ).fetchone()[0]
        conn.close()
        assert count <= MAX_OHLCV_ROWS, f"Expected at most {MAX_OHLCV_ROWS} rows, got {count}"

    def test_prunes_oldest_rows(self, tmp_path):
        """After pruning, the retained rows are the most-recent ones."""
        from bot.logger import MAX_OHLCV_ROWS
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")

        n = MAX_OHLCV_ROWS + 10
        df = _make_indicator_df(n)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        conn = sqlite3.connect(db_path)
        # The oldest ts present should be at index (n - MAX_OHLCV_ROWS), not index 0
        min_ts = conn.execute("SELECT MIN(ts) FROM bot_ohlcv").fetchone()[0]
        conn.close()

        # ts[0] is the oldest; ts[n - MAX_OHLCV_ROWS] is the first kept
        first_kept_ts = int(df["timestamp"].iloc[n - MAX_OHLCV_ROWS])
        assert min_ts >= first_kept_ts, (
            f"Oldest row ts={min_ts} should be >= first_kept ts={first_kept_ts}"
        )

    def test_separate_symbols_bounded_independently(self, tmp_path):
        """Pruning is per (symbol, timeframe) — BTC rows don't prune ETH rows."""
        from bot.logger import MAX_OHLCV_ROWS
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")

        n = MAX_OHLCV_ROWS + 10
        df_btc = _make_indicator_df(n)
        df_eth = _make_indicator_df(5)  # only 5 rows for ETH

        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df_btc)
        upsert_candles(db_path=db_path, symbol="ETHUSDT", timeframe="15m", df=df_eth)

        conn = sqlite3.connect(db_path)
        btc_count = conn.execute(
            "SELECT COUNT(*) FROM bot_ohlcv WHERE symbol='BTCUSDT'"
        ).fetchone()[0]
        eth_count = conn.execute(
            "SELECT COUNT(*) FROM bot_ohlcv WHERE symbol='ETHUSDT'"
        ).fetchone()[0]
        conn.close()

        assert btc_count <= MAX_OHLCV_ROWS
        assert eth_count == 5, f"ETH should have 5 rows, got {eth_count}"


# ---------------------------------------------------------------------------
# Integration: after upsert, /api/candles returns available=True
# ---------------------------------------------------------------------------

class TestIntegrationWithApi:
    def test_api_candles_available_true_after_upsert(self, tmp_path):
        """After upsert, GET /api/candles returns available=True with correct data."""
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")
        df = _make_indicator_df(5)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        import api.deps as deps_module
        from fastapi.testclient import TestClient
        from api.main import app

        app.dependency_overrides[deps_module.get_db_path] = lambda: db_path
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/api/candles?symbol=BTCUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["available"] is True
            assert len(data["candles"]) == 5
            # Verify indicator fields are present in first candle
            c0 = data["candles"][0]
            assert c0["ema9"] is not None
            assert c0["ema21"] is not None
            assert c0["rsi14"] is not None
        finally:
            app.dependency_overrides.clear()

    def test_api_mime_type_json(self, tmp_path):
        """Response Content-Type must be application/json."""
        upsert_candles = _import_producer()
        db_path = str(tmp_path / "trades.db")
        df = _make_indicator_df(3)
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        import api.deps as deps_module
        from fastapi.testclient import TestClient
        from api.main import app

        app.dependency_overrides[deps_module.get_db_path] = lambda: db_path
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/api/candles?symbol=BTCUSDT")
            ct = resp.headers.get("content-type", "")
            assert "application/json" in ct, f"Expected JSON content-type, got {ct}"
        finally:
            app.dependency_overrides.clear()
