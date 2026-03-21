"""sweep_round9.py — Round 9 strategy sweep: 20 new indicators across 133 coins.

Tests 20 never-before-tested strategies on 1H and 4H timeframes.
All strategies use closed-candle signals (iloc[-2] pattern) and the same
event-driven simulator as previous sweeps.

Strategies implemented:
  S1  OBV Breakout            — OBV crosses 20-period high with EMA50 filter
  S2  CMF Cross               — Chaikin Money Flow crosses zero with EMA50 filter
  S3  Williams %R + ADX       — WR crosses out of extreme zone with ADX > 25 trend
  S4  LinReg Channel Breakout — Price breaks 2-std band of linear regression channel
  S5  Heikin-Ashi Trend       — 2+ consecutive no-wick HA candles with EMA50 filter
  S6  ADX + DI Cross          — DI+/DI- crossover with ADX > 20 and rising ADX
  S7  Keltner + ADX           — Price breaks Keltner channel with ADX > 25 momentum
  S8  TRIX Cross              — TRIX line crosses signal line with EMA50 filter
  S9  Vortex Cross            — VI+ / VI- crossover with EMA50 filter
  S10 Donchian + ADX          — Donchian channel breakout with ADX > 25
  S11 Choppiness + EMA Cross  — EMA(9/21) cross only when CI < 38.2 (trending)
  S12 ROC Momentum            — ROC(10) crosses zero with EMA50 filter
  S13 Hull MA Cross           — HMA(9) crosses HMA(21) with EMA50 filter
  S14 KAMA Cross              — Price crosses KAMA with EMA alignment
  S15 Price Channel + Volume  — Donchian channel breakout with 2x volume surge
  S16 Stochastic + Supertrend — Stoch K/D cross in extreme zone + supertrend direction
  S17 DEMA Cross              — DEMA(9) crosses DEMA(21) with EMA50 filter
  S18 EMA + Alligator         — EMA9/21 cross with Alligator lips/teeth/jaw aligned
  S19 RSI + Ichimoku          — RSI crosses 50 with price above/below cloud
  S20 Supertrend + Volume     — Supertrend direction flip with 2x volume surge

Usage:
    python3 -u research/sweep_round9.py
    python3 -u research/sweep_round9.py --coins btcusdt,ethusdt
    python3 -u research/sweep_round9.py --min-pf 1.5 --min-trades 6
    python3 -u research/sweep_round9.py --all-coins  # include deployed coins too
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
OUTPUT_FILE = os.path.join(DATA_DIR, "sweep_round9.json")

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
WARMUP_BARS = 100      # skip first N bars for all indicators to settle

# SL/TP defaults per strategy — chosen to match indicator characteristics
# Format: (sl_mult, tp_mult, trail_mult)   trail_mult=0 means fixed TP
STRATEGY_PARAMS: Dict[str, Tuple[float, float, float]] = {
    "S1_OBV_Breakout":        (2.0, 4.0, 0.0),
    "S2_CMF_Cross":           (2.0, 4.0, 0.0),
    "S3_WilliamsR_ADX":       (2.0, 4.0, 0.0),
    "S4_LinReg_Breakout":     (2.0, 4.0, 0.0),
    "S5_HeikinAshi_Trend":    (2.0, 4.0, 0.0),
    "S6_ADX_DI_Cross":        (2.0, 4.0, 0.0),
    "S7_Keltner_ADX":         (2.0, 4.0, 0.0),
    "S8_TRIX_Cross":          (2.0, 4.0, 0.0),
    "S9_Vortex_Cross":        (2.0, 4.0, 0.0),
    "S10_Donchian_ADX":       (2.0, 4.0, 0.0),
    "S11_Choppiness_EMA":     (2.0, 4.0, 0.0),
    "S12_ROC_Momentum":       (2.0, 4.0, 0.0),
    "S13_HullMA_Cross":       (2.0, 4.0, 0.0),
    "S14_KAMA_Cross":         (2.0, 4.0, 0.0),
    "S15_PriceChannel_Vol":   (2.0, 4.0, 0.0),
    "S16_Stoch_Supertrend":   (2.0, 4.0, 0.0),
    "S17_DEMA_Cross":         (2.0, 4.0, 0.0),
    "S18_EMA_Alligator":      (2.0, 4.0, 0.0),
    "S19_RSI_Ichimoku":       (2.0, 4.0, 0.0),
    "S20_Supertrend_Volume":  (2.0, 4.0, 0.0),
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


# ---------------------------------------------------------------------------
# OHLCV loader (avoids ccxt import inside data_loader)
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
    vol_ratio, hour, dow (day of week).

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

    # ATR rolling mean for regime filter
    df["atr_ma50"] = df["atr"].rolling(50).mean()

    # Trading window helpers
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
# Indicator implementations
# ---------------------------------------------------------------------------

def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume.

    Accumulates volume positively when close rises and negatively when it falls.

    Args:
        close: Close price series.
        volume: Volume series.

    Returns:
        OBV series.
    """
    obv_vals = np.zeros(len(close))
    c = close.values
    v = volume.values
    for i in range(1, len(c)):
        if c[i] > c[i - 1]:
            obv_vals[i] = obv_vals[i - 1] + v[i]
        elif c[i] < c[i - 1]:
            obv_vals[i] = obv_vals[i - 1] - v[i]
        else:
            obv_vals[i] = obv_vals[i - 1]
    return pd.Series(obv_vals, index=close.index)


