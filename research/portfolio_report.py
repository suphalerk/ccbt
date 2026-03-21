"""portfolio_report.py — Comprehensive backtest portfolio report.

Generates three sections:
  Part 1: All-coins sweep results table (best strategy per coin)
  Part 2: Portfolio monthly PnL for last 12 months (19 bots)
  Part 3: Portfolio daily PnL for last 2 months (19 bots)

Usage:
    python research/portfolio_report.py
    python research/portfolio_report.py --no-sweep   # skip Part 1, just portfolio
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import compute_atr, compute_ema, compute_rsi, compute_volume_ma

DATA_DIR = "/Users/iceai/Work/ccbt/data"

# ---------------------------------------------------------------------------
# Portfolio definition (19 bots)
# ---------------------------------------------------------------------------

PORTFOLIO: List[Dict] = [
    # --- Original 7 ---
    {"coin": "BTC",       "prefix": "btcusdt",      "strategy": "ema",  "risk": 0.05, "sl": 1.0, "tp": 3.0, "trail": 2.0},
    {"coin": "DOGE",      "prefix": "dogeusdt",     "strategy": "ema",  "risk": 0.03, "sl": 1.0, "tp": 3.0, "trail": 2.0},
    {"coin": "ARB",       "prefix": "arbusdt",      "strategy": "ema",  "risk": 0.03, "sl": 1.0, "tp": 3.0, "trail": 2.0},
    {"coin": "WIF",       "prefix": "wifusdt",      "strategy": "ema",  "risk": 0.03, "sl": 1.0, "tp": 3.0, "trail": 2.0},
    {"coin": "AVAX",      "prefix": "avaxusdt",     "strategy": "ichi", "risk": 0.02, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "NEAR",      "prefix": "nearusdt",     "strategy": "ichi", "risk": 0.02, "sl": 2.5, "tp": 5.0, "trail": 3.0},
    {"coin": "SOL",       "prefix": "solusdt",      "strategy": "ichi", "risk": 0.02, "sl": 1.5, "tp": 4.0, "trail": 2.25},
    # --- New 12 ---
    {"coin": "GUN",       "prefix": "gunusdt",      "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "BERA",      "prefix": "berausdt",     "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "ATH",       "prefix": "athusdt",      "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "ZETA",      "prefix": "zetausdt",     "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "ARC",       "prefix": "arcusdt",      "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "ANIME",     "prefix": "animeusdt",    "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "TRUMP",     "prefix": "trumpusdt",    "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "INJ",       "prefix": "injusdt",      "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "XLM",       "prefix": "xlmusdt",      "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "1000SHIB",  "prefix": "1000shibusdt", "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "TRX",       "prefix": "trxusdt",      "strategy": "ichi", "risk": 0.01, "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "ARC_EMA",   "prefix": "arcusdt",      "strategy": "ema",  "risk": 0.01, "sl": 1.0, "tp": 3.0, "trail": 2.0},
]

STARTING_BALANCE = 10_000.0
COMMISSION = 0.00055
SLIPPAGE = 0.0002
LEVERAGE = 25


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def add_indicators(df: pd.DataFrame, ema_f: int = 9, ema_s: int = 21) -> pd.DataFrame:
    """Compute all indicators needed by both strategies. Returns a copy."""
    df = df.copy()
    df["ema_f"] = compute_ema(df["close"], ema_f)
    df["ema_s"] = compute_ema(df["close"], ema_s)
    df["rsi"] = compute_rsi(df["close"], 14)
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["vol_ma"] = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / df["vol_ma"].clip(lower=1e-10)

    df["cross_up"] = (df["ema_f"] > df["ema_s"]) & (
        df["ema_f"].shift(1) <= df["ema_s"].shift(1)
    )
    df["cross_down"] = (df["ema_f"] < df["ema_s"]) & (
        df["ema_f"].shift(1) >= df["ema_s"].shift(1)
    )
    df["ema_slope"] = (df["ema_f"] - df["ema_f"].shift(1)).abs() / df["close"].shift(1)

    # Ichimoku
    df["tenkan"] = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    df["kijun"] = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    df["cloud_top"] = pd.concat([span_a, span_b], axis=1).max(axis=1)
    df["cloud_bottom"] = pd.concat([span_a, span_b], axis=1).min(axis=1)
    df["ichi_bull"] = (
        (df["tenkan"] > df["kijun"])
        & (df["tenkan"].shift(1) <= df["kijun"].shift(1))
        & (df["close"] > df["cloud_top"])
    )
    df["ichi_bear"] = (
        (df["tenkan"] < df["kijun"])
        & (df["tenkan"].shift(1) >= df["kijun"].shift(1))
        & (df["close"] < df["cloud_bottom"])
    )

    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek

    return df


# ---------------------------------------------------------------------------
# Strategy signal generators
# ---------------------------------------------------------------------------

def strategy_ema_cross(
    df: pd.DataFrame,
    df_1h: Optional[pd.DataFrame] = None,
    hours_start: int = 3,
    hours_end: int = 20,
    vol_mult: float = 1.3,
    slope_min: float = 0.0001,
) -> pd.DataFrame:
    """EMA(9/21) crossover with RSI + volume + trading-hours + 1h trend filter."""
    df_s = df.copy()
    signals = pd.Series(0, index=df_s.index)
    in_hours = (df_s["hour"] >= hours_start) & (df_s["hour"] < hours_end)
    not_we = df_s["dow"] < 5
    vol_ok = df_s["vol_ratio"] >= vol_mult
    rsi_bull = (df_s["rsi"] >= 45) & (df_s["rsi"] <= 65)
    rsi_bear = (df_s["rsi"] >= 35) & (df_s["rsi"] <= 55)
    slope_ok = (
        df_s["ema_slope"] >= slope_min
        if slope_min > 0
        else pd.Series(True, index=df_s.index)
    )

    if df_1h is not None and not df_1h.empty:
        ema50_1h = compute_ema(df_1h["close"], 50)
        trend_1h = pd.DataFrame({"ema50_1h": ema50_1h, "close_1h": df_1h["close"]})
        trend_1h = trend_1h.reindex(df_s.index, method="ffill")
        trend_bull = trend_1h["close_1h"] > trend_1h["ema50_1h"]
        trend_bear = trend_1h["close_1h"] < trend_1h["ema50_1h"]
    else:
        ema50 = compute_ema(df_s["close"], 50)
        trend_bull = df_s["close"] > ema50
        trend_bear = df_s["close"] < ema50

    signals[df_s["cross_up"] & in_hours & not_we & vol_ok & rsi_bull & slope_ok & trend_bull] = 1
    signals[df_s["cross_down"] & in_hours & not_we & vol_ok & rsi_bear & slope_ok & trend_bear] = -1
    signals.iloc[:60] = 0
    df_s["signal"] = signals
    return df_s


def strategy_ichimoku(
    df: pd.DataFrame,
    hours_start: int = 3,
    hours_end: int = 20,
) -> pd.DataFrame:
    """Ichimoku Tenkan/Kijun cross above/below cloud."""
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    signals[df["ichi_bull"] & in_hours & not_we] = 1
    signals[df["ichi_bear"] & in_hours & not_we] = -1
    signals.iloc[:80] = 0
    df_s = df.copy()
    df_s["signal"] = signals
    return df_s


# ---------------------------------------------------------------------------
# Simulators (numpy-array-based for 10-20x speed vs iloc-per-row)
# ---------------------------------------------------------------------------

def _extract_arrays(df: pd.DataFrame) -> Tuple:
    """Extract OHLCV + indicator columns as numpy arrays for fast iteration."""
    opens  = df["open"].to_numpy(dtype=np.float64)
    highs  = df["high"].to_numpy(dtype=np.float64)
    lows   = df["low"].to_numpy(dtype=np.float64)
    closes = df["close"].to_numpy(dtype=np.float64)
    atrs   = df["atr"].to_numpy(dtype=np.float64)
    sigs   = df["signal"].to_numpy(dtype=np.float64)
    dows   = df["dow"].to_numpy(dtype=np.float64) if "dow" in df.columns else np.zeros(len(df))
    return opens, highs, lows, closes, atrs, sigs, dows


def simulate(
    df: pd.DataFrame,
    sl_mult: float = 1.0,
    tp_mult: float = 3.0,
    risk_pct: float = 0.10,
    leverage: float = 25,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
    trail_mult: float = 0,
) -> dict:
    """Lightweight event-driven simulator (numpy arrays). Returns summary metrics dict."""
    _, highs, lows, closes, atrs, sigs, dows = _extract_arrays(df)
    n = len(df)
    fee2 = (commission + slippage) * 2

    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    trade_count = 0
    position = None  # dict: side, entry, sl, tp, trail

    for i in range(2, n):
        h = highs[i]; lo = lows[i]; c = closes[i]
        sig_i = int(sigs[i - 1])
        atr_i = atrs[i - 1]
        dow_i = dows[i - 1]

        if position is not None:
            side = position["side"]
            sl   = position["sl"]
            tp   = position["tp"]

            if trail_mult > 0:
                trail_dist = atrs[i - 1] * trail_mult
                if side == 1:
                    new_sl = h - trail_dist
                    if new_sl > sl:
                        sl = new_sl
                        position["sl"] = sl
                else:
                    new_sl = lo + trail_dist
                    if new_sl < sl:
                        sl = new_sl
                        position["sl"] = sl

            hit_sl = (side == 1 and lo <= sl) or (side == -1 and h >= sl)
            hit_tp = ((side == 1 and h >= tp) or (side == -1 and lo <= tp)) if tp > 0 else False

            if hit_sl or hit_tp:
                entry  = position["entry"]
                exit_p = sl if hit_sl else tp
                pnl_pct = side * (exit_p - entry) / entry - fee2
                pnl = balance * risk_pct * leverage * pnl_pct / sl_mult
                if pnl < -balance * risk_pct * leverage:
                    pnl = -balance * risk_pct * leverage
                balance += pnl
                if balance > peak:
                    peak = balance
                dd = (peak - balance) / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd
                trade_count += 1
                if pnl > 0:
                    wins += 1
                    gross_profit += pnl
                else:
                    gross_loss += abs(pnl)
                position = None
                if balance <= 0:
                    break

        if position is None and sig_i != 0:
            if dow_i >= 5:
                continue
            if np.isnan(atr_i) or atr_i <= 0:
                continue
            entry_p = closes[i]
            sl_d = atr_i * sl_mult
            tp_d = atr_i * tp_mult if tp_mult > 0 else 0.0
            if sig_i == 1:
                sl_p = entry_p - sl_d
                tp_p = entry_p + tp_d if tp_d > 0 else 0.0
            else:
                sl_p = entry_p + sl_d
                tp_p = entry_p - tp_d if tp_d > 0 else 0.0
            position = {"side": sig_i, "entry": entry_p, "sl": sl_p, "tp": tp_p}

    pf = gross_profit / gross_loss if gross_loss > 0 else 0.0
    wr = wins / trade_count * 100 if trade_count > 0 else 0.0
    days = (df.index[-1] - df.index[0]).days
    yr = days / 365.25
    return {
        "trades": trade_count,
        "tr_yr": trade_count / yr if yr > 0 else 0,
        "wr": wr,
        "pf": pf,
        "dd": max_dd * 100,
        "balance": balance,
        "ret": (balance - 1000) / 10,
    }


def simulate_with_trades(
    df: pd.DataFrame,
    sl_mult: float = 1.0,
    tp_mult: float = 3.0,
    risk_pct: float = 0.10,
    leverage: float = 25,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
    trail_mult: float = 0,
    coin: str = "",
    account_balance: float = STARTING_BALANCE,
) -> Tuple[dict, List[dict]]:
    """Same logic as simulate() but records per-trade data with timestamps.

    Returns (metrics_dict, trade_records).
    Each trade record has: entry_time, exit_time, coin, side, pnl_dollar, reason.
    PnL is in dollars based on account_balance, risk_pct, leverage.
    """
    _, highs, lows, closes, atrs, sigs, dows = _extract_arrays(df)
    n = len(df)
    fee2 = (commission + slippage) * 2

    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    trade_count = 0
    trade_records: List[dict] = []
    position = None

    for i in range(2, n):
        h = highs[i]; lo = lows[i]; c = closes[i]
        sig_i = int(sigs[i - 1])
        atr_i = atrs[i - 1]
        dow_i = dows[i - 1]

        if position is not None:
            side = position["side"]
            sl   = position["sl"]
            tp   = position["tp"]

            if trail_mult > 0:
                trail_dist = atrs[i - 1] * trail_mult
                if side == 1:
                    new_sl = h - trail_dist
                    if new_sl > sl:
                        sl = new_sl
                        position["sl"] = sl
                else:
                    new_sl = lo + trail_dist
                    if new_sl < sl:
                        sl = new_sl
                        position["sl"] = sl

            hit_sl = (side == 1 and lo <= sl) or (side == -1 and h >= sl)
            hit_tp = ((side == 1 and h >= tp) or (side == -1 and lo <= tp)) if tp > 0 else False

            if hit_sl or hit_tp:
                entry  = position["entry"]
                exit_p = sl if hit_sl else tp
                pnl_pct = side * (exit_p - entry) / entry - fee2

                # Normalised internal pnl
                pnl_norm = balance * risk_pct * leverage * pnl_pct / sl_mult
                if pnl_norm < -balance * risk_pct * leverage:
                    pnl_norm = -balance * risk_pct * leverage
                balance += pnl_norm
                if balance > peak:
                    peak = balance
                dd = (peak - balance) / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd
                trade_count += 1
                if pnl_norm > 0:
                    wins += 1
                    gross_profit += pnl_norm
                else:
                    gross_loss += abs(pnl_norm)

                # Dollar pnl for portfolio reports
                pnl_dollar = account_balance * risk_pct * leverage * pnl_pct / sl_mult
                if pnl_dollar < -account_balance * risk_pct * leverage:
                    pnl_dollar = -account_balance * risk_pct * leverage

                trade_records.append({
                    "entry_time": df.index[position["entry_bar"]],
                    "exit_time":  df.index[i],
                    "coin":       coin,
                    "side":       "long" if side == 1 else "short",
                    "pnl_dollar": round(pnl_dollar, 2),
                    "reason":     "sl" if hit_sl else "tp",
                })
                position = None
                if balance <= 0:
                    break

        if position is None and sig_i != 0:
            if dow_i >= 5:
                continue
            if np.isnan(atr_i) or atr_i <= 0:
                continue
            entry_p = closes[i]
            sl_d = atr_i * sl_mult
            tp_d = atr_i * tp_mult if tp_mult > 0 else 0.0
            if sig_i == 1:
                sl_p = entry_p - sl_d
                tp_p = entry_p + tp_d if tp_d > 0 else 0.0
            else:
                sl_p = entry_p + sl_d
                tp_p = entry_p - tp_d if tp_d > 0 else 0.0
            position = {
                "side":      sig_i,
                "entry":     entry_p,
                "sl":        sl_p,
                "tp":        tp_p,
                "entry_bar": i,
            }

    pf = gross_profit / gross_loss if gross_loss > 0 else 0.0
    wr = wins / trade_count * 100 if trade_count > 0 else 0.0
    days = (df.index[-1] - df.index[0]).days
    yr = days / 365.25
    metrics = {
        "trades": trade_count,
        "tr_yr":  trade_count / yr if yr > 0 else 0,
        "wr":     wr,
        "pf":     pf,
        "dd":     max_dd * 100,
        "balance": balance,
        "ret":    (balance - 1000) / 10,
    }
    return metrics, trade_records


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_coin_data(prefix: str) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Load 15m and 1h data for a coin prefix. Returns (df_15m, df_1h) or None if missing."""
    df_15m = None
    df_1h = None
    for period in ["2y", "5y"]:
        if df_15m is None:
            p = os.path.join(DATA_DIR, f"{prefix}_15m_{period}.csv")
            if os.path.exists(p):
                try:
                    df_15m = add_indicators(load_ohlcv(p))
                except Exception:
                    pass
        if df_1h is None:
            p = os.path.join(DATA_DIR, f"{prefix}_1h_{period}.csv")
            if os.path.exists(p):
                try:
                    df_1h = add_indicators(load_ohlcv(p))
                except Exception:
                    pass
    return df_15m, df_1h


