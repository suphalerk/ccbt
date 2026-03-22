"""sweep_improve_weak.py — Fast parameter optimization sweep for 30 weak/negative-PnL coins.

Uses the lightweight vectorized simulator (same approach as sweep_round11.py) for speed.
Tests 10 base strategies × 10 param sets × 2 timeframes = 200 combos per coin.
Total: 30 coins × 200 combos = ~6,000 backtests.

Strategies tested:
  1  dual_thrust         — Breakout beyond N-bar range
  2  ichimoku_cloud      — Tenkan/Kijun cross above/below cloud
  3  awesome_oscillator  — AO zero-line cross + saucer
  4  range_bounce        — Mean reversion from Bollinger Bands
  5  ema_ribbon          — 6-EMA alignment signal
  6  dualthrust_adx      — DualThrust + ADX confirmation
  7  zscore_meanrev      — Z-score extreme + EMA50 filter
  8  stoch_mtf           — Stochastic K/D cross + EMA50 filter
  9  ichi_adx            — Ichimoku cloud + ADX strength gate
  10 supertrend          — ATR adaptive trend bands direction flip

Output: data/sweep_improve_weak.json + printed improvement table.

Usage:
    python3 -u research/sweep_improve_weak.py
"""

from __future__ import annotations

import json
import logging
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)
logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.data import compute_atr, compute_ema, compute_rsi

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_DIR = Path("/Users/iceai/Work/ccbt/data")
OUTPUT_FILE = DATA_DIR / "sweep_improve_weak.json"

COMMISSION = 0.00055   # 0.055% taker fee
SLIPPAGE = 0.00015     # 0.015% slippage
LEVERAGE = 25
RISK_PCT = 0.01        # 1% risk per trade
HOURS_START = 3
HOURS_END = 20
WARMUP_BARS = 120

WEAK_COINS = [
    "GALA", "ICP", "UNI", "DOT", "SUI", "PENGU", "ZEC", "ZEN", "ZRO",
    "VVV", "AXS", "XAI", "QNT", "FARTCOIN", "ALICE", "ATOM", "ENA",
    "TON", "KAS", "ADA", "ANIME", "AVAX", "POL", "DASH",
    "PIXEL", "CFX", "CRV", "BCH", "SAND", "AAVE",
]

# Standard SL/TP/Trail combinations — (sl, tp, trail, label)
# tp=0 means trailing stop only
PARAM_SETS: List[Tuple[float, float, float, str]] = [
    (1.0, 2.0, 2.0, "tight"),
    (1.0, 3.0, 2.0, "tight3"),
    (1.5, 3.0, 2.5, "med3"),
    (1.5, 4.0, 3.0, "med4"),
    (2.0, 3.0, 2.5, "wide3"),
    (2.0, 4.0, 3.0, "wide4"),
    (2.0, 5.0, 3.0, "wide5"),
    (2.5, 4.0, 3.5, "vwide4"),
    (2.5, 5.0, 4.0, "vwide5"),
    (3.0, 0.0, 4.0, "trail_only"),
]

# Mean-reversion strategies get tighter params
MR_PARAM_SETS: List[Tuple[float, float, float, str]] = [
    (0.5, 1.0, 1.0, "mr_tight"),
    (0.7, 1.5, 1.5, "mr_med"),
    (1.0, 1.5, 1.5, "mr_wide"),
    (1.0, 2.0, 2.0, "mr_wide2"),
    (1.5, 2.0, 2.0, "mr_vwide"),
    (1.5, 2.5, 2.5, "mr_vwide25"),
    (1.0, 0.0, 2.0, "mr_trail"),
    (0.7, 1.0, 1.0, "mr_scalp"),
    (1.0, 3.0, 2.0, "mr_catch"),
    (2.0, 3.0, 2.5, "mr_trend"),
]

MR_STRATEGIES = {"range_bounce", "zscore_meanrev"}

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def load_csv(path: Path) -> pd.DataFrame:
    """Load OHLCV CSV with DatetimeIndex. Returns empty DataFrame if missing."""
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.columns = [c.lower() for c in df.columns]
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return pd.DataFrame()
    return df[["open", "high", "low", "close", "volume"]].copy()


def resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV to 4H."""
    if df.empty:
        return df
    return (
        df.resample("4h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def compute_base(df: pd.DataFrame) -> pd.DataFrame:
    """Compute shared base indicators."""
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
    df["atr_ma20"] = df["atr"].rolling(20).mean()

    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek
    else:
        df["hour"] = 12
        df["dow"] = 0

    return df


def _in_window(df: pd.DataFrame) -> pd.Series:
    """Boolean mask: valid trading hours (3-20 UTC, Mon-Fri)."""
    return (
        (df["hour"] >= HOURS_START) & (df["hour"] < HOURS_END) & (df["dow"] < 5)
    )


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------


def _compute_supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 10, multiplier: float = 3.0,
) -> Tuple[pd.Series, pd.Series]:
    """Supertrend: returns (values, direction) where direction=1 long, -1 short."""
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


def _compute_ichimoku(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Ichimoku: tenkan, kijun, cloud_top, cloud_bottom."""
    tenkan = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    kijun = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)
    return tenkan, kijun, cloud_top, cloud_bottom


def _compute_adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """ADX, +DI, -DI with Wilder's smoothing."""
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


def _compute_stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series,
    k_period: int = 14, d_smooth: int = 3,
) -> Tuple[pd.Series, pd.Series]:
    """Stochastic %K and %D."""
    hh = high.rolling(k_period).max()
    ll = low.rolling(k_period).min()
    k = 100.0 * (close - ll) / (hh - ll).replace(0, 1e-10)
    d = k.rolling(d_smooth).mean()
    return k, d


# ---------------------------------------------------------------------------
# Strategy signal generators (return df with 'signal' column)
# ---------------------------------------------------------------------------


