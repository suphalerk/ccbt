"""Comprehensive Binance testnet API test suite for CCBT.

Tests every exchange API endpoint the bot uses against Binance testnet.
Uses the actual BybitClient class from bot/exchange.py.

Run:
    python tests/test_api_binance_testnet.py
"""

import sys
import os
import time
import json
import math

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.exchange import BybitClient

# ---------------------------------------------------------------------------
# Config — mirrors config_yolo.json but only needs fields used by BybitClient
# ---------------------------------------------------------------------------
CONFIG = {
    "exchange": "binance",
    "symbol": "BTCUSDT",
    "timeframe_signal": "15m",
    "timeframe_trend": "1h",
    "leverage": 100,
    "use_testnet": True,
}

# Minimum BTC trade size on Binance futures testnet.
# Binance requires minimum notional of $100.  At ~$70k/BTC, 0.002 BTC = ~$140.
MIN_BTC_SIZE = 0.002
MIN_NOTIONAL_USD = 110.0

# ---------------------------------------------------------------------------
# Test framework helpers
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []
_total = 0
_passed = 0


def result(idx: int, name: str, ok: bool, detail: str) -> None:
    """Print a single test result and record it."""
    global _passed
    status = "PASS" if ok else "FAIL"
    padded = f"{name} ".ljust(45, ".")
    print(f"[{idx:2d}/{_total}] {padded} {status}  ({detail})")
    _results.append((name, ok, detail))
    if ok:
        _passed += 1


