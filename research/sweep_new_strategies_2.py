"""
sweep_new_strategies_2.py — Lightweight sweep of 10 NEW strategies (S11-S20)
across all coins with 1h 2yr data.

Strategies tested:
  S11: EMA + Ichimoku Hybrid 1H
  S12: Vol Expansion + Supertrend Confluence 1H
  S13: Multi-Timeframe RSI Alignment (1H signal, 4H confirmation)
  S14: Funding Rate as Primary Signal 1H
  S15: Session Momentum - London Open 1H
  S16: Day-of-Week Filter + EMA Crossover 1H
  S17: Regime Transition Momentum 1H
  S18: Consecutive Candle Momentum 1H
  S19: ATR Regime Adaptive Entry 1H
  S20: Cross-Coin Momentum Leader 1H (BTC leads alts)

Each strategy (except S15 and S20) is also tested on 4H resampled data.

Usage:
    python research/sweep_new_strategies_2.py
    python research/sweep_new_strategies_2.py --coins btcusdt,ethusdt
    python research/sweep_new_strategies_2.py --min-pf 1.2 --min-trades 4
"""
import argparse
import glob
import json
import logging
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Suppress noisy loggers
logging.basicConfig(level=logging.WARNING)
logging.getLogger("bot").setLevel(logging.WARNING)

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import compute_atr, compute_ema, compute_rsi, compute_supertrend, compute_volume_ma

DATA_DIR = "/Users/iceai/Work/ccbt/data"


# ---------------------------------------------------------------------------
# Fast vectorized supertrend direction (replaces Python-loop version for sweep)
# ---------------------------------------------------------------------------

