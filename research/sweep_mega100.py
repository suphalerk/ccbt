"""sweep_mega100.py — Massive sweep of 100 combination strategies across all coins.

Each strategy is a COMBINATION of 2-3 indicators. Tests 100 distinct entry logic
definitions, all sharing the same exit framework (SL=2.0 ATR, TP=4.0 ATR) and a
1H-only timeframe. Computation is structured as:

    1. compute_all_indicators() — builds every indicator column once per coin.
    2. get_signals()            — evaluates boolean expressions for a strategy ID.
    3. simulate()               — event-driven P&L simulation.

Strategy categories:
    A. Trend + Momentum   (S1-S20)
    B. Trend + Volatility (S21-S40)
    C. Mean Reversion     (S41-S55)
    D. Volume-Based       (S56-S65)
    E. Multi-Confluence   (S66-S80)
    F. Pattern + Indicator (S81-S90)
    G. Exotic / Adaptive  (S91-S100)

Output: data/sweep_mega100.json

Usage:
    python3 -u research/sweep_mega100.py
    python3 -u research/sweep_mega100.py --coins ethusdt,bnbusdt
    python3 -u research/sweep_mega100.py --min-pf 1.3 --min-trades 10
    python3 -u research/sweep_mega100.py --strategies 1,2,3
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import warnings

import numpy as np
import pandas as pd

# Suppress noisy loggers from bot module
logging.basicConfig(level=logging.WARNING)
logging.getLogger("bot").setLevel(logging.WARNING)

# Suppress pandas fragmentation / future warnings from indicator builder
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import (
    compute_atr,
    compute_ema,
    compute_rsi,
    compute_volume_ma,
    compute_adx_di,
    compute_stochastic,
    compute_zscore,
    compute_williams_r,
    compute_roc,
    compute_choppiness,
)

DATA_DIR = "/Users/iceai/Work/ccbt/data"
OUTPUT_FILE = os.path.join(DATA_DIR, "sweep_mega100.json")

# Simulation constants
COMMISSION = 0.00055   # 0.055% taker
SLIPPAGE = 0.0002      # 0.02% per side
RISK_PCT = 0.01        # 1% risk per trade
LEVERAGE = 25
WARMUP = 120           # bars to skip at start (enough for EMA200 + cloud)

# Default SL/TP multipliers (ATR-based) — same for all 100 strategies
SL_MULT = 2.0
TP_MULT = 4.0

# Trading hours
HOURS_START = 3
HOURS_END = 20


# ---------------------------------------------------------------------------
# Strategy metadata: (id, name, category)
# Used for output labelling only — logic is in get_signals()
# ---------------------------------------------------------------------------

STRATEGY_META: Dict[int, Tuple[str, str]] = {
    # A. Trend + Momentum
    1:  ("EMA+RSI+Vol",         "A"),
    2:  ("EMA+ADX",             "A"),
    3:  ("EMA+MACD",            "A"),
    4:  ("EMA+AO",              "A"),
    5:  ("EMA+Stoch",           "A"),
    6:  ("Ichi+RSI",            "A"),
    7:  ("Ichi+ADX",            "A"),
    8:  ("Ichi+MACD",           "A"),
    9:  ("ST+RSI",              "A"),
    10: ("ST+ADX",              "A"),
    11: ("ST+MACD",             "A"),
    12: ("ST+AO",               "A"),
    13: ("MACD+ADX",            "A"),
    14: ("MACD+Vol",            "A"),
    15: ("ROC+ADX",             "A"),
    16: ("ROC+RSI_zone",        "A"),
    17: ("CCI+ADX",             "A"),
    18: ("CCI+Vol",             "A"),
    19: ("Aroon+RSI",           "A"),
    20: ("Aroon+Vol",           "A"),
    # B. Trend + Volatility
    21: ("EMA+BB_expand",       "B"),
    22: ("EMA+ATR_rise",        "B"),
    23: ("EMA+KC",              "B"),
    24: ("ST+BB_squeeze",       "B"),
    25: ("ST+ATR_pctile",       "B"),
    26: ("Ichi+ATR",            "B"),
    27: ("MACD+BB",             "B"),
    28: ("DualThrust+RSI",      "B"),
    29: ("DualThrust+Vol",      "B"),
    30: ("DualThrust+ADX",      "B"),
    31: ("ATRFlip+RSI",         "B"),
    32: ("ATRFlip+Vol",         "B"),
    33: ("Donch+ATR",           "B"),
    34: ("Donch+Vol",           "B"),
    35: ("Donch+RSI",           "B"),
    36: ("PriceCh+ADX",         "B"),
    37: ("KC+ADX",              "B"),
    38: ("KC+RSI",              "B"),
    39: ("BB+RSI+Vol",          "B"),
    40: ("BB+MACD",             "B"),
    # C. Mean Reversion
    41: ("ZScore+RSI+trend",    "C"),
    42: ("ZScore+Stoch",        "C"),
    43: ("ZScore+BB",           "C"),
    44: ("RSI2+trend+Vol",      "C"),
    45: ("RSI+BB+Vol",          "C"),
    46: ("RangeBounce+Stoch",   "C"),
    47: ("RangeBounce+ZScore",  "C"),
    48: ("ConnorsRSI+trend",    "C"),
    49: ("WR+ADX+RSI",          "C"),
    50: ("Eltrut+Chop",         "C"),
    51: ("BB+CMF",              "C"),
    52: ("Support+RSI_div",     "C"),
    53: ("Fib+RSI+trend",       "C"),
    54: ("Fib+Vol",             "C"),
    55: ("ZScore+OBV_div",      "C"),
    # D. Volume-Based
    56: ("OBV+EMA",             "D"),
    57: ("OBV+RSI",             "D"),
    58: ("CMF+EMA",             "D"),
    59: ("CMF+ADX",             "D"),
    60: ("VolSpike+EMA",        "D"),
    61: ("VolSpike+ST",         "D"),
    62: ("VolSpike+MACD",       "D"),
    63: ("OBV_div+breakout",    "D"),
    64: ("ForceIdx+EMA",        "D"),
    65: ("MFI+trend",           "D"),
    # E. Multi-Confluence
    66: ("Triple_trend",        "E"),
    67: ("Triple_strong",       "E"),
    68: ("Triple_ichi",         "E"),
    69: ("Triple_ST_ichi",      "E"),
    70: ("Quad_confirm",        "E"),
    71: ("Penta_confirm",       "E"),
    72: ("Ribbon+RSI+Vol",      "E"),
    73: ("DualST+Ichi",         "E"),
    74: ("Alligator+RSI+Vol",   "E"),
    75: ("Osc_triple",          "E"),
    76: ("Quad_trend",          "E"),
    77: ("Quad_ST",             "E"),
    78: ("Multi_system",        "E"),
    79: ("All_osc",             "E"),
    80: ("All_MA_above",        "E"),
    # F. Pattern + Indicator
    81: ("Hammer+RSI+trend",    "F"),
    82: ("ShootStar+RSI+trend", "F"),
    83: ("Engulf+Vol+trend",    "F"),
    84: ("HA_rev+RSI",          "F"),
    85: ("InsideBar+ADX",       "F"),
    86: ("3Green+Vol+EMA",      "F"),
    87: ("Doji+squeeze+ADX",    "F"),
    88: ("BigCandle+Vol+EMA",   "F"),
    89: ("PinBar+Fib+RSI",      "F"),
    90: ("Swing+Vol+RSI",       "F"),
    # G. Exotic / Adaptive
    91: ("Kalman+ADX",          "G"),
    92: ("Kalman+RSI",          "G"),
    93: ("Renko+EMA",           "G"),
    94: ("Renko+Vol",           "G"),
    95: ("Pivot+Vol+ADX",       "G"),
    96: ("StochMTF+ZScore",     "G"),
    97: ("Ribbon+AO",           "G"),
    98: ("Chop+ATR+EMA",        "G"),
    99: ("MTF_EMA",             "G"),
    100: ("Regime+Vol",         "G"),
}


# ---------------------------------------------------------------------------
# Supertrend direction (vectorized numpy loop)
# ---------------------------------------------------------------------------

def _compute_st_direction(
    upper: np.ndarray,
    lower: np.ndarray,
    close: np.ndarray,
) -> np.ndarray:
    """Compute Supertrend direction (1=bull, -1=bear) via numpy loop.

    Args:
        upper: Raw upper band values.
        lower: Raw lower band values.
        close: Close price array.

    Returns:
        Integer direction array (1 or -1).
    """
    n = len(close)
    direction = np.ones(n, dtype=np.int8)
    fu = upper.copy()
    fl = lower.copy()
    for i in range(1, n):
        fl[i] = lower[i] if lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1] else fl[i - 1]
        fu[i] = upper[i] if upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1] else fu[i - 1]
        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < fl[i] else 1
        else:
            direction[i] = 1 if close[i] > fu[i] else -1
    return direction


# ---------------------------------------------------------------------------
# Kalman filter (1D price smoother)
# ---------------------------------------------------------------------------

def _kalman_filter(close: pd.Series, q: float = 0.01, r: float = 1.0) -> pd.Series:
    """Simple 1D Kalman filter for price smoothing.

    Args:
        close: Close price series.
        q: Process noise (how fast the true state changes).
        r: Measurement noise (how noisy the observations are).

    Returns:
        Kalman smoothed price series.
    """
    values = close.values.astype(float)
    n = len(values)
    x = np.empty(n)
    p = np.empty(n)
    x[0] = values[0]
    p[0] = 1.0
    for i in range(1, n):
        # Predict
        x_pred = x[i - 1]
        p_pred = p[i - 1] + q
        # Update
        k_gain = p_pred / (p_pred + r)
        x[i] = x_pred + k_gain * (values[i] - x_pred)
        p[i] = (1 - k_gain) * p_pred
    return pd.Series(x, index=close.index)


# ---------------------------------------------------------------------------
# All-indicators builder — called ONCE per coin
# ---------------------------------------------------------------------------

def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute every indicator needed by the 100 strategies.

    Must be called on a raw OHLCV DataFrame with a DatetimeIndex.
    Returns a new DataFrame with all indicator columns appended.

    Args:
        df: Raw OHLCV DataFrame (open/high/low/close/volume, DatetimeIndex UTC).

    Returns:
        DataFrame copy with ~70 new indicator columns.
    """
    df = df.copy()

    # --- Time filters ---
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek  # 0=Mon, 6=Sun
    df["in_hours"] = (df["hour"] >= HOURS_START) & (df["hour"] < HOURS_END)
    df["not_we"] = df["dow"] < 5
    df["time_ok"] = df["in_hours"] & df["not_we"]

    # --- EMAs ---
    df["ema5"]   = compute_ema(df["close"], 5)
    df["ema9"]   = compute_ema(df["close"], 9)
    df["ema13"]  = compute_ema(df["close"], 13)
    df["ema21"]  = compute_ema(df["close"], 21)
    df["ema50"]  = compute_ema(df["close"], 50)
    df["ema100"] = compute_ema(df["close"], 100)
    df["ema200"] = compute_ema(df["close"], 200)

    # --- EMA crossover events (1=just crossed up, -1=just crossed down) ---
    df["ema_cross_up"]   = (df["ema9"] > df["ema21"]) & (df["ema9"].shift(1) <= df["ema21"].shift(1))
    df["ema_cross_down"] = (df["ema9"] < df["ema21"]) & (df["ema9"].shift(1) >= df["ema21"].shift(1))
    df["ema_bull"]  = df["ema9"] > df["ema21"]
    df["ema_bear"]  = df["ema9"] < df["ema21"]
    df["ema50_bull"] = df["close"] > df["ema50"]
    df["ema50_bear"] = df["close"] < df["ema50"]

    # --- ATR ---
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["atr_ma20"] = df["atr"].rolling(20).mean()
    df["atr_ma50"] = df["atr"].rolling(50).mean()
    df["atr_rising"] = df["atr"] > df["atr_ma20"]
    df["atr_pctile"] = df["atr"].rolling(100).rank(pct=True)

    # --- RSI ---
    df["rsi14"] = compute_rsi(df["close"], 14)
    df["rsi2"]  = compute_rsi(df["close"], 2)
    df["rsi_bull"] = df["rsi14"] > 50
    df["rsi_bear"] = df["rsi14"] < 50
    # RSI 40-60 zone (momentum zone, not extreme)
    df["rsi_zone_bull"] = (df["rsi14"] > 40) & (df["rsi14"] < 60)

    # --- Volume ---
    df["vol_ma20"]  = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / df["vol_ma20"].clip(lower=1e-10)
    df["vol_1_5x"]  = df["vol_ratio"] > 1.5
    df["vol_2x"]    = df["vol_ratio"] > 2.0
    df["vol_3x"]    = df["vol_ratio"] > 3.0

    # --- MACD ---
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd_line"]   = ema12 - ema26
    df["macd_signal"] = df["macd_line"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd_line"] - df["macd_signal"]
    df["macd_bull"] = df["macd_hist"] > 0
    df["macd_bear"] = df["macd_hist"] < 0
    df["macd_cross_up"]   = (df["macd_line"] > df["macd_signal"]) & (df["macd_line"].shift(1) <= df["macd_signal"].shift(1))
    df["macd_cross_down"] = (df["macd_line"] < df["macd_signal"]) & (df["macd_line"].shift(1) >= df["macd_signal"].shift(1))

    # --- Stochastic ---
    stoch_k, stoch_d = compute_stochastic(df["high"], df["low"], df["close"])
    df["stoch_k"] = stoch_k
    df["stoch_d"] = stoch_d
    df["stoch_cross_up"]   = (df["stoch_k"] > df["stoch_d"]) & (df["stoch_k"].shift(1) <= df["stoch_d"].shift(1))
    df["stoch_cross_down"] = (df["stoch_k"] < df["stoch_d"]) & (df["stoch_k"].shift(1) >= df["stoch_d"].shift(1))
    df["stoch_oversold"]   = df["stoch_k"] < 25
    df["stoch_overbought"] = df["stoch_k"] > 75
    df["stoch_bull"]       = df["stoch_k"] > 50

    # --- ADX / DI ---
    adx, di_plus, di_minus = compute_adx_di(df["high"], df["low"], df["close"])
    df["adx"]      = adx
    df["di_plus"]  = di_plus
    df["di_minus"] = di_minus
    df["adx25"]    = df["adx"] > 25
    df["adx20"]    = df["adx"] > 20
    df["di_bull"]  = df["di_plus"] > df["di_minus"]
    df["di_bear"]  = df["di_minus"] > df["di_plus"]

    # --- Awesome Oscillator ---
    mid = (df["high"] + df["low"]) / 2
    df["ao"] = mid.rolling(5).mean() - mid.rolling(34).mean()
    df["ao_bull"] = df["ao"] > 0
    df["ao_bear"] = df["ao"] < 0

    # --- Bollinger Bands ---
    bb_ma  = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = bb_ma + 2.0 * bb_std
    df["bb_lower"] = bb_ma - 2.0 * bb_std
    df["bb_mid"]   = bb_ma
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / bb_ma.clip(lower=1e-10)
    df["bb_width_pctile"] = df["bb_width"].rolling(100).rank(pct=True)
    df["bb_squeeze"] = df["bb_width_pctile"] < 0.25   # width in bottom 25% = squeeze
    df["bb_squeeze_release"] = (~df["bb_squeeze"]) & df["bb_squeeze"].shift(1).fillna(False)
    df["bb_expanding"] = df["bb_width_pctile"] > 0.50  # width above median

    # --- Keltner Channel ---
    kc_ema = df["close"].ewm(span=20, adjust=False).mean()
    df["kc_upper"] = kc_ema + 1.5 * df["atr"]
    df["kc_lower"] = kc_ema - 1.5 * df["atr"]
    df["kc_break_up"]   = df["close"] > df["kc_upper"]
    df["kc_break_down"] = df["close"] < df["kc_lower"]

    # --- Ichimoku ---
    df["tenkan"] = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    df["kijun"]  = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    df["cloud_top"]    = pd.concat([span_a, span_b], axis=1).max(axis=1)
    df["cloud_bottom"] = pd.concat([span_a, span_b], axis=1).min(axis=1)
    df["tk_cross_up"]   = (df["tenkan"] > df["kijun"]) & (df["tenkan"].shift(1) <= df["kijun"].shift(1))
    df["tk_cross_down"] = (df["tenkan"] < df["kijun"]) & (df["tenkan"].shift(1) >= df["kijun"].shift(1))
    df["above_cloud"]   = df["close"] > df["cloud_top"]
    df["below_cloud"]   = df["close"] < df["cloud_bottom"]

    # --- Supertrend (ATR mult=2.0) ---
    hl2 = (df["high"] + df["low"]) / 2
    st_upper = hl2 + 2.0 * df["atr"]
    st_lower = hl2 - 2.0 * df["atr"]
    st_dir = _compute_st_direction(st_upper.values, st_lower.values, df["close"].values)
    df["st_dir"] = pd.Series(st_dir, index=df.index)
    df["st_bull"]      = df["st_dir"] == 1
    df["st_bear"]      = df["st_dir"] == -1
    df["st_flip_up"]   = (df["st_dir"] == 1) & (df["st_dir"].shift(1) == -1)
    df["st_flip_down"] = (df["st_dir"] == -1) & (df["st_dir"].shift(1) == 1)

    # --- Supertrend #2 (ATR mult=3.0 for dual ST) ---
    st2_upper = hl2 + 3.0 * df["atr"]
    st2_lower = hl2 - 3.0 * df["atr"]
    st2_dir = _compute_st_direction(st2_upper.values, st2_lower.values, df["close"].values)
    df["st2_dir"]  = pd.Series(st2_dir, index=df.index)
    df["st2_bull"] = df["st2_dir"] == 1
    df["st2_bear"] = df["st2_dir"] == -1

    # --- Z-Score ---
    df["zscore"] = compute_zscore(df["close"], 20)
    df["zscore_low"]  = df["zscore"] < -2.0
    df["zscore_high"] = df["zscore"] > 2.0
    df["zscore_neg1"] = df["zscore"] < -1.5

    # --- OBV ---
    obv_delta = np.where(df["close"] > df["close"].shift(1), df["volume"],
                np.where(df["close"] < df["close"].shift(1), -df["volume"], 0))
    df["obv"] = pd.Series(obv_delta, index=df.index).cumsum()
    df["obv_ma20"]  = df["obv"].rolling(20).mean()
    df["obv_break"] = (df["obv"] > df["obv_ma20"]) & (df["obv"].shift(1) <= df["obv_ma20"].shift(1))
    df["obv_break_down"] = (df["obv"] < df["obv_ma20"]) & (df["obv"].shift(1) >= df["obv_ma20"].shift(1))
    # OBV divergence: price lower high, OBV higher high (bullish divergence proxy)
    df["obv_rising"]  = df["obv"] > df["obv"].shift(5)
    df["obv_falling"] = df["obv"] < df["obv"].shift(5)

    # --- CMF (Chaikin Money Flow) ---
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"]).clip(lower=1e-10)
    df["cmf"] = (mfm * df["volume"]).rolling(20).sum() / df["volume"].rolling(20).sum().clip(lower=1e-10)
    df["cmf_cross_up"]   = (df["cmf"] > 0) & (df["cmf"].shift(1) <= 0)
    df["cmf_cross_down"] = (df["cmf"] < 0) & (df["cmf"].shift(1) >= 0)
    df["cmf_bull"] = df["cmf"] > 0
    df["cmf_bear"] = df["cmf"] < 0

    # --- CCI ---
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_mean   = tp.rolling(20).mean()
    tp_mad    = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["cci"] = (tp - tp_mean) / (0.015 * tp_mad.clip(lower=1e-10))
    df["cci_bull"] = df["cci"] > 100
    df["cci_bear"] = df["cci"] < -100

    # --- ROC ---
    df["roc10"]      = compute_roc(df["close"], 10)
    df["roc_bull"]   = df["roc10"] > 0
    df["roc_bear"]   = df["roc10"] < 0

    # --- Williams %R ---
    df["wr14"]          = compute_williams_r(df["high"], df["low"], df["close"], 14)
    df["wr_oversold"]   = df["wr14"] < -80
    df["wr_overbought"] = df["wr14"] > -20

    # --- Aroon ---
    df["aroon_up"]   = df["high"].rolling(25).apply(
        lambda x: float(np.argmax(x)) / 24.0 * 100, raw=True)
    df["aroon_down"] = df["low"].rolling(25).apply(
        lambda x: float(np.argmin(x)) / 24.0 * 100, raw=True)
    df["aroon_cross_up"]   = (df["aroon_up"] > df["aroon_down"]) & (df["aroon_up"].shift(1) <= df["aroon_down"].shift(1))
    df["aroon_cross_down"] = (df["aroon_up"] < df["aroon_down"]) & (df["aroon_up"].shift(1) >= df["aroon_down"].shift(1))

    # --- Donchian Channel (20-period, shifted to avoid look-ahead) ---
    df["donch_high"] = df["high"].rolling(20).max().shift(1)
    df["donch_low"]  = df["low"].rolling(20).min().shift(1)
    df["donch_break_up"]   = df["close"] > df["donch_high"]
    df["donch_break_down"] = df["close"] < df["donch_low"]

    # --- Dual Thrust ---
    dt_range = df["high"].rolling(20).max() - df["low"].rolling(20).min()
    df["dt_upper"] = df["close"].shift(1) + 0.5 * dt_range.shift(1)
    df["dt_lower"] = df["close"].shift(1) - 0.5 * dt_range.shift(1)
    df["dt_break_up"]   = df["close"] > df["dt_upper"]
    df["dt_break_down"] = df["close"] < df["dt_lower"]

    # --- Choppiness ---
    df["choppiness"] = compute_choppiness(df["high"], df["low"], df["close"], 14)
    df["trending"]   = df["choppiness"] < 38.2
    df["ranging"]    = df["choppiness"] > 61.8

    # --- Body / Wick ---
    df["body"]        = (df["close"] - df["open"]).abs()
    df["upper_wick"]  = df["high"] - df[["close", "open"]].max(axis=1)
    df["lower_wick"]  = df[["close", "open"]].min(axis=1) - df["low"]
    df["avg_body"]    = df["body"].rolling(20).mean()
    df["candle_bull"] = df["close"] > df["open"]
    df["candle_bear"] = df["close"] < df["open"]
    df["big_candle"]  = df["body"] > df["avg_body"] * 1.5

    # --- Heikin-Ashi ---
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open  = (df["open"].shift(1) + df["close"].shift(1)) / 2
    df["ha_bull"] = ha_close > ha_open           # HA green candle
    df["ha_bear"] = ha_close < ha_open           # HA red candle
    df["ha_rev_up"]   = df["ha_bull"] & ~df["ha_bull"].shift(1).fillna(False)   # first HA green
    df["ha_rev_down"] = df["ha_bear"] & ~df["ha_bear"].shift(1).fillna(False)   # first HA red

    # --- Force Index ---
    df["force_idx"] = (df["close"] - df["close"].shift(1)) * df["volume"]
    df["force_ma13"] = df["force_idx"].ewm(span=13, adjust=False).mean()
    df["force_cross_up"]   = (df["force_ma13"] > 0) & (df["force_ma13"].shift(1) <= 0)
    df["force_cross_down"] = (df["force_ma13"] < 0) & (df["force_ma13"].shift(1) >= 0)

    # --- MFI (Money Flow Index) ---
    tp_mfi = (df["high"] + df["low"] + df["close"]) / 3
    mf     = tp_mfi * df["volume"]
    up_mf  = mf.where(tp_mfi > tp_mfi.shift(1), 0.0)
    dn_mf  = mf.where(tp_mfi < tp_mfi.shift(1), 0.0)
    raw_mfr = up_mf.rolling(14).sum() / dn_mf.rolling(14).sum().clip(lower=1e-10)
    df["mfi"] = 100.0 - 100.0 / (1.0 + raw_mfr)
    df["mfi_oversold"]   = df["mfi"] < 20
    df["mfi_overbought"] = df["mfi"] > 80

    # --- EMA Ribbon (all 5 EMAs must align) ---
    df["ribbon_bull"] = (
        (df["ema5"] > df["ema9"]) &
        (df["ema9"] > df["ema13"]) &
        (df["ema13"] > df["ema21"]) &
        (df["ema21"] > df["ema50"])
    )
    df["ribbon_bear"] = (
        (df["ema5"] < df["ema9"]) &
        (df["ema9"] < df["ema13"]) &
        (df["ema13"] < df["ema21"]) &
        (df["ema21"] < df["ema50"])
    )

    # --- All MAs above (mega-trend) ---
    df["all_ma_bull"] = (
        (df["close"] > df["ema9"]) &
        (df["close"] > df["ema21"]) &
        (df["close"] > df["ema50"]) &
        (df["close"] > df["ema100"]) &
        (df["close"] > df["ema200"])
    )
    df["all_ma_bear"] = (
        (df["close"] < df["ema9"]) &
        (df["close"] < df["ema21"]) &
        (df["close"] < df["ema50"]) &
        (df["close"] < df["ema100"]) &
        (df["close"] < df["ema200"])
    )

    # --- Williams Alligator ---
    # Jaw = SMA(13) shifted 8, Teeth = SMA(8) shifted 5, Lips = SMA(5) shifted 3
    jaw   = (df["high"] + df["low"]).rolling(13).mean() / 2
    jaw   = jaw.shift(8)
    teeth = (df["high"] + df["low"]).rolling(8).mean() / 2
    teeth = teeth.shift(5)
    lips  = (df["high"] + df["low"]).rolling(5).mean() / 2
    lips  = lips.shift(3)
    df["alligator_bull"] = (lips > teeth) & (teeth > jaw)
    df["alligator_bear"] = (lips < teeth) & (teeth < jaw)

    # --- Connors RSI (3-component: RSI2, streak-RSI, percentile rank) ---
    # RSI(3) of close
    rsi3 = compute_rsi(df["close"], 3)
    # Streak: consecutive up/down bars
    streak = (df["close"] - df["close"].shift(1)).apply(np.sign)
    streak_cum = streak.copy()
    streak_vals = streak.values
    streak_cum_vals = np.zeros(len(streak_vals))
    for i in range(len(streak_vals)):
        if i == 0:
            streak_cum_vals[i] = streak_vals[i]
        elif streak_vals[i] == streak_vals[i - 1]:
            streak_cum_vals[i] = streak_cum_vals[i - 1] + streak_vals[i]
        else:
            streak_cum_vals[i] = streak_vals[i]
    streak_series = pd.Series(streak_cum_vals, index=df.index)
    streak_rsi = compute_rsi(streak_series, 2)
    # Percentile rank of 1-day return over 100 bars
    ret1 = df["close"].pct_change()
    pctile_rank = ret1.rolling(100).rank(pct=True) * 100
    df["connors_rsi"] = (rsi3 + streak_rsi + pctile_rank) / 3.0
    df["connors_oversold"]   = df["connors_rsi"] < 15
    df["connors_overbought"] = df["connors_rsi"] > 85

    # --- Fibonacci (simplified: 38.2% and 61.8% retracement from rolling window) ---
    fib_window = 50
    roll_high = df["high"].rolling(fib_window).max()
    roll_low  = df["low"].rolling(fib_window).min()
    fib_range = roll_high - roll_low
    df["fib_382"] = roll_high - 0.382 * fib_range  # 38.2% retrace = price at this level
    df["fib_618"] = roll_high - 0.618 * fib_range  # 61.8% retrace = price at this level
    # Near fib level = within 0.5% of it
    df["near_fib618_bull"] = (df["close"] - df["fib_618"]).abs() / df["close"].clip(lower=1e-10) < 0.005
    df["near_fib382_bull"] = (df["close"] - df["fib_382"]).abs() / df["close"].clip(lower=1e-10) < 0.005

    # --- Pivot points (daily) ---
    # Use previous bar's H/L/C as "daily pivot" approximation on 1H
    # Classic pivot: (H+L+C)/3; R1=(2*P-L); S1=(2*P-H)
    ph = df["high"].shift(1)
    pl = df["low"].shift(1)
    pc = df["close"].shift(1)
    pivot = (ph + pl + pc) / 3.0
    r1 = 2.0 * pivot - pl
    s1 = 2.0 * pivot - ph
    df["above_r1"] = df["close"] > r1
    df["below_s1"] = df["close"] < s1
    df["pivot_break_up"]   = df["above_r1"] & ~df["above_r1"].shift(1).fillna(False)
    df["pivot_break_down"] = df["below_s1"] & ~df["below_s1"].shift(1).fillna(False)

    # --- Swing high/low breakout ---
    # Swing high: bar is highest in surrounding 5 bars (3 left + 3 right, shifted to avoid bias)
    swing_high = df["high"].rolling(7).max().shift(2)
    swing_low  = df["low"].rolling(7).min().shift(2)
    df["swing_break_up"]   = df["close"] > swing_high
    df["swing_break_down"] = df["close"] < swing_low

    # --- Kalman filter ---
    kf = _kalman_filter(df["close"])
    df["kalman_cross_up"]   = (df["close"] > kf) & (df["close"].shift(1) <= kf.shift(1))
    df["kalman_cross_down"] = (df["close"] < kf) & (df["close"].shift(1) >= kf.shift(1))

    # --- Renko approximation (threshold = 1×ATR) ---
    # When price moves more than ATR in a direction vs prior renko block, signal
    df["renko_bull"] = df["close"] > df["close"].shift(1) + df["atr"].shift(1)
    df["renko_bear"] = df["close"] < df["close"].shift(1) - df["atr"].shift(1)

    # --- Multi-timeframe EMA (4H EMA9 vs EMA21) ---
    # Resample close to 4H and forward-fill onto 1H index
    close_4h = df["close"].resample("4h").last().dropna()
    ema9_4h  = compute_ema(close_4h, 9).reindex(df.index, method="ffill")
    ema21_4h = compute_ema(close_4h, 21).reindex(df.index, method="ffill")
    df["ema_4h_bull"] = ema9_4h > ema21_4h
    df["ema_4h_bear"] = ema9_4h < ema21_4h

    # --- Regime transition: ranging→trending ---
    was_ranging = df["ranging"].shift(1).fillna(False)
    df["regime_trans_bull"] = was_ranging & df["trending"] & df["ema_bull"]
    df["regime_trans_bear"] = was_ranging & df["trending"] & df["ema_bear"]

    # --- Stochastic multi-timeframe (4H stoch) ---
    high_4h  = df["high"].resample("4h").max().dropna()
    low_4h   = df["low"].resample("4h").min().dropna()
    close_4h_full = df["close"].resample("4h").last().dropna()
    stoch_k_4h, _ = compute_stochastic(high_4h, low_4h, close_4h_full)
    df["stoch_4h_bull"] = stoch_k_4h.reindex(df.index, method="ffill").fillna(50) > 50
    df["stoch_4h_bear"] = stoch_k_4h.reindex(df.index, method="ffill").fillna(50) < 50

    # --- Inside bar ---
    df["inside_bar"] = (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))
    # Inside bar breakout: previous inside bar, current breaks out
    df["inside_bar_break_up"]   = df["inside_bar"].shift(1).fillna(False) & (df["close"] > df["high"].shift(2))
    df["inside_bar_break_down"] = df["inside_bar"].shift(1).fillna(False) & (df["close"] < df["low"].shift(2))

    # --- Pin bar ---
    # Bullish pin: lower_wick > 2× body, upper_wick < 0.3× body
    # Bearish pin: upper_wick > 2× body, lower_wick < 0.3× body
    body_safe = df["body"].clip(lower=1e-10)
    df["pin_bull"] = (df["lower_wick"] > 2.0 * body_safe) & (df["upper_wick"] < 0.5 * body_safe)
    df["pin_bear"] = (df["upper_wick"] > 2.0 * body_safe) & (df["lower_wick"] < 0.5 * body_safe)

    # --- Hammer / Shooting star (same as pin bar but with trend context) ---
    df["hammer"]        = df["pin_bull"] & df["ema50_bull"]  # bullish reversal in uptrend
    df["shooting_star"] = df["pin_bear"] & df["ema50_bear"]  # bearish reversal in downtrend

    # --- Doji ---
    df["doji"] = df["body"] < df["avg_body"] * 0.2

    # --- Engulfing ---
    df["engulf_bull"] = (
        df["candle_bull"] &
        df["candle_bear"].shift(1).fillna(False) &
        (df["open"] < df["close"].shift(1)) &
        (df["close"] > df["open"].shift(1))
    )
    df["engulf_bear"] = (
        df["candle_bear"] &
        df["candle_bull"].shift(1).fillna(False) &
        (df["open"] > df["close"].shift(1)) &
        (df["close"] < df["open"].shift(1))
    )

    # --- 3 consecutive candles ---
    df["three_green"] = (
        df["candle_bull"] &
        df["candle_bull"].shift(1).fillna(False) &
        df["candle_bull"].shift(2).fillna(False)
    )
    df["three_red"] = (
        df["candle_bear"] &
        df["candle_bear"].shift(1).fillna(False) &
        df["candle_bear"].shift(2).fillna(False)
    )

    # --- Volume increasing ---
    df["vol_increasing"] = (
        (df["volume"] > df["volume"].shift(1)) &
        (df["volume"].shift(1) > df["volume"].shift(2))
    )

    # --- Support bounce proxy: close near 50-bar rolling low + upward close ---
    df["near_support"] = (
        (df["close"] - df["low"].rolling(50).min()) /
        (df["high"].rolling(50).max() - df["low"].rolling(50).min()).clip(lower=1e-10)
    ) < 0.1
    df["near_resistance"] = (
        (df["high"].rolling(50).max() - df["close"]) /
        (df["high"].rolling(50).max() - df["low"].rolling(50).min()).clip(lower=1e-10)
    ) < 0.1

    # --- RSI divergence proxy ---
    # Bullish: price makes lower low in last 5 bars but RSI makes higher low
    price_ll = df["close"] < df["close"].shift(5)
    rsi_hl   = df["rsi14"] > df["rsi14"].shift(5)
    df["rsi_bull_div"] = price_ll & rsi_hl & (df["rsi14"] < 40)

    price_hh = df["close"] > df["close"].shift(5)
    rsi_lh   = df["rsi14"] < df["rsi14"].shift(5)
    df["rsi_bear_div"] = price_hh & rsi_lh & (df["rsi14"] > 60)

    # Defragment: copying consolidates the internal block structure
    return df.copy()