def sleep(seconds: float = 0.5) -> None:
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run_tests() -> None:
    global _total
    _total = 21

    print()
    print("=" * 70)
    print("  BINANCE TESTNET API TEST SUITE (updated for separate SL/TP orders)")
    print("=" * 70)
    print()

    # ------------------------------------------------------------------
    # GROUP 1 — Initialization
    # ------------------------------------------------------------------
    client: BybitClient | None = None

    # Test 1: load_markets
    try:
        client = BybitClient(CONFIG)
        symbol = client.symbol
        markets = client.exchange.markets
        if symbol in markets:
            result(1, "load_markets", True, f"found {symbol} in {len(markets)} markets")
        else:
            result(1, "load_markets", False, f"{symbol} NOT in {len(markets)} markets")
    except Exception as e:
        result(1, "load_markets", False, str(e))
        _print_summary()
        return

    sleep()

    # Test 2: set_leverage
    try:
        actual_lev = client.set_leverage(CONFIG["leverage"], client.symbol)
        result(2, "set_leverage", True, f"{actual_lev}x set for {client.symbol}")
    except Exception as e:
        if "already" in str(e).lower() or "not modified" in str(e).lower():
            result(2, "set_leverage", True, f"already set (no-op)")
        else:
            result(2, "set_leverage", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 2 — Account Data
    # ------------------------------------------------------------------

    # Test 3: fetch_balance
    try:
        balance = client.get_balance()
        if balance > 0:
            result(3, "fetch_balance", True, f"USDT free = {balance:.2f}")
        else:
            result(3, "fetch_balance", False, f"balance = {balance}")
    except Exception as e:
        result(3, "fetch_balance", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 3 — Market Data
    # ------------------------------------------------------------------

    last_close = None

    # Test 4: fetch_ohlcv 15m
    try:
        df_15m = client.get_ohlcv(client.symbol, "15m", limit=100)
        rows = len(df_15m)
        last_close = float(df_15m["close"].iloc[-1])
        result(4, "fetch_ohlcv 15m", rows >= 90, f"{rows} candles, last={last_close:.2f}")
    except Exception as e:
        result(4, "fetch_ohlcv 15m", False, str(e))

    sleep()

    # Test 5: fetch_ohlcv 1h
    try:
        df_1h = client.get_ohlcv(client.symbol, "1h", limit=100)
        result(5, "fetch_ohlcv 1h", len(df_1h) >= 90, f"{len(df_1h)} candles")
    except Exception as e:
        result(5, "fetch_ohlcv 1h", False, str(e))

    sleep()

    # Test 6: fetch_ticker
    try:
        price = client.get_ticker_price(client.symbol)
        if price > 0:
            result(6, "fetch_ticker", True, f"last price = {price:.2f}")
            last_close = price
        else:
            result(6, "fetch_ticker", False, f"price = {price}")
    except Exception as e:
        result(6, "fetch_ticker", False, str(e))

    # Calculate min trade size dynamically
    if last_close and last_close > 0:
        step = 0.001
        min_trade_size = math.ceil((MIN_NOTIONAL_USD / last_close) / step) * step
        min_trade_size = round(min_trade_size, 3)
    else:
        min_trade_size = MIN_BTC_SIZE

    sleep()

    # Test 7: fetch_funding_rate
    try:
        rate = client.get_funding_rate(client.symbol)
        result(7, "fetch_funding_rate", True, f"rate = {rate:.6f} ({rate*100:.4f}%)")
    except Exception as e:
        result(7, "fetch_funding_rate", False, str(e))

    sleep()

    # Test 8: fetch_open_interest
    try:
        oi = client.exchange.fetch_open_interest(client.symbol)
        oi_val = oi.get("openInterest") or oi.get("openInterestAmount", "n/a")
        result(8, "fetch_open_interest", True, f"OI = {oi_val}")
    except Exception as e:
        result(8, "fetch_open_interest", False, str(e))

    sleep()

    # Test 9: fetch_order_book
    try:
        ob = client.exchange.fetch_order_book(client.symbol, limit=20)
        bids = len(ob.get("bids", []))
        asks = len(ob.get("asks", []))
        if bids > 0 and asks > 0:
            result(9, "fetch_order_book", True, f"bid={ob['bids'][0][0]:.2f} ask={ob['asks'][0][0]:.2f}")
        else:
            result(9, "fetch_order_book", False, f"empty (bids={bids}, asks={asks})")
    except Exception as e:
        result(9, "fetch_order_book", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 4 — Order Management (LONG round-trip)
    # ------------------------------------------------------------------

    long_filled_size = min_trade_size

    # Test 10: open long (no SL/TP)
    try:
        order = client.place_order("buy", min_trade_size)
        long_filled_size = order.size
        result(10, "open_long (market)", True,
               f"id={order.order_id} size={long_filled_size} price={order.price}")
    except Exception as e:
        result(10, "open_long (market)", False, str(e))

    sleep()

    # Test 11: verify long position exists
    try:
        positions = client.get_positions()
        long_found = any(
            p.get("side") == "long" and float(p.get("contracts", 0)) > 0
            for p in positions
        )
        result(11, "verify_long_position", long_found,
               f"positions={[(p['side'], p['contracts']) for p in positions]}")
    except Exception as e:
        result(11, "verify_long_position", False, str(e))

    sleep()

    # Test 12: close long
    try:
        positions = client.get_positions()
        total_long = sum(
            float(p.get("contracts", 0)) for p in positions if p.get("side") == "long"
        )
        if total_long <= 0:
            total_long = long_filled_size
        close_order = client.place_order("sell", total_long, reduce_only=True)
        result(12, "close_long (reduceOnly)", True,
               f"id={close_order.order_id} size={close_order.size}")
    except Exception as e:
        result(12, "close_long (reduceOnly)", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 5 — SL/TP as Separate Orders (NEW — critical Binance test)
    # ------------------------------------------------------------------

    # Test 13: place_order with SL+TP → verify algo orders created
    try:
        live_price = client.get_ticker_price(client.symbol)
        sl_price = client.price_precision(live_price * 0.96)  # 4% below
        tp_price = client.price_precision(live_price * 1.06)  # 6% above

        order = client.place_order("buy", min_trade_size, sl=sl_price, tp=tp_price)
        sleep(1.5)

        # Verify SL/TP exist as algo conditional orders
        algo_orders = client._get_binance_algo_orders(client.symbol)
        sl_found = any(o.get("orderType") == "STOP_MARKET" for o in algo_orders)
        tp_found = any(o.get("orderType") == "TAKE_PROFIT_MARKET" for o in algo_orders)

        result(13, "place_order SL+TP (algo orders)", sl_found and tp_found,
               f"sl={sl_found} tp={tp_found} algo_count={len(algo_orders)}")
    except Exception as e:
        result(13, "place_order SL+TP (algo orders)", False, str(e))

    sleep()

    # Test 14: verify SL/TP trigger prices match
    try:
        algo_orders = client._get_binance_algo_orders(client.symbol)
        sl_trigger = 0.0
        tp_trigger = 0.0
        for ao in algo_orders:
            if ao.get("orderType") == "STOP_MARKET":
                sl_trigger = float(ao.get("triggerPrice", 0))
            elif ao.get("orderType") == "TAKE_PROFIT_MARKET":
                tp_trigger = float(ao.get("triggerPrice", 0))

        sl_match = abs(sl_trigger - sl_price) < 1.0 if sl_trigger > 0 else False
        tp_match = abs(tp_trigger - tp_price) < 1.0 if tp_trigger > 0 else False

        result(14, "SL/TP trigger prices match", sl_match and tp_match,
               f"sl={sl_trigger} (want {sl_price}) tp={tp_trigger} (want {tp_price})")
    except Exception as e:
        result(14, "SL/TP trigger prices match", False, str(e))

    sleep()

    # Test 15: modify_sl (cancel old STOP_MARKET algo, place new)
    try:
        new_sl = client.price_precision(live_price * 0.95)  # 5% below
        ok = client.modify_sl(symbol=client.symbol, side="buy", new_sl=new_sl)
        sleep(1.5)

        # Verify: old SL gone, new SL present in algo orders
        algo_orders = client._get_binance_algo_orders(client.symbol)
        new_sl_found = False
        sl_count = 0
        for ao in algo_orders:
            if ao.get("orderType") == "STOP_MARKET":
                sl_count += 1
                trigger = float(ao.get("triggerPrice", 0))
                if abs(trigger - new_sl) < 1.0:
                    new_sl_found = True

        result(15, "modify_sl (cancel-recreate)", ok and new_sl_found and sl_count == 1,
               f"ok={ok} new_sl_found={new_sl_found} sl_count={sl_count} target={new_sl}")
    except Exception as e:
        result(15, "modify_sl (cancel-recreate)", False, str(e))

    sleep()

    # Test 16: verify TP survived modify_sl (should NOT be cancelled)
    try:
        algo_orders = client._get_binance_algo_orders(client.symbol)
        tp_survived = any(o.get("orderType") == "TAKE_PROFIT_MARKET" for o in algo_orders)
        result(16, "TP survives modify_sl", tp_survived,
               f"tp_exists={tp_survived} algo_count={len(algo_orders)}")
    except Exception as e:
        result(16, "TP survives modify_sl", False, str(e))

    sleep()

    # Cleanup: cancel all orders + close position
    try:
        client.cancel_all_orders(client.symbol)
    except Exception:
        pass
    sleep(0.5)
    try:
        positions = client.get_positions()
        total_long = sum(
            float(p.get("contracts", 0)) for p in positions if p.get("side") == "long"
        )
        if total_long > 0:
            client.place_order("sell", total_long, reduce_only=True)
    except Exception:
        pass

    sleep()

    # ------------------------------------------------------------------
    # GROUP 6 — Short Round-Trip
    # ------------------------------------------------------------------

    # Test 17: short round-trip with SL+TP
    try:
        live_price = client.get_ticker_price(client.symbol)
        short_sl = client.price_precision(live_price * 1.04)  # 4% above
        short_tp = client.price_precision(live_price * 0.94)  # 6% below

        short_order = client.place_order("sell", min_trade_size, sl=short_sl, tp=short_tp)
        sleep(1.5)

        # Verify position + algo orders
        positions = client.get_positions()
        short_found = any(
            p.get("side") == "short" and float(p.get("contracts", 0)) > 0
            for p in positions
        )
        algo_orders = client._get_binance_algo_orders(client.symbol)
        sl_found = any(o.get("orderType") == "STOP_MARKET" for o in algo_orders)
        tp_found = any(o.get("orderType") == "TAKE_PROFIT_MARKET" for o in algo_orders)

        # Cleanup
        try:
            client.cancel_all_orders(client.symbol)
        except Exception:
            pass
        sleep(0.3)
        total_short = sum(
            float(p.get("contracts", 0)) for p in positions if p.get("side") == "short"
        )
        if total_short > 0:
            client.place_order("buy", total_short, reduce_only=True)

        result(17, "short round-trip + SL+TP", short_found and sl_found and tp_found,
               f"pos={short_found} sl={sl_found} tp={tp_found}")
    except Exception as e:
        result(17, "short round-trip + SL+TP", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 7 — Trade History
    # ------------------------------------------------------------------

    # Test 18: fetch_my_trades
    try:
        since_ms = int((time.time() - 3600) * 1000)
        trades = client.exchange.fetch_my_trades(client.symbol, since=since_ms, limit=50)
        result(18, "fetch_my_trades", True, f"{len(trades)} trades")
    except Exception as e:
        result(18, "fetch_my_trades", False, str(e))

    sleep()

    # Test 19: get_closed_pnl
    try:
        since_ms = int((time.time() - 3600) * 1000)
        closed = client.get_closed_pnl(client.symbol, since_ms=since_ms)
        result(19, "get_closed_pnl", True, f"{len(closed)} records")
    except Exception as e:
        result(19, "get_closed_pnl", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 8 — Price Precision
    # ------------------------------------------------------------------

    # Test 20: price_precision works for BTC
    try:
        precise = client.price_precision(87654.321)
        # BTC/USDT:USDT should have 1 or 2 decimal precision
        result(20, "price_precision BTC", precise > 0, f"87654.321 → {precise}")
    except Exception as e:
        result(20, "price_precision BTC", False, str(e))

    # ------------------------------------------------------------------
    # GROUP 9 — Final Cleanup
    # ------------------------------------------------------------------

    # Test 21: cancel_all_orders + close_all_positions
    try:
        client.cancel_all_orders(client.symbol)
        client.close_all_positions(client.symbol)
        positions = client.get_positions()
        clean = all(float(p.get("contracts", 0)) == 0 for p in positions) if positions else True
        result(21, "final_cleanup", clean,
               f"all orders cancelled, positions closed")
    except Exception as e:
        if "no order" in str(e).lower() or "no position" in str(e).lower():
            result(21, "final_cleanup", True, "nothing to clean")
        else:
            result(21, "final_cleanup", False, str(e))

    _print_summary()


def _print_summary() -> None:
    failed = [(name, detail) for name, ok, detail in _results if not ok]
    print()
    print("=" * 70)
    print(f"  RESULTS: {_passed}/{_total} PASSED")
    if failed:
        print()
        print("  FAILED:")
        for name, detail in failed:
            print(f"    - {name}: {detail}")
    else:
        print("  ALL TESTS PASSED!")
    print("=" * 70)
    print()


if __name__ == "__main__":
    run_tests()
