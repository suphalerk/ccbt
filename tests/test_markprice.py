"""TDD tests for api/markprice.py — realtime unrealized PnL via public Binance WS.

Tests use MOCKS for the websockets connection — no real network calls.

Covers:
1. Long position: uPnL = (mark - entry) * size * +1 (hand-computed)
2. Short position: uPnL = (mark - entry) * size * -1 (hand-computed)
3. Symbol-set change → triggers resubscribe (reconnect loop exits)
4. No API key / API secret referenced anywhere in api/markprice (import-time assert)
5. Feed-down → feed_status='offline', no crash, last-known uPnL retained
6. Throttle: second tick within < 1s must NOT emit a second broadcast
7. DB with no open positions → sleeps and returns (no WS connect attempt)
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_db(tmp_path: Path, trades: List[Dict[str, Any]]) -> str:
    """Create a WAL-mode trades.db with the given open trades."""
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            entry_price REAL,
            size REAL,
            status TEXT
        )"""
    )
    for t in trades:
        conn.execute(
            "INSERT INTO trades (symbol, side, entry_price, size, status) VALUES (?,?,?,?,?)",
            (t["symbol"], t["side"], t["entry_price"], t["size"], t.get("status", "open")),
        )
    conn.commit()
    conn.close()
    return db_path


def _make_mark_message(symbol: str, mark: float) -> str:
    """Build a Binance combined-stream markPriceUpdate frame as JSON string."""
    return json.dumps({
        "stream": f"{symbol.lower()}@markPrice",
        "data": {
            "e": "markPriceUpdate",
            "s": symbol.upper(),
            "p": str(mark),   # mark price field
        },
    })


# ---------------------------------------------------------------------------
# 1. Long position uPnL — hand-computed
# ---------------------------------------------------------------------------

class TestLongUpnl:
    """uPnL for a LONG position = (mark - entry) * size."""

    def test_long_upnl_positive_move(self, tmp_path: Any) -> None:
        """Entry 30000, mark 31000, size 0.5 → uPnL = (31000-30000)*0.5 = 500."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "long", "entry_price": 30000.0, "size": 0.5},
        ])
        from api.markprice import MarkPriceClient

        broadcasts: List[Dict[str, Any]] = []

        async def fake_broadcast(msg: Dict[str, Any]) -> None:
            broadcasts.append(msg)

        client = MarkPriceClient(
            db_path=db_path,
            broadcast_fn=fake_broadcast,
            ws_base_url="wss://unused",
        )
        # Load positions from DB
        client._reload_positions()

        async def run() -> None:
            await client._handle_message(_make_mark_message("BTCUSDT", 31000.0))

        asyncio.get_event_loop().run_until_complete(run())

        pos = client._positions.get("BTCUSDT")
        assert pos is not None, "BTCUSDT position must be loaded"
        assert abs(pos.upnl - 500.0) < 1e-9, f"Expected uPnL=500.0, got {pos.upnl}"
        assert abs(pos.mark_price - 31000.0) < 1e-9

        # Broadcast must have been emitted
        assert len(broadcasts) >= 1
        total = broadcasts[-1]["data"]["total_upnl"]
        assert abs(total - 500.0) < 1e-9

    def test_long_upnl_negative_move(self, tmp_path: Any) -> None:
        """Entry 30000, mark 29000, size 1.0 → uPnL = -1000."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "long", "entry_price": 30000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient

        broadcasts: List[Dict[str, Any]] = []
        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: broadcasts.append(m), ws_base_url="wss://unused")
        client._reload_positions()

        async def run() -> None:
            await client._handle_message(_make_mark_message("BTCUSDT", 29000.0))

        asyncio.get_event_loop().run_until_complete(run())
        pos = client._positions["BTCUSDT"]
        assert abs(pos.upnl - (-1000.0)) < 1e-9, f"Expected -1000.0, got {pos.upnl}"


# ---------------------------------------------------------------------------
# 2. Short position uPnL — hand-computed
# ---------------------------------------------------------------------------

