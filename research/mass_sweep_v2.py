"""
mass_sweep_v2.py — Extended strategy sweep with 6 new variants.

Tests coins that failed the original mass_sweep.py variants:
  1. long_only_ichi   — Long-Only Ichimoku (9/26/52), no shorts
  2. fast_ichi        — Faster Ichimoku (7/22/44)
  3. slow_ichi        — Slower Ichimoku (13/34/52)
  4. momentum_roc     — ROC(10) > 1.5% + close > EMA(21), long only
  5. ema_12_26        — EMA(12/26) with 1h EMA50 trend filter
  6. adaptive_sltp    — Standard Ichimoku 9/26/52, sweep SL×TP 3×3=9 combos

Skips coins already deployed in the 7-bot portfolio.

Usage:
    python research/mass_sweep_v2.py --variant long_only_ichi
    python research/mass_sweep_v2.py --variant all
    python research/mass_sweep_v2.py --coins bnbusdt,linkusdt --variant fast_ichi
    python research/mass_sweep_v2.py --min-pf 1.2 --min-trades 15
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from itertools import product
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import compute_atr, compute_ema, compute_rsi, compute_volume_ma

DATA_DIR = "/Users/iceai/Work/ccbt/data"

# Coins already deployed — skip them in the sweep
DEPLOYED_PREFIXES = {
    "btcusdt", "dogeusdt", "arbusdt", "wifusdt",
    "avaxusdt", "nearusdt", "solusdt",
    "1000shibusdt", "animeusdt", "arcusdt", "athusdt",
    "berausdt", "gunusdt", "injusdt", "trumpusdt", "trxusdt",
    "xlmusdt", "zetausdt",
}

ALL_VARIANTS = ["long_only_ichi", "fast_ichi", "slow_ichi", "momentum_roc", "ema_12_26", "adaptive_sltp"]


# ---------------------------------------------------------------------------
# Shared indicator builders
# ---------------------------------------------------------------------------

def _build_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add ATR, volume MA, hour/weekday columns used by all variants."""
    df = df.copy()
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["vol_ma"] = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / df["vol_ma"].clip(lower=1e-10)
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek
    return df


def add_ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52) -> pd.DataFrame:
    """Add Ichimoku cloud columns with configurable periods."""
    df = df.copy()
    df["tenkan"] = (df["high"].rolling(tenkan).max() + df["low"].rolling(tenkan).min()) / 2
    df["kijun"] = (df["high"].rolling(kijun).max() + df["low"].rolling(kijun).min()) / 2
    span_a = ((df["tenkan"] + df["kijun"]) / 2).shift(kijun)
    span_b = ((df["high"].rolling(senkou_b).max() + df["low"].rolling(senkou_b).min()) / 2).shift(kijun)
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
    return df


def add_ema_indicators(df: pd.DataFrame, fast: int = 12, slow: int = 26) -> pd.DataFrame:
    """Add EMA crossover columns."""
    df = df.copy()
    df["ema_f"] = compute_ema(df["close"], fast)
    df["ema_s"] = compute_ema(df["close"], slow)
    df["rsi"] = compute_rsi(df["close"], 14)
    df["ema_slope"] = (df["ema_f"] - df["ema_f"].shift(1)).abs() / df["close"].shift(1).clip(lower=1e-10)
    df["cross_up"] = (df["ema_f"] > df["ema_s"]) & (df["ema_f"].shift(1) <= df["ema_s"].shift(1))
    df["cross_down"] = (df["ema_f"] < df["ema_s"]) & (df["ema_f"].shift(1) >= df["ema_s"].shift(1))
    return df


