"""Tests for T2: BybitClient.market_data wiring.

TDD spec — written BEFORE implementation.

Scenarios:
1. flag-on + market_data → get_balance served from cache, exchange NOT called on hit
2. flag-on + market_data → get_ohlcv served from cache, exchange NOT called on hit
3. flag-on + cache hit → _last_request_time NOT advanced (rate-limit clock frozen)
4. flag-on + fresh=True → cache bypassed, exchange called
5. flag-off → get_balance/get_ohlcv go through _retry/_rate_limit exactly as before (parity)
6. get_positions always hits exchange per-symbol regardless of flag or market_data
7. market_data=None + flag-on → falls through to _retry/_rate_limit (same as flag-off parity)
8. get_balance/get_ohlcv accept `fresh` param and pass it to market_data.get_balance/get_ohlcv
"""

import os
import time
from typing import Optional
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from bot.exchange import BybitClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config() -> dict:
    return {
        "symbol": "BTCUSDT",
        "use_testnet": True,
        "leverage": 5,
        "risk_per_trade": 0.01,
        "max_daily_loss": 0.03,
        "max_positions": 2,
    }


def _make_ohlcv_df() -> pd.DataFrame:
    """Return a minimal closed-candle DataFrame."""
    ts = pd.to_datetime([1_700_000_000, 1_700_003_600], unit="s", utc=True)
    return pd.DataFrame(
        {"open": [100.0, 101.0], "high": [102.0, 103.0],
         "low": [99.0, 100.0], "close": [101.0, 102.0], "volume": [500.0, 600.0]},
        index=ts,
    )


def _make_stub_market_data(balance: float = 500.0, ohlcv_df: Optional[pd.DataFrame] = None) -> MagicMock:
    """Return a MagicMock that behaves like SharedMarketData."""
    smd = MagicMock()
    smd.get_balance.return_value = balance
    if ohlcv_df is None:
        ohlcv_df = _make_ohlcv_df()
    smd.get_ohlcv.return_value = ohlcv_df
    return smd


# ---------------------------------------------------------------------------
# Fixture: BybitClient with mocked ccxt (no real network)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ccxt():
    """Patch ccxt.bybit so no network is used."""
    with patch("bot.exchange.ccxt.bybit") as MockBybit:
        mock_ex = MagicMock()
        MockBybit.return_value = mock_ex
        mock_ex.load_markets.return_value = {}
        mock_ex.set_sandbox_mode = MagicMock()
        mock_ex.markets = {"BTC/USDT:USDT": {}}
        mock_ex.amount_to_precision.side_effect = lambda s, v: v
        mock_ex.price_to_precision.side_effect = lambda s, v: v
        yield mock_ex


@pytest.fixture
def config():
    return _make_config()


# ---------------------------------------------------------------------------
# 1. flag-on + market_data → get_balance served from cache
# ---------------------------------------------------------------------------

class TestGetBalanceCacheHit:
    """When CCBT_SHARED_MARKETDATA=1 and market_data is wired in,
    get_balance() must return the cached value without touching the exchange."""

    def test_balance_served_from_cache_not_exchange(self, config, mock_ccxt):
        smd = _make_stub_market_data(balance=999.0)
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            result = client.get_balance()

        assert result == 999.0
        smd.get_balance.assert_called_once()
        # The ccxt exchange.fetch_balance must NOT have been called
        mock_ccxt.fetch_balance.assert_not_called()

    def test_balance_fresh_passed_to_market_data(self, config, mock_ccxt):
        smd = _make_stub_market_data(balance=42.0)
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            result = client.get_balance(fresh=True)

        assert result == 42.0
        smd.get_balance.assert_called_once_with(fresh=True)
        mock_ccxt.fetch_balance.assert_not_called()


# ---------------------------------------------------------------------------
# 2. flag-on + market_data → get_ohlcv served from cache
# ---------------------------------------------------------------------------

