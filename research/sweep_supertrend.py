"""sweep_supertrend.py — Supertrend strategy sweep on 1H data.

Tests Supertrend indicator across multiplier, SL, TP, and trailing stop combos.
Variants:
  both  — Long + Short signals
  long  — Long-only (skip short entries)

Sweeps:
  Multiplier: [2.0, 2.5, 3.0]
  SL: [1.5, 2.0, 2.5] × ATR
  TP: [3.0, 4.0, 5.0] × ATR  (fixed TP mode)
  Trail: [2.0, 3.0] × ATR    (trailing stop mode, TP=0)
  Hours: 3-20 UTC, weekends off

Skips all already-deployed coins.

Usage:
    python research/sweep_supertrend.py
    python research/sweep_supertrend.py --variant long
    python research/sweep_supertrend.py --coins ethusdt,linkusdt
    python research/sweep_supertrend.py --min-pf 1.2 --min-trades 10
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from itertools import product
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import compute_atr, compute_ema, compute_volume_ma

DATA_DIR = "/Users/iceai/Work/ccbt/data"
OUTPUT_FILE = os.path.join(DATA_DIR, "sweep_supertrend_winners.json")

# Coins already deployed — skip in sweep
DEPLOYED_PREFIXES = {
    "btcusdt", "dogeusdt", "arbusdt", "wifusdt",
    "avaxusdt", "nearusdt", "solusdt",
    "1000shibusdt", "animeusdt", "arcusdt", "athusdt",
    "berausdt", "gunusdt", "injusdt", "trumpusdt", "trxusdt",
    "xlmusdt", "zetausdt", "taousdt", "renderusdt", "hbarusdt",
}

# ---------------------------------------------------------------------------
# Supertrend indicator
# ---------------------------------------------------------------------------

def compute_supertrend(
    df: pd.DataFrame,
    atr_period: int = 14,
    multiplier: float = 2.0,
) -> tuple[pd.Series, pd.Series]:
    """Compute Supertrend indicator using a vectorized numpy loop.

    The ratchet logic (bands only move in the favorable direction) requires
    sequential state. We use numpy arrays for fast element access instead of
    pandas iloc to minimize per-iteration overhead.

    Args:
        df: DataFrame with high, low, close columns.
        atr_period: ATR lookback period.
        multiplier: ATR multiplier for band width.

    Returns:
        Tuple of (supertrend line, direction series).
        direction: 1 = bullish (price above supertrend), -1 = bearish.
    """
    atr = compute_atr(df["high"], df["low"], df["close"], atr_period)
    hl2 = (df["high"].values + df["low"].values) / 2
    close = df["close"].values
    atr_vals = atr.values

    upper_band = hl2 + multiplier * atr_vals
    lower_band = hl2 - multiplier * atr_vals

    n = len(df)
    direction = np.ones(n, dtype=np.int8)
    supertrend = np.zeros(n, dtype=np.float64)

    for i in range(1, n):
        prev_close = close[i - 1]
        # Determine direction from previous close vs previous bands
        if prev_close > upper_band[i - 1]:
            direction[i] = 1
        elif prev_close < lower_band[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
            # Ratchet: bands only move in the favorable direction
            if direction[i] == 1:
                if lower_band[i] < lower_band[i - 1]:
                    lower_band[i] = lower_band[i - 1]
            else:
                if upper_band[i] > upper_band[i - 1]:
                    upper_band[i] = upper_band[i - 1]

        supertrend[i] = lower_band[i] if direction[i] == 1 else upper_band[i]

    return (
        pd.Series(supertrend, index=df.index),
        pd.Series(direction, index=df.index),
    )


# ---------------------------------------------------------------------------
# Shared base indicator builder
# ---------------------------------------------------------------------------

def _build_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add ATR, vol_ma, hour, dow columns."""
    df = df.copy()
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["vol_ma"] = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / df["vol_ma"].clip(lower=1e-10)
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek
    return df


# ---------------------------------------------------------------------------
# Strategy: Supertrend signal generator
# ---------------------------------------------------------------------------

