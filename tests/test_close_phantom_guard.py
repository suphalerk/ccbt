"""Tests for close_all_positions() phantom-close hardening.

ROUND 1 — hardening: ensure callers CANNOT record phantom closes.

Two branches previously returned normally even when position was still open:
  - hedge-skip branch: positionSide != BOTH → logged warning, continued
  - not-flat branch: post-close re-fetch still shows position open

Both must now RAISE so callers' try/except blocks catch the error and do NOT
proceed to log_trade_close / register_close — keeping the book accurate.

Tests follow TDD order (written before the fix):
  1. not-flat after fallback → raises RuntimeError (position_not_flat_after_close)
  2. hedge-skip → raises ccxt.ExchangeError (hedge_mode_close_unsupported)
  3. happy path (reduceOnly success, flat after -2022 fallback) → no raise
  4. caller regression: PANIC with still-open mock does NOT call log_trade_close
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import ccxt
import pytest

from bot.exchange import BybitClient


# ---------------------------------------------------------------------------
# Shared helpers (mirror test_close_all_positions_fallback.py helpers)
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


def _position(
    side: str,
    contracts: float,
    position_amt: float,
    position_side: str = "BOTH",
) -> dict[str, Any]:
    """Build a minimal ccxt position dict."""
    return {
        "symbol": "BTC/USDT:USDT",
        "side": side,
        "contracts": contracts,
        "info": {
            "positionAmt": str(position_amt),
            "positionSide": position_side,
        },
    }


def _invalid_order_2022() -> ccxt.InvalidOrder:
    return ccxt.InvalidOrder("ReduceOnly Order is rejected, -2022")


# ---------------------------------------------------------------------------
# Test 1 — not-flat: raises after fallback still shows position open
# ---------------------------------------------------------------------------


class TestNotFlatRaises:
    """After plain-market fallback, if position is still open → raise."""

    def test_raises_when_still_open_after_fallback(self):
        """RuntimeError raised when post-close re-fetch shows position still open."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.side_effect = [
            [pos],  # initial get_positions
            [pos],  # re-fetch before fallback (get authoritative positionAmt)
            [pos],  # post-close verify → STILL OPEN
        ]
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "fallback-1", "status": "closed"},
        ]

        with pytest.raises(RuntimeError, match="position_not_flat_after_close"):
            client.close_all_positions()

    def test_error_logged_before_raise(self, caplog):
        """The 'position_not_flat_after_close' event must be logged before the raise."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.side_effect = [
            [pos],
            [pos],
            [pos],  # still open
        ]
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "fallback-2", "status": "closed"},
        ]

        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError):
                client.close_all_positions()

        log_text = " ".join(r.getMessage() for r in caplog.records)
        assert "not_flat" in log_text or "plain_market_fallback" in log_text

    def test_fallback_order_was_placed_before_raise(self):
        """Fallback order IS placed — the raise happens at verification, not before."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.side_effect = [
            [pos],
            [pos],
            [pos],  # still open → triggers raise
        ]
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "fallback-3", "status": "closed"},
        ]

        with pytest.raises(RuntimeError):
            client.close_all_positions()

        # Both orders were placed: reduceOnly attempt + fallback
        assert mock_ex.create_order.call_count == 2


# ---------------------------------------------------------------------------
# Test 2 — hedge-skip: raises instead of silently continuing
# ---------------------------------------------------------------------------