class TestGetOHLCVCacheHit:
    """When flag=1 and market_data is set, get_ohlcv() returns from cache."""

    def test_ohlcv_served_from_cache_not_exchange(self, config, mock_ccxt):
        df_expected = _make_ohlcv_df()
        smd = _make_stub_market_data(ohlcv_df=df_expected)
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            df = client.get_ohlcv("BTCUSDT", "1h", limit=50)

        assert isinstance(df, pd.DataFrame)
        # cache was queried
        smd.get_ohlcv.assert_called_once()
        # raw exchange must NOT have been called
        mock_ccxt.fetch_ohlcv.assert_not_called()

    def test_ohlcv_passes_symbol_timeframe_limit(self, config, mock_ccxt):
        smd = _make_stub_market_data()
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            client.get_ohlcv("BTCUSDT", "4h", limit=200)

        # The normalised symbol (BTC/USDT:USDT) and timeframe must be forwarded
        args, kwargs = smd.get_ohlcv.call_args
        # symbol may be normalised — at minimum, timeframe and limit must arrive
        assert "4h" in (args + tuple(kwargs.values()))
        assert 200 in (args + tuple(kwargs.values()))

    def test_ohlcv_fresh_passed_to_market_data(self, config, mock_ccxt):
        smd = _make_stub_market_data()
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            client.get_ohlcv("BTCUSDT", "1h", fresh=True)

        _, kwargs = smd.get_ohlcv.call_args
        assert kwargs.get("fresh") is True or True in smd.get_ohlcv.call_args[0]


# ---------------------------------------------------------------------------
# 3. Cache hit must NOT advance _last_request_time
# ---------------------------------------------------------------------------

class TestNoClockAdvanceOnCacheHit:
    """A cache hit must not call _rate_limit(), so _last_request_time stays frozen."""

    def test_last_request_time_unchanged_on_balance_cache_hit(self, config, mock_ccxt):
        smd = _make_stub_market_data(balance=7.0)
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)
        initial_lrt = client._last_request_time

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            client.get_balance()

        assert client._last_request_time == initial_lrt, (
            "_last_request_time must not advance on a cache hit"
        )

    def test_last_request_time_unchanged_on_ohlcv_cache_hit(self, config, mock_ccxt):
        smd = _make_stub_market_data()
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)
        initial_lrt = client._last_request_time

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            client.get_ohlcv("BTCUSDT", "1h")

        assert client._last_request_time == initial_lrt, (
            "_last_request_time must not advance on a cache hit"
        )


# ---------------------------------------------------------------------------
# 4. flag-off → exchange called via _retry exactly as before (parity)
# ---------------------------------------------------------------------------

class TestFlagOffParity:
    """When CCBT_SHARED_MARKETDATA is absent or != '1', behaviour is byte-for-byte as before."""

    def test_balance_calls_retry_when_flag_off(self, config, mock_ccxt):
        smd = _make_stub_market_data(balance=999.0)
        mock_ccxt.fetch_balance.return_value = {"USDT": {"free": 111.0, "total": 111.0}}
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {}, clear=True):
            # Ensure CCBT_SHARED_MARKETDATA is NOT set
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            result = client.get_balance()

        assert result == 111.0  # from exchange, not cache
        mock_ccxt.fetch_balance.assert_called_once()
        smd.get_balance.assert_not_called()

    def test_ohlcv_calls_retry_when_flag_off(self, config, mock_ccxt):
        smd = _make_stub_market_data()
        mock_ccxt.fetch_ohlcv.return_value = [
            [1_700_000_000_000, 60000, 60100, 59900, 60050, 100],
            [1_700_003_600_000, 60050, 60200, 60000, 60150, 120],
        ]
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            df = client.get_ohlcv("BTCUSDT", "1h")

        assert isinstance(df, pd.DataFrame)
        mock_ccxt.fetch_ohlcv.assert_called_once()
        smd.get_ohlcv.assert_not_called()

    def test_balance_no_market_data_always_uses_retry(self, config, mock_ccxt):
        """market_data=None (default) must always call exchange, flag irrelevant."""
        mock_ccxt.fetch_balance.return_value = {"USDT": {"free": 222.0, "total": 222.0}}
        client = BybitClient(config, shared_exchange=mock_ccxt)  # no market_data

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            result = client.get_balance()

        assert result == 222.0
        mock_ccxt.fetch_balance.assert_called_once()

    def test_ohlcv_no_market_data_always_uses_retry(self, config, mock_ccxt):
        """market_data=None (default) must always call exchange, flag irrelevant."""
        mock_ccxt.fetch_ohlcv.return_value = [
            [1_700_000_000_000, 60000, 60100, 59900, 60050, 100],
        ]
        client = BybitClient(config, shared_exchange=mock_ccxt)  # no market_data

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            df = client.get_ohlcv("BTCUSDT", "1h")

        assert isinstance(df, pd.DataFrame)
        mock_ccxt.fetch_ohlcv.assert_called_once()