# ---------------------------------------------------------------------------
# Simulator (same as mass_sweep.py — copy to avoid import coupling)
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    sl_mult: float = 1.0,
    tp_mult: float = 3.0,
    risk_pct: float = 0.10,
    leverage: float = 25,
    commission: float = 0.00055,
    slippage: float = 0.0002,
    trail_mult: float = 0,
) -> dict:
    """Event-driven simulator.

    df must have a 'signal' column (1=long, -1=short, 0=none).
    Returns metrics dict with trades, tr_yr, wr, pf, dd, balance, ret.
    """
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades: list = []
    position = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]

        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]

            if trail_mult > 0 and position.get("trail"):
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
            hit_tp = (
                (side == 1 and row["high"] >= tp) or (side == -1 and row["low"] <= tp)
            ) if tp > 0 else False

            if hit_sl or hit_tp:
                exit_p = sl if hit_sl else tp
                pnl_pct = side * (exit_p - entry) / entry - (commission + slippage) * 2
                pnl = balance * risk_pct * leverage * pnl_pct / sl_mult
                pnl = max(pnl, -balance * risk_pct * leverage)
                balance += pnl
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                trades.append({"pnl": pnl, "reason": "sl" if hit_sl else "tp"})
                position = None
                if balance <= 0:
                    break

        if position is None and sig_row.get("signal", 0) != 0:
            if (
                "dow" in sig_row.index
                and not pd.isna(sig_row.get("dow"))
                and sig_row["dow"] >= 5
            ):
                continue
            atr = sig_row["atr"]
            if pd.isna(atr) or atr <= 0:
                continue
            entry_p = row["close"]
            side = int(sig_row["signal"])
            sl_d = atr * sl_mult
            tp_d = atr * tp_mult if tp_mult > 0 else 0
            if side == 1:
                sl_p = entry_p - sl_d
                tp_p = entry_p + tp_d if tp_d > 0 else 0
            else:
                sl_p = entry_p + sl_d
                tp_p = entry_p - tp_d if tp_d > 0 else 0
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
    days = (df.index[-1] - df.index[0]).days
    yr = days / 365.25
    return {
        "trades": total,
        "tr_yr": total / yr if yr > 0 else 0,
        "wr": wr,
        "pf": pf,
        "dd": max_dd * 100,
        "balance": balance,
        "ret": (balance - 1000) / 10,
    }


# ---------------------------------------------------------------------------
# Strategy variants
# ---------------------------------------------------------------------------

def strategy_long_only_ichi(df: pd.DataFrame, hours_start: int = 3, hours_end: int = 20) -> pd.DataFrame:
    """Long-Only Ichimoku (9/26/52). No short positions.

    Same entry logic as standard Ichimoku but only fires signal=1.
    SL 2.0×ATR, TP 5.0×ATR (applied in simulate() via caller kwargs).
    """
    df = df.copy()
    df = add_ichimoku(df, tenkan=9, kijun=26, senkou_b=52)
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    signals[df["ichi_bull"] & in_hours & not_we] = 1
    # No shorts
    signals.iloc[:80] = 0
    df["signal"] = signals
    return df


def strategy_fast_ichi(df: pd.DataFrame, hours_start: int = 3, hours_end: int = 20) -> pd.DataFrame:
    """Faster Ichimoku (7/22/44). Both long and short.

    Faster tenkan/kijun responds more quickly to momentum shifts.
    Cloud shift = kijun period = 22.
    """
    df = df.copy()
    df = add_ichimoku(df, tenkan=7, kijun=22, senkou_b=44)
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    signals[df["ichi_bull"] & in_hours & not_we] = 1
    signals[df["ichi_bear"] & in_hours & not_we] = -1
    # Warmup: max(44 + 22 shift, 80) = 80
    signals.iloc[:80] = 0
    df["signal"] = signals
    return df


def strategy_slow_ichi(df: pd.DataFrame, hours_start: int = 3, hours_end: int = 20) -> pd.DataFrame:
    """Slower Ichimoku (13/34/52). Both long and short.

    Slower periods reduce false crossovers in choppy markets.
    Cloud shift = kijun period = 34.
    """
    df = df.copy()
    df = add_ichimoku(df, tenkan=13, kijun=34, senkou_b=52)
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    signals[df["ichi_bull"] & in_hours & not_we] = 1
    signals[df["ichi_bear"] & in_hours & not_we] = -1
    # Warmup: 52 + 34 = 86 bars
    signals.iloc[:90] = 0
    df["signal"] = signals
    return df


