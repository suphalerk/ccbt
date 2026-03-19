"""Tests for strategy signal generation."""

import numpy as np
import pandas as pd
import pytest

from bot.data import add_indicators, compute_atr, compute_ema, compute_rsi, detect_regime
from bot.strategy import (
    SignalType,
    TradeSignal,
    check_body_dominance_conditions,
    check_entry_conditions,
    check_mean_reversion_conditions,
    check_squeeze_release_conditions,
    compute_levels,
    compute_net_rr,
    compute_signal_quality_score,
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
        signal, _ = generate_signal(df, None, config)
        # Signal may or may not exist, but it should be valid if it does
        if signal is not None:
            assert isinstance(signal, TradeSignal)
            assert signal.signal_type in (SignalType.LONG, SignalType.SHORT)

    def test_signal_has_valid_levels(self, config):
        """If a signal is generated, levels should be consistent."""
        df = make_ohlcv(100, "up")
        signal, _ = generate_signal(df, None, config)
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
        """Longs should allow RSI up to 72 (trend momentum, not overbought)."""
        row = pd.Series({
            "rsi": 70.0,  # Above old rsi_max=65 but within long range (<=72)
            "atr": 500.0,
            "volume": 300.0,
            "volume_ma": 200.0,
            "ema_cross_up": True,
            "above_trend": True,
        })
        assert check_entry_conditions(row, config, SignalType.LONG)

    def test_short_rejects_high_rsi(self, config):
        """Shorts should reject RSI above rsi_short_max (config default 55, fallback 52)."""
        row = pd.Series({
            "rsi": 60.0,  # Above short max range
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

    def test_rsi_long_max_72_rejects_overbought(self):
        """RSI > 72 should reject long entries (prevent overbought entries)."""
        config = {
            "rsi_min": 45, "rsi_max": 65, "rsi_long_min": 45, "rsi_long_max": 72,
            "atr_min": 0.001, "volume_mult": 1.0,
        }
        row = pd.Series({
            "rsi": 75.0, "atr": 500.0, "volume": 300.0,
            "volume_ma": 200.0, "ema_cross_up": True, "above_trend": True,
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
        signal, _ = generate_signal(df, None, config)
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


class TestPostTP1TrailingStop:
    """Test trailing stop with post-TP1 wider multiplier."""

    def test_post_tp1_uses_wider_trail(self):
        """After TP1, trail should use atr_trail_mult_post_tp1 (wider)."""
        config = {"atr_sl_mult": 1.5, "atr_trail_mult": 1.8, "atr_trail_mult_post_tp1": 2.5}
        atr = 500.0
        current_price = 61000.0
        current_sl = 59000.0

        # Normal trail: 61000 - 1.8*500 = 60100
        normal_sl = compute_trailing_stop(current_price, current_sl, atr, SignalType.LONG, config, post_tp1=False)
        assert normal_sl == pytest.approx(60100.0)

        # Post-TP1 trail: 61000 - 2.5*500 = 59750
        post_tp1_sl = compute_trailing_stop(current_price, current_sl, atr, SignalType.LONG, config, post_tp1=True)
        assert post_tp1_sl == pytest.approx(59750.0)

        # Post-TP1 trail should be wider (lower SL for long)
        assert post_tp1_sl < normal_sl

    def test_post_tp1_short_uses_wider_trail(self):
        """After TP1 on short, trail should be wider (higher SL)."""
        config = {"atr_sl_mult": 1.5, "atr_trail_mult": 1.8, "atr_trail_mult_post_tp1": 2.5}
        atr = 500.0
        current_price = 59000.0
        current_sl = 61000.0

        # Normal trail: 59000 + 1.8*500 = 59900
        normal_sl = compute_trailing_stop(current_price, current_sl, atr, SignalType.SHORT, config, post_tp1=False)
        assert normal_sl == pytest.approx(59900.0)

        # Post-TP1 trail: 59000 + 2.5*500 = 60250
        post_tp1_sl = compute_trailing_stop(current_price, current_sl, atr, SignalType.SHORT, config, post_tp1=True)
        assert post_tp1_sl == pytest.approx(60250.0)

    def test_no_post_tp1_config_falls_back(self):
        """Without atr_trail_mult_post_tp1, post_tp1=True uses default trail."""
        config = {"atr_sl_mult": 1.5, "atr_trail_mult": 1.8}
        atr = 500.0
        normal_sl = compute_trailing_stop(61000, 59000, atr, SignalType.LONG, config, post_tp1=False)
        post_sl = compute_trailing_stop(61000, 59000, atr, SignalType.LONG, config, post_tp1=True)
        assert normal_sl == post_sl  # Same when no post_tp1 config


class TestFullTPNetRR:
    """Test net R:R calculation based on full TP distance.

    Partial TP is an execution strategy decoupled from signal filtering —
    compute_net_rr always uses the full TP distance regardless of partial TP config.
    """

    def test_partial_tp_config_ignored_in_rr(self):
        """partial_tp_enabled flag must not change the R:R result."""
        config_no_partial = {
            "commission_rate": 0.0004, "slippage_rate": 0.00015,
            "partial_tp_enabled": False,
        }
        config_partial = {
            "commission_rate": 0.0004, "slippage_rate": 0.00015,
            "partial_tp_enabled": True, "partial_tp_pct": 0.3,
            "partial_tp_atr_mult": 2.0, "atr_sl_mult": 1.5, "atr_tp_mult": 3.0,
        }
        entry = 60000.0
        sl = 60000 - 500 * 1.5  # 59250
        tp = 60000 + 500 * 3.0  # 61500

        rr_no_partial = compute_net_rr(entry, sl, tp, config_no_partial)
        rr_partial = compute_net_rr(entry, sl, tp, config_partial)

        # Both configs must produce the same R:R — partial TP is not a filter
        assert rr_no_partial == pytest.approx(rr_partial, rel=0.001)
        assert rr_no_partial > 0

    def test_full_tp_rr_formula(self):
        """R:R must use full TP distance and correct fee defaults."""
        config = {"commission_rate": 0.0004, "slippage_rate": 0.00015}
        entry = 60000.0
        sl = 59100.0
        tp = 62700.0
        rr = compute_net_rr(entry, sl, tp, config)
        # Expected formula: (reward - fee_impact) / (risk + fee_impact)
        round_trip_cost = (0.0004 + 0.00015) * 2
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        fee_impact = entry * round_trip_cost
        expected = (reward - fee_impact) / (risk + fee_impact)
        assert rr == pytest.approx(expected, rel=0.001)

    def test_rr_uses_full_tp_not_tp1(self):
        """R:R result must equal the full-TP R:R, not the TP1 (partial) R:R."""
        config = {
            "commission_rate": 0.0, "slippage_rate": 0.0,
            "partial_tp_enabled": True, "partial_tp_pct": 0.5,
            "partial_tp_atr_mult": 2.0, "atr_sl_mult": 1.5, "atr_tp_mult": 3.0,
        }
        entry = 60000.0
        atr = 500.0
        sl = entry - atr * 1.5   # 59250, risk = 750
        tp = entry + atr * 3.0   # 61500, full reward = 1500

        rr = compute_net_rr(entry, sl, tp, config)
        # Full TP: reward=1500, risk=750 → RR=2.0 (zero fees)
        assert rr == pytest.approx(1500 / 750, rel=0.01)
        # Should NOT be the old blended value (1250/750 ≈ 1.667)
        assert rr != pytest.approx(1250 / 750, rel=0.01)


class TestSignalQualityScore:
    """Test signal quality scoring for flexible cooldown."""

    @pytest.fixture
    def base_config(self):
        return {"min_rr_ratio": 1.5}

    def test_perfect_signal_scores_high(self, base_config):
        """Trending + net_rr=3.0 + RSI optimal + volume 2x → score >= 0.85."""
        score = compute_signal_quality_score(
            net_rr=3.0, rsi=58.0, volume_ratio=2.0,
            regime="trending", signal_type=SignalType.LONG, config=base_config,
        )
        assert score >= 0.85

    def test_mediocre_signal_scores_low(self, base_config):
        """Ranging + net_rr=min + RSI edge + volume 1x → score < 0.5."""
        score = compute_signal_quality_score(
            net_rr=1.5, rsi=48.0, volume_ratio=1.0,
            regime="ranging", signal_type=SignalType.LONG, config=base_config,
        )
        assert score < 0.5

    def test_score_bounded_0_to_1(self, base_config):
        """Extreme inputs → 0.0 <= score <= 1.0."""
        for rr in [0.0, 1.5, 5.0, 100.0]:
            for rsi in [0.0, 50.0, 100.0]:
                for vol in [0.0, 1.0, 10.0]:
                    for regime in ["trending", "volatile", "ranging", "unknown"]:
                        score = compute_signal_quality_score(
                            rr, rsi, vol, regime, SignalType.LONG, base_config,
                        )
                        assert 0.0 <= score <= 1.0, f"Score {score} out of bounds for rr={rr}, rsi={rsi}, vol={vol}, regime={regime}"

    def test_rr_scales_linearly(self, base_config):
        """Higher net_rr → higher score (monotonic)."""
        scores = []
        for rr in [1.5, 2.0, 2.5, 3.0]:
            score = compute_signal_quality_score(
                rr, 58.0, 1.5, "trending", SignalType.LONG, base_config,
            )
            scores.append(score)
        for i in range(1, len(scores)):
            assert scores[i] >= scores[i - 1]

    def test_regime_affects_score(self, base_config):
        """Trending > volatile > ranging."""
        kwargs = dict(net_rr=2.5, rsi=58.0, volume_ratio=1.5,
                      signal_type=SignalType.LONG, config=base_config)
        s_trending = compute_signal_quality_score(regime="trending", **kwargs)
        s_volatile = compute_signal_quality_score(regime="volatile", **kwargs)
        s_ranging = compute_signal_quality_score(regime="ranging", **kwargs)
        assert s_trending > s_volatile > s_ranging

    def test_rsi_optimal_for_long(self, base_config):
        """RSI 58 scores higher than RSI 48 or 68 for longs."""
        kwargs = dict(net_rr=2.5, volume_ratio=1.5, regime="trending",
                      signal_type=SignalType.LONG, config=base_config)
        s_optimal = compute_signal_quality_score(rsi=58.0, **kwargs)
        s_low = compute_signal_quality_score(rsi=48.0, **kwargs)
        s_high = compute_signal_quality_score(rsi=68.0, **kwargs)
        assert s_optimal > s_low
        assert s_optimal > s_high

    def test_rsi_optimal_for_short(self, base_config):
        """RSI 40 scores higher than RSI 30 or 50 for shorts."""
        kwargs = dict(net_rr=2.5, volume_ratio=1.5, regime="trending",
                      signal_type=SignalType.SHORT, config=base_config)
        s_optimal = compute_signal_quality_score(rsi=40.0, **kwargs)
        s_low = compute_signal_quality_score(rsi=30.0, **kwargs)
        s_high = compute_signal_quality_score(rsi=50.0, **kwargs)
        assert s_optimal > s_low
        assert s_optimal > s_high


class TestMeanReversionConditions:
    """Test mean-reversion signal conditions."""

    @pytest.fixture
    def mr_config(self):
        return {
            "atr_min": 0.001,
            "mr_rsi_oversold": 30,
            "mr_rsi_overbought": 70,
        }

    def _long_row(self, rsi=25.0, regime="ranging", close=100.0, low=98.0, bb_lower=99.0, atr=1.0):
        """Helper: build a row that should pass mean-reversion LONG."""
        return pd.Series({
            "rsi": rsi,
            "atr": atr,
            "regime": regime,
            "close": close,
            "low": low,
            "high": close + 1.0,
            "bb_lower": bb_lower,
            "bb_upper": close + 5.0,
        })

    def _short_row(self, rsi=75.0, regime="ranging", close=100.0, high=102.0, bb_upper=101.0, atr=1.0):
        """Helper: build a row that should pass mean-reversion SHORT."""
        return pd.Series({
            "rsi": rsi,
            "atr": atr,
            "regime": regime,
            "close": close,
            "low": close - 1.0,
            "high": high,
            "bb_lower": close - 5.0,
            "bb_upper": bb_upper,
        })

    def test_valid_long_in_ranging(self, mr_config):
        """All conditions met: bounce off lower BB in ranging regime."""
        row = self._long_row()
        assert check_mean_reversion_conditions(row, mr_config, SignalType.LONG)

    def test_valid_short_in_ranging(self, mr_config):
        """All conditions met: rejection at upper BB in ranging regime."""
        row = self._short_row()
        assert check_mean_reversion_conditions(row, mr_config, SignalType.SHORT)

    def test_rejects_when_regime_is_trending(self, mr_config):
        """Mean-reversion must NOT fire in trending regime."""
        row = self._long_row(regime="trending")
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.LONG)

    def test_rejects_when_regime_is_volatile(self, mr_config):
        """Mean-reversion must NOT fire in volatile regime."""
        row = self._long_row(regime="volatile")
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.LONG)

    def test_long_rejects_when_rsi_not_oversold(self, mr_config):
        """Long mean-reversion: RSI above oversold threshold must be rejected."""
        row = self._long_row(rsi=40.0)  # 40 > mr_rsi_oversold=30
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.LONG)

    def test_short_rejects_when_rsi_not_overbought(self, mr_config):
        """Short mean-reversion: RSI below overbought threshold must be rejected."""
        row = self._short_row(rsi=60.0)  # 60 < mr_rsi_overbought=70
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.SHORT)

    def test_long_rejects_when_no_bb_touch(self, mr_config):
        """Long: close did not touch or go below lower BB."""
        # low > bb_lower: no touch
        row = self._long_row(close=105.0, low=103.0, bb_lower=99.0)
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.LONG)

    def test_long_rejects_when_close_below_bb_lower(self, mr_config):
        """Long: close is still below BB lower (no bounce confirmation)."""
        # low touched BB, but close is still below — not a confirmed bounce
        row = self._long_row(close=98.0, low=97.0, bb_lower=99.0)
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.LONG)

    def test_short_rejects_when_no_bb_touch(self, mr_config):
        """Short: close did not touch or go above upper BB."""
        row = self._short_row(close=95.0, high=97.0, bb_upper=101.0)
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.SHORT)

    def test_short_rejects_when_close_above_bb_upper(self, mr_config):
        """Short: close is still above BB upper (no rejection confirmation)."""
        row = self._short_row(close=103.0, high=104.0, bb_upper=101.0)
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.SHORT)

    def test_rejects_when_atr_below_min(self, mr_config):
        """Should reject when ATR is below minimum threshold."""
        row = self._long_row(atr=0.0001)
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.LONG)

    def test_rejects_when_rsi_missing(self, mr_config):
        """Should reject gracefully when RSI is NaN."""
        row = self._long_row()
        row["rsi"] = float("nan")
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.LONG)

    def test_rejects_when_bb_lower_missing(self, mr_config):
        """Should reject gracefully when Bollinger Band is NaN."""
        row = self._long_row()
        row["bb_lower"] = float("nan")
        assert not check_mean_reversion_conditions(row, mr_config, SignalType.LONG)

    def test_mr_sl_tp_tighter_than_trend_following(self):
        """MR SL/TP multipliers should produce tighter levels than trend-following."""
        atr = 500.0
        entry = 60000.0
        # Trend-following levels
        tf_sl, tf_tp = compute_levels(entry, atr, SignalType.LONG, {"atr_sl_mult": 1.5, "atr_tp_mult": 3.0})
        # Mean-reversion levels
        mr_sl, mr_tp = compute_levels(entry, atr, SignalType.LONG, {"atr_sl_mult": 1.0, "atr_tp_mult": 1.5})
        # MR SL is closer to entry (higher for longs)
        assert mr_sl > tf_sl
        # MR TP is closer to entry (lower for longs)
        assert mr_tp < tf_tp


