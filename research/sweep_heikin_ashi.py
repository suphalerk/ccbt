"""
sweep_heikin_ashi.py — Heikin-Ashi + EMA crossover strategy sweep.

Strategy variants tested:
  1. ha_ema_base       — HA EMA(9/21) crossover + EMA(50) trend filter
  2. ha_ema_color      — HA EMA(9/21) crossover + EMA(50) trend + HA color gate
     (only long when ha_close > ha_open, only short when ha_close < ha_open)

SL/TP sweep: SL=[1.0, 1.5, 2.0] x TP=[3.0, 4.0, 5.0] = 9 combos per variant
SL/TP use original (non-HA) ATR for realistic stop placement.
Hours: 03:00-20:00 UTC, weekends off.

Coins: all *_1h_2y.csv files. Skips the 7 deployed coins.

Winners (PF >= 1.3, trades >= 10, DD <= 30%) are saved to:
    data/sweep_heikin_ashi_winners.json

Usage:
    python research/sweep_heikin_ashi.py
    python research/sweep_heikin_ashi.py --coins bnbusdt,linkusdt
    python research/sweep_heikin_ashi.py --variant ha_ema_base
    python research/sweep_heikin_ashi.py --min-pf 1.4 --min-trades 15 --max-dd 25
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
from bot.data import compute_atr, compute_ema, compute_volume_ma

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_DIR = "/Users/iceai/Work/ccbt/data"
WINNERS_PATH = os.path.join(DATA_DIR, "sweep_heikin_ashi_winners.json")

# Coins already deployed — skip in sweep
DEPLOYED_PREFIXES = {
    "btcusdt", "dogeusdt", "arbusdt", "wifusdt",
    "avaxusdt", "nearusdt", "solusdt",
}

ALL_VARIANTS = ["ha_ema_base", "ha_ema_color"]

SL_VALUES = [1.0, 1.5, 2.0]
TP_VALUES = [3.0, 4.0, 5.0]

HOURS_START = 3
HOURS_END = 20
WARMUP_BARS = 80  # EMA(50) convergence + HA warmup


# ---------------------------------------------------------------------------
# Heikin-Ashi computation
# ---------------------------------------------------------------------------

def compute_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Compute Heikin-Ashi OHLC from standard OHLCV.

    HA Close  = (open + high + low + close) / 4
    HA Open   = iterative: (prev_ha_open + prev_ha_close) / 2
    HA High   = max(high, ha_open, ha_close)
    HA Low    = min(low, ha_open, ha_close)

    Uses a Python loop for ha_open (inherently sequential), but all other
    columns are fully vectorized.

    Returns a copy of df with ha_open, ha_close, ha_high, ha_low added.
    """
    df = df.copy()

    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4

    # ha_open is sequential — must iterate
    ha_open_arr = np.empty(len(df), dtype=np.float64)
    # Seed with midpoint of first bar to reduce initial bias
    ha_open_arr[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
    ha_close_arr = ha_close.to_numpy()
    for i in range(1, len(df)):
        ha_open_arr[i] = (ha_open_arr[i - 1] + ha_close_arr[i - 1]) / 2

    ha_open = pd.Series(ha_open_arr, index=df.index)

    df["ha_close"] = ha_close
    df["ha_open"] = ha_open
    df["ha_high"] = pd.concat(
        [df["high"], ha_open, ha_close], axis=1
    ).max(axis=1)
    df["ha_low"] = pd.concat(
        [df["low"], ha_open, ha_close], axis=1
    ).min(axis=1)

    return df


# ---------------------------------------------------------------------------
# Shared indicator builders
# ---------------------------------------------------------------------------

def _build_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add original ATR, volume MA, and time columns.

    ATR is computed on original OHLC (not HA) — used for SL/TP placement.
    """
    df = df.copy()
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["vol_ma"] = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / df["vol_ma"].clip(lower=1e-10)
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek
    return df


def _add_ha_ema_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add HA-based EMA columns: ema9_ha, ema21_ha, ema50_ha.

    EMAs are computed on ha_close, not original close.
    Cross-up / cross-down flags are on the HA EMAs.
    """
    df = df.copy()
    df["ema9_ha"] = compute_ema(df["ha_close"], 9)
    df["ema21_ha"] = compute_ema(df["ha_close"], 21)
    df["ema50_ha"] = compute_ema(df["ha_close"], 50)

    # Crossover flags (current bar vs previous bar)
    df["ha_cross_up"] = (
        (df["ema9_ha"] > df["ema21_ha"])
        & (df["ema9_ha"].shift(1) <= df["ema21_ha"].shift(1))
    )
    df["ha_cross_down"] = (
        (df["ema9_ha"] < df["ema21_ha"])
        & (df["ema9_ha"].shift(1) >= df["ema21_ha"].shift(1))
    )

    # HA candle color: green when ha_close > ha_open
    df["ha_green"] = df["ha_close"] > df["ha_open"]
    df["ha_red"] = df["ha_close"] < df["ha_open"]

    return df


# ---------------------------------------------------------------------------
# Strategy builders
# ---------------------------------------------------------------------------

def strategy_ha_ema_base(
    df: pd.DataFrame,
    hours_start: int = HOURS_START,
    hours_end: int = HOURS_END,
) -> pd.DataFrame:
    """HA EMA(9/21) crossover with EMA(50) trend filter — no color gate.

    Entry (LONG):  HA EMA9 crosses above HA EMA21 AND ha_close > HA EMA50
    Entry (SHORT): HA EMA9 crosses below HA EMA21 AND ha_close < HA EMA50
    Hours: 03-20 UTC, weekends off.
    Warmup: first 80 bars zeroed.
    """
    df = compute_heikin_ashi(df)
    df = _add_ha_ema_indicators(df)

    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5

    trend_bull = df["ha_close"] > df["ema50_ha"]
    trend_bear = df["ha_close"] < df["ema50_ha"]

    signals[df["ha_cross_up"] & trend_bull & in_hours & not_we] = 1
    signals[df["ha_cross_down"] & trend_bear & in_hours & not_we] = -1

    signals.iloc[:WARMUP_BARS] = 0
    df["signal"] = signals
    return df


def strategy_ha_ema_color(
    df: pd.DataFrame,
    hours_start: int = HOURS_START,
    hours_end: int = HOURS_END,
) -> pd.DataFrame:
    """HA EMA(9/21) crossover + EMA(50) trend filter + HA color gate.

    Like ha_ema_base but adds:
      - Long only when current HA candle is green (ha_close > ha_open)
      - Short only when current HA candle is red  (ha_close < ha_open)

    This reduces whipsaws: an EMA cross on a HA candle that is already
    trending in the right direction is a higher-quality signal.
    """
    df = compute_heikin_ashi(df)
    df = _add_ha_ema_indicators(df)

    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5

    trend_bull = df["ha_close"] > df["ema50_ha"]
    trend_bear = df["ha_close"] < df["ema50_ha"]

    signals[
        df["ha_cross_up"] & trend_bull & df["ha_green"] & in_hours & not_we
    ] = 1
    signals[
        df["ha_cross_down"] & trend_bear & df["ha_red"] & in_hours & not_we
    ] = -1

    signals.iloc[:WARMUP_BARS] = 0
    df["signal"] = signals
    return df


# ---------------------------------------------------------------------------
# Simulator (copied from mass_sweep_v2.py for self-containment)
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

    df must have a 'signal' column (1=long, -1=short, 0=none) and an 'atr'
    column computed from the ORIGINAL candles (not HA).

    Returns metrics dict: trades, tr_yr, wr, pf, dd, balance, ret.
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
# Coin discovery
# ---------------------------------------------------------------------------

def discover_coins(coins_arg: Optional[List[str]]) -> List[str]:
    """Return list of lowercase prefixes with 1h_2y.csv data.

    Priority: CLI --coins > glob scan of data/*.
    Deployed coins are always filtered unless passed explicitly via --coins.
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
# Data loading
# ---------------------------------------------------------------------------

def _load_coin_1h(prefix: str) -> Tuple[str, Optional[pd.DataFrame]]:
    """Load 1h CSV for a coin prefix. Returns (name, df) or (name, None)."""
    name = prefix.replace("usdt", "").replace("1000", "1000").upper()
    # Normalise name: strip leading "1000" prefix for display only
    for period in ["2y", "5y"]:
        fpath = os.path.join(DATA_DIR, f"{prefix}_1h_{period}.csv")
        if os.path.exists(fpath):
            try:
                df = load_ohlcv(fpath)
                df = _build_base_indicators(df)
                return name, df
            except Exception as e:
                print(f"  WARN: could not load {fpath}: {e}")
                return name, None
    return name, None


# ---------------------------------------------------------------------------
# Per-variant sweep runner
# ---------------------------------------------------------------------------

def _print_row(name: str, strat: str, sl: float, tp: float, r: dict, is_winner: bool) -> None:
    marker = " *" if is_winner else ""
    if r["trades"] >= 3:
        print(
            f"  {name:<10} {strat:<16} SL{sl:.1f}/TP{tp:.1f}"
            f"  {r['trades']:>5} {r['tr_yr']:>5.0f}/yr"
            f"  WR {r['wr']:>5.1f}%  PF {r['pf']:>5.2f}  DD {r['dd']:>5.1f}%{marker}"
        )


def run_variant_sweep(
    coins: List[str],
    variant: str,
    strat_fn,
    min_pf: float,
    min_trades: int,
    max_dd: float,
) -> List[dict]:
    """Run a single strategy variant across all coins with SL/TP parameter sweep.

    For each coin, tests all 9 SL×TP combos and records each result.
    Winners = PF >= min_pf AND trades >= min_trades AND dd <= max_dd.
    Reports only the best combo per coin in the summary.
    """
    print(f"\nLoading 1h data for {len(coins)} coin(s)...")

    # Load all coin data upfront
    loaded: Dict[str, pd.DataFrame] = {}
    prefix_map: Dict[str, str] = {}
    for prefix in coins:
        name, df = _load_coin_1h(prefix)
        if df is not None and len(df) > WARMUP_BARS + 10:
            loaded[name] = df
            prefix_map[name] = prefix

    print(f"  Loaded {len(loaded)} coin(s) with sufficient data.\n")

    header = (
        f"  {'Coin':<10} {'Variant':<16} {'SL/TP':<12}"
        f"  {'Tr':>5} {'Tr/yr':>6}"
        f"  {'WR%':>8}  {'PF':>7}  {'DD%':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    all_results: List[dict] = []

    for name in sorted(loaded.keys()):
        df = loaded[name]

        # Build signals once per coin (not per SL/TP combo)
        try:
            df_sig = strat_fn(df)
        except Exception as e:
            print(f"  ERROR {name} {variant} signal build: {e}")
            continue

        coin_results: List[dict] = []

        for sl_m, tp_m in product(SL_VALUES, TP_VALUES):
            try:
                r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m)
            except Exception as e:
                print(f"  ERROR {name} {variant} sl={sl_m} tp={tp_m}: {e}")
                continue

            is_winner = (
                r["pf"] >= min_pf
                and r["trades"] >= min_trades
                and r["dd"] <= max_dd
            )
            _print_row(name, variant, sl_m, tp_m, r, is_winner)

            entry = {
                "coin": name,
                "prefix": prefix_map[name],
                "strategy": variant,
                "timeframe": "1h",
                "sl_mult": sl_m,
                "tp_mult": tp_m,
                "pf": round(r["pf"], 3),
                "trades": r["trades"],
                "tr_yr": round(r["tr_yr"], 1),
                "wr_pct": round(r["wr"], 1),
                "dd_pct": round(r["dd"], 1),
                "ret_pct": round(r["ret"], 1),
                "is_winner": is_winner,
            }
            coin_results.append(entry)
            if is_winner:
                all_results.append(entry)

    return all_results


# ---------------------------------------------------------------------------
# Winner persistence
# ---------------------------------------------------------------------------

def _save_winners(new_winners: List[dict]) -> None:
    """Write winners to WINNERS_PATH, merging with existing entries."""
    existing: List[dict] = []
    if os.path.exists(WINNERS_PATH):
        try:
            with open(WINNERS_PATH) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = []

    # Deduplicate by (coin, strategy, sl_mult, tp_mult) — prefer higher PF
    key_map: Dict[tuple, dict] = {
        (e["coin"], e["strategy"], e["sl_mult"], e["tp_mult"]): e
        for e in existing
    }
    for w in new_winners:
        k = (w["coin"], w["strategy"], w["sl_mult"], w["tp_mult"])
        if k not in key_map or w["pf"] > key_map[k]["pf"]:
            key_map[k] = w

    merged = sorted(key_map.values(), key=lambda x: (-x["pf"], x["coin"]))

    # Normalise all values to Python-native types before JSON serialization.
    # numpy scalars (int64, float64, bool_) are not JSON-serializable.
    def _to_native(v):
        if isinstance(v, bool):
            return bool(v)
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        if isinstance(v, (np.bool_,)):
            return bool(v)
        return v

    class _NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return super().default(obj)

    cleaned = [{k2: _to_native(v2) for k2, v2 in w.items()} for w in merged]
    with open(WINNERS_PATH, "w") as f:
        json.dump(cleaned, f, indent=2, cls=_NumpyEncoder)
    print(f"\n  Saved {len(merged)} total winner(s) to {WINNERS_PATH}")


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def _print_summary(winners: List[dict], variant: str, min_pf: float, min_trades: int) -> None:
    print(f"\n--- {variant} Winners (PF >= {min_pf}, trades >= {min_trades}) ---")
    if not winners:
        print("  No winners found.")
        return

    # Best combo per coin
    best_per_coin: Dict[str, dict] = {}
    for w in winners:
        c = w["coin"]
        if c not in best_per_coin or w["pf"] > best_per_coin[c]["pf"]:
            best_per_coin[c] = w

    hdr = (
        f"  {'Coin':<10} {'Strategy':<16} {'SL':>5} {'TP':>5}"
        f"  {'PF':>6}  {'Trades':>7}  {'Tr/yr':>6}  {'WR%':>6}  {'DD%':>6}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for w in sorted(best_per_coin.values(), key=lambda x: -x["pf"]):
        print(
            f"  {w['coin']:<10} {w['strategy']:<16} {w['sl_mult']:>4.1f} {w['tp_mult']:>4.1f}"
            f"  {w['pf']:>6.2f}  {w['trades']:>7}  {w['tr_yr']:>5.0f}/yr"
            f"  {w['wr_pct']:>5.1f}%  {w['dd_pct']:>5.1f}%"
        )
    print(f"  Total unique winning coins: {len(best_per_coin)}")
    print(f"  Total winning combos:       {len(winners)}")


# ---------------------------------------------------------------------------
# Master sweep dispatcher
# ---------------------------------------------------------------------------

_VARIANT_FNS = {
    "ha_ema_base": strategy_ha_ema_base,
    "ha_ema_color": strategy_ha_ema_color,
}


def run_sweep(
    coins: List[str],
    variants: List[str],
    min_pf: float,
    min_trades: int,
    max_dd: float,
) -> None:
    """Run all requested variants, print results, save winners."""
    all_winners: List[dict] = []

    for variant in variants:
        print(f"\n{'=' * 72}")
        print(f"VARIANT: {variant.upper()}  ({len(coins)} coins, {len(SL_VALUES) * len(TP_VALUES)} SL/TP combos each)")
        print("=" * 72)

        strat_fn = _VARIANT_FNS.get(variant)
        if strat_fn is None:
            print(f"  Unknown variant '{variant}', skipping.")
            continue

        winners = run_variant_sweep(
            coins=coins,
            variant=variant,
            strat_fn=strat_fn,
            min_pf=min_pf,
            min_trades=min_trades,
            max_dd=max_dd,
        )
        _print_summary(winners, variant, min_pf, min_trades)
        all_winners.extend(winners)

    if all_winners:
        _save_winners(all_winners)
    else:
        print("\nNo winners found across all variants.")

    print(f"\n{'=' * 72}")
    print(
        f"SWEEP COMPLETE — {len(all_winners)} winner combo(s) across"
        f" {len(variants)} variant(s), {len(coins)} coin(s)"
    )
    print("=" * 72)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Heikin-Ashi + EMA crossover sweep on all 1h_2y coins",
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
        help="Comma-separated coin prefixes, e.g. bnbusdt,linkusdt (skips deployed-coin filter)",
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
        help="Minimum number of trades for winners (default: 10)",
    )
    parser.add_argument(
        "--max-dd",
        type=float,
        default=30.0,
        help="Maximum drawdown %% for winners (default: 30.0)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.variant.lower() == "all":
        variants = ALL_VARIANTS
    elif args.variant in ALL_VARIANTS:
        variants = [args.variant]
    else:
        print(f"Unknown variant '{args.variant}'. Choose from: {', '.join(ALL_VARIANTS)}, all")
        sys.exit(1)

    if args.coins:
        coins = [c.lower().strip() for c in args.coins.split(",") if c.strip()]
    else:
        coins = discover_coins(None)

    if not coins:
        print("No coins found. Add *_1h_2y.csv files to data/ or pass --coins.")
        sys.exit(1)

    print(f"Heikin-Ashi EMA Sweep")
    print(f"Coins  : {len(coins)} ({', '.join(coins[:8])}{'...' if len(coins) > 8 else ''})")
    print(f"Variant: {', '.join(variants)}")
    print(f"Filter : PF >= {args.min_pf}, trades >= {args.min_trades}, DD <= {args.max_dd}%")
    print(f"SL     : {SL_VALUES}")
    print(f"TP     : {TP_VALUES}")

    run_sweep(
        coins=coins,
        variants=variants,
        min_pf=args.min_pf,
        min_trades=args.min_trades,
        max_dd=args.max_dd,
    )
