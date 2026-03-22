"""sweep_github_inspired.py — 5 GitHub-inspired trading strategy sweep on 1H/4H data.

Strategies tested (from popular open-source trading bots):

  G1: Dual Thrust Breakout (je-suis-tm/quant-trading, 4.5K stars)
      Classic futures range breakout: price breaks above/below N-bar rolling range bound.

  G2: Kalman Filter Trend (QuantConnect pairs trading research)
      Adaptive moving average with less lag than EMA; fast/slow Kalman crossover.

  G3: Awesome Oscillator Zero-Cross (je-suis-tm/quant-trading)
      Bill Williams AO: SMA(5) - SMA(34) of midpoint; crosses zero line + EMA(50) filter.

  G4: Grid-Inspired Range Bounce (OctoBot, 4K stars)
      Detect ranging market (tight price channel); buy near support, sell near resistance.
      NOTE: Expected to fail — crypto mean-reversion historically fails (75+ prior tests).

  G5: Multi-Indicator Confluence (NostalgiaForInfinity-style, 2.9K stars)
      Require 4/5 indicators to align (EMA, RSI, AO, ADX, Volume) before entry.

Simulation rules (same as all prior sweeps):
  - Signal computed on iloc[-2] (closed candle); entry at next candle open (approx. close)
  - Commission 0.055% + Slippage 0.02% per side (round trip 0.15%)
  - Leverage 25x, Risk 1% per trade
  - Hours 3-20 UTC, weekdays only
  - Min 10 trades required to count as winner (audit-aligned)

Parameter grids:
  G1: k1/k2 in [0.3,0.5,0.7], lookback N in [10,20,30], SL [1.5,2.0], TP [3.0,4.0]
  G2: process_noise_fast [0.05,0.1,0.2], process_noise_slow [0.005,0.01,0.02], SL [1.5,2.0], TP [3.0,4.0]
  G3: SL [1.5,2.0], TP [3.0,4.0]  (no extra params — AO is fixed)
  G4: range_lookback [15,20,30], rsi_lo/hi [30/70,35/65,40/60], SL [1.0,1.5], TP [1.5,2.0]
  G5: min_score [3,4] out of 5, SL [1.5,2.0], TP [3.0,4.0]

Skips all currently deployed coins.
Tests 4H (resample 1H->4H) for coins with at least one 1H winner.

Usage:
    python research/sweep_github_inspired.py
    python research/sweep_github_inspired.py --coins ethusdt,linkusdt
    python research/sweep_github_inspired.py --strategies G1,G2,G5
    python research/sweep_github_inspired.py --min-pf 1.2 --min-trades 10
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from itertools import product
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.WARNING)
logging.getLogger("bot").setLevel(logging.WARNING)

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import compute_atr, compute_ema, compute_rsi, compute_volume_ma

DATA_DIR = "/Users/iceai/Work/ccbt/data"
OUTPUT_FILE = os.path.join(DATA_DIR, "sweep_github.json")

# Coins already deployed — skip in sweep
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
# Simulation constants
# ---------------------------------------------------------------------------
COMMISSION = 0.00055   # 0.055% taker
SLIPPAGE = 0.0002      # 0.02% per side
RISK_PCT = 0.01        # 1% risk per trade
LEVERAGE = 25.0
WARMUP = 100           # candles to skip at start (indicator warmup)


# ---------------------------------------------------------------------------
# Core simulator (shared across all strategies)
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    sl_mult: float,
    tp_mult: float,
) -> dict:
    """Event-driven position simulator.

    df must have columns: signal (1=long, -1=short, 0=none), atr.
    Signal is treated as iloc[-2] — generated on closed candle, entered on next bar's close.

    Args:
        df: OHLCV DataFrame with 'signal' and 'atr' columns.
        sl_mult: Stop-loss ATR multiplier.
        tp_mult: Take-profit ATR multiplier.

    Returns:
        Dict with trades, tr_yr, wr, pf, dd, balance, ret metrics.
    """
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades: list = []
    position = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]

        # Manage open position
        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]

            hit_sl = (side == 1 and row["low"] <= sl) or (side == -1 and row["high"] >= sl)
            hit_tp = (side == 1 and row["high"] >= tp) or (side == -1 and row["low"] <= tp)

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

        # Open new position if flat and signal present
        if position is None and sig_row.get("signal", 0) != 0:
            # Weekend guard (belt-and-suspenders — signals already filtered)
            dow = sig_row.get("dow", 0)
            if not pd.isna(dow) and int(dow) >= 5:
                continue
            atr = sig_row["atr"]
            if pd.isna(atr) or atr <= 0:
                continue
            side = int(sig_row["signal"])
            entry_p = row["close"]
            sl_d = atr * sl_mult
            tp_d = atr * tp_mult
            if side == 1:
                sl_p = entry_p - sl_d
                tp_p = entry_p + tp_d
            else:
                sl_p = entry_p + sl_d
                tp_p = entry_p - tp_d
            position = {"side": side, "entry": entry_p, "sl": sl_p, "tp": tp_p}

    total = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = sum(abs(t["pnl"]) for t in trades if t["pnl"] < 0)
    pf = gp / gl if gl > 0 else 0.0
    wr = wins / total * 100 if total > 0 else 0.0
    days = (df.index[-1] - df.index[0]).days
    yr = days / 365.25
    return {
        "trades": total,
        "tr_yr": round(total / yr, 1) if yr > 0 else 0.0,
        "wr": round(wr, 1),
        "pf": round(pf, 3),
        "dd": round(max_dd * 100, 1),
        "balance": round(balance, 2),
        "ret": round((balance - 1000.0) / 10.0, 1),
    }


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _add_time_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Add hour and dow columns if index is DatetimeIndex."""
    df = df.copy()
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek
    else:
        df["hour"] = 12
        df["dow"] = 0
    return df