def strategy_momentum_roc(df: pd.DataFrame, hours_start: int = 3, hours_end: int = 20) -> pd.DataFrame:
    """Momentum ROC Long-Only.

    Entry: ROC(10) > 1.5% AND close > EMA(21).
    Long only. Warmup 50 bars.
    """
    df = df.copy()
    df["ema21"] = compute_ema(df["close"], 21)
    df["roc10"] = (df["close"] - df["close"].shift(10)) / df["close"].shift(10).clip(lower=1e-10) * 100

    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5

    roc_ok = df["roc10"] > 1.5
    above_ema = df["close"] > df["ema21"]

    # Only fire on transition from not-OK to OK (entry signal, not hold)
    cond_now = roc_ok & above_ema & in_hours & not_we
    cond_prev = (df["roc10"].shift(1) > 1.5) & (df["close"].shift(1) > df["ema21"].shift(1))
    # Entry = condition newly true (was false last bar)
    entry_signal = cond_now & ~cond_prev

    signals[entry_signal] = 1
    signals.iloc[:50] = 0
    df["signal"] = signals
    return df


def strategy_ema_12_26(
    df: pd.DataFrame,
    df_1h: Optional[pd.DataFrame] = None,
    hours_start: int = 3,
    hours_end: int = 20,
    vol_mult: float = 1.3,
    slope_min: float = 0.0002,
) -> pd.DataFrame:
    """EMA(12/26) crossover with RSI + volume + 1h EMA50 trend filter.

    Drop-in replacement for EMA(9/21) variant from mass_sweep.py.
    Same RSI zones (long 45-65, short 35-55), same volume threshold.
    """
    df = df.copy()
    df = add_ema_indicators(df, fast=12, slow=26)

    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    vol_ok = df["vol_ratio"] >= vol_mult
    rsi_bull = (df["rsi"] >= 45) & (df["rsi"] <= 65)
    rsi_bear = (df["rsi"] >= 35) & (df["rsi"] <= 55)
    slope_ok = df["ema_slope"] >= slope_min

    if df_1h is not None and not df_1h.empty:
        ema50_1h = compute_ema(df_1h["close"], 50)
        trend_1h = pd.DataFrame({"ema50_1h": ema50_1h, "close_1h": df_1h["close"]})
        trend_1h = trend_1h.reindex(df.index, method="ffill")
        trend_bull = trend_1h["close_1h"] > trend_1h["ema50_1h"]
        trend_bear = trend_1h["close_1h"] < trend_1h["ema50_1h"]
    else:
        ema50 = compute_ema(df["close"], 50)
        trend_bull = df["close"] > ema50
        trend_bear = df["close"] < ema50

    signals[df["cross_up"] & in_hours & not_we & vol_ok & rsi_bull & slope_ok & trend_bull] = 1
    signals[df["cross_down"] & in_hours & not_we & vol_ok & rsi_bear & slope_ok & trend_bear] = -1
    # Warmup: EMA(26) needs ~60 bars to converge
    signals.iloc[:60] = 0
    df["signal"] = signals
    return df


def strategy_adaptive_sltp(
    df: pd.DataFrame,
    hours_start: int = 3,
    hours_end: int = 20,
) -> Tuple[pd.DataFrame, List[dict]]:
    """Standard Ichimoku (9/26/52) with 3×3 SL/TP sweep.

    Returns (df_with_signals, list_of_sim_results).
    Each result dict includes sl_mult and tp_mult so the best can be identified.
    """
    df = df.copy()
    df = add_ichimoku(df, tenkan=9, kijun=26, senkou_b=52)
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    signals[df["ichi_bull"] & in_hours & not_we] = 1
    signals[df["ichi_bear"] & in_hours & not_we] = -1
    signals.iloc[:80] = 0
    df["signal"] = signals

    sl_values = [1.5, 2.0, 2.5]
    tp_values = [3.0, 4.0, 5.0]
    combo_results = []
    for sl_m, tp_m in product(sl_values, tp_values):
        r = simulate(df, sl_mult=sl_m, tp_mult=tp_m)
        r["sl_mult"] = sl_m
        r["tp_mult"] = tp_m
        combo_results.append(r)

    return df, combo_results


