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

    # EMA slope: rate of change of slow EMA over last N candles (%)
    # Used to filter crossovers when the slow EMA is flat (whipsaw risk)
    ema_slope_period = config.get("ema_slope_period", 5)
    df["ema_slope"] = (
        df["ema_slow"].diff(ema_slope_period)
        / df["ema_slow"].shift(ema_slope_period)
        * 100
    )

    # Bollinger Bands
    bb_period = config.get("bb_period", 20)
    bb_std = config.get("bb_std", 2.0)
    df["bb_mid"] = df["close"].rolling(window=bb_period).mean()
    df["bb_std_val"] = df["close"].rolling(window=bb_period).std()
    df["bb_upper"] = df["bb_mid"] + bb_std * df["bb_std_val"]
    df["bb_lower"] = df["bb_mid"] - bb_std * df["bb_std_val"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    # BB width percentile: is current squeeze unusually tight?
    df["bb_width_pct"] = df["bb_width"].rolling(window=120).rank(pct=True)

    # Swing high/low detection for RSI divergence
    swing_lookback = config.get("swing_lookback", 5)
    window_size = 2 * swing_lookback + 1
    df["swing_low"] = df["low"] == df["low"].rolling(window=window_size, center=True).min()
    df["swing_high"] = df["high"] == df["high"].rolling(window=window_size, center=True).max()

    # EMA pullback detection — tighter proximity threshold (0.1% default, configurable)
    pullback_pct = config.get("pullback_proximity_pct", 0.001)
    long_pullback = (
        (df["low"] <= df["ema_slow"] * (1 + pullback_pct)) &  # Price touched within pct of EMA slow
        (df["close"] > df["ema_slow"]) &                       # But closed above
        (df["ema_fast"] > df["ema_slow"])                      # Trend is bullish
    )
    short_pullback = (
        (df["high"] >= df["ema_slow"] * (1 - pullback_pct)) &  # Price touched within pct of EMA slow
        (df["close"] < df["ema_slow"]) &                        # But closed below
        (df["ema_fast"] < df["ema_slow"])                       # Trend is bearish
    )

    # Require trend established for N candles before pullback is valid
    pullback_trend_bars = config.get("pullback_trend_bars", 10)
    ema_bull_trend = (df["ema_fast"] > df["ema_slow"]).rolling(window=pullback_trend_bars).min().astype(bool)
    ema_bear_trend = (df["ema_fast"] < df["ema_slow"]).rolling(window=pullback_trend_bars).min().astype(bool)
    df["pullback_long"] = long_pullback & ema_bull_trend
    df["pullback_short"] = short_pullback & ema_bear_trend

    # Secondary fast EMA pair for more crossover opportunities
    ema_fast2_period = config.get("ema_fast2", 5)
    ema_slow2_period = config.get("ema_slow2", 13)
    if ema_fast2_period and ema_slow2_period:
        df["ema_fast2"] = compute_ema(df["close"], ema_fast2_period)
        df["ema_slow2"] = compute_ema(df["close"], ema_slow2_period)

        crossover_lookback2 = config.get("crossover_lookback", 2)
        exact_cross_up2 = (df["ema_fast2"] > df["ema_slow2"]) & (
            df["ema_fast2"].shift(1) <= df["ema_slow2"].shift(1)
        )
        exact_cross_down2 = (df["ema_fast2"] < df["ema_slow2"]) & (
            df["ema_fast2"].shift(1) >= df["ema_slow2"].shift(1)
        )
        df["ema_cross_up2"] = (
            exact_cross_up2.rolling(window=crossover_lookback2, min_periods=1).max().astype(bool)
            & (df["ema_fast2"] > df["ema_slow2"])
        )
        df["ema_cross_down2"] = (
            exact_cross_down2.rolling(window=crossover_lookback2, min_periods=1).max().astype(bool)
            & (df["ema_fast2"] < df["ema_slow2"])
        )
        warmup2 = max(ema_fast2_period, ema_slow2_period) + 10
        df.iloc[:warmup2, df.columns.get_loc("ema_cross_up2")] = False
        df.iloc[:warmup2, df.columns.get_loc("ema_cross_down2")] = False

    # EMA crossover detection with lookback window
    # Suppress crossovers during EMA warmup period to avoid spurious signals
    # Add buffer beyond ema_slow for better EMA convergence
    warmup = max(config["ema_fast"], config["ema_slow"]) + 10
    crossover_lookback = config.get("crossover_lookback", 3)

    exact_cross_up = (df["ema_fast"] > df["ema_slow"]) & (
        df["ema_fast"].shift(1) <= df["ema_slow"].shift(1)
    )
    exact_cross_down = (df["ema_fast"] < df["ema_slow"]) & (
        df["ema_fast"].shift(1) >= df["ema_slow"].shift(1)
    )

    df["ema_cross_up"] = (
        exact_cross_up.rolling(window=crossover_lookback, min_periods=1).max().astype(bool)
        & (df["ema_fast"] > df["ema_slow"])
    )
    df["ema_cross_down"] = (
        exact_cross_down.rolling(window=crossover_lookback, min_periods=1).max().astype(bool)
        & (df["ema_fast"] < df["ema_slow"])
    )
    # Zero out crossovers in warmup rows
    df.iloc[:warmup, df.columns.get_loc("ema_cross_up")] = False
    df.iloc[:warmup, df.columns.get_loc("ema_cross_down")] = False

    # Body dominance indicator: ratio of candle body to total range
    # Guards against zero-range doji candles with epsilon floor
    candle_range = (df["high"] - df["low"]).clip(lower=1e-10)
    df["body_pct"] = (df["close"] - df["open"]).abs() / candle_range

    # Momentum indicators
    df["mom10"] = df["close"] / df["close"].shift(10) - 1
    df["mom4"] = df["close"] / df["close"].shift(4) - 1

    # Squeeze indicator: current ATR relative to its 50-period mean
    # Values < 0.7 = compressed (squeeze), values > 0.8 = expanding (release)
    atr_ma50 = df["atr"].rolling(window=50).mean()
    df["squeeze"] = df["atr"] / atr_ma50.clip(lower=1e-10)

    # Ichimoku Cloud — only computed when ichimoku_tenkan key is present in config
    if "ichimoku_tenkan" in config:
        df = add_ichimoku_indicators(df, config)

    # Price Action patterns — computed when any PA signal is enabled
    if (
        config.get("signals", {}).get("pin_bar", {}).get("enabled", False)
        or config.get("signals", {}).get("engulfing", {}).get("enabled", False)
        or config.get("signals", {}).get("inside_bar_breakout", {}).get("enabled", False)
    ):
        df = add_price_action_patterns(df, config)

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


def add_ichimoku_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add Ichimoku Cloud indicators to the DataFrame.

    Columns added:
        tenkan    — Tenkan-sen (conversion line): (highest_high + lowest_low) / 2 over tenkan period
        kijun     — Kijun-sen (base line): same over kijun period
        span_a    — Senkou Span A raw (before display shift): (tenkan + kijun) / 2
        span_b    — Senkou Span B raw (before display shift): (H+L)/2 over senkou_b period
        cloud_top    — max(span_a_shifted_back, span_b_shifted_back) at current bar
        cloud_bottom — min(span_a_shifted_back, span_b_shifted_back) at current bar

    Signal logic uses the cloud at current time, which equals span_a/span_b values
    from 26 bars ago (i.e. span_a.shift(26) and span_b.shift(26) shifted backward).

    Args:
        df: DataFrame with high, low, close columns.
        config: Bot configuration with ichimoku_tenkan, ichimoku_kijun, ichimoku_senkou_b keys.

    Returns:
        DataFrame with Ichimoku columns added.
    """
    tenkan_period = config.get("ichimoku_tenkan", 9)
    kijun_period = config.get("ichimoku_kijun", 26)
    senkou_b_period = config.get("ichimoku_senkou_b", 52)
    cloud_shift = kijun_period  # Standard Ichimoku: cloud is projected forward by kijun bars

    # Tenkan-sen: midpoint of highest high and lowest low over tenkan period
    df["tenkan"] = (
        df["high"].rolling(window=tenkan_period).max()
        + df["low"].rolling(window=tenkan_period).min()
    ) / 2

    # Kijun-sen: midpoint of highest high and lowest low over kijun period
    df["kijun"] = (
        df["high"].rolling(window=kijun_period).max()
        + df["low"].rolling(window=kijun_period).min()
    ) / 2

    # Senkou Span A (raw, unshifted): average of tenkan and kijun
    span_a_raw = (df["tenkan"] + df["kijun"]) / 2

    # Senkou Span B (raw, unshifted): midpoint of highest high and lowest low over senkou_b period
    span_b_raw = (
        df["high"].rolling(window=senkou_b_period).max()
        + df["low"].rolling(window=senkou_b_period).min()
    ) / 2

    # Store raw spans for reference
    df["span_a"] = span_a_raw
    df["span_b"] = span_b_raw

    # For SIGNAL LOGIC: cloud at current bar = span_a/span_b from cloud_shift bars ago.
    # The Ichimoku cloud is projected forward by cloud_shift bars in display, so to see
    # where the cloud is NOW we look at the span values from cloud_shift bars back.
    span_a_current = span_a_raw.shift(cloud_shift)
    span_b_current = span_b_raw.shift(cloud_shift)

    df["cloud_top"] = pd.concat([span_a_current, span_b_current], axis=1).max(axis=1)
    df["cloud_bottom"] = pd.concat([span_a_current, span_b_current], axis=1).min(axis=1)

    logger.debug(
        "ichimoku_indicators_computed",
        extra={
            "tenkan_period": tenkan_period,
            "kijun_period": kijun_period,
            "senkou_b_period": senkou_b_period,
            "rows": len(df),
        },
    )
    return df


def add_price_action_patterns(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add Price Action pattern boolean columns to the DataFrame.

    All patterns are computed using fully vectorized pandas operations with no loops.
    Uses only closed candle OHLCV data — no forming candle look-ahead bias.

    Columns added:
        pin_bar_bull      — Bullish pin bar (long lower wick, close in upper portion)
        pin_bar_bear      — Bearish pin bar (long upper wick, close in lower portion)
        engulfing_bull    — Bullish engulfing candle (current body engulfs prior bear body)
        engulfing_bear    — Bearish engulfing candle (current body engulfs prior bull body)
        inside_bar        — Inside bar (current H/L within prior candle's H/L range)
        ib_breakout_bull  — Inside bar bullish breakout (close above mother bar high)
        ib_breakout_bear  — Inside bar bearish breakout (close below mother bar low)

    Args:
        df: DataFrame with open, high, low, close, atr columns already computed.
        config: Bot configuration with PA-specific parameter overrides.

    Returns:
        DataFrame with PA boolean columns added.
    """
    df = df.copy()

    # --- Shared helper series ---
    body = (df["close"] - df["open"]).abs()
    candle_range = (df["high"] - df["low"]).clip(lower=1e-10)
    lower_wick = df[["close", "open"]].min(axis=1) - df["low"]
    upper_wick = df["high"] - df[["close", "open"]].max(axis=1)

    # --- Pin Bar parameters ---
    wick_ratio = config.get("pin_bar_wick_ratio", 2.0)
    close_position_thresh = config.get("pin_bar_close_position", 0.70)
    max_upper_wick_pct = config.get("pin_bar_max_upper_wick_pct", 0.30)
    min_range_atr = config.get("pin_bar_min_range_atr_mult", 0.5)

    # Bullish pin bar: long lower wick, close in upper portion of range
    df["pin_bar_bull"] = (
        (lower_wick >= body * wick_ratio)  # Lower wick >= 2x body
        & ((df["close"] - df["low"]) / candle_range >= close_position_thresh)  # Close in upper 30%
        & (upper_wick / candle_range <= max_upper_wick_pct)  # Small upper wick
        & (body > 0)  # Not a perfect doji
        & (candle_range >= df["atr"] * min_range_atr)  # Minimum size
    )

    # Bearish pin bar: long upper wick, close in lower portion of range
    df["pin_bar_bear"] = (
        (upper_wick >= body * wick_ratio)  # Upper wick >= 2x body
        & ((df["high"] - df["close"]) / candle_range >= close_position_thresh)  # Close in lower 30%
        & (lower_wick / candle_range <= max_upper_wick_pct)  # Small lower wick
        & (body > 0)  # Not a perfect doji
        & (candle_range >= df["atr"] * min_range_atr)  # Minimum size
    )

    # --- Engulfing parameters ---
    engulfing_ratio = config.get("engulfing_body_ratio", 1.3)
    min_body_pct = config.get("engulfing_min_body_pct", 0.40)

    prev_body = body.shift(1)
    prev_close = df["close"].shift(1)
    prev_open = df["open"].shift(1)
    is_bull = df["close"] > df["open"]
    is_bear = df["close"] < df["open"]
    prev_is_bear = prev_close < prev_open
    prev_is_bull = prev_close > prev_open

    # Bullish engulfing: current bull body fully engulfs prior bear body.
    # For a bearish prior candle (prev_open > prev_close):
    #   - current close must exceed the TOP of the prior bear body (prev_open)
    #   - current open must be BELOW the TOP of the prior bear body (prev_open)
    #     i.e. open is somewhere inside or below the prior candle.
    # Note: prev_close is the BOTTOM of a bear candle so requiring open < prev_close
    # is too strict (almost never hit in a gap-less crypto market). Using prev_open instead.
    df["engulfing_bull"] = (
        is_bull
        & prev_is_bear
        & (body >= prev_body * engulfing_ratio)  # Current body >= 1.3x prev body
        & (df["close"] > prev_open)   # Current close > prior bear open (top of bear body)
        & (df["open"] < prev_open)    # Current open < prior bear open (inside/below bear body)
        & (body / candle_range >= min_body_pct)  # Min body-to-range ratio
    )

    # Bearish engulfing: current bear body fully engulfs prior bull body.
    # For a bullish prior candle (prev_close > prev_open):
    #   - current open must exceed the BOTTOM of the prior bull body (prev_open)
    #     i.e. open is somewhere inside or above the prior candle.
    #   - current close must fall BELOW the BOTTOM of the prior bull body (prev_open)
    # Note: prev_close is the TOP of a bull candle so requiring open > prev_close
    # is too strict (almost never hit in a gap-less crypto market). Using prev_open instead.
    df["engulfing_bear"] = (
        is_bear
        & prev_is_bull
        & (body >= prev_body * engulfing_ratio)  # Current body >= 1.3x prev body
        & (df["open"] > prev_open)    # Current open > prior bull open (inside/above bull body)
        & (df["close"] < prev_open)   # Current close < prior bull open (bottom of bull body)
        & (body / candle_range >= min_body_pct)  # Min body-to-range ratio
    )

    # --- Inside Bar ---
    df["inside_bar"] = (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))

    # --- Inside Bar Breakout parameters ---
    min_mother_atr = config.get("inside_bar_min_mother_range_atr_mult", 0.7)

    # Mother bar is 2 bars back, inside bar is 1 bar back, current = breakout candle
    mother_high = df["high"].shift(2)
    mother_low = df["low"].shift(2)
    mother_range = (mother_high - mother_low).clip(lower=1e-10)

    # Bullish IB breakout: previous bar was inside bar, current closes above mother high
    df["ib_breakout_bull"] = (
        df["inside_bar"].shift(1)  # Previous bar was an inside bar
        & (df["close"] > mother_high)  # Current closes above mother high
        & (mother_range >= df["atr"] * min_mother_atr)  # Mother bar was significant
    )

    # Bearish IB breakout: previous bar was inside bar, current closes below mother low
    df["ib_breakout_bear"] = (
        df["inside_bar"].shift(1)  # Previous bar was an inside bar
        & (df["close"] < mother_low)  # Current closes below mother low
        & (mother_range >= df["atr"] * min_mother_atr)  # Mother bar was significant
    )

    logger.debug(
        "price_action_patterns_computed",
        extra={
            "rows": len(df),
            "pin_bar_bull": int(df["pin_bar_bull"].sum()),
            "pin_bar_bear": int(df["pin_bar_bear"].sum()),
            "engulfing_bull": int(df["engulfing_bull"].sum()),
            "engulfing_bear": int(df["engulfing_bear"].sum()),
            "inside_bar": int(df["inside_bar"].sum()),
            "ib_breakout_bull": int(df["ib_breakout_bull"].sum()),
            "ib_breakout_bear": int(df["ib_breakout_bear"].sum()),
        },
    )
    return df


