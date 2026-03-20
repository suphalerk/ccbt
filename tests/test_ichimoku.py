"""Tests for Ichimoku Cloud indicator and signal generation."""

import json

import numpy as np
import pandas as pd
import pytest

from bot.data import add_ichimoku_indicators, add_indicators
from bot.strategy import (
    SignalType,
    check_ichimoku_conditions,
)

# Minimum bars required before cloud_top/cloud_bottom are valid:
# - span_a_raw needs kijun (26) bars, first valid at index 25 (0-indexed)
# - span_a_raw.shift(26) first valid at index 51
# - cloud_top = max(span_a_shifted, span_b_shifted) with skipna=True
# So cloud_top first valid at index 51; bars 0..50 produce NaN.
# check_ichimoku_conditions() guards on NaN, so no signals before index 51.
ICHIMOKU_WARMUP_ROWS = 51  # First index where cloud_top is valid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**overrides) -> dict:
    """Minimal config with Ichimoku enabled."""
    base = {
        "ema_fast": 9,
        "ema_slow": 21,
        "ema_trend": 50,
        "rsi_period": 14,
        "rsi_min": 45,
        "rsi_max": 65,
        "atr_period": 14,
        "atr_sl_mult": 2.5,
        "atr_tp_mult": 0,
        "atr_min": 0.0,
        "volume_mult": 1.0,
        "min_rr_ratio": 0,
        "ichimoku_tenkan": 9,
        "ichimoku_kijun": 26,
        "ichimoku_senkou_b": 52,
        "signals": {
            "ema_crossover": {"enabled": False},
            "ema_fast_crossover": {"enabled": False},
            "ema_pullback": {"enabled": False},
            "rsi_divergence": {"enabled": False},
            "bb_breakout": {"enabled": False},
            "mean_reversion": {"enabled": False},
            "body_dominance": {"enabled": False},
            "squeeze_release": {"enabled": False},
            "ichimoku_cloud": {"enabled": True},
        },
    }
    base.update(overrides)
    return base


def make_trending_df(n: int = 200, direction: str = "up", seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV with a clear trend.

    Args:
        n: Number of candles.
        direction: 'up' or 'down'.
        seed: Random seed for reproducibility.

    Returns:
        DataFrame with OHLCV columns indexed by timestamp.
    """
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="15min")

    if direction == "up":
        base = 1800 + np.cumsum(np.random.randn(n) * 2 + 0.5)
    else:
        base = 1800 + np.cumsum(np.random.randn(n) * 2 - 0.5)

    noise = np.abs(np.random.randn(n)) * 1.5
    high = base + noise
    low = base - noise

    df = pd.DataFrame(
        {
            "open": base + np.random.randn(n) * 0.5,
            "high": high,
            "low": low,
            "close": base,
            "volume": np.random.uniform(1000, 5000, n),
        },
        index=dates,
    )
    # Ensure OHLC consistency
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


def make_flat_df(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Generate flat/ranging OHLCV data — price oscillates around a mean."""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="15min")

    base = 1800 + np.random.randn(n) * 3  # Flat with noise
    noise = np.abs(np.random.randn(n)) * 1.5
    high = base + noise
    low = base - noise

    df = pd.DataFrame(
        {
            "open": base + np.random.randn(n) * 0.3,
            "high": high,
            "low": low,
            "close": base,
            "volume": np.random.uniform(1000, 5000, n),
        },
        index=dates,
    )
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


# ---------------------------------------------------------------------------
# T1 tests: indicator computation
# ---------------------------------------------------------------------------

