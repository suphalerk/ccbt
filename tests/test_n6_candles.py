"""N6 TDD — /api/candles endpoint tests.

Tests are written FIRST (before the real implementation).
Key requirements from the ticket:
1. Returns {available: false} when no bot_ohlcv table (or empty).
2. Returns {available: true, candles: [...]} when the table has rows.
3. Supports ?symbol= and ?limit= query params.
4. NEVER imports or constructs a ccxt client.
5. NEVER touches the network.

Python 3.12 venv; 'from __future__ import annotations' for forward refs.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_db_with_ohlcv(tmp_path: Path, symbol: str = "BTCUSDT") -> str:
    """Create a WAL-mode DB with a bot_ohlcv table and a few rows."""
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_ohlcv (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL,          -- unix epoch ms (open time)
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            ema9 REAL,
            ema21 REAL,
            rsi14 REAL,
            PRIMARY KEY (symbol, timeframe, ts)
        )
        """
    )
    # Insert 3 candles
    candles = [
        (symbol, "15m", 1_704_067_200_000, 40000.0, 40500.0, 39800.0, 40300.0, 100.0, 40100.0, 39900.0, 55.0),
        (symbol, "15m", 1_704_068_100_000, 40300.0, 40800.0, 40200.0, 40750.0, 120.0, 40250.0, 40000.0, 58.0),
        (symbol, "15m", 1_704_069_000_000, 40750.0, 41000.0, 40600.0, 40900.0, 90.0,  40450.0, 40100.0, 60.0),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO bot_ohlcv "
        "(symbol,timeframe,ts,open,high,low,close,volume,ema9,ema21,rsi14) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        candles,
    )
    conn.commit()
    conn.close()
    return db_path


def _create_db_no_ohlcv(tmp_path: Path) -> str:
    """DB with trades table but no bot_ohlcv table."""
    db_path = str(tmp_path / "trades_no_ohlcv.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)"
    )
    conn.commit()
    conn.close()
    return db_path


