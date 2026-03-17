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
    df["ema_cross_up"] = (df["ema_fast"] > df["ema_slow"]) & (
        df["ema_fast"].shift(1) <= df["ema_slow"].shift(1)
    )
    df["ema_cross_down"] = (df["ema_fast"] < df["ema_slow"]) & (
        df["ema_fast"].shift(1) >= df["ema_slow"].shift(1)
    )

    logger.info("indicators_computed", extra={"rows": len(df)})
    return df


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