def discover_all_prefixes() -> List[str]:
    """Discover all unique coin prefixes that have at least 1h data."""
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*_1h_2y.csv")))
    prefixes = []
    for f in files:
        base = os.path.basename(f)
        prefix = base.replace("_1h_2y.csv", "")
        # Skip non-ASCII prefixes (corrupt filenames)
        try:
            prefix.encode("ascii")
        except UnicodeEncodeError:
            continue
        # Skip gold/forex
        if prefix in ("xauusd", "xauusdt", "xagusdt", "xptusdt", "xplusdt"):
            continue
        prefixes.append(prefix)
    return prefixes


# ---------------------------------------------------------------------------
# Part 1: All-coins sweep
# ---------------------------------------------------------------------------

def _apply_regime_filter(df_sig: pd.DataFrame) -> pd.DataFrame:
    """Zero out signals in ranging/low-volatility regimes (vectorised). Returns copy."""
    atr_ma = df_sig["atr"].rolling(50).mean()
    vol_ma50 = df_sig["vol_ratio"].rolling(50).mean()
    ranging = (df_sig["atr"] < atr_ma * 0.8) & (df_sig["vol_ratio"] < vol_ma50 * 0.8)
    out = df_sig.copy()
    out.loc[ranging, "signal"] = 0
    return out