# ---------------------------------------------------------------------------
# 5. get_positions always hits exchange, never cached
# ---------------------------------------------------------------------------

class TestGetPositionsNeverCached:
    """get_positions() must ALWAYS fetch from the exchange per-symbol,
    regardless of flag or market_data."""

    def test_positions_hit_exchange_flag_on(self, config, mock_ccxt):
        smd = _make_stub_market_data()
        mock_ccxt.fetch_positions.return_value = []
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            positions = client.get_positions()

        assert positions == []
        mock_ccxt.fetch_positions.assert_called_once()
        # market_data must have no get_positions method called (not cached)
        assert not hasattr(smd, "get_positions") or not smd.get_positions.called

    def test_positions_hit_exchange_flag_off(self, config, mock_ccxt):
        smd = _make_stub_market_data()
        mock_ccxt.fetch_positions.return_value = []
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            client.get_positions()

        mock_ccxt.fetch_positions.assert_called_once()

    def test_positions_hit_exchange_no_market_data(self, config, mock_ccxt):
        mock_ccxt.fetch_positions.return_value = []
        client = BybitClient(config, shared_exchange=mock_ccxt)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            client.get_positions()

        mock_ccxt.fetch_positions.assert_called_once()


# ---------------------------------------------------------------------------
# 6. market_data=None default: __init__ signature is backward compatible
# ---------------------------------------------------------------------------

class TestInitBackwardCompat:
    """BybitClient(config) and BybitClient(config, shared_exchange=x) still work
    without the new market_data parameter."""

    def test_default_market_data_is_none(self, config, mock_ccxt):
        client = BybitClient(config, shared_exchange=mock_ccxt)
        assert client._market_data is None

    def test_market_data_stored(self, config, mock_ccxt):
        smd = _make_stub_market_data()
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)
        assert client._market_data is smd


# ---------------------------------------------------------------------------
# 7. get_balance fresh=True accepted as positional/keyword (signature check)
# ---------------------------------------------------------------------------

class TestGetBalanceFreshSignature:
    """get_balance must accept fresh as a keyword argument."""

    def test_get_balance_accepts_fresh_kwarg(self, config, mock_ccxt):
        mock_ccxt.fetch_balance.return_value = {"USDT": {"free": 1.0, "total": 1.0}}
        client = BybitClient(config, shared_exchange=mock_ccxt)

        # Should not raise TypeError regardless of flag state
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            result = client.get_balance(fresh=True)
        assert result == 1.0


# ---------------------------------------------------------------------------
# 8. get_ohlcv fresh=True accepted (signature check)
# ---------------------------------------------------------------------------

class TestGetOHLCVFreshSignature:
    """get_ohlcv must accept fresh as a keyword argument."""

    def test_get_ohlcv_accepts_fresh_kwarg(self, config, mock_ccxt):
        mock_ccxt.fetch_ohlcv.return_value = [
            [1_700_000_000_000, 60000, 60100, 59900, 60050, 100],
        ]
        client = BybitClient(config, shared_exchange=mock_ccxt)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            df = client.get_ohlcv("BTCUSDT", "1h", fresh=True)
        assert isinstance(df, pd.DataFrame)