class TestIchimokuIndicators:
    """Test that Ichimoku indicator columns are computed correctly."""

    def test_columns_are_added(self):
        """All expected columns appear after add_ichimoku_indicators()."""
        df = make_trending_df(n=100)
        config = make_config()
        result = add_ichimoku_indicators(df.copy(), config)

        for col in ("tenkan", "kijun", "span_a", "span_b", "cloud_top", "cloud_bottom"):
            assert col in result.columns, f"Missing column: {col}"

    def test_tenkan_known_value(self):
        """Tenkan-sen equals (rolling_max_high + rolling_min_low) / 2."""
        df = make_trending_df(n=100)
        config = make_config()
        result = add_ichimoku_indicators(df.copy(), config)

        period = config["ichimoku_tenkan"]
        # Check a specific row (index 50)
        i = 50
        expected = (
            df["high"].iloc[i - period + 1 : i + 1].max()
            + df["low"].iloc[i - period + 1 : i + 1].min()
        ) / 2
        assert abs(result["tenkan"].iloc[i] - expected) < 1e-9

    def test_kijun_known_value(self):
        """Kijun-sen equals (rolling_max_high + rolling_min_low) / 2 over kijun period."""
        df = make_trending_df(n=100)
        config = make_config()
        result = add_ichimoku_indicators(df.copy(), config)

        period = config["ichimoku_kijun"]
        i = 80
        expected = (
            df["high"].iloc[i - period + 1 : i + 1].max()
            + df["low"].iloc[i - period + 1 : i + 1].min()
        ) / 2
        assert abs(result["kijun"].iloc[i] - expected) < 1e-9

    def test_cloud_top_ge_cloud_bottom(self):
        """cloud_top must always be >= cloud_bottom where not NaN."""
        df = make_trending_df(n=200)
        config = make_config()
        result = add_ichimoku_indicators(df.copy(), config)

        valid = result.dropna(subset=["cloud_top", "cloud_bottom"])
        assert (valid["cloud_top"] >= valid["cloud_bottom"]).all(), (
            "cloud_top must be >= cloud_bottom at every row"
        )

    def test_span_a_is_midpoint_of_tenkan_kijun(self):
        """Span A raw = (tenkan + kijun) / 2."""
        df = make_trending_df(n=100)
        config = make_config()
        result = add_ichimoku_indicators(df.copy(), config)

        valid = result.dropna(subset=["span_a", "tenkan", "kijun"])
        expected_span_a = (valid["tenkan"] + valid["kijun"]) / 2
        diff = (valid["span_a"] - expected_span_a).abs().max()
        assert diff < 1e-9, f"span_a deviation too large: {diff}"

    def test_no_ichimoku_without_config_key(self):
        """Ichimoku is NOT computed when ichimoku_tenkan is absent from config."""
        df = make_trending_df(n=100)
        # Use a config without ichimoku keys — add_indicators should skip them
        config = {
            "ema_fast": 9,
            "ema_slow": 21,
            "rsi_period": 14,
            "rsi_min": 45,
            "rsi_max": 65,
            "atr_period": 14,
            "atr_sl_mult": 1.5,
            "atr_tp_mult": 3.0,
            "atr_min": 0.001,
            "volume_mult": 1.0,
        }
        result = add_indicators(df.copy(), config)
        assert "tenkan" not in result.columns
        assert "cloud_top" not in result.columns

    def test_ichimoku_computed_via_add_indicators(self):
        """add_indicators() delegates to add_ichimoku_indicators when key present."""
        df = make_trending_df(n=200)
        config = make_config()
        result = add_indicators(df.copy(), config)
        assert "tenkan" in result.columns
        assert "cloud_top" in result.columns


# ---------------------------------------------------------------------------
# T2 tests: warmup guard
# ---------------------------------------------------------------------------

class TestIchimokuWarmup:
    """Ensure no signals are generated before bar 78."""

    def test_no_signal_before_warmup(self):
        """check_ichimoku_conditions returns False for rows where cloud is NaN."""
        df = make_trending_df(n=200)
        config = make_config()
        df_with_indicators = add_ichimoku_indicators(df.copy(), config)

        # Rows before warmup: cloud_top/cloud_bottom will be NaN
        for i in range(1, ICHIMOKU_WARMUP_ROWS):
            row = df_with_indicators.iloc[i]
            prev_row = df_with_indicators.iloc[i - 1]
            # Should return False because cloud values are NaN
            assert not check_ichimoku_conditions(row, prev_row, config, SignalType.LONG), (
                f"Should not fire before warmup at bar {i}"
            )

    def test_cloud_values_nan_before_warmup(self):
        """cloud_top and cloud_bottom are NaN for the first ~51 bars.

        cloud_top = max(span_a.shift(26), span_b.shift(26)) with skipna=True.
        span_a_raw first valid at index 25 (needs kijun=26 bars, 0-indexed).
        span_a_raw.shift(26) first valid at index 51.
        So cloud_top is NaN for indices 0..50, first valid at index 51.
        """
        df = make_trending_df(n=200)
        config = make_config()
        result = add_ichimoku_indicators(df.copy(), config)

        # Verify row 49 (0-indexed) is still NaN — well before warmup ends
        assert pd.isna(result["cloud_top"].iloc[49]), (
            "cloud_top should be NaN at index 49 (before warmup)"
        )
        assert pd.isna(result["cloud_top"].iloc[50]), (
            "cloud_top should be NaN at index 50 (last NaN row)"
        )
        # Index 51 onwards should be valid
        assert not pd.isna(result["cloud_top"].iloc[51]), (
            "cloud_top should be valid at index 51 (first valid bar)"
        )


# ---------------------------------------------------------------------------
# T3 tests: LONG signal conditions
# ---------------------------------------------------------------------------