def compute_cmf(
    high: pd.Series, low: pd.Series, close: pd.Series,
    volume: pd.Series, period: int = 20,
) -> pd.Series:
    """Chaikin Money Flow.

    Measures buying/selling pressure by combining price position within the
    high-low range with volume.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        volume: Volume series.
        period: Lookback period (default 20).

    Returns:
        CMF series in range [-1, 1].
    """
    hl_range = (high - low).replace(0, 1e-10)
    mfm = ((close - low) - (high - close)) / hl_range
    mfv = mfm * volume
    cmf = mfv.rolling(period).sum() / volume.rolling(period).sum().replace(0, 1e-10)
    return cmf


def compute_williams_r(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
) -> pd.Series:
    """Williams %R oscillator.

    Measures close relative to the period high-low range, returns -100 to 0.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: Lookback period (default 14).

    Returns:
        Williams %R series in range [-100, 0].
    """
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    wr = -100 * (hh - close) / (hh - ll).replace(0, 1e-10)
    return wr


def compute_linreg(close: pd.Series, period: int = 20) -> Tuple[pd.Series, pd.Series]:
    """Linear regression channel: center line + residual standard deviation.

    Uses numpy polyfit on rolling windows. Note: O(n*period) — keep period small.

    Args:
        close: Close price series.
        period: Regression window length (default 20).

    Returns:
        Tuple of (center, std_dev) Series.
    """
    n = len(close)
    center_vals = np.full(n, np.nan)
    std_vals = np.full(n, np.nan)
    c = close.values
    x = np.arange(period)
    for i in range(period, n):
        y = c[i - period:i]
        coeffs = np.polyfit(x, y, 1)
        predicted = coeffs[0] * (period - 1) + coeffs[1]
        residuals = y - (coeffs[0] * x + coeffs[1])
        center_vals[i] = predicted
        std_vals[i] = np.std(residuals)
    return (
        pd.Series(center_vals, index=close.index),
        pd.Series(std_vals, index=close.index),
    )


