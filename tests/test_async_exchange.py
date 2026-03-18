"""Tests for async_exchange module — AsyncBybitClient and TradingPort."""

import asyncio
from typing import Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from bot.async_exchange import AsyncBybitClient, TradingPort
from bot.exchange import OrderResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config() -> dict:
    return {
        "symbol": "BTCUSDT",
        "use_testnet": True,
        "leverage": 3,
        "risk_per_trade": 0.01,
        "max_daily_loss": 0.03,
        "max_positions": 2,
    }


def _make_order_result() -> OrderResult:
    return OrderResult(
        order_id="ord1",
        symbol="BTC/USDT:USDT",
        side="buy",
        size=0.001,
        price=60000.0,
        sl=59100.0,
        tp=61800.0,
        status="filled",
        raw={},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_sync():
    """Patch BybitClient.__init__ so no real ccxt calls are made."""
    with patch("bot.async_exchange.BybitClient") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def async_client(mock_sync) -> AsyncBybitClient:
    return AsyncBybitClient(_make_config())


# ---------------------------------------------------------------------------
# TradingPort structural checks
# ---------------------------------------------------------------------------

class TestTradingPort:
    """Verify TradingPort is a runtime-checkable Protocol."""

    def test_async_client_satisfies_protocol(self, async_client):
        """AsyncBybitClient must satisfy TradingPort at runtime."""
        assert isinstance(async_client, TradingPort)

    def test_incomplete_class_fails_check(self):
        """An object lacking required methods must not satisfy TradingPort."""
        class Incomplete:
            async def get_balance(self) -> float:
                return 0.0
            # Missing all other methods

        assert not isinstance(Incomplete(), TradingPort)

    def test_complete_class_satisfies_protocol(self):
        """A fully-implemented mock satisfies TradingPort (testability goal)."""

        class MockExchange:
            async def get_balance(self) -> float: return 1000.0
            async def get_positions(self) -> list: return []
            async def get_ohlcv(self, symbol, timeframe, limit=100): return pd.DataFrame()
            async def place_order(self, side, size, sl=None, tp=None): ...
            async def modify_sl(self, symbol=None, side="buy", new_sl=0.0) -> bool: return True
            async def set_leverage(self, leverage, symbol=None): ...
            async def get_ticker_price(self, symbol=None) -> float: return 0.0
            async def cancel_all_orders(self, symbol=None): ...
            async def close_all_positions(self, symbol=None): ...
            async def get_closed_pnl(self, symbol=None, since_ms=None) -> list: return []
            async def get_funding_rate(self, symbol=None) -> float: return 0.0

        assert isinstance(MockExchange(), TradingPort)


# ---------------------------------------------------------------------------
# AsyncBybitClient — initialisation
# ---------------------------------------------------------------------------

class TestAsyncBybitClientInit:
    """Initialisation delegates to the underlying BybitClient."""

    def test_sync_client_property(self, async_client, mock_sync):
        """sync_client property returns the underlying BybitClient instance."""
        assert async_client.sync_client is mock_sync


# ---------------------------------------------------------------------------
# AsyncBybitClient — async method delegation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAsyncDelegation:
    """Each async method must call the corresponding sync method."""

    async def test_get_balance(self, async_client, mock_sync):
        mock_sync.get_balance.return_value = 500.0
        result = await async_client.get_balance()
        assert result == 500.0
        mock_sync.get_balance.assert_called_once()

    async def test_get_positions(self, async_client, mock_sync):
        positions = [{"symbol": "BTC/USDT:USDT", "contracts": 0.001}]
        mock_sync.get_positions.return_value = positions
        result = await async_client.get_positions()
        assert result == positions
        mock_sync.get_positions.assert_called_once()

    async def test_get_ohlcv(self, async_client, mock_sync):
        df = pd.DataFrame({"close": [60000.0]})
        mock_sync.get_ohlcv.return_value = df
        result = await async_client.get_ohlcv("BTC/USDT:USDT", "15m", limit=50)
        assert result is df
        mock_sync.get_ohlcv.assert_called_once_with("BTC/USDT:USDT", "15m", 50)

    async def test_get_ticker_price(self, async_client, mock_sync):
        mock_sync.get_ticker_price.return_value = 65000.0
        result = await async_client.get_ticker_price()
        assert result == 65000.0
        mock_sync.get_ticker_price.assert_called_once_with(None)

    async def test_get_ticker_price_with_symbol(self, async_client, mock_sync):
        mock_sync.get_ticker_price.return_value = 65000.0
        await async_client.get_ticker_price("BTC/USDT:USDT")
        mock_sync.get_ticker_price.assert_called_once_with("BTC/USDT:USDT")

    async def test_get_funding_rate(self, async_client, mock_sync):
        mock_sync.get_funding_rate.return_value = 0.0001
        result = await async_client.get_funding_rate()
        assert result == pytest.approx(0.0001)
        mock_sync.get_funding_rate.assert_called_once_with(None)

    async def test_get_funding_rate_with_symbol(self, async_client, mock_sync):
        mock_sync.get_funding_rate.return_value = -0.0002
        result = await async_client.get_funding_rate("ETH/USDT:USDT")
        assert result == pytest.approx(-0.0002)
        mock_sync.get_funding_rate.assert_called_once_with("ETH/USDT:USDT")

    async def test_get_closed_pnl_defaults(self, async_client, mock_sync):
        mock_sync.get_closed_pnl.return_value = []
        result = await async_client.get_closed_pnl()
        assert result == []
        mock_sync.get_closed_pnl.assert_called_once_with(None, None)

    async def test_get_closed_pnl_with_args(self, async_client, mock_sync):
        pnl = [{"id": "t1", "pnl": 12.5}]
        mock_sync.get_closed_pnl.return_value = pnl
        result = await async_client.get_closed_pnl("BTC/USDT:USDT", since_ms=1700000000000)
        assert result == pnl
        mock_sync.get_closed_pnl.assert_called_once_with("BTC/USDT:USDT", 1700000000000)

    async def test_place_order_no_sltp(self, async_client, mock_sync):
        order = _make_order_result()
        mock_sync.place_order.return_value = order
        result = await async_client.place_order("buy", 0.001)
        assert result is order
        mock_sync.place_order.assert_called_once_with("buy", 0.001, None, None)

    async def test_place_order_with_sltp(self, async_client, mock_sync):
        order = _make_order_result()
        mock_sync.place_order.return_value = order
        result = await async_client.place_order("buy", 0.001, sl=59100.0, tp=61800.0)
        assert result is order
        mock_sync.place_order.assert_called_once_with("buy", 0.001, 59100.0, 61800.0)

    async def test_modify_sl_defaults(self, async_client, mock_sync):
        mock_sync.modify_sl.return_value = True
        result = await async_client.modify_sl()
        assert result is True
        mock_sync.modify_sl.assert_called_once_with(None, "buy", 0.0)

    async def test_modify_sl_with_args(self, async_client, mock_sync):
        mock_sync.modify_sl.return_value = True
        await async_client.modify_sl(symbol="BTC/USDT:USDT", side="sell", new_sl=70500.0)
        mock_sync.modify_sl.assert_called_once_with("BTC/USDT:USDT", "sell", 70500.0)

    async def test_cancel_all_orders_default(self, async_client, mock_sync):
        await async_client.cancel_all_orders()
        mock_sync.cancel_all_orders.assert_called_once_with(None)

    async def test_cancel_all_orders_with_symbol(self, async_client, mock_sync):
        await async_client.cancel_all_orders("BTC/USDT:USDT")
        mock_sync.cancel_all_orders.assert_called_once_with("BTC/USDT:USDT")

    async def test_close_all_positions_default(self, async_client, mock_sync):
        await async_client.close_all_positions()
        mock_sync.close_all_positions.assert_called_once_with(None)

    async def test_close_all_positions_with_symbol(self, async_client, mock_sync):
        await async_client.close_all_positions("BTC/USDT:USDT")
        mock_sync.close_all_positions.assert_called_once_with("BTC/USDT:USDT")

    async def test_set_leverage_default_symbol(self, async_client, mock_sync):
        await async_client.set_leverage(5)
        mock_sync.set_leverage.assert_called_once_with(5, None)

    async def test_set_leverage_with_symbol(self, async_client, mock_sync):
        await async_client.set_leverage(3, "BTC/USDT:USDT")
        mock_sync.set_leverage.assert_called_once_with(3, "BTC/USDT:USDT")

    async def test_exception_propagates(self, async_client, mock_sync):
        """Exceptions from the sync layer must propagate to the caller."""
        mock_sync.get_balance.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError, match="API down"):
            await async_client.get_balance()
