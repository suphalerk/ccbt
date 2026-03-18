"""Tests for exchange module using mock exchange."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from bot.exchange import BybitClient, OrderResult


@pytest.fixture
def config():
    """Test configuration for testnet mode."""
    return {
        "symbol": "BTCUSDT",
        "use_testnet": True,
        "leverage": 5,
        "risk_per_trade": 0.005,
        "max_daily_loss": 0.02,
        "max_positions": 2,
    }


@pytest.fixture
def mock_ccxt():
    """Mock ccxt.bybit exchange."""
    with patch("bot.exchange.ccxt.bybit") as MockBybit:
        mock_exchange = MagicMock()
        MockBybit.return_value = mock_exchange
        mock_exchange.load_markets.return_value = {}
        mock_exchange.set_sandbox_mode = MagicMock()
        # Precision methods return the value as-is for testing
        mock_exchange.amount_to_precision.side_effect = lambda sym, val: val
        mock_exchange.price_to_precision.side_effect = lambda sym, val: val
        yield mock_exchange


@pytest.fixture
def client(config, mock_ccxt):
    """Create a BybitClient with mocked exchange."""
    return BybitClient(config)


class TestBybitClientInit:
    """Test client initialization."""

    def test_testnet_mode(self, client, mock_ccxt):
        """Client should enable sandbox mode for testnet."""
        mock_ccxt.set_sandbox_mode.assert_called_once_with(True)

    def test_markets_loaded(self, client, mock_ccxt):
        """Client should load markets on init."""
        mock_ccxt.load_markets.assert_called_once()


class TestGetBalance:
    """Test balance fetching."""

    def test_get_balance(self, client, mock_ccxt):
        """Should return USDT free balance."""
        mock_ccxt.fetch_balance.return_value = {
            "USDT": {"total": 1000.0, "free": 950.0}
        }
        balance = client.get_balance()
        assert balance == 950.0

    def test_get_balance_empty(self, client, mock_ccxt):
        """Should return 0 for missing USDT balance."""
        mock_ccxt.fetch_balance.return_value = {}
        balance = client.get_balance()
        assert balance == 0.0


class TestGetOHLCV:
    """Test OHLCV data fetching."""

    def test_get_ohlcv_returns_dataframe(self, client, mock_ccxt):
        """Should return a DataFrame with proper columns."""
        mock_ccxt.fetch_ohlcv.return_value = [
            [1700000000000, 60000, 60100, 59900, 60050, 100],
            [1700000900000, 60050, 60200, 60000, 60150, 120],
        ]
        df = client.get_ohlcv("BTCUSDT", "15m", limit=2)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) == 2

    def test_get_ohlcv_timestamp_index(self, client, mock_ccxt):
        """Should have datetime index."""
        mock_ccxt.fetch_ohlcv.return_value = [
            [1700000000000, 60000, 60100, 59900, 60050, 100],
        ]
        df = client.get_ohlcv("BTCUSDT", "15m", limit=1)
        assert isinstance(df.index, pd.DatetimeIndex)


class TestPlaceOrder:
    """Test order placement."""

    def test_place_market_order(self, client, mock_ccxt):
        """Should place a market order and return OrderResult."""
        mock_ccxt.create_order.return_value = {
            "id": "order123",
            "symbol": "BTCUSDT",
            "side": "buy",
            "amount": 0.001,
            "average": 60000.0,
            "price": 60000.0,
            "status": "filled",
        }
        result = client.place_order("buy", 0.001, sl=59400.0, tp=61200.0)
        assert isinstance(result, OrderResult)
        assert result.order_id == "order123"
        assert result.side == "buy"
        # SL/TP go through _safe_precision, values should be preserved
        assert result.sl == pytest.approx(59400.0)
        assert result.tp == pytest.approx(61200.0)

    def test_place_order_with_params(self, client, mock_ccxt):
        """Should include SL/TP in order params."""
        mock_ccxt.create_order.return_value = {
            "id": "order456",
            "symbol": "BTCUSDT",
            "side": "sell",
            "amount": 0.002,
            "average": 60000.0,
            "price": None,
            "status": "filled",
        }
        client.place_order("sell", 0.002, sl=60600.0, tp=58800.0)
        call_args = mock_ccxt.create_order.call_args
        params = call_args[0][5] if len(call_args[0]) > 5 else call_args[1].get("params", {})
        assert "stopLoss" in params
        assert "takeProfit" in params


class TestPositions:
    """Test position management."""

    def test_get_positions(self, client, mock_ccxt):
        """Should return active positions only."""
        mock_ccxt.fetch_positions.return_value = [
            {"symbol": "BTCUSDT", "contracts": 0.001, "side": "long"},
            {"symbol": "BTCUSDT", "contracts": 0, "side": "long"},
        ]
        positions = client.get_positions()
        assert len(positions) == 1

    def test_cancel_all_orders(self, client, mock_ccxt):
        """Should call cancel_all_orders on exchange."""
        client.cancel_all_orders()
        mock_ccxt.cancel_all_orders.assert_called_once_with("BTCUSDT")

    def test_close_all_positions(self, client, mock_ccxt):
        """Should close all open positions with reduce-only orders."""
        mock_ccxt.fetch_positions.return_value = [
            {"symbol": "BTCUSDT", "contracts": 0.001, "side": "long"},
        ]
        client.close_all_positions()
        mock_ccxt.create_order.assert_called_once()
        call_args = mock_ccxt.create_order.call_args
        assert call_args[0][2] == "sell"  # Close long = sell


class TestRetryLogic:
    """Test retry mechanism."""

    def test_retry_on_network_error(self, client, mock_ccxt):
        """Should retry on network errors."""
        import ccxt as ccxt_module

        mock_ccxt.fetch_balance.side_effect = [
            ccxt_module.NetworkError("timeout"),
            {"USDT": {"total": 1000.0}},
        ]
        balance = client.get_balance()
        assert balance == 1000.0
        assert mock_ccxt.fetch_balance.call_count == 2

    def test_no_retry_on_exchange_error(self, client, mock_ccxt):
        """Should not retry on exchange errors (e.g., insufficient balance)."""
        import ccxt as ccxt_module

        mock_ccxt.fetch_balance.side_effect = ccxt_module.ExchangeError("Insufficient")
        with pytest.raises(ccxt_module.ExchangeError):
            client.get_balance()
        assert mock_ccxt.fetch_balance.call_count == 1


class TestFundingRate:
    """Test funding rate fetching."""

    def test_get_funding_rate(self, client, mock_ccxt):
        """Should return current funding rate."""
        mock_ccxt.fetch_funding_rate.return_value = {
            "fundingRate": 0.0001,
        }
        rate = client.get_funding_rate()
        assert rate == pytest.approx(0.0001)