def _compute_supertrend_dir(
    upper: np.ndarray, lower: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """Compute Supertrend direction array (1=bullish, -1=bearish) using numpy."""
    n = len(close)
    direction = np.ones(n, dtype=np.int8)
    final_upper = upper.copy()
    final_lower = lower.copy()

    for i in range(1, n):
        # Lower band ratchets up only
        if lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = lower[i]
        else:
            final_lower[i] = final_lower[i - 1]
        # Upper band ratchets down only
        if upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = upper[i]
        else:
            final_upper[i] = final_upper[i - 1]
        # Direction
        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if close[i] > final_upper[i] else -1

    return direction


# ---------------------------------------------------------------------------
# Simulation constants
# ---------------------------------------------------------------------------
COMMISSION = 0.00055  # 0.055% taker
SLIPPAGE = 0.0002     # 0.02% per side
RISK_PCT = 0.01       # 1% risk per trade
LEVERAGE = 25
WARMUP = 100          # bars to skip at start

# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _add_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add standard indicators used across multiple strategies. Returns copy."""
    df = df.copy()
    df["ema9"] = compute_ema(df["close"], 9)
    df["ema21"] = compute_ema(df["close"], 21)
    df["ema50"] = compute_ema(df["close"], 50)
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["rsi14"] = compute_rsi(df["close"], 14)
    df["vol_ma20"] = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / df["vol_ma20"].clip(lower=1e-10)

    # EMA crossover booleans
    df["cross_up"] = (df["ema9"] > df["ema21"]) & (df["ema9"].shift(1) <= df["ema21"].shift(1))
    df["cross_down"] = (df["ema9"] < df["ema21"]) & (df["ema9"].shift(1) >= df["ema21"].shift(1))

    # Ichimoku
    df["tenkan"] = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    df["kijun"] = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    df["cloud_top"] = pd.concat([span_a, span_b], axis=1).max(axis=1)
    df["cloud_bottom"] = pd.concat([span_a, span_b], axis=1).min(axis=1)

    # Supertrend (multiplier 2.0) — vectorized implementation for performance
    # The imported compute_supertrend has a Python loop; use vectorized version here
    atr_st = compute_atr(df["high"], df["low"], df["close"], 14)
    hl2 = (df["high"] + df["low"]) / 2
    _upper = hl2 + 2.0 * atr_st
    _lower = hl2 - 2.0 * atr_st
    # Vectorized supertrend direction via numpy
    st_dir_arr = _compute_supertrend_dir(_upper.values, _lower.values, df["close"].values)
    df["st_dir"] = st_dir_arr

    # Hour and day-of-week
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek

    # Vectorized regime filter: ranging = low ATR + no direction
    atr_ma50 = df["atr"].rolling(50).mean()
    vol_ma50 = df["vol_ratio"].rolling(50).mean()
    df["ranging"] = (df["atr"] < atr_ma50 * 0.8) & (df["vol_ratio"] < vol_ma50 * 0.8)

    return df


def _hours_and_dow_filter(df: pd.DataFrame, h_start: int = 3, h_end: int = 20) -> "pd.Series[bool]":
    """Boolean mask for trading hours + no-weekend."""
    in_hours = (df["hour"] >= h_start) & (df["hour"] < h_end)
    not_we = df["dow"] < 5
    return in_hours & not_we


# ---------------------------------------------------------------------------
# Strategy signal generators
# Each returns a df with "signal" column: 1=long, -1=short, 0=flat
# ---------------------------------------------------------------------------

def s11_ema_ichi_hybrid(df: pd.DataFrame) -> pd.DataFrame:
    """S11: EMA(9/21) cross + must be above/below Ichimoku cloud."""
    df = df.copy()
    hours = _hours_and_dow_filter(df)
    not_ranging = ~df["ranging"]
    long_sig = df["cross_up"] & (df["close"] > df["cloud_top"]) & hours & not_ranging
    short_sig = df["cross_down"] & (df["close"] < df["cloud_bottom"]) & hours & not_ranging
    sig = pd.Series(0, index=df.index)
    sig[long_sig] = 1
    sig[short_sig] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def s12_volexp_supertrend(df: pd.DataFrame) -> pd.DataFrame:
    """S12: Vol expansion breakout confirmed by Supertrend direction."""
    df = df.copy()
    atr_ma20 = df["atr"].rolling(20).mean()
    vol_expanding = df["atr"] > atr_ma20 * 1.8
    vol_break_high = df["close"] > df["high"].rolling(10).max().shift(1)
    vol_break_low = df["close"] < df["low"].rolling(10).min().shift(1)

    hours = _hours_and_dow_filter(df)
    long_sig = vol_expanding & vol_break_high & (df["st_dir"] == 1) & hours
    short_sig = vol_expanding & vol_break_low & (df["st_dir"] == -1) & hours
    sig = pd.Series(0, index=df.index)
    sig[long_sig] = 1
    sig[short_sig] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def s13_mtf_rsi(df: pd.DataFrame) -> pd.DataFrame:
    """S13: RSI(14) crosses 50 on 1H with 4H RSI confirmation + EMA trend."""
    df = df.copy()

    # Compute 4H RSI by resampling then forward-filling back to 1H index
    df_4h = df.resample("4h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    rsi_4h = compute_rsi(df_4h["close"], 14)
    rsi_4h_ff = rsi_4h.reindex(df.index, method="ffill")

    rsi_cross_up = (df["rsi14"] > 50) & (df["rsi14"].shift(1) <= 50)
    rsi_cross_down = (df["rsi14"] < 50) & (df["rsi14"].shift(1) >= 50)
    ema_bull = df["ema9"] > df["ema21"]
    ema_bear = df["ema9"] < df["ema21"]

    hours = _hours_and_dow_filter(df)
    not_ranging = ~df["ranging"]

    long_sig = rsi_cross_up & (rsi_4h_ff > 50) & ema_bull & hours & not_ranging
    short_sig = rsi_cross_down & (rsi_4h_ff < 50) & ema_bear & hours & not_ranging

    sig = pd.Series(0, index=df.index)
    sig[long_sig] = 1
    sig[short_sig] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def s14_funding_primary(df: pd.DataFrame, funding_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """S14: Funding rate as primary contrarian signal."""
    df = df.copy()
    df["signal"] = 0
    if funding_df is None or funding_df.empty:
        return df

    # Align funding to 1H candles — shift(1) to prevent look-ahead
    funding_aligned = funding_df["funding_rate"].reindex(df.index, method="ffill").shift(1)

    ema50_bull = df["close"] > df["ema50"]
    ema50_bear = df["close"] < df["ema50"]

    # Contrarian: shorts crowded (negative funding) → go long; longs crowded → go short
    # Thresholds: long < -0.0003 (-0.03%), short > 0.0005 (+0.05%)
    long_sig = (funding_aligned < -0.0003) & ema50_bull
    short_sig = (funding_aligned > 0.0005) & ema50_bear

    hours = _hours_and_dow_filter(df)
    sig = pd.Series(0, index=df.index)
    sig[long_sig & hours] = 1
    sig[short_sig & hours] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def s15_london_open(df: pd.DataFrame) -> pd.DataFrame:
    """S15: London Open momentum (08:00-10:00 UTC breakout) — vectorized."""
    df = df.copy()

    # For each 10:00 candle, we want the close of the 08:00 candle on the same date.
    # Vectorized: extract close values for each hour, align on date.
    is_08h = df["hour"] == 8
    is_10h = df["hour"] == 10
    not_we = df["dow"] < 5

    # Build a Series of 08:00 closes indexed by date
    close_08 = df.loc[is_08h & not_we, "close"].copy()
    close_08.index = close_08.index.normalize()  # convert to date

    # Build the signal conditions at 10:00
    df_10 = df.loc[is_10h & not_we].copy()
    df_10_dates = df_10.index.normalize()
    # Get price_08 for each 10:00 bar's date
    price_08 = close_08.reindex(df_10_dates).values
    price_10 = df_10["close"].values
    atr_10 = df_10["atr"].values
    ema50_10 = df_10["ema50"].values

    move = price_10 - price_08
    threshold = 1.0 * atr_10

    long_mask = (move > threshold) & (price_10 > ema50_10) & ~np.isnan(price_08)
    short_mask = (-move > threshold) & (price_10 < ema50_10) & ~np.isnan(price_08)

    sig = pd.Series(0, index=df.index)
    sig.loc[df_10.index[long_mask]] = 1
    sig.loc[df_10.index[short_mask]] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def s16_dow_ema(df: pd.DataFrame) -> pd.DataFrame:
    """S16: EMA(9/21) cross only on Tue-Thu, trend-aligned."""
    df = df.copy()
    hours = _hours_and_dow_filter(df)
    tue_thu = df["dow"].isin([1, 2, 3])
    not_ranging = ~df["ranging"]

    long_sig = df["cross_up"] & tue_thu & hours & (df["close"] > df["ema50"]) & not_ranging
    short_sig = df["cross_down"] & tue_thu & hours & (df["close"] < df["ema50"]) & not_ranging

    sig = pd.Series(0, index=df.index)
    sig[long_sig] = 1
    sig[short_sig] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def s17_regime_transition(df: pd.DataFrame) -> pd.DataFrame:
    """S17: Enter on ranging→trending transition with EMA alignment."""
    df = df.copy()
    # ranging Series already computed in base indicators
    was_ranging = df["ranging"].shift(1)
    transition = was_ranging & ~df["ranging"]  # previous bar ranging, current trending

    ema_bull = df["ema9"] > df["ema21"]
    ema_bear = df["ema9"] < df["ema21"]
    hours = _hours_and_dow_filter(df)

    long_sig = transition & ema_bull & (df["close"] > df["ema50"]) & hours
    short_sig = transition & ema_bear & (df["close"] < df["ema50"]) & hours

    sig = pd.Series(0, index=df.index)
    sig[long_sig] = 1
    sig[short_sig] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def s18_consecutive_candles(df: pd.DataFrame) -> pd.DataFrame:
    """S18: 3 consecutive same-direction candles with increasing volume."""
    df = df.copy()
    green = (df["close"] > df["open"]).astype(int)
    red = (df["close"] < df["open"]).astype(int)

    three_green = (green == 1) & (green.shift(1) == 1) & (green.shift(2) == 1)
    three_red = (red == 1) & (red.shift(1) == 1) & (red.shift(2) == 1)

    vol_inc = (df["volume"] > df["volume"].shift(1)) & (df["volume"].shift(1) > df["volume"].shift(2))

    hours = _hours_and_dow_filter(df)
    not_ranging = ~df["ranging"]

    long_sig = three_green & vol_inc & (df["close"] > df["ema50"]) & hours & not_ranging
    short_sig = three_red & vol_inc & (df["close"] < df["ema50"]) & hours & not_ranging

    sig = pd.Series(0, index=df.index)
    sig[long_sig] = 1
    sig[short_sig] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def s19_atr_adaptive(df: pd.DataFrame) -> pd.DataFrame:
    """S19: ATR percentile crosses 0.5 from below (expanding vol) + EMA trend."""
    df = df.copy()
    atr_pctile = df["atr"].rolling(100).rank(pct=True)
    # Cross above 0.5: previous below, current above
    atr_expand = (atr_pctile > 0.5) & (atr_pctile.shift(1) <= 0.5)

    ema_bull = df["ema9"] > df["ema21"]
    ema_bear = df["ema9"] < df["ema21"]
    hours = _hours_and_dow_filter(df)
    # No regime filter here — the ATR expansion IS the regime signal

    long_sig = atr_expand & ema_bull & (df["close"] > df["ema50"]) & hours
    short_sig = atr_expand & ema_bear & (df["close"] < df["ema50"]) & hours

    sig = pd.Series(0, index=df.index)
    sig[long_sig] = 1
    sig[short_sig] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def s20_btc_leader(df: pd.DataFrame, btc_df: pd.DataFrame, lag: int = 4) -> pd.DataFrame:
    """S20: BTC EMA cross leads alt entry after 4 candles delay."""
    df = df.copy()
    # Compute BTC signals
    btc = btc_df.copy()
    btc_cross_up = (btc["ema9"] > btc["ema21"]) & (btc["ema9"].shift(1) <= btc["ema21"].shift(1))
    btc_cross_down = (btc["ema9"] < btc["ema21"]) & (btc["ema9"].shift(1) >= btc["ema21"].shift(1))

    # Forward-fill BTC signal with lag
    btc_long_trigger = btc_cross_up.astype(int).rolling(lag + 1).max()  # any cross in last (lag+1) bars
    btc_short_trigger = btc_cross_down.astype(int).rolling(lag + 1).max()

    # Align BTC signals to alt index
    btc_long_ff = btc_long_trigger.reindex(df.index, method="ffill").fillna(0)
    btc_short_ff = btc_short_trigger.reindex(df.index, method="ffill").fillna(0)

    ema50_bull = df["close"] > df["ema50"]
    ema50_bear = df["close"] < df["ema50"]
    hours = _hours_and_dow_filter(df)
    not_ranging = ~df["ranging"]

    long_sig = (btc_long_ff == 1) & ema50_bull & hours & not_ranging
    short_sig = (btc_short_ff == 1) & ema50_bear & hours & not_ranging

    sig = pd.Series(0, index=df.index)
    sig[long_sig] = 1
    sig[short_sig] = -1
    # Remove consecutive duplicates — only fire on first bar of condition
    sig = sig.where(sig != sig.shift(1), 0)
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


# ---------------------------------------------------------------------------
# Simulator (same logic as mass_sweep.py)
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    sl_mult: float,
    tp_mult: float,
    trail_mult: float = 0.0,
) -> dict:
    """Simple event-driven simulator. df must have 'signal' and 'atr' columns."""
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades = []
    position = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]  # closed candle that generated signal

        # Manage open position
        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]

            # Ratchet trailing stop
            if trail_mult > 0:
                trail_dist = sig_row["atr"] * trail_mult
                if side == 1:
                    new_sl = row["high"] - trail_dist
                    if new_sl > sl:
                        sl = new_sl
                        position["sl"] = sl
                else:
                    new_sl = row["low"] + trail_dist
                    if new_sl < sl:
                        sl = new_sl
                        position["sl"] = sl

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
                trades.append({"pnl": pnl, "exit": "sl" if hit_sl else "tp"})
                position = None
                if balance <= 0:
                    break

        # Check for new signal on closed candle
        if position is None and sig_row.get("signal", 0) != 0:
            # Weekend check
            dow = sig_row.get("dow")
            if not pd.isna(dow) and int(dow) >= 5:
                continue
            atr_val = sig_row["atr"]
            if pd.isna(atr_val) or atr_val <= 0:
                continue
            entry_p = row["close"]
            side = int(sig_row["signal"])
            sl_d = atr_val * sl_mult
            tp_d = atr_val * tp_mult if tp_mult > 0 else 0.0
            sl_p = entry_p - sl_d * side
            tp_p = entry_p + tp_d * side if tp_d > 0 else 0.0
            position = {"side": side, "entry": entry_p, "sl": sl_p, "tp": tp_p}

    total = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = sum(abs(t["pnl"]) for t in trades if t["pnl"] < 0)
    pf = gp / gl if gl > 0 else 0.0
    wr = wins / total * 100 if total > 0 else 0.0
    days = (df.index[-1] - df.index[0]).days
    yr = days / 365.25

    # Sharpe: annualized return / annualized std of trade PnLs
    if total >= 2:
        pnls = [t["pnl"] / 1000.0 for t in trades]
        trades_per_yr = total / yr if yr > 0 else 0
        ann_ret = (balance - 1000.0) / 1000.0
        std = float(np.std(pnls))
        sharpe = (ann_ret / (std * (trades_per_yr ** 0.5))) if std > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "trades": total,
        "tr_yr": total / yr if yr > 0 else 0,
        "wr": wr,
        "pf": pf,
        "dd": max_dd * 100,
        "sharpe": sharpe,
    }


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

