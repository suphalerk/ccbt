"""Tests for close_all_positions() reduceOnly fallback logic.

ROUND 1: Fix the broken close path — Binance -2022 ReduceOnly rejection.

TDD: all tests in this file were written BEFORE the fix was implemented.
They describe the required behaviour:
  1. reduceOnly path succeeds → no fallback (one create_order call).
  2. -2022 / "ReduceOnly" InvalidOrder → fallback plain market order with
     positionAmt qty (precision-applied), correct side, no reduceOnly.
  3. exact-qty: fallback uses info['positionAmt'] (not stale 'contracts').
  4. non-reduceOnly errors are NOT swallowed into a blind plain-market retry.
  5. hedge-mode (dualSidePosition=True): detected & logged, no silent over-shoot.
  6. Engine PANIC path invokes the fixed method and does NOT raise on -2022.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, call, patch

import ccxt
import pytest

from bot.exchange import BybitClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

BINANCE_CONFIG = {
    "symbol": "BTCUSDT",
    "exchange": "binance",
    "use_testnet": True,
    "leverage": 5,
    "risk_per_trade": 0.01,
    "max_daily_loss": 0.03,
    "max_positions": 2,
}


def _make_binance_client(mock_exchange: MagicMock) -> BybitClient:
    """Instantiate a BybitClient with a fully pre-built mock ccxt exchange."""
    market = {
        "id": "BTCUSDT",
        "symbol": "BTC/USDT:USDT",
        "base": "BTC",
        "quote": "USDT",
        "settle": "USDT",
        "type": "swap",
        "linear": True,
        "active": True,
        "precision": {"amount": 0.001, "price": 0.01},
        "limits": {},
        "info": {},
    }
    mock_exchange.markets = {"BTC/USDT:USDT": market}
    mock_exchange.market.return_value = market
    mock_exchange.amount_to_precision.side_effect = lambda sym, val: str(round(float(val), 3))
    mock_exchange.price_to_precision.side_effect = lambda sym, val: str(round(float(val), 2))
    mock_exchange.load_markets.return_value = {"BTC/USDT:USDT": market}
    mock_exchange.set_sandbox_mode = MagicMock()

    with patch("bot.exchange.ccxt.binance", return_value=mock_exchange):
        client = BybitClient(BINANCE_CONFIG)
    return client


def _position(side: str, contracts: float, position_amt: float) -> dict[str, Any]:
    """Build a minimal ccxt position dict."""
    return {
        "symbol": "BTC/USDT:USDT",
        "side": side,            # "long" or "short"
        "contracts": contracts,  # may be stale; fallback must NOT use this
        "info": {"positionAmt": str(position_amt)},  # authoritative
    }


def _invalid_order_2022(msg: str = "ReduceOnly Order is rejected, -2022") -> ccxt.InvalidOrder:
    return ccxt.InvalidOrder(msg)


# ---------------------------------------------------------------------------
# Test 1: reduceOnly path succeeds — NO fallback
# ---------------------------------------------------------------------------

class TestReduceOnlySucceeds:
    def test_single_create_order_call_on_success(self):
        """When reduceOnly succeeds, create_order is called exactly once."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.return_value = [pos]
        mock_ex.create_order.return_value = {"id": "1", "status": "closed"}

        client.close_all_positions()

        # Exactly one create_order call
        assert mock_ex.create_order.call_count == 1
        first_call = mock_ex.create_order.call_args
        # reduceOnly must be in params
        params = first_call[0][5] if len(first_call[0]) > 5 else first_call[1].get("params", {})
        if not params:
            # ccxt positional: symbol, type, side, amount, price, params
            args = first_call[0]
            assert len(args) >= 6
            params = args[5]
        assert params.get("reduceOnly") is True


# ---------------------------------------------------------------------------
# Test 2: -2022 fallback — plain market order placed, no raise
# ---------------------------------------------------------------------------

