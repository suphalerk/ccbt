"""
sweep_keltner.py — Keltner Channel breakout strategy sweep.

Keltner Channel:
  middle = EMA(20)
  ATR    = ATR(20)
  upper  = middle + mult * ATR
  lower  = middle - mult * ATR

Breakout signals:
  LONG  : close crosses ABOVE upper band
  SHORT : close crosses BELOW lower band
  Also tests long-only variant

Sweep dimensions:
  channel_mult : [1.5, 2.0, 2.5]
  sl_mult      : [1.5, 2.0, 2.5] × ATR
  tp_mult      : [3.0, 4.0, 5.0] × ATR

Hours filter  : 03:00–20:00 UTC, weekends off
Coins         : all *_1h_2y.csv, deployed coins skipped
Winners saved : data/sweep_keltner_winners.json  (PF >= 1.3, trades >= 10, DD <= 30%)

Usage:
    python research/sweep_keltner.py
    python research/sweep_keltner.py --coins bnbusdt,linkusdt
    python research/sweep_keltner.py --min-pf 1.4 --min-trades 15 --max-dd 25
    python research/sweep_keltner.py --variant long_only
    python research/sweep_keltner.py --variant both
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_DIR = "/Users/iceai/Work/ccbt/data"
WINNERS_PATH = os.path.join(DATA_DIR, "sweep_keltner_winners.json")

HOURS_START = 3
HOURS_END = 20

CHANNEL_MULTS = [1.5, 2.0, 2.5]
SL_MULTS = [1.5, 2.0, 2.5]
TP_MULTS = [3.0, 4.0, 5.0]

# Coins already deployed — skip them in the sweep
DEPLOYED_PREFIXES = {
    "btcusdt", "dogeusdt", "arbusdt", "wifusdt",
    "avaxusdt", "nearusdt", "solusdt",
    "1000shibusdt", "animeusdt", "arcusdt", "athusdt",
    "berausdt", "gunusdt", "injusdt", "trumpusdt", "trxusdt",
    "xlmusdt", "zetausdt",
}

WARMUP_BARS = 60  # EMA(20) + ATR(20) need ~40 bars; 60 is safe


# ---------------------------------------------------------------------------
# Indicator builders
# ---------------------------------------------------------------------------

def _build_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add ATR, volume MA, hour/weekday columns used by simulate()."""
    df = df.copy()
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["vol_ma"] = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / df["vol_ma"].clip(lower=1e-10)
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek
    return df


def add_keltner(df: pd.DataFrame, mult: float, ema_period: int = 20, atr_period: int = 20) -> pd.DataFrame:
    """Add Keltner Channel bands to df.

    Columns added:
        kc_middle : EMA(ema_period) of close
        kc_upper  : middle + mult * ATR(atr_period)
        kc_lower  : middle - mult * ATR(atr_period)
        kc_atr    : ATR used for channel construction (separate from signal ATR)
    """
    df = df.copy()
    df["kc_middle"] = compute_ema(df["close"], ema_period)
    df["kc_atr"] = compute_atr(df["high"], df["low"], df["close"], atr_period)
    df["kc_upper"] = df["kc_middle"] + mult * df["kc_atr"]
    df["kc_lower"] = df["kc_middle"] - mult * df["kc_atr"]
    return df


def build_signals(
    df: pd.DataFrame,
    mult: float,
    long_only: bool = False,
    hours_start: int = HOURS_START,
    hours_end: int = HOURS_END,
) -> pd.DataFrame:
    """Generate Keltner breakout signals.

    LONG  : close crosses above upper band  (prev close <= prev upper)
    SHORT : close crosses below lower band  (prev close >= prev lower)
           skipped when long_only=True

    Returns df with a 'signal' column (1=long, -1=short, 0=none).
    """
    df = df.copy()
    df = add_keltner(df, mult)

    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5

    # Breakout: close crosses above upper band
    cross_up = (
        (df["close"] > df["kc_upper"])
        & (df["close"].shift(1) <= df["kc_upper"].shift(1))
    )

    # Breakout: close crosses below lower band
    cross_down = (
        (df["close"] < df["kc_lower"])
        & (df["close"].shift(1) >= df["kc_lower"].shift(1))
    )

    signals = pd.Series(0, index=df.index, dtype=int)
    signals[cross_up & in_hours & not_we] = 1
    if not long_only:
        signals[cross_down & in_hours & not_we] = -1

    # Suppress signals during warmup
    signals.iloc[:WARMUP_BARS] = 0
    df["signal"] = signals
    return df