# ---------------------------------------------------------------------------
# Coin discovery (same logic as mass_sweep.py)
# ---------------------------------------------------------------------------

def discover_coins(coins_arg: Optional[List[str]]) -> List[str]:
    """Return list of lowercase prefixes.

    Priority: CLI --coins > data/liquid_coins.json > glob scan.
    Filters out already-deployed coins.
    """
    if coins_arg:
        return [c.lower().strip() for c in coins_arg if c.strip()]

    liquid_path = os.path.join(DATA_DIR, "liquid_coins.json")
    if os.path.exists(liquid_path):
        with open(liquid_path) as f:
            raw = json.load(f)
        if raw and isinstance(raw[0], dict):
            result = []
            for entry in raw:
                if "prefix" in entry:
                    result.append(entry["prefix"].lower())
                else:
                    sym = entry.get("symbol", "")
                    result.append(sym.lower().replace("/", "").replace(":", "").replace("usdt", "usdt", 1))
            return [p for p in result if p not in DEPLOYED_PREFIXES]
        return [s.lower().replace("/", "").replace(":", "") for s in raw if s.lower() not in DEPLOYED_PREFIXES]

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
# Data loading helper
# ---------------------------------------------------------------------------

def _load_coin_data(
    prefix: str,
    loaded_15m: Dict[str, pd.DataFrame],
    loaded_1h: Dict[str, pd.DataFrame],
) -> str:
    """Load 15m and 1h CSVs for a coin prefix into the given dicts.

    Returns the coin name (e.g. 'BNB') or empty string on failure.
    """
    name = prefix.replace("usdt", "").upper()
    for suffix, store in [("15m", loaded_15m), ("1h", loaded_1h)]:
        for period in ["2y", "5y"]:
            fpath = os.path.join(DATA_DIR, f"{prefix}_{suffix}_{period}.csv")
            if os.path.exists(fpath):
                try:
                    raw = load_ohlcv(fpath)
                    store[name] = _build_base_indicators(raw)
                    break
                except Exception as e:
                    print(f"  WARN: could not load {fpath}: {e}")
    return name


# ---------------------------------------------------------------------------
# Winner append helper
# ---------------------------------------------------------------------------

def _append_winners(new_winners: List[dict]) -> None:
    """Append winners to data/sweep_v2_winners.json (creates if missing)."""
    out_path = os.path.join(DATA_DIR, "sweep_v2_winners.json")
    existing: List[dict] = []
    if os.path.exists(out_path):
        try:
            with open(out_path) as f:
                existing = json.load(f)
        except json.JSONDecodeError:
            existing = []
    existing.extend(new_winners)
    with open(out_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"  Appended {len(new_winners)} winner(s) to {out_path}")


# ---------------------------------------------------------------------------
# Per-variant sweep runners
# ---------------------------------------------------------------------------

def _print_row(name: str, strat: str, r: dict, is_winner: bool) -> None:
    marker = " *" if is_winner else ""
    if r["trades"] >= 5:
        print(
            f"{name:<8} {strat:<20} {r['trades']:>6} {r['tr_yr']:>5.0f}/yr"
            f" {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%{marker}"
        )