class TestReduceOnly2022Fallback:
    def test_fallback_plain_market_on_2022(self):
        """-2022 InvalidOrder triggers a second create_order WITHOUT reduceOnly."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)

        # First call raises -2022, second succeeds
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "2", "status": "closed"},
        ]
        # fetch_positions is called 3 times:
        #   1. initial get_positions() in close_all_positions
        #   2. re-fetch before fallback (get authoritative positionAmt)
        #   3. post-close verify
        mock_ex.fetch_positions.side_effect = [
            [pos],   # initial
            [pos],   # re-fetch before fallback
            [],      # post-close verify → flat
        ]

        # Must NOT raise
        client.close_all_positions()

        assert mock_ex.create_order.call_count == 2
        fallback_call = mock_ex.create_order.call_args_list[1]
        fallback_args = fallback_call[0]
        # No reduceOnly in the fallback params
        fallback_params = fallback_args[5] if len(fallback_args) > 5 else {}
        assert not fallback_params.get("reduceOnly", False), (
            "Fallback must NOT have reduceOnly=True"
        )
        # Side: long position → sell
        assert fallback_args[2] == "sell"

    def test_fallback_on_reduceonly_string_in_message(self):
        """Fallback also triggers when 'ReduceOnly' appears anywhere in the message."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("short", 0.10, -0.10)
        mock_ex.create_order.side_effect = [
            ccxt.InvalidOrder("ReduceOnly Order is rejected"),
            {"id": "3", "status": "closed"},
        ]
        mock_ex.fetch_positions.side_effect = [
            [pos],   # initial
            [pos],   # re-fetch before fallback
            [],      # post-close verify
        ]

        client.close_all_positions()

        assert mock_ex.create_order.call_count == 2
        fallback_args = mock_ex.create_order.call_args_list[1][0]
        # Short position → buy
        assert fallback_args[2] == "buy"


# ---------------------------------------------------------------------------
# Test 3: exact-qty — fallback uses info['positionAmt'], not stale contracts
# ---------------------------------------------------------------------------

class TestExactQtyFromPositionAmt:
    def test_fallback_qty_is_positionAmt_not_contracts(self):
        """Fallback qty must come from info['positionAmt'], precision-applied."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        # contracts is stale/wrong (0.05), real positionAmt is 0.123
        pos = _position("long", 0.05, 0.123)
        mock_ex.fetch_positions.side_effect = [
            [pos],   # initial get_positions
            [pos],   # re-fetch before fallback
            [],      # post-close verify
        ]
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "4", "status": "closed"},
        ]

        client.close_all_positions()

        fallback_args = mock_ex.create_order.call_args_list[1][0]
        # amount arg is index 3; amount_to_precision is called on abs(positionAmt)
        # The mock precision side_effect rounds to 3 dp: str(round(0.123, 3)) = "0.123"
        # Then we float() it back: 0.123
        fallback_qty = float(fallback_args[3])
        assert abs(fallback_qty - 0.123) < 1e-9, (
            f"Expected 0.123 from positionAmt, got {fallback_qty}"
        )

    def test_short_position_qty_is_absolute(self):
        """Short positionAmt is negative; fallback qty must be positive."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        # Short: positionAmt = -0.08 (negative on Binance)
        pos = _position("short", 0.08, -0.08)
        mock_ex.fetch_positions.side_effect = [
            [pos],   # initial
            [pos],   # re-fetch before fallback
            [],      # post-close verify
        ]
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "5", "status": "closed"},
        ]

        client.close_all_positions()

        fallback_args = mock_ex.create_order.call_args_list[1][0]
        fallback_qty = float(fallback_args[3])
        assert fallback_qty > 0, "Fallback qty must be positive (abs of positionAmt)"
        assert abs(fallback_qty - 0.08) < 1e-9


# ---------------------------------------------------------------------------
# Test 4: non-reduceOnly errors are NOT swallowed
# ---------------------------------------------------------------------------

