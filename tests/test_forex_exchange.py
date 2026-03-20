"""Unit tests for OandaClient (bot/forex_exchange.py).

All OANDA API calls are mocked — no real network connection is made.
Tests verify:
1. Symbol normalisation
2. Timeframe mapping
3. Return types and shapes for all public methods
4. Error handling (V20Error, missing credentials)
5. Interface parity with BybitClient (duck-typing contract)
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import fields


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def set_env_vars(monkeypatch):
    """Ensure OANDA env vars are always set for tests."""
    monkeypatch.setenv("OANDA_API_TOKEN", "test-token-abc123")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "001-001-12345678-001")
    monkeypatch.setenv("OANDA_PRACTICE", "true")


@pytest.fixture
def base_config():
    return {
        "exchange": "oanda",
        "symbol": "XAU_USD",
        "timeframe_signal": "H1",
        "timeframe_trend": "H4",
        "leverage": 20,
        "risk_per_trade": 0.05,
        "max_daily_loss": 0.15,
        "use_testnet": True,
    }


def _make_client(config=None):
    """Build OandaClient with a mocked oandapyV20.API."""
    from bot.forex_exchange import OandaClient

    cfg = config or {
        "exchange": "oanda",
        "symbol": "XAU_USD",
        "use_testnet": True,
        "leverage": 20,
    }
    with patch("bot.forex_exchange.oandapyV20.API"):
        client = OandaClient(cfg)
    client._api = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Symbol normalisation
# ---------------------------------------------------------------------------

class TestNormalizeSymbol:
    def test_underscore_passthrough(self):
        from bot.forex_exchange import OandaClient
        assert OandaClient._normalize_symbol("XAU_USD") == "XAU_USD"

    def test_slash_format(self):
        from bot.forex_exchange import OandaClient
        assert OandaClient._normalize_symbol("XAU/USD") == "XAU_USD"

    def test_concatenated_format(self):
        from bot.forex_exchange import OandaClient
        assert OandaClient._normalize_symbol("XAUUSD") == "XAU_USD"

    def test_lowercase(self):
        from bot.forex_exchange import OandaClient
        assert OandaClient._normalize_symbol("xau_usd") == "XAU_USD"

    def test_eurusd(self):
        from bot.forex_exchange import OandaClient
        assert OandaClient._normalize_symbol("EURUSD") == "EUR_USD"


# ---------------------------------------------------------------------------
# Timeframe mapping
# ---------------------------------------------------------------------------

class TestMapTimeframe:
    def test_standard_mappings(self):
        from bot.forex_exchange import OandaClient
        assert OandaClient._map_timeframe("15m") == "M15"
        assert OandaClient._map_timeframe("1h") == "H1"
        assert OandaClient._map_timeframe("4h") == "H4"
        assert OandaClient._map_timeframe("1d") == "D"

    def test_oanda_passthrough(self):
        from bot.forex_exchange import OandaClient
        assert OandaClient._map_timeframe("H1") == "H1"
        assert OandaClient._map_timeframe("M15") == "M15"

    def test_unsupported_raises(self):
        from bot.forex_exchange import OandaClient
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            OandaClient._map_timeframe("2w")


# ---------------------------------------------------------------------------
# Credential validation
# ---------------------------------------------------------------------------

class TestCredentialValidation:
    def test_missing_token_raises(self, monkeypatch):
        from bot.forex_exchange import OandaClient
        monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
        with patch("bot.forex_exchange.oandapyV20.API"):
            with pytest.raises(ValueError, match="OANDA_API_TOKEN"):
                OandaClient({"symbol": "XAU_USD", "use_testnet": True})

    def test_missing_account_id_raises(self, monkeypatch):
        from bot.forex_exchange import OandaClient
        monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
        with patch("bot.forex_exchange.oandapyV20.API"):
            with pytest.raises(ValueError, match="OANDA_ACCOUNT_ID"):
                OandaClient({"symbol": "XAU_USD", "use_testnet": True})


# ---------------------------------------------------------------------------
# get_balance
# ---------------------------------------------------------------------------

class TestGetBalance:
    def test_returns_nav_as_float(self):
        client = _make_client()
        client._api.request.return_value = {
            "account": {"NAV": "12345.67", "balance": "12000.00"}
        }
        balance = client.get_balance()
        assert isinstance(balance, float)
        assert abs(balance - 12345.67) < 1e-6

    def test_integer_nav(self):
        client = _make_client()
        client._api.request.return_value = {"account": {"NAV": 5000}}
        assert client.get_balance() == 5000.0


# ---------------------------------------------------------------------------
# get_ohlcv
# ---------------------------------------------------------------------------

class TestGetOhlcv:
    def _make_candle(self, ts, o, h, l, c, volume=100, complete=True):
        return {
            "time": ts,
            "mid": {"o": str(o), "h": str(h), "l": str(l), "c": str(c)},
            "volume": volume,
            "complete": complete,
        }

    def test_returns_dataframe_with_correct_columns(self):
        import pandas as pd
        client = _make_client()
        candles = [
            self._make_candle("2024-01-01T10:00:00.000000000Z", 1900, 1910, 1895, 1905),
            self._make_candle("2024-01-01T11:00:00.000000000Z", 1905, 1915, 1900, 1910),
        ]
        client._api.request.return_value = {"candles": candles}
        df = client.get_ohlcv("XAU_USD", "H1", limit=2)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) == 2
        assert isinstance(df.index, pd.DatetimeIndex)

    def test_forming_candle_is_dropped(self):
        client = _make_client()
        candles = [
            self._make_candle("2024-01-01T10:00:00.000000000Z", 1900, 1910, 1895, 1905, complete=True),
            self._make_candle("2024-01-01T11:00:00.000000000Z", 1905, 1920, 1900, 1915, complete=False),
        ]
        client._api.request.return_value = {"candles": candles}
        df = client.get_ohlcv("XAU_USD", "H1", limit=10)
        assert len(df) == 1  # Forming candle dropped

    def test_limit_applied(self):
        client = _make_client()
        candles = [
            self._make_candle(f"2024-01-01T{i:02d}:00:00.000000000Z", 1900, 1910, 1895, 1905)
            for i in range(10)
        ]
        client._api.request.return_value = {"candles": candles}
        df = client.get_ohlcv("XAU_USD", "H1", limit=5)
        assert len(df) <= 5

    def test_empty_response_returns_empty_df(self):
        client = _make_client()
        client._api.request.return_value = {"candles": []}
        df = client.get_ohlcv("XAU_USD", "H1", limit=10)
        assert df.empty


# ---------------------------------------------------------------------------
# get_ticker_price
# ---------------------------------------------------------------------------

class TestGetTickerPrice:
    def test_returns_float(self):
        client = _make_client()
        client._api.request.return_value = {
            "candles": [
                {
                    "time": "2024-01-01T10:00:00.000000000Z",
                    "mid": {"o": "1900", "h": "1910", "l": "1895", "c": "1905.5"},
                    "complete": True,
                }
            ]
        }
        price = client.get_ticker_price()
        assert isinstance(price, float)
        assert abs(price - 1905.5) < 1e-4

    def test_empty_candles_returns_zero(self):
        client = _make_client()
        client._api.request.return_value = {"candles": []}
        price = client.get_ticker_price()
        assert price == 0.0


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------

class TestPlaceOrder:
    def _fill_response(self, order_id="1234", price="1905.50", units="10"):
        return {
            "orderFillTransaction": {
                "orderID": order_id,
                "price": price,
                "units": units,
                "type": "ORDER_FILL",
            },
            "relatedTransactionIDs": [order_id],
        }

    def test_buy_order_returns_order_result(self):
        from bot.forex_exchange import OrderResult
        client = _make_client()
        client._api.request.return_value = self._fill_response()
        result = client.place_order("buy", 10.0, sl=1890.0, tp=1930.0)
        assert isinstance(result, OrderResult)
        assert result.side == "buy"
        assert result.symbol == "XAU_USD"
        assert result.sl == 1890.0
        assert result.tp == 1930.0

    def test_sell_order_result(self):
        from bot.forex_exchange import OrderResult
        client = _make_client()
        client._api.request.return_value = self._fill_response(units="-5")
        result = client.place_order("sell", 5.0)
        assert isinstance(result, OrderResult)
        assert result.side == "sell"

    def test_order_body_sent_to_api(self):
        import oandapyV20.endpoints.orders as v20_orders
        client = _make_client()
        client._api.request.return_value = self._fill_response()
        client.place_order("buy", 10.0, sl=1890.0, tp=1930.0)
        call_args = client._api.request.call_args
        endpoint = call_args[0][0]
        assert isinstance(endpoint, v20_orders.OrderCreate)

    def test_reduce_only_sets_position_fill(self):
        client = _make_client()
        client._api.request.return_value = self._fill_response()
        # Should not raise
        result = client.place_order("sell", 5.0, reduce_only=True)
        call_args = client._api.request.call_args[0][0]
        body = call_args.data
        assert body["order"]["positionFill"] == "REDUCE_ONLY"


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------

class TestGetPositions:
    def _position_response(self, long_units="10", short_units="0", avg_price="1905.00"):
        return {
            "position": {
                "instrument": "XAU_USD",
                "long": {
                    "units": long_units,
                    "averagePrice": avg_price,
                    "unrealizedPL": "50.00",
                },
                "short": {
                    "units": short_units,
                    "averagePrice": "0",
                    "unrealizedPL": "0",
                },
            }
        }

    def test_long_position_returned(self):
        client = _make_client()
        client._api.request.return_value = self._position_response(long_units="10")
        positions = client.get_positions()
        assert len(positions) == 1
        assert positions[0]["side"] == "long"
        assert positions[0]["contracts"] == 10.0

    def test_no_position_returns_empty(self):
        from oandapyV20.exceptions import V20Error
        client = _make_client()
        err = V20Error(404, "POSITION_NOT_FOUND")
        client._api.request.side_effect = err
        positions = client.get_positions()
        assert positions == []

    def test_both_sides(self):
        client = _make_client()
        client._api.request.return_value = self._position_response(
            long_units="5", short_units="-3"
        )
        positions = client.get_positions()
        sides = {p["side"] for p in positions}
        assert "long" in sides
        assert "short" in sides


# ---------------------------------------------------------------------------
# modify_sl
# ---------------------------------------------------------------------------

class TestModifySl:
    def test_returns_true_on_success(self):
        client = _make_client()
        # TradesList returns one open long trade
        client._api.request.side_effect = [
            {"trades": [{"id": "999", "currentUnits": "10"}]},
            {},  # TradeCRCDO response
        ]
        result = client.modify_sl(side="buy", new_sl=1880.0)
        assert result is True

    def test_returns_false_when_no_trades(self):
        client = _make_client()
        client._api.request.return_value = {"trades": []}
        result = client.modify_sl(side="buy", new_sl=1880.0)
        assert result is False

    def test_wrong_side_skipped(self):
        client = _make_client()
        # Short trade — asking to modify long side
        client._api.request.return_value = {"trades": [{"id": "999", "currentUnits": "-5"}]}
        result = client.modify_sl(side="buy", new_sl=1880.0)
        assert result is False


# ---------------------------------------------------------------------------
# set_leverage
# ---------------------------------------------------------------------------

class TestSetLeverage:
    def test_does_not_raise(self):
        client = _make_client()
        # Should complete without making an API call
        client.set_leverage(20)
        client._api.request.assert_not_called()


# ---------------------------------------------------------------------------
# get_funding_rate
# ---------------------------------------------------------------------------

class TestGetFundingRate:
    def test_always_returns_zero(self):
        client = _make_client()
        assert client.get_funding_rate() == 0.0
        assert client.get_funding_rate("XAU_USD") == 0.0


# ---------------------------------------------------------------------------
# get_closed_pnl
# ---------------------------------------------------------------------------

class TestGetClosedPnl:
    def test_returns_list_of_dicts(self):
        client = _make_client()
        client._api.request.return_value = {
            "trades": [
                {
                    "id": "123",
                    "currentUnits": "0",
                    "initialUnits": "10",
                    "averageClosePrice": "1920.00",
                    "realizedPL": "150.00",
                    "closeTime": "2024-01-02T10:00:00.000000000Z",
                }
            ]
        }
        # Pass since_ms=0 so the timestamp filter doesn't exclude the fixture trade
        result = client.get_closed_pnl(since_ms=0)
        assert isinstance(result, list)
        assert len(result) == 1
        entry = result[0]
        assert "id" in entry
        assert "side" in entry
        assert "price" in entry
        assert "amount" in entry
        assert "cost" in entry

    def test_empty_returns_empty_list(self):
        client = _make_client()
        client._api.request.return_value = {"trades": []}
        result = client.get_closed_pnl()
        assert result == []


# ---------------------------------------------------------------------------
# close_all_positions
# ---------------------------------------------------------------------------

class TestCloseAllPositions:
    def test_no_positions_noop(self):
        from oandapyV20.exceptions import V20Error
        client = _make_client()
        err = V20Error(404, "POSITION_NOT_FOUND")
        client._api.request.side_effect = err
        # Should not raise
        client.close_all_positions()

    def test_closes_long_with_sell(self):
        client = _make_client()
        client._api.request.side_effect = [
            # get_positions -> PositionDetails
            {
                "position": {
                    "instrument": "XAU_USD",
                    "long": {"units": "10", "averagePrice": "1905", "unrealizedPL": "0"},
                    "short": {"units": "0", "averagePrice": "0", "unrealizedPL": "0"},
                }
            },
            # place_order (close) -> OrderCreate
            {
                "orderFillTransaction": {
                    "orderID": "555",
                    "price": "1905",
                    "units": "-10",
                }
            },
        ]
        client.close_all_positions()
        assert client._api.request.call_count == 2


# ---------------------------------------------------------------------------
# cancel_all_orders
# ---------------------------------------------------------------------------

class TestCancelAllOrders:
    def test_no_pending_orders_noop(self):
        client = _make_client()
        client._api.request.return_value = {"trades": []}
        # Should not raise
        client.cancel_all_orders()

    def test_cancels_pending_trades(self):
        client = _make_client()
        client._api.request.side_effect = [
            {"trades": [{"id": "777"}]},
            {},  # TradeClose response
        ]
        client.cancel_all_orders()
        assert client._api.request.call_count == 2


# ---------------------------------------------------------------------------
# Interface parity check (duck-typing contract with BybitClient)
# ---------------------------------------------------------------------------

class TestInterfaceParity:
    """Verify OandaClient exposes the same public methods as BybitClient."""

    BYBIT_METHODS = [
        "get_balance",
        "get_ohlcv",
        "get_ticker_price",
        "place_order",
        "get_positions",
        "close_all_positions",
        "cancel_all_orders",
        "modify_sl",
        "set_leverage",
        "get_closed_pnl",
        "get_funding_rate",
    ]

    def test_all_methods_present(self):
        from bot.forex_exchange import OandaClient
        for method_name in self.BYBIT_METHODS:
            assert hasattr(OandaClient, method_name), (
                f"OandaClient is missing method: {method_name}"
            )

    def test_order_result_fields_match(self):
        """OrderResult dataclass must have same fields in both modules."""
        from bot.forex_exchange import OrderResult as ForexOrderResult
        from bot.exchange import OrderResult as BybitOrderResult

        forex_fields = {f.name for f in fields(ForexOrderResult)}
        bybit_fields = {f.name for f in fields(BybitOrderResult)}
        assert forex_fields == bybit_fields, (
            f"OrderResult field mismatch. "
            f"Forex: {forex_fields}, Bybit: {bybit_fields}"
        )