def _apply_time_filter(
    signal: pd.Series,
    hour: pd.Series,
    dow: pd.Series,
    hours_start: int = 3,
    hours_end: int = 20,
) -> pd.Series:
    """Zero out signals outside trading hours / weekends.

    Args:
        signal: Raw signal series (1/-1/0).
        hour: UTC hour series.
        dow: Day-of-week series (0=Mon, 6=Sun).
        hours_start: First valid UTC hour (inclusive).
        hours_end: Last valid UTC hour (exclusive).

    Returns:
        Filtered signal series.
    """
    in_hours = (hour >= hours_start) & (hour < hours_end)
    not_we = dow < 5
    out = signal.copy()
    out[~(in_hours & not_we)] = 0
    return out


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Compute Average Directional Index (ADX).

    Uses Wilder's smoothing, same approach as standard ADX.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: ADX smoothing period.

    Returns:
        ADX series (0-100).
    """
    up_move = high.diff()
    down_move = -low.diff()
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr_raw = compute_atr(high, low, close, period)

    pos_di = (
        pd.Series(pos_dm, index=close.index).ewm(alpha=1 / period, adjust=False).mean()
        / atr_raw
        * 100
    )
    neg_di = (
        pd.Series(neg_dm, index=close.index).ewm(alpha=1 / period, adjust=False).mean()
        / atr_raw
        * 100
    )
    dx = (abs(pos_di - neg_di) / (pos_di + neg_di).replace(0, np.nan)) * 100
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx


def _compute_kalman(
    close: pd.Series,
    process_noise: float = 0.01,
    measurement_noise: float = 1.0,
) -> pd.Series:
    """1D Kalman filter as adaptive trend estimator (less lag than EMA).

    Args:
        close: Close price series.
        process_noise: Q parameter — how fast the true value can change.
        measurement_noise: R parameter — observation noise level.

    Returns:
        Kalman-filtered price series (same index as close).
    """
    n = len(close)
    vals = close.values
    x = float(vals[0])
    P = 1.0
    Q = process_noise
    R = measurement_noise
    result = np.zeros(n)
    for i in range(n):
        # Predict step
        P = P + Q
        # Update step
        K = P / (P + R)
        x = x + K * (vals[i] - x)
        P = (1.0 - K) * P
        result[i] = x
    return pd.Series(result, index=close.index)


# ---------------------------------------------------------------------------
# G1: Dual Thrust Breakout
# ---------------------------------------------------------------------------

def signals_g1_dual_thrust(
    df: pd.DataFrame,
    k1: float = 0.5,
    k2: float = 0.5,
    lookback: int = 20,
) -> pd.DataFrame:
    """Dual Thrust range breakout signal (je-suis-tm/quant-trading).

    Upper bound = prev_close + k1 * rolling_range(N)
    Lower bound = prev_close - k2 * rolling_range(N)
    Long:  close breaks above upper_bound AND close > EMA(50)
    Short: close breaks below lower_bound AND close < EMA(50)
    Edge detect: compare to previous candle to avoid re-entries.

    Args:
        df: OHLCV DataFrame with ema50, hour, dow, atr columns.
        k1: Upper thrust coefficient (0-1).
        k2: Lower thrust coefficient (0-1).
        lookback: Rolling window for range computation.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    rolling_range = df["high"].rolling(lookback).max() - df["low"].rolling(lookback).min()
    upper = df["close"].shift(1) + k1 * rolling_range.shift(1)
    lower = df["close"].shift(1) - k2 * rolling_range.shift(1)

    above_ema = df["close"] > df["ema50"]
    below_ema = df["close"] < df["ema50"]

    # Edge detect: was NOT above upper last bar, IS above now
    long_raw = (df["close"] > upper) & (df["close"].shift(1) <= upper.shift(1)) & above_ema
    short_raw = (df["close"] < lower) & (df["close"].shift(1) >= lower.shift(1)) & below_ema

    sig = pd.Series(0, index=df.index)
    sig[long_raw] = 1
    sig[short_raw] = -1
    sig = _apply_time_filter(sig, df["hour"], df["dow"])
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


# ---------------------------------------------------------------------------
# G2: Kalman Filter Trend Crossover
# ---------------------------------------------------------------------------