# ---------------------------------------------------------------------------
# Signal generator: 100 strategies
# ---------------------------------------------------------------------------

def get_signals(df: pd.DataFrame, sid: int) -> Tuple[pd.Series, pd.Series]:
    """Return (long_signal, short_signal) boolean Series for strategy sid.

    All signals reference the CURRENT bar's indicators. The caller shifts
    them by 1 before entering so we always trade on a closed candle.
    time_ok (hours + no-weekend) is applied inside here.

    Args:
        df: DataFrame with all indicator columns (from compute_all_indicators).
        sid: Strategy ID (1-100).

    Returns:
        (long_signal, short_signal) — boolean pd.Series aligned to df.index.
    """
    h = df["time_ok"]  # shorthand

    # ---- A. Trend + Momentum (1-20) ----

    if sid == 1:   # EMA+RSI+Vol
        L = df["ema_cross_up"] & (df["rsi14"] > 50) & df["vol_1_5x"] & df["ema50_bull"] & h
        S = df["ema_cross_down"] & (df["rsi14"] < 50) & df["vol_1_5x"] & df["ema50_bear"] & h

    elif sid == 2:   # EMA+ADX
        L = df["ema_cross_up"] & df["adx25"] & df["di_bull"] & df["ema50_bull"] & h
        S = df["ema_cross_down"] & df["adx25"] & df["di_bear"] & df["ema50_bear"] & h

    elif sid == 3:   # EMA+MACD
        L = df["ema_cross_up"] & df["macd_bull"] & df["ema50_bull"] & h
        S = df["ema_cross_down"] & df["macd_bear"] & df["ema50_bear"] & h

    elif sid == 4:   # EMA+AO
        L = df["ema_cross_up"] & df["ao_bull"] & df["ema50_bull"] & h
        S = df["ema_cross_down"] & df["ao_bear"] & df["ema50_bear"] & h

    elif sid == 5:   # EMA+Stoch
        L = df["ema_cross_up"] & df["stoch_cross_up"] & ~df["stoch_overbought"] & df["ema50_bull"] & h
        S = df["ema_cross_down"] & df["stoch_cross_down"] & ~df["stoch_oversold"] & df["ema50_bear"] & h

    elif sid == 6:   # Ichi+RSI
        L = df["tk_cross_up"] & df["rsi_bull"] & df["above_cloud"] & h
        S = df["tk_cross_down"] & df["rsi_bear"] & df["below_cloud"] & h

    elif sid == 7:   # Ichi+ADX
        L = df["tk_cross_up"] & df["adx25"] & df["di_bull"] & df["above_cloud"] & h
        S = df["tk_cross_down"] & df["adx25"] & df["di_bear"] & df["below_cloud"] & h

    elif sid == 8:   # Ichi+MACD
        L = df["tk_cross_up"] & df["macd_bull"] & df["above_cloud"] & h
        S = df["tk_cross_down"] & df["macd_bear"] & df["below_cloud"] & h

    elif sid == 9:   # ST+RSI
        L = df["st_flip_up"] & df["rsi_bull"] & h
        S = df["st_flip_down"] & df["rsi_bear"] & h

    elif sid == 10:  # ST+ADX
        L = df["st_flip_up"] & df["adx25"] & df["di_bull"] & h
        S = df["st_flip_down"] & df["adx25"] & df["di_bear"] & h

    elif sid == 11:  # ST+MACD
        L = df["st_flip_up"] & df["macd_bull"] & h
        S = df["st_flip_down"] & df["macd_bear"] & h

    elif sid == 12:  # ST+AO
        L = df["st_flip_up"] & df["ao_bull"] & h
        S = df["st_flip_down"] & df["ao_bear"] & h

    elif sid == 13:  # MACD+ADX
        L = df["macd_cross_up"] & df["adx25"] & df["di_bull"] & df["ema50_bull"] & h
        S = df["macd_cross_down"] & df["adx25"] & df["di_bear"] & df["ema50_bear"] & h

    elif sid == 14:  # MACD+Vol
        L = df["macd_cross_up"] & df["vol_2x"] & df["ema50_bull"] & h
        S = df["macd_cross_down"] & df["vol_2x"] & df["ema50_bear"] & h

    elif sid == 15:  # ROC+ADX
        L = df["roc_bull"] & df["adx25"] & df["di_bull"] & df["ema50_bull"] & h
        # Deduplicate consecutive signals
        L = L & ~L.shift(1).fillna(False)
        S = df["roc_bear"] & df["adx25"] & df["di_bear"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 16:  # ROC+RSI_zone
        L = df["roc_bull"] & df["rsi_zone_bull"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["roc_bear"] & df["rsi_zone_bull"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 17:  # CCI+ADX
        L = df["cci_bull"] & df["adx25"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["cci_bear"] & df["adx25"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 18:  # CCI+Vol
        L = df["cci_bull"] & df["vol_1_5x"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["cci_bear"] & df["vol_1_5x"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 19:  # Aroon+RSI
        L = df["aroon_cross_up"] & df["rsi_bull"] & df["ema50_bull"] & h
        S = df["aroon_cross_down"] & df["rsi_bear"] & df["ema50_bear"] & h

    elif sid == 20:  # Aroon+Vol
        L = df["aroon_cross_up"] & df["vol_1_5x"] & df["ema50_bull"] & h
        S = df["aroon_cross_down"] & df["vol_1_5x"] & df["ema50_bear"] & h

    # ---- B. Trend + Volatility (21-40) ----

    elif sid == 21:  # EMA+BB_expand
        L = df["ema_cross_up"] & df["bb_expanding"] & df["ema50_bull"] & h
        S = df["ema_cross_down"] & df["bb_expanding"] & df["ema50_bear"] & h

    elif sid == 22:  # EMA+ATR_rise
        L = df["ema_cross_up"] & df["atr_rising"] & df["ema50_bull"] & h
        S = df["ema_cross_down"] & df["atr_rising"] & df["ema50_bear"] & h

    elif sid == 23:  # EMA+KC
        L = df["ema_cross_up"] & df["kc_break_up"] & h
        S = df["ema_cross_down"] & df["kc_break_down"] & h

    elif sid == 24:  # ST+BB_squeeze
        L = df["st_flip_up"] & df["bb_squeeze_release"] & h
        S = df["st_flip_down"] & df["bb_squeeze_release"] & h

    elif sid == 25:  # ST+ATR_pctile
        L = df["st_flip_up"] & (df["atr_pctile"] > 0.5) & h
        S = df["st_flip_down"] & (df["atr_pctile"] > 0.5) & h

    elif sid == 26:  # Ichi+ATR
        L = df["tk_cross_up"] & df["atr_rising"] & df["above_cloud"] & h
        S = df["tk_cross_down"] & df["atr_rising"] & df["below_cloud"] & h

    elif sid == 27:  # MACD+BB
        L = df["macd_cross_up"] & df["bb_expanding"] & df["ema50_bull"] & h
        S = df["macd_cross_down"] & df["bb_expanding"] & df["ema50_bear"] & h

    elif sid == 28:  # DualThrust+RSI
        L = df["dt_break_up"] & df["rsi_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["dt_break_down"] & df["rsi_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 29:  # DualThrust+Vol
        L = df["dt_break_up"] & df["vol_1_5x"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["dt_break_down"] & df["vol_1_5x"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 30:  # DualThrust+ADX
        L = df["dt_break_up"] & df["adx25"] & df["di_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["dt_break_down"] & df["adx25"] & df["di_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 31:  # ATRFlip+RSI — ATR percentile crosses 50 + RSI
        atr_flip = (df["atr_pctile"] > 0.5) & (df["atr_pctile"].shift(1) <= 0.5)
        L = atr_flip & df["rsi_bull"] & df["ema_bull"] & df["ema50_bull"] & h
        S = atr_flip & df["rsi_bear"] & df["ema_bear"] & df["ema50_bear"] & h

    elif sid == 32:  # ATRFlip+Vol
        atr_flip = (df["atr_pctile"] > 0.5) & (df["atr_pctile"].shift(1) <= 0.5)
        L = atr_flip & df["vol_1_5x"] & df["ema50_bull"] & h
        S = atr_flip & df["vol_1_5x"] & df["ema50_bear"] & h

    elif sid == 33:  # Donch+ATR
        L = df["donch_break_up"] & df["atr_rising"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["donch_break_down"] & df["atr_rising"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 34:  # Donch+Vol
        L = df["donch_break_up"] & df["vol_2x"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["donch_break_down"] & df["vol_2x"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 35:  # Donch+RSI
        L = df["donch_break_up"] & (df["rsi14"] > 55) & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["donch_break_down"] & (df["rsi14"] < 45) & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 36:  # PriceCh+ADX — price channel 20 + ADX
        L = df["donch_break_up"] & df["adx25"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["donch_break_down"] & df["adx25"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 37:  # KC+ADX
        L = df["kc_break_up"] & df["adx25"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["kc_break_down"] & df["adx25"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 38:  # KC+RSI
        L = df["kc_break_up"] & (df["rsi14"] > 55) & h
        L = L & ~L.shift(1).fillna(False)
        S = df["kc_break_down"] & (df["rsi14"] < 45) & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 39:  # BB+RSI+Vol
        bb_break_up   = df["close"] > df["bb_upper"]
        bb_break_down = df["close"] < df["bb_lower"]
        L = bb_break_up & (df["rsi14"] > 55) & df["vol_1_5x"] & h
        L = L & ~L.shift(1).fillna(False)
        S = bb_break_down & (df["rsi14"] < 45) & df["vol_1_5x"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 40:  # BB+MACD
        bb_break_up   = df["close"] > df["bb_upper"]
        bb_break_down = df["close"] < df["bb_lower"]
        L = bb_break_up & df["macd_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = bb_break_down & df["macd_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    # ---- C. Mean Reversion (41-55) ----

    elif sid == 41:  # ZScore+RSI+trend
        L = df["zscore_low"] & (df["rsi14"] < 30) & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["zscore_high"] & (df["rsi14"] > 70) & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 42:  # ZScore+Stoch
        L = df["zscore_low"] & df["stoch_oversold"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["zscore_high"] & df["stoch_overbought"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 43:  # ZScore+BB
        bb_lower_touch = df["close"] < df["bb_lower"]
        bb_upper_touch = df["close"] > df["bb_upper"]
        L = df["zscore_low"] & bb_lower_touch & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["zscore_high"] & bb_upper_touch & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 44:  # RSI2+trend+Vol
        L = (df["rsi2"] < 10) & df["ema50_bull"] & df["vol_1_5x"] & h
        L = L & ~L.shift(1).fillna(False)
        S = (df["rsi2"] > 90) & df["ema50_bear"] & df["vol_1_5x"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 45:  # RSI+BB+Vol
        L = (df["rsi14"] < 30) & (df["close"] < df["bb_lower"]) & df["vol_1_5x"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = (df["rsi14"] > 70) & (df["close"] > df["bb_upper"]) & df["vol_1_5x"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 46:  # RangeBounce+Stoch  (range = choppiness high, price near low)
        L = df["ranging"] & df["near_support"] & df["stoch_oversold"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["ranging"] & df["near_resistance"] & df["stoch_overbought"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 47:  # RangeBounce+ZScore
        L = df["ranging"] & df["near_support"] & df["zscore_neg1"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["ranging"] & df["near_resistance"] & df["zscore_high"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 48:  # ConnorsRSI+trend
        L = df["connors_oversold"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["connors_overbought"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 49:  # WR+ADX+RSI
        L = df["wr_oversold"] & df["adx25"] & (df["rsi14"] < 35) & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["wr_overbought"] & df["adx25"] & (df["rsi14"] > 65) & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 50:  # Eltrut+Chop (reverse Donchian when choppy)
        rev_break_up   = df["close"] < df["donch_low"]   # reverse: short when breaks up
        rev_break_down = df["close"] > df["donch_high"]  # reverse: long when breaks down
        L = rev_break_down & df["ranging"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = rev_break_up & df["ranging"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 51:  # BB+CMF
        L = (df["close"] < df["bb_lower"]) & df["cmf_cross_up"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = (df["close"] > df["bb_upper"]) & df["cmf_cross_down"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 52:  # Support+RSI_div
        L = df["near_support"] & df["rsi_bull_div"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["near_resistance"] & df["rsi_bear_div"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 53:  # Fib+RSI+trend
        L = df["near_fib618_bull"] & (df["rsi14"] < 40) & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["near_fib382_bull"] & (df["rsi14"] > 60) & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 54:  # Fib+Vol
        L = df["near_fib618_bull"] & df["vol_1_5x"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["near_fib382_bull"] & df["vol_1_5x"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 55:  # ZScore+OBV_div (z-score low + OBV rising = bullish div)
        L = df["zscore_low"] & df["obv_rising"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["zscore_high"] & df["obv_falling"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    # ---- D. Volume-Based (56-65) ----

    elif sid == 56:  # OBV+EMA
        L = df["obv_break"] & df["ema_cross_up"] & h
        S = df["obv_break_down"] & df["ema_cross_down"] & h

    elif sid == 57:  # OBV+RSI
        L = df["obv_break"] & df["rsi_bull"] & df["ema50_bull"] & h
        S = df["obv_break_down"] & df["rsi_bear"] & df["ema50_bear"] & h

    elif sid == 58:  # CMF+EMA
        L = df["cmf_cross_up"] & df["ema_bull"] & df["ema50_bull"] & h
        S = df["cmf_cross_down"] & df["ema_bear"] & df["ema50_bear"] & h

    elif sid == 59:  # CMF+ADX
        L = df["cmf_cross_up"] & df["adx20"] & df["ema50_bull"] & h
        S = df["cmf_cross_down"] & df["adx20"] & df["ema50_bear"] & h

    elif sid == 60:  # VolSpike+EMA
        L = df["vol_3x"] & df["ema_bull"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["vol_3x"] & df["ema_bear"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 61:  # VolSpike+ST
        L = df["vol_3x"] & df["st_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["vol_3x"] & df["st_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 62:  # VolSpike+MACD
        L = df["vol_3x"] & df["macd_bull"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["vol_3x"] & df["macd_bear"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 63:  # OBV_div+breakout (OBV rising while price breaks out)
        L = df["obv_rising"] & df["donch_break_up"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["obv_falling"] & df["donch_break_down"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 64:  # ForceIdx+EMA
        L = df["force_cross_up"] & df["ema_bull"] & df["ema50_bull"] & h
        S = df["force_cross_down"] & df["ema_bear"] & df["ema50_bear"] & h

    elif sid == 65:  # MFI+trend
        L = df["mfi_oversold"] & df["ema50_bull"] & df["ema_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["mfi_overbought"] & df["ema50_bear"] & df["ema_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    # ---- E. Multi-Confluence (66-80) ----

    elif sid == 66:  # Triple_trend: EMA+RSI+MACD all aligned
        L = df["ema_bull"] & df["rsi_bull"] & df["macd_bull"] & df["ema_cross_up"] & df["ema50_bull"] & h
        S = df["ema_bear"] & df["rsi_bear"] & df["macd_bear"] & df["ema_cross_down"] & df["ema50_bear"] & h

    elif sid == 67:  # Triple_strong: EMA+RSI+ADX all aligned
        L = df["ema_cross_up"] & df["rsi_bull"] & df["adx25"] & df["di_bull"] & df["ema50_bull"] & h
        S = df["ema_cross_down"] & df["rsi_bear"] & df["adx25"] & df["di_bear"] & df["ema50_bear"] & h

    elif sid == 68:  # Triple_ichi: EMA+Ichi+RSI
        L = df["ema_cross_up"] & df["above_cloud"] & df["rsi_bull"] & h
        S = df["ema_cross_down"] & df["below_cloud"] & df["rsi_bear"] & h

    elif sid == 69:  # Triple_ST_ichi: ST+Ichi+RSI
        L = df["st_flip_up"] & df["above_cloud"] & df["rsi_bull"] & h
        S = df["st_flip_down"] & df["below_cloud"] & df["rsi_bear"] & h

    elif sid == 70:  # Quad_confirm: 4 of 5 — EMA/RSI/MACD/ADX/Vol
        score_bull = (
            df["ema_bull"].astype(int) +
            df["rsi_bull"].astype(int) +
            df["macd_bull"].astype(int) +
            df["adx25"].astype(int) +
            df["vol_1_5x"].astype(int)
        )
        score_bear = (
            df["ema_bear"].astype(int) +
            df["rsi_bear"].astype(int) +
            df["macd_bear"].astype(int) +
            df["adx25"].astype(int) +
            df["vol_1_5x"].astype(int)
        )
        cross_L = df["ema_cross_up"] | df["macd_cross_up"]
        cross_S = df["ema_cross_down"] | df["macd_cross_down"]
        L = (score_bull >= 4) & cross_L & df["ema50_bull"] & h
        S = (score_bear >= 4) & cross_S & df["ema50_bear"] & h

    elif sid == 71:  # Penta_confirm: 5 of 7 — EMA/RSI/MACD/ADX/Vol/AO/Stoch
        score_bull = (
            df["ema_bull"].astype(int) +
            df["rsi_bull"].astype(int) +
            df["macd_bull"].astype(int) +
            df["adx20"].astype(int) +
            df["vol_1_5x"].astype(int) +
            df["ao_bull"].astype(int) +
            df["stoch_bull"].astype(int)
        )
        score_bear = (
            df["ema_bear"].astype(int) +
            df["rsi_bear"].astype(int) +
            df["macd_bear"].astype(int) +
            df["adx20"].astype(int) +
            df["vol_1_5x"].astype(int) +
            (~df["ao_bull"]).astype(int) +
            (~df["stoch_bull"]).astype(int)
        )
        cross_L = df["ema_cross_up"] | df["macd_cross_up"] | df["stoch_cross_up"]
        cross_S = df["ema_cross_down"] | df["macd_cross_down"] | df["stoch_cross_down"]
        L = (score_bull >= 5) & cross_L & df["ema50_bull"] & h
        S = (score_bear >= 5) & cross_S & df["ema50_bear"] & h

    elif sid == 72:  # Ribbon+RSI+Vol
        L = df["ribbon_bull"] & df["rsi_bull"] & df["vol_1_5x"] & df["ema_cross_up"] & h
        S = df["ribbon_bear"] & df["rsi_bear"] & df["vol_1_5x"] & df["ema_cross_down"] & h

    elif sid == 73:  # DualST+Ichi: both supertrends bull + above cloud
        L = df["st_bull"] & df["st2_bull"] & df["above_cloud"] & df["st_flip_up"] & h
        S = df["st_bear"] & df["st2_bear"] & df["below_cloud"] & df["st_flip_down"] & h

    elif sid == 74:  # Alligator+RSI+Vol
        L = df["alligator_bull"] & df["rsi_bull"] & df["vol_1_5x"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["alligator_bear"] & df["rsi_bear"] & df["vol_1_5x"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 75:  # Osc_triple: RSI+Stoch+AO all bullish
        L = df["rsi_bull"] & df["stoch_bull"] & df["ao_bull"] & df["ema_cross_up"] & df["ema50_bull"] & h
        S = df["rsi_bear"] & ~df["stoch_bull"] & df["ao_bear"] & df["ema_cross_down"] & df["ema50_bear"] & h

    elif sid == 76:  # Quad_trend: EMA+cloud+AO+Vol
        L = df["ema_cross_up"] & df["above_cloud"] & df["ao_bull"] & df["vol_1_5x"] & h
        S = df["ema_cross_down"] & df["below_cloud"] & df["ao_bear"] & df["vol_1_5x"] & h

    elif sid == 77:  # Quad_ST: ST+ADX+RSI+Vol
        L = df["st_flip_up"] & df["adx25"] & df["rsi_bull"] & df["vol_1_5x"] & h
        S = df["st_flip_down"] & df["adx25"] & df["rsi_bear"] & df["vol_1_5x"] & h

    elif sid == 78:  # Multi_system: Ichi+MACD+Stoch (3 systems agree)
        L = df["tk_cross_up"] & df["macd_bull"] & df["stoch_cross_up"] & df["above_cloud"] & h
        S = df["tk_cross_down"] & df["macd_bear"] & df["stoch_cross_down"] & df["below_cloud"] & h

    elif sid == 79:  # All_osc: RSI+Stoch+AO+CCI all bullish/bearish
        L = df["rsi_bull"] & df["stoch_bull"] & df["ao_bull"] & df["cci_bull"] & df["ema_cross_up"] & h
        S = df["rsi_bear"] & ~df["stoch_bull"] & df["ao_bear"] & df["cci_bear"] & df["ema_cross_down"] & h

    elif sid == 80:  # All_MA_above: price > all 5 EMAs
        L = df["all_ma_bull"] & df["ema_cross_up"] & h
        S = df["all_ma_bear"] & df["ema_cross_down"] & h

    # ---- F. Pattern + Indicator (81-90) ----

    elif sid == 81:  # Hammer+RSI+trend
        L = df["hammer"] & (df["rsi14"] < 35) & h
        L = L & ~L.shift(1).fillna(False)
        S = df["shooting_star"] & (df["rsi14"] > 65) & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 82:  # ShootStar+RSI+trend (focused short signal)
        L = df["pin_bull"] & (df["rsi14"] < 35) & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["pin_bear"] & (df["rsi14"] > 65) & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 83:  # Engulf+Vol+trend
        L = df["engulf_bull"] & df["vol_1_5x"] & df["ema50_bull"] & h
        S = df["engulf_bear"] & df["vol_1_5x"] & df["ema50_bear"] & h

    elif sid == 84:  # HA_rev+RSI
        L = df["ha_rev_up"] & (df["rsi14"] < 45) & df["ema50_bull"] & h
        S = df["ha_rev_down"] & (df["rsi14"] > 55) & df["ema50_bear"] & h

    elif sid == 85:  # InsideBar+ADX
        L = df["inside_bar_break_up"] & df["adx25"] & df["ema50_bull"] & h
        S = df["inside_bar_break_down"] & df["adx25"] & df["ema50_bear"] & h

    elif sid == 86:  # 3Green+Vol+EMA
        L = df["three_green"] & df["vol_increasing"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["three_red"] & df["vol_increasing"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 87:  # Doji+squeeze+ADX (volatility contraction then expansion)
        L = (
            df["doji"].shift(1).fillna(False) &
            df["bb_squeeze"].shift(1).fillna(False) &
            (df["adx"].shift(1).fillna(0) < 20) &
            df["adx25"] &
            df["ema50_bull"] & h
        )
        S = (
            df["doji"].shift(1).fillna(False) &
            df["bb_squeeze"].shift(1).fillna(False) &
            (df["adx"].shift(1).fillna(0) < 20) &
            df["adx25"] &
            df["ema50_bear"] & h
        )
        # Direction determined by candle direction after squeeze break
        L = L & df["candle_bull"]
        S = S & df["candle_bear"]

    elif sid == 88:  # BigCandle+Vol+EMA
        L = df["big_candle"] & df["candle_bull"] & df["vol_2x"] & df["ema50_bull"] & h
        S = df["big_candle"] & df["candle_bear"] & df["vol_2x"] & df["ema50_bear"] & h

    elif sid == 89:  # PinBar+Fib+RSI
        L = df["pin_bull"] & df["near_fib618_bull"] & (df["rsi14"] < 45) & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["pin_bear"] & df["near_fib382_bull"] & (df["rsi14"] > 55) & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 90:  # Swing+Vol+RSI
        L = df["swing_break_up"] & df["vol_1_5x"] & (df["rsi14"] > 50) & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["swing_break_down"] & df["vol_1_5x"] & (df["rsi14"] < 50) & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    # ---- G. Exotic / Adaptive (91-100) ----

    elif sid == 91:  # Kalman+ADX
        L = df["kalman_cross_up"] & df["adx25"] & df["di_bull"] & df["ema50_bull"] & h
        S = df["kalman_cross_down"] & df["adx25"] & df["di_bear"] & df["ema50_bear"] & h

    elif sid == 92:  # Kalman+RSI
        L = df["kalman_cross_up"] & df["rsi_bull"] & df["ema50_bull"] & h
        S = df["kalman_cross_down"] & df["rsi_bear"] & df["ema50_bear"] & h

    elif sid == 93:  # Renko+EMA
        L = df["renko_bull"] & df["ema_bull"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["renko_bear"] & df["ema_bear"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 94:  # Renko+Vol
        L = df["renko_bull"] & df["vol_1_5x"] & df["ema50_bull"] & h
        L = L & ~L.shift(1).fillna(False)
        S = df["renko_bear"] & df["vol_1_5x"] & df["ema50_bear"] & h
        S = S & ~S.shift(1).fillna(False)

    elif sid == 95:  # Pivot+Vol+ADX
        L = df["pivot_break_up"] & df["vol_1_5x"] & df["adx25"] & h
        S = df["pivot_break_down"] & df["vol_1_5x"] & df["adx25"] & h

    elif sid == 96:  # StochMTF+ZScore
        L = df["stoch_4h_bull"] & df["stoch_cross_up"] & df["zscore_neg1"] & df["ema50_bull"] & h
        S = df["stoch_4h_bear"] & df["stoch_cross_down"] & df["zscore_high"] & df["ema50_bear"] & h

    elif sid == 97:  # Ribbon+AO
        L = df["ribbon_bull"] & df["ao_bull"] & df["ema_cross_up"] & h
        S = df["ribbon_bear"] & df["ao_bear"] & df["ema_cross_down"] & h

    elif sid == 98:  # Chop+ATR+EMA (choppiness drops = trending, + ATR rising + EMA)
        chop_cross_low = (df["choppiness"] < 38.2) & (df["choppiness"].shift(1) >= 38.2)
        L = chop_cross_low & df["atr_rising"] & df["ema_bull"] & df["ema50_bull"] & h
        S = chop_cross_low & df["atr_rising"] & df["ema_bear"] & df["ema50_bear"] & h

    elif sid == 99:  # MTF_EMA: 1H + 4H EMA both bullish on EMA cross
        L = df["ema_cross_up"] & df["ema_4h_bull"] & df["ema50_bull"] & h
        S = df["ema_cross_down"] & df["ema_4h_bear"] & df["ema50_bear"] & h

    elif sid == 100:  # Regime+Vol: ranging→trending transition + volume surge
        L = df["regime_trans_bull"] & df["vol_2x"] & h
        S = df["regime_trans_bear"] & df["vol_2x"] & h

    else:
        empty = pd.Series(False, index=df.index)
        return empty, empty

    # Shift by 1: signal on closed candle, enter on next open candle
    return L.shift(1).fillna(False), S.shift(1).fillna(False)


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    long_sig: pd.Series,
    short_sig: pd.Series,
    sl_mult: float = SL_MULT,
    tp_mult: float = TP_MULT,
) -> dict:
    """Event-driven P&L simulator.

    One position at a time. ATR-based SL and TP. Commission + slippage included.

    Args:
        df: DataFrame with 'atr', 'close', 'high', 'low' columns.
        long_sig: Boolean series (True = open long on this bar).
        short_sig: Boolean series (True = open short on this bar).
        sl_mult: SL distance as ATR multiplier.
        tp_mult: TP distance as ATR multiplier.

    Returns:
        Dict with trades, tr_yr, wr, pf, dd, sharpe.
    """
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades: list = []
    position = None

    lsig = long_sig.values
    ssig = short_sig.values
    atr_v = df["atr"].values
    close_v = df["close"].values
    high_v = df["high"].values
    low_v = df["low"].values

    for i in range(2, len(df)):
        # Manage open position
        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]

            hit_sl = (side == 1 and low_v[i] <= sl) or (side == -1 and high_v[i] >= sl)
            hit_tp = (side == 1 and high_v[i] >= tp) or (side == -1 and low_v[i] <= tp)

            if hit_sl or hit_tp:
                exit_p = sl if hit_sl else tp
                pnl_pct = side * (exit_p - entry) / entry - (COMMISSION + SLIPPAGE) * 2
                pnl = balance * RISK_PCT * LEVERAGE * pnl_pct / sl_mult
                pnl = max(pnl, -balance * RISK_PCT * LEVERAGE)
                balance += pnl
                if balance <= 0:
                    break
                peak = max(peak, balance)
                dd = (peak - balance) / peak
                max_dd = max(max_dd, dd)
                trades.append(pnl)
                position = None

        # Enter new position
        if position is None:
            is_long  = bool(lsig[i - 1])
            is_short = bool(ssig[i - 1])
            if not (is_long or is_short):
                continue
            atr_val = atr_v[i - 1]
            if not np.isfinite(atr_val) or atr_val <= 0:
                continue
            entry_p = close_v[i]
            side = 1 if is_long else -1
            sl_d = atr_val * sl_mult
            tp_d = atr_val * tp_mult
            position = {
                "side": side,
                "entry": entry_p,
                "sl": entry_p - sl_d * side,
                "tp": entry_p + tp_d * side,
            }

    total = len(trades)
    if total == 0:
        return {"trades": 0, "tr_yr": 0, "wr": 0.0, "pf": 0.0, "dd": 0.0, "sharpe": 0.0}

    wins = sum(1 for p in trades if p > 0)
    gp = sum(p for p in trades if p > 0)
    gl = sum(abs(p) for p in trades if p < 0)
    pf = gp / gl if gl > 0 else 0.0
    wr = wins / total * 100.0

    days = (df.index[-1] - df.index[0]).days
    yr = max(days / 365.25, 0.01)
    tr_yr = total / yr

    # Simplified Sharpe
    if total >= 2:
        arr = np.array(trades) / 1000.0
        std = float(np.std(arr))
        ann_ret = (balance - 1000.0) / 1000.0
        sharpe = ann_ret / (std * (tr_yr ** 0.5)) if std > 0 else 0.0
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
# Coin discovery + data loading
# ---------------------------------------------------------------------------

# Deployed coins — include them too, since we're testing NEW strategy combinations
# (not the same strategies already deployed)
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


def discover_coins(coins_arg: Optional[List[str]], include_deployed: bool = False) -> List[str]:
    """Return list of coin prefixes (lowercase) with 1H 2yr data.

    Args:
        coins_arg: Optional explicit list of coin prefixes.
        include_deployed: If True, include already-deployed coins too.

    Returns:
        Sorted list of coin prefixes.
    """
    if coins_arg:
        return [c.lower().strip() for c in coins_arg if c.strip()]

    pattern = os.path.join(DATA_DIR, "*_1h_2y.csv")
    files = sorted(glob.glob(pattern))
    prefixes = []
    for f in files:
        base = os.path.basename(f)
        prefix = base.replace("_1h_2y.csv", "")
        if include_deployed or prefix not in DEPLOYED_PREFIXES:
            prefixes.append(prefix)
    return prefixes


def _load_coin(prefix: str) -> Optional[pd.DataFrame]:
    """Load and enrich 1H OHLCV data for a coin prefix.

    Args:
        prefix: Coin symbol prefix e.g. 'ethusdt'.

    Returns:
        Indicator-enriched DataFrame, or None if loading fails.
    """
    for suffix in ["2y", "5y"]:
        fpath = os.path.join(DATA_DIR, f"{prefix}_1h_{suffix}.csv")
        if os.path.exists(fpath):
            try:
                raw = load_ohlcv(fpath)
                return compute_all_indicators(raw)
            except Exception as e:
                print(f"  WARN: {prefix} load error: {e}", flush=True)
    return None


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(
    coins: List[str],
    strategy_ids: Optional[List[int]],
    min_pf: float,
    min_trades: int,
    max_dd: float,
) -> List[dict]:
    """Run the mega100 sweep across all coins and strategies.

    Computes all indicators once per coin, then evaluates all 100 strategy
    signal functions. Uses numpy-vectorized boolean operations for speed.

    Args:
        coins: List of coin prefixes.
        strategy_ids: Specific strategy IDs to test (None = all 1-100).
        min_pf: Minimum profit factor to count as a winner.
        min_trades: Minimum trade count to count as a winner.
        max_dd: Maximum drawdown % allowed.

    Returns:
        Sorted list of winner dicts.
    """
    sids = strategy_ids if strategy_ids else list(range(1, 101))
    n_strats = len(sids)

    print(f"\nMEGA100 SWEEP", flush=True)
    print(f"Coins: {len(coins)}  |  Strategies: {n_strats}  |  "
          f"Tests: {len(coins) * n_strats}", flush=True)
    print(f"Filter: PF >= {min_pf}, trades >= {min_trades}, DD <= {max_dd}%", flush=True)
    print(f"SL={SL_MULT}×ATR, TP={TP_MULT}×ATR, Hours=3-20 UTC, No weekends\n", flush=True)

    all_results: List[dict] = []
    t0 = time.time()

    for coin_idx, prefix in enumerate(coins):
        coin_name = prefix.replace("usdt", "").upper()
        t_coin = time.time()

        # Load + compute all indicators once
        df = _load_coin(prefix)
        if df is None:
            print(f"[{coin_idx+1}/{len(coins)}] {coin_name}: SKIP (no data)", flush=True)
            continue

        # Check data length
        days = (df.index[-1] - df.index[0]).days
        if days < 180:
            print(f"[{coin_idx+1}/{len(coins)}] {coin_name}: SKIP (<6mo data, {days}d)", flush=True)
            continue

        coin_winners = 0
        coin_results = []

        for sid in sids:
            try:
                long_sig, short_sig = get_signals(df, sid)
            except Exception as e:
                # Skip individual strategy errors silently
                _ = e
                continue

            n_long = int(long_sig.sum())
            n_short = int(short_sig.sum())
            if n_long + n_short == 0:
                continue

            try:
                r = simulate(df, long_sig, short_sig)
            except Exception as e:
                _ = e
                continue

            if r["trades"] < 2:
                continue

            meta_name, meta_cat = STRATEGY_META.get(sid, (f"S{sid}", "?"))
            result = {
                "coin": coin_name,
                "prefix": prefix,
                "strategy_id": sid,
                "strategy": meta_name,
                "category": meta_cat,
                "pf": r["pf"],
                "trades": r["trades"],
                "tr_yr": r["tr_yr"],
                "wr_pct": r["wr"],
                "dd_pct": r["dd"],
                "sharpe": r["sharpe"],
                "days_data": days,
            }
            coin_results.append(result)

            is_winner = (
                r["pf"] >= min_pf and
                r["trades"] >= min_trades and
                r["dd"] <= max_dd
            )
            if is_winner:
                coin_winners += 1
                marker = " ***"
            else:
                marker = ""

            if r["trades"] >= min_trades // 2:
                print(
                    f"  S{sid:3d} {meta_name:<22} {r['pf']:>5.2f} PF"
                    f"  {r['wr']:>4.0f}%WR  {r['trades']:>4}tr  Sh{r['sharpe']:>5.2f}"
                    f"  DD{r['dd']:>4.0f}%{marker}",
                    flush=True,
                )

        all_results.extend(coin_results)
        elapsed = time.time() - t_coin
        total_elapsed = time.time() - t0
        print(
            f"[{coin_idx+1}/{len(coins)}] {coin_name}: "
            f"{coin_winners} winners / {len(coin_results)} results  "
            f"({elapsed:.1f}s coin, {total_elapsed:.0f}s total)",
            flush=True,
        )

    # Filter and sort
    winners = [r for r in all_results if (
        r["pf"] >= min_pf and
        r["trades"] >= min_trades and
        r["dd_pct"] <= max_dd
    )]
    winners.sort(key=lambda x: -x["pf"])
    return winners, all_results


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_summary(
    winners: List[dict],
    all_results: List[dict],
    min_pf: float,
    min_trades: int,
) -> None:
    """Print ranked summary: strategy rankings, top combos, new coins.

    Args:
        winners: Filtered winner list (sorted by PF).
        all_results: All result records (for strategy ranking).
        min_pf: PF threshold used.
        min_trades: Trade count threshold used.
    """
    print(f"\n{'=' * 90}", flush=True)
    print(f"MEGA100 SWEEP COMPLETE — {len(winners)} winners (PF>={min_pf}, tr>={min_trades})", flush=True)
    print("=" * 90, flush=True)

    if not winners:
        print("No winners found.", flush=True)
        return

    # 1. Strategy ranking (top 20 by avg PF across all coins)
    print("\n--- 1. Strategy Ranking (top 20 by avg PF, min 3 coins) ---", flush=True)
    strat_pf: Dict[int, List[float]] = {}
    for r in all_results:
        sid = r["strategy_id"]
        if r["pf"] > 0 and r["trades"] >= min_trades:
            strat_pf.setdefault(sid, []).append(r["pf"])

    strat_avg = {
        sid: (sum(pfs) / len(pfs), len(pfs))
        for sid, pfs in strat_pf.items()
        if len(pfs) >= 3
    }
    top_strats = sorted(strat_avg.items(), key=lambda x: -x[1][0])[:20]

    print(f"  {'ID':>4} {'Name':<24} {'AvgPF':>6} {'Coins':>6} {'Category'}", flush=True)
    print("  " + "-" * 52, flush=True)
    for sid, (avg_pf, n_coins) in top_strats:
        name, cat = STRATEGY_META.get(sid, (f"S{sid}", "?"))
        print(f"  S{sid:>3d} {name:<24} {avg_pf:>6.2f} {n_coins:>6}  {cat}", flush=True)

    # 2. Top 30 coin×strategy combos
    print("\n--- 2. Top 30 Coin×Strategy Combos ---", flush=True)
    hdr = f"  {'Coin':<10} {'Strategy':<24} {'PF':>6} {'WR%':>5} {'Tr':>5} {'Tr/yr':>6} {'Sh':>6} {'DD%':>5}"
    print(hdr, flush=True)
    print("  " + "-" * (len(hdr) - 2), flush=True)
    for r in winners[:30]:
        print(
            f"  {r['coin']:<10} {r['strategy']:<24} {r['pf']:>6.2f} "
            f"{r['wr_pct']:>4.0f}% {r['trades']:>5} {r['tr_yr']:>5.0f}/yr "
            f"{r['sharpe']:>6.2f} {r['dd_pct']:>4.0f}%",
            flush=True,
        )

    # 3. New coins not yet deployed
    print("\n--- 3. New Coins (not currently deployed) ---", flush=True)
    new_coin_wins: Dict[str, List[dict]] = {}
    for r in winners:
        if r["prefix"] not in DEPLOYED_PREFIXES:
            new_coin_wins.setdefault(r["coin"], []).append(r)

    if not new_coin_wins:
        print("  (no new coins found in winners)", flush=True)
    else:
        for coin in sorted(new_coin_wins, key=lambda c: -max(r["pf"] for r in new_coin_wins[c])):
            best = max(new_coin_wins[coin], key=lambda r: r["pf"])
            print(
                f"  {coin:<10} best: S{best['strategy_id']:>3d} {best['strategy']:<24}"
                f" PF={best['pf']:.2f}  WR={best['wr_pct']:.0f}%"
                f"  {best['trades']}tr  DD={best['dd_pct']:.0f}%",
                flush=True,
            )

    # Summary stats
    print(f"\nTotal winners: {len(winners)}", flush=True)
    cats = {}
    for r in winners:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print("Winners by category:", flush=True)
    for cat in sorted(cats):
        cat_names = {
            "A": "Trend+Momentum", "B": "Trend+Volatility", "C": "Mean Reversion",
            "D": "Volume", "E": "Multi-Confluence", "F": "Pattern+Indicator", "G": "Exotic",
        }
        print(f"  {cat} {cat_names.get(cat,'?'):<20} {cats[cat]}", flush=True)


def _save_results(winners: List[dict], all_results: List[dict]) -> None:
    """Save winners and full results to JSON.

    Args:
        winners: Filtered winner list.
        all_results: Complete result list.
    """
    output = {
        "winners": winners,
        "all_results": all_results,
        "meta": {
            "sl_mult": SL_MULT,
            "tp_mult": TP_MULT,
            "hours": f"{HOURS_START}-{HOURS_END} UTC",
            "commission": COMMISSION,
            "slippage": SLIPPAGE,
            "strategies_tested": len(STRATEGY_META),
        },
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {OUTPUT_FILE}", flush=True)
    print(f"  {len(winners)} winners / {len(all_results)} total results", flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="sweep_mega100: 100-strategy combination sweep on 1H data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help="Comma-separated coin prefixes (default: all *_1h_2y.csv files)",
    )
    parser.add_argument(
        "--strategies",
        type=str,
        default=None,
        help="Comma-separated strategy IDs to test e.g. 1,2,3 (default: all 1-100)",
    )
    parser.add_argument(
        "--min-pf",
        type=float,
        default=1.3,
        help="Minimum profit factor for winners (default: 1.3)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=10,
        help="Minimum trade count for winners (default: 10)",
    )
    parser.add_argument(
        "--max-dd",
        type=float,
        default=30.0,
        help="Maximum drawdown %% for winners (default: 30.0)",
    )
    parser.add_argument(
        "--include-deployed",
        action="store_true",
        default=False,
        help="Include already-deployed coins in sweep (default: new coins only)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    coins_arg = [c.strip() for c in args.coins.split(",")] if args.coins else None
    strat_arg = [int(s.strip()) for s in args.strategies.split(",")] if args.strategies else None

    coins = discover_coins(coins_arg, include_deployed=args.include_deployed)
    if not coins:
        print("No coins found. Add *_1h_2y.csv files to data/ or pass --coins.", flush=True)
        sys.exit(1)

    print(f"Coins to sweep: {len(coins)}", flush=True)
    print(f"First 10: {', '.join(coins[:10])}{'...' if len(coins) > 10 else ''}", flush=True)

    winners, all_results = run_sweep(
        coins=coins,
        strategy_ids=strat_arg,
        min_pf=args.min_pf,
        min_trades=args.min_trades,
        max_dd=args.max_dd,
    )

    _save_results(winners, all_results)
    _print_summary(winners, all_results, min_pf=args.min_pf, min_trades=args.min_trades)