def run_ichimoku_variant(
    coins: List[str],
    variant: str,
    strat_fn,
    sim_kwargs: dict,
    min_pf: float,
    min_trades: int,
) -> List[dict]:
    """Generic runner for variants that produce a single signal series per coin."""
    loaded_1h: Dict[str, pd.DataFrame] = {}
    loaded_15m: Dict[str, pd.DataFrame] = {}

    print(f"\nLoading 1h data for {len(coins)} coin(s)...")
    names = []
    for prefix in coins:
        n = _load_coin_data(prefix, loaded_15m, loaded_1h)
        if n in loaded_1h:
            names.append(n)

    prefix_map = {p.replace("usdt", "").upper(): p for p in coins}
    winners: List[dict] = []

    header = f"{'Coin':<8} {'Strategy':<20} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}"
    print(f"\n{header}")
    print("-" * len(header))

    for name in sorted(names):
        df = loaded_1h[name]
        try:
            df_sig = strat_fn(df)
            r = simulate(df_sig, **sim_kwargs)
        except Exception as e:
            print(f"  ERROR {name} {variant}: {e}")
            continue

        is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades
        _print_row(name, variant, r, is_winner)

        if is_winner:
            winners.append({
                "coin": name,
                "prefix": prefix_map.get(name, name.lower() + "usdt"),
                "strategy": variant,
                "timeframe": "1h",
                "pf": round(r["pf"], 3),
                "trades": r["trades"],
                "tr_yr": round(r["tr_yr"], 1),
                "wr_pct": round(r["wr"], 1),
                "dd_pct": round(r["dd"], 1),
                "ret_pct": round(r["ret"], 1),
                "params": sim_kwargs,
            })

    return winners


def run_ema_12_26_variant(
    coins: List[str],
    min_pf: float,
    min_trades: int,
) -> List[dict]:
    """Runner for EMA(12/26) variant — needs both 15m + 1h data."""
    loaded_1h: Dict[str, pd.DataFrame] = {}
    loaded_15m: Dict[str, pd.DataFrame] = {}

    print(f"\nLoading 15m + 1h data for {len(coins)} coin(s)...")
    names_15m: set = set()
    for prefix in coins:
        name = _load_coin_data(prefix, loaded_15m, loaded_1h)
        if name in loaded_15m:
            names_15m.add(name)

    prefix_map = {p.replace("usdt", "").upper(): p for p in coins}
    winners: List[dict] = []
    sim_kwargs = {"sl_mult": 1.0, "tp_mult": 3.0}

    header = f"{'Coin':<8} {'Strategy':<20} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}"
    print(f"\n{header}")
    print("-" * len(header))

    for name in sorted(names_15m):
        df = loaded_15m[name]
        df_1h = loaded_1h.get(name)
        try:
            df_sig = strategy_ema_12_26(df, df_1h=df_1h)
            r = simulate(df_sig, **sim_kwargs)
        except Exception as e:
            print(f"  ERROR {name} ema_12_26: {e}")
            continue

        is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades
        _print_row(name, "ema_12_26", r, is_winner)

        if is_winner:
            winners.append({
                "coin": name,
                "prefix": prefix_map.get(name, name.lower() + "usdt"),
                "strategy": "ema_12_26",
                "timeframe": "15m",
                "pf": round(r["pf"], 3),
                "trades": r["trades"],
                "tr_yr": round(r["tr_yr"], 1),
                "wr_pct": round(r["wr"], 1),
                "dd_pct": round(r["dd"], 1),
                "ret_pct": round(r["ret"], 1),
                "params": sim_kwargs,
            })

    return winners