class TestIchimokuLongSignal:
    """Test LONG signal fires on a clean Tenkan/Kijun crossover above cloud."""

    def _make_long_setup(self):
        """Build a row + prev_row that satisfies all LONG conditions.

        Returns:
            (row, prev_row, config)
        """
        config = make_config()
        # Previous row: tenkan <= kijun (no crossover yet)
        prev_row = pd.Series({
            "tenkan": 1799.0,
            "kijun": 1800.0,   # tenkan <= kijun
            "cloud_top": 1750.0,
            "cloud_bottom": 1740.0,
            "atr": 5.0,
            "close": 1802.0,
        })
        # Current row: tenkan > kijun (crossover), close > cloud_top
        row = pd.Series({
            "tenkan": 1801.0,
            "kijun": 1800.0,   # tenkan > kijun — crossover!
            "cloud_top": 1750.0,
            "cloud_bottom": 1740.0,
            "atr": 5.0,
            "close": 1820.0,   # above cloud_top
        })
        return row, prev_row, config

    def test_long_signal_fires(self):
        """LONG fires when tenkan crosses above kijun and close > cloud_top."""
        row, prev_row, config = self._make_long_setup()
        assert check_ichimoku_conditions(row, prev_row, config, SignalType.LONG)

    def test_long_no_signal_without_crossover(self):
        """LONG does NOT fire when tenkan was already above kijun (no crossover)."""
        row, prev_row, config = self._make_long_setup()
        # Prev row: tenkan already above kijun
        prev_row = prev_row.copy()
        prev_row["tenkan"] = 1801.5  # Was already above kijun
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.LONG)

    def test_long_no_signal_when_price_inside_cloud(self):
        """LONG does NOT fire when close is inside the cloud."""
        row, prev_row, config = self._make_long_setup()
        row = row.copy()
        row["close"] = 1745.0  # inside cloud (bottom=1740, top=1750)
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.LONG)

    def test_long_no_signal_when_price_below_cloud(self):
        """LONG does NOT fire when close is below cloud_top."""
        row, prev_row, config = self._make_long_setup()
        row = row.copy()
        row["close"] = 1730.0  # below cloud_bottom
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.LONG)

    def test_long_no_signal_atr_too_small(self):
        """LONG does NOT fire when ATR is below atr_min."""
        row, prev_row, config = self._make_long_setup()
        config = {**config, "atr_min": 10.0}
        row = row.copy()
        row["atr"] = 5.0  # below atr_min
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.LONG)


# ---------------------------------------------------------------------------
# T4 tests: SHORT signal conditions
# ---------------------------------------------------------------------------

class TestIchimokuShortSignal:
    """Test SHORT signal fires on a clean Tenkan/Kijun crossover below cloud."""

    def _make_short_setup(self):
        """Build a row + prev_row that satisfies all SHORT conditions."""
        config = make_config()
        # Previous row: tenkan >= kijun (no crossover yet)
        prev_row = pd.Series({
            "tenkan": 1801.0,
            "kijun": 1800.0,   # tenkan >= kijun
            "cloud_top": 1850.0,
            "cloud_bottom": 1840.0,
            "atr": 5.0,
            "close": 1830.0,
        })
        # Current row: tenkan < kijun (crossover), close < cloud_bottom
        row = pd.Series({
            "tenkan": 1799.0,
            "kijun": 1800.0,   # tenkan < kijun — crossover!
            "cloud_top": 1850.0,
            "cloud_bottom": 1840.0,
            "atr": 5.0,
            "close": 1820.0,   # below cloud_bottom
        })
        return row, prev_row, config

    def test_short_signal_fires(self):
        """SHORT fires when tenkan crosses below kijun and close < cloud_bottom."""
        row, prev_row, config = self._make_short_setup()
        assert check_ichimoku_conditions(row, prev_row, config, SignalType.SHORT)

    def test_short_no_signal_without_crossover(self):
        """SHORT does NOT fire when tenkan was already below kijun (no crossover)."""
        row, prev_row, config = self._make_short_setup()
        prev_row = prev_row.copy()
        prev_row["tenkan"] = 1798.5  # Was already below kijun
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.SHORT)

    def test_short_no_signal_when_price_inside_cloud(self):
        """SHORT does NOT fire when close is inside the cloud."""
        row, prev_row, config = self._make_short_setup()
        row = row.copy()
        row["close"] = 1845.0  # inside cloud
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.SHORT)

    def test_short_no_signal_when_price_above_cloud(self):
        """SHORT does NOT fire when close is above cloud_bottom."""
        row, prev_row, config = self._make_short_setup()
        row = row.copy()
        row["close"] = 1860.0  # above cloud_top
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.SHORT)

    def test_short_no_signal_atr_too_small(self):
        """SHORT does NOT fire when ATR is below atr_min."""
        row, prev_row, config = self._make_short_setup()
        config = {**config, "atr_min": 10.0}
        row = row.copy()
        row["atr"] = 5.0
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.SHORT)