def strategy_supertrend(
    df: pd.DataFrame,
    multiplier: float = 2.0,
    atr_period: int = 14,
    long_only: bool = False,
    hours_start: int = 3,
    hours_end: int = 20,
) -> pd.DataFrame:
    """Generate Supertrend signals on 1H data.

    Entry: direction change -1 -> 1 = LONG, 1 -> -1 = SHORT.
    Hours filter: 3-20 UTC. Weekend filter: off.

    Args:
        df: DataFrame with OHLCV + atr/hour/dow (from _build_base_indicators).
        multiplier: Supertrend ATR multiplier.
        atr_period: ATR period for Supertrend calculation.
        long_only: If True, only emit long signals (skip direction -1 entries).
        hours_start: Start hour UTC (inclusive).
        hours_end: End hour UTC (exclusive).

    Returns:
        DataFrame with 'signal' column (1=long, -1=short, 0=none).
    """
    df = df.copy()
    _supertrend, direction = compute_supertrend(df, atr_period=atr_period, multiplier=multiplier)
    df["st_direction"] = direction

    # Direction change = entry signal on the closed candle
    dir_prev = direction.shift(1)
    long_entry = (direction == 1) & (dir_prev == -1)
    short_entry = (direction == -1) & (dir_prev == 1)

    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5

    signals = pd.Series(0, index=df.index)
    signals[long_entry & in_hours & not_we] = 1
    if not long_only:
        signals[short_entry & in_hours & not_we] = -1

    # Warmup: need at least atr_period bars for ATR to converge + 1 for shift
    warmup = atr_period + 5
    signals.iloc[:warmup] = 0

    df["signal"] = signals
    return df


# ---------------------------------------------------------------------------
# Simulator (copied from mass_sweep_v2.py to avoid import coupling)
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
    """Event-driven trade simulator.

    df must have a 'signal' column (1=long, -1=short, 0=none) and 'atr'.
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

            # Update trailing stop
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
    """Return list of coin prefixes (lowercase), filtering deployed coins.

    Priority: CLI --coins arg > glob scan of *_1h_2y.csv files.
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
# Data loading helper
# ---------------------------------------------------------------------------

def _load_coin_1h(prefix: str) -> Optional[pd.DataFrame]:
    """Load 1H OHLCV CSV for a coin prefix, return None on failure."""
    for period in ["2y", "5y"]:
        fpath = os.path.join(DATA_DIR, f"{prefix}_1h_{period}.csv")
        if os.path.exists(fpath):
            try:
                raw = load_ohlcv(fpath)
                return _build_base_indicators(raw)
            except Exception as e:
                print(f"  WARN: could not load {fpath}: {e}")
    return None


# ---------------------------------------------------------------------------
# Main sweep runner
# ---------------------------------------------------------------------------

