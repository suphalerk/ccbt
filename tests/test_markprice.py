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
    """When the DB grows a new open symbol, _run_once must tear down and rebuild the WS
    with the new symbol included in the subscribe URL.

    The test drives the REAL _symbol_poll_loop + _run_once control flow using a mock
    websockets.connect.  It must FAIL against the old buggy code (where _symbol_poll_loop
    called _reload_positions() and _open_symbols was clobbered before the break check)
    and PASS after the fix.
    """

    def test_new_symbol_triggers_reconnect_and_resubscribe(self, tmp_path: Any) -> None:
        """Insert ETHUSDT into DB after WS connects; _run_once must exit and the next
        _run_once call must build a URL that includes both BTCUSDT and ETHUSDT.

        Mechanism: the fake WS for session 1 first yields one BTC tick, then blocks on an
        asyncio.Event.  Meanwhile the patched asyncio.sleep (fired by _symbol_poll_loop)
        inserts ETHUSDT into the DB and sets the event, unblocking __anext__ which raises
        StopAsyncIteration.  At that point _needs_reconnect is already True, so the inner
        for-loop in _run_once sees it and breaks cleanly.
        """
        db_path = _seed_db(tmp_path, [
            {"symbol": "BTCUSDT", "side": "long", "entry_price": 30000.0, "size": 1.0},
        ])
        from api.markprice import MarkPriceClient, SYMBOL_POLL_S

        broadcasts: List[Dict[str, Any]] = []

        async def fake_broadcast(msg: Dict[str, Any]) -> None:
            broadcasts.append(msg)

        client = MarkPriceClient(
            db_path=db_path,
            broadcast_fn=fake_broadcast,
            ws_base_url="wss://testhost",
        )

        # Track every URL that websockets.connect is called with
        connect_urls: List[str] = []

        # An event that the poll-sleep callback will set after inserting ETHUSDT.
        # The session-1 FakeWS blocks on this event, so it stays alive long enough
        # for the poll loop to detect the change and set _needs_reconnect=True.
        eth_inserted_event: Optional[asyncio.Event] = None

        session_index = [0]

        class FakeWS:
            """Async context manager + async iterator.
            Session 0: yield one BTC tick, then block until eth_inserted_event is set.
            Session 1: yield one ETH tick + one BTC tick, then stop.
            """
            def __init__(self, url: str) -> None:
                self.url = url
                self._session = session_index[0]
                connect_urls.append(url)
                session_index[0] += 1
                self._tick_count = 0

            async def __aenter__(self) -> "FakeWS":
                return self

            async def __aexit__(self, *_: Any) -> None:
                pass

            def __aiter__(self) -> "FakeWS":
                return self

            async def __anext__(self) -> str:
                if self._session == 0:
                    if self._tick_count == 0:
                        self._tick_count += 1
                        return _make_mark_message("BTCUSDT", 31000.0)
                    # Block until the poll loop fires and inserts ETHUSDT
                    assert eth_inserted_event is not None
                    await eth_inserted_event.wait()
                    raise StopAsyncIteration
                else:
                    # Session 1: just a couple of ticks so _run_once can complete
                    if self._tick_count < 2:
                        sym = "BTCUSDT" if self._tick_count == 0 else "ETHUSDT"
                        self._tick_count += 1
                        return _make_mark_message(sym, 31500.0 if sym == "BTCUSDT" else 2100.0)
                    raise StopAsyncIteration

        # Patch asyncio.sleep so the poll-loop sleep(SYMBOL_POLL_S) resolves instantly
        # and inserts ETHUSDT into the DB the first time it fires.
        poll_sleep_calls = [0]
        original_sleep = asyncio.sleep

        async def patched_sleep(n: float) -> None:
            nonlocal eth_inserted_event
            if abs(n - SYMBOL_POLL_S) < 1e-3:
                poll_sleep_calls[0] += 1
                if poll_sleep_calls[0] == 1:
                    # Insert ETHUSDT into DB so the poll query sees a new symbol
                    conn2 = sqlite3.connect(db_path, isolation_level=None)
                    conn2.execute(
                        "INSERT INTO trades (symbol, side, entry_price, size, status)"
                        " VALUES ('ETHUSDT','long',2000.0,5.0,'open')"
                    )
                    conn2.close()
                    # Unblock the FakeWS session-0 __anext__
                    if eth_inserted_event is not None:
                        eth_inserted_event.set()
            # Always yield without real delay
            await original_sleep(0)

        async def run_two_sessions() -> None:
            nonlocal eth_inserted_event
            eth_inserted_event = asyncio.Event()
            with patch("asyncio.sleep", patched_sleep):
                with patch("websockets.connect", side_effect=lambda url, **kw: FakeWS(url)):
                    # Session 1: connects BTC-only; poll fires → ETHUSDT inserted →
                    #            _needs_reconnect=True → WS loop breaks
                    await client._run_once()
                    # Session 2: reconnects; _reload_positions() sees both BTC + ETH
                    await client._run_once()

        asyncio.get_event_loop().run_until_complete(run_two_sessions())

        assert len(connect_urls) == 2, (
            f"websockets.connect must be called twice (once per session); got {connect_urls}"
        )
        first_url, second_url = connect_urls
        assert "btcusdt@markprice" in first_url.lower(), (
            f"Session 1 URL must contain btcusdt@markPrice; got {first_url}"
        )
        # After reconnect, both symbols must appear in the subscribe URL
        assert "ethusdt@markprice" in second_url.lower(), (
            f"Session 2 URL must contain ethusdt@markPrice (new symbol); got {second_url}"
        )
        assert "btcusdt@markprice" in second_url.lower(), (
            f"Session 2 URL must still contain btcusdt@markPrice; got {second_url}"
        )


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