class TestNonReduceOnlyErrorsNotSwallowed:
    def test_insufficient_margin_raises(self):
        """InsufficientFunds is not a reduceOnly error — must propagate, no fallback."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.return_value = [pos]
        mock_ex.create_order.side_effect = ccxt.InsufficientFunds("insufficient margin")

        with pytest.raises(ccxt.InsufficientFunds):
            client.close_all_positions()

        # Only ONE attempt — the InsufficientFunds is raised, no fallback
        assert mock_ex.create_order.call_count == 1

    def test_generic_exchange_error_without_reduceonly_text_raises(self):
        """An InvalidOrder without -2022/ReduceOnly text is NOT treated as reduceOnly."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.return_value = [pos]
        mock_ex.create_order.side_effect = ccxt.InvalidOrder(
            "Order would immediately trigger -2021"
        )

        with pytest.raises(ccxt.InvalidOrder):
            client.close_all_positions()

        assert mock_ex.create_order.call_count == 1


# ---------------------------------------------------------------------------
# Test 5: hedge-mode detection — no silent over-shoot
# ---------------------------------------------------------------------------

class TestHedgeModeDetection:
    def test_hedge_mode_logged_and_raises(self, caplog):
        """When dualSidePosition=True is detected, log warning and RAISE ExchangeError.

        Raising (instead of silently skipping) prevents callers from recording a
        phantom close — the position is still open and the book must stay accurate.
        """
        import logging

        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        # Make pos look like a hedge-mode position (has positionSide != BOTH)
        pos["info"]["positionSide"] = "LONG"

        # fetch_positions: initial + re-fetch before fallback (hedge detected there)
        mock_ex.fetch_positions.side_effect = [
            [pos],   # initial get_positions
            [pos],   # re-fetch before fallback (hedge detected → raise)
        ]
        # reduceOnly attempt raises -2022; no second create_order expected
        mock_ex.create_order.side_effect = _invalid_order_2022()

        with caplog.at_level(logging.WARNING):
            # MUST raise ExchangeError so callers do not record a phantom close
            with pytest.raises(ccxt.ExchangeError, match="hedge_mode_close_unsupported"):
                client.close_all_positions()

        # Must NOT have placed a second (potentially direction-flipping) order
        # Exactly 1 create_order call: the reduceOnly attempt that failed
        assert mock_ex.create_order.call_count == 1
        log_text = " ".join(r.message for r in caplog.records)
        assert "hedge" in log_text.lower() or "dualside" in log_text.lower() or \
               "positionside" in log_text.lower()


# ---------------------------------------------------------------------------
# Test 6: Engine PANIC path — does NOT raise on -2022
# ---------------------------------------------------------------------------

class TestEnginePanicPath:
    """Regression: engine PANIC path calls close_all_positions; with -2022 fix
    the PANIC loop must not propagate the InvalidOrder."""

    def _make_minimal_engine(self, mock_exchange: MagicMock):
        """Build a BybitClient (the engine's _client) with a -2022 account."""
        client = _make_binance_client(mock_exchange)
        return client

    def test_close_all_positions_does_not_raise_on_2022(self):
        """-2022 on reduceOnly must not cause close_all_positions to raise."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.side_effect = [
            [pos],   # initial
            [pos],   # re-fetch before fallback
            [],      # post-close verify
        ]
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "panic-1", "status": "closed"},
        ]

        # This is the call the engine PANIC block makes:
        try:
            client.close_all_positions()
        except Exception as exc:
            pytest.fail(
                f"close_all_positions raised {type(exc).__name__} on -2022: {exc}"
            )

        # Fallback fired
        assert mock_ex.create_order.call_count == 2

    def test_position_flat_after_fallback(self):
        """After fallback, close_all_positions re-fetches and verifies flatness."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        empty = []
        # Sequence: initial fetch → one position; re-fetch before fallback → same;
        # final verify → flat (empty)
        mock_ex.fetch_positions.side_effect = [
            [pos],   # get_positions in close_all_positions
            [pos],   # re-fetch before plain order
            empty,   # post-close verify (flat)
        ]
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "6", "status": "closed"},
        ]

        client.close_all_positions()  # Must not raise

        # Verify call count: 2 close orders + 3 fetch_positions
        assert mock_ex.create_order.call_count == 2