# ---------------------------------------------------------------------------
# T5 tests: no signal when price inside cloud
# ---------------------------------------------------------------------------

class TestIchimokuNoSignalInsideCloud:
    """Price inside the cloud should never generate a signal in either direction."""

    def test_long_rejected_when_in_cloud(self):
        """LONG is rejected even with tenkan crossover when price is in cloud."""
        config = make_config()
        prev_row = pd.Series({
            "tenkan": 1799.0, "kijun": 1800.0,
            "cloud_top": 1830.0, "cloud_bottom": 1810.0,
            "atr": 5.0, "close": 1815.0,
        })
        row = pd.Series({
            "tenkan": 1801.0, "kijun": 1800.0,  # crossover
            "cloud_top": 1830.0, "cloud_bottom": 1810.0,
            "atr": 5.0, "close": 1820.0,  # inside cloud
        })
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.LONG)

    def test_short_rejected_when_in_cloud(self):
        """SHORT is rejected even with tenkan crossover when price is in cloud."""
        config = make_config()
        prev_row = pd.Series({
            "tenkan": 1801.0, "kijun": 1800.0,
            "cloud_top": 1830.0, "cloud_bottom": 1810.0,
            "atr": 5.0, "close": 1815.0,
        })
        row = pd.Series({
            "tenkan": 1799.0, "kijun": 1800.0,  # crossover
            "cloud_top": 1830.0, "cloud_bottom": 1810.0,
            "atr": 5.0, "close": 1820.0,  # inside cloud
        })
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.SHORT)

    def test_no_signal_with_nan_cloud(self):
        """No signal when cloud values are NaN (pre-warmup rows)."""
        config = make_config()
        prev_row = pd.Series({
            "tenkan": 1799.0, "kijun": 1800.0,
            "cloud_top": float("nan"), "cloud_bottom": float("nan"),
            "atr": 5.0, "close": 1820.0,
        })
        row = pd.Series({
            "tenkan": 1801.0, "kijun": 1800.0,
            "cloud_top": float("nan"), "cloud_bottom": float("nan"),
            "atr": 5.0, "close": 1820.0,
        })
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.LONG)
        assert not check_ichimoku_conditions(row, prev_row, config, SignalType.SHORT)


# ---------------------------------------------------------------------------
# T6 tests: config loading
# ---------------------------------------------------------------------------

class TestIchimokuConfigLoading:
    """Test that gold configs load correctly and have expected Ichimoku settings."""

    def test_config_gold_has_ichimoku_keys(self):
        """config_gold.json has all required Ichimoku parameters."""
        with open("/Users/iceai/Work/ccbt/config_gold.json") as f:
            config = json.load(f)

        assert config.get("ichimoku_tenkan") == 9
        assert config.get("ichimoku_kijun") == 26
        assert config.get("ichimoku_senkou_b") == 52
        assert config["signals"]["ichimoku_cloud"]["enabled"] is True
        assert config["signals"]["ema_crossover"]["enabled"] is False
        assert config["signals"]["ema_fast_crossover"]["enabled"] is False
        assert config.get("atr_sl_mult") == 2.5
        assert config.get("atr_tp_mult") == 0
        assert config.get("atr_trail_mult") == 3.0
        assert config.get("min_rr_ratio") == 0
        assert config["trading_hours"]["enabled"] is True
        assert config["trading_hours"]["start_utc"] == 8
        assert config["trading_hours"]["end_utc"] == 20

    def test_config_gold_forex_has_ichimoku_keys(self):
        """config_gold_forex.json has all required Ichimoku parameters."""
        with open("/Users/iceai/Work/ccbt/config_gold_forex.json") as f:
            config = json.load(f)

        assert config.get("ichimoku_tenkan") == 9
        assert config.get("ichimoku_kijun") == 26
        assert config.get("ichimoku_senkou_b") == 52
        assert config["signals"]["ichimoku_cloud"]["enabled"] is True
        assert config["signals"]["ema_crossover"]["enabled"] is False
        assert config["signals"]["ema_fast_crossover"]["enabled"] is False
        assert config.get("atr_sl_mult") == 2.5
        assert config.get("atr_tp_mult") == 0
        assert config.get("min_rr_ratio") == 0

    def test_config_gold_is_valid_json(self):
        """config_gold.json is parseable JSON."""
        with open("/Users/iceai/Work/ccbt/config_gold.json") as f:
            config = json.load(f)
        assert isinstance(config, dict)

    def test_config_gold_forex_is_valid_json(self):
        """config_gold_forex.json is parseable JSON."""
        with open("/Users/iceai/Work/ccbt/config_gold_forex.json") as f:
            config = json.load(f)
        assert isinstance(config, dict)