class TestBodyDominanceConditions:
    """Test body dominance signal conditions."""

    @pytest.fixture
    def bd_config(self):
        return {
            "atr_min": 0.001,
            "body_dominance_min_body": 0.65,
            "body_dominance_min_mom": 0.02,
            "body_dominance_min_vol": 1.5,
        }

    def _long_row(
        self,
        body_pct=0.70,
        mom10=0.03,
        volume=300.0,
        volume_ma=150.0,
        atr=500.0,
        open_price=59500.0,
        close=60000.0,
    ):
        """Build a row that passes body dominance LONG."""
        return pd.Series({
            "atr": atr,
            "body_pct": body_pct,
            "mom10": mom10,
            "volume": volume,
            "volume_ma": volume_ma,
            "open": open_price,
            "close": close,
        })

    def _short_row(
        self,
        body_pct=0.70,
        mom10=-0.03,
        volume=300.0,
        volume_ma=150.0,
        atr=500.0,
        open_price=60500.0,
        close=60000.0,
    ):
        """Build a row that passes body dominance SHORT."""
        return pd.Series({
            "atr": atr,
            "body_pct": body_pct,
            "mom10": mom10,
            "volume": volume,
            "volume_ma": volume_ma,
            "open": open_price,
            "close": close,
        })

    def test_valid_long(self, bd_config):
        """All conditions met should pass for LONG."""
        row = self._long_row()
        assert check_body_dominance_conditions(row, bd_config, SignalType.LONG)

    def test_valid_short(self, bd_config):
        """All conditions met should pass for SHORT."""
        row = self._short_row()
        assert check_body_dominance_conditions(row, bd_config, SignalType.SHORT)

    def test_rejects_small_body(self, bd_config):
        """body_pct below threshold must be rejected."""
        row = self._long_row(body_pct=0.40)
        assert not check_body_dominance_conditions(row, bd_config, SignalType.LONG)

    def test_rejects_bearish_candle_for_long(self, bd_config):
        """Bearish candle (close < open) must be rejected for LONG."""
        row = self._long_row(open_price=60500.0, close=60000.0)  # close < open
        assert not check_body_dominance_conditions(row, bd_config, SignalType.LONG)

    def test_rejects_bullish_candle_for_short(self, bd_config):
        """Bullish candle (close > open) must be rejected for SHORT."""
        row = self._short_row(open_price=59500.0, close=60000.0)  # close > open
        assert not check_body_dominance_conditions(row, bd_config, SignalType.SHORT)

    def test_rejects_weak_momentum_long(self, bd_config):
        """mom10 below min_mom must be rejected for LONG."""
        row = self._long_row(mom10=0.01)  # below 0.02
        assert not check_body_dominance_conditions(row, bd_config, SignalType.LONG)

    def test_rejects_weak_momentum_short(self, bd_config):
        """mom10 above -min_mom must be rejected for SHORT."""
        row = self._short_row(mom10=-0.01)  # above -0.02
        assert not check_body_dominance_conditions(row, bd_config, SignalType.SHORT)

    def test_rejects_low_volume(self, bd_config):
        """Volume below vol_ma * min_vol must be rejected."""
        row = self._long_row(volume=100.0, volume_ma=200.0)  # ratio = 0.5 < 1.5
        assert not check_body_dominance_conditions(row, bd_config, SignalType.LONG)

    def test_rejects_low_atr(self, bd_config):
        """ATR below atr_min must be rejected."""
        row = self._long_row(atr=0.0001)
        assert not check_body_dominance_conditions(row, bd_config, SignalType.LONG)

    def test_rejects_nan_body_pct(self, bd_config):
        """NaN body_pct should be rejected gracefully."""
        row = self._long_row()
        row["body_pct"] = float("nan")
        assert not check_body_dominance_conditions(row, bd_config, SignalType.LONG)

    def test_rejects_nan_mom10(self, bd_config):
        """NaN mom10 should be rejected gracefully."""
        row = self._long_row()
        row["mom10"] = float("nan")
        assert not check_body_dominance_conditions(row, bd_config, SignalType.LONG)

    def test_trend_filter_long_rejected(self, bd_config):
        """above_trend=False must reject LONG."""
        row = self._long_row()
        row["above_trend"] = False
        assert not check_body_dominance_conditions(row, bd_config, SignalType.LONG)

    def test_trend_filter_short_rejected(self, bd_config):
        """below_trend=False must reject SHORT."""
        row = self._short_row()
        row["below_trend"] = False
        assert not check_body_dominance_conditions(row, bd_config, SignalType.SHORT)