def _create_empty_ohlcv_db(tmp_path: Path) -> str:
    """DB with bot_ohlcv table but no rows."""
    db_path = str(tmp_path / "empty_ohlcv.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE bot_ohlcv (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            ema9 REAL,
            ema21 REAL,
            rsi14 REAL,
            PRIMARY KEY (symbol, timeframe, ts)
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _get_client(db_path: str):
    import api.deps as deps_module
    from fastapi.testclient import TestClient
    from api.main import app
    app.dependency_overrides[deps_module.get_db_path] = lambda: db_path
    client = TestClient(app, raise_server_exceptions=True)
    return client, app


def _reset_overrides(app) -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test: available=false when no table
# ---------------------------------------------------------------------------

class TestCandlesUnavailable:
    """Returns {available: false} when bot_ohlcv table is absent or empty."""

    def test_no_table_returns_available_false(self, tmp_path):
        db = _create_db_no_ohlcv(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/candles?symbol=BTCUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["available"] is False
            assert data.get("candles", []) == []
        finally:
            _reset_overrides(app)

    def test_empty_table_returns_available_false(self, tmp_path):
        db = _create_empty_ohlcv_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/candles?symbol=BTCUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["available"] is False
        finally:
            _reset_overrides(app)

    def test_no_symbol_filter_with_no_table(self, tmp_path):
        db = _create_db_no_ohlcv(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/candles")
            assert resp.status_code == 200
            assert resp.json()["available"] is False
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# Test: available=true when table has rows
# ---------------------------------------------------------------------------

class TestCandlesAvailable:
    """Returns {available: true, candles: [...]} when rows exist."""

    def test_200_with_data(self, tmp_path):
        db = _create_db_with_ohlcv(tmp_path, symbol="BTCUSDT")
        client, app = _get_client(db)
        try:
            resp = client.get("/api/candles?symbol=BTCUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["available"] is True
            assert len(data["candles"]) == 3
        finally:
            _reset_overrides(app)

    def test_candle_schema_fields(self, tmp_path):
        db = _create_db_with_ohlcv(tmp_path, symbol="BTCUSDT")
        client, app = _get_client(db)
        try:
            data = client.get("/api/candles?symbol=BTCUSDT").json()
            candle = data["candles"][0]
            assert "ts" in candle
            assert "open" in candle
            assert "high" in candle
            assert "low" in candle
            assert "close" in candle
            assert "volume" in candle
            assert "ema9" in candle
            assert "ema21" in candle
            assert "rsi14" in candle
        finally:
            _reset_overrides(app)

    def test_candles_ordered_ascending_by_ts(self, tmp_path):
        db = _create_db_with_ohlcv(tmp_path, symbol="BTCUSDT")
        client, app = _get_client(db)
        try:
            data = client.get("/api/candles?symbol=BTCUSDT").json()
            tss = [c["ts"] for c in data["candles"]]
            assert tss == sorted(tss), "Candles must be ascending by timestamp"
        finally:
            _reset_overrides(app)

    def test_limit_param_respected(self, tmp_path):
        db = _create_db_with_ohlcv(tmp_path, symbol="BTCUSDT")
        client, app = _get_client(db)
        try:
            # DB has 3 candles; request limit=2 → 2 most-recent returned
            data = client.get("/api/candles?symbol=BTCUSDT&limit=2").json()
            assert data["available"] is True
            assert len(data["candles"]) == 2
        finally:
            _reset_overrides(app)

    def test_symbol_filter_returns_only_that_symbol(self, tmp_path):
        # Add candles for two symbols
        db = _create_db_with_ohlcv(tmp_path, symbol="BTCUSDT")
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT OR REPLACE INTO bot_ohlcv "
            "(symbol,timeframe,ts,open,high,low,close,volume) VALUES "
            "('ETHUSDT','15m',1704067200000,2500,2600,2490,2580,500)"
        )
        conn.commit()
        conn.close()
        client, app = _get_client(db)
        try:
            data = client.get("/api/candles?symbol=BTCUSDT").json()
            assert data["available"] is True
            for c in data["candles"]:
                # The endpoint filters by symbol; symbol is not in the response payload
                # (it's a URL param), but all ts values must match BTC rows
                assert c["ts"] in [1_704_067_200_000, 1_704_068_100_000, 1_704_069_000_000]
        finally:
            _reset_overrides(app)

    def test_response_includes_symbol_and_timeframe(self, tmp_path):
        db = _create_db_with_ohlcv(tmp_path, symbol="BTCUSDT")
        client, app = _get_client(db)
        try:
            data = client.get("/api/candles?symbol=BTCUSDT").json()
            assert data["symbol"] == "BTCUSDT"
            assert data["timeframe"] is not None
        finally:
            _reset_overrides(app)


# ---------------------------------------------------------------------------
# Critical: no ccxt import in the candles endpoint
# ---------------------------------------------------------------------------

class TestCandlesNoCcxt:
    """The candles endpoint must NEVER import or construct a ccxt client."""

    def test_ccxt_not_imported_in_candles_module(self):
        """Assert that api/routers/candles.py does not import ccxt."""
        candles_path = REPO / "api" / "routers" / "candles.py"
        assert candles_path.exists(), "api/routers/candles.py must exist"
        source = candles_path.read_text()
        assert "import ccxt" not in source, "candles.py must never import ccxt"
        assert "ccxt." not in source, "candles.py must never use ccxt."

    def test_endpoint_does_not_call_exchange(self, tmp_path):
        """Calling /api/candles must not trigger any network call."""
        import unittest.mock as mock

        db = _create_db_with_ohlcv(tmp_path, symbol="BTCUSDT")
        client, app = _get_client(db)

        # Patch socket.create_connection to fail if any network call is made
        with mock.patch("socket.create_connection", side_effect=AssertionError("Network call detected!")) as m:
            try:
                resp = client.get("/api/candles?symbol=BTCUSDT")
                # Should succeed without triggering socket
                assert resp.status_code == 200
                m.assert_not_called()
            finally:
                _reset_overrides(app)
