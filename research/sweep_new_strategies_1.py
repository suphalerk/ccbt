"""sweep_new_strategies_1.py — Lightweight sweep of 10 new strategies across all coins with 1H 2yr data.

Tests each strategy on 1H data AND 4H resampled data.
Saves results to data/sweep_new_strats_1.json.

Usage:
    python research/sweep_new_strategies_1.py
    python research/sweep_new_strategies_1.py --coins btcusdt,ethusdt
    python research/sweep_new_strategies_1.py --min-pf 1.2 --min-trades 4
"""

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

# Suppress noisy warnings
warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, "/Users/iceai/Work/ccbt")
# Import only what we need — avoid triggering ccxt initialisation inside data_loader
from bot.data import compute_atr, compute_ema, compute_rsi


def load_ohlcv(path: str) -> pd.DataFrame:
    """Load OHLCV CSV (avoids ccxt import in data_loader)."""
    return pd.read_csv(path, index_col="timestamp", parse_dates=True)


def _supertrend_fast(high: pd.Series, low: pd.Series, close: pd.Series,
                     period: int = 14, multiplier: float = 2.0) -> Tuple[pd.Series, pd.Series]:
    """Fast numpy Supertrend avoiding pandas iloc loop overhead."""
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
        # Lower band ratchets up
        if lower_band[i] > final_lower[i - 1] or c[i - 1] < final_lower[i - 1]:
            final_lower[i] = lower_band[i]
        else:
            final_lower[i] = final_lower[i - 1]
        # Upper band ratchets down
        if upper_band[i] < final_upper[i - 1] or c[i - 1] > final_upper[i - 1]:
            final_upper[i] = upper_band[i]
        else:
            final_upper[i] = final_upper[i - 1]
        # Direction
        if direction[i - 1] == 1:
            direction[i] = -1 if c[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if c[i] > final_upper[i] else -1

    st_vals = np.where(direction == 1, final_lower, final_upper)
    return (pd.Series(st_vals, index=close.index),
            pd.Series(direction.astype(int), index=close.index))

DATA_DIR = "/Users/iceai/Work/ccbt/data"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
COMMISSION = 0.00055  # 0.055% taker
SLIPPAGE = 0.00015    # 0.015%
LEVERAGE = 25
RISK_PCT = 0.01       # 1% risk per trade
HOURS_START = 3
HOURS_END = 20
MIN_TRADES = 4
WARMUP_BARS = 100     # blank warmup period for all indicators


# ---------------------------------------------------------------------------
# Base indicators (computed once, shared across strategies)
# ---------------------------------------------------------------------------

def compute_base(df: pd.DataFrame) -> pd.DataFrame:
    """Compute base indicators needed by multiple strategies."""
    df = df.copy()
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["rsi"] = compute_rsi(df["close"], 14)
    df["ema50"] = compute_ema(df["close"], 50)
    df["ema13"] = compute_ema(df["close"], 13)

    # Volume MA
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma20"].clip(lower=1e-10)

    # ATR rolling mean for regime filter
    df["atr_ma50"] = df["atr"].rolling(50).mean()

    # Trading hours + weekday (UTC)
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek
    else:
        df["hour"] = 12  # fallback: always in hours
        df["dow"] = 0

    return df


def in_trading_window(df: pd.DataFrame) -> pd.Series:
    return (df["hour"] >= HOURS_START) & (df["hour"] < HOURS_END) & (df["dow"] < 5)


def regime_ok(df: pd.DataFrame) -> pd.Series:
    """Vectorised ranging filter: skip candles where ATR is well below MA (consolidation)."""
    vol_ma50 = df["vol_ratio"].rolling(50).mean()
    return ~((df["atr"] < df["atr_ma50"] * 0.8) & (df["vol_ratio"] < vol_ma50 * 0.8))


# ---------------------------------------------------------------------------
# Strategy 1: ADX Trend Initiation
# ---------------------------------------------------------------------------

def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Compute ADX, +DI, -DI and attach to df copy."""
    df = df.copy()
    high, low, close = df["high"], df["low"], df["close"]

    # True range (already have ATR but need raw TR for DI computation)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)

    # Directional movements
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    # Smoothed with Wilder's method (EWM with alpha=1/period)
    atr_smooth = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_dm_smooth = plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    plus_di = 100 * plus_dm_smooth / atr_smooth.clip(lower=1e-10)
    minus_di = 100 * minus_dm_smooth / atr_smooth.clip(lower=1e-10)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).clip(lower=1e-10)
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    df["adx"] = adx
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    return df


def strategy_adx_trend(df: pd.DataFrame) -> pd.DataFrame:
    """ADX crosses above 25 (was below 25), DI+/DI- sets direction.

    Original spec called for ADX crossing from below 20 to above 25 — that never
    fires on 1H data (ADX moves gradually). Using 25 crossover instead, which
    captures the same 'trend initiation' concept without the 5-point single-bar gap.
    """
    df = compute_adx(df)
    window = in_trading_window(df)
    reg = regime_ok(df)

    adx_cross_up = (df["adx"] >= 25) & (df["adx"].shift(1) < 25)
    long_sig = adx_cross_up & (df["plus_di"] > df["minus_di"])
    short_sig = adx_cross_up & (df["minus_di"] > df["plus_di"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy 2: Stochastic RSI Crossover
# ---------------------------------------------------------------------------

def compute_stochrsi(close: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
                     k_period: int = 3, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Compute StochRSI K and D lines."""
    rsi = compute_rsi(close, rsi_period)
    rsi_min = rsi.rolling(stoch_period).min()
    rsi_max = rsi.rolling(stoch_period).max()
    stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min).clip(lower=1e-10)
    k = stoch_rsi.rolling(k_period).mean()
    d = k.rolling(d_period).mean()
    return k, d