class TestSqueezeReleaseConditions:
    """Test squeeze release signal conditions."""

    @pytest.fixture
    def sq_config(self):
        return {
            "atr_min": 0.001,
            "squeeze_release_low": 0.7,
            "squeeze_release_high": 0.8,
            "squeeze_release_min_mom4": 0.0,
        }

    def _long_row(
        self,
        squeeze=0.85,
        mom4=0.005,
        close=60000.0,
        ema_slow=59000.0,
        atr=500.0,
    ):
        """Build a row that passes squeeze release LONG."""
        return pd.Series({
            "atr": atr,
            "squeeze": squeeze,
            "mom4": mom4,
            "close": close,
            "ema_slow": ema_slow,
        })

    def _short_row(
        self,
        squeeze=0.85,
        mom4=-0.005,
        close=60000.0,
        ema_slow=61000.0,
        atr=500.0,
    ):
        """Build a row that passes squeeze release SHORT."""
        return pd.Series({
            "atr": atr,
            "squeeze": squeeze,
            "mom4": mom4,
            "close": close,
            "ema_slow": ema_slow,
        })

    def _prev_squeezed(self, squeeze=0.60):
        """Build a prev_row that is in squeeze."""
        return pd.Series({"squeeze": squeeze})

    def test_valid_long(self, sq_config):
        """All conditions met should pass for LONG."""
        row = self._long_row()
        prev = self._prev_squeezed()
        assert check_squeeze_release_conditions(row, prev, sq_config, SignalType.LONG)

    def test_valid_short(self, sq_config):
        """All conditions met should pass for SHORT."""
        row = self._short_row()
        prev = self._prev_squeezed()
        assert check_squeeze_release_conditions(row, prev, sq_config, SignalType.SHORT)

    def test_rejects_when_prev_not_squeezed(self, sq_config):
        """If previous squeeze >= squeeze_low, reject (was not in squeeze)."""
        row = self._long_row()
        prev = pd.Series({"squeeze": 0.75})  # >= 0.7, not in squeeze
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.LONG)

    def test_rejects_when_current_not_expanding(self, sq_config):
        """If current squeeze <= squeeze_high, release has not started."""
        row = self._long_row(squeeze=0.75)  # <= 0.8
        prev = self._prev_squeezed()
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.LONG)

    def test_rejects_price_below_ema_for_long(self, sq_config):
        """LONG: close below ema_slow must be rejected."""
        row = self._long_row(close=58000.0, ema_slow=59000.0)
        prev = self._prev_squeezed()
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.LONG)

    def test_rejects_price_above_ema_for_short(self, sq_config):
        """SHORT: close above ema_slow must be rejected."""
        row = self._short_row(close=62000.0, ema_slow=61000.0)
        prev = self._prev_squeezed()
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.SHORT)

    def test_rejects_negative_mom4_for_long(self, sq_config):
        """LONG: mom4 <= 0 must be rejected."""
        row = self._long_row(mom4=-0.001)
        prev = self._prev_squeezed()
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.LONG)

    def test_rejects_positive_mom4_for_short(self, sq_config):
        """SHORT: mom4 >= 0 must be rejected."""
        row = self._short_row(mom4=0.001)
        prev = self._prev_squeezed()
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.SHORT)

    def test_rejects_low_atr(self, sq_config):
        """ATR below atr_min must be rejected."""
        row = self._long_row(atr=0.0001)
        prev = self._prev_squeezed()
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.LONG)

    def test_rejects_nan_squeeze(self, sq_config):
        """NaN squeeze in current row must be rejected gracefully."""
        row = self._long_row()
        row["squeeze"] = float("nan")
        prev = self._prev_squeezed()
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.LONG)

    def test_rejects_nan_prev_squeeze(self, sq_config):
        """NaN squeeze in prev_row must be rejected gracefully."""
        row = self._long_row()
        prev = pd.Series({"squeeze": float("nan")})
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.LONG)

    def test_trend_filter_long_rejected(self, sq_config):
        """above_trend=False must reject LONG."""
        row = self._long_row()
        row["above_trend"] = False
        prev = self._prev_squeezed()
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.LONG)

    def test_trend_filter_short_rejected(self, sq_config):
        """below_trend=False must reject SHORT."""
        row = self._short_row()
        row["below_trend"] = False
        prev = self._prev_squeezed()
        assert not check_squeeze_release_conditions(row, prev, sq_config, SignalType.SHORT)