def add_funding_rate(
    df: pd.DataFrame, funding_file: str = "data/btcusdt_funding_rate.csv"
) -> pd.DataFrame:
    """Merge funding rate data with signal DataFrame.

    Funding rate updates every 8h (00:00, 08:00, 16:00 UTC).
    Forward-fill to match 15m candles.
    Uses shift(1) to prevent look-ahead bias — only the last SETTLED rate
    is visible at any candle, never the rate being determined right now.

    Args:
        df: Signal DataFrame with a DatetimeIndex or 'timestamp' column.
        funding_file: Path to a CSV with columns ``timestamp`` and ``fundingRate``.

    Returns:
        DataFrame with a ``fundingRate`` column added (0.0 where data unavailable).
    """
    import os

    if not os.path.exists(funding_file):
        logger.debug("funding_file_not_found", extra={"path": funding_file})
        return df

    funding = pd.read_csv(funding_file, parse_dates=["timestamp"])
    funding = funding.set_index("timestamp").sort_index()

    # shift(1) so that the rate visible at settlement time T is the
    # *previous* settled rate — not the one that just settled at T.
    funding["fundingRate"] = funding["fundingRate"].shift(1)

    df = df.copy()
    df_reset = df.reset_index()

    # Determine the timestamp column name
    ts_col = "timestamp" if "timestamp" in df_reset.columns else df_reset.columns[0]

    df_merged = pd.merge_asof(
        df_reset.sort_values(ts_col),
        funding.reset_index().rename(columns={"timestamp": ts_col}),
        on=ts_col,
        direction="backward",
    )

    if ts_col == "timestamp":
        df_merged = df_merged.set_index("timestamp")
    else:
        df_merged = df_merged.set_index(ts_col)

    df_merged["fundingRate"] = df_merged["fundingRate"].ffill().fillna(0.0)

    logger.debug(
        "funding_rate_merged",
        extra={
            "rows": len(df_merged),
            "funding_rows": len(funding),
            "non_zero": int((df_merged["fundingRate"] != 0).sum()),
        },
    )
    return df_merged


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

    # Compute 1H indicators for body_dominance and squeeze_release signals
    ema9_1h = compute_ema(trend_df["close"], 9)
    ema21_1h = compute_ema(trend_df["close"], 21)
    hl_range = trend_df["high"] - trend_df["low"]
    body = abs(trend_df["close"] - trend_df["open"])
    atr_1h = compute_atr(trend_df["high"], trend_df["low"], trend_df["close"], config.get("atr_period", 14))

    trend_feat = pd.DataFrame({
        "ema_trend_1h": trend_df["ema_trend"],
        "ema9_1h": ema9_1h,
        "ema21_1h": ema21_1h,
        "body_pct_1h": body / hl_range.replace(0, np.nan).ffill().clip(lower=0.01),
        "mom10_1h": trend_df["close"] / trend_df["close"].shift(10) - 1,
        "mom4_1h": trend_df["close"] / trend_df["close"].shift(4) - 1,
        "squeeze_1h": atr_1h / atr_1h.rolling(50).mean(),
        "vol_ratio_1h": trend_df["volume"] / trend_df["volume"].rolling(20).mean(),
        "close_1h": trend_df["close"],
        "open_1h": trend_df["open"],
    }, index=trend_df.index)

    # CRITICAL: Shift all 1H features by 1 period to prevent look-ahead bias.
    # Without this shift, a 15m candle at 15:15 would see the 1H candle for
    # 15:00-16:00 which hasn't closed yet. By shifting, we only see the LAST
    # COMPLETED 1H candle (e.g., 14:00-15:00 at time 15:15).
    # EMA-based columns (ema_trend, ema9, ema21) are lagging by nature so the
    # shift has minimal impact, but body_pct, mom, squeeze, vol_ratio are
    # entirely dependent on the candle's close — shift is essential.
    trend_feat = trend_feat.shift(1)

    # Merge with backward-looking alignment
    df = pd.merge_asof(
        df.reset_index(), trend_feat.reset_index(),
        on="timestamp" if "timestamp" in df.reset_index().columns else df.reset_index().columns[0],
        direction="backward",
    )
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    for col in trend_feat.columns:
        df[col] = df[col].ffill()
    df["above_trend"] = df["close"] > df["ema_trend_1h"]
    df["below_trend"] = df["close"] < df["ema_trend_1h"]

    return df