def signals_g2_kalman(
    df: pd.DataFrame,
    fast_noise: float = 0.1,
    slow_noise: float = 0.01,
) -> pd.DataFrame:
    """Kalman filter adaptive trend crossover (QuantConnect-style).

    Fast Kalman (high process noise) reacts quickly to price.
    Slow Kalman (low process noise) acts like a smoothed long-term trend.
    Long:  fast_kalman crosses above slow_kalman AND close > EMA(50)
    Short: fast_kalman crosses below slow_kalman AND close < EMA(50)

    Args:
        df: OHLCV DataFrame with ema50, hour, dow, atr columns.
        fast_noise: Process noise for fast Kalman (higher = more responsive).
        slow_noise: Process noise for slow Kalman (lower = smoother).

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    kf_fast = _compute_kalman(df["close"], process_noise=fast_noise)
    kf_slow = _compute_kalman(df["close"], process_noise=slow_noise)

    above_slow = (kf_fast > kf_slow)
    # shift(1) produces object dtype due to NaN; fill with False then cast to bool
    above_slow_prev = above_slow.shift(1).fillna(False).astype(bool)

    above_ema = df["close"] > df["ema50"]
    below_ema = df["close"] < df["ema50"]

    long_raw = above_slow & ~above_slow_prev & above_ema
    short_raw = ~above_slow & above_slow_prev & below_ema

    sig = pd.Series(0, index=df.index)
    sig[long_raw.fillna(False)] = 1
    sig[short_raw.fillna(False)] = -1
    sig = _apply_time_filter(sig, df["hour"], df["dow"])
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


# ---------------------------------------------------------------------------
# G3: Awesome Oscillator Zero-Cross
# ---------------------------------------------------------------------------

def signals_g3_awesome_oscillator(df: pd.DataFrame) -> pd.DataFrame:
    """Awesome Oscillator zero-line crossover (Bill Williams / je-suis-tm).

    Midpoint = (high + low) / 2
    AO = SMA(midpoint, 5) - SMA(midpoint, 34)
    Long:  AO crosses above 0 AND close > EMA(50)
    Short: AO crosses below 0 AND close < EMA(50)

    Args:
        df: OHLCV DataFrame with ema50, hour, dow, atr columns.

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    midpoint = (df["high"] + df["low"]) / 2.0
    ao = midpoint.rolling(5).mean() - midpoint.rolling(34).mean()
    ao_prev = ao.shift(1)

    above_zero = ao > 0
    was_below = ao_prev <= 0
    below_zero = ao < 0
    was_above = ao_prev >= 0

    above_ema = df["close"] > df["ema50"]
    below_ema = df["close"] < df["ema50"]

    long_raw = above_zero & was_below & above_ema
    short_raw = below_zero & was_above & below_ema

    sig = pd.Series(0, index=df.index)
    sig[long_raw.fillna(False)] = 1
    sig[short_raw.fillna(False)] = -1
    sig = _apply_time_filter(sig, df["hour"], df["dow"])
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


# ---------------------------------------------------------------------------
# G4: Grid-Inspired Range Bounce
# ---------------------------------------------------------------------------