def compute_heikin_ashi(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Heikin-Ashi candles.

    HA_Close = (O+H+L+C)/4
    HA_Open  = (prev_HA_Open + prev_HA_Close) / 2

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


def compute_adx_full(
    df: pd.DataFrame, period: int = 14,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """ADX with +DI and -DI using Wilder's smoothing.

    Args:
        df: DataFrame with high/low/close columns.
        period: ADX smoothing period (default 14).

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

    plus_di = 100 * pdm_s / atr_s.clip(lower=1e-10)
    minus_di = 100 * mdm_s / atr_s.clip(lower=1e-10)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).clip(lower=1e-10)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    return adx, plus_di, minus_di


def compute_keltner(
    close: pd.Series, high: pd.Series, low: pd.Series,
    ema_period: int = 20, atr_mult: float = 2.0, atr_period: int = 14,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Keltner Channel: EMA ± ATR*mult.

    Args:
        close: Close price series.
        high: High price series.
        low: Low price series.
        ema_period: EMA smoothing period (default 20).
        atr_mult: ATR multiplier for band width (default 2.0).
        atr_period: ATR period (default 14).

    Returns:
        Tuple of (mid, upper, lower) Series.
    """
    mid = compute_ema(close, ema_period)
    atr = compute_atr(high, low, close, atr_period)
    upper = mid + atr_mult * atr
    lower = mid - atr_mult * atr
    return mid, upper, lower


def compute_trix(close: pd.Series, period: int = 15) -> Tuple[pd.Series, pd.Series]:
    """TRIX: triple-smoothed EMA percentage change.

    Args:
        close: Close price series.
        period: EMA smoothing period (default 15).

    Returns:
        Tuple of (trix, signal) Series.
    """
    ema1 = close.ewm(span=period, adjust=False).mean()
    ema2 = ema1.ewm(span=period, adjust=False).mean()
    ema3 = ema2.ewm(span=period, adjust=False).mean()
    trix = ema3.pct_change() * 100
    signal = trix.ewm(span=9, adjust=False).mean()
    return trix, signal


def compute_vortex(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
) -> Tuple[pd.Series, pd.Series]:
    """Vortex Indicator: VI+ and VI- lines.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: Lookback period (default 14).

    Returns:
        Tuple of (vi_plus, vi_minus) Series.
    """
    vm_plus = (high - low.shift(1)).abs()
    vm_minus = (low - high.shift(1)).abs()
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    vi_plus = vm_plus.rolling(period).sum() / tr.rolling(period).sum().replace(0, 1e-10)
    vi_minus = vm_minus.rolling(period).sum() / tr.rolling(period).sum().replace(0, 1e-10)
    return vi_plus, vi_minus


def compute_choppiness(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
) -> pd.Series:
    """Choppiness Index: 100 * log10(sum_atr / range) / log10(period).

    Values near 38.2 → strong trend; values near 61.8 → choppy/ranging.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: Lookback period (default 14).

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
    ci = 100 * np.log10(atr_sum / (hh - ll).replace(0, 1e-10)) / np.log10(period)
    return ci


def compute_hma(close: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average: 2*WMA(half) - WMA(full), then WMA(sqrt(period)).

    Uses rolling().apply() with linear weights — accurate but not blazing fast.

    Args:
        close: Close price series.
        period: HMA period.

    Returns:
        HMA series.
    """
    half = max(int(period / 2), 2)
    sqrt_p = max(int(np.sqrt(period)), 2)

    def wma(series: pd.Series, n: int) -> pd.Series:
        weights = np.arange(1, n + 1, dtype=float)
        return series.rolling(n).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )

    diff = 2 * wma(close, half) - wma(close, period)
    return wma(diff, sqrt_p)


def compute_kama(
    close: pd.Series, period: int = 10, fast: int = 2, slow: int = 30,
) -> pd.Series:
    """Kaufman Adaptive Moving Average.

    Adapts smoothing based on the Efficiency Ratio (direction / volatility).

    Args:
        close: Close price series.
        period: ER lookback period (default 10).
        fast: Fast EMA constant (default 2, meaning EMA span 2).
        slow: Slow EMA constant (default 30, meaning EMA span 30).

    Returns:
        KAMA series.
    """
    direction = (close - close.shift(period)).abs()
    volatility = close.diff().abs().rolling(period).sum()
    er = direction / volatility.replace(0, 1e-10)

    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2

    kama_vals = close.values.copy().astype(float)
    sc_vals = sc.values

    # Set initial valid KAMA at index `period`
    for i in range(period + 1, len(kama_vals)):
        if np.isnan(sc_vals[i]) or np.isnan(kama_vals[i - 1]):
            continue
        kama_vals[i] = kama_vals[i - 1] + sc_vals[i] * (kama_vals[i] - kama_vals[i - 1])

    return pd.Series(kama_vals, index=close.index)


def compute_dema(close: pd.Series, period: int) -> pd.Series:
    """Double EMA: 2*EMA - EMA(EMA).

    Reduces lag vs standard EMA.

    Args:
        close: Close price series.
        period: EMA period.

    Returns:
        DEMA series.
    """
    ema1 = close.ewm(span=period, adjust=False).mean()
    ema2 = ema1.ewm(span=period, adjust=False).mean()
    return 2 * ema1 - ema2


def _compute_supertrend_fast(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 14, multiplier: float = 2.0,
) -> Tuple[pd.Series, pd.Series]:
    """Fast numpy Supertrend (same as sweep_new_strategies_1.py).

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


def _compute_stochrsi(
    close: pd.Series,
    rsi_period: int = 14, stoch_period: int = 14,
    k_smooth: int = 3, d_smooth: int = 3,
) -> Tuple[pd.Series, pd.Series]:
    """Stochastic RSI K and D lines.

    Args:
        close: Close price series.
        rsi_period: RSI period.
        stoch_period: Stochastic lookback on RSI values.
        k_smooth: K smoothing period.
        d_smooth: D smoothing period.

    Returns:
        Tuple of (K, D) Series in range [0, 1].
    """
    rsi = compute_rsi(close, rsi_period)
    rsi_min = rsi.rolling(stoch_period).min()
    rsi_max = rsi.rolling(stoch_period).max()
    stochrsi = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, 1e-10)
    k = stochrsi.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d


def _compute_smma(series: pd.Series, period: int) -> pd.Series:
    """Smoothed Moving Average (Wilder's method) for Alligator.

    Args:
        series: Input price series.
        period: Smoothing period.

    Returns:
        SMMA series.
    """
    vals = series.values.astype(float)
    out = np.full(len(vals), np.nan)
    if len(vals) < period:
        return pd.Series(out, index=series.index)
    out[period - 1] = np.nanmean(vals[:period])
    alpha = (period - 1) / period
    for i in range(period, len(vals)):
        if not np.isnan(out[i - 1]):
            out[i] = alpha * out[i - 1] + (1.0 - alpha) * vals[i]
    return pd.Series(out, index=series.index)


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
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)
    return tenkan, kijun, cloud_top, cloud_bottom


# ---------------------------------------------------------------------------
# Strategy signal generators — each returns df with 'signal' column
# ---------------------------------------------------------------------------

def strategy_s1_obv_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """S1: OBV Breakout.

    Entry: OBV crosses above its 20-period rolling max AND close > EMA50.
    Short: OBV crosses below its 20-period rolling min AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    obv = compute_obv(df["close"], df["volume"])
    obv_high = obv.rolling(20).max().shift(1)   # shift(1): closed-candle reference
    obv_low = obv.rolling(20).min().shift(1)

    window = _in_window(df)
    long_sig = (obv > obv_high) & (obv.shift(1) <= obv_high.shift(1)) & (df["close"] > df["ema50"])
    short_sig = (obv < obv_low) & (obv.shift(1) >= obv_low.shift(1)) & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s2_cmf_cross(df: pd.DataFrame) -> pd.DataFrame:
    """S2: CMF Cross.

    Entry: CMF crosses above 0 AND close > EMA50.
    Short: CMF crosses below 0 AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    cmf = compute_cmf(df["high"], df["low"], df["close"], df["volume"], period=20)
    window = _in_window(df)

    cross_up = (cmf >= 0) & (cmf.shift(1) < 0)
    cross_dn = (cmf <= 0) & (cmf.shift(1) > 0)

    long_sig = cross_up & (df["close"] > df["ema50"])
    short_sig = cross_dn & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s3_williamsr_adx(df: pd.DataFrame) -> pd.DataFrame:
    """S3: Williams %R + ADX.

    Entry: WR crosses above -80 (from oversold) AND ADX > 25 AND close > EMA50.
    Short: WR crosses below -20 (from overbought) AND ADX > 25 AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    wr = compute_williams_r(df["high"], df["low"], df["close"], 14)
    adx, _, _ = compute_adx_full(df, 14)
    window = _in_window(df)

    # Cross out of oversold: WR was below -80, now above -80
    wr_cross_up = (wr >= -80) & (wr.shift(1) < -80)
    # Cross out of overbought: WR was above -20, now below -20
    wr_cross_dn = (wr <= -20) & (wr.shift(1) > -20)

    adx_ok = adx > 25

    long_sig = wr_cross_up & adx_ok & (df["close"] > df["ema50"])
    short_sig = wr_cross_dn & adx_ok & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s4_linreg_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """S4: Linear Regression Channel Breakout.

    Entry: Close breaks above center + 2*std AND EMA50 bullish.
    Short: Close breaks below center - 2*std AND EMA50 bearish.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    center, std_dev = compute_linreg(df["close"], period=20)
    upper_band = center + 2.0 * std_dev
    lower_band = center - 2.0 * std_dev
    window = _in_window(df)

    cross_above_upper = (df["close"] >= upper_band) & (df["close"].shift(1) < upper_band.shift(1))
    cross_below_lower = (df["close"] <= lower_band) & (df["close"].shift(1) > lower_band.shift(1))

    long_sig = cross_above_upper & (df["close"] > df["ema50"])
    short_sig = cross_below_lower & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s5_heikinashi_trend(df: pd.DataFrame) -> pd.DataFrame:
    """S5: Heikin-Ashi Trend.

    Entry: 2 consecutive HA candles with no lower wick (ha_low == ha_open within 0.1%)
           AND close > EMA50.
    Short: 2 consecutive HA candles with no upper wick (ha_high == ha_open within 0.1%)
           AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    ha_open, ha_high, ha_low, ha_close = compute_heikin_ashi(
        df["open"], df["high"], df["low"], df["close"]
    )

    # Tolerance: 0.1% of close price
    tol = df["close"] * 0.001

    # Bullish HA: no lower wick (HA low == HA open)
    bull_candle = (ha_open - ha_low).abs() <= tol
    # Bearish HA: no upper wick (HA high == HA open)
    bear_candle = (ha_high - ha_open).abs() <= tol

    # Require 2 consecutive
    bull_2 = bull_candle & bull_candle.shift(1)
    bear_2 = bear_candle & bear_candle.shift(1)

    # Transition: not both were true 3 bars ago (avoid repeated signals in a run)
    bull_entry = bull_2 & ~bull_2.shift(1).fillna(False)
    bear_entry = bear_2 & ~bear_2.shift(1).fillna(False)

    window = _in_window(df)
    long_sig = bull_entry & (df["close"] > df["ema50"])
    short_sig = bear_entry & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s6_adx_di_cross(df: pd.DataFrame) -> pd.DataFrame:
    """S6: ADX + DI Cross.

    Entry: DI+ crosses above DI- AND ADX > 20 AND ADX is rising.
    Short: DI- crosses above DI+ AND ADX > 20 AND ADX is rising.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    adx, plus_di, minus_di = compute_adx_full(df, 14)
    window = _in_window(df)

    adx_rising = adx > adx.shift(1)
    adx_strong = adx > 20

    di_cross_up = (plus_di > minus_di) & (plus_di.shift(1) <= minus_di.shift(1))
    di_cross_dn = (minus_di > plus_di) & (minus_di.shift(1) <= plus_di.shift(1))

    long_sig = di_cross_up & adx_strong & adx_rising
    short_sig = di_cross_dn & adx_strong & adx_rising

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s7_keltner_adx(df: pd.DataFrame) -> pd.DataFrame:
    """S7: Keltner Channel + ADX.

    Entry: Close breaks above Keltner upper band AND ADX > 25.
    Short: Close breaks below Keltner lower band AND ADX > 25.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    _mid, kc_upper, kc_lower = compute_keltner(
        df["close"], df["high"], df["low"], ema_period=20, atr_mult=2.0
    )
    adx, _, _ = compute_adx_full(df, 14)
    window = _in_window(df)

    break_upper = (df["close"] > kc_upper) & (df["close"].shift(1) <= kc_upper.shift(1))
    break_lower = (df["close"] < kc_lower) & (df["close"].shift(1) >= kc_lower.shift(1))

    long_sig = break_upper & (adx > 25)
    short_sig = break_lower & (adx > 25)

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s8_trix_cross(df: pd.DataFrame) -> pd.DataFrame:
    """S8: TRIX Cross.

    Entry: TRIX crosses above its 9-period signal line AND close > EMA50.
    Short: TRIX crosses below signal AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    trix, trix_sig = compute_trix(df["close"], period=15)
    window = _in_window(df)

    cross_up = (trix > trix_sig) & (trix.shift(1) <= trix_sig.shift(1))
    cross_dn = (trix < trix_sig) & (trix.shift(1) >= trix_sig.shift(1))

    long_sig = cross_up & (df["close"] > df["ema50"])
    short_sig = cross_dn & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s9_vortex_cross(df: pd.DataFrame) -> pd.DataFrame:
    """S9: Vortex Cross.

    Entry: VI+ crosses above VI- AND close > EMA50.
    Short: VI- crosses above VI+ AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    vi_plus, vi_minus = compute_vortex(df["high"], df["low"], df["close"], 14)
    window = _in_window(df)

    cross_up = (vi_plus > vi_minus) & (vi_plus.shift(1) <= vi_minus.shift(1))
    cross_dn = (vi_minus > vi_plus) & (vi_minus.shift(1) <= vi_plus.shift(1))

    long_sig = cross_up & (df["close"] > df["ema50"])
    short_sig = cross_dn & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s10_donchian_adx(df: pd.DataFrame) -> pd.DataFrame:
    """S10: Donchian Channel + ADX.

    Entry: Close exceeds prior period's 20-bar high AND ADX > 25.
    Short: Close falls below prior period's 20-bar low AND ADX > 25.

    Uses shift(1) on the channel to avoid look-ahead on the current bar.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    adx, _, _ = compute_adx_full(df, 14)
    # shift(1): channel from previous bar (closed candle reference)
    don_high = df["high"].rolling(20).max().shift(1)
    don_low = df["low"].rolling(20).min().shift(1)
    window = _in_window(df)

    break_high = (df["close"] > don_high) & (df["close"].shift(1) <= don_high.shift(1))
    break_low = (df["close"] < don_low) & (df["close"].shift(1) >= don_low.shift(1))

    long_sig = break_high & (adx > 25)
    short_sig = break_low & (adx > 25)

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s11_choppiness_ema(df: pd.DataFrame) -> pd.DataFrame:
    """S11: Choppiness Index + EMA Cross.

    Entry: EMA9 crosses above EMA21 AND Choppiness Index < 38.2 (trending).
    Short: EMA9 crosses below EMA21 AND CI < 38.2.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    ci = compute_choppiness(df["high"], df["low"], df["close"], 14)
    window = _in_window(df)

    ema_cross_up = (df["ema9"] > df["ema21"]) & (df["ema9"].shift(1) <= df["ema21"].shift(1))
    ema_cross_dn = (df["ema9"] < df["ema21"]) & (df["ema9"].shift(1) >= df["ema21"].shift(1))

    trending = ci < 38.2

    long_sig = ema_cross_up & trending
    short_sig = ema_cross_dn & trending

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s12_roc_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """S12: ROC Momentum.

    Entry: ROC(10) crosses above 0 AND close > EMA50.
    Short: ROC(10) crosses below 0 AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    roc = df["close"].pct_change(10) * 100
    window = _in_window(df)

    cross_up = (roc >= 0) & (roc.shift(1) < 0)
    cross_dn = (roc <= 0) & (roc.shift(1) > 0)

    long_sig = cross_up & (df["close"] > df["ema50"])
    short_sig = cross_dn & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s13_hullma_cross(df: pd.DataFrame) -> pd.DataFrame:
    """S13: Hull MA Cross.

    Entry: HMA(9) crosses above HMA(21) AND close > EMA50.
    Short: HMA(9) crosses below HMA(21) AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    hma9 = compute_hma(df["close"], 9)
    hma21 = compute_hma(df["close"], 21)
    window = _in_window(df)

    cross_up = (hma9 > hma21) & (hma9.shift(1) <= hma21.shift(1))
    cross_dn = (hma9 < hma21) & (hma9.shift(1) >= hma21.shift(1))

    long_sig = cross_up & (df["close"] > df["ema50"])
    short_sig = cross_dn & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s14_kama_cross(df: pd.DataFrame) -> pd.DataFrame:
    """S14: KAMA Cross.

    Entry: Close crosses above KAMA(10) AND EMA9 > EMA21 (trend confirmation).
    Short: Close crosses below KAMA(10) AND EMA9 < EMA21.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    kama = compute_kama(df["close"], period=10)
    window = _in_window(df)

    cross_up = (df["close"] > kama) & (df["close"].shift(1) <= kama.shift(1))
    cross_dn = (df["close"] < kama) & (df["close"].shift(1) >= kama.shift(1))

    long_sig = cross_up & (df["ema9"] > df["ema21"])
    short_sig = cross_dn & (df["ema9"] < df["ema21"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s15_price_channel_vol(df: pd.DataFrame) -> pd.DataFrame:
    """S15: Price Channel + Volume.

    Entry: Close exceeds previous 20-bar high AND volume > 2× vol_ma20.
    Short: Close falls below previous 20-bar low AND volume > 2× vol_ma20.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    # shift(1): closed-bar channel (no look-ahead)
    high20 = df["high"].rolling(20).max().shift(1)
    low20 = df["low"].rolling(20).min().shift(1)
    window = _in_window(df)

    vol_surge = df["vol_ratio"] >= 2.0

    break_high = (df["close"] > high20) & (df["close"].shift(1) <= high20.shift(1))
    break_low = (df["close"] < low20) & (df["close"].shift(1) >= low20.shift(1))

    long_sig = break_high & vol_surge
    short_sig = break_low & vol_surge

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s16_stoch_supertrend(df: pd.DataFrame) -> pd.DataFrame:
    """S16: Stochastic + Supertrend.

    Entry: Stoch K crosses above D in oversold zone (K < 0.20) AND
           Supertrend(14, 2.0) direction == 1 (bullish).
    Short: Stoch K crosses below D in overbought zone (K > 0.80) AND
           Supertrend direction == -1 (bearish).

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    k, d = _compute_stochrsi(df["close"])
    _st, st_dir = _compute_supertrend_fast(df["high"], df["low"], df["close"], 14, 2.0)
    window = _in_window(df)

    k_cross_up = (k > d) & (k.shift(1) <= d.shift(1))
    k_cross_dn = (k < d) & (k.shift(1) >= d.shift(1))

    long_sig = k_cross_up & (k < 0.20) & (st_dir == 1)
    short_sig = k_cross_dn & (k > 0.80) & (st_dir == -1)

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s17_dema_cross(df: pd.DataFrame) -> pd.DataFrame:
    """S17: DEMA Cross.

    Entry: DEMA(9) crosses above DEMA(21) AND close > EMA50.
    Short: DEMA(9) crosses below DEMA(21) AND close < EMA50.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    dema9 = compute_dema(df["close"], 9)
    dema21 = compute_dema(df["close"], 21)
    window = _in_window(df)

    cross_up = (dema9 > dema21) & (dema9.shift(1) <= dema21.shift(1))
    cross_dn = (dema9 < dema21) & (dema9.shift(1) >= dema21.shift(1))

    long_sig = cross_up & (df["close"] > df["ema50"])
    short_sig = cross_dn & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s18_ema_alligator(df: pd.DataFrame) -> pd.DataFrame:
    """S18: EMA Cross + Alligator Alignment.

    Entry: EMA9 crosses above EMA21 AND Alligator lips > teeth > jaw (bullish alignment).
    Short: EMA9 crosses below EMA21 AND lips < teeth < jaw (bearish alignment).

    Alligator: jaw=SMMA(13).shift(8), teeth=SMMA(8).shift(5), lips=SMMA(5).shift(3)

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    jaw = _compute_smma(df["close"], 13).shift(8)
    teeth = _compute_smma(df["close"], 8).shift(5)
    lips = _compute_smma(df["close"], 5).shift(3)
    window = _in_window(df)

    ema_cross_up = (df["ema9"] > df["ema21"]) & (df["ema9"].shift(1) <= df["ema21"].shift(1))
    ema_cross_dn = (df["ema9"] < df["ema21"]) & (df["ema9"].shift(1) >= df["ema21"].shift(1))

    alligator_bull = (lips > teeth) & (teeth > jaw)
    alligator_bear = (lips < teeth) & (teeth < jaw)

    long_sig = ema_cross_up & alligator_bull
    short_sig = ema_cross_dn & alligator_bear

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s19_rsi_ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    """S19: RSI + Ichimoku Cloud.

    Entry: RSI crosses above 50 AND close > cloud_top (above cloud).
    Short: RSI crosses below 50 AND close < cloud_bottom (below cloud).

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    _tenkan, _kijun, cloud_top, cloud_bottom = _compute_ichimoku_cloud(df)
    rsi = df["rsi"]
    window = _in_window(df)

    rsi_cross_up = (rsi >= 50) & (rsi.shift(1) < 50)
    rsi_cross_dn = (rsi <= 50) & (rsi.shift(1) > 50)

    long_sig = rsi_cross_up & (df["close"] > cloud_top)
    short_sig = rsi_cross_dn & (df["close"] < cloud_bottom)

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def strategy_s20_supertrend_volume(df: pd.DataFrame) -> pd.DataFrame:
    """S20: Supertrend + Volume.

    Entry: Supertrend(14, 2.0) direction flips to bullish AND volume > 2× vol_ma20.
    Short: Direction flips to bearish AND volume > 2× vol_ma20.

    Args:
        df: Base-indicator-enriched OHLCV DataFrame.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    _st, st_dir = _compute_supertrend_fast(df["high"], df["low"], df["close"], 14, 2.0)
    window = _in_window(df)

    flip_bull = (st_dir == 1) & (st_dir.shift(1) == -1)
    flip_bear = (st_dir == -1) & (st_dir.shift(1) == 1)

    vol_surge = df["vol_ratio"] >= 2.0

    long_sig = flip_bull & vol_surge
    short_sig = flip_bear & vol_surge

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window] = 1
    signal[short_sig & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES = [
    {"id": "S1",  "name": "OBV Breakout",         "fn": strategy_s1_obv_breakout},
    {"id": "S2",  "name": "CMF Cross",             "fn": strategy_s2_cmf_cross},
    {"id": "S3",  "name": "WilliamsR+ADX",         "fn": strategy_s3_williamsr_adx},
    {"id": "S4",  "name": "LinReg Breakout",       "fn": strategy_s4_linreg_breakout},
    {"id": "S5",  "name": "HeikinAshi Trend",      "fn": strategy_s5_heikinashi_trend},
    {"id": "S6",  "name": "ADX+DI Cross",          "fn": strategy_s6_adx_di_cross},
    {"id": "S7",  "name": "Keltner+ADX",           "fn": strategy_s7_keltner_adx},
    {"id": "S8",  "name": "TRIX Cross",            "fn": strategy_s8_trix_cross},
    {"id": "S9",  "name": "Vortex Cross",          "fn": strategy_s9_vortex_cross},
    {"id": "S10", "name": "Donchian+ADX",          "fn": strategy_s10_donchian_adx},
    {"id": "S11", "name": "Choppiness+EMA",        "fn": strategy_s11_choppiness_ema},
    {"id": "S12", "name": "ROC Momentum",          "fn": strategy_s12_roc_momentum},
    {"id": "S13", "name": "HullMA Cross",          "fn": strategy_s13_hullma_cross},
    {"id": "S14", "name": "KAMA Cross",            "fn": strategy_s14_kama_cross},
    {"id": "S15", "name": "PriceChannel+Vol",      "fn": strategy_s15_price_channel_vol},
    {"id": "S16", "name": "Stoch+Supertrend",      "fn": strategy_s16_stoch_supertrend},
    {"id": "S17", "name": "DEMA Cross",            "fn": strategy_s17_dema_cross},
    {"id": "S18", "name": "EMA+Alligator",         "fn": strategy_s18_ema_alligator},
    {"id": "S19", "name": "RSI+Ichimoku",          "fn": strategy_s19_rsi_ichimoku},
    {"id": "S20", "name": "Supertrend+Volume",     "fn": strategy_s20_supertrend_volume},
]


# ---------------------------------------------------------------------------
# Event-driven simulator (matches sweep_new_strategies_1.py and sweep_vol_expansion.py)
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

            hit_sl = (side == 1 and row["low"] <= sl) or (side == -1 and row["high"] >= sl)
            hit_tp = (
                (side == 1 and row["high"] >= tp) or (side == -1 and row["low"] <= tp)
            ) if tp > 0 else False

            if hit_sl or hit_tp:
                exit_p = sl if hit_sl else tp
                pnl_pct = side * (exit_p - entry) / entry - (COMMISSION + SLIPPAGE) * 2
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
            if sig_row.get("dow", 0) >= 5:  # no weekend entries
                continue
            atr = sig_row["atr"]
            if pd.isna(atr) or atr <= 0:
                continue

            entry_p = row["close"]
            side = int(sig_row["signal"])
            sl_d = atr * sl_mult
            tp_d = atr * tp_mult if tp_mult > 0 else 0.0

            sl_p = entry_p - sl_d if side == 1 else entry_p + sl_d
            tp_p = (entry_p + tp_d if side == 1 else entry_p - tp_d) if tp_d > 0 else 0.0

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
        return {"trades": 0, "tr_yr": 0.0, "wr": 0.0, "pf": 0.0, "dd": 0.0, "sharpe": 0.0}

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
        sharpe = (np.mean(pnl_arr) * tr_yr) / (np.std(pnl_arr, ddof=1) * np.sqrt(tr_yr))
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

    Prints progress every 10 coins. Prints table of PF >= min_pf results.

    Args:
        coins: List of coin prefixes (e.g. ['ethusdt', 'bnbusdt']).
        min_pf: Minimum profit factor to include in output table.
        min_trades: Minimum trades required to include a result.

    Returns:
        List of all result dicts (with pf >= 0).
    """
    total_coins = len(coins)
    print(f"\nLoading 1H data for {total_coins} coins...")

    loaded: Dict[str, pd.DataFrame] = {}
    for prefix in coins:
        name = prefix.replace("usdt", "").upper()
        fpath = os.path.join(DATA_DIR, f"{prefix}_1h_2y.csv")
        if not os.path.exists(fpath):
            continue
        try:
            raw = _load_ohlcv(fpath)
            if len(raw) < 300:
                continue
            loaded[name] = compute_base(raw)
        except Exception as exc:
            print(f"  WARN: could not load {fpath}: {exc}")

    print(f"Loaded {len(loaded)} coins with 1H data.")
    print(f"Testing {len(STRATEGIES)} strategies × 2 timeframes (1H + 4H) = "
          f"{len(STRATEGIES) * 2} combos per coin × {len(loaded)} coins = "
          f"{len(STRATEGIES) * 2 * len(loaded)} total runs\n")

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
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "volume": "sum"})
                .dropna(subset=["close"])
            )
            df_4h = compute_base(df_4h_raw) if len(df_4h_raw) >= 60 else None
        except Exception as exc:
            print(f"  WARN: 4H resample failed for {name}: {exc}")
            df_4h = None

        for spec in STRATEGIES:
            strat_id = spec["id"]
            strat_name = spec["name"]
            sl_m, tp_m, trail_m = STRATEGY_PARAMS.get(
                f"{strat_id}_{strat_name.replace(' ', '_').replace('+', '_')}",
                (2.0, 4.0, 0.0),
            )

            for tf_label, df_in in [("1H", df_1h), ("4H", df_4h)]:
                if df_in is None:
                    continue
                full_name = f"{strat_id} {strat_name} {tf_label}"
                try:
                    df_sig = spec["fn"](df_in)
                    r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m, trail_mult=trail_m)
                except Exception as exc:
                    # Silently skip — keep sweep moving
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
            print(f"Progress: {coins_done}/{total_coins} coins done "
                  f"({len(all_results)} results so far)")

    return all_results