# (id, name, signal_fn, extra_args_key, sl_mult, tp_mult, trail_mult, run_4h)
# extra_args_key: None | "funding" | "btc"
STRATEGIES = [
    (11, "EMA+Ichi Hybrid",    s11_ema_ichi_hybrid,   None,      1.5, 4.0, 0.0, True),
    (12, "VolExp+Supertrend",  s12_volexp_supertrend, None,      2.5, 4.0, 0.0, True),
    (13, "MTF RSI Align",      s13_mtf_rsi,           None,      2.0, 4.0, 0.0, True),
    (14, "Funding Primary",    s14_funding_primary,   "funding", 2.5, 5.0, 0.0, True),
    (15, "London Open",        s15_london_open,       None,      1.5, 3.0, 0.0, False),
    (16, "DoW+EMA Cross",      s16_dow_ema,           None,      2.0, 4.0, 0.0, True),
    (17, "Regime Transition",  s17_regime_transition, None,      2.0, 4.0, 0.0, True),
    (18, "Consec Candles",     s18_consecutive_candles, None,    1.5, 3.0, 0.0, True),
    (19, "ATR Adaptive",       s19_atr_adaptive,      None,      2.0, 3.0, 0.0, True),
    (20, "BTC Leader",         s20_btc_leader,        "btc",     2.0, 4.0, 0.0, False),
]


