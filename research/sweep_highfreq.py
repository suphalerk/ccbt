"""
sweep_highfreq.py — Lightweight sweep of 10 high-frequency trading strategies.

Focuses on quick, frequent trades (target: 30-200 trades/yr per coin).
Tests tight SL/TP configs designed for compounding via trade frequency.

Usage:
    python3 -u research/sweep_highfreq.py
    python3 -u research/sweep_highfreq.py --coins btcusdt,ethusdt
    python3 -u research/sweep_highfreq.py --min-pf 1.1 --min-trades 20
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

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import compute_atr, compute_ema, compute_rsi, compute_volume_ma

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = "/Users/iceai/Work/ccbt/data"

COMMISSION = 0.00055
SLIPPAGE = 0.00020
RISK_PCT = 0.01
LEVERAGE = 25.0
HOURS_START = 3
HOURS_END = 20
MIN_TRADES = 10


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def compute_bb(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands: returns (upper, mid, lower)."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


def compute_stoch_fast(high: pd.Series, low: pd.Series, close: pd.Series,
                       k_period: int = 5, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Fast Stochastic. Returns (K, D)."""
    lowest = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    denom = (highest - lowest).clip(lower=1e-10)
    raw_k = 100.0 * (close - lowest) / denom
    k = raw_k.rolling(d_period).mean()
    d = k.rolling(d_period).mean()
    return k, d