# ---------------------------------------------------------------------------
# Simulator (copied from mass_sweep_v2.py to avoid import coupling)
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
    risk_pct: float = 0.10,
    leverage: float = 25,
    commission: float = 0.00055,
    slippage: float = 0.0002,
    trail_mult: float = 0,
) -> dict:
    """Event-driven trade simulator.

    df must have columns: signal, atr, hour, dow, high, low, close.
    Returns metrics dict: trades, tr_yr, wr, pf, dd, balance, ret.
    """
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades: list = []
    position: Optional[dict] = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]

        # ---- Manage open position ----
        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]

            # Trailing stop ratchet
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
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                trades.append({"pnl": pnl, "reason": "sl" if hit_sl else "tp"})
                position = None
                if balance <= 0:
                    break

        # ---- Open new position ----
        if position is None and sig_row.get("signal", 0) != 0:
            # Weekend guard (belt-and-suspenders)
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
            tp_d = atr * tp_mult if tp_mult > 0 else 0.0
            if side == 1:
                sl_p = entry_p - sl_d
                tp_p = entry_p + tp_d if tp_d > 0 else 0.0
            else:
                sl_p = entry_p + sl_d
                tp_p = entry_p - tp_d if tp_d > 0 else 0.0
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
        "tr_yr": total / yr if yr > 0 else 0.0,
        "wr": wr,
        "pf": pf,
        "dd": max_dd * 100,
        "balance": balance,
        "ret": (balance - 1000.0) / 10.0,
    }


# ---------------------------------------------------------------------------
# Coin discovery
# ---------------------------------------------------------------------------

def discover_coins(coins_arg: Optional[List[str]]) -> List[str]:
    """Return list of lowercase coin prefixes from data/*.csv, excluding deployed coins.

    Priority: CLI --coins > data/liquid_coins.json > glob scan.
    """
    if coins_arg:
        return [c.lower().strip() for c in coins_arg if c.strip()]

    liquid_path = os.path.join(DATA_DIR, "liquid_coins.json")
    if os.path.exists(liquid_path):
        with open(liquid_path) as fh:
            raw = json.load(fh)
        if raw and isinstance(raw[0], dict):
            result = []
            for entry in raw:
                if "prefix" in entry:
                    result.append(entry["prefix"].lower())
                else:
                    sym = entry.get("symbol", "")
                    result.append(
                        sym.lower().replace("/", "").replace(":", "").replace("usdt", "usdt", 1)
                    )
            return [p for p in result if p not in DEPLOYED_PREFIXES]
        return [
            s.lower().replace("/", "").replace(":", "")
            for s in raw
            if s.lower() not in DEPLOYED_PREFIXES
        ]

    pattern = os.path.join(DATA_DIR, "*_1h_2y.csv")
    files = sorted(glob.glob(pattern))
    prefixes = []
    for fpath in files:
        base = os.path.basename(fpath)
        prefix = base.replace("_1h_2y.csv", "")
        if prefix not in DEPLOYED_PREFIXES:
            prefixes.append(prefix)
    return prefixes


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_coin_1h(prefix: str) -> Optional[pd.DataFrame]:
    """Load 1h CSV for a coin prefix.  Returns None on failure."""
    for period in ["2y", "5y"]:
        fpath = os.path.join(DATA_DIR, f"{prefix}_1h_{period}.csv")
        if os.path.exists(fpath):
            try:
                raw = load_ohlcv(fpath)
                return _build_base_indicators(raw)
            except Exception as exc:
                print(f"  WARN: could not load {fpath}: {exc}")
    return None