def strategy_stochrsi(df: pd.DataFrame) -> pd.DataFrame:
    """K crosses D in oversold/overbought zone with EMA50 trend filter."""
    k, d = compute_stochrsi(df["close"])
    window = in_trading_window(df)
    reg = regime_ok(df)

    k_cross_up = (k > d) & (k.shift(1) <= d.shift(1))
    k_cross_dn = (k < d) & (k.shift(1) >= d.shift(1))

    long_sig = k_cross_up & (k < 20) & (df["close"] > df["ema50"])
    short_sig = k_cross_dn & (k > 80) & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy 3: CCI Momentum Breakout
# ---------------------------------------------------------------------------

def compute_cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """CCI(period)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma_tp = tp.rolling(period).mean()
    mean_dev = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    cci = (tp - sma_tp) / (0.015 * mean_dev.clip(lower=1e-10))
    return cci


def strategy_cci_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """CCI crosses +100/-100 with EMA50 filter."""
    cci = compute_cci(df)
    window = in_trading_window(df)
    reg = regime_ok(df)

    cross_above_100 = (cci >= 100) & (cci.shift(1) < 100)
    cross_below_neg100 = (cci <= -100) & (cci.shift(1) > -100)

    long_sig = cross_above_100 & (df["close"] > df["ema50"])
    short_sig = cross_below_neg100 & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy 4: Williams Alligator
# ---------------------------------------------------------------------------

def compute_smma(series: pd.Series, period: int) -> pd.Series:
    """Smoothed Moving Average (Wilder's): SMA for first value, then (prev*(n-1)+cur)/n.

    Uses numpy arrays to avoid pandas iloc overhead in the loop.
    """
    vals = series.values.astype(float)
    out = np.full(len(vals), np.nan)
    # First SMA seed
    if len(vals) < period:
        return pd.Series(out, index=series.index)
    out[period - 1] = np.nanmean(vals[:period])
    alpha = (period - 1) / period
    for i in range(period, len(vals)):
        out[i] = alpha * out[i - 1] + (1.0 - alpha) * vals[i]
    return pd.Series(out, index=series.index)


def strategy_alligator(df: pd.DataFrame) -> pd.DataFrame:
    """Williams Alligator: Lips/Teeth/Jaw crossover alignment."""
    jaw_raw = compute_smma(df["close"], 13).shift(8)
    teeth_raw = compute_smma(df["close"], 8).shift(5)
    lips_raw = compute_smma(df["close"], 5).shift(3)

    window = in_trading_window(df)
    reg = regime_ok(df)

    lips_cross_up_teeth = (lips_raw > teeth_raw) & (lips_raw.shift(1) <= teeth_raw.shift(1))
    lips_cross_dn_teeth = (lips_raw < teeth_raw) & (lips_raw.shift(1) >= teeth_raw.shift(1))

    long_sig = lips_cross_up_teeth & (teeth_raw > jaw_raw) & (df["close"] > lips_raw)
    short_sig = lips_cross_dn_teeth & (teeth_raw < jaw_raw) & (df["close"] < lips_raw)

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy 5: Parabolic SAR Flip
# ---------------------------------------------------------------------------

def compute_psar(high: pd.Series, low: pd.Series, close: pd.Series,
                 af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.20
                 ) -> Tuple[pd.Series, pd.Series]:
    """Compute Parabolic SAR using numpy arrays for speed.

    Returns (sar, direction) where direction: 1=long, -1=short.
    """
    h = high.values
    l = low.values
    c = close.values
    n = len(c)
    sar_arr = np.empty(n)
    dir_arr = np.ones(n, dtype=np.int8)

    af = af_start
    ep = h[0]
    sar_arr[0] = l[0]

    for i in range(1, n):
        prev_sar = sar_arr[i - 1]
        prev_dir = int(dir_arr[i - 1])

        if prev_dir == 1:  # bullish
            new_sar = prev_sar + af * (ep - prev_sar)
            low_cap = l[i - 1] if i < 2 else min(l[i - 1], l[i - 2])
            new_sar = min(new_sar, low_cap)
            if c[i] < new_sar:
                dir_arr[i] = -1
                new_sar = ep
                ep = l[i]
                af = af_start
            else:
                dir_arr[i] = 1
                if h[i] > ep:
                    ep = h[i]
                    af = min(af + af_step, af_max)
        else:  # bearish
            new_sar = prev_sar + af * (ep - prev_sar)
            high_cap = h[i - 1] if i < 2 else max(h[i - 1], h[i - 2])
            new_sar = max(new_sar, high_cap)
            if c[i] > new_sar:
                dir_arr[i] = 1
                new_sar = ep
                ep = h[i]
                af = af_start
            else:
                dir_arr[i] = -1
                if l[i] < ep:
                    ep = l[i]
                    af = min(af + af_step, af_max)

        sar_arr[i] = new_sar

    return (pd.Series(sar_arr, index=close.index),
            pd.Series(dir_arr.astype(int), index=close.index))


def strategy_psar_flip(df: pd.DataFrame) -> pd.DataFrame:
    """Parabolic SAR direction flip with EMA50 trend filter."""
    sar, psar_dir = compute_psar(df["high"], df["low"], df["close"])
    window = in_trading_window(df)
    reg = regime_ok(df)

    flip_to_long = (psar_dir == 1) & (psar_dir.shift(1) == -1)
    flip_to_short = (psar_dir == -1) & (psar_dir.shift(1) == 1)

    long_sig = flip_to_long & (df["close"] > df["ema50"])
    short_sig = flip_to_short & (df["close"] < df["ema50"])

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy 6: Elder Impulse System
# ---------------------------------------------------------------------------

def compute_macd_hist(close: pd.Series, fast: int = 12, slow: int = 26,
                      signal_p: int = 9) -> pd.Series:
    ema_fast = compute_ema(close, fast)
    ema_slow = compute_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal_p)
    return macd_line - signal_line


def strategy_elder_impulse(df: pd.DataFrame) -> pd.DataFrame:
    """EMA13 rising + MACD_hist rising (both current > previous), transition-only."""
    macd_hist = compute_macd_hist(df["close"])
    ema13_rising = df["ema13"] > df["ema13"].shift(1)
    ema13_falling = df["ema13"] < df["ema13"].shift(1)
    macd_rising = macd_hist > macd_hist.shift(1)
    macd_falling = macd_hist < macd_hist.shift(1)

    aligned_bull = ema13_rising & macd_rising & (df["close"] > df["ema50"])
    aligned_bear = ema13_falling & macd_falling & (df["close"] < df["ema50"])

    # Edge detection: transition from NOT aligned to aligned
    long_sig = aligned_bull & ~aligned_bull.shift(1).fillna(False)
    short_sig = aligned_bear & ~aligned_bear.shift(1).fillna(False)

    window = in_trading_window(df)
    reg = regime_ok(df)

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy 7: Aroon Crossover
# ---------------------------------------------------------------------------

def compute_aroon(df: pd.DataFrame, period: int = 25) -> Tuple[pd.Series, pd.Series]:
    """Aroon Up and Down."""
    # periods_since_highest_high = index_of_max in rolling window (counted from right)
    aroon_up = df["high"].rolling(period + 1).apply(
        lambda x: (period - np.argmax(x[::-1])) / period * 100, raw=True
    )
    aroon_dn = df["low"].rolling(period + 1).apply(
        lambda x: (period - np.argmin(x[::-1])) / period * 100, raw=True
    )
    return aroon_up, aroon_dn


def strategy_aroon_cross(df: pd.DataFrame) -> pd.DataFrame:
    """Aroon_Up crosses above Aroon_Down and Aroon_Up > 70."""
    aroon_up, aroon_dn = compute_aroon(df)
    window = in_trading_window(df)
    reg = regime_ok(df)

    up_cross_dn = (aroon_up > aroon_dn) & (aroon_up.shift(1) <= aroon_dn.shift(1))
    dn_cross_up = (aroon_dn > aroon_up) & (aroon_dn.shift(1) <= aroon_up.shift(1))

    long_sig = up_cross_dn & (aroon_up > 70)
    short_sig = dn_cross_up & (aroon_dn > 70)

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy 8: Dual Supertrend
# ---------------------------------------------------------------------------

def strategy_dual_supertrend(df: pd.DataFrame) -> pd.DataFrame:
    """Fast Supertrend (7, 2.0) flips while Slow Supertrend (14, 3.0) already aligned."""
    _st_fast, dir_fast = _supertrend_fast(df["high"], df["low"], df["close"], 7, 2.0)
    _st_slow, dir_slow = _supertrend_fast(df["high"], df["low"], df["close"], 14, 3.0)

    window = in_trading_window(df)
    reg = regime_ok(df)

    fast_flip_bull = (dir_fast == 1) & (dir_fast.shift(1) == -1)
    fast_flip_bear = (dir_fast == -1) & (dir_fast.shift(1) == 1)

    long_sig = fast_flip_bull & (dir_slow == 1)
    short_sig = fast_flip_bear & (dir_slow == -1)

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy 9: Ichimoku + Supertrend Confluence
# ---------------------------------------------------------------------------

def compute_ichimoku_cloud(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """Compute Ichimoku cloud_top and cloud_bottom inline (no config dependency)."""
    tenkan = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    kijun = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)
    return cloud_top, cloud_bottom


def strategy_ichi_supertrend(df: pd.DataFrame) -> pd.DataFrame:
    """Supertrend flip + price above/below Ichimoku cloud."""
    cloud_top, cloud_bottom = compute_ichimoku_cloud(df)
    _st, st_dir = _supertrend_fast(df["high"], df["low"], df["close"], 10, 2.0)

    window = in_trading_window(df)
    reg = regime_ok(df)

    st_flip_bull = (st_dir == 1) & (st_dir.shift(1) == -1)
    st_flip_bear = (st_dir == -1) & (st_dir.shift(1) == 1)

    long_sig = st_flip_bull & (df["close"] > cloud_top)
    short_sig = st_flip_bear & (df["close"] < cloud_bottom)

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Strategy 10: RSI Momentum 50-Cross
# ---------------------------------------------------------------------------

def strategy_rsi_50_cross(df: pd.DataFrame) -> pd.DataFrame:
    """RSI crosses above/below 50 with EMA50 trend + volume filter."""
    rsi = df["rsi"]
    window = in_trading_window(df)
    reg = regime_ok(df)

    rsi_cross_up = (rsi >= 50) & (rsi.shift(1) < 50)
    rsi_cross_dn = (rsi <= 50) & (rsi.shift(1) > 50)

    vol_ok = df["vol_ratio"] >= 1.3

    long_sig = rsi_cross_up & (df["close"] > df["ema50"]) & vol_ok
    short_sig = rsi_cross_dn & (df["close"] < df["ema50"]) & vol_ok

    signal = pd.Series(0, index=df.index)
    signal[long_sig & window & reg] = 1
    signal[short_sig & window & reg] = -1
    signal.iloc[:WARMUP_BARS] = 0

    df = df.copy()
    df["signal"] = signal
    return df


# ---------------------------------------------------------------------------
# Simulator (adapted from mass_sweep.py pattern)
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    sl_mult: float = 2.0,
    tp_mult: float = 4.0,
    trail_mult: float = 0.0,
) -> Dict:
    """Event-driven simulator. df must have 'signal' and 'atr' columns.

    Entry at NEXT bar's open (conservative: entry = next bar close in this
    lightweight sim, matching mass_sweep.py convention where entry = row["close"]).
    Uses iloc[-2] (closed candle) via the signal column: signal set at row i-1,
    entry executed at row i.
    """
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades = []
    position = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]

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
            hit_tp = ((side == 1 and row["high"] >= tp) or (side == -1 and row["low"] <= tp)) if tp > 0 else False

            if hit_sl or hit_tp:
                exit_p = sl if hit_sl else tp
                pnl_pct = side * (exit_p - entry) / entry - (COMMISSION + SLIPPAGE) * 2
                pnl = balance * RISK_PCT * LEVERAGE * pnl_pct / sl_mult
                pnl = max(pnl, -balance * RISK_PCT * LEVERAGE)
                balance += pnl
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                trades.append({"pnl": pnl, "reason": "sl" if hit_sl else "tp"})
                position = None
                if balance <= 0:
                    break

        if position is None and sig_row.get("signal", 0) != 0:
            # Weekend gate (already covered in signal generation, but double-check)
            if sig_row.get("dow", 0) >= 5:
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

    total = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = sum(abs(t["pnl"]) for t in trades if t["pnl"] < 0)
    pf = gp / gl if gl > 0 else 0.0
    wr = wins / total * 100 if total > 0 else 0.0
    days = (df.index[-1] - df.index[0]).days if len(df) > 1 else 365
    yr = max(days / 365.25, 0.01)

    # Sharpe: annualised mean / std of per-trade PnL
    pnls = [t["pnl"] for t in trades]
    if len(pnls) >= 4:
        pnl_arr = np.array(pnls)
        trades_per_year = total / yr
        sharpe = (np.mean(pnl_arr) * trades_per_year) / (np.std(pnl_arr, ddof=1) * np.sqrt(trades_per_year)) if np.std(pnl_arr, ddof=1) > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "trades": total,
        "tr_yr": total / yr,
        "wr": wr,
        "pf": pf,
        "dd": max_dd * 100,
        "balance": balance,
        "sharpe": sharpe,
    }


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES = [
    {
        "name": "ADX Trend Init",
        "fn": strategy_adx_trend,
        "sl": 2.0, "tp": 4.0, "trail": 3.0,
    },
    {
        "name": "StochRSI Cross",
        "fn": strategy_stochrsi,
        "sl": 1.5, "tp": 3.0, "trail": 0.0,
    },
    {
        "name": "CCI Breakout",
        "fn": strategy_cci_breakout,
        "sl": 2.0, "tp": 4.0, "trail": 0.0,
    },
    {
        "name": "Alligator",
        "fn": strategy_alligator,
        "sl": 2.0, "tp": 4.0, "trail": 0.0,
    },
    {
        "name": "PSAR Flip",
        "fn": strategy_psar_flip,
        "sl": 2.0, "tp": 3.0, "trail": 0.0,
    },
    {
        "name": "Elder Impulse",
        "fn": strategy_elder_impulse,
        "sl": 1.5, "tp": 3.0, "trail": 0.0,
    },
    {
        "name": "Aroon Cross",
        "fn": strategy_aroon_cross,
        "sl": 2.0, "tp": 4.0, "trail": 0.0,
    },
    {
        "name": "Dual Supertrend",
        "fn": strategy_dual_supertrend,
        "sl": 2.5, "tp": 4.0, "trail": 0.0,
    },
    {
        "name": "Ichi+Supertrend",
        "fn": strategy_ichi_supertrend,
        "sl": 2.0, "tp": 5.0, "trail": 0.0,
    },
    {
        "name": "RSI 50-Cross",
        "fn": strategy_rsi_50_cross,
        "sl": 1.5, "tp": 3.0, "trail": 0.0,
    },
]


# ---------------------------------------------------------------------------
# Coin discovery
# ---------------------------------------------------------------------------

def discover_coins(coins_arg: Optional[List[str]]) -> List[str]:
    """Return list of lowercase prefixes with 1h 2yr data."""
    if coins_arg:
        return [c.lower().strip() for c in coins_arg if c.strip()]

    pattern = os.path.join(DATA_DIR, "*_1h_2y.csv")
    files = glob.glob(pattern)
    prefixes = []
    for f in sorted(files):
        base = os.path.basename(f)
        prefix = base.replace("_1h_2y.csv", "")
        prefixes.append(prefix)
    return prefixes


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(coins: List[str], min_pf: float = 1.0, min_trades: int = MIN_TRADES) -> List[Dict]:
    total_coins = len(coins)
    print(f"\nLoading 1H data for {total_coins} coin(s)...")

    loaded: Dict[str, pd.DataFrame] = {}
    for prefix in coins:
        name = prefix.replace("usdt", "").upper()
        fpath = os.path.join(DATA_DIR, f"{prefix}_1h_2y.csv")
        if not os.path.exists(fpath):
            continue
        try:
            df_raw = load_ohlcv(fpath)
            if len(df_raw) < 200:
                continue
            loaded[name] = compute_base(df_raw)
        except Exception as e:
            print(f"  WARN: could not load {fpath}: {e}")

    print(f"Loaded {len(loaded)} coins with 1H data.\n")

    all_results: List[Dict] = []
    coins_done = 0

    for name, df_1h in sorted(loaded.items()):
        # Also build 4H version
        df_4h_raw = (
            df_1h[["open", "high", "low", "close", "volume"]]
            .resample("4h")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
        )
        df_4h = compute_base(df_4h_raw) if len(df_4h_raw) >= 60 else None

        for spec in STRATEGIES:
            strat_name = spec["name"]
            sl_m = spec["sl"]
            tp_m = spec["tp"]
            trail_m = spec["trail"]

            for tf_label, df_in in [("1H", df_1h), ("4H", df_4h)]:
                if df_in is None:
                    continue
                full_name = f"{strat_name} {tf_label}"
                try:
                    df_sig = spec["fn"](df_in)
                    r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m, trail_mult=trail_m)
                except Exception as e:
                    # Silently skip strategy errors to keep sweep fast
                    continue

                if r["trades"] < min_trades:
                    continue

                result = {
                    "coin": name,
                    "prefix": name.lower() + "usdt",
                    "strategy": full_name,
                    "timeframe": tf_label,
                    "pf": round(r["pf"], 3),
                    "wr_pct": round(r["wr"], 1),
                    "trades": r["trades"],
                    "tr_yr": round(r["tr_yr"], 1),
                    "sharpe": round(r["sharpe"], 2),
                    "dd_pct": round(r["dd"], 1),
                    "sl_mult": sl_m,
                    "tp_mult": tp_m,
                    "trail_mult": trail_m,
                }
                all_results.append(result)

        coins_done += 1
        if coins_done % 10 == 0:
            print(f"Progress: {coins_done}/{total_coins} coins done")

    print(f"Progress: {total_coins}/{total_coins} coins done\n")

    # Sort by PF descending
    all_results.sort(key=lambda x: -x["pf"])

    # Print full results table (PF > 1.0 only)
    winners = [r for r in all_results if r["pf"] >= min_pf]

    header = f"{'Coin':<12} {'Strategy':<24} {'PF':>6} {'WR%':>6} {'Trades':>7} {'Sharpe':>8}"
    sep = "-" * len(header)
    print(f"RESULTS: Strategies 1-10 across all coins (2yr, 1H + 4H resample)")
    print(header)
    print(sep)
    for r in winners:
        print(
            f"{r['coin']:<12} {r['strategy']:<24} {r['pf']:>6.2f} {r['wr_pct']:>5.1f}%"
            f" {r['trades']:>7} {r['sharpe']:>8.2f}"
        )

    print(sep)
    print(f"Total shown (PF >= {min_pf}): {len(winners)} / {len(all_results)} with >= {min_trades} trades\n")

    # Summary by strategy: count winners per strategy
    strat_pf: Dict[str, List[float]] = {}
    for r in all_results:
        strat_pf.setdefault(r["strategy"], []).append(r["pf"])

    print("Strategy summary (mean PF across all coins with >= 4 trades):")
    summary_rows = []
    for strat, pfs in sorted(strat_pf.items()):
        avg_pf = np.mean(pfs)
        n_winners = sum(1 for p in pfs if p >= 1.3)
        summary_rows.append((strat, avg_pf, n_winners, len(pfs)))
    summary_rows.sort(key=lambda x: -x[1])
    print(f"  {'Strategy':<24} {'AvgPF':>7} {'>=1.3':>6} {'Tested':>7}")
    for row in summary_rows:
        print(f"  {row[0]:<24} {row[1]:>7.2f} {row[2]:>6} {row[3]:>7}")

    # Save
    out_path = os.path.join(DATA_DIR, "sweep_new_strats_1.json")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nAll results saved to {out_path}")

    return all_results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sweep 10 new strategies (1H + 4H) across all coins with 1H 2yr data"
    )
    parser.add_argument("--coins", type=str, default=None,
                        help="Comma-separated coin prefixes, e.g. btcusdt,ethusdt")
    parser.add_argument("--min-pf", type=float, default=1.0,
                        help="Minimum PF to include in results table (default: 1.0)")
    parser.add_argument("--min-trades", type=int, default=MIN_TRADES,
                        help=f"Minimum trades to include (default: {MIN_TRADES})")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    coins_arg = args.coins.split(",") if args.coins else None
    coins = discover_coins(coins_arg)

    if not coins:
        print("No coins found. Add *_1h_2y.csv files to data/ or pass --coins.")
        sys.exit(1)

    print(f"Sweeping {len(coins)} coin(s): {', '.join(coins[:10])}{'...' if len(coins) > 10 else ''}")
    run_sweep(coins, min_pf=args.min_pf, min_trades=args.min_trades)