def sig_dual_thrust(df: pd.DataFrame) -> pd.DataFrame:
    """Dual Thrust: breakout of N-bar high/low range."""
    df = df.copy()
    lookback = 4
    hh = df["high"].rolling(lookback).max().shift(1)
    ll = df["low"].rolling(lookback).min().shift(1)
    cl = df["close"].shift(1)
    op = df["open"].shift(1)
    k = 0.5

    buy_range = pd.concat([hh - cl, cl - ll], axis=1).max(axis=1)
    sell_range = pd.concat([hh - op, op - ll], axis=1).max(axis=1)

    buy_trigger = df["open"] + k * buy_range
    sell_trigger = df["open"] - k * sell_range

    window = _in_window(df)
    long_filter = df["close"].shift(1) > df["ema50"].shift(1)
    short_filter = df["close"].shift(1) < df["ema50"].shift(1)

    signal = pd.Series(0, index=df.index)
    signal[(df["close"] > buy_trigger) & long_filter & window] = 1
    signal[(df["close"] < sell_trigger) & short_filter & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def sig_ichimoku_cloud(df: pd.DataFrame) -> pd.DataFrame:
    """Ichimoku: Tenkan/Kijun cross above/below cloud."""
    df = df.copy()
    tenkan, kijun, cloud_top, cloud_bottom = _compute_ichimoku(df)

    tenkan_above = (tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1))
    tenkan_below = (tenkan < kijun) & (tenkan.shift(1) >= kijun.shift(1))

    price_above_cloud = df["close"] > cloud_top
    price_below_cloud = df["close"] < cloud_bottom

    window = _in_window(df)
    signal = pd.Series(0, index=df.index)
    signal[tenkan_above & price_above_cloud & window] = 1
    signal[tenkan_below & price_below_cloud & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def sig_awesome_oscillator(df: pd.DataFrame) -> pd.DataFrame:
    """Awesome Oscillator: AO histogram zero-line cross."""
    df = df.copy()
    midpoint = (df["high"] + df["low"]) / 2
    ao = midpoint.rolling(5).mean() - midpoint.rolling(34).mean()

    cross_up = (ao > 0) & (ao.shift(1) <= 0)
    cross_down = (ao < 0) & (ao.shift(1) >= 0)

    window = _in_window(df)
    long_filter = df["close"] > df["ema50"]
    short_filter = df["close"] < df["ema50"]

    signal = pd.Series(0, index=df.index)
    signal[cross_up & long_filter & window] = 1
    signal[cross_down & short_filter & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def sig_range_bounce(df: pd.DataFrame) -> pd.DataFrame:
    """Mean reversion: Bollinger Band touch + RSI extremes."""
    df = df.copy()
    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std

    touch_lower = df["low"] <= bb_lower
    touch_upper = df["high"] >= bb_upper
    oversold = df["rsi"] < 35
    overbought = df["rsi"] > 65

    window = _in_window(df)
    signal = pd.Series(0, index=df.index)
    signal[touch_lower & oversold & window] = 1
    signal[touch_upper & overbought & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def sig_ema_ribbon(df: pd.DataFrame) -> pd.DataFrame:
    """EMA Ribbon: 6-EMA alignment — all aligned in same direction."""
    df = df.copy()
    close = df["close"]
    e10 = compute_ema(close, 10)
    e20 = compute_ema(close, 20)
    e30 = compute_ema(close, 30)
    e40 = compute_ema(close, 40)
    e50 = compute_ema(close, 50)
    e60 = compute_ema(close, 60)

    bull = (e10 > e20) & (e20 > e30) & (e30 > e40) & (e40 > e50) & (e50 > e60)
    bear = (e10 < e20) & (e20 < e30) & (e30 < e40) & (e40 < e50) & (e50 < e60)

    # Edge detect: ribbon just aligned
    bull_cross = bull & ~bull.shift(1).fillna(False)
    bear_cross = bear & ~bear.shift(1).fillna(False)

    window = _in_window(df)
    signal = pd.Series(0, index=df.index)
    signal[bull_cross & window] = 1
    signal[bear_cross & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def sig_dualthrust_adx(df: pd.DataFrame) -> pd.DataFrame:
    """DualThrust + ADX >= 25 confirmation."""
    df = df.copy()
    lookback = 4
    hh = df["high"].rolling(lookback).max().shift(1)
    ll = df["low"].rolling(lookback).min().shift(1)
    cl = df["close"].shift(1)
    op = df["open"].shift(1)
    k = 0.5

    buy_range = pd.concat([hh - cl, cl - ll], axis=1).max(axis=1)
    sell_range = pd.concat([hh - op, op - ll], axis=1).max(axis=1)
    buy_trigger = df["open"] + k * buy_range
    sell_trigger = df["open"] - k * sell_range

    adx, plus_di, minus_di = _compute_adx(df)
    trending = adx >= 25

    window = _in_window(df)
    long_filter = (df["close"].shift(1) > df["ema50"].shift(1)) & (plus_di > minus_di)
    short_filter = (df["close"].shift(1) < df["ema50"].shift(1)) & (minus_di > plus_di)

    signal = pd.Series(0, index=df.index)
    signal[(df["close"] > buy_trigger) & long_filter & trending & window] = 1
    signal[(df["close"] < sell_trigger) & short_filter & trending & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def sig_zscore_meanrev(df: pd.DataFrame) -> pd.DataFrame:
    """Z-score mean reversion: price extreme vs 20-bar rolling mean."""
    df = df.copy()
    close = df["close"]
    mu = close.rolling(20).mean()
    sigma = close.rolling(20).std()
    zscore = (close - mu) / sigma.clip(lower=1e-10)

    # Enter when z-score crosses back from extreme
    long_entry = (zscore > -2.5) & (zscore.shift(1) <= -2.5) & (close > df["ema50"])
    short_entry = (zscore < 2.5) & (zscore.shift(1) >= 2.5) & (close < df["ema50"])

    window = _in_window(df)
    signal = pd.Series(0, index=df.index)
    signal[long_entry & window] = 1
    signal[short_entry & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def sig_stoch_mtf(df: pd.DataFrame) -> pd.DataFrame:
    """Stochastic K/D cross + EMA50 trend filter."""
    df = df.copy()
    k, d = _compute_stochastic(df["high"], df["low"], df["close"], k_period=14, d_smooth=3)

    # K crosses D from below (oversold zone)
    k_cross_up = (k > d) & (k.shift(1) <= d.shift(1)) & (k < 80)
    k_cross_down = (k < d) & (k.shift(1) >= d.shift(1)) & (k > 20)

    window = _in_window(df)
    long_filter = df["close"] > df["ema50"]
    short_filter = df["close"] < df["ema50"]

    signal = pd.Series(0, index=df.index)
    signal[k_cross_up & long_filter & window] = 1
    signal[k_cross_down & short_filter & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def sig_ichi_adx(df: pd.DataFrame) -> pd.DataFrame:
    """Ichimoku + ADX >= 20 strength gate."""
    df = df.copy()
    tenkan, kijun, cloud_top, cloud_bottom = _compute_ichimoku(df)
    adx, plus_di, minus_di = _compute_adx(df)

    tenkan_above = (tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1))
    tenkan_below = (tenkan < kijun) & (tenkan.shift(1) >= kijun.shift(1))

    price_above_cloud = df["close"] > cloud_top
    price_below_cloud = df["close"] < cloud_bottom
    trending = adx >= 20

    window = _in_window(df)
    signal = pd.Series(0, index=df.index)
    signal[tenkan_above & price_above_cloud & trending & window] = 1
    signal[tenkan_below & price_below_cloud & trending & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


def sig_supertrend(df: pd.DataFrame) -> pd.DataFrame:
    """Supertrend: direction flip = entry signal."""
    df = df.copy()
    _, direction = _compute_supertrend(df["high"], df["low"], df["close"], period=10, multiplier=3.0)

    dir_change_long = (direction == 1) & (direction.shift(1) == -1)
    dir_change_short = (direction == -1) & (direction.shift(1) == 1)

    window = _in_window(df)
    signal = pd.Series(0, index=df.index)
    signal[dir_change_long & window] = 1
    signal[dir_change_short & window] = -1
    signal.iloc[:WARMUP_BARS] = 0
    df["signal"] = signal
    return df


STRATEGY_FUNCS: Dict[str, callable] = {
    "dual_thrust":     sig_dual_thrust,
    "ichimoku_cloud":  sig_ichimoku_cloud,
    "awesome_osc":     sig_awesome_oscillator,
    "range_bounce":    sig_range_bounce,
    "ema_ribbon":      sig_ema_ribbon,
    "dualthrust_adx":  sig_dualthrust_adx,
    "zscore_meanrev":  sig_zscore_meanrev,
    "stoch_mtf":       sig_stoch_mtf,
    "ichi_adx":        sig_ichi_adx,
    "supertrend":      sig_supertrend,
}

# ---------------------------------------------------------------------------
# Lightweight simulator (same pattern as sweep_round11.py)
# ---------------------------------------------------------------------------


def simulate(
    df: pd.DataFrame,
    sl_mult: float = 2.0,
    tp_mult: float = 4.0,
    trail_mult: float = 0.0,
) -> Dict:
    """Simulate trades from the 'signal' column.

    Signal at row i-1 (closed candle) triggers entry at row i close.
    Exits on SL/TP hit within the bar. Trailing stop ratchets in profit direction.

    Args:
        df: DataFrame with 'signal', 'atr', 'high', 'low', 'close', 'dow' columns.
        sl_mult: Stop loss ATR multiplier.
        tp_mult: Take profit ATR multiplier (0 = trailing stop only).
        trail_mult: Trailing stop ATR multiplier (0 = disabled).

    Returns:
        Dict with pf, wr, trades, tr_yr, sharpe, dd.
    """
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades: List[Dict] = []
    position: Optional[Dict] = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]   # closed candle (iloc[-2] pattern)

        # --- Manage open position ---
        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]

            # Ratchet trailing stop
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

        # --- Check for new entry ---
        if position is None and sig_row.get("signal", 0) != 0:
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

    # --- Metrics ---
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
# Coin prefix map (handle special cases)
# ---------------------------------------------------------------------------

COIN_PREFIX_MAP: Dict[str, str] = {
    "1000PEPE": "1000pepeusdt",
    "1000SHIB": "1000shibusdt",
    "1000BONK": "1000bonkusdt",
}


def coin_to_prefix(coin: str) -> str:
    """Convert coin name to data file prefix."""
    if coin in COIN_PREFIX_MAP:
        return COIN_PREFIX_MAP[coin]
    return coin.lower() + "usdt"


# ---------------------------------------------------------------------------
# Per-coin sweep
# ---------------------------------------------------------------------------


def sweep_coin(coin: str) -> List[Dict]:
    """Run all strategy/param/timeframe combos for one coin.

    Signals are computed once per strategy, then reused across all param sets.

    Returns list of result dicts.
    """
    prefix = coin_to_prefix(coin)

    path_1h = DATA_DIR / f"{prefix}_1h_2y.csv"
    raw_1h = load_csv(path_1h)

    if raw_1h.empty:
        return []

    raw_4h = resample_4h(raw_1h)
    has_4h = len(raw_4h) >= 80

    # Pre-compute base indicators
    df_1h = compute_base(raw_1h)
    df_4h = compute_base(raw_4h) if has_4h else pd.DataFrame()

    results: List[Dict] = []

    for strat_name, strat_fn in STRATEGY_FUNCS.items():
        param_sets = MR_PARAM_SETS if strat_name in MR_STRATEGIES else PARAM_SETS

        # Compute signals once per strategy per timeframe
        try:
            sig_1h = strat_fn(df_1h)
        except Exception:
            sig_1h = None

        sig_4h = None
        if has_4h and not df_4h.empty:
            try:
                sig_4h = strat_fn(df_4h)
            except Exception:
                sig_4h = None

        for tf_label, sig_df in [("1h", sig_1h), ("4h", sig_4h)]:
            if sig_df is None or sig_df.empty:
                continue

            for sl, tp, trail, label in param_sets:
                try:
                    r = simulate(sig_df, sl_mult=sl, tp_mult=tp, trail_mult=trail)
                except Exception:
                    continue

                if r["trades"] > 0:
                    results.append({
                        "coin": coin,
                        "strategy": strat_name,
                        "tf": tf_label,
                        "params": label,
                        "sl": sl,
                        "tp": tp,
                        "trail": trail,
                        **r,
                    })

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def print_summary(all_results: List[Dict], min_pf: float = 1.2, min_trades: int = 8) -> None:
    """Print best strategy per weak coin."""
    print("\n" + "=" * 105)
    print("IMPROVEMENT RECOMMENDATIONS — Best New Strategy per Weak Coin")
    print("=" * 105)

    header = (
        f"{'':2}{'Coin':<12} {'Strategy':<20} {'TF':>4} {'Params':<12} "
        f"{'PF':>6} {'WR%':>6} {'Tr':>5} {'Tr/yr':>6} {'Sharpe':>7} {'DD%':>6}"
    )
    print(header)
    print("-" * 105)

    from collections import defaultdict
    by_coin: Dict[str, List[Dict]] = defaultdict(list)
    for r in all_results:
        by_coin[r["coin"]].append(r)

    reco_count = 0
    for coin in WEAK_COINS:
        coin_results = by_coin.get(coin, [])
        valid = [r for r in coin_results if r["pf"] >= min_pf and r["trades"] >= min_trades]

        if not valid:
            valid_any = [r for r in coin_results if r["trades"] >= 4]
            if valid_any:
                best = max(valid_any, key=lambda x: x["pf"])
                mark = "  "
            else:
                print(f"  {coin:<10} {'NO DATA / NO TRADES':<80}")
                continue
        else:
            best = max(valid, key=lambda x: x["pf"])
            mark = ">>" if best["pf"] >= 1.5 else " >"
            reco_count += 1

        print(
            f"{mark}{best['coin']:<10} {best['strategy']:<20} {best['tf']:>4} "
            f"{best['params']:<12} "
            f"{best['pf']:>6.2f} {best['wr']:>6.1f} {best['trades']:>5} "
            f"{best.get('tr_yr', 0):>6.1f} {best['sharpe']:>7.2f} {best['dd']:>6.1f}"
        )

    print("-" * 105)
    print(f"Coins with PF>={min_pf} and trades>={min_trades}: {reco_count}/{len(WEAK_COINS)}")


def print_top_combos(all_results: List[Dict], top_n: int = 25) -> None:
    """Print top N combos across all coins, sorted by PF."""
    valid = [r for r in all_results if r["pf"] >= 1.2 and r["trades"] >= 8]
    valid.sort(key=lambda x: x["pf"], reverse=True)

    print(f"\n\nTOP {top_n} COMBOS (PF >= 1.2, trades >= 8)")
    print("=" * 105)
    header = (
        f"{'':2}{'Coin':<12} {'Strategy':<20} {'TF':>4} {'Params':<12} "
        f"{'PF':>6} {'WR%':>6} {'Tr':>5} {'Tr/yr':>6} {'Sharpe':>7} {'DD%':>6}"
    )
    print(header)
    print("-" * 105)
    for r in valid[:top_n]:
        print(
            f"  {r['coin']:<10} {r['strategy']:<20} {r['tf']:>4} "
            f"{r['params']:<12} "
            f"{r['pf']:>6.2f} {r['wr']:>6.1f} {r['trades']:>5} "
            f"{r.get('tr_yr', 0):>6.1f} {r['sharpe']:>7.2f} {r['dd']:>6.1f}"
        )
    print("-" * 105)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 80)
    print("SWEEP: Parameter Optimization for 30 Weak Coins (Lightweight Simulator)")
    print(f"Strategies: {len(STRATEGY_FUNCS)} | Param sets: {len(PARAM_SETS)} | Timeframes: 2")
    print(f"Target: ~{len(WEAK_COINS) * len(STRATEGY_FUNCS) * len(PARAM_SETS) * 2} combos max")
    print("=" * 80)

    all_results: List[Dict] = []

    for i, coin in enumerate(WEAK_COINS, 1):
        print(f"[{i:02d}/{len(WEAK_COINS)}] {coin} ...", end="", flush=True)
        coin_results = sweep_coin(coin)

        passing = [r for r in coin_results if r["pf"] >= 1.2 and r["trades"] >= 8]
        if passing:
            best_pf = max(r["pf"] for r in passing)
            best = max(passing, key=lambda x: x["pf"])
            print(
                f" {len(coin_results)} combos, {len(passing)} passing, "
                f"best: {best['strategy']} {best['tf']} PF={best_pf:.2f}"
            )
        else:
            print(f" {len(coin_results)} combos, 0 passing PF>=1.2 trades>=8")

        all_results.extend(coin_results)

        if i % 5 == 0:
            passing_so_far = [r for r in all_results if r["pf"] >= 1.2 and r["trades"] >= 8]
            print(f"\n  -- {i}/{len(WEAK_COINS)} coins done, {len(passing_so_far)} passing combos --\n")

    # Print results
    print_summary(all_results)
    print_top_combos(all_results, top_n=25)

    # Compute per-coin best
    from collections import defaultdict
    by_coin: Dict[str, List[Dict]] = defaultdict(list)
    for r in all_results:
        by_coin[r["coin"]].append(r)

    top_per_coin: Dict[str, Dict] = {}
    for coin in WEAK_COINS:
        coin_results = by_coin.get(coin, [])
        valid = [r for r in coin_results if r["trades"] >= 8]
        if valid:
            top_per_coin[coin] = max(valid, key=lambda x: x["pf"])

    # Save JSON
    output = {
        "metadata": {
            "coins": WEAK_COINS,
            "strategies": list(STRATEGY_FUNCS.keys()),
            "param_sets": [list(p) for p in PARAM_SETS],
            "mr_param_sets": [list(p) for p in MR_PARAM_SETS],
            "total_combos_with_trades": len(all_results),
        },
        "results": all_results,
        "top_per_coin": top_per_coin,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n\nResults saved to {OUTPUT_FILE}")
    passing = [r for r in all_results if r["pf"] >= 1.2 and r["trades"] >= 8]
    print(f"Total combos with trades: {len(all_results)}")
    print(f"Passing combos (PF>=1.2, trades>=8): {len(passing)}")


if __name__ == "__main__":
    main()