# ---------------------------------------------------------------------------
# Sweep runner
# ---------------------------------------------------------------------------

def run_keltner_sweep(
    coins: List[str],
    min_pf: float,
    min_trades: int,
    max_dd: float,
    long_only_only: bool = False,
    both_only: bool = False,
) -> List[dict]:
    """Full Keltner sweep across all parameter combinations.

    For each coin × channel_mult × (long_only / both-sides) × sl_mult × tp_mult:
        - Build signals
        - Run simulate()
        - Collect winners (PF >= min_pf, trades >= min_trades, DD <= max_dd)

    Returns list of winner dicts.
    """
    all_combos = list(product(CHANNEL_MULTS, SL_MULTS, TP_MULTS))
    variants = []
    if not both_only:
        variants.append(("long_only", True))
    if not long_only_only:
        variants.append(("both", False))

    total_coins = len(coins)
    loaded_count = 0

    header = (
        f"{'Coin':<10} {'Variant':<12} {'ChMult':>7} {'SL':>5} {'TP':>5}"
        f" {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}"
    )
    print(f"\n{header}")
    print("-" * len(header))

    winners: List[dict] = []

    for idx, prefix in enumerate(coins, 1):
        name = prefix.replace("usdt", "").upper()
        df = _load_coin_1h(prefix)
        if df is None:
            continue
        loaded_count += 1

        coin_best: dict = {}  # (variant, ch_mult) -> best result for dedup printing

        for variant_name, is_long_only in variants:
            for ch_mult, sl_m, tp_m in all_combos:
                try:
                    df_sig = build_signals(df, mult=ch_mult, long_only=is_long_only)
                    r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m)
                except Exception as exc:
                    print(f"  ERROR {name} {variant_name} ch={ch_mult} sl={sl_m} tp={tp_m}: {exc}")
                    continue

                if r["trades"] < 5:
                    continue  # Too few trades to print or collect

                is_winner = (
                    r["pf"] >= min_pf
                    and r["trades"] >= min_trades
                    and r["dd"] <= max_dd
                )
                marker = " *" if is_winner else ""

                print(
                    f"{name:<10} {variant_name:<12} {ch_mult:>7.1f} {sl_m:>5.1f} {tp_m:>5.1f}"
                    f" {r['trades']:>6} {r['tr_yr']:>5.1f}/yr"
                    f" {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%{marker}"
                )

                if is_winner:
                    winners.append({
                        "coin": name,
                        "prefix": prefix,
                        "strategy": f"keltner_{variant_name}",
                        "timeframe": "1h",
                        "channel_mult": ch_mult,
                        "pf": round(r["pf"], 3),
                        "trades": r["trades"],
                        "tr_yr": round(r["tr_yr"], 1),
                        "wr_pct": round(r["wr"], 1),
                        "dd_pct": round(r["dd"], 1),
                        "ret_pct": round(r["ret"], 1),
                        "params": {
                            "channel_mult": ch_mult,
                            "sl_mult": sl_m,
                            "tp_mult": tp_m,
                            "long_only": is_long_only,
                        },
                    })

        if idx % 10 == 0 or idx == total_coins:
            print(f"  [{idx}/{total_coins}] loaded={loaded_count} winners_so_far={len(winners)}")

    return winners


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_winners(winners: List[dict]) -> None:
    """Write winners to data/sweep_keltner_winners.json."""
    with open(WINNERS_PATH, "w") as fh:
        json.dump(winners, fh, indent=2)
    print(f"\nSaved {len(winners)} winner(s) to {WINNERS_PATH}")


