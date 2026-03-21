"""sweep_mtf_confluence.py — Multi-Timeframe Confluence Signal sweep.

Requires 3 timeframes to agree before entering:
  - 15m: EMA(9/21) crossover (entry trigger)
  - 1H:  Close > EMA(50) + RSI > 45 (trend confirmation)
  - 4H:  Close > EMA(200) (macro filter, derived from 1H data)

The 4H macro is created by resampling the 1H OHLCV to 4H and forward-filling
back to the 15m index — no look-ahead bias (only closed 4H candles visible).

Groups tested:
  A) Deployed EMA coins — compare MTF PF vs baseline PF
  B) Rejected coins (have 15m data but are not deployed) — new coin discovery

Usage:
    python research/sweep_mtf_confluence.py
    python research/sweep_mtf_confluence.py --group a
    python research/sweep_mtf_confluence.py --group b
    python research/sweep_mtf_confluence.py --min-pf 1.3 --min-trades 8
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import compute_atr, compute_ema, compute_rsi, compute_volume_ma

DATA_DIR = "/Users/iceai/Work/ccbt/data"

# ---------------------------------------------------------------------------
# Deployed coin registry (Group A)
# ---------------------------------------------------------------------------

# prefix -> (display_name, baseline_pf, config_note)
DEPLOYED_COINS: Dict[str, Tuple[str, float, str]] = {
    "btcusdt":  ("BTC",  1.85, "EMA 15m + funding scorer"),
    "wifusdt":  ("WIF",  1.64, "EMA 15m + funding scorer"),
    "arbusdt":  ("ARB",  1.46, "EMA 15m"),
    "dogeusdt": ("DOGE", 1.38, "EMA 15m, slope=0.01"),
    "arcusdt":  ("ARC",  1.70, "EMA 15m"),
}

# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _add_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA(9/21/50), RSI(14), ATR(14), volume ratio.  Returns a copy."""
    df = df.copy()
    df["ema9"]    = compute_ema(df["close"], 9)
    df["ema21"]   = compute_ema(df["close"], 21)
    df["ema50"]   = compute_ema(df["close"], 50)
    df["rsi"]     = compute_rsi(df["close"], 14)
    df["atr"]     = compute_atr(df["high"], df["low"], df["close"], 14)
    vol_ma        = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / vol_ma.clip(lower=1e-10)

    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"]  = df.index.dayofweek
    return df


def _build_1h_features(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Compute EMA(50) + RSI(14) on 1H data.  Returns a copy."""
    df = df_1h.copy()
    df["ema50_1h"] = compute_ema(df["close"], 50)
    df["rsi_1h"]   = compute_rsi(df["close"], 14)
    return df


def _build_4h_features(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV to 4H, compute EMA(200).

    The 4H candle at timestamp T represents bars T to T+4h (exclusive).
    We shift(1) before merging so that the 4H bar visible at time T is
    the last *completed* 4H bar — not the one still forming.
    """
    # Resample 1H → 4H
    df_4h = df_1h.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}
    ).dropna()
    df_4h["ema200_4h"] = compute_ema(df_4h["close"], 200)
    # shift(1): only see the completed 4H bar, not the forming one
    df_4h["close_4h"]  = df_4h["close"].shift(1)
    df_4h["ema200_4h"] = df_4h["ema200_4h"].shift(1)
    return df_4h[["close_4h", "ema200_4h"]]