class TestHedgeSkipRaises:
    """When positionSide != BOTH is detected, raise ExchangeError (not silently skip)."""

    def test_raises_on_hedge_mode_position(self):
        """ExchangeError raised when hedge-mode positionSide detected."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05, position_side="LONG")
        mock_ex.fetch_positions.side_effect = [
            [pos],  # initial
            [pos],  # re-fetch before fallback (hedge detected here)
        ]
        mock_ex.create_order.side_effect = _invalid_order_2022()

        with pytest.raises(ccxt.ExchangeError, match="hedge_mode_close_unsupported"):
            client.close_all_positions()

    def test_hedge_mode_error_logged(self, caplog):
        """Hedge-mode error must be logged before the raise."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("short", 0.08, -0.08, position_side="SHORT")
        mock_ex.fetch_positions.side_effect = [
            [pos],
            [pos],
        ]
        mock_ex.create_order.side_effect = _invalid_order_2022()

        with caplog.at_level(logging.WARNING):
            with pytest.raises(ccxt.ExchangeError):
                client.close_all_positions()

        log_text = " ".join(r.getMessage() for r in caplog.records)
        assert (
            "hedge" in log_text.lower()
            or "dualside" in log_text.lower()
            or "positionside" in log_text.lower()
        )

    def test_no_ambiguous_order_placed_on_hedge(self):
        """No second create_order must be placed after hedge detection."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05, position_side="LONG")
        mock_ex.fetch_positions.side_effect = [
            [pos],
            [pos],
        ]
        mock_ex.create_order.side_effect = _invalid_order_2022()

        with pytest.raises(ccxt.ExchangeError):
            client.close_all_positions()

        # Only 1 create_order: the reduceOnly attempt; no direction-flipping plain order
        assert mock_ex.create_order.call_count == 1


# ---------------------------------------------------------------------------
# Test 3 — happy paths still do NOT raise
# ---------------------------------------------------------------------------


class TestHappyPathsUnchanged:
    """Successful close paths must not be disturbed by the hardening."""

    def test_reduceonly_success_no_raise(self):
        """reduceOnly success path returns normally, no raise."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.return_value = [pos]
        mock_ex.create_order.return_value = {"id": "1", "status": "closed"}

        client.close_all_positions()  # must not raise

        assert mock_ex.create_order.call_count == 1

    def test_2022_fallback_then_flat_no_raise(self):
        """-2022 fallback succeeds and position is flat → no raise."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.side_effect = [
            [pos],  # initial
            [pos],  # re-fetch before fallback
            [],     # post-close verify → flat
        ]
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "2", "status": "closed"},
        ]

        client.close_all_positions()  # must not raise

        assert mock_ex.create_order.call_count == 2


# ---------------------------------------------------------------------------
# Test 4 — caller regression: PANIC with still-open must not record phantom close
# ---------------------------------------------------------------------------


class TestCallerPanicRegression:
    """Engine PANIC with a mocked still-open close does NOT call log_trade_close."""

    def test_panic_with_not_flat_does_not_log_trade_close(self):
        """When close_all_positions raises (not-flat), the PANIC catch block fires.
        log_trade_close must NOT be called — no phantom close recorded."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05)
        mock_ex.fetch_positions.side_effect = [
            [pos],  # initial
            [pos],  # re-fetch before fallback
            [pos],  # post-close verify → still open → raises
        ]
        mock_ex.create_order.side_effect = [
            _invalid_order_2022(),
            {"id": "panic-fallback", "status": "closed"},
        ]

        mock_journal = MagicMock()
        mock_risk = MagicMock()

        # Simulate the engine PANIC try/except block:
        tracked_trades = {
            "trade-1": {
                "side": "buy",
                "entry_price": 50000.0,
                "size": 100.0,
                "open_time": 1700000000,
            }
        }

        try:
            client.close_all_positions()
            # This block only runs if close_all_positions returns normally:
            for trade_id, info in tracked_trades.items():
                mock_journal.log_trade_close(
                    trade_id=trade_id,
                    exit_price=51000.0,
                    pnl=100.0,
                    pnl_pct=0.01,
                    close_reason="panic_mode",
                    duration_seconds=60,
                )
                mock_risk.record_trade_result(100.0)
        except Exception:
            pass  # engine catches and logs panic_close_failed

        # Key assertion: no phantom close was recorded
        mock_journal.log_trade_close.assert_not_called()
        mock_risk.record_trade_result.assert_not_called()

    def test_panic_with_hedge_mode_does_not_log_trade_close(self):
        """When close_all_positions raises (hedge-mode), phantom close is prevented."""
        mock_ex = MagicMock()
        client = _make_binance_client(mock_ex)

        pos = _position("long", 0.05, 0.05, position_side="LONG")
        mock_ex.fetch_positions.side_effect = [
            [pos],
            [pos],
        ]
        mock_ex.create_order.side_effect = _invalid_order_2022()

        mock_journal = MagicMock()

        tracked_trades = {
            "trade-hedge": {
                "side": "buy",
                "entry_price": 50000.0,
                "size": 100.0,
                "open_time": 1700000000,
            }
        }

        try:
            client.close_all_positions()
            for trade_id, info in tracked_trades.items():
                mock_journal.log_trade_close(
                    trade_id=trade_id,
                    exit_price=51000.0,
                    pnl=100.0,
                    pnl_pct=0.01,
                    close_reason="panic_mode",
                    duration_seconds=60,
                )
        except Exception:
            pass

        mock_journal.log_trade_close.assert_not_called()