def run_sweep(
    coins: List[str],
    variant: str,
    min_pf: float,
    min_trades: int,
    min_dd: float,
) -> List[dict]:
    """Run full Supertrend sweep across all parameter combos.

    Args:
        coins: List of coin prefixes (e.g. ['ethusdt', 'bnbusdt']).
        variant: 'both' or 'long'.
        min_pf: Minimum profit factor for a winner.
        min_trades: Minimum trade count for a winner.
        min_dd: Maximum drawdown % allowed for a winner.

    Returns:
        List of winner dicts.
    """
    long_only = variant == "long"

    # Parameter grid
    multipliers = [2.0, 2.5, 3.0]
    sl_values = [1.5, 2.0, 2.5]
    tp_values = [3.0, 4.0, 5.0]        # Fixed TP combos
    trail_values = [2.0, 3.0]           # Trailing stop combos (no fixed TP)

    # Build full combo list: (multiplier, sl, tp, trail_mult)
    # tp_mult > 0 = fixed TP; trail_mult > 0 = trailing (tp_mult = 0)
    fixed_tp_combos = list(product(multipliers, sl_values, tp_values))
    trail_combos = list(product(multipliers, sl_values, trail_values))
    total_combos = len(fixed_tp_combos) + len(trail_combos)

    print(f"\nLoading 1H data for {len(coins)} coin(s)...")
    coin_data: Dict[str, pd.DataFrame] = {}
    for prefix in coins:
        df = _load_coin_1h(prefix)
        if df is not None:
            name = prefix.replace("usdt", "").upper()
            coin_data[name] = df
    print(f"Loaded {len(coin_data)} coins with 1H data.")

    prefix_map = {p.replace("usdt", "").upper(): p for p in coins}
    winners: List[dict] = []

    print(f"\nSweeping {total_combos} combos per coin × {len(coin_data)} coins = "
          f"{total_combos * len(coin_data)} total simulations")
    print(f"Variant: {variant.upper()}  |  Filter: PF >= {min_pf}, trades >= {min_trades}, DD <= {min_dd}%\n")

    header = (
        f"{'Coin':<8} {'Mult':>5} {'SL':>5} {'TP/Tr':>6} {'Mode':<8} "
        f"{'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}"
    )
    print(header)
    print("-" * len(header))

    for name in sorted(coin_data):
        df = coin_data[name]
        coin_winners: List[dict] = []

        for mult in multipliers:
            # Build signals once per multiplier (reused across SL/TP combos)
            try:
                df_sig = strategy_supertrend(df, multiplier=mult, long_only=long_only)
            except Exception as e:
                print(f"  ERROR {name} mult={mult}: {e}")
                continue

            # Fixed TP combos
            for sl_m, tp_m in product(sl_values, tp_values):
                try:
                    r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m, trail_mult=0)
                except Exception as e:
                    print(f"  ERROR sim {name} mult={mult} sl={sl_m} tp={tp_m}: {e}")
                    continue

                is_winner = (
                    r["pf"] >= min_pf
                    and r["trades"] >= min_trades
                    and r["dd"] <= min_dd
                )

                if r["trades"] >= 5:
                    marker = " *" if is_winner else ""
                    print(
                        f"{name:<8} {mult:>5.1f} {sl_m:>5.1f} {tp_m:>6.1f} {'fixed':<8}"
                        f" {r['trades']:>6} {r['tr_yr']:>5.0f}/yr"
                        f" {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%{marker}"
                    )

                if is_winner:
                    coin_winners.append({
                        "coin": name,
                        "prefix": prefix_map.get(name, name.lower() + "usdt"),
                        "strategy": f"supertrend_{variant}",
                        "timeframe": "1h",
                        "pf": round(r["pf"], 3),
                        "trades": r["trades"],
                        "tr_yr": round(r["tr_yr"], 1),
                        "wr_pct": round(r["wr"], 1),
                        "dd_pct": round(r["dd"], 1),
                        "ret_pct": round(r["ret"], 1),
                        "params": {
                            "multiplier": mult,
                            "sl_mult": sl_m,
                            "tp_mult": tp_m,
                            "trail_mult": 0,
                            "mode": "fixed_tp",
                            "long_only": long_only,
                        },
                    })

            # Trailing stop combos (no fixed TP)
            for sl_m, trail_m in product(sl_values, trail_values):
                try:
                    r = simulate(df_sig, sl_mult=sl_m, tp_mult=0, trail_mult=trail_m)
                except Exception as e:
                    print(f"  ERROR sim {name} mult={mult} sl={sl_m} trail={trail_m}: {e}")
                    continue

                is_winner = (
                    r["pf"] >= min_pf
                    and r["trades"] >= min_trades
                    and r["dd"] <= min_dd
                )

                if r["trades"] >= 5:
                    marker = " *" if is_winner else ""
                    print(
                        f"{name:<8} {mult:>5.1f} {sl_m:>5.1f} {trail_m:>6.1f} {'trail':<8}"
                        f" {r['trades']:>6} {r['tr_yr']:>5.0f}/yr"
                        f" {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%{marker}"
                    )

                if is_winner:
                    coin_winners.append({
                        "coin": name,
                        "prefix": prefix_map.get(name, name.lower() + "usdt"),
                        "strategy": f"supertrend_{variant}",
                        "timeframe": "1h",
                        "pf": round(r["pf"], 3),
                        "trades": r["trades"],
                        "tr_yr": round(r["tr_yr"], 1),
                        "wr_pct": round(r["wr"], 1),
                        "dd_pct": round(r["dd"], 1),
                        "ret_pct": round(r["ret"], 1),
                        "params": {
                            "multiplier": mult,
                            "sl_mult": sl_m,
                            "tp_mult": 0,
                            "trail_mult": trail_m,
                            "mode": "trailing",
                            "long_only": long_only,
                        },
                    })

        winners.extend(coin_winners)

    return winners


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _save_winners(winners: List[dict]) -> None:
    """Write winners to OUTPUT_FILE (overwrites if exists)."""
    with open(OUTPUT_FILE, "w") as f:
        json.dump(winners, f, indent=2)
    print(f"\nSaved {len(winners)} winner(s) to {OUTPUT_FILE}")


