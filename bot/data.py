"""OHLCV data fetching and technical indicator calculations."""

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute Exponential Moving Average.

    Args:
        series: Price series.
        period: EMA period.

    Returns:
        EMA series.
    """
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute Relative Strength Index.

    Args:
        series: Price series (typically close).
        period: RSI lookback period.

    Returns:
        RSI series (0-100).
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Compute Average True Range.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: ATR lookback period.

    Returns:
        ATR series.
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(span=period, adjust=False).mean()


def compute_volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
    """Compute Volume Moving Average.

    Args:
        volume: Volume series.
        period: MA period.

    Returns:
        Volume MA series.
    """
    return volume.rolling(window=period).mean()


def add_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add all required technical indicators to an OHLCV DataFrame.

    Args:
        df: DataFrame with open, high, low, close, volume columns.
        config: Bot configuration with indicator parameters.

    Returns:
        DataFrame with added indicator columns.
    """
    df = df.copy()

    # EMAs
    df["ema_fast"] = compute_ema(df["close"], config["ema_fast"])
    df["ema_slow"] = compute_ema(df["close"], config["ema_slow"])

    # RSI
    df["rsi"] = compute_rsi(df["close"], config["rsi_period"])

    # ATR
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], config["atr_period"])

    # Volume MA
    df["volume_ma"] = compute_volume_ma(df["volume"])

    # EMA crossover detection
    # Suppress crossovers during EMA warmup period to avoid spurious signals
    # Add buffer beyond ema_slow for better EMA convergence
    warmup = max(config["ema_fast"], config["ema_slow"]) + 10
    df["ema_cross_up"] = (df["ema_fast"] > df["ema_slow"]) & (
        df["ema_fast"].shift(1) <= df["ema_slow"].shift(1)
    )
    df["ema_cross_down"] = (df["ema_fast"] < df["ema_slow"]) & (
        df["ema_fast"].shift(1) >= df["ema_slow"].shift(1)
    )
    # Zero out crossovers in warmup rows
    df.iloc[:warmup, df.columns.get_loc("ema_cross_up")] = False
    df.iloc[:warmup, df.columns.get_loc("ema_cross_down")] = False

    logger.info("indicators_computed", extra={"rows": len(df)})
    return df


def detect_regime(df: pd.DataFrame, atr_period: int = 14, lookback: int = 20) -> str:
    """Detect market regime based on ATR ratio and price structure.

    Uses only closed candles (excludes the forming candle at iloc[-1])
    to be consistent with signal generation which uses iloc[-2].

    Args:
        df: DataFrame with high, low, close columns (and ideally pre-computed ATR).
        atr_period: Period for ATR calculation.
        lookback: Lookback window for ATR SMA and directional analysis.

    Returns:
        "trending", "ranging", or "volatile".
    """
    # Exclude the forming candle to avoid look-ahead bias
    df_closed = df.iloc[:-1] if len(df) > 1 else df

    if len(df_closed) < atr_period + lookback:
        return "ranging"  # Not enough data, be conservative

    # Compute ATR if not already present
    if "atr" in df_closed.columns:
        atr = df_closed["atr"]
    else:
        atr = compute_atr(df_closed["high"], df_closed["low"], df_closed["close"], atr_period)

    current_atr = atr.iloc[-1]
    if pd.isna(current_atr) or current_atr == 0:
        return "ranging"

    atr_sma = atr.rolling(window=lookback).mean()
    current_atr_sma = atr_sma.iloc[-1]
    if pd.isna(current_atr_sma) or current_atr_sma == 0:
        return "ranging"

    atr_ratio = current_atr / current_atr_sma

    # Directional movement: check for consistent higher highs/higher lows
    # or lower highs/lower lows over the lookback window
    recent = df_closed.iloc[-lookback:]
    highs = recent["high"].values
    lows = recent["low"].values

    higher_highs = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    lower_lows = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])

    total_comparisons = len(highs) - 1
    if total_comparisons == 0:
        return "ranging"

    # Clear direction if >60% of bars show consistent HH/HL or LH/LL
    up_score = (higher_highs + higher_lows) / (2 * total_comparisons)
    down_score = (lower_highs + lower_lows) / (2 * total_comparisons)
    has_clear_direction = up_score > 0.6 or down_score > 0.6

    if atr_ratio > 1.5:
        return "volatile"
    elif atr_ratio < 0.8 and not has_clear_direction:
        return "ranging"
    elif 0.8 <= atr_ratio <= 1.5 and has_clear_direction:
        return "trending"
    else:
        # Edge cases: moderate ATR but no direction, or low ATR with direction
        if has_clear_direction:
            return "trending"
        return "ranging"


def add_trend_filter(
    df: pd.DataFrame, trend_df: pd.DataFrame, config: dict
) -> pd.DataFrame:
    """Add trend filter from higher timeframe data.

    Merges the 1h EMA(50) trend filter into the signal timeframe DataFrame
    using forward-fill alignment.

    Args:
        df: Signal timeframe DataFrame (e.g., 15m).
        trend_df: Trend timeframe DataFrame (e.g., 1h) with OHLCV data.
        config: Bot configuration.

    Returns:
        Signal DataFrame with 'ema_trend' and 'above_trend' columns added.
    """
    df = df.copy()
    trend_df = trend_df.copy()

    trend_df["ema_trend"] = compute_ema(trend_df["close"], config["ema_trend"])
    trend_ema = trend_df[["ema_trend"]].rename(columns={"ema_trend": "ema_trend_1h"})

    # Merge with backward-looking alignment to avoid look-ahead bias
    df = pd.merge_asof(
        df.reset_index(), trend_ema.reset_index(),
        on="timestamp" if "timestamp" in df.reset_index().columns else df.reset_index().columns[0],
        direction="backward",
    )
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    df["ema_trend_1h"] = df["ema_trend_1h"].ffill()
    df["above_trend"] = df["close"] > df["ema_trend_1h"]
    df["below_trend"] = df["close"] < df["ema_trend_1h"]

    return df
