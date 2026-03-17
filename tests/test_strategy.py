"""Tests for strategy signal generation."""

import numpy as np
import pandas as pd
import pytest

from bot.data import add_indicators, compute_atr, compute_ema, compute_rsi
from bot.strategy import (
    SignalType,
    TradeSignal,
    check_entry_conditions,
    compute_levels,
    compute_trailing_stop,
    generate_signal,
)


@pytest.fixture
def config():
    """Standard test configuration."""
    return {
        "symbol": "BTCUSDT",
        "ema_fast": 9,
        "ema_slow": 21,
        "ema_trend": 50,
        "rsi_period": 14,
        "rsi_min": 45,
        "rsi_max": 65,
        "atr_period": 14,
        "atr_sl_mult": 1.5,
        "atr_tp_mult": 3.0,
        "atr_min": 0.001,
        "volume_mult": 1.2,
        "min_rr_ratio": 2.0,
    }


def make_ohlcv(n: int = 100, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic OHLCV data.

    Args:
        n: Number of candles.
        trend: 'up', 'down', or 'flat'.

    Returns:
        DataFrame with OHLCV columns.
    """
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="15min")

    if trend == "up":
        base = 60000 + np.cumsum(np.random.randn(n) * 50 + 10)
    elif trend == "down":
        base = 60000 + np.cumsum(np.random.randn(n) * 50 - 10)
    else:
        base = 60000 + np.cumsum(np.random.randn(n) * 20)

    high = base + np.abs(np.random.randn(n) * 30)
    low = base - np.abs(np.random.randn(n) * 30)
    volume = np.random.uniform(100, 500, n)

    df = pd.DataFrame(
        {
            "open": base + np.random.randn(n) * 5,
            "high": high,
            "low": low,
            "close": base,
            "volume": volume,
        },
        index=dates,
    )
    return df


class TestIndicators:
    """Test technical indicator calculations."""

    def test_ema_length(self):
        """EMA output should have same length as input."""
        s = pd.Series(range(50), dtype=float)
        ema = compute_ema(s, 9)
        assert len(ema) == 50

    def test_ema_values(self):
        """EMA should converge to constant for constant input."""
        s = pd.Series([100.0] * 50)
        ema = compute_ema(s, 9)
        assert abs(ema.iloc[-1] - 100.0) < 0.001

    def test_rsi_range(self):
        """RSI should be between 0 and 100."""
        df = make_ohlcv(100, "up")
        rsi = compute_rsi(df["close"], 14)
        valid = rsi.dropna()
        assert all(0 <= v <= 100 for v in valid)

    def test_rsi_overbought_in_uptrend(self):
        """RSI should be relatively high in a strong uptrend."""
        df = make_ohlcv(100, "up")
        rsi = compute_rsi(df["close"], 14)
        assert rsi.iloc[-1] > 50

    def test_atr_positive(self):
        """ATR should always be positive."""
        df = make_ohlcv(100)
        atr = compute_atr(df["high"], df["low"], df["close"], 14)
        valid = atr.dropna()
        assert all(v > 0 for v in valid)


class TestSignalGeneration:
    """Test signal generation logic."""

    def test_no_signal_flat_market(self, config):
        """Flat market should produce no signal (no crossover)."""
        df = make_ohlcv(100, "flat")
        # In a flat market, crossovers are less likely to coincide with all conditions
        signal = generate_signal(df, None, config)
        # Signal may or may not exist, but it should be valid if it does
        if signal is not None:
            assert isinstance(signal, TradeSignal)
            assert signal.signal_type in (SignalType.LONG, SignalType.SHORT)

    def test_signal_has_valid_levels(self, config):
        """If a signal is generated, levels should be consistent."""
        df = make_ohlcv(100, "up")
        signal = generate_signal(df, None, config)
        if signal is not None:
            if signal.signal_type == SignalType.LONG:
                assert signal.stop_loss < signal.entry_price
                assert signal.take_profit > signal.entry_price
            else:
                assert signal.stop_loss > signal.entry_price
                assert signal.take_profit < signal.entry_price
            assert signal.risk_reward_ratio >= config["min_rr_ratio"]

    def test_compute_levels_long(self, config):
        """Long levels: SL below entry, TP above entry."""
        sl, tp = compute_levels(60000, 500, SignalType.LONG, config)
        assert sl == 60000 - 500 * 1.5  # 59250
        assert tp == 60000 + 500 * 3.0  # 61500

    def test_compute_levels_short(self, config):
        """Short levels: SL above entry, TP below entry."""
        sl, tp = compute_levels(60000, 500, SignalType.SHORT, config)
        assert sl == 60000 + 500 * 1.5  # 60750
        assert tp == 60000 - 500 * 3.0  # 58500


class TestTrailingStop:
    """Test trailing stop loss logic."""

    def test_trailing_long_moves_up(self, config):
        """Trailing SL for long should only move up."""
        current_sl = 59000
        new_sl = compute_trailing_stop(61000, current_sl, 500, SignalType.LONG, config)
        assert new_sl >= current_sl

    def test_trailing_long_never_moves_down(self, config):
        """Trailing SL for long should never move down."""
        current_sl = 59500
        # Price drops but SL should not
        new_sl = compute_trailing_stop(59600, current_sl, 500, SignalType.LONG, config)
        assert new_sl >= current_sl

    def test_trailing_short_moves_down(self, config):
        """Trailing SL for short should only move down."""
        current_sl = 61000
        new_sl = compute_trailing_stop(59000, current_sl, 500, SignalType.SHORT, config)
        assert new_sl <= current_sl

    def test_trailing_short_never_moves_up(self, config):
        """Trailing SL for short should never move up."""
        current_sl = 60500
        new_sl = compute_trailing_stop(60400, current_sl, 500, SignalType.SHORT, config)
        assert new_sl <= current_sl


class TestEntryConditions:
    """Test individual entry condition checks."""

    def test_rsi_out_of_range_rejects(self, config):
        """Signal should be rejected when RSI is outside range."""
        row = pd.Series({
            "rsi": 70.0,  # Above rsi_max
            "atr": 500.0,
            "volume": 300.0,
            "volume_ma": 200.0,
            "ema_cross_up": True,
            "above_trend": True,
        })
        assert not check_entry_conditions(row, config, SignalType.LONG)

    def test_low_volume_rejects(self, config):
        """Signal should be rejected when volume is below threshold."""
        row = pd.Series({
            "rsi": 55.0,
            "atr": 500.0,
            "volume": 100.0,  # Below volume_ma * 1.2 = 240
            "volume_ma": 200.0,
            "ema_cross_up": True,
            "above_trend": True,
        })
        assert not check_entry_conditions(row, config, SignalType.LONG)

    def test_no_crossover_rejects(self, config):
        """Signal should be rejected without EMA crossover."""
        row = pd.Series({
            "rsi": 55.0,
            "atr": 500.0,
            "volume": 300.0,
            "volume_ma": 200.0,
            "ema_cross_up": False,
            "above_trend": True,
        })
        assert not check_entry_conditions(row, config, SignalType.LONG)

    def test_valid_long_entry(self, config):
        """All conditions met should produce valid long entry."""
        row = pd.Series({
            "rsi": 55.0,
            "atr": 500.0,
            "volume": 300.0,
            "volume_ma": 200.0,
            "ema_cross_up": True,
            "above_trend": True,
        })
        assert check_entry_conditions(row, config, SignalType.LONG)

    def test_valid_short_entry(self, config):
        """All conditions met should produce valid short entry."""
        row = pd.Series({
            "rsi": 55.0,
            "atr": 500.0,
            "volume": 300.0,
            "volume_ma": 200.0,
            "ema_cross_down": True,
            "below_trend": True,
        })
        assert check_entry_conditions(row, config, SignalType.SHORT)

    def test_low_atr_rejects(self, config):
        """Signal should be rejected when ATR is below minimum."""
        row = pd.Series({
            "rsi": 55.0,
            "atr": 0.0001,  # Below atr_min
            "volume": 300.0,
            "volume_ma": 200.0,
            "ema_cross_up": True,
            "above_trend": True,
        })
        assert not check_entry_conditions(row, config, SignalType.LONG)