def signals_g4_range_bounce(
    df: pd.DataFrame,
    range_lookback: int = 20,
    rsi_lo: float = 35,
    rsi_hi: float = 65,
) -> pd.DataFrame:
    """Range-market bounce trading inspired by OctoBot's grid logic.

    Detects ranging market using Choppiness-like channel width, then
    trades bounces from support/resistance levels with RSI confirmation.

    Long:  close near rolling support AND is_ranging AND RSI < rsi_lo
    Short: close near rolling resistance AND is_ranging AND RSI > rsi_hi

    NOTE: Mean-reversion historically fails on crypto. This test will
    likely confirm that finding but completes the GitHub-inspired suite.

    Args:
        df: OHLCV DataFrame with rsi14, hour, dow, atr columns.
        range_lookback: Rolling window for support/resistance.
        rsi_lo: RSI threshold for long entry (oversold).
        rsi_hi: RSI threshold for short entry (overbought).

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()
    support = df["low"].rolling(range_lookback).min()
    resistance = df["high"].rolling(range_lookback).max()
    channel_pct = (resistance - support) / df["close"]

    # "Ranging" = channel is tighter than 30th percentile of recent 50-bar window
    is_ranging = channel_pct < channel_pct.rolling(50).quantile(0.30)

    near_support = df["close"] < support * 1.005      # within 0.5% of support
    near_resistance = df["close"] > resistance * 0.995  # within 0.5% of resistance

    long_raw = near_support & is_ranging & (df["rsi14"] < rsi_lo)
    short_raw = near_resistance & is_ranging & (df["rsi14"] > rsi_hi)

    sig = pd.Series(0, index=df.index)
    sig[long_raw.fillna(False)] = 1
    sig[short_raw.fillna(False)] = -1
    sig = _apply_time_filter(sig, df["hour"], df["dow"])
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


# ---------------------------------------------------------------------------
# G5: Multi-Indicator Confluence (NostalgiaForInfinity-style)
# ---------------------------------------------------------------------------

def signals_g5_confluence(
    df: pd.DataFrame,
    min_score: int = 4,
) -> pd.DataFrame:
    """NostalgiaForInfinity-style multi-indicator confluence entry.

    Requires min_score out of 5 bull/bear indicators to align.
    Bull indicators (each scores 1 point):
      1. EMA: ema9 > ema21 AND close > ema50
      2. RSI: 45 < rsi < 65
      3. Awesome Oscillator: ao > 0
      4. ADX: adx > 20 (trending, not ranging)
      5. Volume: volume > vol_ma20

    Long:  bull_score >= min_score  AND  prev_score < min_score  (edge detect)
    Short: bear_score >= min_score  AND  prev_score < min_score

    Args:
        df: OHLCV DataFrame with ema9, ema21, ema50, rsi14, adx, ao,
            vol_ma20, hour, dow, atr columns.
        min_score: Number of indicators that must agree (3, 4, or 5).

    Returns:
        DataFrame with 'signal' column.
    """
    df = df.copy()

    # Bull conditions (each bool = 1 point)
    ema_bull = (df["ema9"] > df["ema21"]) & (df["close"] > df["ema50"])
    rsi_bull = (df["rsi14"] > 45) & (df["rsi14"] < 65)
    ao_bull = df["ao"] > 0
    adx_ok = df["adx"] > 20
    vol_ok = df["volume"] > df["vol_ma20"]

    # Bear conditions
    ema_bear = (df["ema9"] < df["ema21"]) & (df["close"] < df["ema50"])
    rsi_bear = (df["rsi14"] > 35) & (df["rsi14"] < 55)
    ao_bear = df["ao"] < 0

    bull_score = (
        ema_bull.astype(int)
        + rsi_bull.astype(int)
        + ao_bull.astype(int)
        + adx_ok.astype(int)
        + vol_ok.astype(int)
    )
    bear_score = (
        ema_bear.astype(int)
        + rsi_bear.astype(int)
        + ao_bear.astype(int)
        + adx_ok.astype(int)
        + vol_ok.astype(int)
    )

    bull_trigger = (bull_score >= min_score) & (bull_score.shift(1) < min_score)
    bear_trigger = (bear_score >= min_score) & (bear_score.shift(1) < min_score)

    sig = pd.Series(0, index=df.index)
    sig[bull_trigger.fillna(False)] = 1
    sig[bear_trigger.fillna(False)] = -1
    sig = _apply_time_filter(sig, df["hour"], df["dow"])
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


# ---------------------------------------------------------------------------
# Indicator precomputation (build everything once per coin)
# ---------------------------------------------------------------------------

def _build_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Precompute all indicators needed across the 5 strategies.

    Args:
        df: Raw OHLCV DataFrame.

    Returns:
        DataFrame with ema9, ema21, ema50, atr, rsi14, vol_ma20, adx, ao,
        hour, dow columns.
    """
    df = df.copy()
    df["ema9"] = compute_ema(df["close"], 9)
    df["ema21"] = compute_ema(df["close"], 21)
    df["ema50"] = compute_ema(df["close"], 50)
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["rsi14"] = compute_rsi(df["close"], 14)
    df["vol_ma20"] = compute_volume_ma(df["volume"], 20)
    df["adx"] = _compute_adx(df["high"], df["low"], df["close"], 14)
    midpoint = (df["high"] + df["low"]) / 2.0
    df["ao"] = midpoint.rolling(5).mean() - midpoint.rolling(34).mean()
    df = _add_time_cols(df)
    return df


# ---------------------------------------------------------------------------
# Data loading and resampling
# ---------------------------------------------------------------------------

def _load_coin(prefix: str) -> Optional[pd.DataFrame]:
    """Load and preprocess 1H OHLCV for a coin prefix.

    Args:
        prefix: Lowercase coin prefix, e.g. 'ethusdt'.

    Returns:
        Indicator-enriched DataFrame or None if unavailable.
    """
    for period in ["2y", "5y"]:
        fpath = os.path.join(DATA_DIR, f"{prefix}_1h_{period}.csv")
        if os.path.exists(fpath):
            try:
                raw = load_ohlcv(fpath)
                # Require >= 12 months of data
                days = (raw.index[-1] - raw.index[0]).days
                if days < 365:
                    return None
                return _build_all_indicators(raw)
            except Exception as e:
                print(f"  WARN: failed loading {fpath}: {e}")
    return None


