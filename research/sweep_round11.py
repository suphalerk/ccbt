"""sweep_round11.py — Round 11 strategy sweep: 20 new strategies across 133 coins.

Tests 20 strategies on 1H and 4H timeframes — each with its own unique entry logic.
All strategies use closed-candle signals (iloc[-2] pattern via sig_row = df.iloc[i-1])
and the same event-driven simulator as previous sweeps.

Strategies implemented:
  S1  Turtle Trading 20/10     — Donchian 20-bar high/low entry, 10-bar exit
  S2  Connors RSI(3)           — CRSI = (RSI3 + StreakRSI + PctRank) / 3
  S3  Swing Point Breakout     — Close > last 5-bar swing high + EMA50 filter
  S4  EMA Ribbon (6 EMAs)      — All 6 EMAs just aligned bullish/bearish
  S5  ATR Flip (Chandelier)    — Chandelier exit flips direction
  S6  RSI(2) Extreme Bounce    — RSI2 < 10 in uptrend, > 90 in downtrend
  S7  Eltrut (Reverse Turtle)  — Fade 20-bar breakout in ranging regime
  S8  Stochastic MTF Swing     — 4H stoch direction + 1H stoch K/D cross
  S9  Z-Score Mean Reversion   — Z-score extreme with EMA50 filter
  S10 Fibonacci Retracement    — Bounce from 38.2-61.8% retracement zone
  S11 Pivot Point Breakout     — Break daily R1/S1 with EMA50 filter
  S12 Heikin-Ashi Color Rev.   — HA turns green/red after 2+ opposite candles
  S13 Consecutive Red DCA      — 3+ consecutive red candles in uptrend (long only)
  S14 Volatility Squeeze       — TTM Squeeze release (BB inside KC → expands)
  S15 Triple Confluence        — Stoch + RSI + MACD all aligned
  S16 Support/Resistance Break — Break rolling 10-bar high/low cluster w/ volume
  S17 Shooting Star / Hammer   — Single-candle wick patterns at EMA21
  S18 Weighted Score (7 inds)  — 7-indicator equal-weight score >= 0.7 edge detect
  S19 Pairs Spread BTC/Alt     — Z-score of log-price spread vs BTC
  S20 Renko Trend              — Renko brick direction flip from 2+ consecutive bricks

Usage:
    python3 -u research/sweep_round11.py
    python3 -u research/sweep_round11.py --coins btcusdt,ethusdt
    python3 -u research/sweep_round11.py --min-pf 1.5 --min-trades 6
    python3 -u research/sweep_round11.py --all-coins  # include deployed coins too
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from bot.data import compute_atr, compute_ema, compute_rsi

DATA_DIR = "/Users/iceai/Work/ccbt/data"
OUTPUT_FILE = os.path.join(DATA_DIR, "sweep_round11.json")

# ---------------------------------------------------------------------------
# Simulation constants — must match previous sweeps for comparability
# ---------------------------------------------------------------------------
COMMISSION = 0.00055   # 0.055% taker fee
SLIPPAGE = 0.00015     # 0.015% slippage
LEVERAGE = 25
RISK_PCT = 0.01        # 1% risk per trade
HOURS_START = 3
HOURS_END = 20
MIN_TRADES = 4
WARMUP_BARS = 120      # skip first N bars for all indicators to settle

# SL/TP per strategy: (sl_mult, tp_mult, trail_mult)
# trail_mult=0 means fixed TP; tp_mult=0 means trailing-stop only
STRATEGY_PARAMS: Dict[str, Tuple[float, float, float]] = {
    "S1_Turtle_2010":         (2.0, 0.0, 3.0),   # exit on opposite channel + trail
    "S2_Connors_RSI3":        (1.5, 3.0, 0.0),
    "S3_Swing_Breakout":      (2.0, 4.0, 0.0),
    "S4_EMA_Ribbon":          (2.0, 4.0, 0.0),
    "S5_ATR_Flip":            (2.0, 4.0, 0.0),
    "S6_RSI2_Extreme":        (1.0, 2.0, 0.0),
    "S7_Eltrut_Reverse":      (1.5, 2.0, 0.0),
    "S8_Stoch_MTF":           (2.0, 4.0, 0.0),
    "S9_ZScore_MeanRev":      (1.5, 2.0, 0.0),
    "S10_Fib_Retracement":    (2.0, 4.0, 0.0),
    "S11_Pivot_Breakout":     (2.0, 3.0, 0.0),
    "S12_HeikinAshi_Rev":     (2.0, 4.0, 0.0),
    "S13_ConsecRed_DCA":      (1.5, 3.0, 0.0),
    "S14_VolSqueeze":         (2.0, 4.0, 0.0),
    "S15_Triple_Confluence":  (2.0, 4.0, 0.0),
    "S16_SR_Break":           (1.5, 3.0, 0.0),
    "S17_CandlePattern":      (1.5, 3.0, 0.0),
    "S18_Weighted_Score":     (2.0, 4.0, 0.0),
    "S19_Pairs_Spread":       (1.5, 2.0, 0.0),
    "S20_Renko_Trend":        (2.0, 4.0, 0.0),
}

# Currently deployed coins — used to flag new vs existing
DEPLOYED_PREFIXES = {
    "btcusdt", "dogeusdt", "arbusdt", "wifusdt",
    "avaxusdt", "nearusdt", "solusdt",
    "gunusdt", "berausdt", "athusdt", "zetausdt",
    "arcusdt", "animeusdt", "trumpusdt", "injusdt",
    "xlmusdt", "1000shibusdt", "trxusdt", "taousdt",
    "renderusdt", "hbarusdt", "polusdt", "polyxusdt",
    "fetusdt", "algousdt", "mstrusdt", "xagusdt", "saharausdt",
    "1000pepeusdt", "wldusdt",
}

# BTC 1H data loaded once for S19 pairs spread
_BTC_1H_CLOSE: Optional[pd.Series] = None


# ---------------------------------------------------------------------------
# OHLCV loader
# ---------------------------------------------------------------------------

def _load_ohlcv(path: str) -> pd.DataFrame:
    """Load OHLCV CSV with timestamp index.

    Args:
        path: Absolute path to CSV file.

    Returns:
        DataFrame with DatetimeIndex.
    """
    return pd.read_csv(path, index_col="timestamp", parse_dates=True)


# ---------------------------------------------------------------------------
# Base indicator computation (shared across all strategies)
# ---------------------------------------------------------------------------

def compute_base(df: pd.DataFrame) -> pd.DataFrame:
    """Compute shared base indicators needed by multiple strategies.

    Computes: ATR(14), RSI(14), EMA(9), EMA(21), EMA(50), volume MA(20),
    vol_ratio, hour, dow.

    Args:
        df: Raw OHLCV DataFrame with DatetimeIndex.

    Returns:
        Copy of df with added indicator columns.
    """
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    df["atr"] = compute_atr(high, low, close, 14)
    df["rsi"] = compute_rsi(close, 14)
    df["ema9"] = compute_ema(close, 9)
    df["ema21"] = compute_ema(close, 21)
    df["ema50"] = compute_ema(close, 50)
    df["vol_ma20"] = vol.rolling(20).mean()
    df["vol_ratio"] = vol / df["vol_ma20"].clip(lower=1e-10)
    df["atr_ma50"] = df["atr"].rolling(50).mean()

    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek
    else:
        df["hour"] = 12
        df["dow"] = 0

    return df


def _in_window(df: pd.DataFrame) -> pd.Series:
    """Return boolean mask for valid trading hours (3-20 UTC, Mon-Fri)."""
    return (
        (df["hour"] >= HOURS_START) & (df["hour"] < HOURS_END) & (df["dow"] < 5)
    )


# ---------------------------------------------------------------------------
# Shared indicator helpers
# ---------------------------------------------------------------------------

def _compute_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_smooth: int = 3,
) -> Tuple[pd.Series, pd.Series]:
    """Classic Stochastic %K and %D.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        k_period: Lookback for highest high / lowest low.
        d_smooth: Smoothing period for %D (SMA of %K).

    Returns:
        Tuple of (k, d) Series in range [0, 100].
    """
    hh = high.rolling(k_period).max()
    ll = low.rolling(k_period).min()
    k = 100.0 * (close - ll) / (hh - ll).replace(0, 1e-10)
    d = k.rolling(d_smooth).mean()
    return k, d


def _compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, and histogram.

    Args:
        close: Close price series.
        fast: Fast EMA period.
        slow: Slow EMA period.
        signal_period: Signal line EMA period.

    Returns:
        Tuple of (macd_line, signal_line, histogram) Series.
    """
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _compute_adx_full(
    df: pd.DataFrame, period: int = 14,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """ADX with +DI and -DI using Wilder's smoothing.

    Args:
        df: DataFrame with high/low/close columns.
        period: ADX smoothing period.

    Returns:
        Tuple of (adx, plus_di, minus_di) Series.
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )

    alpha = 1.0 / period
    atr_s = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    pdm_s = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    mdm_s = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    plus_di = 100.0 * pdm_s / atr_s.clip(lower=1e-10)
    minus_di = 100.0 * mdm_s / atr_s.clip(lower=1e-10)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).clip(lower=1e-10)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    return adx, plus_di, minus_di


def _compute_supertrend_fast(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
    multiplier: float = 2.0,
) -> Tuple[pd.Series, pd.Series]:
    """Fast numpy Supertrend.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: ATR period.
        multiplier: ATR band multiplier.

    Returns:
        Tuple of (supertrend_values, direction) where direction: 1=long, -1=short.
    """
    atr = compute_atr(high, low, close, period)
    hl2 = (high + low) / 2
    upper_band = (hl2 + multiplier * atr).values
    lower_band = (hl2 - multiplier * atr).values
    c = close.values
    n = len(c)

    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    direction = np.ones(n, dtype=np.int8)

    for i in range(1, n):
        if lower_band[i] > final_lower[i - 1] or c[i - 1] < final_lower[i - 1]:
            final_lower[i] = lower_band[i]
        else:
            final_lower[i] = final_lower[i - 1]
        if upper_band[i] < final_upper[i - 1] or c[i - 1] > final_upper[i - 1]:
            final_upper[i] = upper_band[i]
        else:
            final_upper[i] = final_upper[i - 1]
        if direction[i - 1] == 1:
            direction[i] = -1 if c[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if c[i] > final_upper[i] else -1

    st_vals = np.where(direction == 1, final_lower, final_upper)
    return (
        pd.Series(st_vals, index=close.index),
        pd.Series(direction.astype(int), index=close.index),
    )


def _compute_heikin_ashi(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Heikin-Ashi candles.

    Args:
        open_: Open price series.
        high: High price series.
        low: Low price series.
        close: Close price series.

    Returns:
        Tuple of (ha_open, ha_high, ha_low, ha_close) Series.
    """
    ha_close = (open_ + high + low + close) / 4
    ha_open_vals = open_.values.copy().astype(float)
    ha_c = ha_close.values
    for i in range(1, len(ha_open_vals)):
        ha_open_vals[i] = (ha_open_vals[i - 1] + ha_c[i - 1]) / 2
    ha_open = pd.Series(ha_open_vals, index=open_.index)
    ha_high = pd.concat([high, ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([low, ha_open, ha_close], axis=1).min(axis=1)
    return ha_open, ha_high, ha_low, ha_close


def _compute_ichimoku_cloud(
    df: pd.DataFrame,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Ichimoku Cloud: Tenkan, Kijun, cloud_top, cloud_bottom.

    Args:
        df: DataFrame with high/low columns.

    Returns:
        Tuple of (tenkan, kijun, cloud_top, cloud_bottom).
    """
    tenkan = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    kijun = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = (
        (df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2
    ).shift(26)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)
    return tenkan, kijun, cloud_top, cloud_bottom


def _compute_choppiness(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Choppiness Index: 38.2 = strong trend, 61.8 = choppy/ranging.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: Lookback period.

    Returns:
        Choppiness Index series.
    """
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr_sum = tr.rolling(period).sum()
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    ci = (
        100.0
        * np.log10(atr_sum / (hh - ll).replace(0, 1e-10))
        / np.log10(period)
    )
    return ci


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def strategy_s1_turtle(df: pd.DataFrame) -> pd.DataFrame:
    """S1: Turtle Trading 20/10.

    Entry: close breaks above 20-bar high (long) or below 20-bar low (short).
    Exit signal (via trailing stop): 10-bar exit channel stored in 'exit_channel'.
    The simulator uses trail_mult=3.0 for ATR trailing stop.

    The 10-bar exit channel is embedded as an auxiliary signal:
    if already long and close < lower_10 → we encode -1 override, but since
    the simulator cannot do state-dependent exits natively, we use ATR
    trailing stop (trail_mult=3.0) as the exit mechanism. The 20-bar entry
    is the pure signal here.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    # shift(1): use closed bars only (no look-ahead)
    upper_20 = df["high"].rolling(20).max().shift(1)
    lower_20 = df["low"].rolling(20).min().shift(1)

    window = _in_window(df)

    # Entry on close breaching the 20-bar donchian
    break_long = (df["close"] > upper_20) & (df["close"].shift(1) <= upper_20.shift(1))
    break_short = (df["close"] < lower_20) & (df["close"].shift(1) >= lower_20.shift(1))

    # Trend filter: EMA50 direction
    long_filter = df["close"] > df["ema50"]
    short_filter = df["close"] < df["ema50"]

    signal = pd.Series(0, index=df.index)
    signal[break_long & long_filter & window] = 1
    signal[break_short & short_filter & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s2_connors_rsi3(df: pd.DataFrame) -> pd.DataFrame:
    """S2: Connors RSI(3).

    CRSI = (RSI3 + StreakRSI2 + PercentileRank100) / 3
    Long: CRSI < 15 AND close > EMA50
    Short: CRSI > 85 AND close < EMA50

    Uses edge detection: signal fires only when CRSI crosses the threshold.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]

    # RSI(3)
    rsi3 = compute_rsi(close, 3)

    # Up/down streak
    diff = close.diff()
    streak_vals = np.zeros(len(close))
    for i in range(1, len(streak_vals)):
        if diff.iloc[i] > 0:
            streak_vals[i] = max(streak_vals[i - 1], 0) + 1
        elif diff.iloc[i] < 0:
            streak_vals[i] = min(streak_vals[i - 1], 0) - 1
        else:
            streak_vals[i] = 0
    streak = pd.Series(streak_vals, index=close.index)

    # RSI of streak (period 2)
    streak_rsi = compute_rsi(streak, 2)

    # 100-bar percentile rank of close
    pct_rank = close.rolling(100).rank(pct=True) * 100

    crsi = (rsi3 + streak_rsi + pct_rank) / 3.0

    window = _in_window(df)

    # Edge detect: CRSI crosses below 15 (enter oversold = long)
    cross_oversold = (crsi < 15) & (crsi.shift(1) >= 15)
    # Edge detect: CRSI crosses above 85 (enter overbought = short)
    cross_overbought = (crsi > 85) & (crsi.shift(1) <= 85)

    signal = pd.Series(0, index=df.index)
    signal[cross_oversold & (close > df["ema50"]) & window] = 1
    signal[cross_overbought & (close < df["ema50"]) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s3_swing_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """S3: Swing Point Breakout.

    Last swing high (5-bar local max) break = long entry.
    Last swing low (5-bar local min) break = short entry.
    Shift by 2 bars to avoid look-ahead (center=True needs +2 bars future).

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # 5-bar rolling max/min with center=True uses 2 future bars
    # We shift(2) forward after rolling(5, center=True) to ensure no look-ahead
    swing_high_raw = high.rolling(5, center=True).max()
    swing_low_raw = low.rolling(5, center=True).min()

    # A swing high is where high == the 5-bar centered max
    is_swing_high = (high == swing_high_raw)
    is_swing_low = (low == swing_low_raw)

    # Forward-fill the swing levels, shift(2) to avoid look-ahead from center window
    # (center=True already reads 2 bars forward, shift(2) compensates)
    last_swing_high = swing_high_raw.where(is_swing_high).ffill().shift(2)
    last_swing_low = swing_low_raw.where(is_swing_low).ffill().shift(2)

    window = _in_window(df)

    long_sig = (
        (close > last_swing_high)
        & (close.shift(1) <= last_swing_high.shift(1))
        & (close > df["ema50"])
    )
    short_sig = (
        (close < last_swing_low)
        & (close.shift(1) >= last_swing_low.shift(1))
        & (close < df["ema50"])
    )

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s4_ema_ribbon(df: pd.DataFrame) -> pd.DataFrame:
    """S4: EMA Ribbon (6 EMAs aligned).

    Bull ribbon: EMA8 > EMA13 > EMA21 > EMA34 > EMA55 > EMA89 — just turned fully bullish.
    Bear ribbon: EMA8 < EMA13 < EMA21 < EMA34 < EMA55 < EMA89 — just turned fully bearish.
    Entry: first bar where ribbon is fully aligned after NOT being fully aligned.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]

    ema8 = compute_ema(close, 8)
    ema13 = compute_ema(close, 13)
    ema21 = df["ema21"]   # already computed
    ema34 = compute_ema(close, 34)
    ema55 = compute_ema(close, 55)
    ema89 = compute_ema(close, 89)

    bull = (ema8 > ema13) & (ema13 > ema21) & (ema21 > ema34) & (ema34 > ema55) & (ema55 > ema89)
    bear = (ema8 < ema13) & (ema13 < ema21) & (ema21 < ema34) & (ema34 < ema55) & (ema55 < ema89)

    # Edge detection: just became fully aligned
    bull_entry = bull & ~bull.shift(1).fillna(False)
    bear_entry = bear & ~bear.shift(1).fillna(False)

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[bull_entry & window] = 1
    signal[bear_entry & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s5_atr_flip(df: pd.DataFrame) -> pd.DataFrame:
    """S5: ATR Flip (Chandelier Exit).

    Chandelier exit = N-period highest high (long) or lowest low (short) minus/plus 3×ATR.
    Direction flips when close crosses the Chandelier line — entry on the flip.

    Long: close > chandelier_short (direction flips from -1 to 1).
    Short: close < chandelier_long (direction flips from 1 to -1).

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    high = df["high"]
    low = df["low"]
    close = df["close"]
    atr14 = df["atr"]

    period = 22
    atr_mult = 3.0

    chan_long = high.rolling(period).max() - atr_mult * atr14   # long side exit
    chan_short = low.rolling(period).min() + atr_mult * atr14   # short side exit

    # Direction array
    dir_vals = np.zeros(len(close))
    cl = chan_long.values
    cs = chan_short.values
    c = close.values

    dir_vals[0] = 1
    for i in range(1, len(dir_vals)):
        if np.isnan(cl[i]) or np.isnan(cs[i]):
            dir_vals[i] = dir_vals[i - 1]
            continue
        if dir_vals[i - 1] == 1:
            dir_vals[i] = -1 if c[i] < cl[i] else 1
        else:
            dir_vals[i] = 1 if c[i] > cs[i] else -1

    direction = pd.Series(dir_vals, index=close.index)

    flip_bull = (direction == 1) & (direction.shift(1) == -1)
    flip_bear = (direction == -1) & (direction.shift(1) == 1)

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[flip_bull & window] = 1
    signal[flip_bear & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s6_rsi2_extreme(df: pd.DataFrame) -> pd.DataFrame:
    """S6: RSI(2) Extreme Bounce.

    Very short-term mean reversion using ultra-fast RSI(2).
    Long: RSI2 crosses below 10 (becoming oversold) AND close > EMA50.
    Short: RSI2 crosses above 90 AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    rsi2 = compute_rsi(df["close"], 2)
    window = _in_window(df)

    enter_long = (rsi2 < 10) & (rsi2.shift(1) >= 10)
    enter_short = (rsi2 > 90) & (rsi2.shift(1) <= 90)

    signal = pd.Series(0, index=df.index)
    signal[enter_long & (df["close"] > df["ema50"]) & window] = 1
    signal[enter_short & (df["close"] < df["ema50"]) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s7_eltrut(df: pd.DataFrame) -> pd.DataFrame:
    """S7: Eltrut (Reverse Turtle) — fade 20-bar breakout in ranging markets.

    Short: close > 20-bar high (overbought spike in ranging regime).
    Long: close < 20-bar low (oversold spike in ranging regime).
    Requires: Choppiness Index > 61.8 (ranging, not trending).

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]

    upper_20 = df["high"].rolling(20).max().shift(1)
    lower_20 = df["low"].rolling(20).min().shift(1)

    ci = _compute_choppiness(df["high"], df["low"], df["close"], period=14)
    ranging = ci > 61.8

    break_long = (close > upper_20) & (close.shift(1) <= upper_20.shift(1))
    break_short = (close < lower_20) & (close.shift(1) >= lower_20.shift(1))

    window = _in_window(df)

    # Eltrut: fade the breakout
    signal = pd.Series(0, index=df.index)
    signal[break_long & ranging & window] = -1   # fade high breakout = short
    signal[break_short & ranging & window] = 1   # fade low breakout = long
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s8_stoch_mtf(
    df: pd.DataFrame,
    df_4h: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """S8: Stochastic MTF Swing.

    4H Stochastic K > 50 = bullish context; 1H stoch K/D cross in oversold = entry.
    If df_4h is None (single-timeframe mode, e.g. already running on 4H data),
    uses standard stochastic cross with EMA50 filter only.

    Args:
        df: Base-indicator-enriched 1H OHLCV DataFrame.
        df_4h: Optional 4H OHLCV DataFrame (pre-computed base indicators).

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()

    stoch_k_1h, stoch_d_1h = _compute_stochastic(
        df["high"], df["low"], df["close"], k_period=14, d_smooth=3
    )

    if df_4h is not None:
        stoch_k_4h, _ = _compute_stochastic(
            df_4h["high"], df_4h["low"], df_4h["close"], k_period=14, d_smooth=3
        )
        # Reindex 4H stoch to 1H (forward fill)
        stoch_k_4h_h = stoch_k_4h.reindex(df.index, method="ffill")
        bull_4h = stoch_k_4h_h > 50
        bear_4h = stoch_k_4h_h < 50
    else:
        # Fallback: use EMA50 direction as proxy for higher-TF context
        bull_4h = df["close"] > df["ema50"]
        bear_4h = df["close"] < df["ema50"]

    # 1H: K crosses above D from oversold zone
    k_cross_up = (stoch_k_1h > stoch_d_1h) & (stoch_k_1h.shift(1) <= stoch_d_1h.shift(1))
    k_cross_dn = (stoch_k_1h < stoch_d_1h) & (stoch_k_1h.shift(1) >= stoch_d_1h.shift(1))

    oversold = stoch_k_1h < 20
    overbought = stoch_k_1h > 80

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[k_cross_up & oversold & bull_4h & window] = 1
    signal[k_cross_dn & overbought & bear_4h & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s9_zscore(df: pd.DataFrame) -> pd.DataFrame:
    """S9: Z-Score Mean Reversion.

    Z-score of close vs 20-bar mean/std.
    Long: Z < -2.0 AND close > EMA50 (extreme below mean in uptrend).
    Short: Z > +2.0 AND close < EMA50.
    Edge detect: Z crosses the threshold.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]

    roll_mean = close.rolling(20).mean()
    roll_std = close.rolling(20).std()
    zscore = (close - roll_mean) / roll_std.replace(0, 1e-10)

    # Edge detection on threshold crossing
    enter_long = (zscore < -2.0) & (zscore.shift(1) >= -2.0)
    enter_short = (zscore > 2.0) & (zscore.shift(1) <= 2.0)

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[enter_long & (close > df["ema50"]) & window] = 1
    signal[enter_short & (close < df["ema50"]) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s10_fib_retracement(df: pd.DataFrame) -> pd.DataFrame:
    """S10: Fibonacci Retracement Entry.

    Uses 50-bar swing high/low to compute Fibonacci levels.
    Long: prev close < fib_618 AND current close > fib_618 (bounce from 61.8% zone)
          AND close > open (bullish candle) AND EMA9 > EMA21.
    Short: mirror using fib_382 from the bottom.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # 50-bar swing (shifted 1 to avoid look-ahead)
    swing_high = high.rolling(50).max().shift(1)
    swing_low = low.rolling(50).min().shift(1)
    swing_range = (swing_high - swing_low).replace(0, 1e-10)

    # Fib levels from the TOP of the swing (retracement into uptrend)
    fib_382 = swing_high - 0.382 * swing_range
    fib_618 = swing_high - 0.618 * swing_range

    # Bullish bounce: close crosses above fib_618 after being below (deep pullback bounce)
    # In uptrend (EMA9 > EMA21), bullish candle
    cross_618_up = (close > fib_618) & (close.shift(1) <= fib_618.shift(1))
    bull_candle = close > df["open"]
    ema_bull = df["ema9"] > df["ema21"]

    # Bearish reversal: close crosses below fib_382 from above (shallow pullback fail)
    # In downtrend (EMA9 < EMA21)
    cross_382_dn = (close < fib_382) & (close.shift(1) >= fib_382.shift(1))
    bear_candle = close < df["open"]
    ema_bear = df["ema9"] < df["ema21"]

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[cross_618_up & bull_candle & ema_bull & window] = 1
    signal[cross_382_dn & bear_candle & ema_bear & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s11_pivot_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """S11: Pivot Point Breakout (Daily Pivots).

    Computes yesterday's pivot, R1, S1 from daily OHLCV.
    Long: close crosses above R1 AND close > EMA50.
    Short: close crosses below S1 AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]

    # Resample to daily, shift(1) to get previous day's values
    daily = (
        df[["open", "high", "low", "close"]]
        .resample("1D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
        .shift(1)   # shift: use prior day (no look-ahead)
    )

    pivot_d = (daily["high"] + daily["low"] + daily["close"]) / 3.0
    r1_d = 2.0 * pivot_d - daily["low"]
    s1_d = 2.0 * pivot_d - daily["high"]

    # Reindex to hourly using forward fill
    r1_h = r1_d.reindex(df.index, method="ffill")
    s1_h = s1_d.reindex(df.index, method="ffill")

    # Cross above R1 or below S1
    cross_above_r1 = (close > r1_h) & (close.shift(1) <= r1_h.shift(1))
    cross_below_s1 = (close < s1_h) & (close.shift(1) >= s1_h.shift(1))

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[cross_above_r1 & (close > df["ema50"]) & window] = 1
    signal[cross_below_s1 & (close < df["ema50"]) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s12_heikinashi_reversal(df: pd.DataFrame) -> pd.DataFrame:
    """S12: Heikin-Ashi Color Reversal.

    Long: HA turns green (ha_close > ha_open) after 2+ consecutive red HA candles AND close > EMA50.
    Short: HA turns red after 2+ consecutive green HA candles AND close < EMA50.
    Edge detect: fires only on the first candle of the reversal.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    ha_open, _ha_high, _ha_low, ha_close = _compute_heikin_ashi(
        df["open"], df["high"], df["low"], df["close"]
    )

    ha_green = ha_close > ha_open
    ha_red = ha_close < ha_open

    # Require 2 consecutive opposite candles before current
    prev2_red = ha_red.shift(1) & ha_red.shift(2)
    prev2_green = ha_green.shift(1) & ha_green.shift(2)

    # Current candle reverses
    rev_to_green = ha_green & prev2_red.fillna(False)
    rev_to_red = ha_red & prev2_green.fillna(False)

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[rev_to_green & (df["close"] > df["ema50"]) & window] = 1
    signal[rev_to_red & (df["close"] < df["ema50"]) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s13_consec_red_dca(df: pd.DataFrame) -> pd.DataFrame:
    """S13: Consecutive Red DCA Bounce (long-only).

    Long entry after 3+ consecutive red candles in an uptrend (close > EMA50).
    Edge detect: 3rd consecutive red candle is the signal bar.
    No short side (DCA is long-biased).

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]
    open_ = df["open"]

    red = close < open_
    green = close >= open_

    # Rolling count of consecutive red candles
    # red_count[i] = N if red[i], red[i-1], ..., red[i-N+1] are all red
    # We want exactly the moment when 3 consecutive reds just happened
    # i.e., red.shift(0) & red.shift(1) & red.shift(2) & ~red.shift(3)
    # That means the third red candle in a row (not the 4th or later)
    three_red = red & red.shift(1).fillna(False) & red.shift(2).fillna(False)
    exactly_three = three_red & ~red.shift(3).fillna(False)

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[exactly_three & (close > df["ema50"]) & window] = 1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s14_vol_squeeze(df: pd.DataFrame) -> pd.DataFrame:
    """S14: Volatility Squeeze (TTM Squeeze).

    Squeeze: BB(20,2) is completely inside KC(EMA20, 1.5×ATR14).
    Release: squeeze was on, now off.
    Long: release AND close > 20-bar SMA.
    Short: release AND close < 20-bar SMA.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    atr14 = df["atr"]

    # Bollinger Bands (20, 2)
    bb_mean = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mean + 2.0 * bb_std
    bb_lower = bb_mean - 2.0 * bb_std

    # Keltner Channel (EMA20, 1.5×ATR)
    kc_mid = close.ewm(span=20, adjust=False).mean()
    kc_upper = kc_mid + 1.5 * atr14
    kc_lower = kc_mid - 1.5 * atr14

    # Squeeze: BB fully inside KC
    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    squeeze_release = squeeze_on.shift(1).fillna(False) & ~squeeze_on

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[squeeze_release & (close > bb_mean) & window] = 1
    signal[squeeze_release & (close < bb_mean) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s15_triple_confluence(df: pd.DataFrame) -> pd.DataFrame:
    """S15: Triple Indicator Confluence (Stoch + RSI + MACD).

    Bullish: Stoch K > D (and K < 80), RSI14 in 50-70, MACD line > signal.
    All three must be aligned AND close > EMA50.
    Edge detect: first bar where all three align after NOT all being aligned.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]
    rsi14 = df["rsi"]

    stoch_k, stoch_d = _compute_stochastic(df["high"], df["low"], close, 14, 3)
    macd_line, macd_signal, _ = _compute_macd(close, 12, 26, 9)

    stoch_bull = (stoch_k > stoch_d) & (stoch_k < 80)
    rsi_bull = (rsi14 > 50) & (rsi14 < 70)
    macd_bull = macd_line > macd_signal

    stoch_bear = (stoch_k < stoch_d) & (stoch_k > 20)
    rsi_bear = (rsi14 > 30) & (rsi14 < 50)
    macd_bear = macd_line < macd_signal

    all_bull = stoch_bull & rsi_bull & macd_bull
    all_bear = stoch_bear & rsi_bear & macd_bear

    # Edge detect
    bull_entry = all_bull & ~all_bull.shift(1).fillna(False)
    bear_entry = all_bear & ~all_bear.shift(1).fillna(False)

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[bull_entry & (close > df["ema50"]) & window] = 1
    signal[bear_entry & (close < df["ema50"]) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s16_sr_break(df: pd.DataFrame) -> pd.DataFrame:
    """S16: Support/Resistance Auto-Detect Breakout.

    Resistance = rolling 10-bar high (shifted for closed bars).
    Support = rolling 10-bar low (shifted).
    Breakout: close > resistance AND volume > 1.5× vol_ma20.
    Breakdown: close < support AND volume > 1.5× vol_ma20.
    EMA50 trend filter applied.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # Use shift(1) so resistance/support is from closed bars
    resistance = high.rolling(10).max().shift(1)
    support = low.rolling(10).min().shift(1)

    vol_surge = df["vol_ratio"] >= 1.5

    break_high = (close > resistance) & (close.shift(1) <= resistance.shift(1))
    break_low = (close < support) & (close.shift(1) >= support.shift(1))

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[break_high & vol_surge & (close > df["ema50"]) & window] = 1
    signal[break_low & vol_surge & (close < df["ema50"]) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s17_candle_pattern(df: pd.DataFrame) -> pd.DataFrame:
    """S17: Shooting Star / Hammer Candle Pattern.

    Hammer: lower wick > 2× body, upper wick < 0.3× body, close > open.
            Long entry when price is near EMA21 (within 2% below EMA21) in uptrend.
    Shooting Star: upper wick > 2× body, lower wick < 0.3× body, close < open.
                   Short when near EMA21 (within 2% above EMA21) in downtrend.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    close = df["close"]
    ema21 = df["ema21"]
    ema50 = df["ema50"]

    body = (close - open_).abs().replace(0, 1e-10)
    candle_top = pd.concat([close, open_], axis=1).max(axis=1)
    candle_bot = pd.concat([close, open_], axis=1).min(axis=1)

    upper_wick = high - candle_top
    lower_wick = candle_bot - low

    # Hammer: long bullish wick below body
    is_hammer = (
        (lower_wick > 2.0 * body)
        & (upper_wick < 0.3 * body)
        & (close > open_)
    )
    # Shooting star: long bearish wick above body
    is_shooting_star = (
        (upper_wick > 2.0 * body)
        & (lower_wick < 0.3 * body)
        & (close < open_)
    )

    # Near EMA21: price within 2%
    near_ema21_below = (close < ema21) & (close > ema21 * 0.98)
    near_ema21_above = (close > ema21) & (close < ema21 * 1.02)

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[is_hammer & near_ema21_below & (close > ema50) & window] = 1
    signal[is_shooting_star & near_ema21_above & (close < ema50) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s18_weighted_score(df: pd.DataFrame) -> pd.DataFrame:
    """S18: Weighted Strategy Score (NostalgiaForInfinity-inspired).

    7 binary indicators, equal weight (each = 1/7 ≈ 0.143):
      1. EMA9 > EMA21
      2. RSI14 in [45, 65]
      3. MACD line > signal
      4. ADX > 20
      5. AO (Awesome Oscillator) > 0
      6. Volume > vol_ma20
      7. Close > Ichimoku cloud top

    Long: score >= 0.7 AND close > EMA50 — edge detect (was < 0.7 prev bar).
    Short: bear_score >= 0.7 AND close < EMA50 — edge detect.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]
    rsi14 = df["rsi"]

    macd_line, macd_sig, _ = _compute_macd(close, 12, 26, 9)
    adx, _, _ = _compute_adx_full(df, 14)

    # Awesome Oscillator: SMA(5, midpoint) - SMA(34, midpoint)
    midpoint = (df["high"] + df["low"]) / 2.0
    ao = midpoint.rolling(5).mean() - midpoint.rolling(34).mean()

    _, _, cloud_top, cloud_bottom = _compute_ichimoku_cloud(df)

    # Bull score (7 conditions)
    b1 = (df["ema9"] > df["ema21"]).astype(float)
    b2 = ((rsi14 >= 45) & (rsi14 <= 65)).astype(float)
    b3 = (macd_line > macd_sig).astype(float)
    b4 = (adx > 20).astype(float)
    b5 = (ao > 0).astype(float)
    b6 = (df["vol_ratio"] > 1.0).astype(float)
    b7 = (close > cloud_top).astype(float)

    bull_score = (b1 + b2 + b3 + b4 + b5 + b6 + b7) / 7.0

    # Bear score (7 mirror conditions)
    r1 = (df["ema9"] < df["ema21"]).astype(float)
    r2 = ((rsi14 >= 35) & (rsi14 <= 55)).astype(float)
    r3 = (macd_line < macd_sig).astype(float)
    r4 = (adx > 20).astype(float)
    r5 = (ao < 0).astype(float)
    r6 = (df["vol_ratio"] > 1.0).astype(float)
    r7 = (close < cloud_bottom).astype(float)

    bear_score = (r1 + r2 + r3 + r4 + r5 + r6 + r7) / 7.0

    # Edge detect on threshold crossing
    THRESHOLD = 0.7
    bull_entry = (bull_score >= THRESHOLD) & (bull_score.shift(1).fillna(0) < THRESHOLD)
    bear_entry = (bear_score >= THRESHOLD) & (bear_score.shift(1).fillna(0) < THRESHOLD)

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[bull_entry & (close > df["ema50"]) & window] = 1
    signal[bear_entry & (close < df["ema50"]) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s19_pairs_spread(
    df: pd.DataFrame,
    btc_close: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """S19: Pairs Spread (Alt vs BTC).

    Compute log-price spread = log(alt) - log(btc), z-score over 20 bars.
    Long alt: z < -2.0 (alt undervalued vs BTC) — edge detect.
    Short alt: z > +2.0 (alt overvalued vs BTC) — edge detect.
    EMA50 trend filter on BTC applied to avoid trading against macro.

    If btc_close is None (coin IS BTC or BTC data unavailable), returns no signals.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame for the alt coin.
        btc_close: BTC close price Series (aligned to df's index).

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    signal = pd.Series(0, index=df.index)
    df["signal"] = signal

    if btc_close is None:
        return df

    # Align BTC close to this coin's index
    btc_aligned = btc_close.reindex(df.index, method="ffill")
    if btc_aligned.isna().all():
        return df

    alt_log = np.log(df["close"].replace(0, 1e-10))
    btc_log = np.log(btc_aligned.replace(0, 1e-10))

    spread = alt_log - btc_log
    spread_mean = spread.rolling(20).mean()
    spread_std = spread.rolling(20).std()
    zscore = (spread - spread_mean) / spread_std.replace(0, 1e-10)

    # BTC EMA50 macro filter
    btc_ema50 = btc_aligned.ewm(span=50, adjust=False).mean()
    btc_bullish = btc_aligned > btc_ema50
    btc_bearish = btc_aligned < btc_ema50

    enter_long = (zscore < -2.0) & (zscore.shift(1) >= -2.0) & btc_bullish
    enter_short = (zscore > 2.0) & (zscore.shift(1) <= 2.0) & btc_bearish

    window = _in_window(df)

    signal.loc[enter_long & window] = 1
    signal.loc[enter_short & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s20_renko_trend(df: pd.DataFrame) -> pd.DataFrame:
    """S20: Renko Trend.

    Brick size = 50-bar average ATR.
    Build Renko: new brick when price moves >= brick_size from last close.
    Direction: 1 = up bricks, -1 = down bricks.
    Entry: first up brick after 2+ consecutive down bricks AND close > EMA50.
    Short: first down brick after 2+ consecutive up bricks AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    close = df["close"]
    atr = df["atr"]

    brick_size = atr.rolling(50).mean()

    # Build Renko bricks
    renko_dir = np.zeros(len(close))
    last_brick_close = close.iloc[0]
    brick_count_up = 0
    brick_count_dn = 0

    for i in range(1, len(close)):
        bs = brick_size.iloc[i]
        if pd.isna(bs) or bs <= 0:
            renko_dir[i] = renko_dir[i - 1]
            continue

        c = close.iloc[i]
        move = c - last_brick_close

        if move >= bs:
            renko_dir[i] = 1
            brick_count_up += 1
            brick_count_dn = 0
            last_brick_close = c
        elif move <= -bs:
            renko_dir[i] = -1
            brick_count_dn += 1
            brick_count_up = 0
            last_brick_close = c
        else:
            renko_dir[i] = renko_dir[i - 1]

    renko_dir_s = pd.Series(renko_dir, index=close.index)

    # First up brick (flip from down to up)
    flip_up = (renko_dir_s == 1) & (renko_dir_s.shift(1) == -1)
    # First down brick (flip from up to down)
    flip_dn = (renko_dir_s == -1) & (renko_dir_s.shift(1) == 1)

    # Require prior bricks context: last 2 bricks before flip were the opposite
    # We encode this via ensuring renko was -1 for at least 2 bars before flip
    prior_2_dn = (renko_dir_s.shift(1) == -1) & (renko_dir_s.shift(2) == -1)
    prior_2_up = (renko_dir_s.shift(1) == 1) & (renko_dir_s.shift(2) == 1)

    window = _in_window(df)

    signal = pd.Series(0, index=df.index)
    signal[flip_up & prior_2_dn & (close > df["ema50"]) & window] = 1
    signal[flip_dn & prior_2_up & (close < df["ema50"]) & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES = [
    {"id": "S1",  "name": "Turtle 20/10",          "fn": strategy_s1_turtle,              "key": "S1_Turtle_2010"},
    {"id": "S2",  "name": "Connors RSI3",           "fn": strategy_s2_connors_rsi3,        "key": "S2_Connors_RSI3"},
    {"id": "S3",  "name": "Swing Breakout",         "fn": strategy_s3_swing_breakout,      "key": "S3_Swing_Breakout"},
    {"id": "S4",  "name": "EMA Ribbon",             "fn": strategy_s4_ema_ribbon,          "key": "S4_EMA_Ribbon"},
    {"id": "S5",  "name": "ATR Flip",               "fn": strategy_s5_atr_flip,            "key": "S5_ATR_Flip"},
    {"id": "S6",  "name": "RSI2 Extreme",           "fn": strategy_s6_rsi2_extreme,        "key": "S6_RSI2_Extreme"},
    {"id": "S7",  "name": "Eltrut Reverse",         "fn": strategy_s7_eltrut,              "key": "S7_Eltrut_Reverse"},
    {"id": "S8",  "name": "Stoch MTF",              "fn": strategy_s8_stoch_mtf,           "key": "S8_Stoch_MTF"},
    {"id": "S9",  "name": "ZScore MeanRev",         "fn": strategy_s9_zscore,              "key": "S9_ZScore_MeanRev"},
    {"id": "S10", "name": "Fib Retracement",        "fn": strategy_s10_fib_retracement,    "key": "S10_Fib_Retracement"},
    {"id": "S11", "name": "Pivot Breakout",         "fn": strategy_s11_pivot_breakout,     "key": "S11_Pivot_Breakout"},
    {"id": "S12", "name": "HeikinAshi Rev",         "fn": strategy_s12_heikinashi_reversal,"key": "S12_HeikinAshi_Rev"},
    {"id": "S13", "name": "ConsecRed DCA",          "fn": strategy_s13_consec_red_dca,     "key": "S13_ConsecRed_DCA"},
    {"id": "S14", "name": "VolSqueeze TTM",         "fn": strategy_s14_vol_squeeze,        "key": "S14_VolSqueeze"},
    {"id": "S15", "name": "Triple Confluence",      "fn": strategy_s15_triple_confluence,  "key": "S15_Triple_Confluence"},
    {"id": "S16", "name": "S/R Break",              "fn": strategy_s16_sr_break,           "key": "S16_SR_Break"},
    {"id": "S17", "name": "Candle Pattern",         "fn": strategy_s17_candle_pattern,     "key": "S17_CandlePattern"},
    {"id": "S18", "name": "Weighted Score",         "fn": strategy_s18_weighted_score,     "key": "S18_Weighted_Score"},
    {"id": "S19", "name": "Pairs Spread",           "fn": strategy_s19_pairs_spread,       "key": "S19_Pairs_Spread"},
    {"id": "S20", "name": "Renko Trend",            "fn": strategy_s20_renko_trend,        "key": "S20_Renko_Trend"},
]

# Strategies that need special kwargs during the sweep
# S8 needs df_4h kwarg; S19 needs btc_close kwarg
SPECIAL_STRATEGIES = {"S8", "S19"}


# ---------------------------------------------------------------------------
# Event-driven simulator (matches sweep_round9.py)
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    sl_mult: float = 2.0,
    tp_mult: float = 4.0,
    trail_mult: float = 0.0,
) -> Dict:
    """Simulate trades from 'signal' column.

    Signal at row i-1 triggers entry at row i's open (proxied by close).
    Exits on SL/TP hit within the bar. Trailing stop ratchets in profit direction.

    Args:
        df: DataFrame with 'signal', 'atr', 'high', 'low', 'close', 'dow' columns.
        sl_mult: Stop loss ATR multiplier.
        tp_mult: Take profit ATR multiplier (0 = use trailing stop only).
        trail_mult: Trailing stop ATR multiplier (0 = disabled).

    Returns:
        Dict with trades, tr_yr, wr, pf, dd, sharpe.
    """
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades: List[Dict] = []
    position: Optional[Dict] = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]  # closed candle (iloc[-2] pattern)

        # --- Manage open position ---
        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]

            # Ratchet trailing stop in profit direction only
            if trail_mult > 0 and position.get("trail"):
                atr_now = sig_row["atr"]
                if not pd.isna(atr_now) and atr_now > 0:
                    trail_dist = atr_now * trail_mult
                    if side == 1:
                        new_sl = row["high"] - trail_dist
                        if new_sl > sl:
                            position["sl"] = new_sl
                            sl = new_sl
                    else:
                        new_sl = row["low"] + trail_dist
                        if new_sl < sl:
                            position["sl"] = new_sl
                            sl = new_sl

            hit_sl = (side == 1 and row["low"] <= sl) or (
                side == -1 and row["high"] >= sl
            )
            hit_tp = (
                (side == 1 and row["high"] >= tp) or (side == -1 and row["low"] <= tp)
            ) if tp > 0 else False

            if hit_sl or hit_tp:
                exit_p = sl if hit_sl else tp
                pnl_pct = (
                    side * (exit_p - entry) / entry
                    - (COMMISSION + SLIPPAGE) * 2
                )
                pnl = balance * RISK_PCT * LEVERAGE * pnl_pct / sl_mult
                pnl = max(pnl, -balance * RISK_PCT * LEVERAGE)
                balance += pnl
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                trades.append({"pnl": pnl, "win": pnl > 0})
                position = None
                if balance <= 0:
                    break

        # --- Check for new signal ---
        if position is None and sig_row.get("signal", 0) != 0:
            if sig_row.get("dow", 0) >= 5:   # no weekend entries
                continue
            atr = sig_row["atr"]
            if pd.isna(atr) or atr <= 0:
                continue

            entry_p = row["close"]
            side = int(sig_row["signal"])
            sl_d = atr * sl_mult
            tp_d = atr * tp_mult if tp_mult > 0 else 0.0

            sl_p = entry_p - sl_d if side == 1 else entry_p + sl_d
            tp_p = (
                (entry_p + tp_d if side == 1 else entry_p - tp_d) if tp_d > 0 else 0.0
            )

            position = {
                "side": side,
                "entry": entry_p,
                "sl": sl_p,
                "tp": tp_p,
                "trail": trail_mult > 0,
            }

    # --- Compute metrics ---
    total = len(trades)
    if total == 0:
        return {
            "trades": 0,
            "tr_yr": 0.0,
            "wr": 0.0,
            "pf": 0.0,
            "dd": 0.0,
            "sharpe": 0.0,
        }

    wins = sum(1 for t in trades if t["win"])
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = sum(abs(t["pnl"]) for t in trades if t["pnl"] < 0)
    pf = gp / gl if gl > 0 else 0.0
    wr = wins / total * 100

    days = (df.index[-1] - df.index[0]).days if len(df) > 1 else 365
    yr = max(days / 365.25, 0.01)
    tr_yr = total / yr

    pnl_arr = np.array([t["pnl"] for t in trades])
    if len(pnl_arr) >= 4 and np.std(pnl_arr, ddof=1) > 0:
        sharpe = (np.mean(pnl_arr) * tr_yr) / (
            np.std(pnl_arr, ddof=1) * np.sqrt(tr_yr)
        )
    else:
        sharpe = 0.0

    return {
        "trades": total,
        "tr_yr": round(tr_yr, 1),
        "wr": round(wr, 1),
        "pf": round(pf, 3),
        "dd": round(max_dd * 100, 1),
        "sharpe": round(sharpe, 2),
    }


# ---------------------------------------------------------------------------
# Coin discovery
# ---------------------------------------------------------------------------

def discover_coins(
    coins_arg: Optional[List[str]],
    include_deployed: bool = False,
) -> List[str]:
    """Return list of coin prefixes from *_1h_2y.csv files.

    Args:
        coins_arg: Optional explicit list of coin prefixes (overrides file scan).
        include_deployed: If True, include already-deployed coins in sweep.

    Returns:
        Sorted list of coin prefixes.
    """
    if coins_arg:
        return sorted(set(c.lower().strip() for c in coins_arg if c.strip()))

    pattern = os.path.join(DATA_DIR, "*_1h_2y.csv")
    files = glob.glob(pattern)
    prefixes = []
    for f in sorted(files):
        base = os.path.basename(f)
        prefix = base.replace("_1h_2y.csv", "")
        if include_deployed or prefix not in DEPLOYED_PREFIXES:
            prefixes.append(prefix)
    return sorted(prefixes)


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(
    coins: List[str],
    min_pf: float = 1.0,
    min_trades: int = MIN_TRADES,
) -> List[Dict]:
    """Run all 20 strategies on 1H and 4H data for all coins.

    Prints progress every 10 coins.

    Args:
        coins: List of coin prefixes (e.g. ['ethusdt', 'bnbusdt']).
        min_pf: Minimum profit factor to include in output table.
        min_trades: Minimum trades required to include a result.

    Returns:
        List of all result dicts.
    """
    global _BTC_1H_CLOSE

    total_coins = len(coins)
    print(f"\nLoading 1H data for {total_coins} coins...")

    # Load BTC 1H for S19 pairs spread
    btc_path = os.path.join(DATA_DIR, "btcusdt_1h_2y.csv")
    if os.path.exists(btc_path):
        try:
            btc_raw = _load_ohlcv(btc_path)
            _BTC_1H_CLOSE = btc_raw["close"]
            print(f"  Loaded BTC 1H for S19 pairs spread ({len(btc_raw)} bars)")
        except Exception as exc:
            print(f"  WARN: could not load BTC 1H for S19: {exc}")
            _BTC_1H_CLOSE = None
    else:
        print("  WARN: btcusdt_1h_2y.csv not found; S19 will produce no signals")
        _BTC_1H_CLOSE = None

    loaded: Dict[str, pd.DataFrame] = {}
    for prefix in coins:
        name = prefix.replace("usdt", "").upper()
        fpath = os.path.join(DATA_DIR, f"{prefix}_1h_2y.csv")
        if not os.path.exists(fpath):
            continue
        try:
            raw = _load_ohlcv(fpath)
            # Filter: require at least 12 months of data
            days_available = (raw.index[-1] - raw.index[0]).days if len(raw) > 1 else 0
            if len(raw) < 300 or days_available < 365:
                continue
            loaded[name] = compute_base(raw)
        except Exception as exc:
            print(f"  WARN: could not load {fpath}: {exc}")

    print(f"Loaded {len(loaded)} coins with 1H data (>= 12 months).")
    total_runs = len(STRATEGIES) * 2 * len(loaded)
    print(
        f"Testing {len(STRATEGIES)} strategies × 2 timeframes (1H + 4H) = "
        f"{len(STRATEGIES) * 2} combos per coin × {len(loaded)} coins = "
        f"{total_runs} total runs\n"
    )

    all_results: List[Dict] = []
    coins_done = 0

    for name, df_1h in sorted(loaded.items()):
        prefix = name.lower() + "usdt"
        is_deployed = prefix in DEPLOYED_PREFIXES

        # Build 4H resample once per coin
        try:
            df_4h_raw = (
                df_1h[["open", "high", "low", "close", "volume"]]
                .resample("4h")
                .agg(
                    {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volume": "sum",
                    }
                )
                .dropna(subset=["close"])
            )
            df_4h = compute_base(df_4h_raw) if len(df_4h_raw) >= 60 else None
        except Exception as exc:
            print(f"  WARN: 4H resample failed for {name}: {exc}")
            df_4h = None

        for spec in STRATEGIES:
            strat_id = spec["id"]
            strat_name = spec["name"]
            strat_key = spec["key"]
            sl_m, tp_m, trail_m = STRATEGY_PARAMS.get(strat_key, (2.0, 4.0, 0.0))

            for tf_label, df_in in [("1H", df_1h), ("4H", df_4h)]:
                if df_in is None:
                    continue

                full_name = f"{strat_id} {strat_name} {tf_label}"
                try:
                    # Handle strategies with special parameters
                    if strat_id == "S8":
                        # Pass 4H dataframe for MTF stochastic (only for 1H mode)
                        if tf_label == "1H":
                            df_sig = spec["fn"](df_in, df_4h=df_4h)
                        else:
                            # On 4H, no higher timeframe available — use EMA fallback
                            df_sig = spec["fn"](df_in, df_4h=None)
                    elif strat_id == "S19":
                        # Pass BTC close; skip if this coin IS BTC
                        if prefix == "btcusdt" or _BTC_1H_CLOSE is None:
                            continue
                        if tf_label == "1H":
                            btc_close = _BTC_1H_CLOSE
                        else:
                            # Resample BTC close to 4H for 4H pairs spread
                            if _BTC_1H_CLOSE is not None:
                                btc_4h = (
                                    _BTC_1H_CLOSE
                                    .resample("4h")
                                    .last()
                                    .reindex(df_in.index, method="ffill")
                                )
                                btc_close = btc_4h
                            else:
                                continue
                        df_sig = spec["fn"](df_in, btc_close=btc_close)
                    else:
                        df_sig = spec["fn"](df_in)

                    r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m, trail_mult=trail_m)
                except Exception as exc:
                    # Silently skip — keep sweep moving
                    _ = exc
                    continue

                if r["trades"] < min_trades:
                    continue

                result = {
                    "coin": name,
                    "prefix": prefix,
                    "is_deployed": is_deployed,
                    "strategy_id": strat_id,
                    "strategy": strat_name,
                    "timeframe": tf_label,
                    "full_name": full_name,
                    "pf": r["pf"],
                    "wr_pct": r["wr"],
                    "trades": r["trades"],
                    "tr_yr": r["tr_yr"],
                    "sharpe": r["sharpe"],
                    "dd_pct": r["dd"],
                    "sl_mult": sl_m,
                    "tp_mult": tp_m,
                    "trail_mult": trail_m,
                }
                all_results.append(result)

        coins_done += 1
        if coins_done % 10 == 0 or coins_done == total_coins:
            print(
                f"Progress: {coins_done}/{total_coins} coins done "
                f"({len(all_results)} results so far)"
            )

    return all_results


# ---------------------------------------------------------------------------
# Output and summary
# ---------------------------------------------------------------------------

def _print_results_table(results: List[Dict], min_pf: float, min_trades: int) -> None:
    """Print results table sorted by PF descending.

    Args:
        results: All result dicts.
        min_pf: Minimum PF for inclusion.
        min_trades: Minimum trade count for inclusion.
    """
    winners = [r for r in results if r["pf"] >= min_pf and r["trades"] >= min_trades]
    winners.sort(key=lambda x: -x["pf"])

    hdr = (
        f"{'Coin':<12} {'Strategy':<22} {'TF':<4} "
        f"{'PF':>6} {'WR%':>6} {'Trades':>7} {'Sharpe':>8}"
    )
    sep = "-" * len(hdr)
    print(f"\nRESULTS: 20 new strategies across all coins (2yr 1H + 4H resample)")
    print(hdr)
    print(sep)
    for r in winners:
        deployed_tag = " *" if r["is_deployed"] else ""
        print(
            f"{r['coin']:<12} {r['strategy']:<22} {r['timeframe']:<4} "
            f"{r['pf']:>6.2f} {r['wr_pct']:>5.1f}% {r['trades']:>7} "
            f"{r['sharpe']:>8.2f}{deployed_tag}"
        )
    print(sep)
    print(
        f"Total shown (PF >= {min_pf}, trades >= {min_trades}): "
        f"{len(winners)} / {len(results)}\n"
    )


def _print_strategy_ranking(results: List[Dict]) -> None:
    """Print strategy ranking by average PF across all coins.

    Args:
        results: All result dicts.
    """
    strat_pf: Dict[str, List[float]] = {}
    for r in results:
        key = f"{r['strategy_id']} {r['strategy']} {r['timeframe']}"
        strat_pf.setdefault(key, []).append(r["pf"])

    rows = []
    for strat, pfs in strat_pf.items():
        avg_pf = np.mean(pfs)
        n_above_1 = sum(1 for p in pfs if p >= 1.0)
        n_above_13 = sum(1 for p in pfs if p >= 1.3)
        n_above_20 = sum(1 for p in pfs if p >= 2.0)
        rows.append((strat, avg_pf, n_above_1, n_above_13, n_above_20, len(pfs)))

    rows.sort(key=lambda x: -x[1])

    print("=" * 80)
    print("STRATEGY RANKING (avg PF across all coins with >= 4 trades)")
    print("=" * 80)
    print(
        f"  {'Strategy':<34} {'AvgPF':>7} {'>=1.0':>6} "
        f"{'>=1.3':>6} {'>=2.0':>6} {'N':>6}"
    )
    print("  " + "-" * 68)
    for row in rows:
        print(
            f"  {row[0]:<34} {row[1]:>7.2f} {row[2]:>6} "
            f"{row[3]:>6} {row[4]:>6} {row[5]:>6}"
        )


def _print_top30(results: List[Dict]) -> None:
    """Print top 30 coin x strategy combinations with PF >= 2.0.

    Args:
        results: All result dicts.
    """
    top = [r for r in results if r["pf"] >= 2.0]
    top.sort(key=lambda x: -x["pf"])
    top30 = top[:30]

    print("\n" + "=" * 95)
    print("TOP 30 COIN × STRATEGY COMBINATIONS (PF >= 2.0)")
    print("=" * 95)
    if not top30:
        print("  No combinations with PF >= 2.0 found.")
        return

    hdr = (
        f"  {'#':<4} {'Coin':<12} {'Strategy':<22} {'TF':<4} "
        f"{'PF':>6} {'WR%':>6} {'Trades':>7} {'Sharpe':>8} {'Deployed':<10}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for i, r in enumerate(top30, 1):
        deployed = "YES" if r["is_deployed"] else ""
        print(
            f"  {i:<4} {r['coin']:<12} {r['strategy']:<22} {r['timeframe']:<4} "
            f"{r['pf']:>6.2f} {r['wr_pct']:>5.1f}% {r['trades']:>7} "
            f"{r['sharpe']:>8.2f} {deployed:<10}"
        )


def _print_new_coin_candidates(results: List[Dict]) -> None:
    """Print new coins (not deployed) with PF >= 1.5.

    Shows best strategy per new coin.

    Args:
        results: All result dicts.
    """
    new_coin_results = [r for r in results if not r["is_deployed"] and r["pf"] >= 1.5]

    best_per_coin: Dict[str, Dict] = {}
    for r in new_coin_results:
        coin = r["coin"]
        if coin not in best_per_coin or r["pf"] > best_per_coin[coin]["pf"]:
            best_per_coin[coin] = r

    rows = sorted(best_per_coin.values(), key=lambda x: -x["pf"])

    print("\n" + "=" * 95)
    print("NEW COINS NOT IN PORTFOLIO WITH PF >= 1.5 (best strategy per coin)")
    print("=" * 95)
    if not rows:
        print("  No new coins found with PF >= 1.5.")
        return

    hdr = (
        f"  {'Coin':<12} {'Best Strategy':<26} {'TF':<4} "
        f"{'PF':>6} {'WR%':>6} {'Trades':>7} {'Sharpe':>8}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        full = f"{r['strategy_id']} {r['strategy']}"
        print(
            f"  {r['coin']:<12} {full:<26} {r['timeframe']:<4} "
            f"{r['pf']:>6.2f} {r['wr_pct']:>5.1f}% {r['trades']:>7} "
            f"{r['sharpe']:>8.2f}"
        )

    print(f"\n  Total new candidate coins: {len(rows)}")


def _print_strategy_per_class(results: List[Dict]) -> None:
    """Print top-performing coin for each strategy class.

    Args:
        results: All result dicts.
    """
    print("\n" + "=" * 95)
    print("BEST COIN PER STRATEGY (highest PF, new coins only)")
    print("=" * 95)

    by_strat: Dict[str, List[Dict]] = {}
    for r in results:
        if not r["is_deployed"]:
            key = f"{r['strategy_id']} {r['strategy']} {r['timeframe']}"
            by_strat.setdefault(key, []).append(r)

    keys_sorted = sorted(
        by_strat.keys(),
        key=lambda k: max((r["pf"] for r in by_strat[k]), default=0),
        reverse=True,
    )

    hdr = (
        f"  {'Strategy + TF':<36} {'Best Coin':<12} "
        f"{'PF':>6} {'WR%':>6} {'Trades':>7}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for key in keys_sorted:
        candidates = by_strat[key]
        if not candidates:
            continue
        best = max(candidates, key=lambda r: r["pf"])
        if best["pf"] < 1.0:
            continue
        print(
            f"  {key:<36} {best['coin']:<12} "
            f"{best['pf']:>6.2f} {best['wr_pct']:>5.1f}% {best['trades']:>7}"
        )


def _save_results(results: List[Dict]) -> None:
    """Save all results to JSON output file.

    Args:
        results: All result dicts.
    """
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved {len(results)} result(s) to {OUTPUT_FILE}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="sweep_round11: 20 new strategies × 133 coins × 1H + 4H",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help="Comma-separated coin prefixes, e.g. btcusdt,ethusdt",
    )
    parser.add_argument(
        "--min-pf",
        type=float,
        default=1.0,
        help="Minimum PF to include in results table (default: 1.0)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=MIN_TRADES,
        help=f"Minimum trades to include a result (default: {MIN_TRADES})",
    )
    parser.add_argument(
        "--all-coins",
        action="store_true",
        default=False,
        help="Include currently deployed coins in the sweep (default: exclude)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    coins_arg = [c.strip() for c in args.coins.split(",")] if args.coins else None
    coins = discover_coins(coins_arg, include_deployed=args.all_coins)

    if not coins:
        print("No coins found. Check data/ for *_1h_2y.csv files or pass --coins.")
        sys.exit(1)

    print(f"sweep_round11: 20 new strategies, 1H + 4H, {len(coins)} coins")
    print(f"Filter: PF >= {args.min_pf}, trades >= {args.min_trades}")
    print(f"Deployed coins: {'included' if args.all_coins else 'excluded'}")
    print(f"Coins: {', '.join(coins[:12])}{'...' if len(coins) > 12 else ''}")

    all_results = run_sweep(coins, min_pf=args.min_pf, min_trades=args.min_trades)

    if all_results:
        _save_results(all_results)

    _print_results_table(all_results, min_pf=args.min_pf, min_trades=args.min_trades)
    _print_strategy_ranking(all_results)
    _print_top30(all_results)
    _print_new_coin_candidates(all_results)
    _print_strategy_per_class(all_results)

    print("\nDone.")