def add_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add common indicators used by all strategies. Returns a copy."""
    df = df.copy()
    df["ema5"] = compute_ema(df["close"], 5)
    df["ema8"] = compute_ema(df["close"], 8)
    df["ema9"] = compute_ema(df["close"], 9)
    df["ema13"] = compute_ema(df["close"], 13)
    df["ema21"] = compute_ema(df["close"], 21)
    df["ema50"] = compute_ema(df["close"], 50)
    df["rsi"] = compute_rsi(df["close"], 14)
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["vol_ma"] = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / df["vol_ma"].clip(lower=1e-10)

    # Body size
    df["body"] = (df["close"] - df["open"]).abs()
    df["body_ma"] = df["body"].rolling(20).mean()

    # Candle range
    df["candle_range"] = df["high"] - df["low"]

    # ROC
    df["roc5"] = (df["close"] - df["close"].shift(5)) / df["close"].shift(5).clip(lower=1e-10) * 100

    # Stochastic fast (5,3,3)
    df["stoch_k"], df["stoch_d"] = compute_stoch_fast(df["high"], df["low"], df["close"], 5, 3)

    # Bollinger Bands (20, 2)
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = compute_bb(df["close"], 20, 2.0)
    bb_width = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].clip(lower=1e-10)
    df["bb_width"] = bb_width
    # Percentile rank of BB width over last 100 bars
    df["bb_width_pctile"] = bb_width.rolling(100).rank(pct=True)

    # Hour / day-of-week (if datetime index)
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek

    return df


# ---------------------------------------------------------------------------
# Strategy signal generators (return df with 'signal' column)
# ---------------------------------------------------------------------------

def _base_filters(df: pd.DataFrame) -> pd.Series:
    """Hours + weekend mask (True = can trade)."""
    in_hours = (df["hour"] >= HOURS_START) & (df["hour"] < HOURS_END)
    not_we = df["dow"] < 5
    return in_hours & not_we


WARMUP = 100  # bars to blank at start


def hf1_micro_ema(df: pd.DataFrame, **_) -> pd.DataFrame:
    """HF1: Micro EMA 5/8 crossover (15m). Trend filter: close > ema21."""
    df = df.copy()
    mask = _base_filters(df)

    cross_up = (df["ema5"] > df["ema8"]) & (df["ema5"].shift(1) <= df["ema8"].shift(1))
    cross_dn = (df["ema5"] < df["ema8"]) & (df["ema5"].shift(1) >= df["ema8"].shift(1))
    trend_bull = df["close"] > df["ema21"]
    trend_bear = df["close"] < df["ema21"]

    sig = pd.Series(0, index=df.index)
    sig[cross_up & trend_bull & mask] = 1
    sig[cross_dn & trend_bear & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def hf2_rsi_bounce(df: pd.DataFrame, **_) -> pd.DataFrame:
    """HF2: RSI Bounce — dip into 35-45 zone then bounce above 45 (uptrend).
    Short: rise to 55-65 then drop below 55 (downtrend). 15m."""
    df = df.copy()
    mask = _base_filters(df)

    rsi = df["rsi"]
    rsi_prev = rsi.shift(1)
    trend_bull = df["close"] > df["ema21"]
    trend_bear = df["close"] < df["ema21"]

    # Long: previous RSI was in 35-45 (dip), now crosses back above 45
    rsi_bull = (rsi_prev >= 35) & (rsi_prev <= 45) & (rsi > 45)
    # Short: previous RSI was in 55-65 (overbought touch), now crosses back below 55
    rsi_bear = (rsi_prev >= 55) & (rsi_prev <= 65) & (rsi < 55)

    sig = pd.Series(0, index=df.index)
    sig[rsi_bull & trend_bull & mask] = 1
    sig[rsi_bear & trend_bear & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def hf3_volume_spike(df: pd.DataFrame, **_) -> pd.DataFrame:
    """HF3: Volume Spike Momentum — first spike candle only (15m)."""
    df = df.copy()
    mask = _base_filters(df)

    vr = df["vol_ratio"]
    vr_prev = vr.shift(1)
    first_spike = (vr >= 3.0) & (vr_prev < 3.0)

    green = df["close"] > df["open"]
    red = df["close"] < df["open"]
    trend_bull = df["close"] > df["ema21"]
    trend_bear = df["close"] < df["ema21"]

    sig = pd.Series(0, index=df.index)
    sig[first_spike & green & trend_bull & mask] = 1
    sig[first_spike & red & trend_bear & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def hf4_big_candle(df: pd.DataFrame, **_) -> pd.DataFrame:
    """HF4: Big Candle Breakout — body > 1.5×avg AND breaks 3-bar range (15m)."""
    df = df.copy()
    mask = _base_filters(df)

    big_body = df["body"] > 1.5 * df["body_ma"]
    # Shift(1) so we use closed candles only for the range reference
    high3 = df["high"].shift(1).rolling(3).max()
    low3 = df["low"].shift(1).rolling(3).min()
    trend_bull = df["close"] > df["ema21"]
    trend_bear = df["close"] < df["ema21"]

    sig = pd.Series(0, index=df.index)
    sig[big_body & (df["close"] > high3) & trend_bull & mask] = 1
    sig[big_body & (df["close"] < low3) & trend_bear & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def hf5_ema_touch(df: pd.DataFrame, df_1h: Optional[pd.DataFrame] = None, **_) -> pd.DataFrame:
    """HF5: EMA Touch & Go — price touches ema21 during ema9>ema21>ema50 alignment (1H)."""
    df = df.copy()
    mask = _base_filters(df)

    # Strong trend alignment
    trend_bull_strong = (df["ema9"] > df["ema21"]) & (df["ema21"] > df["ema50"])
    trend_bear_strong = (df["ema9"] < df["ema21"]) & (df["ema21"] < df["ema50"])

    tol = 0.002  # 0.2%
    touch_bull = (df["low"] <= df["ema21"] * (1 + tol)) & (df["close"] > df["ema21"])
    touch_bear = (df["high"] >= df["ema21"] * (1 - tol)) & (df["close"] < df["ema21"])

    sig = pd.Series(0, index=df.index)
    sig[touch_bull & trend_bull_strong & mask] = 1
    sig[touch_bear & trend_bear_strong & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def hf6_stoch_scalp(df: pd.DataFrame, **_) -> pd.DataFrame:
    """HF6: Fast Stochastic (5,3,3) — K crosses D from oversold/overbought (15m)."""
    df = df.copy()
    mask = _base_filters(df)

    k = df["stoch_k"]
    d = df["stoch_d"]
    k_prev = k.shift(1)
    d_prev = d.shift(1)

    # K crosses above D AND was previously oversold (< 20)
    cross_up = (k > d) & (k_prev <= d_prev) & (k_prev < 20)
    # K crosses below D AND was previously overbought (> 80)
    cross_dn = (k < d) & (k_prev >= d_prev) & (k_prev > 80)

    trend_bull = df["close"] > df["ema21"]
    trend_bear = df["close"] < df["ema21"]

    sig = pd.Series(0, index=df.index)
    sig[cross_up & trend_bull & mask] = 1
    sig[cross_dn & trend_bear & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def hf7_atr_spike(df: pd.DataFrame, **_) -> pd.DataFrame:
    """HF7: ATR Spike Entry — first wide-range candle (range > 1.5×ATR14) (15m)."""
    df = df.copy()
    mask = _base_filters(df)

    cr = df["candle_range"]
    atr = df["atr"]
    cr_prev = cr.shift(1)
    atr_prev = atr.shift(1)

    first_spike = (cr > 1.5 * atr) & (cr_prev <= 1.5 * atr_prev)
    green = df["close"] > df["open"]
    red = df["close"] < df["open"]
    trend_bull = df["close"] > df["ema21"]
    trend_bear = df["close"] < df["ema21"]

    sig = pd.Series(0, index=df.index)
    sig[first_spike & green & trend_bull & mask] = 1
    sig[first_spike & red & trend_bear & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def hf8_triple_ema(df: pd.DataFrame, **_) -> pd.DataFrame:
    """HF8: Triple EMA Alignment (5>8>13 or 5<8<13) — edge detect on alignment start (15m)."""
    df = df.copy()
    mask = _base_filters(df)

    e5, e8, e13 = df["ema5"], df["ema8"], df["ema13"]
    aligned_bull = (e5 > e8) & (e8 > e13)
    aligned_bear = (e5 < e8) & (e8 < e13)

    # Edge: newly aligned (previous bar was NOT aligned)
    new_bull = aligned_bull & ~aligned_bull.shift(1).fillna(False)
    new_bear = aligned_bear & ~aligned_bear.shift(1).fillna(False)

    sig = pd.Series(0, index=df.index)
    sig[new_bull & mask] = 1
    sig[new_bear & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def hf9_bb_squeeze(df: pd.DataFrame, **_) -> pd.DataFrame:
    """HF9: BB Squeeze Scalp — low-volatility squeeze + breakout (1H)."""
    df = df.copy()
    mask = _base_filters(df)

    squeeze = df["bb_width_pctile"] < 0.2
    trend_bull = df["close"] > df["ema21"]
    trend_bear = df["close"] < df["ema21"]

    # Breakout: close above upper band (squeeze condition on PREVIOUS bar)
    prev_squeeze = squeeze.shift(1).fillna(False)
    breakout_up = prev_squeeze & (df["close"] > df["bb_upper"])
    breakout_dn = prev_squeeze & (df["close"] < df["bb_lower"])

    sig = pd.Series(0, index=df.index)
    sig[breakout_up & trend_bull & mask] = 1
    sig[breakout_dn & trend_bear & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


def hf10_roc_quick(df: pd.DataFrame, **_) -> pd.DataFrame:
    """HF10: ROC(5) crosses zero with ema21 trend filter (1H)."""
    df = df.copy()
    mask = _base_filters(df)

    roc = df["roc5"]
    roc_prev = roc.shift(1)
    trend_bull = df["close"] > df["ema21"]
    trend_bear = df["close"] < df["ema21"]

    cross_up = (roc > 0) & (roc_prev <= 0)
    cross_dn = (roc < 0) & (roc_prev >= 0)

    sig = pd.Series(0, index=df.index)
    sig[cross_up & trend_bull & mask] = 1
    sig[cross_dn & trend_bear & mask] = -1
    sig.iloc[:WARMUP] = 0
    df["signal"] = sig
    return df


# ---------------------------------------------------------------------------
# Strategy specs: (name, timeframe, fn, sl_mult, tp_mult, trail_mult)
# ---------------------------------------------------------------------------

STRATEGY_SPECS = [
    # name          tf     fn                sl    tp    trail
    ("HF1-MicroEMA",  "15m", hf1_micro_ema,    0.5,  1.0,  1.0),
    ("HF2-RSIBounce", "15m", hf2_rsi_bounce,   0.7,  1.5,  1.5),
    ("HF3-VolSpike",  "15m", hf3_volume_spike, 0.5,  1.5,  1.5),
    ("HF4-BigCandle", "15m", hf4_big_candle,   1.0,  1.5,  1.5),
    ("HF5-EMATouchGo","1h",  hf5_ema_touch,    0.7,  1.5,  1.5),
    ("HF6-StochScalp","15m", hf6_stoch_scalp,  0.5,  1.0,  1.0),
    ("HF7-ATRSpike",  "15m", hf7_atr_spike,    0.5,  1.5,  1.5),
    ("HF8-TripleEMA", "15m", hf8_triple_ema,   0.5,  1.0,  1.0),
    ("HF9-BBSqueeze", "1h",  hf9_bb_squeeze,   1.0,  2.0,  2.0),
    ("HF10-ROCQuick", "1h",  hf10_roc_quick,   0.7,  1.5,  1.5),
]


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    sl_mult: float,
    tp_mult: float,
    trail_mult: float = 0.0,
) -> dict:
    """Event-driven simulator. df must have 'signal', 'atr', 'hour', 'dow' columns.

    Uses iloc[-2] pattern: signal generated at bar i-1, entry at open/close of bar i.
    Applies trading hours + weekend filters at signal time.
    """
    # Filter to last 1 year only
    if len(df) > 0:
        cutoff = df.index[-1] - pd.Timedelta(days=365)
        df = df[df.index >= cutoff]

    if len(df) < 50:
        return {"trades": 0, "tr_yr": 0.0, "wr": 0.0, "pf": 0.0, "dd": 0.0, "sharpe": 0.0}

    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades = []
    position = None
    pnl_series = []

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]  # signal on closed candle

        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]
            atr_at_entry = position["atr_entry"]

            # Ratchet trailing stop
            if trail_mult > 0:
                atr_cur = sig_row["atr"]
                if pd.isna(atr_cur) or atr_cur <= 0:
                    atr_cur = atr_at_entry
                trail_dist = atr_cur * trail_mult
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
            hit_tp = (tp > 0) and ((side == 1 and row["high"] >= tp) or (side == -1 and row["low"] <= tp))

            if hit_sl or hit_tp:
                exit_p = sl if hit_sl else tp
                pnl_pct = side * (exit_p - entry) / entry - (COMMISSION + SLIPPAGE) * 2
                pnl = balance * RISK_PCT * LEVERAGE * pnl_pct / sl_mult
                pnl = max(pnl, -balance * RISK_PCT * LEVERAGE)
                balance += pnl
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                trades.append({"pnl": pnl})
                pnl_series.append(pnl)
                position = None
                if balance <= 0:
                    break

        if position is None and sig_row.get("signal", 0) != 0:
            atr = sig_row["atr"]
            if pd.isna(atr) or atr <= 0:
                continue
            entry_p = row["close"]
            side = int(sig_row["signal"])
            sl_d = atr * sl_mult
            tp_d = atr * tp_mult if tp_mult > 0 else 0.0
            sl_p = (entry_p - sl_d) if side == 1 else (entry_p + sl_d)
            tp_p = (entry_p + tp_d) if (side == 1 and tp_d > 0) else (
                   (entry_p - tp_d) if (side == -1 and tp_d > 0) else 0.0)
            position = {
                "side": side,
                "entry": entry_p,
                "sl": sl_p,
                "tp": tp_p,
                "atr_entry": atr,
            }

    total = len(trades)
    if total == 0:
        return {"trades": 0, "tr_yr": 0.0, "wr": 0.0, "pf": 0.0, "dd": 0.0, "sharpe": 0.0}

    wins = sum(1 for t in trades if t["pnl"] > 0)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = sum(abs(t["pnl"]) for t in trades if t["pnl"] < 0)
    pf = gp / gl if gl > 0 else float(gp > 0) * 99.0

    days = (df.index[-1] - df.index[0]).days
    yr = max(days / 365.25, 1 / 365.25)
    tr_yr = total / yr

    wr = wins / total * 100.0

    # Sharpe on trade PnL series
    if len(pnl_series) >= 2:
        arr = np.array(pnl_series)
        sharpe = (arr.mean() / (arr.std() + 1e-10)) * (252 ** 0.5)
    else:
        sharpe = 0.0

    return {
        "trades": total,
        "tr_yr": round(tr_yr, 1),
        "wr": round(wr, 1),
        "pf": round(pf, 3),
        "dd": round(max_dd * 100, 2),
        "sharpe": round(float(sharpe), 2),
    }


# ---------------------------------------------------------------------------
# Coin discovery
# ---------------------------------------------------------------------------

def discover_coins(coins_arg: Optional[List[str]]) -> List[str]:
    """Return list of lowercase prefixes with BOTH 15m and 1h data."""
    if coins_arg:
        return [c.lower().strip() for c in coins_arg if c.strip()]

    files_15m = glob.glob(os.path.join(DATA_DIR, "*_15m_2y.csv"))
    files_1h = glob.glob(os.path.join(DATA_DIR, "*_1h_2y.csv"))

    prefixes_15m = {os.path.basename(f).replace("_15m_2y.csv", "") for f in files_15m}
    prefixes_1h = {os.path.basename(f).replace("_1h_2y.csv", "") for f in files_1h}

    both = sorted(prefixes_15m & prefixes_1h)
    return both


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(
    coins: List[str],
    min_pf: float = 1.0,
    min_trades: int = MIN_TRADES,
) -> Tuple[List[Dict], List[Dict]]:
    """Run sweep. Returns (all_results, winners)."""

    print(f"\nLoading data for {len(coins)} coins...")
    loaded_15m: Dict[str, pd.DataFrame] = {}
    loaded_1h: Dict[str, pd.DataFrame] = {}

    for prefix in coins:
        name = prefix.upper()
        for suffix, store in [("15m", loaded_15m), ("1h", loaded_1h)]:
            fpath = os.path.join(DATA_DIR, f"{prefix}_{suffix}_2y.csv")
            if os.path.exists(fpath):
                try:
                    store[name] = add_base_indicators(load_ohlcv(fpath))
                except Exception as e:
                    logger.warning("Could not load %s: %s", fpath, e)

    names_15m = set(loaded_15m.keys())
    names_1h = set(loaded_1h.keys())
    # Only run coins that have BOTH timeframes
    valid_names = sorted(names_15m & names_1h)

    print(f"Valid coins (both 15m + 1h): {len(valid_names)}")
    print(f"\nRunning {len(STRATEGY_SPECS)} strategies × {len(valid_names)} coins "
          f"= {len(STRATEGY_SPECS) * len(valid_names)} combinations\n")

    header = f"{'Coin':<12} {'Strategy':<18} {'TF':<4} {'PF':>6} {'WR%':>6} {'Trades':>7} {'Tr/yr':>7} {'Sharpe':>7}"
    print(header)
    print("-" * len(header))

    all_results: List[Dict] = []
    winners: List[Dict] = []

    for idx, name in enumerate(valid_names):
        if idx > 0 and idx % 10 == 0:
            print(f"  ... progress: {idx}/{len(valid_names)} coins processed")

        for strat_name, tf, strat_fn, sl_mult, tp_mult, trail_mult in STRATEGY_SPECS:
            df_src = loaded_15m[name] if tf == "15m" else loaded_1h[name]
            df_1h = loaded_1h.get(name)

            try:
                df_sig = strat_fn(df_src, df_1h=df_1h)
                metrics = simulate(df_sig, sl_mult=sl_mult, tp_mult=tp_mult, trail_mult=trail_mult)
            except Exception as e:
                logger.warning("Error running %s on %s: %s", strat_name, name, e)
                continue

            result = {
                "coin": name,
                "strategy": strat_name,
                "tf": tf,
                **metrics,
            }
            all_results.append(result)

            if metrics["pf"] >= min_pf and metrics["trades"] >= min_trades:
                winners.append(result)
                coin_short = name.replace("USDT", "")
                print(f"{coin_short:<12} {strat_name:<18} {tf:<4} "
                      f"{metrics['pf']:>6.2f} {metrics['wr']:>6.1f} "
                      f"{metrics['trades']:>7} {metrics['tr_yr']:>7.1f} "
                      f"{metrics['sharpe']:>7.2f}")

    return all_results, winners


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def strategy_ranking(all_results: List[Dict]) -> None:
    """Print strategy ranking by average PF and average trades/yr."""
    df = pd.DataFrame(all_results)
    df = df[df["trades"] >= MIN_TRADES]
    if df.empty:
        print("No results with sufficient trades.")
        return

    ranking = (
        df.groupby("strategy")
        .agg(
            avg_pf=("pf", "mean"),
            median_pf=("pf", "median"),
            avg_trades_yr=("tr_yr", "mean"),
            coins_above_1=("pf", lambda x: (x >= 1.0).sum()),
            coins_above_13=("pf", lambda x: (x >= 1.3).sum()),
            count=("pf", "count"),
        )
        .sort_values("avg_pf", ascending=False)
        .round(3)
    )

    print("\n" + "=" * 80)
    print("STRATEGY RANKING (avg PF, min 10 trades)")
    print("=" * 80)
    print(f"{'Strategy':<20} {'AvgPF':>7} {'MedPF':>7} {'AvgTr/yr':>9} "
          f"{'PF>=1':>7} {'PF>=1.3':>8} {'Tested':>7}")
    print("-" * 70)
    for strat, row in ranking.iterrows():
        print(f"{strat:<20} {row['avg_pf']:>7.3f} {row['median_pf']:>7.3f} "
              f"{row['avg_trades_yr']:>9.1f} {row['coins_above_1']:>7} "
              f"{row['coins_above_13']:>8} {row['count']:>7}")


def top_combinations(winners: List[Dict], top_n: int = 20, min_pf: float = 1.3, min_trades: int = 20) -> None:
    """Print top combinations by PF."""
    filtered = [w for w in winners if w["pf"] >= min_pf and w["trades"] >= min_trades]
    filtered.sort(key=lambda x: x["pf"], reverse=True)
    top = filtered[:top_n]

    print(f"\n{'=' * 80}")
    print(f"TOP {top_n} COMBINATIONS (PF >= {min_pf}, Trades >= {min_trades})")
    print("=" * 80)
    print(f"{'Coin':<12} {'Strategy':<20} {'TF':<4} {'PF':>6} {'WR%':>6} {'Trades':>7} {'Tr/yr':>7} {'Sharpe':>7}")
    print("-" * 70)
    for r in top:
        coin = r["coin"].replace("USDT", "")
        print(f"{coin:<12} {r['strategy']:<20} {r['tf']:<4} "
              f"{r['pf']:>6.2f} {r['wr']:>6.1f} "
              f"{r['trades']:>7} {r['tr_yr']:>7.1f} "
              f"{r['sharpe']:>7.2f}")


def compare_vs_deployed(winners: List[Dict]) -> None:
    """Compare best high-freq results vs deployed strategies."""
    deployed = [
        {"coin": "BTC",        "strategy": "EMA15m",       "pf": 1.85, "tr_yr": 23, "sharpe": 1.62},
        {"coin": "1000PEPE",   "strategy": "VolExp1H",      "pf": 10.85,"tr_yr": 8,  "sharpe": 2.81},
        {"coin": "TAO",        "strategy": "Ichimoku4H",    "pf": 5.21, "tr_yr": 8,  "sharpe": 2.24},
        {"coin": "ALGO",       "strategy": "Ichi4HTrail",   "pf": 5.84, "tr_yr": 5,  "sharpe": 1.11},
        {"coin": "WLD",        "strategy": "VolExp1H",      "pf": 3.25, "tr_yr": 6,  "sharpe": 1.47},
    ]

    print("\n" + "=" * 80)
    print("COMPARISON: BEST HIGH-FREQ vs DEPLOYED STRATEGIES")
    print("=" * 80)

    print("\nDeployed (reference):")
    print(f"{'Coin':<12} {'Strategy':<20} {'PF':>6} {'Tr/yr':>7} {'Sharpe':>7}")
    print("-" * 55)
    for d in sorted(deployed, key=lambda x: x["pf"], reverse=True):
        print(f"{d['coin']:<12} {d['strategy']:<20} {d['pf']:>6.2f} {d['tr_yr']:>7.1f} {d['sharpe']:>7.2f}")

    # Best high-freq by trade frequency
    hf_sorted_freq = sorted(winners, key=lambda x: x["tr_yr"], reverse=True)[:5]
    # Best high-freq by PF (min 30 trades/yr)
    hf_highpf = sorted(
        [w for w in winners if w["tr_yr"] >= 30],
        key=lambda x: x["pf"], reverse=True
    )[:5]

    print("\nBest High-Freq by trade frequency (top 5):")
    print(f"{'Coin':<12} {'Strategy':<20} {'TF':<4} {'PF':>6} {'Tr/yr':>7} {'Sharpe':>7}")
    print("-" * 58)
    for r in hf_sorted_freq:
        coin = r["coin"].replace("USDT", "")
        print(f"{coin:<12} {r['strategy']:<20} {r['tf']:<4} "
              f"{r['pf']:>6.2f} {r['tr_yr']:>7.1f} {r['sharpe']:>7.2f}")

    print("\nBest High-Freq by PF (Tr/yr >= 30, top 5):")
    print(f"{'Coin':<12} {'Strategy':<20} {'TF':<4} {'PF':>6} {'Tr/yr':>7} {'Sharpe':>7}")
    print("-" * 58)
    for r in hf_highpf:
        coin = r["coin"].replace("USDT", "")
        print(f"{coin:<12} {r['strategy']:<20} {r['tf']:<4} "
              f"{r['pf']:>6.2f} {r['tr_yr']:>7.1f} {r['sharpe']:>7.2f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="High-frequency strategy sweep")
    parser.add_argument("--coins", help="Comma-separated coin prefixes (e.g. btcusdt,ethusdt)")
    parser.add_argument("--min-pf", type=float, default=1.0, help="Minimum PF to print/save")
    parser.add_argument("--min-trades", type=int, default=MIN_TRADES, help="Minimum trades to count")
    args = parser.parse_args()

    coins_arg = args.coins.split(",") if args.coins else None
    coins = discover_coins(coins_arg)

    if not coins:
        print("ERROR: No coins found. Check data/ directory for *_15m_2y.csv files.")
        sys.exit(1)

    print(f"High-Frequency Strategy Sweep")
    print(f"Coins: {len(coins)} | Strategies: {len(STRATEGY_SPECS)} | "
          f"Min PF: {args.min_pf} | Min Trades: {args.min_trades}")
    print(f"Commission: {COMMISSION*100:.3f}% | Slippage: {SLIPPAGE*100:.3f}% | "
          f"Risk: {RISK_PCT*100:.0f}% | Leverage: {LEVERAGE}x")
    print(f"Hours: {HOURS_START:02d}:00-{HOURS_END:02d}:00 UTC | No weekends | Last 1yr of data")

    all_results, winners = run_sweep(coins, min_pf=args.min_pf, min_trades=args.min_trades)

    print(f"\nTotal combos tested: {len(all_results)}")
    print(f"Winners (PF>={args.min_pf}, trades>={args.min_trades}): {len(winners)}")

    # Save results
    out_path = os.path.join(DATA_DIR, "sweep_highfreq.json")
    with open(out_path, "w") as f:
        json.dump(
            {
                "winners": winners,
                "all_results": all_results,
                "params": {
                    "min_pf": args.min_pf,
                    "min_trades": args.min_trades,
                    "commission": COMMISSION,
                    "slippage": SLIPPAGE,
                    "risk_pct": RISK_PCT,
                    "leverage": LEVERAGE,
                },
            },
            f,
            indent=2,
        )
    print(f"Results saved to {out_path}")

    # Analysis
    strategy_ranking(all_results)
    top_combinations(winners, top_n=20, min_pf=1.3, min_trades=20)
    compare_vs_deployed(winners)


if __name__ == "__main__":
    main()