def _print_summary(winners: List[dict], min_pf: float, min_trades: int) -> None:
    """Print sorted summary of all winners."""
    print(f"\n{'=' * 80}")
    print(f"SWEEP COMPLETE — Winners (PF >= {min_pf}, trades >= {min_trades})")
    print("=" * 80)

    if not winners:
        print("No winners found.")
        return

    hdr = (
        f"{'Coin':<8} {'Strategy':<22} {'Mult':>5} {'SL':>5} {'TP/Tr':>6} {'Mode':<8}"
        f" {'PF':>6} {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'DD%':>6}"
    )
    print(hdr)
    print("-" * len(hdr))

    for w in sorted(winners, key=lambda x: -x["pf"]):
        p = w["params"]
        exit_val = p["tp_mult"] if p["mode"] == "fixed_tp" else p["trail_mult"]
        print(
            f"{w['coin']:<8} {w['strategy']:<22} {p['multiplier']:>5.1f}"
            f" {p['sl_mult']:>5.1f} {exit_val:>6.1f} {p['mode']:<8}"
            f" {w['pf']:>6.2f} {w['trades']:>7}"
            f" {w['tr_yr']:>5.0f}/yr {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}%"
        )

    print(f"\nTotal winners: {len(winners)}")

    # Coin frequency breakdown
    coin_counts: Dict[str, int] = {}
    for w in winners:
        coin_counts[w["coin"]] = coin_counts.get(w["coin"], 0) + 1
    print("\nWinners per coin:")
    for coin, cnt in sorted(coin_counts.items(), key=lambda x: -x[1]):
        print(f"  {coin:<10} {cnt} combo(s)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="sweep_supertrend: Supertrend 1H strategy sweep",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--variant",
        type=str,
        default="both",
        choices=["both", "long"],
        help="Signal mode: 'both' (long+short) or 'long' (long-only). Default: both",
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help="Comma-separated coin prefixes, e.g. ethusdt,bnbusdt",
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
    args = parse_args()

    if args.coins:
        coins = [c.lower().strip() for c in args.coins.split(",") if c.strip()]
    else:
        coins = discover_coins(None)
        # Ensure deployed coins are excluded when running full sweep
        coins = [c for c in coins if c not in DEPLOYED_PREFIXES]

    if not coins:
        print("No coins found. Add *_1h_2y.csv files to data/ or pass --coins.")
        sys.exit(1)

    print(f"Sweeping {len(coins)} coin(s): {', '.join(coins[:10])}{'...' if len(coins) > 10 else ''}")
    print(f"Filter: PF >= {args.min_pf}, trades >= {args.min_trades}, DD <= {args.max_dd}%")

    winners = run_sweep(
        coins=coins,
        variant=args.variant,
        min_pf=args.min_pf,
        min_trades=args.min_trades,
        min_dd=args.max_dd,
    )

    if winners:
        _save_winners(winners)

    _print_summary(winners, min_pf=args.min_pf, min_trades=args.min_trades)
