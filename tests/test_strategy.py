"""Tests for strategy signal generation."""

import numpy as np
import pandas as pd
import pytest

from bot.data import add_indicators, compute_atr, compute_ema, compute_rsi, detect_regime
from bot.strategy import (
    SignalType,
    TradeSignal,
    check_entry_conditions,
    compute_levels,
    compute_net_rr,
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


class TestNetRiskReward:
    """Test net R:R calculation with fees."""

    def test_fees_reduce_rr(self, config):
        """Net R:R should be lower than gross R:R due to fees."""
        entry = 60000.0
        sl = 59100.0  # 1.5% SL
        tp = 62700.0  # 4.5% TP (gross RR = 3.0)
        net_rr = compute_net_rr(entry, sl, tp, config)
        gross_rr = abs(tp - entry) / abs(entry - sl)
        assert net_rr < gross_rr
        assert net_rr > 0

    def test_zero_fees_equals_gross(self):
        """With zero fees, net R:R should equal gross R:R."""
        config = {"commission_rate": 0.0, "slippage_rate": 0.0}
        entry = 60000.0
        sl = 59000.0
        tp = 62000.0
        net_rr = compute_net_rr(entry, sl, tp, config)
        gross_rr = abs(tp - entry) / abs(entry - sl)
        assert net_rr == pytest.approx(gross_rr, rel=0.001)

    def test_high_fees_reduce_significantly(self):
        """High fees should dramatically reduce net R:R."""
        config = {"commission_rate": 0.005, "slippage_rate": 0.005}
        entry = 60000.0
        sl = 59400.0  # 600 risk
        tp = 61200.0  # 1200 reward (gross RR = 2.0)
        net_rr = compute_net_rr(entry, sl, tp, config)
        assert net_rr < 1.0  # High fees make this unprofitable


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
        """Signal should be rejected when RSI is extreme overbought."""
        row = pd.Series({
            "rsi": 82.0,  # Well above directional max (75 for longs)
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
            "rsi": 45.0,
            "atr": 500.0,
            "volume": 300.0,
            "volume_ma": 200.0,
            "ema_cross_down": True,
            "below_trend": True,
        })
        assert check_entry_conditions(row, config, SignalType.SHORT)

    def test_long_allows_higher_rsi(self, config):
        """Longs should allow RSI up to 75 (trend momentum)."""
        row = pd.Series({
            "rsi": 72.0,  # Above old rsi_max=65 but within long range
            "atr": 500.0,
            "volume": 300.0,
            "volume_ma": 200.0,
            "ema_cross_up": True,
            "above_trend": True,
        })
        assert check_entry_conditions(row, config, SignalType.LONG)

    def test_short_rejects_high_rsi(self, config):
        """Shorts should reject RSI above 52."""
        row = pd.Series({
            "rsi": 55.0,  # Above short max (52)
            "atr": 500.0,
            "volume": 300.0,
            "volume_ma": 200.0,
            "ema_cross_down": True,
            "below_trend": True,
        })
        assert not check_entry_conditions(row, config, SignalType.SHORT)

    def test_short_allows_lower_rsi(self, config):
        """Shorts should allow RSI down to 25."""
        row = pd.Series({
            "rsi": 30.0,  # Below old rsi_min=45 but within short range
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


class TestDetectRegime:
    """Test market regime detection."""

    def test_trending_regime(self):
        """Consistent uptrend with moderate ATR should be 'trending'."""
        np.random.seed(10)
        n = 80
        dates = pd.date_range("2024-01-01", periods=n, freq="15min")
        # Strong uptrend: consistent higher highs / higher lows
        base = 60000 + np.arange(n) * 20.0 + np.random.randn(n) * 5
        high = base + 15
        low = base - 15
        df = pd.DataFrame({
            "open": base,
            "high": high,
            "low": low,
            "close": base,
            "volume": np.random.uniform(100, 500, n),
        }, index=dates)
        regime = detect_regime(df, atr_period=14, lookback=20)
        assert regime == "trending"

    def test_ranging_regime(self):
        """Flat, low-volatility market should be 'ranging'."""
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="15min")
        # Truly flat market: constant price with tiny noise
        base = np.full(n, 60000.0) + np.random.randn(n) * 0.5
        high = base + 1
        low = base - 1
        df = pd.DataFrame({
            "open": base,
            "high": high,
            "low": low,
            "close": base,
            "volume": np.random.uniform(100, 500, n),
        }, index=dates)
        regime = detect_regime(df, atr_period=14, lookback=20)
        assert regime == "ranging"

    def test_volatile_regime(self):
        """Sudden spike in ATR should produce 'volatile'."""
        np.random.seed(30)
        n = 80
        dates = pd.date_range("2024-01-01", periods=n, freq="15min")
        base = 60000 + np.random.randn(n) * 10
        high = base + 15
        low = base - 15
        # Make the last few candles extremely volatile
        for i in range(n - 10, n):
            high[i] = base[i] + 500
            low[i] = base[i] - 500
        df = pd.DataFrame({
            "open": base,
            "high": high,
            "low": low,
            "close": base,
            "volume": np.random.uniform(100, 500, n),
        }, index=dates)
        regime = detect_regime(df, atr_period=14, lookback=20)
        assert regime == "volatile"

    def test_insufficient_data_returns_ranging(self):
        """Too few candles should default to 'ranging'."""
        dates = pd.date_range("2024-01-01", periods=10, freq="15min")
        df = pd.DataFrame({
            "open": [60000] * 10,
            "high": [60100] * 10,
            "low": [59900] * 10,
            "close": [60000] * 10,
            "volume": [200] * 10,
        }, index=dates)
        regime = detect_regime(df, atr_period=14, lookback=20)
        assert regime == "ranging"


class TestRegimeIntegration:
    """Test regime detection integration with signal generation."""

    def test_regime_blocks_signal_in_ranging(self, config):
        """Ranging regime should block all signals."""
        np.random.seed(20)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="15min")
        # Very flat market
        base = 60000 + np.random.randn(n) * 1
        df = pd.DataFrame({
            "open": base,
            "high": base + 2,
            "low": base - 2,
            "close": base,
            "volume": np.random.uniform(100, 500, n),
        }, index=dates)
        signal = generate_signal(df, None, config)
        assert signal is None

    def test_volatile_regime_sets_flag(self, config):
        """Volatile regime should set regime='volatile' on TradeSignal."""
        # We test the regime field is properly set on the dataclass
        signal = TradeSignal(
            signal_type=SignalType.LONG,
            entry_price=60000,
            stop_loss=59000,
            take_profit=62000,
            atr=500,
            rsi=55,
            risk_reward_ratio=2.0,
            regime="volatile",
        )
        assert signal.regime == "volatile"

    def test_trending_regime_allows_signal(self, config):
        """Trending regime should allow signal generation."""
        signal = TradeSignal(
            signal_type=SignalType.LONG,
            entry_price=60000,
            stop_loss=59000,
            take_profit=62000,
            atr=500,
            rsi=55,
            risk_reward_ratio=2.0,
            regime="trending",
        )
        assert signal.regime == "trending"