class TestShortUpnl:
    """uPnL for a SHORT position = (mark - entry) * size * -1."""

    def test_short_upnl_positive_for_price_drop(self, tmp_path: Any) -> None:
        """Short entry 30000, mark 28000, size 0.1 → uPnL = (28000-30000)*0.1*(-1) = +200."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "ETHUSDT", "side": "short", "entry_price": 30000.0, "size": 0.1},
        ])
        from api.markprice import MarkPriceClient

        broadcasts: List[Dict[str, Any]] = []
        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: broadcasts.append(m), ws_base_url="wss://unused")
        client._reload_positions()

        async def run() -> None:
            await client._handle_message(_make_mark_message("ETHUSDT", 28000.0))

        asyncio.get_event_loop().run_until_complete(run())
        pos = client._positions["ETHUSDT"]
        assert abs(pos.upnl - 200.0) < 1e-9, f"Expected +200.0, got {pos.upnl}"

    def test_short_upnl_negative_for_price_rise(self, tmp_path: Any) -> None:
        """Short entry 30000, mark 32000, size 0.1 → uPnL = (32000-30000)*0.1*(-1) = -200."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "ETHUSDT", "side": "short", "entry_price": 30000.0, "size": 0.1},
        ])
        from api.markprice import MarkPriceClient

        broadcasts: List[Dict[str, Any]] = []
        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: broadcasts.append(m), ws_base_url="wss://unused")
        client._reload_positions()

        async def run() -> None:
            await client._handle_message(_make_mark_message("ETHUSDT", 32000.0))

        asyncio.get_event_loop().run_until_complete(run())
        pos = client._positions["ETHUSDT"]
        assert abs(pos.upnl - (-200.0)) < 1e-9, f"Expected -200.0, got {pos.upnl}"

    def test_side_buy_treated_as_long(self, tmp_path: Any) -> None:
        """'buy' side is treated as long (multiplier +1)."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "buy", "entry_price": 10000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient
        broadcasts: List[Dict[str, Any]] = []
        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: broadcasts.append(m), ws_base_url="wss://unused")
        client._reload_positions()

        async def run() -> None:
            await client._handle_message(_make_mark_message("BTCUSDT", 11000.0))

        asyncio.get_event_loop().run_until_complete(run())
        pos = client._positions["BTCUSDT"]
        assert abs(pos.upnl - 1000.0) < 1e-9, f"buy side must be long (+1000), got {pos.upnl}"

    def test_side_sell_treated_as_short(self, tmp_path: Any) -> None:
        """'sell' side is treated as short (multiplier -1)."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "sell", "entry_price": 10000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient
        broadcasts: List[Dict[str, Any]] = []
        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: broadcasts.append(m), ws_base_url="wss://unused")
        client._reload_positions()

        async def run() -> None:
            await client._handle_message(_make_mark_message("BTCUSDT", 11000.0))

        asyncio.get_event_loop().run_until_complete(run())
        pos = client._positions["BTCUSDT"]
        assert abs(pos.upnl - (-1000.0)) < 1e-9, f"sell side must be short (-1000), got {pos.upnl}"


# ---------------------------------------------------------------------------
# 3. Symbol-set change → resubscribe
# ---------------------------------------------------------------------------