def _coin_name(prefix: str) -> str:
    return prefix.replace("usdt", "").upper()


def run_all_coins_sweep(min_pf: float = 1.2, min_trades: int = 15) -> List[dict]:
    """Run best-strategy sweep across all coins. Returns list of result dicts.

    Optimised: pre-loads all data first, then runs signal+simulate in one pass.
    """
    prefixes = discover_all_prefixes()
    total = len(prefixes)

    strategy_specs = [
        # (name, timeframe, fn, fn_kwargs, sim_kwargs)
        ("EMA(9/21)-std",  "15m", strategy_ema_cross,  {"slope_min": 0.0002}, {"sl_mult": 1.0, "tp_mult": 3.0}),
        ("EMA(9/21)-meme", "15m", strategy_ema_cross,  {"slope_min": 0.0001}, {"sl_mult": 1.0, "tp_mult": 3.0}),
        ("Ichimoku",       "1h",  strategy_ichimoku,   {},                    {"sl_mult": 2.0, "tp_mult": 5.0}),
        ("Ichi+Trail",     "1h",  strategy_ichimoku,   {},                    {"sl_mult": 2.5, "tp_mult": 0,   "trail_mult": 3.0}),
    ]

    # ---- Phase 1: load + compute indicators for all coins ----
    print(f"  Loading {total} coins", end="", flush=True)
    loaded_15m: Dict[str, pd.DataFrame] = {}
    loaded_1h: Dict[str, pd.DataFrame] = {}
    dot_interval = max(1, total // 40)

    for idx, prefix in enumerate(prefixes):
        if idx % dot_interval == 0:
            print(".", end="", flush=True)
        name = _coin_name(prefix)
        for period in ["2y", "5y"]:
            if name not in loaded_15m:
                p = os.path.join(DATA_DIR, f"{prefix}_15m_{period}.csv")
                if os.path.exists(p):
                    try:
                        loaded_15m[name] = add_indicators(load_ohlcv(p))
                    except Exception:
                        pass
            if name not in loaded_1h:
                p = os.path.join(DATA_DIR, f"{prefix}_1h_{period}.csv")
                if os.path.exists(p):
                    try:
                        loaded_1h[name] = add_indicators(load_ohlcv(p))
                    except Exception:
                        pass

    # Precompute regime masks (1h is shared across ichi strategies — compute once)
    regime_masked_1h: Dict[str, pd.DataFrame] = {}
    regime_masked_15m: Dict[str, pd.DataFrame] = {}

    for name, df in loaded_1h.items():
        atr_ma = df["atr"].rolling(50).mean()
        vol_ma50 = df["vol_ratio"].rolling(50).mean()
        mask = (df["atr"] < atr_ma * 0.8) & (df["vol_ratio"] < vol_ma50 * 0.8)
        regime_masked_1h[name] = mask

    for name, df in loaded_15m.items():
        atr_ma = df["atr"].rolling(50).mean()
        vol_ma50 = df["vol_ratio"].rolling(50).mean()
        mask = (df["atr"] < atr_ma * 0.8) & (df["vol_ratio"] < vol_ma50 * 0.8)
        regime_masked_15m[name] = mask

    print()
    print(f"  Loaded: {len(loaded_15m)} with 15m data, {len(loaded_1h)} with 1h data")

    # ---- Phase 2: sweep strategies ----
    best_per_coin: Dict[str, dict] = {}
    all_names = set(loaded_15m) | set(loaded_1h)
    total_names = len(all_names)
    print(f"  Sweeping {total_names} coins", end="", flush=True)
    dot_interval2 = max(1, total_names // 40)

    for idx, name in enumerate(sorted(all_names)):
        if idx % dot_interval2 == 0:
            print(".", end="", flush=True)

        prefix = name.lower() + "usdt"
        coin_best: Optional[dict] = None

        for strat_name, tf, strat_fn, strat_kw, sim_kw in strategy_specs:
            df = loaded_15m.get(name) if tf == "15m" else loaded_1h.get(name)
            if df is None:
                continue
            try:
                kw = dict(strat_kw)
                if tf == "15m" and name in loaded_1h:
                    kw["df_1h"] = loaded_1h[name]
                df_sig = strat_fn(df, **kw)

                # Apply precomputed regime mask
                mask = regime_masked_15m.get(name) if tf == "15m" else regime_masked_1h.get(name)
                if mask is not None:
                    # Reindex mask to df_sig (signal fn returns a copy with same index)
                    df_sig = df_sig.copy()
                    df_sig.loc[mask.reindex(df_sig.index, fill_value=False), "signal"] = 0

                r = simulate(df_sig, **sim_kw)
            except Exception:
                continue

            result = {
                "coin": name,
                "prefix": prefix,
                "strategy": strat_name,
                "pf": round(r["pf"], 3),
                "trades": r["trades"],
                "tr_yr": round(r["tr_yr"], 1),
                "wr": round(r["wr"], 1),
                "dd": round(r["dd"], 1),
                "ret": round(r["ret"], 1),
                "status": "PASS" if r["pf"] >= min_pf and r["trades"] >= min_trades else "FAIL",
            }

            if coin_best is None or result["pf"] > coin_best["pf"]:
                coin_best = result

        if coin_best is not None:
            best_per_coin[name] = coin_best

    print()
    results = sorted(best_per_coin.values(), key=lambda x: -x["pf"])
    return results


def print_part1(results: List[dict]) -> None:
    W = 84
    print()
    print("=" * W)
    print("PART 1: ALL COINS BACKTEST RESULTS (best strategy per coin)")
    print("=" * W)
    hdr = f"{'#':>4}  {'Coin':<12} {'Strategy':<16} {'PF':>6}  {'Trades':>6}  {'Tr/yr':>5}  {'WR%':>5}  {'DD%':>6}  {'Ret%':>7}  {'Status':<6}"
    print(hdr)
    print("-" * W)
    for rank, r in enumerate(results, 1):
        ret_str = f"{'+' if r['ret'] >= 0 else ''}{r['ret']:.0f}%"
        print(
            f"{rank:>4}  {r['coin']:<12} {r['strategy']:<16} {r['pf']:>6.2f}"
            f"  {r['trades']:>6}  {r['tr_yr']:>4.0f}/y"
            f"  {r['wr']:>5.1f}%  {r['dd']:>5.1f}%  {ret_str:>7}  {r['status']:<6}"
        )
    print("-" * W)
    passes = sum(1 for r in results if r["status"] == "PASS")
    fails = len(results) - passes
    print(f"PASS: {passes} coins (PF >= 1.2, trades >= 15)")
    print(f"FAIL: {fails} coins")


# ---------------------------------------------------------------------------
# Part 2 & 3: Portfolio PnL reports
# ---------------------------------------------------------------------------

def build_portfolio_trades(verbose: bool = False) -> List[dict]:
    """Run all 19 portfolio bots and collect trade records."""
    all_trades: List[dict] = []

    for bot in PORTFOLIO:
        coin = bot["coin"]
        prefix = bot["prefix"]
        strategy = bot["strategy"]
        risk = bot["risk"]
        sl = bot["sl"]
        tp = bot["tp"]
        trail = bot["trail"]

        df_15m, df_1h = load_coin_data(prefix)

        if strategy == "ema":
            df = df_15m
            if df is None:
                if verbose:
                    print(f"  WARN: no 15m data for {coin} ({prefix})")
                continue
            kw: dict = {"slope_min": 0.0001}
            if df_1h is not None:
                kw["df_1h"] = df_1h
            try:
                df_sig = strategy_ema_cross(df, **kw)
            except Exception as e:
                if verbose:
                    print(f"  WARN: signal error {coin}: {e}")
                continue
            sim_kw = {"sl_mult": sl, "tp_mult": tp, "trail_mult": trail}
        else:  # ichi
            df = df_1h
            if df is None:
                if verbose:
                    print(f"  WARN: no 1h data for {coin} ({prefix})")
                continue
            try:
                df_sig = strategy_ichimoku(df)
            except Exception as e:
                if verbose:
                    print(f"  WARN: signal error {coin}: {e}")
                continue
            sim_kw = {"sl_mult": sl, "tp_mult": tp, "trail_mult": trail}

        # Regime filter
        try:
            atr_ma = df_sig["atr"].rolling(50).mean()
            vol_ma50 = df_sig["vol_ratio"].rolling(50).mean()
            ranging = (df_sig["atr"] < atr_ma * 0.8) & (df_sig["vol_ratio"] < vol_ma50 * 0.8)
            df_sig = df_sig.copy()
            df_sig.loc[ranging, "signal"] = 0
        except Exception:
            pass

        try:
            _, trades = simulate_with_trades(
                df_sig,
                risk_pct=risk,
                leverage=LEVERAGE,
                coin=coin,
                account_balance=STARTING_BALANCE,
                **sim_kw,
            )
            all_trades.extend(trades)
        except Exception as e:
            if verbose:
                print(f"  WARN: simulate error {coin}: {e}")
            continue

    return all_trades


def print_part2(all_trades: List[dict]) -> None:
    """Print monthly PnL table for last 12 months."""
    if not all_trades:
        print("  No trade data available.")
        return

    now = pd.Timestamp.utcnow()
    cutoff_12m = now - pd.DateOffset(months=12)
    trades_df = pd.DataFrame(all_trades)
    trades_df["exit_time"] = pd.to_datetime(trades_df["exit_time"], utc=True)

    recent = trades_df[trades_df["exit_time"] >= cutoff_12m].copy()
    if recent.empty:
        # Try without timezone filter (data may have no tz)
        trades_df["exit_time"] = trades_df["exit_time"].dt.tz_localize(None)
        recent = trades_df[trades_df["exit_time"] >= cutoff_12m.tz_localize(None)].copy()

    if recent.empty:
        print("  No trades in last 12 months.")
        return

    recent["month"] = recent["exit_time"].dt.to_period("M")

    coins = [b["coin"] for b in PORTFOLIO]
    months = sorted(recent["month"].unique())

    # Build monthly coin pivot
    pivot = recent.pivot_table(
        index="month", columns="coin", values="pnl_dollar", aggfunc="sum", fill_value=0
    )

    # Column order matches portfolio order
    ordered_coins = [c for c in coins if c in pivot.columns]
    pivot = pivot.reindex(columns=ordered_coins, fill_value=0)

    # Compute totals and cumulative balance
    pivot["Total"] = pivot.sum(axis=1)
    balance = STARTING_BALANCE
    cum_balances = []
    peaks: List[float] = []
    peak = STARTING_BALANCE
    dds = []
    for _, row in pivot.iterrows():
        balance += row["Total"]
        peak = max(peak, balance)
        peaks.append(peak)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0.0
        dds.append(dd)
        cum_balances.append(balance)
    pivot["CumBal"] = cum_balances
    pivot["DD%"] = dds

    W = max(100, 30 + len(ordered_coins) * 8)
    print()
    print("=" * W)
    print(f"PART 2: PORTFOLIO MONTHLY PnL (19 bots, last 12 months)")
    print("=" * W)
    print(f"Starting balance: ${STARTING_BALANCE:,.0f}")
    print()

    # Header — split coins into EMA group and Ichi group
    ema_coins = [c for c in ordered_coins if any(b["coin"] == c and b["strategy"] == "ema" for b in PORTFOLIO)]
    ichi_coins = [c for c in ordered_coins if c not in ema_coins]

    header_coins = "  ".join(f"{c:>7}" for c in ordered_coins)
    print(f"{'Month':<9}  {header_coins}  {'Total':>8}  {'CumBal':>10}  {'DD%':>5}")
    print("-" * W)

    for month, row in pivot.iterrows():
        coin_vals = "  ".join(
            f"{row.get(c, 0):>+7.0f}" for c in ordered_coins
        )
        total = row["Total"]
        cum = row["CumBal"]
        dd = row["DD%"]
        print(f"{str(month):<9}  {coin_vals}  {total:>+8.0f}  ${cum:>9,.0f}  {dd:>5.1f}%")

    print("-" * W)
    total_pnl = sum(pivot["Total"])
    final_bal = STARTING_BALANCE + total_pnl
    print(f"{'12m Total':<9}  {'':>{len(header_coins)}}  {total_pnl:>+8.0f}  ${final_bal:>9,.0f}")


def print_part3(all_trades: List[dict]) -> None:
    """Print daily PnL table for last 2 months."""
    if not all_trades:
        print("  No trade data available.")
        return

    now = pd.Timestamp.utcnow()
    cutoff_2m = now - pd.DateOffset(months=2)

    trades_df = pd.DataFrame(all_trades)
    trades_df["exit_time"] = pd.to_datetime(trades_df["exit_time"], utc=True)

    try:
        recent = trades_df[trades_df["exit_time"] >= cutoff_2m].copy()
    except TypeError:
        trades_df["exit_time"] = trades_df["exit_time"].dt.tz_localize(None)
        recent = trades_df[trades_df["exit_time"] >= cutoff_2m.tz_localize(None)].copy()

    if recent.empty:
        print("  No trades in last 2 months.")
        return

    recent["date"] = recent["exit_time"].dt.date

    # Build all-days range
    date_range = pd.date_range(
        start=cutoff_2m.date(), end=now.date(), freq="D"
    )

    # Daily aggregation
    daily = (
        recent.groupby("date")
        .agg(
            trades=("pnl_dollar", "count"),
            wins=("pnl_dollar", lambda x: (x > 0).sum()),
            losses=("pnl_dollar", lambda x: (x < 0).sum()),
            total_pnl=("pnl_dollar", "sum"),
        )
        .reindex([d.date() for d in date_range], fill_value=0)
    )

    # Cumulative balance starting from the balance at cutoff_2m
    # Compute balance at cutoff from all trades before cutoff
    trades_df_all = pd.DataFrame(all_trades)
    trades_df_all["exit_time"] = pd.to_datetime(trades_df_all["exit_time"], utc=True)
    try:
        before = trades_df_all[trades_df_all["exit_time"] < cutoff_2m]
    except TypeError:
        trades_df_all["exit_time"] = trades_df_all["exit_time"].dt.tz_localize(None)
        before = trades_df_all[trades_df_all["exit_time"] < cutoff_2m.tz_localize(None)]

    balance_at_cutoff = STARTING_BALANCE + before["pnl_dollar"].sum()

    balance = balance_at_cutoff
    peak = balance
    cum_bals = []
    dds = []
    for _, row in daily.iterrows():
        balance += row["total_pnl"]
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0.0
        cum_bals.append(balance)
        dds.append(dd)
    daily["cum_bal"] = cum_bals
    daily["dd_pct"] = dds

    # Coins active per day
    coin_by_day = (
        recent.groupby("date")["coin"]
        .apply(lambda x: x.tolist())
        .to_dict()
    )
    pnl_by_day = (
        recent.groupby(["date", "coin"])["pnl_dollar"]
        .sum()
        .to_dict()
    )

    W = 100
    print()
    print("=" * W)
    print(f"PART 3: PORTFOLIO DAILY PnL (last 2 months)")
    print("=" * W)
    print(
        f"{'Date':<12}  {'Trades':>6}  {'Win':>4}  {'Loss':>4}  "
        f"{'Total PnL':>10}  {'Cum.Bal':>10}  {'DD%':>5}  Coins with trades"
    )
    print("-" * W)

    current_month = None
    month_trades = 0
    month_wins = 0
    month_losses = 0
    month_pnl = 0.0
    month_rows = []

    def flush_month(month_label: str) -> None:
        if current_month is not None:
            print(
                f"  >> {month_label} monthly: {month_trades} trades, "
                f"{month_wins}W/{month_losses}L, PnL: {month_pnl:>+,.0f}"
            )
            print()

    for date, row in daily.iterrows():
        m = date.strftime("%Y-%m")
        if m != current_month:
            if current_month is not None:
                prev_label = current_month
                print(
                    f"  --- {prev_label}: {month_trades} trades, "
                    f"{month_wins}W/{month_losses}L, PnL: {month_pnl:>+,.2f} ---"
                )
                print()
            current_month = m
            month_trades = 0
            month_wins = 0
            month_losses = 0
            month_pnl = 0.0

        month_trades += int(row["trades"])
        month_wins += int(row["wins"])
        month_losses += int(row["losses"])
        month_pnl += row["total_pnl"]

        coins_today = coin_by_day.get(date, [])
        if coins_today:
            coin_str_parts = []
            seen = set()
            for c in coins_today:
                if c not in seen:
                    seen.add(c)
                    pnl = pnl_by_day.get((date, c), 0)
                    coin_str_parts.append(f"{c}({pnl:+.0f})")
            coin_str = " ".join(coin_str_parts)
        else:
            coin_str = "-"

        pnl_str = f"{row['total_pnl']:>+,.2f}" if row["trades"] > 0 else f"{'$0':>10}"
        print(
            f"{str(date):<12}  {int(row['trades']):>6}  {int(row['wins']):>4}  "
            f"{int(row['losses']):>4}  {row['total_pnl']:>+10,.2f}  "
            f"${row['cum_bal']:>9,.0f}  {row['dd_pct']:>5.1f}%  {coin_str}"
        )

    # Flush last month
    if current_month is not None:
        print()
        print(
            f"  --- {current_month}: {month_trades} trades, "
            f"{month_wins}W/{month_losses}L, PnL: {month_pnl:>+,.2f} ---"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Portfolio backtest report")
    p.add_argument("--no-sweep", action="store_true", help="Skip Part 1 all-coins sweep")
    p.add_argument("--sweep-only", action="store_true", help="Only run Part 1 sweep")
    p.add_argument("--min-pf", type=float, default=1.2)
    p.add_argument("--min-trades", type=int, default=15)
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # ------------------------------------------------------------------ Part 1
    if not args.no_sweep:
        print("\nPart 1: Running all-coins sweep...")
        results = run_all_coins_sweep(min_pf=args.min_pf, min_trades=args.min_trades)
        print_part1(results)
    else:
        print("\n[Part 1 skipped — use without --no-sweep to include]")

    if args.sweep_only:
        return

    # ------------------------------------------------------------------ Parts 2 & 3
    print("\nParts 2 & 3: Running portfolio simulations (19 bots)...", flush=True)
    all_trades = build_portfolio_trades(verbose=False)
    print(f"  Collected {len(all_trades)} trade records across all bots.")

    print_part2(all_trades)
    print_part3(all_trades)

    print()
    print("=" * 84)
    print("Report complete.")
    print("=" * 84)


if __name__ == "__main__":
    main()