def run_adaptive_sltp_variant(
    coins: List[str],
    min_pf: float,
    min_trades: int,
) -> List[dict]:
    """Runner for adaptive_sltp variant — reports best SL/TP combo per coin."""
    loaded_1h: Dict[str, pd.DataFrame] = {}
    loaded_15m: Dict[str, pd.DataFrame] = {}

    print(f"\nLoading 1h data for {len(coins)} coin(s)...")
    names = []
    for prefix in coins:
        n = _load_coin_data(prefix, loaded_15m, loaded_1h)
        if n in loaded_1h:
            names.append(n)

    prefix_map = {p.replace("usdt", "").upper(): p for p in coins}
    winners: List[dict] = []

    header = f"{'Coin':<8} {'Strategy':<20} {'SL':>5} {'TP':>5} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}"
    print(f"\n{header}")
    print("-" * (len(header) + 2))

    for name in sorted(names):
        df = loaded_1h[name]
        try:
            df_sig, combo_results = strategy_adaptive_sltp(df)
        except Exception as e:
            print(f"  ERROR {name} adaptive_sltp: {e}")
            continue

        # Find best combo by PF with minimum trade filter
        valid = [r for r in combo_results if r["trades"] >= min_trades]
        if not valid:
            continue
        best = max(valid, key=lambda r: r["pf"])

        is_winner = best["pf"] >= min_pf
        marker = " *" if is_winner else ""
        if best["trades"] >= 5:
            print(
                f"{name:<8} {'adaptive_sltp':<20} {best['sl_mult']:>4.1f} {best['tp_mult']:>4.1f}"
                f" {best['trades']:>6} {best['tr_yr']:>5.0f}/yr"
                f" {best['wr']:>5.1f}% {best['pf']:>6.2f} {best['dd']:>5.1f}%{marker}"
            )

        if is_winner:
            winners.append({
                "coin": name,
                "prefix": prefix_map.get(name, name.lower() + "usdt"),
                "strategy": "adaptive_sltp",
                "timeframe": "1h",
                "pf": round(best["pf"], 3),
                "trades": best["trades"],
                "tr_yr": round(best["tr_yr"], 1),
                "wr_pct": round(best["wr"], 1),
                "dd_pct": round(best["dd"], 1),
                "ret_pct": round(best["ret"], 1),
                "params": {"sl_mult": best["sl_mult"], "tp_mult": best["tp_mult"]},
                "all_combos": [
                    {
                        "sl": r["sl_mult"],
                        "tp": r["tp_mult"],
                        "pf": round(r["pf"], 3),
                        "trades": r["trades"],
                        "wr": round(r["wr"], 1),
                    }
                    for r in sorted(combo_results, key=lambda x: -x["pf"])
                ],
            })

    return winners


def run_momentum_roc_variant(
    coins: List[str],
    min_pf: float,
    min_trades: int,
) -> List[dict]:
    """Runner for momentum_roc variant — uses 1h data."""
    loaded_1h: Dict[str, pd.DataFrame] = {}
    loaded_15m: Dict[str, pd.DataFrame] = {}

    print(f"\nLoading 1h data for {len(coins)} coin(s)...")
    names = []
    for prefix in coins:
        n = _load_coin_data(prefix, loaded_15m, loaded_1h)
        if n in loaded_1h:
            names.append(n)

    prefix_map = {p.replace("usdt", "").upper(): p for p in coins}
    winners: List[dict] = []
    sim_kwargs = {"sl_mult": 2.0, "tp_mult": 5.0}

    header = f"{'Coin':<8} {'Strategy':<20} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}"
    print(f"\n{header}")
    print("-" * len(header))

    for name in sorted(names):
        df = loaded_1h[name]
        try:
            df_sig = strategy_momentum_roc(df)
            r = simulate(df_sig, **sim_kwargs)
        except Exception as e:
            print(f"  ERROR {name} momentum_roc: {e}")
            continue

        is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades
        _print_row(name, "momentum_roc", r, is_winner)

        if is_winner:
            winners.append({
                "coin": name,
                "prefix": prefix_map.get(name, name.lower() + "usdt"),
                "strategy": "momentum_roc",
                "timeframe": "1h",
                "pf": round(r["pf"], 3),
                "trades": r["trades"],
                "tr_yr": round(r["tr_yr"], 1),
                "wr_pct": round(r["wr"], 1),
                "dd_pct": round(r["dd"], 1),
                "ret_pct": round(r["ret"], 1),
                "params": sim_kwargs,
            })

    return winners


# ---------------------------------------------------------------------------
# Master sweep dispatcher
# ---------------------------------------------------------------------------