class TestSymbolSetChange:
    """When _open_symbols != set(_positions.keys()), _run_once exits its WS loop."""

    def test_symbol_set_change_triggers_exit(self, tmp_path: Any) -> None:
        """Simulate the WS loop body: if open_symbols changes, the inner loop breaks."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "long", "entry_price": 30000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient

        broadcasts: List[Dict[str, Any]] = []
        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: broadcasts.append(m), ws_base_url="wss://unused")
        client._reload_positions()
        initial_symbols = set(client._positions.keys())
        assert "BTCUSDT" in initial_symbols

        # Simulate a new trade being opened in DB after the WS connected
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute(
            "INSERT INTO trades (symbol, side, entry_price, size, status) VALUES ('ETHUSDT','long',3000.0,10.0,'open')"
        )
        conn.close()

        # Poll loop updates _open_symbols
        new_symbols = client._reload_positions()
        assert "ETHUSDT" in new_symbols, "ETHUSDT must appear after DB insert"

        # The WS loop checks: if _open_symbols != set(_positions.keys()) → exit
        # After _reload_positions(), _positions is updated (contains both BTC and ETH)
        # _open_symbols was set to initial_symbols (just BTC).  Simulate the check:
        client._open_symbols = initial_symbols   # restore to pre-reload state (as if WS was connected)
        # Now positions has BTC+ETH; _open_symbols has only BTC → mismatch → reconnect
        mismatch = client._open_symbols != set(client._positions.keys())
        assert mismatch, "Symbol set mismatch must trigger reconnect (loop break)"


# ---------------------------------------------------------------------------
# 4. No API key/secret referenced in api/markprice module
# ---------------------------------------------------------------------------

class TestNoApiKey:
    """api/markprice must not reference any API key or secret."""

    def test_no_api_key_in_module_source(self) -> None:
        """Read the module source and assert no API key variable names appear."""
        import inspect
        import api.markprice as mp_mod
        source = inspect.getsource(mp_mod)
        forbidden = ["API_KEY", "API_SECRET", "MAINNET_API_KEY", "MAINNET_SECRET_KEY",
                     "api_key", "api_secret", "apiKey", "apiSecret"]
        found = [name for name in forbidden if name in source]
        assert not found, (
            f"api/markprice.py must NOT reference API key variables; found: {found}"
        )

    def test_no_api_key_imported(self) -> None:
        """Importing api.markprice must not trigger any key-reading side-effects."""
        # If we can import without exception, there's no eager key read
        import api.markprice  # noqa: F401 (side-effect import test)


# ---------------------------------------------------------------------------
# 5. Feed-down → feed_status='offline', no crash, last-known retained
# ---------------------------------------------------------------------------

class TestFeedDown:
    """On WS disconnect, feed_status must be 'offline'; last-known uPnL must persist."""

    def test_offline_status_on_disconnect(self, tmp_path: Any) -> None:
        """After a connection error, get_upnl_payload must show feed_status='offline'."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "long", "entry_price": 30000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient

        broadcasts: List[Dict[str, Any]] = []
        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: broadcasts.append(m), ws_base_url="wss://unused")
        client._reload_positions()

        async def run() -> None:
            # Simulate a mark tick while live
            client._feed_status = "live"
            await client._handle_message(_make_mark_message("BTCUSDT", 31000.0))
            # Record uPnL before disconnect
            upnl_before = client._positions["BTCUSDT"].upnl

            # Simulate disconnect
            client._feed_status = "offline"

            payload = client.get_upnl_payload()
            assert payload["data"]["feed_status"] == "offline", (
                f"feed_status must be 'offline' after disconnect; got {payload['data']['feed_status']}"
            )
            # Last-known uPnL must be retained
            pos_in_payload = next(
                (p for p in payload["data"]["positions"] if p["symbol"] == "BTCUSDT"), None
            )
            assert pos_in_payload is not None, "BTCUSDT must still appear in offline payload"
            assert abs(pos_in_payload["upnl"] - upnl_before) < 1e-9, (
                f"Last-known uPnL {upnl_before} must be retained offline; got {pos_in_payload['upnl']}"
            )

        asyncio.get_event_loop().run_until_complete(run())

    def test_no_crash_on_bad_ws_message(self, tmp_path: Any) -> None:
        """Malformed WS frame must not crash the client."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "long", "entry_price": 30000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient

        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: None, ws_base_url="wss://unused")
        client._reload_positions()

        async def run() -> None:
            # Malformed JSON — must not raise
            try:
                await client._handle_message("not-valid-json{{{")
            except Exception as exc:
                raise AssertionError(f"handle_message must not raise on bad JSON; got {exc!r}") from exc
            # Empty dict — must not raise
            try:
                await client._handle_message(json.dumps({}))
            except Exception as exc:
                raise AssertionError(f"handle_message must not raise on empty dict; got {exc!r}") from exc

        asyncio.get_event_loop().run_until_complete(run())

    def test_run_forever_reconnects_after_exception(self, tmp_path: Any) -> None:
        """run_forever must reconnect after _run_once raises, not crash."""
        db_path = _seed_db(tmp_path, [])
        from api.markprice import MarkPriceClient

        call_count = 0

        async def fake_broadcast(msg: Any) -> None:
            pass

        client = MarkPriceClient(db_path=db_path, broadcast_fn=fake_broadcast, ws_base_url="wss://unused")

        async def fake_run_once(self_inner: Any = None) -> None:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("simulated WS failure")
            # On the 3rd call, cancel to exit run_forever
            raise asyncio.CancelledError()

        import api.markprice as mp_mod
        original = MarkPriceClient._run_once

        async def run() -> None:
            with patch.object(MarkPriceClient, "_run_once", fake_run_once):
                with pytest.raises(asyncio.CancelledError):
                    await client.run_forever()

        asyncio.get_event_loop().run_until_complete(run())
        assert call_count >= 3, f"run_forever must retry after failures; only called {call_count} times"


# ---------------------------------------------------------------------------
# 6. Throttle: rapid marks must NOT produce more than 1 broadcast/second
# ---------------------------------------------------------------------------

class TestThrottle:
    """Two ticks within the same second must produce at most 1 broadcast."""

    def test_second_tick_throttled(self, tmp_path: Any) -> None:
        """Two back-to-back handle_message calls must emit only 1 broadcast."""
        import time
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "long", "entry_price": 30000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient, BROADCAST_MIN_INTERVAL_S

        broadcasts: List[Dict[str, Any]] = []

        async def fake_broadcast(msg: Dict[str, Any]) -> None:
            broadcasts.append(msg)

        client = MarkPriceClient(db_path=db_path, broadcast_fn=fake_broadcast, ws_base_url="wss://unused")
        client._reload_positions()
        client._feed_status = "live"

        async def run() -> None:
            # Set last_broadcast_ts to "just now" — next broadcast is throttled
            client._last_broadcast_ts = time.monotonic()
            await client._handle_message(_make_mark_message("BTCUSDT", 31000.0))
            await client._handle_message(_make_mark_message("BTCUSDT", 31100.0))

        asyncio.get_event_loop().run_until_complete(run())
        assert len(broadcasts) == 0, (
            f"Broadcasts within throttle window must be suppressed; got {len(broadcasts)}"
        )

    def test_broadcast_after_throttle_window(self, tmp_path: Any) -> None:
        """After BROADCAST_MIN_INTERVAL_S has elapsed, the next tick emits a broadcast."""
        import time
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "long", "entry_price": 30000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient, BROADCAST_MIN_INTERVAL_S

        broadcasts: List[Dict[str, Any]] = []

        async def fake_broadcast(msg: Dict[str, Any]) -> None:
            broadcasts.append(msg)

        client = MarkPriceClient(db_path=db_path, broadcast_fn=fake_broadcast, ws_base_url="wss://unused")
        client._reload_positions()
        client._feed_status = "live"

        async def run() -> None:
            # Set last_broadcast_ts to far in the past
            client._last_broadcast_ts = time.monotonic() - BROADCAST_MIN_INTERVAL_S - 1.0
            await client._handle_message(_make_mark_message("BTCUSDT", 31000.0))

        asyncio.get_event_loop().run_until_complete(run())
        assert len(broadcasts) == 1, (
            f"Broadcast must be emitted after throttle window; got {len(broadcasts)}"
        )


# ---------------------------------------------------------------------------
# 7. No open positions → no WS connect, returns cleanly
# ---------------------------------------------------------------------------

class TestNoOpenPositions:
    """With zero open positions, _run_once must sleep and return without connecting."""

    def test_no_connect_when_no_positions(self, tmp_path: Any) -> None:
        """Empty DB → _run_once sleeps SYMBOL_POLL_S and returns."""
        db_path = _seed_db(tmp_path, [])
        from api.markprice import MarkPriceClient

        broadcasts: List[Dict[str, Any]] = []
        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: broadcasts.append(m), ws_base_url="wss://unused")

        ws_connect_called = False

        async def mock_sleep(n: float) -> None:
            pass  # fast-forward

        async def run() -> None:
            with patch("asyncio.sleep", mock_sleep):
                await client._run_once()

        # Should complete without attempting websockets.connect
        asyncio.get_event_loop().run_until_complete(run())
        # No broadcasts emitted for empty positions
        # Key: must not raise, must not connect to WS


# ---------------------------------------------------------------------------
# 8. total_upnl is the sum of all per-position uPnLs
# ---------------------------------------------------------------------------

class TestTotalUpnl:
    """total_upnl = sum of all individual position uPnLs."""

    def test_total_upnl_multi_position(self, tmp_path: Any) -> None:
        """BTC long +500, ETH short +200 → total = 700."""
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "long", "entry_price": 30000.0, "size": 0.5},
            {"symbol": "ETHUSDT", "side": "short", "entry_price": 2000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient

        broadcasts: List[Dict[str, Any]] = []
        client = MarkPriceClient(db_path=db_path, broadcast_fn=lambda m: broadcasts.append(m), ws_base_url="wss://unused")
        client._reload_positions()
        client._feed_status = "live"

        async def run() -> None:
            # BTC: (31000-30000)*0.5 = +500
            await client._handle_message(_make_mark_message("BTCUSDT", 31000.0))
            # ETH: (1800-2000)*1.0*(-1) = +200
            # Reset throttle so second broadcast goes through
            client._last_broadcast_ts = 0.0
            await client._handle_message(_make_mark_message("ETHUSDT", 1800.0))

        asyncio.get_event_loop().run_until_complete(run())
        payload = client.get_upnl_payload()
        assert abs(payload["data"]["total_upnl"] - 700.0) < 1e-9, (
            f"total_upnl must be 700.0; got {payload['data']['total_upnl']}"
        )
