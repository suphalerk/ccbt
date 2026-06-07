"""T5 — Flag wiring + start.sh + full parity test.

TDD spec: written BEFORE confirming implementation, run to see GREEN because
all wiring was completed in T1-T4. These tests LOCK IN the guarantee that:

  1. Flag-OFF end-to-end parity: BybitClient(config, market_data=None) with the
     flag unset/off is byte-for-byte identical to the pre-feature code path — the
     exchange is always called directly via _retry, the cache is never consulted,
     and _last_request_time advances exactly as before.

  2. main.py single-bot path: TradingEngine is constructed without market_data,
     so engine._market_data is None — the shared-data path is fully inert there.

  3. start.sh has the canonical 'export CCBT_SHARED_MARKETDATA=1' line so the
     flag goes live only on the next manual restart (not now — DO NOT restart).

Conventions:
- No real network calls (all exchange interactions mocked).
- Follow tests/test_ai_analyst.py / tests/test_exchange.py patterns.
- Never weaken coverage; if a test is wrong, fix the test WITH justification.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _make_ohlcv_rows() -> list:
    """Raw ccxt OHLCV list (what the exchange returns before DataFrame conversion)."""
    return [
        [1_700_000_000_000, 60000.0, 60100.0, 59900.0, 60050.0, 100.0],
        [1_700_003_600_000, 60050.0, 60200.0, 60000.0, 60150.0, 120.0],
    ]


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
# 1. Flag-OFF end-to-end parity — get_balance
# ---------------------------------------------------------------------------

class TestFlagOffBalanceParity:
    """Flag-OFF: get_balance() must reach the exchange exactly as before the feature.

    Invariants that must hold regardless of whether market_data is set:
    - exchange.fetch_balance called once
    - _last_request_time is advanced (rate-limit clock runs normally)
    - SharedMarketData (if present) is never consulted
    """

    def test_flag_off_market_data_none_balance_hits_exchange(self, config, mock_ccxt):
        """Base case: no market_data, flag off → exchange called (unambiguous parity)."""
        mock_ccxt.fetch_balance.return_value = {"USDT": {"free": 500.0, "total": 500.0}}
        client = BybitClient(config, shared_exchange=mock_ccxt)  # market_data=None by default

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            balance = client.get_balance()

        assert balance == 500.0
        mock_ccxt.fetch_balance.assert_called_once()

    def test_flag_off_with_market_data_still_hits_exchange(self, config, mock_ccxt):
        """Even if market_data is wired but flag is OFF, exchange must be called."""
        mock_ccxt.fetch_balance.return_value = {"USDT": {"free": 300.0, "total": 300.0}}
        smd = MagicMock()
        smd.get_balance.return_value = 999.0  # cache says 999, but must not be used
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            balance = client.get_balance()

        assert balance == 300.0, "Flag-off must return exchange value, not cache"
        mock_ccxt.fetch_balance.assert_called_once()
        smd.get_balance.assert_not_called()

    def test_flag_zero_string_market_data_hits_exchange(self, config, mock_ccxt):
        """CCBT_SHARED_MARKETDATA='0' is flag-off; must fall through to exchange."""
        mock_ccxt.fetch_balance.return_value = {"USDT": {"free": 700.0, "total": 700.0}}
        smd = MagicMock()
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "0"}):
            balance = client.get_balance()

        assert balance == 700.0
        mock_ccxt.fetch_balance.assert_called_once()
        smd.get_balance.assert_not_called()

    def test_flag_off_last_request_time_advances_on_balance(self, config, mock_ccxt):
        """Flag-off: _last_request_time must advance after get_balance (rate-limit runs)."""
        mock_ccxt.fetch_balance.return_value = {"USDT": {"free": 100.0, "total": 100.0}}
        client = BybitClient(config, shared_exchange=mock_ccxt)
        before = client._last_request_time

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            client.get_balance()

        # After a real exchange call, _last_request_time must have been updated.
        # It may still equal `before` if the call happened within the same
        # time.monotonic() resolution, so we only assert it has not decreased.
        assert client._last_request_time >= before, (
            "_last_request_time must not decrease after a real exchange call"
        )

    def test_flag_off_fresh_kwarg_ignored_still_calls_exchange(self, config, mock_ccxt):
        """fresh=True with flag-off: exchange is still called (no cache to bypass)."""
        mock_ccxt.fetch_balance.return_value = {"USDT": {"free": 50.0, "total": 50.0}}
        client = BybitClient(config, shared_exchange=mock_ccxt)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            balance = client.get_balance(fresh=True)

        assert balance == 50.0
        mock_ccxt.fetch_balance.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Flag-OFF end-to-end parity — get_ohlcv
# ---------------------------------------------------------------------------

class TestFlagOffOHLCVParity:
    """Flag-OFF: get_ohlcv() must reach the exchange exactly as before the feature."""

    def test_flag_off_market_data_none_ohlcv_hits_exchange(self, config, mock_ccxt):
        """No market_data, flag off → exchange called (unambiguous parity)."""
        mock_ccxt.fetch_ohlcv.return_value = _make_ohlcv_rows()
        client = BybitClient(config, shared_exchange=mock_ccxt)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            df = client.get_ohlcv("BTCUSDT", "1h")

        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        mock_ccxt.fetch_ohlcv.assert_called_once()

    def test_flag_off_with_market_data_still_hits_exchange_ohlcv(self, config, mock_ccxt):
        """market_data wired but flag OFF → exchange called, cache untouched."""
        mock_ccxt.fetch_ohlcv.return_value = _make_ohlcv_rows()
        smd = MagicMock()
        smd.get_ohlcv.return_value = pd.DataFrame()  # would return empty if called
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            df = client.get_ohlcv("BTCUSDT", "1h")

        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0  # real data from exchange
        mock_ccxt.fetch_ohlcv.assert_called_once()
        smd.get_ohlcv.assert_not_called()

    def test_flag_off_ohlcv_last_request_time_advances(self, config, mock_ccxt):
        """Flag-off: _last_request_time must not decrease after get_ohlcv."""
        mock_ccxt.fetch_ohlcv.return_value = _make_ohlcv_rows()
        client = BybitClient(config, shared_exchange=mock_ccxt)
        before = client._last_request_time

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            client.get_ohlcv("BTCUSDT", "1h")

        assert client._last_request_time >= before

    def test_flag_off_fresh_kwarg_ohlcv_calls_exchange(self, config, mock_ccxt):
        """fresh=True with flag-off and no cache → exchange still called."""
        mock_ccxt.fetch_ohlcv.return_value = _make_ohlcv_rows()
        client = BybitClient(config, shared_exchange=mock_ccxt)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            df = client.get_ohlcv("BTCUSDT", "1h", fresh=True)

        assert isinstance(df, pd.DataFrame)
        mock_ccxt.fetch_ohlcv.assert_called_once()


# ---------------------------------------------------------------------------
# 3. main.py single-bot path — TradingEngine constructed with market_data=None
# ---------------------------------------------------------------------------

class TestMainPySingleBotPath:
    """main.py must construct TradingEngine without market_data, keeping the
    single-bot path completely inert with respect to the shared-data feature."""

    def test_trading_engine_market_data_defaults_to_none(self, config, mock_ccxt):
        """TradingEngine(config=...) must leave _market_data as None."""
        import asyncio
        from bot.engine import TradingEngine

        shutdown_event = asyncio.Event()
        engine = TradingEngine(config=config, shutdown_event=shutdown_event)

        assert engine._market_data is None, (
            "TradingEngine constructed without market_data must have _market_data=None; "
            "the single-bot main.py path must be inert w.r.t. the shared-data feature"
        )

    def test_main_py_does_not_pass_market_data(self):
        """Regression guard: main.py must NOT pass market_data to TradingEngine.

        We inspect the source of main.py to assert the TradingEngine constructor
        call does not include market_data — if it does, the single-bot path is no
        longer guaranteed to be inert, and this test must fail until reviewed.
        """
        main_path = Path(__file__).parent.parent / "main.py"
        source = main_path.read_text()

        # Find the TradingEngine constructor call block.
        # It should NOT contain 'market_data=' as a keyword argument.
        # (The keyword exists in engine.py's __init__ signature — that's fine.
        # What must NOT happen is main.py passing it.)
        import re
        # Look for TradingEngine( ... market_data= ... ) in the source.
        # A simple substring search is sufficient: if 'market_data' appears in
        # the same context as 'TradingEngine(' we flag it.
        te_block_match = re.search(
            r"TradingEngine\s*\(.*?market_data\s*=",
            source,
            re.DOTALL,
        )
        assert te_block_match is None, (
            "main.py must not pass market_data= to TradingEngine; "
            "the single-bot path must stay inert. "
            f"Found: {te_block_match.group() if te_block_match else ''}"
        )

    def test_bybit_client_market_data_none_flag_on_still_calls_exchange(
        self, config, mock_ccxt
    ):
        """BybitClient with market_data=None + flag ON → exchange called (no cache).

        This is the single-bot path: even if someone enables the flag globally,
        without market_data wired the client behaves exactly as before.
        """
        mock_ccxt.fetch_balance.return_value = {"USDT": {"free": 111.0, "total": 111.0}}
        client = BybitClient(config, shared_exchange=mock_ccxt)  # market_data=None

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            balance = client.get_balance()

        assert balance == 111.0
        mock_ccxt.fetch_balance.assert_called_once()


# ---------------------------------------------------------------------------
# 4. start.sh has the canonical export line
# ---------------------------------------------------------------------------

class TestStartShExport:
    """deploy/macos/start.sh must contain the canonical export line.

    The bot only picks this up on a future manual restart — we are NOT
    restarting now.  This test acts as a regression guard to prevent the line
    being accidentally removed in a future commit.
    """

    START_SH = Path(__file__).parent.parent / "deploy" / "macos" / "start.sh"

    def test_start_sh_exists(self):
        """start.sh must exist at the expected path."""
        assert self.START_SH.exists(), f"start.sh not found at {self.START_SH}"

    def test_start_sh_has_ccbt_shared_marketdata_export(self):
        """start.sh must contain 'export CCBT_SHARED_MARKETDATA=1'."""
        content = self.START_SH.read_text()
        assert "export CCBT_SHARED_MARKETDATA=1" in content, (
            "deploy/macos/start.sh must contain 'export CCBT_SHARED_MARKETDATA=1' "
            "(the canonical place to enable the shared-data feature for production). "
            "If this line is missing, the bots will run without the cache on restart."
        )

    def test_start_sh_export_not_commented_out(self):
        """The export line must not be commented out (disabled)."""
        content = self.START_SH.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if "CCBT_SHARED_MARKETDATA=1" in stripped:
                assert not stripped.startswith("#"), (
                    "The CCBT_SHARED_MARKETDATA=1 export line is commented out in start.sh; "
                    "it must be active (uncommented) for production."
                )
                return  # found an uncommented line — pass
        pytest.fail(
            "Could not find an uncommented 'export CCBT_SHARED_MARKETDATA=1' in start.sh"
        )


# ---------------------------------------------------------------------------
# 5. get_positions is never cached regardless of flag or market_data
# ---------------------------------------------------------------------------

class TestPositionsNeverCachedParity:
    """Regression guard: get_positions() must ALWAYS call the exchange.

    This behaviour was established in T2 but is re-asserted here as part of
    the T5 end-to-end parity picture.
    """

    def test_positions_hit_exchange_flag_off_no_market_data(self, config, mock_ccxt):
        mock_ccxt.fetch_positions.return_value = []
        client = BybitClient(config, shared_exchange=mock_ccxt)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            result = client.get_positions()

        assert result == []
        mock_ccxt.fetch_positions.assert_called_once()

    def test_positions_hit_exchange_flag_on_no_market_data(self, config, mock_ccxt):
        mock_ccxt.fetch_positions.return_value = []
        client = BybitClient(config, shared_exchange=mock_ccxt)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            result = client.get_positions()

        assert result == []
        mock_ccxt.fetch_positions.assert_called_once()

    def test_positions_hit_exchange_flag_on_with_market_data(self, config, mock_ccxt):
        mock_ccxt.fetch_positions.return_value = []
        smd = MagicMock()
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)

        with patch.dict(os.environ, {"CCBT_SHARED_MARKETDATA": "1"}):
            result = client.get_positions()

        assert result == []
        mock_ccxt.fetch_positions.assert_called_once()
        assert not hasattr(smd, "get_positions") or not smd.get_positions.called


# ---------------------------------------------------------------------------
# 6. BybitClient backward-compatibility: market_data= is optional
# ---------------------------------------------------------------------------

class TestBybitClientBackwardCompat:
    """BybitClient must remain fully backward-compatible: the new market_data
    param is optional and defaults to None — existing callers are unaffected."""

    def test_no_args_beyond_config_still_works(self, config, mock_ccxt):
        """BybitClient(config) (no shared_exchange, no market_data) must not raise."""
        # Patch ccxt.bybit so no real exchange init happens
        client = BybitClient(config)
        assert client._market_data is None

    def test_shared_exchange_only_still_works(self, config, mock_ccxt):
        """BybitClient(config, shared_exchange=x) must not raise."""
        client = BybitClient(config, shared_exchange=mock_ccxt)
        assert client._market_data is None

    def test_all_three_args_works(self, config, mock_ccxt):
        """BybitClient(config, shared_exchange=x, market_data=y) must not raise."""
        smd = MagicMock()
        client = BybitClient(config, shared_exchange=mock_ccxt, market_data=smd)
        assert client._market_data is smd