# ---------------------------------------------------------------------------
# Output and summary
# ---------------------------------------------------------------------------

def _print_results_table(results: List[Dict], min_pf: float, min_trades: int) -> None:
    """Print the main results table sorted by PF descending.

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
    print(f"\nRESULTS: 20 new strategies across all coins (2yr, 1H + 4H resample)")
    print(hdr)
    print(sep)
    for r in winners:
        print(
            f"{r['coin']:<12} {r['strategy']:<22} {r['timeframe']:<4} "
            f"{r['pf']:>6.2f} {r['wr_pct']:>5.1f}% {r['trades']:>7} {r['sharpe']:>8.2f}"
        )
    print(sep)
    print(f"Total shown (PF >= {min_pf}, trades >= {min_trades}): "
          f"{len(winners)} / {len(results)}\n")


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
    print(f"  {'Strategy':<32} {'AvgPF':>7} {'>=1.0':>6} {'>=1.3':>6} {'>=2.0':>6} {'N':>6}")
    print("  " + "-" * 68)
    for row in rows:
        print(
            f"  {row[0]:<32} {row[1]:>7.2f} {row[2]:>6} {row[3]:>6} {row[4]:>6} {row[5]:>6}"
        )


def _print_top30(results: List[Dict]) -> None:
    """Print top 30 coin×strategy combinations with PF >= 2.0.

    Args:
        results: All result dicts.
    """
    top = [r for r in results if r["pf"] >= 2.0]
    top.sort(key=lambda x: -x["pf"])
    top30 = top[:30]

    print("\n" + "=" * 90)
    print("TOP 30 COIN × STRATEGY COMBINATIONS (PF >= 2.0)")
    print("=" * 90)
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

    # Best result per coin (highest PF)
    best_per_coin: Dict[str, Dict] = {}
    for r in new_coin_results:
        coin = r["coin"]
        if coin not in best_per_coin or r["pf"] > best_per_coin[coin]["pf"]:
            best_per_coin[coin] = r

    rows = sorted(best_per_coin.values(), key=lambda x: -x["pf"])

    print("\n" + "=" * 90)
    print("NEW COINS NOT IN PORTFOLIO WITH PF >= 1.5 (best strategy per coin)")
    print("=" * 90)
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
            f"{r['pf']:>6.2f} {r['wr_pct']:>5.1f}% {r['trades']:>7} {r['sharpe']:>8.2f}"
        )

    print(f"\n  Total new candidate coins: {len(rows)}")


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
        description="sweep_round9: 20 new strategies × 133 coins × 1H + 4H",
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

    print(f"sweep_round9: 20 new strategies, 1H + 4H, {len(coins)} coins")
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

    print("\nDone.")