def print_summary(winners: List[dict], min_pf: float, min_trades: int, max_dd: float) -> None:
    """Print winner summary table sorted by PF descending."""
    print(f"\n{'=' * 70}")
    print(f"KELTNER SWEEP WINNERS  (PF >= {min_pf}, trades >= {min_trades}, DD <= {max_dd}%)")
    print("=" * 70)

    if not winners:
        print("No winners found.")
        return

    hdr = (
        f"{'Coin':<10} {'Strategy':<18} {'ChMult':>7} {'SL':>5} {'TP':>5}"
        f" {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}"
    )
    print(hdr)
    print("-" * len(hdr))

    for w in sorted(winners, key=lambda x: -x["pf"]):
        p = w["params"]
        print(
            f"{w['coin']:<10} {w['strategy']:<18} {p['channel_mult']:>7.1f}"
            f" {p['sl_mult']:>5.1f} {p['tp_mult']:>5.1f}"
            f" {w['trades']:>7} {w['tr_yr']:>5.1f}/yr"
            f" {w['wr_pct']:>5.1f}% {w['pf']:>6.2f} {w['dd_pct']:>5.1f}%"
        )

    print(f"\nTotal winners: {len(winners)}")

    # Best per coin
    print(f"\n--- Best result per coin ---")
    seen: dict = {}
    for w in sorted(winners, key=lambda x: -x["pf"]):
        if w["coin"] not in seen:
            seen[w["coin"]] = w
    best_hdr = f"{'Coin':<10} {'Strategy':<18} {'ChMult':>7} {'SL':>5} {'TP':>5} {'PF':>6} {'Trades':>7}"
    print(best_hdr)
    print("-" * len(best_hdr))
    for coin, w in sorted(seen.items(), key=lambda kv: -kv[1]["pf"]):
        p = w["params"]
        print(
            f"{w['coin']:<10} {w['strategy']:<18} {p['channel_mult']:>7.1f}"
            f" {p['sl_mult']:>5.1f} {p['tp_mult']:>5.1f}"
            f" {w['pf']:>6.2f} {w['trades']:>7}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="sweep_keltner: Keltner Channel breakout strategy sweep on 1h data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python research/sweep_keltner.py\n"
            "  python research/sweep_keltner.py --variant long_only\n"
            "  python research/sweep_keltner.py --coins bnbusdt,linkusdt\n"
            "  python research/sweep_keltner.py --min-pf 1.4 --min-trades 15 --max-dd 25\n"
        ),
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help="Comma-separated coin prefixes, e.g. bnbusdt,linkusdt",
    )
    parser.add_argument(
        "--variant",
        type=str,
        default="all",
        choices=["all", "long_only", "both"],
        help="Which variant to test: all (default), long_only, both",
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

    # Resolve coins
    if args.coins:
        coins = [c.lower().strip() for c in args.coins.split(",") if c.strip()]
        # When user explicitly passes coins, don't auto-filter deployed ones
    else:
        coins = discover_coins(None)
        coins = [c for c in coins if c not in DEPLOYED_PREFIXES]

    if not coins:
        print("No coins found. Add *_1h_2y.csv files to data/ or pass --coins.")
        sys.exit(1)

    long_only_only = args.variant == "long_only"
    both_only = args.variant == "both"

    print(f"Keltner Channel Breakout Sweep")
    print(f"Coins        : {len(coins)} — {', '.join(coins[:8])}{'...' if len(coins) > 8 else ''}")
    print(f"Variant      : {args.variant}")
    print(f"Channel mults: {CHANNEL_MULTS}")
    print(f"SL mults     : {SL_MULTS}")
    print(f"TP mults     : {TP_MULTS}")
    print(f"Winner filter: PF >= {args.min_pf}, trades >= {args.min_trades}, DD <= {args.max_dd}%")
    print(f"Total combos : {len(coins)} coins × {len(CHANNEL_MULTS)} ch × {len(SL_MULTS)} sl × {len(TP_MULTS)} tp × {'1 variant' if args.variant != 'all' else '2 variants'}")

    winners = run_keltner_sweep(
        coins=coins,
        min_pf=args.min_pf,
        min_trades=args.min_trades,
        max_dd=args.max_dd,
        long_only_only=long_only_only,
        both_only=both_only,
    )

    print_summary(winners, args.min_pf, args.min_trades, args.max_dd)

    if winners:
        save_winners(winners)
    else:
        print("\nNo winners — nothing saved.")