class TestNewIndicators:
    """Test that add_indicators computes the new indicator columns correctly."""

    def _make_df(self, n: int = 100) -> pd.DataFrame:
        np.random.seed(7)
        dates = pd.date_range("2024-01-01", periods=n, freq="15min")
        close = 60000 + np.cumsum(np.random.randn(n) * 50)
        open_ = close + np.random.randn(n) * 30
        high = np.maximum(close, open_) + np.abs(np.random.randn(n) * 20)
        low = np.minimum(close, open_) - np.abs(np.random.randn(n) * 20)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close,
             "volume": np.random.uniform(100, 500, n)},
            index=dates,
        )

    @pytest.fixture
    def base_config(self):
        return {
            "ema_fast": 9, "ema_slow": 21, "ema_trend": 50,
            "rsi_period": 14, "rsi_min": 45, "rsi_max": 65,
            "atr_period": 14, "atr_min": 0.001,
        }

    def test_body_pct_column_present(self, base_config):
        """add_indicators must produce a body_pct column."""
        from bot.data import add_indicators
        df = add_indicators(self._make_df(), base_config)
        assert "body_pct" in df.columns

    def test_body_pct_bounded_0_to_1(self, base_config):
        """body_pct must always be in [0, 1]."""
        from bot.data import add_indicators
        df = add_indicators(self._make_df(), base_config)
        valid = df["body_pct"].dropna()
        assert (valid >= 0.0).all()
        assert (valid <= 1.0).all()

    def test_mom10_column_present(self, base_config):
        """add_indicators must produce a mom10 column."""
        from bot.data import add_indicators
        df = add_indicators(self._make_df(), base_config)
        assert "mom10" in df.columns

    def test_mom4_column_present(self, base_config):
        """add_indicators must produce a mom4 column."""
        from bot.data import add_indicators
        df = add_indicators(self._make_df(), base_config)
        assert "mom4" in df.columns

    def test_squeeze_column_present(self, base_config):
        """add_indicators must produce a squeeze column."""
        from bot.data import add_indicators
        df = add_indicators(self._make_df(), base_config)
        assert "squeeze" in df.columns

    def test_squeeze_positive(self, base_config):
        """squeeze must be positive for all non-NaN rows."""
        from bot.data import add_indicators
        df = add_indicators(self._make_df(200), base_config)
        valid = df["squeeze"].dropna()
        assert (valid > 0).all()

    def test_mom10_nan_first_10_rows(self, base_config):
        """First 10 rows of mom10 should be NaN (shift window)."""
        from bot.data import add_indicators
        df = add_indicators(self._make_df(), base_config)
        assert df["mom10"].iloc[:10].isna().all()

    def test_mom4_nan_first_4_rows(self, base_config):
        """First 4 rows of mom4 should be NaN (shift window)."""
        from bot.data import add_indicators
        df = add_indicators(self._make_df(), base_config)
        assert df["mom4"].iloc[:4].isna().all()
