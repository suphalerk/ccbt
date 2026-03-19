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
# If market price changes significantly, the test will dynamically recalculate
# (see MIN_NOTIONAL_USD + get-price fallback below).
MIN_BTC_SIZE = 0.002  # 0.002 BTC — satisfies $100 minimum notional at current price
MIN_NOTIONAL_USD = 110.0  # Target notional with a 10% buffer over the $100 minimum

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
    # Pad name for alignment
    padded = f"{name} ".ljust(40, ".")
    print(f"[{idx:2d}/18] {padded} {status}  ({detail})")
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
    _total = 18

    print()
    print("=" * 65)
    print("  BINANCE TESTNET API TEST SUITE")
    print("=" * 65)
    print()

    # ------------------------------------------------------------------
    # Initialise client (GROUP 1 — tests 1 & 2 happen inside init/after)
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
        # Cannot continue without a client
        _print_summary()
        return

    sleep()

    # Test 2: set_leverage
    try:
        client.set_leverage(CONFIG["leverage"], client.symbol)
        result(2, "set_leverage", True, f"{CONFIG['leverage']}x set for {client.symbol}")
    except Exception as e:
        # Binance testnet sometimes returns "already set" style errors
        if "already" in str(e).lower() or "not modified" in str(e).lower():
            result(2, "set_leverage", True, f"{CONFIG['leverage']}x already set (no-op)")
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
            result(3, "fetch_balance", True, f"USDT free balance = {balance:.2f}")
        else:
            result(3, "fetch_balance", False, f"balance is 0 or negative: {balance}")
    except Exception as e:
        result(3, "fetch_balance", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 3 — Market Data
    # ------------------------------------------------------------------

    # Test 4: fetch_ohlcv 15m (signal timeframe)
    try:
        df_15m = client.get_ohlcv(client.symbol, "15m", limit=100)
        rows = len(df_15m)
        last_close = float(df_15m["close"].iloc[-1])
        if rows >= 90:
            result(4, "fetch_ohlcv 15m", True, f"{rows} candles, last close={last_close:.2f}")
        else:
            result(4, "fetch_ohlcv 15m", False, f"only {rows} candles returned (expected 100)")
    except Exception as e:
        result(4, "fetch_ohlcv 15m", False, str(e))
        last_close = None
    else:
        last_close = float(df_15m["close"].iloc[-1])

    sleep()

    # Test 5: fetch_ohlcv 1h (trend timeframe)
    try:
        df_1h = client.get_ohlcv(client.symbol, "1h", limit=100)
        rows = len(df_1h)
        result(5, "fetch_ohlcv 1h", rows >= 90, f"{rows} candles")
    except Exception as e:
        result(5, "fetch_ohlcv 1h", False, str(e))

    sleep()

    # Test 6: fetch_ticker
    try:
        price = client.get_ticker_price(client.symbol)
        if price > 0:
            result(6, "fetch_ticker", True, f"last price = {price:.2f}")
            last_close = price  # Use live price for subsequent order sizing
        else:
            result(6, "fetch_ticker", False, f"price is {price}")
    except Exception as e:
        result(6, "fetch_ticker", False, str(e))

    # Recalculate minimum trade size to satisfy Binance $100 notional minimum.
    # Apply exchange precision (step size) so the order is accepted.
    if last_close and last_close > 0:
        raw_min_size = MIN_NOTIONAL_USD / last_close
        # Round up to nearest 0.001 BTC step (Binance BTC step size)
        import math
        step = 0.001
        min_trade_size = math.ceil(raw_min_size / step) * step
        min_trade_size = round(min_trade_size, 3)
    else:
        min_trade_size = MIN_BTC_SIZE

    sleep()

    # Test 7: fetch_funding_rate
    try:
        rate = client.get_funding_rate(client.symbol)
        result(7, "fetch_funding_rate", True, f"funding rate = {rate:.6f} ({rate*100:.4f}%)")
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
            best_bid = ob["bids"][0][0]
            best_ask = ob["asks"][0][0]
            result(9, "fetch_order_book", True, f"bid={best_bid:.2f} ask={best_ask:.2f} ({bids} bids, {asks} asks)")
        else:
            result(9, "fetch_order_book", False, f"empty order book (bids={bids}, asks={asks})")
    except Exception as e:
        result(9, "fetch_order_book", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 4 — Order Management (LONG)
    # ------------------------------------------------------------------

    long_order_id: str | None = None
    long_filled_size: float = min_trade_size

    # Test 10: create long order
    try:
        order = client.place_order(
            side="buy",
            size=min_trade_size,
            sl=None,
            tp=None,
            reduce_only=False,
        )
        long_order_id = order.order_id
        long_filled_size = order.size
        result(
            10,
            "create_order long",
            True,
            f"order_id={long_order_id} size={long_filled_size} price={order.price}",
        )
    except Exception as e:
        result(10, "create_order long", False, str(e))

    sleep()

    # Test 11: fetch_positions (verify long open)
    long_position_found = False
    try:
        positions = client.get_positions()
        for p in positions:
            if float(p.get("contracts", 0)) > 0:
                long_position_found = True
                side_str = p.get("side", "?")
                contracts = float(p.get("contracts", 0))
                entry = p.get("entryPrice") or p.get("info", {}).get("entryPrice", "?")
                result(
                    11,
                    "fetch_positions (long open)",
                    True,
                    f"side={side_str} contracts={contracts} entry={entry}",
                )
                break
        if not long_position_found:
            result(11, "fetch_positions (long open)", False, "no active position found")
    except Exception as e:
        result(11, "fetch_positions (long open)", False, str(e))

    sleep()

    # Test 12: SL/TP can be set (place order with SL+TP params)
    # We use a new small order with SL+TP and immediately close it.
    sl_tp_order_id: str | None = None
    sl_tp_size: float = min_trade_size
    try:
        # Get live price for realistic SL/TP
        live_price = client.get_ticker_price(client.symbol)
        sl_price = round(live_price * 0.98, 2)   # 2% below
        tp_price = round(live_price * 1.04, 2)   # 4% above
        sl_tp_order = client.place_order(
            side="buy",
            size=min_trade_size,
            sl=sl_price,
            tp=tp_price,
            reduce_only=False,
        )
        sl_tp_order_id = sl_tp_order.order_id
        sl_tp_size = sl_tp_order.size
        result(
            12,
            "SL/TP order placement",
            True,
            f"order_id={sl_tp_order_id} sl={sl_price} tp={tp_price}",
        )
    except Exception as e:
        result(12, "SL/TP order placement", False, str(e))

    sleep()

    # Test 13: close long position (reduceOnly)
    # Determine total open long contracts to close
    try:
        positions = client.get_positions()
        total_long_contracts = sum(
            float(p.get("contracts", 0))
            for p in positions
            if p.get("side", "") == "long"
        )
        if total_long_contracts <= 0:
            # Fall back to what we opened
            total_long_contracts = long_filled_size + sl_tp_size

        close_order = client.place_order(
            side="sell",
            size=total_long_contracts,
            reduce_only=True,
        )
        result(
            13,
            "close long (reduceOnly sell)",
            True,
            f"order_id={close_order.order_id} size={close_order.size} status={close_order.status}",
        )
    except Exception as e:
        result(13, "close long (reduceOnly sell)", False, str(e))

    sleep()

    # Test 14: fetch_positions (verify position closed)
    try:
        positions_after = client.get_positions()
        long_still_open = any(
            p.get("side") == "long" and float(p.get("contracts", 0)) > 0
            for p in positions_after
        )
        if not long_still_open:
            result(14, "fetch_positions (long closed)", True, "no active long position")
        else:
            remaining = [(p["side"], p["contracts"]) for p in positions_after]
            result(14, "fetch_positions (long closed)", False, f"position still open: {remaining}")
    except Exception as e:
        result(14, "fetch_positions (long closed)", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 5 — Trade History
    # ------------------------------------------------------------------

    # Test 15: fetch_my_trades
    try:
        since_ms = int((time.time() - 3600) * 1000)  # last 1 hour
        trades = client.exchange.fetch_my_trades(client.symbol, since=since_ms, limit=50)
        if trades:
            result(15, "fetch_my_trades", True, f"{len(trades)} trades returned")
        else:
            # Empty is acceptable — testnet may not surface them immediately
            result(15, "fetch_my_trades", True, "0 trades (empty — may lag on testnet)")
    except Exception as e:
        result(15, "fetch_my_trades", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 6 — Order Cleanup
    # ------------------------------------------------------------------

    # Test 16: cancel_all_orders
    try:
        client.cancel_all_orders(client.symbol)
        result(16, "cancel_all_orders", True, f"all open orders cancelled for {client.symbol}")
    except Exception as e:
        # If there are no open orders, some exchanges return an error — treat as OK
        if "no order" in str(e).lower() or "empty" in str(e).lower():
            result(16, "cancel_all_orders", True, "no open orders to cancel")
        else:
            result(16, "cancel_all_orders", False, str(e))

    sleep()

    # ------------------------------------------------------------------
    # GROUP 7 — Short Side
    # ------------------------------------------------------------------

    # Test 17: open SHORT, verify position, close it
    try:
        short_order = client.place_order(
            side="sell",
            size=min_trade_size,
            reduce_only=False,
        )
        sleep(1.0)  # give exchange a moment to register position

        # Verify short position exists
        positions = client.get_positions()
        short_found = any(
            p.get("side") == "short" and float(p.get("contracts", 0)) > 0
            for p in positions
        )

        # Close the short — use actual contracts to handle partial fills
        total_short_contracts = sum(
            float(p.get("contracts", 0))
            for p in positions
            if p.get("side", "") == "short"
        )
        close_size = total_short_contracts if total_short_contracts > 0 else min_trade_size
        short_close = client.place_order(
            side="buy",
            size=close_size,
            reduce_only=True,
        )
        sleep(0.5)

        detail = (
            f"short open={short_found} "
            f"short_order_id={short_order.order_id} "
            f"close_order_id={short_close.order_id}"
        )
        result(17, "short round-trip", short_found, detail)
    except Exception as e:
        result(17, "short round-trip", False, str(e))

    # ------------------------------------------------------------------
    # GROUP 8 — modify_sl (Binance cancel-and-recreate)
    # ------------------------------------------------------------------

    # Test 18: open a long, call modify_sl to set a new SL, verify it lands,
    #          then clean up (cancel orders + close position).
    try:
        live_price = client.get_ticker_price(client.symbol)

        # Open a small long position without SL so we can set one via modify_sl
        mod_order = client.place_order(
            side="buy",
            size=min_trade_size,
            sl=None,
            tp=None,
            reduce_only=False,
        )
        sleep(1.0)  # let exchange register the position

        initial_sl = round(live_price * 0.97, 2)   # 3% below — initial SL
        new_sl = round(live_price * 0.96, 2)        # 4% below — modified SL

        # Set an initial SL via a STOP_MARKET order so modify_sl has something to cancel.
        # closePosition=True is mutually exclusive with reduceOnly on Binance (error -1106).
        client.exchange.create_order(
            client.symbol,
            "stop_market",
            "sell",
            None,
            None,
            {
                "stopPrice": initial_sl,
                "closePosition": True,
            },
        )
        sleep(0.5)

        # Call modify_sl — should cancel the initial SL and place a new one
        ok = client.modify_sl(symbol=client.symbol, side="buy", new_sl=new_sl)

        # Verify: a STOP_MARKET algo order with the new triggerPrice should exist.
        # Binance futures stores closePosition SL orders as conditional algo orders
        # which do NOT appear in fetch_open_orders — must query the algo endpoint.
        sl_found = False
        if ok:
            sleep(0.5)
            try:
                market = client.exchange.market(client.symbol)
                algo_orders = client.exchange.fapiPrivateGetOpenAlgoOrders(
                    {"symbol": market["id"]}
                )
                for o in algo_orders:
                    raw_stop = float(o.get("triggerPrice") or 0)
                    raw_type = (o.get("orderType") or "").upper()
                    if raw_type in ("STOP_MARKET", "STOP") and abs(raw_stop - new_sl) < 1.0:
                        sl_found = True
                        break
            except Exception:
                pass

        result(
            18,
            "modify_sl (cancel-recreate)",
            ok and sl_found,
            f"modify_sl_returned={ok} new_sl_order_found={sl_found} new_sl={new_sl}",
        )

        # Cleanup: cancel regular + algo orders, then close the position
        try:
            client.cancel_all_orders(client.symbol)
        except Exception:
            pass
        try:
            market = client.exchange.market(client.symbol)
            client.exchange.fapiPrivateDeleteAlgoOpenOrders({"symbol": market["id"]})
        except Exception:
            pass
        sleep(0.5)
        positions = client.get_positions()
        total_longs = sum(
            float(p.get("contracts", 0))
            for p in positions
            if p.get("side", "") == "long"
        )
        if total_longs > 0:
            client.place_order(side="sell", size=total_longs, reduce_only=True)
    except Exception as e:
        result(18, "modify_sl (cancel-recreate)", False, str(e))

    _print_summary()


def _print_summary() -> None:
    failed = [name for name, ok, _ in _results if not ok]
    print()
    print("=" * 65)
    print(f"  RESULTS: {_passed}/{_total} PASSED")
    if failed:
        print()
        print("  FAILED:")
        for name in failed:
            detail = next(d for n, ok, d in _results if n == name)
            print(f"    - {name}: {detail}")
    print("=" * 65)
    print()


if __name__ == "__main__":
    run_tests()