# ---------------------------------------------------------------------------
# Coin discovery
# ---------------------------------------------------------------------------

def discover_coins(coins_arg: Optional[List[str]]) -> List[str]:
    """Return list of lowercase prefixes with 1h 2yr data."""
    if coins_arg:
        return [c.lower().strip() for c in coins_arg if c.strip()]

    pattern = os.path.join(DATA_DIR, "*_1h_2y.csv")
    files = sorted(glob.glob(pattern))
    return [os.path.basename(f).replace("_1h_2y.csv", "") for f in files]


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(
    coins: List[str],
    min_pf: float = 1.0,
    min_trades: int = 4,
) -> List[Dict]:
    print(f"\nLoading data for {len(coins)} coin(s)...")

    loaded_1h: Dict[str, pd.DataFrame] = {}
    funding: Dict[str, pd.DataFrame] = {}

    for prefix in coins:
        name = prefix.replace("usdt", "").upper()
        fpath = os.path.join(DATA_DIR, f"{prefix}_1h_2y.csv")
        if not os.path.exists(fpath):
            # try 5y variant
            fpath = os.path.join(DATA_DIR, f"{prefix}_1h_5y.csv")
        if not os.path.exists(fpath):
            continue
        try:
            df = load_ohlcv(fpath)
            df = _add_base_indicators(df)
            loaded_1h[name] = df
        except Exception as e:
            print(f"  WARN: could not load {fpath}: {e}")

        # Load funding rate if available
        fp = os.path.join(DATA_DIR, f"{prefix}_funding_rate.csv")
        if os.path.exists(fp):
            try:
                fdf = pd.read_csv(fp, index_col=0, parse_dates=True)
                fdf.index = pd.to_datetime(fdf.index, utc=True)
                # Normalize column name
                if "fundingRate" in fdf.columns:
                    fdf = fdf.rename(columns={"fundingRate": "funding_rate"})
                funding[name] = fdf
            except Exception:
                pass

    print(f"Loaded: {len(loaded_1h)} coins with 1H data, {len(funding)} with funding data")

    # Load BTC reference for S20
    btc_df = loaded_1h.get("BTC")
    if btc_df is None:
        print("  WARN: BTC data not found — S20 (BTC Leader) will be skipped for all coins")

    all_results: List[Dict] = []

    print(f"\nRunning strategies 11-20 across {len(loaded_1h)} coins...\n")

    total_coins = len(loaded_1h)
    done = 0

    for name, df_1h in sorted(loaded_1h.items()):
        done += 1
        if done % 10 == 0:
            print(f"Progress: {done}/{total_coins} coins done")

        fdf = funding.get(name)

        for strat_tuple in STRATEGIES:
            sid, sname, fn, extra_key, sl_m, tp_m, trail_m, run_4h = strat_tuple

            # Build signal df variants to run: 1H and optionally 4H
            variants = [("1H", df_1h)]
            if run_4h:
                # Resample 1H to 4H
                try:
                    df_4h_raw = df_1h[["open", "high", "low", "close", "volume"]].resample("4h").agg({
                        "open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum"
                    }).dropna()
                    if len(df_4h_raw) >= 50:
                        df_4h = _add_base_indicators(df_4h_raw)
                        variants.append(("4H", df_4h))
                except Exception:
                    pass

            for tf_label, df_variant in variants:
                strat_label = f"S{sid} {sname}" if tf_label == "1H" else f"S{sid} {sname} 4H"

                # Skip S13 MTF on 4H (it internally resamples — would be 16H which is noisy)
                if sid == 13 and tf_label == "4H":
                    continue

                try:
                    if extra_key == "funding":
                        if fdf is None:
                            continue
                        # Align funding to variant index
                        fdf_aligned = fdf.copy()
                        df_sig = fn(df_variant, funding_df=fdf_aligned)
                    elif extra_key == "btc":
                        if btc_df is None or name == "BTC":
                            continue
                        # Use 1H BTC data regardless of variant (S20 is 1H-specific)
                        if tf_label != "1H":
                            continue
                        df_sig = fn(df_variant, btc_df=btc_df)
                    else:
                        df_sig = fn(df_variant)

                    r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m, trail_mult=trail_m)
                except Exception as e:
                    # Silently skip errors in individual strategies
                    _ = e
                    continue

                if r["trades"] < min_trades:
                    continue

                result = {
                    "coin": name,
                    "prefix": name.lower() + "usdt",
                    "strategy_id": sid,
                    "strategy": strat_label,
                    "timeframe": tf_label,
                    "pf": round(r["pf"], 3),
                    "wr_pct": round(r["wr"], 1),
                    "trades": r["trades"],
                    "tr_yr": round(r["tr_yr"], 1),
                    "sharpe": round(r["sharpe"], 2),
                    "dd_pct": round(r["dd"], 1),
                }
                all_results.append(result)

    # Filter winners and sort by PF
    winners = [r for r in all_results if r["pf"] >= min_pf]
    winners_sorted = sorted(winners, key=lambda x: -x["pf"])

    # Print results table
    print("\n" + "=" * 78)
    print("RESULTS: Strategies 11-20 across all coins (2yr, 1H)")
    print(f"Min PF >= {min_pf}, Min trades >= {min_trades}")
    print("=" * 78)
    hdr = f"{'Coin':<10} {'Strategy':<28} {'PF':>6} {'WR%':>6} {'Trades':>7} {'Sharpe':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in winners_sorted:
        print(
            f"{r['coin']:<10} {r['strategy']:<28} {r['pf']:>6.2f} {r['wr_pct']:>5.1f}%"
            f" {r['trades']:>7} {r['sharpe']:>7.2f}"
        )

    print(f"\nTotal shown: {len(winners)} / {len(all_results)} tested")

    # Save to JSON
    out_path = os.path.join(DATA_DIR, "sweep_new_strats_2.json")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Full results saved to {out_path}")

    # Also print top 20 by PF with PF > 1.3 for quick scan
    top = [r for r in winners_sorted if r["pf"] >= 1.3][:20]
    if top:
        print("\n--- Top results (PF >= 1.3) ---")
        for r in top:
            print(
                f"  {r['coin']:<10} {r['strategy']:<28} PF={r['pf']:.2f}"
                f"  WR={r['wr_pct']:.0f}%  Tr={r['trades']}  Sh={r['sharpe']:.2f}"
            )

    return winners_sorted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="New strategies 11-20 sweep across all 1H 2yr coin data"
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
        help="Minimum profit factor to show (default: 1.0)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=4,
        help="Minimum trades to count result (default: 4)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    coins_arg = args.coins.split(",") if args.coins else None
    coins = discover_coins(coins_arg)

    if not coins:
        print("No 1h_2y CSV files found in data/. Exiting.")
        sys.exit(1)

    print(f"Sweeping {len(coins)} coin(s): {', '.join(coins[:10])}{'...' if len(coins) > 10 else ''}")
    run_sweep(coins, min_pf=args.min_pf, min_trades=args.min_trades)