def run_sweep(
    coins: List[str],
    variants: List[str],
    min_pf: float,
    min_trades: int,
) -> None:
    """Run selected variants across coins, save winners to sweep_v2_winners.json."""

    all_winners: List[dict] = []

    for variant in variants:
        print(f"\n{'=' * 70}")
        print(f"VARIANT: {variant.upper()}  ({len(coins)} coins)")
        print("=" * 70)

        if variant == "long_only_ichi":
            winners = run_ichimoku_variant(
                coins, variant,
                strat_fn=strategy_long_only_ichi,
                sim_kwargs={"sl_mult": 2.0, "tp_mult": 5.0},
                min_pf=min_pf,
                min_trades=min_trades,
            )

        elif variant == "fast_ichi":
            winners = run_ichimoku_variant(
                coins, variant,
                strat_fn=strategy_fast_ichi,
                sim_kwargs={"sl_mult": 2.0, "tp_mult": 5.0},
                min_pf=min_pf,
                min_trades=min_trades,
            )

        elif variant == "slow_ichi":
            winners = run_ichimoku_variant(
                coins, variant,
                strat_fn=strategy_slow_ichi,
                sim_kwargs={"sl_mult": 2.0, "tp_mult": 5.0},
                min_pf=min_pf,
                min_trades=min_trades,
            )

        elif variant == "momentum_roc":
            winners = run_momentum_roc_variant(coins, min_pf, min_trades)

        elif variant == "ema_12_26":
            winners = run_ema_12_26_variant(coins, min_pf, min_trades)

        elif variant == "adaptive_sltp":
            winners = run_adaptive_sltp_variant(coins, min_pf, min_trades)

        else:
            print(f"  Unknown variant '{variant}', skipping.")
            continue

        _print_summary(winners, variant, min_pf, min_trades)
        all_winners.extend(winners)

    if all_winners:
        _append_winners(all_winners)

    print(f"\n{'=' * 70}")
    print(f"SWEEP COMPLETE — {len(all_winners)} total winner(s) across {len(variants)} variant(s)")
    print("=" * 70)


def _print_summary(winners: List[dict], variant: str, min_pf: float, min_trades: int) -> None:
    print(f"\n--- {variant} Winners (PF >= {min_pf}, trades >= {min_trades}) ---")
    if not winners:
        print("  No winners found.")
        return
    hdr = f"{'Coin':<8} {'Strategy':<20} {'PF':>6} {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'DD%':>6}"
    print(hdr)
    print("-" * len(hdr))
    for w in sorted(winners, key=lambda x: -x["pf"]):
        extra = ""
        if "params" in w and "sl_mult" in w["params"]:
            extra = f" [SL{w['params']['sl_mult']} TP{w['params']['tp_mult']}]"
        print(
            f"{w['coin']:<8} {w['strategy']:<20} {w['pf']:>6.2f} {w['trades']:>7}"
            f" {w['tr_yr']:>5.0f}/yr {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}%{extra}"
        )
    print(f"Total: {len(winners)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="mass_sweep_v2: 6 new strategy variants on failing coins",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available variants: {', '.join(ALL_VARIANTS)}, all",
    )
    parser.add_argument(
        "--variant",
        type=str,
        default="all",
        help=f"Which variant to run: {', '.join(ALL_VARIANTS)}, all (default: all)",
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help="Comma-separated coin prefixes, e.g. bnbusdt,linkusdt",
    )
    parser.add_argument(
        "--min-pf",
        type=float,
        default=1.2,
        help="Minimum profit factor for winners (default: 1.2)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=15,
        help="Minimum number of trades for winners (default: 15)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Resolve variant list
    if args.variant.lower() == "all":
        variants = ALL_VARIANTS
    elif args.variant in ALL_VARIANTS:
        variants = [args.variant]
    else:
        print(f"Unknown variant '{args.variant}'. Choose from: {', '.join(ALL_VARIANTS)}, all")
        sys.exit(1)

    # Resolve coins — CLI overrides liquid_coins.json but does NOT auto-filter deployed coins
    if args.coins:
        coins = [c.lower().strip() for c in args.coins.split(",") if c.strip()]
    else:
        coins = discover_coins(None)
        # Remove deployed coins when running full sweep
        coins = [c for c in coins if c not in DEPLOYED_PREFIXES]

    if not coins:
        print("No coins found. Add CSV files to data/ or pass --coins.")
        sys.exit(1)

    print(f"Sweeping {len(coins)} coin(s): {', '.join(coins[:10])}{'...' if len(coins) > 10 else ''}")
    print(f"Variants: {', '.join(variants)}")
    print(f"Filter: PF >= {args.min_pf}, trades >= {args.min_trades}")

    run_sweep(coins, variants=variants, min_pf=args.min_pf, min_trades=args.min_trades)