def _resample_4h(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Resample 1H DataFrame to 4H and recompute indicators.

    Args:
        df: 1H OHLCV DataFrame (raw columns only, no computed indicators).

    Returns:
        4H indicator-enriched DataFrame or None on failure.
    """
    try:
        raw_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df_4h = df[raw_cols].resample("4h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna(subset=["close"])
        return _build_all_indicators(df_4h)
    except Exception as e:
        print(f"  WARN: 4H resample failed: {e}")
        return None


def discover_coins(coins_arg: Optional[List[str]]) -> List[str]:
    """Return list of non-deployed coin prefixes to sweep.

    Args:
        coins_arg: Explicit list from CLI --coins, or None for auto-discovery.

    Returns:
        Sorted list of coin prefixes.
    """
    if coins_arg:
        return [c.lower().strip() for c in coins_arg if c.strip()]
    pattern = os.path.join(DATA_DIR, "*_1h_2y.csv")
    files = glob.glob(pattern)
    prefixes = []
    for f in sorted(files):
        base = os.path.basename(f)
        prefix = base.replace("_1h_2y.csv", "")
        if prefix not in DEPLOYED_PREFIXES:
            prefixes.append(prefix)
    return prefixes


# ---------------------------------------------------------------------------
# Per-strategy sweep runners
# ---------------------------------------------------------------------------

def sweep_g1(
    name: str,
    df: pd.DataFrame,
    timeframe: str,
    prefix: str,
    min_pf: float,
    min_trades: int,
    max_dd: float,
) -> List[dict]:
    """Sweep G1: Dual Thrust Breakout parameter grid.

    Args:
        name: Display coin name.
        df: Indicator-enriched OHLCV DataFrame.
        timeframe: '1h' or '4h'.
        prefix: Raw coin prefix (e.g. 'ethusdt').
        min_pf: Minimum profit factor.
        min_trades: Minimum trade count.
        max_dd: Maximum drawdown % allowed.

    Returns:
        List of winner dicts.
    """
    k_values = [0.3, 0.5, 0.7]
    lookbacks = [10, 20, 30]
    sl_values = [1.5, 2.0]
    tp_values = [3.0, 4.0]
    winners = []

    for k, lb, sl_m, tp_m in product(k_values, lookbacks, sl_values, tp_values):
        try:
            df_sig = signals_g1_dual_thrust(df, k1=k, k2=k, lookback=lb)
            r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m)
        except Exception as e:
            print(f"  ERROR G1 {name}/{timeframe} k={k} lb={lb}: {e}")
            continue

        is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades and r["dd"] <= max_dd
        if r["trades"] >= 5:
            marker = " *" if is_winner else ""
            print(
                f"G1 {name:<8} {timeframe:<4} k={k:.1f} lb={lb:>2} sl={sl_m:.1f} tp={tp_m:.1f}"
                f"  trades={r['trades']:>4}  tr/yr={r['tr_yr']:>4.0f}"
                f"  wr={r['wr']:>5.1f}%  PF={r['pf']:>5.2f}  DD={r['dd']:>5.1f}%{marker}"
            )
        if is_winner:
            winners.append(_winner_dict("G1_DualThrust", name, prefix, timeframe, r, {
                "k1": k, "k2": k, "lookback": lb, "sl_mult": sl_m, "tp_mult": tp_m,
            }))
    return winners


def sweep_g2(
    name: str,
    df: pd.DataFrame,
    timeframe: str,
    prefix: str,
    min_pf: float,
    min_trades: int,
    max_dd: float,
) -> List[dict]:
    """Sweep G2: Kalman Filter Trend Crossover parameter grid.

    Args:
        name: Display coin name.
        df: Indicator-enriched OHLCV DataFrame.
        timeframe: '1h' or '4h'.
        prefix: Raw coin prefix.
        min_pf: Minimum profit factor.
        min_trades: Minimum trade count.
        max_dd: Maximum drawdown % allowed.

    Returns:
        List of winner dicts.
    """
    fast_noises = [0.05, 0.1, 0.2]
    slow_noises = [0.005, 0.01, 0.02]
    sl_values = [1.5, 2.0]
    tp_values = [3.0, 4.0]
    winners = []

    for fn, sn, sl_m, tp_m in product(fast_noises, slow_noises, sl_values, tp_values):
        if fn <= sn:
            continue  # fast must be noisier than slow
        try:
            df_sig = signals_g2_kalman(df, fast_noise=fn, slow_noise=sn)
            r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m)
        except Exception as e:
            print(f"  ERROR G2 {name}/{timeframe} fn={fn} sn={sn}: {e}")
            continue

        is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades and r["dd"] <= max_dd
        if r["trades"] >= 5:
            marker = " *" if is_winner else ""
            print(
                f"G2 {name:<8} {timeframe:<4} fn={fn:.3f} sn={sn:.3f} sl={sl_m:.1f} tp={tp_m:.1f}"
                f"  trades={r['trades']:>4}  tr/yr={r['tr_yr']:>4.0f}"
                f"  wr={r['wr']:>5.1f}%  PF={r['pf']:>5.2f}  DD={r['dd']:>5.1f}%{marker}"
            )
        if is_winner:
            winners.append(_winner_dict("G2_KalmanTrend", name, prefix, timeframe, r, {
                "fast_noise": fn, "slow_noise": sn, "sl_mult": sl_m, "tp_mult": tp_m,
            }))
    return winners


def sweep_g3(
    name: str,
    df: pd.DataFrame,
    timeframe: str,
    prefix: str,
    min_pf: float,
    min_trades: int,
    max_dd: float,
) -> List[dict]:
    """Sweep G3: Awesome Oscillator Zero-Cross (fixed params + SL/TP grid).

    Args:
        name: Display coin name.
        df: Indicator-enriched OHLCV DataFrame.
        timeframe: '1h' or '4h'.
        prefix: Raw coin prefix.
        min_pf: Minimum profit factor.
        min_trades: Minimum trade count.
        max_dd: Maximum drawdown % allowed.

    Returns:
        List of winner dicts.
    """
    sl_values = [1.5, 2.0]
    tp_values = [3.0, 4.0]
    winners = []

    try:
        df_sig = signals_g3_awesome_oscillator(df)
    except Exception as e:
        print(f"  ERROR G3 {name}/{timeframe} signal build: {e}")
        return winners

    for sl_m, tp_m in product(sl_values, tp_values):
        try:
            r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m)
        except Exception as e:
            print(f"  ERROR G3 {name}/{timeframe} sl={sl_m} tp={tp_m}: {e}")
            continue

        is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades and r["dd"] <= max_dd
        if r["trades"] >= 5:
            marker = " *" if is_winner else ""
            print(
                f"G3 {name:<8} {timeframe:<4} sl={sl_m:.1f} tp={tp_m:.1f}"
                f"  trades={r['trades']:>4}  tr/yr={r['tr_yr']:>4.0f}"
                f"  wr={r['wr']:>5.1f}%  PF={r['pf']:>5.2f}  DD={r['dd']:>5.1f}%{marker}"
            )
        if is_winner:
            winners.append(_winner_dict("G3_AwesomeOscillator", name, prefix, timeframe, r, {
                "sl_mult": sl_m, "tp_mult": tp_m,
            }))
    return winners


def sweep_g4(
    name: str,
    df: pd.DataFrame,
    timeframe: str,
    prefix: str,
    min_pf: float,
    min_trades: int,
    max_dd: float,
) -> List[dict]:
    """Sweep G4: Grid-Inspired Range Bounce (mean reversion, expected to fail).

    Args:
        name: Display coin name.
        df: Indicator-enriched OHLCV DataFrame.
        timeframe: '1h' or '4h'.
        prefix: Raw coin prefix.
        min_pf: Minimum profit factor.
        min_trades: Minimum trade count.
        max_dd: Maximum drawdown % allowed.

    Returns:
        List of winner dicts.
    """
    range_lookbacks = [15, 20, 30]
    rsi_bands = [(30, 70), (35, 65), (40, 60)]
    sl_values = [1.0, 1.5]
    tp_values = [1.5, 2.0]
    winners = []

    for rl, (rsi_lo, rsi_hi), sl_m, tp_m in product(range_lookbacks, rsi_bands, sl_values, tp_values):
        try:
            df_sig = signals_g4_range_bounce(df, range_lookback=rl, rsi_lo=rsi_lo, rsi_hi=rsi_hi)
            r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m)
        except Exception as e:
            print(f"  ERROR G4 {name}/{timeframe} rl={rl}: {e}")
            continue

        is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades and r["dd"] <= max_dd
        if r["trades"] >= 5:
            marker = " *" if is_winner else ""
            print(
                f"G4 {name:<8} {timeframe:<4} rl={rl:>2} rsi={rsi_lo}/{rsi_hi} sl={sl_m:.1f} tp={tp_m:.1f}"
                f"  trades={r['trades']:>4}  tr/yr={r['tr_yr']:>4.0f}"
                f"  wr={r['wr']:>5.1f}%  PF={r['pf']:>5.2f}  DD={r['dd']:>5.1f}%{marker}"
            )
        if is_winner:
            winners.append(_winner_dict("G4_RangeBounce", name, prefix, timeframe, r, {
                "range_lookback": rl, "rsi_lo": rsi_lo, "rsi_hi": rsi_hi,
                "sl_mult": sl_m, "tp_mult": tp_m,
            }))
    return winners


def sweep_g5(
    name: str,
    df: pd.DataFrame,
    timeframe: str,
    prefix: str,
    min_pf: float,
    min_trades: int,
    max_dd: float,
) -> List[dict]:
    """Sweep G5: Multi-Indicator Confluence (NostalgiaForInfinity-style).

    Args:
        name: Display coin name.
        df: Indicator-enriched OHLCV DataFrame.
        timeframe: '1h' or '4h'.
        prefix: Raw coin prefix.
        min_pf: Minimum profit factor.
        min_trades: Minimum trade count.
        max_dd: Maximum drawdown % allowed.

    Returns:
        List of winner dicts.
    """
    min_scores = [3, 4]
    sl_values = [1.5, 2.0]
    tp_values = [3.0, 4.0]
    winners = []

    for ms, sl_m, tp_m in product(min_scores, sl_values, tp_values):
        try:
            df_sig = signals_g5_confluence(df, min_score=ms)
            r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m)
        except Exception as e:
            print(f"  ERROR G5 {name}/{timeframe} ms={ms}: {e}")
            continue

        is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades and r["dd"] <= max_dd
        if r["trades"] >= 5:
            marker = " *" if is_winner else ""
            print(
                f"G5 {name:<8} {timeframe:<4} score>={ms}/5 sl={sl_m:.1f} tp={tp_m:.1f}"
                f"  trades={r['trades']:>4}  tr/yr={r['tr_yr']:>4.0f}"
                f"  wr={r['wr']:>5.1f}%  PF={r['pf']:>5.2f}  DD={r['dd']:>5.1f}%{marker}"
            )
        if is_winner:
            winners.append(_winner_dict("G5_Confluence", name, prefix, timeframe, r, {
                "min_score": ms, "sl_mult": sl_m, "tp_mult": tp_m,
            }))
    return winners


# ---------------------------------------------------------------------------
# Helper: build winner record
# ---------------------------------------------------------------------------

def _winner_dict(
    strategy: str,
    coin: str,
    prefix: str,
    timeframe: str,
    r: dict,
    params: dict,
) -> dict:
    """Build a standardised winner record.

    Args:
        strategy: Strategy label (e.g. 'G1_DualThrust').
        coin: Display coin name (e.g. 'ETH').
        prefix: Raw coin prefix (e.g. 'ethusdt').
        timeframe: '1h' or '4h'.
        r: Simulation result dict.
        params: Strategy-specific parameters dict.

    Returns:
        Standardised winner dict.
    """
    return {
        "strategy": strategy,
        "coin": coin,
        "prefix": prefix,
        "timeframe": timeframe,
        "pf": r["pf"],
        "trades": r["trades"],
        "tr_yr": r["tr_yr"],
        "wr_pct": r["wr"],
        "dd_pct": r["dd"],
        "ret_pct": r["ret"],
        "params": params,
    }


# ---------------------------------------------------------------------------
# Strategy dispatch map
# ---------------------------------------------------------------------------

STRATEGY_SWEEPS = {
    "G1": sweep_g1,
    "G2": sweep_g2,
    "G3": sweep_g3,
    "G4": sweep_g4,
    "G5": sweep_g5,
}


# ---------------------------------------------------------------------------
# Main sweep orchestrator
# ---------------------------------------------------------------------------

def run_sweep(
    coins: List[str],
    strategies: List[str],
    min_pf: float,
    min_trades: int,
    max_dd: float,
) -> List[dict]:
    """Run all selected strategies across all coins and timeframes.

    For each coin:
      1. Test selected strategies on 1H data.
      2. If any 1H winner found, also test on 4H resampled data.

    Args:
        coins: List of coin prefixes.
        strategies: List of strategy keys to run (e.g. ['G1', 'G2']).
        min_pf: Minimum profit factor for a winner.
        min_trades: Minimum trade count for a winner.
        max_dd: Maximum allowable drawdown %.

    Returns:
        All winner dicts from the sweep.
    """
    print(f"\nLoading 1H data for {len(coins)} coin(s)...")
    loaded: Dict[str, tuple] = {}  # name -> (df_1h, raw_df)
    for prefix in coins:
        df = _load_coin(prefix)
        if df is not None:
            coin_name = prefix.replace("usdt", "").upper()
            raw_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
            loaded[coin_name] = (df, df[raw_cols].copy())

    print(f"Loaded {len(loaded)} coins with >= 12 months of 1H data.")
    print(f"Strategies: {', '.join(strategies)}")
    print(f"Filter: PF >= {min_pf}, trades >= {min_trades}, DD <= {max_dd}%\n")

    all_winners: List[dict] = []
    coins_with_winners: set = set()
    prefix_map = {p.replace("usdt", "").upper(): p for p in coins}

    # --- 1H sweep ---
    print("=" * 70)
    print("1H SWEEP")
    print("=" * 70)
    for coin_name in sorted(loaded):
        df_1h, _ = loaded[coin_name]
        prefix_str = prefix_map.get(coin_name, coin_name.lower() + "usdt")
        coin_winners: List[dict] = []
        for strat in strategies:
            fn = STRATEGY_SWEEPS[strat]
            w = fn(coin_name, df_1h, "1h", prefix_str, min_pf, min_trades, max_dd)
            coin_winners.extend(w)
        if coin_winners:
            coins_with_winners.add(coin_name)
        all_winners.extend(coin_winners)

    # --- 4H sweep for coins with 1H winners ---
    if coins_with_winners:
        print(f"\n{'=' * 70}")
        print(f"4H SWEEP — {len(coins_with_winners)} coin(s) with 1H winner(s):")
        print(f"  {', '.join(sorted(coins_with_winners))}")
        print("=" * 70)
        for coin_name in sorted(coins_with_winners):
            _, raw_df = loaded[coin_name]
            df_4h = _resample_4h(raw_df)
            if df_4h is None:
                continue
            prefix_str = prefix_map.get(coin_name, coin_name.lower() + "usdt")
            for strat in strategies:
                fn = STRATEGY_SWEEPS[strat]
                w = fn(coin_name, df_4h, "4h", prefix_str, min_pf, min_trades, max_dd)
                all_winners.extend(w)

    return all_winners


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_summary(winners: List[dict], strategies: List[str]) -> None:
    """Print sorted summary table and per-strategy ranking.

    Args:
        winners: All winner dicts from the sweep.
        strategies: List of strategy keys that were tested.
    """
    print(f"\n{'=' * 90}")
    print("SWEEP COMPLETE — GitHub-Inspired Strategies")
    print("=" * 90)

    if not winners:
        print("\nNo winners found.")
        _print_strategy_ranking([], strategies)
        return

    hdr = (
        f"  {'Strategy':<24} {'Coin':<8} {'TF':<4}"
        f" {'PF':>6} {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'DD%':>6} {'Ret%':>7}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for w in sorted(winners, key=lambda x: -x["pf"]):
        print(
            f"  {w['strategy']:<24} {w['coin']:<8} {w['timeframe']:<4}"
            f" {w['pf']:>6.2f} {w['trades']:>7} {w['tr_yr']:>5.0f}/yr"
            f" {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}% {w['ret_pct']:>6.1f}%"
        )

    print(f"\nTotal winners: {len(winners)}")

    # Per-strategy winner counts
    strat_counts: Dict[str, int] = {}
    for w in winners:
        strat_counts[w["strategy"]] = strat_counts.get(w["strategy"], 0) + 1
    print("\nWinners per strategy:")
    for s, cnt in sorted(strat_counts.items(), key=lambda x: -x[1]):
        print(f"  {s:<26} {cnt} winner(s)")

    # Unique coins with winners
    unique_coins = sorted({f"{w['coin']} ({w['timeframe']})" for w in winners})
    print(f"\nUnique coin/TF combinations with winners: {len(unique_coins)}")
    for c in unique_coins:
        print(f"  {c}")

    _print_strategy_ranking(winners, strategies)

    # Top 10 by PF
    print("\nTop 10 combos by Profit Factor:")
    top_hdr = (
        f"  {'Strategy':<24} {'Coin':<8} {'TF':<4}"
        f" {'PF':>6} {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'DD%':>6}"
    )
    print(top_hdr)
    print("  " + "-" * (len(top_hdr) - 2))
    for w in sorted(winners, key=lambda x: -x["pf"])[:10]:
        print(
            f"  {w['strategy']:<24} {w['coin']:<8} {w['timeframe']:<4}"
            f" {w['pf']:>6.2f} {w['trades']:>7} {w['tr_yr']:>5.0f}/yr"
            f" {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}%"
        )


def _print_strategy_ranking(winners: List[dict], strategies: List[str]) -> None:
    """Print per-strategy ranking summary comparing GitHub strategies.

    Args:
        winners: All winner dicts from the sweep.
        strategies: List of strategy keys that were tested.
    """
    print(f"\n{'=' * 70}")
    print("STRATEGY RANKING (best unique coin per strategy by PF)")
    print("=" * 70)

    labels = {
        "G1": "G1 Dual Thrust (je-suis-tm, 4.5K stars)",
        "G2": "G2 Kalman Filter Trend (QuantConnect)",
        "G3": "G3 Awesome Oscillator (je-suis-tm)",
        "G4": "G4 Range Bounce / OctoBot (4K stars) [expected to FAIL]",
        "G5": "G5 Multi-Indicator Confluence (NostalgiaForInfinity)",
    }

    strat_winners: Dict[str, List[dict]] = {s: [] for s in strategies}
    for w in winners:
        for s in strategies:
            if w["strategy"].startswith(s):
                strat_winners[s].append(w)
                break

    for s in strategies:
        sw = strat_winners[s]
        label = labels.get(s, s)
        if not sw:
            verdict = "FAILED — no winners found"
            print(f"\n  {label}")
            print(f"    Verdict: {verdict}")
        else:
            # Best unique coin (highest PF)
            best = max(sw, key=lambda x: x["pf"])
            unique_coins_set = {w["coin"] for w in sw}
            verdict = f"PASSED — {len(unique_coins_set)} coin(s) found edge"
            print(f"\n  {label}")
            print(f"    Verdict:    {verdict}")
            print(f"    Best combo: {best['coin']} {best['timeframe']}"
                  f"  PF={best['pf']:.2f}  trades={best['trades']}"
                  f"  WR={best['wr_pct']:.1f}%  DD={best['dd_pct']:.1f}%")
            print(f"    Params:     {best['params']}")


def _save_winners(winners: List[dict]) -> None:
    """Write all winners to OUTPUT_FILE as JSON.

    Args:
        winners: List of winner dicts.
    """
    with open(OUTPUT_FILE, "w") as f:
        json.dump(winners, f, indent=2)
    print(f"\nSaved {len(winners)} winner(s) to {OUTPUT_FILE}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="sweep_github_inspired: 5 GitHub-inspired strategy sweep",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help="Comma-separated coin prefixes, e.g. ethusdt,linkusdt",
    )
    parser.add_argument(
        "--strategies",
        type=str,
        default="G1,G2,G3,G4,G5",
        help="Comma-separated strategy keys: G1,G2,G3,G4,G5 (default: all)",
    )
    parser.add_argument(
        "--min-pf",
        type=float,
        default=1.3,
        help="Minimum profit factor (default: 1.3)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=10,
        help="Minimum trade count (default: 10, audit-aligned)",
    )
    parser.add_argument(
        "--max-dd",
        type=float,
        default=25.0,
        help="Maximum drawdown %% (default: 25.0)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # Coin list
    if args.coins:
        coins = [c.lower().strip() for c in args.coins.split(",") if c.strip()]
    else:
        coins = discover_coins(None)

    if not coins:
        print("No coins found. Add *_1h_2y.csv to data/ or pass --coins.")
        sys.exit(1)

    # Strategy list
    strats = [s.strip().upper() for s in args.strategies.split(",") if s.strip()]
    invalid = [s for s in strats if s not in STRATEGY_SWEEPS]
    if invalid:
        print(f"Unknown strategies: {invalid}. Valid: {list(STRATEGY_SWEEPS.keys())}")
        sys.exit(1)

    print(f"sweep_github_inspired.py")
    print(f"Coins:      {len(coins)} (e.g. {', '.join(coins[:5])}{'...' if len(coins) > 5 else ''})")
    print(f"Strategies: {', '.join(strats)}")
    print(f"Filter:     PF >= {args.min_pf}, trades >= {args.min_trades}, DD <= {args.max_dd}%")

    winners = run_sweep(
        coins=coins,
        strategies=strats,
        min_pf=args.min_pf,
        min_trades=args.min_trades,
        max_dd=args.max_dd,
    )

    if winners:
        _save_winners(winners)

    _print_summary(winners, strategies=strats)