def _align_higher_tf_to_15m(df_15m: pd.DataFrame, df_htf: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill higher-timeframe columns into the 15m DataFrame.

    Uses merge_asof (backward) so every 15m row sees the last completed
    candle on the higher timeframe — no look-ahead bias.
    """
    idx_name = df_15m.index.name or "timestamp"
    df_reset = df_15m.reset_index()
    htf_reset = df_htf.reset_index()
    # Ensure the join column name matches
    htf_col = htf_reset.columns[0]
    if htf_col != idx_name:
        htf_reset = htf_reset.rename(columns={htf_col: idx_name})

    merged = pd.merge_asof(
        df_reset.sort_values(idx_name),
        htf_reset.sort_values(idx_name),
        on=idx_name,
        direction="backward",
    )
    merged = merged.set_index(idx_name)
    return merged


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def build_mtf_signals(
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
    hours_start: int = 3,
    hours_end: int = 20,
    vol_mult: float = 1.3,
    rsi_long_min: float = 45,
    rsi_long_max: float = 65,
    rsi_short_min: float = 35,
    rsi_short_max: float = 55,
) -> pd.DataFrame:
    """Combine 15m EMA cross + 1H trend + 4H macro into confluence signals.

    All three timeframes must agree before a signal is emitted.
    Signals are placed on iloc[-2] semantics — the signal column is set on
    the BAR where all conditions are true; the simulator enters on the NEXT
    bar's open/close.

    Returns a copy of df_15m with a 'signal' column (1=long, -1=short, 0=none).
    """
    # --- 15m indicators ---
    df = _add_base_indicators(df_15m)

    # EMA(9/21) crossover — exact crossover on closed candle
    cross_up   = (df["ema9"] > df["ema21"]) & (df["ema9"].shift(1) <= df["ema21"].shift(1))
    cross_down = (df["ema9"] < df["ema21"]) & (df["ema9"].shift(1) >= df["ema21"].shift(1))

    # Trading hours + weekday filters
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we   = df["dow"] < 5
    vol_ok   = df["vol_ratio"] >= vol_mult

    # 15m RSI ranges
    rsi_long  = (df["rsi"] >= rsi_long_min)  & (df["rsi"] <= rsi_long_max)
    rsi_short = (df["rsi"] >= rsi_short_min) & (df["rsi"] <= rsi_short_max)

    # --- 1H trend confirmation ---
    df_1h_feat = _build_1h_features(df_1h)
    # shift(1): only see the *completed* 1H candle, not the forming one
    df_1h_shifted = df_1h_feat[["ema50_1h", "rsi_1h", "close"]].copy()
    df_1h_shifted.columns = ["ema50_1h", "rsi_1h", "close_1h"]
    df_1h_shifted = df_1h_shifted.shift(1)

    df = _align_higher_tf_to_15m(df, df_1h_shifted)
    df["ema50_1h"]  = df["ema50_1h"].ffill()
    df["rsi_1h"]    = df["rsi_1h"].ffill()
    df["close_1h"]  = df["close_1h"].ffill()

    trend_bull_1h = (df["close_1h"] > df["ema50_1h"]) & (df["rsi_1h"] > 45)
    trend_bear_1h = (df["close_1h"] < df["ema50_1h"]) & (df["rsi_1h"] < 55)

    # --- 4H macro ---
    df_4h_feat = _build_4h_features(df_1h)
    df = _align_higher_tf_to_15m(df, df_4h_feat)
    df["close_4h"]  = df["close_4h"].ffill()
    df["ema200_4h"] = df["ema200_4h"].ffill()

    macro_bull_4h = df["close_4h"] > df["ema200_4h"]
    macro_bear_4h = df["close_4h"] < df["ema200_4h"]

    # --- Confluence: ALL 3 must agree ---
    long_signal  = cross_up   & in_hours & not_we & vol_ok & rsi_long  & trend_bull_1h & macro_bull_4h
    short_signal = cross_down & in_hours & not_we & vol_ok & rsi_short & trend_bear_1h & macro_bear_4h

    signals = pd.Series(0, index=df.index)
    signals[long_signal]  = 1
    signals[short_signal] = -1

    # Blank warmup: need at least 200 bars for EMA(200) on 4H resampled from 1H
    # 200 × 4H × 4 = 3200 1H bars → only a few months, but our 2y data is fine.
    # Use 60-bar warmup on 15m (matches mass_sweep.py convention)
    signals.iloc[:60] = 0

    df["signal"] = signals
    return df


# ---------------------------------------------------------------------------
# Simulator (identical to mass_sweep.py for fair comparison)
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    sl_mult: float = 1.0,
    tp_mult: float = 3.0,
    risk_pct: float = 0.10,
    leverage: float = 25,
    commission: float = 0.00055,
    slippage: float = 0.0002,
) -> dict:
    """Event-driven simulator.

    df must have a 'signal' column (1=long, -1=short, 0=none) and 'atr' column.
    Uses iloc[-2] semantics: signal is set on previous bar, execution on next open.
    Returns metrics dict with trades, tr_yr, wr, pf, dd, balance, ret.
    """
    balance = 1000.0
    peak    = 1000.0
    max_dd  = 0.0
    trades: List[dict] = []
    position: Optional[dict] = None

    for i in range(2, len(df)):
        row     = df.iloc[i]
        sig_row = df.iloc[i - 1]  # signal from previous (closed) bar

        # --- Manage open position ---
        if position is not None:
            side  = position["side"]
            entry = position["entry"]
            sl    = position["sl"]
            tp    = position["tp"]

            hit_sl = (side == 1 and row["low"] <= sl) or (side == -1 and row["high"] >= sl)
            hit_tp = (side == 1 and row["high"] >= tp) or (side == -1 and row["low"] <= tp)

            if hit_sl or hit_tp:
                exit_p  = sl if hit_sl else tp
                pnl_pct = side * (exit_p - entry) / entry - (commission + slippage) * 2
                pnl     = balance * risk_pct * leverage * pnl_pct / sl_mult
                pnl     = max(pnl, -balance * risk_pct * leverage)
                balance += pnl
                peak     = max(peak, balance)
                dd       = (peak - balance) / peak if peak > 0 else 0
                max_dd   = max(max_dd, dd)
                trades.append({"pnl": pnl, "reason": "sl" if hit_sl else "tp"})
                position = None
                if balance <= 0:
                    break

        # --- Open new position ---
        if position is None and sig_row.get("signal", 0) != 0:
            # Skip weekends
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
            side    = int(sig_row["signal"])
            sl_d    = atr * sl_mult
            tp_d    = atr * tp_mult

            sl_p = entry_p - sl_d if side == 1 else entry_p + sl_d
            tp_p = entry_p + tp_d if side == 1 else entry_p - tp_d

            position = {"side": side, "entry": entry_p, "sl": sl_p, "tp": tp_p}

    total = len(trades)
    wins  = sum(1 for t in trades if t["pnl"] > 0)
    gp    = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl    = sum(abs(t["pnl"]) for t in trades if t["pnl"] < 0)
    pf    = gp / gl if gl > 0 else 0.0
    wr    = wins / total * 100 if total > 0 else 0.0
    days  = (df.index[-1] - df.index[0]).days
    yr    = days / 365.25
    return {
        "trades": total,
        "tr_yr":  total / yr if yr > 0 else 0,
        "wr":     wr,
        "pf":     pf,
        "dd":     max_dd * 100,
        "balance": balance,
        "ret":    (balance - 1000) / 10,
    }


# ---------------------------------------------------------------------------
# Data loading helper
# ---------------------------------------------------------------------------

def _load_pair(prefix: str) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Load 15m and 1h DataFrames for a coin prefix.  Returns (df_15m, df_1h)."""
    df_15m = df_1h = None
    for period in ["2y", "5y"]:
        path_15m = os.path.join(DATA_DIR, f"{prefix}_15m_{period}.csv")
        if os.path.exists(path_15m) and df_15m is None:
            try:
                df_15m = load_ohlcv(path_15m)
            except Exception as e:
                print(f"  WARN: {path_15m}: {e}")

        path_1h = os.path.join(DATA_DIR, f"{prefix}_1h_{period}.csv")
        if os.path.exists(path_1h) and df_1h is None:
            try:
                df_1h = load_ohlcv(path_1h)
            except Exception as e:
                print(f"  WARN: {path_1h}: {e}")

    return df_15m, df_1h


# ---------------------------------------------------------------------------
# Sweep runners
# ---------------------------------------------------------------------------

# SL/TP parameter grid (matches task specification)
PARAM_GRID = [
    {"sl_mult": 1.0, "tp_mult": 3.0},
    {"sl_mult": 1.0, "tp_mult": 4.0},
    {"sl_mult": 1.5, "tp_mult": 3.0},
    {"sl_mult": 1.5, "tp_mult": 4.0},
    {"sl_mult": 2.0, "tp_mult": 3.0},
    {"sl_mult": 2.0, "tp_mult": 4.0},
]


def _run_coin(
    name: str,
    prefix: str,
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
    min_pf: float,
    min_trades: int,
) -> List[dict]:
    """Run all parameter combinations for one coin.  Returns winning rows."""
    results = []
    try:
        df_sig = build_mtf_signals(df_15m, df_1h)
        signal_count = int((df_sig["signal"] != 0).sum())
    except Exception as e:
        print(f"  ERROR building signals for {name}: {e}")
        return []

    for params in PARAM_GRID:
        try:
            r = simulate(df_sig, **params)
        except Exception as e:
            print(f"  ERROR simulating {name} {params}: {e}")
            continue

        label = f"SL{params['sl_mult']}/TP{params['tp_mult']}"
        marker = " *" if r["pf"] >= min_pf and r["trades"] >= min_trades else ""

        if r["trades"] >= 3:
            print(
                f"  {label:<12}  trades={r['trades']:>4}  "
                f"tr/yr={r['tr_yr']:>5.1f}  wr={r['wr']:>5.1f}%  "
                f"PF={r['pf']:>5.2f}  DD={r['dd']:>4.1f}%{marker}"
            )

        if r["pf"] >= min_pf and r["trades"] >= min_trades:
            results.append({
                "coin":    name,
                "prefix":  prefix,
                "strategy": "MTF-EMA",
                "sl_mult": params["sl_mult"],
                "tp_mult": params["tp_mult"],
                "pf":      round(r["pf"], 3),
                "trades":  r["trades"],
                "tr_yr":   round(r["tr_yr"], 1),
                "wr_pct":  round(r["wr"], 1),
                "dd_pct":  round(r["dd"], 1),
                "ret_pct": round(r["ret"], 1),
                "signal_count": signal_count,
            })
    return results


def run_group_a(min_pf: float, min_trades: int) -> Tuple[List[dict], List[dict]]:
    """Group A: deployed EMA coins — compare MTF vs baseline."""
    print("\n" + "=" * 70)
    print("GROUP A — Deployed EMA coins (MTF confluence vs baseline)")
    print("=" * 70)

    comparison_rows = []
    all_winners     = []

    for prefix, (name, baseline_pf, note) in DEPLOYED_COINS.items():
        print(f"\n{name} ({note})  baseline PF={baseline_pf}")
        df_15m, df_1h = _load_pair(prefix)
        if df_15m is None or df_1h is None:
            print(f"  SKIP: missing data for {prefix}")
            continue

        winners = _run_coin(name, prefix, df_15m, df_1h, min_pf=0.0, min_trades=3)
        all_winners.extend([w for w in winners if w["pf"] >= min_pf and w["trades"] >= min_trades])

        # Best MTF result for comparison table
        if winners:
            best = max(winners, key=lambda x: x["pf"])
            vs   = best["pf"] - baseline_pf
            comparison_rows.append({
                "coin":        name,
                "baseline_pf": baseline_pf,
                "mtf_best_pf": best["pf"],
                "delta_pf":    round(vs, 3),
                "mtf_trades":  best["trades"],
                "mtf_tr_yr":   best["tr_yr"],
                "mtf_wr_pct":  best["wr_pct"],
                "mtf_dd_pct":  best["dd_pct"],
                "best_params": f"SL{best['sl_mult']}/TP{best['tp_mult']}",
            })
        else:
            comparison_rows.append({
                "coin":        name,
                "baseline_pf": baseline_pf,
                "mtf_best_pf": 0.0,
                "delta_pf":    round(0.0 - baseline_pf, 3),
                "mtf_trades":  0,
                "mtf_tr_yr":   0.0,
                "mtf_wr_pct":  0.0,
                "mtf_dd_pct":  0.0,
                "best_params": "n/a",
            })

    return comparison_rows, all_winners


def run_group_b(min_pf: float, min_trades: int) -> List[dict]:
    """Group B: coins with 15m+1h data that are NOT deployed."""
    print("\n" + "=" * 70)
    print("GROUP B — Non-deployed coins (can MTF confluence unlock edge?)")
    print("=" * 70)

    deployed_prefixes = set(DEPLOYED_COINS.keys())
    # Also exclude Ichimoku-deployed coins (AVAX, NEAR, SOL) — they have a different strategy
    ichi_prefixes = {"avaxusdt", "nearusdt", "solusdt"}
    skip_prefixes = deployed_prefixes | ichi_prefixes

    pattern = os.path.join(DATA_DIR, "*_15m_2y.csv")
    all_15m = sorted(glob.glob(pattern))

    winners = []
    checked = 0

    for path_15m in all_15m:
        base   = os.path.basename(path_15m)
        prefix = base.replace("_15m_2y.csv", "")

        # Skip non-ASCII filenames (Chinese characters etc.)
        try:
            prefix.encode("ascii")
        except UnicodeEncodeError:
            continue

        if prefix in skip_prefixes:
            continue

        path_1h = os.path.join(DATA_DIR, f"{prefix}_1h_2y.csv")
        if not os.path.exists(path_1h):
            continue

        name = prefix.replace("usdt", "").upper()
        print(f"\n{name}")

        try:
            df_15m = load_ohlcv(path_15m)
            df_1h  = load_ohlcv(path_1h)
        except Exception as e:
            print(f"  SKIP: load error: {e}")
            continue

        checked += 1
        coin_winners = _run_coin(name, prefix, df_15m, df_1h, min_pf, min_trades)
        winners.extend(coin_winners)

    print(f"\nGroup B: checked {checked} non-deployed coins, {len(winners)} winner combos")
    return winners


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _print_comparison_table(rows: List[dict]) -> None:
    """Print Group A before/after comparison table."""
    print("\n" + "=" * 80)
    print("GROUP A — Comparison: Baseline EMA vs MTF Confluence (best SL/TP combo)")
    print("=" * 80)
    hdr = (
        f"{'Coin':<6}  {'Baseline':>8}  {'MTF Best':>8}  {'Delta':>7}  "
        f"{'Trades':>7}  {'Tr/yr':>6}  {'WR%':>5}  {'DD%':>5}  {'Params':<12}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        delta_str = f"+{r['delta_pf']:.3f}" if r["delta_pf"] >= 0 else f"{r['delta_pf']:.3f}"
        print(
            f"{r['coin']:<6}  {r['baseline_pf']:>8.3f}  {r['mtf_best_pf']:>8.3f}  "
            f"{delta_str:>7}  {r['mtf_trades']:>7}  {r['mtf_tr_yr']:>5.1f}/yr  "
            f"{r['mtf_wr_pct']:>5.1f}%  {r['mtf_dd_pct']:>4.1f}%  {r['best_params']:<12}"
        )


def _print_new_coins(winners: List[dict], min_pf: float) -> None:
    """Print Group B new discoveries."""
    print("\n" + "=" * 70)
    print(f"GROUP B — New coin discoveries (PF >= {min_pf})")
    print("=" * 70)
    if not winners:
        print("  None found.")
        return
    # Deduplicate: best combo per coin
    best_per_coin: Dict[str, dict] = {}
    for w in winners:
        key = w["coin"]
        if key not in best_per_coin or w["pf"] > best_per_coin[key]["pf"]:
            best_per_coin[key] = w
    hdr = f"{'Coin':<8}  {'PF':>6}  {'Trades':>7}  {'Tr/yr':>6}  {'WR%':>5}  {'DD%':>5}  {'Params':<12}"
    print(hdr)
    print("-" * len(hdr))
    for w in sorted(best_per_coin.values(), key=lambda x: -x["pf"]):
        label = f"SL{w['sl_mult']}/TP{w['tp_mult']}"
        print(
            f"{w['coin']:<8}  {w['pf']:>6.2f}  {w['trades']:>7}  "
            f"{w['tr_yr']:>5.1f}/yr  {w['wr_pct']:>5.1f}%  {w['dd_pct']:>4.1f}%  {label:<12}"
        )


def _recommendation(comparison_rows: List[dict], new_coins: List[dict], min_pf: float) -> None:
    """Print a concise recommendation section."""
    print("\n" + "=" * 70)
    print("RECOMMENDATION")
    print("=" * 70)

    # Group A analysis
    improved = [r for r in comparison_rows if r["delta_pf"] > 0 and r["mtf_trades"] >= 8]
    degraded = [r for r in comparison_rows if r["delta_pf"] < -0.05 and r["mtf_trades"] >= 8]

    if improved:
        print(f"\nDeployed coins where MTF confluence IMPROVES PF:")
        for r in improved:
            print(f"  {r['coin']}: {r['baseline_pf']:.2f} -> {r['mtf_best_pf']:.2f} "
                  f"(+{r['delta_pf']:.3f})  trades={r['mtf_trades']}  {r['best_params']}")
    else:
        print("\nNo deployed coin shows PF improvement with MTF confluence.")

    if degraded:
        print(f"\nDeployed coins where MTF confluence HURTS PF:")
        for r in degraded:
            print(f"  {r['coin']}: {r['baseline_pf']:.2f} -> {r['mtf_best_pf']:.2f} "
                  f"({r['delta_pf']:.3f})  trades={r['mtf_trades']}")

    # Trade-count impact
    total_base_coins = len([r for r in comparison_rows if r["mtf_trades"] > 0])
    avg_trade_drop = None
    if total_base_coins > 0:
        drops = [
            (r["mtf_trades"] - r["baseline_pf"] * 10) / max(r["baseline_pf"] * 10, 1)
            for r in comparison_rows if r["mtf_trades"] > 0
        ]
    print(f"\nNote: MTF confluence filters reduce trade count significantly.")
    print(f"Watch that trade counts don't fall below ~8/yr (statistical noise territory).")

    # Group B
    quality_b = [w for w in new_coins if w["pf"] >= min_pf and w["trades"] >= 8]
    best_b_coins = {}
    for w in quality_b:
        key = w["coin"]
        if key not in best_b_coins or w["pf"] > best_b_coins[key]["pf"]:
            best_b_coins[key] = w

    if best_b_coins:
        print(f"\nNew coins unlocked by MTF confluence (PF >= {min_pf}, trades >= 8):")
        for w in sorted(best_b_coins.values(), key=lambda x: -x["pf"]):
            label = f"SL{w['sl_mult']}/TP{w['tp_mult']}"
            print(f"  {w['coin']}: PF={w['pf']:.2f}  trades={w['trades']}  tr/yr={w['tr_yr']:.1f}  {label}")
        print("\nRecommendation: backtest these with full-engine (backtest/engine.py) before adding.")
    else:
        print(f"\nNo new coins discovered with PF >= {min_pf} and >= 8 trades.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-Timeframe Confluence sweep: 15m EMA + 1H trend + 4H macro"
    )
    parser.add_argument(
        "--group",
        choices=["a", "b", "both"],
        default="both",
        help="Which group to test: a=deployed coins, b=new coins, both (default)",
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
        default=8,
        help="Minimum trade count over backtest period (default: 8)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    min_pf     = args.min_pf
    min_trades = args.min_trades

    print(f"MTF Confluence Sweep  |  min_pf={min_pf}  min_trades={min_trades}")
    print(f"Signal: 15m EMA(9/21) cross + 1H trend (EMA50+RSI) + 4H macro (EMA200)")
    print(f"Grid: SL=[1.0,1.5,2.0] x TP=[3.0,4.0]  hours=03-20 UTC  weekdays only")

    comparison_rows: List[dict] = []
    group_a_winners: List[dict] = []
    group_b_winners: List[dict] = []

    if args.group in ("a", "both"):
        comparison_rows, group_a_winners = run_group_a(min_pf, min_trades)

    if args.group in ("b", "both"):
        group_b_winners = run_group_b(min_pf, min_trades)

    # --- Print reports ---
    if comparison_rows:
        _print_comparison_table(comparison_rows)

    all_new_coins = group_b_winners
    if all_new_coins:
        _print_new_coins(all_new_coins, min_pf)

    _recommendation(comparison_rows, all_new_coins, min_pf)

    # --- Save winners to JSON ---
    all_winners = group_a_winners + group_b_winners
    out_path = os.path.join(DATA_DIR, "sweep_mtf_confluence_winners.json")
    with open(out_path, "w") as f:
        json.dump(
            {
                "min_pf":     min_pf,
                "min_trades": min_trades,
                "group_a_comparison": comparison_rows,
                "group_a_winners":    group_a_winners,
                "group_b_winners":    group_b_winners,
                "all_winners":        all_winners,
            },
            f,
            indent=2,
        )
    print(f"\nResults saved to {out_path}")
    print(f"Total winners: {len(all_winners)}")


if __name__ == "__main__":
    main()
