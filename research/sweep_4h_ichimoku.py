"""
sweep_4h_ichimoku.py — Ichimoku 9/26/52 sweep on 4H candles (resampled from 1H data).

Loads 1H OHLCV data from data/{coin}_1h_2y.csv (or _1h_5y.csv), resamples to 4H,
computes Ichimoku, generates Tenkan/Kijun cross above/below cloud signals, and
sweeps SL/TP combos for both standard (long+short) and long-only variants.

Signal: Tenkan crosses Kijun while price is above/below cloud (no look-ahead).
Hours filter: 03-20 UTC (on 4H bar open hour).

Usage:
    python research/sweep_4h_ichimoku.py
    python research/sweep_4h_ichimoku.py --coins bnbusdt,linkusdt
    python research/sweep_4h_ichimoku.py --min-pf 1.2 --min-trades 12
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv

DATA_DIR = "/Users/iceai/Work/ccbt/data"

# ---------------------------------------------------------------------------
# Target coins (large-cap + infrastructure)
# ---------------------------------------------------------------------------

DEFAULT_COINS = [
    "bnbusdt", "linkusdt", "adausdt", "xrpusdt", "ltcusdt", "atomusdt",
    "dotusdt", "uniusdt", "bchusdt", "aaveusdt", "filusdt", "icpusdt",
    "suiusdt", "renderusdt", "fetusdt", "opusdt", "hbarusdt", "algousdt",
    "sandusdt", "etcusdt", "tonusdt", "enausdt", "taousdt", "aptusdt",
    "wldusdt", "qntusdt",
    # Bonus — test if data exists
    "paxgusdt", "xauusdt",
]

# SL/TP combos to sweep
SL_MULTS = [1.5, 2.0, 2.5]
TP_MULTS = [4.0, 5.0]

# Hours filter (inclusive start, exclusive end)
HOURS_START = 3
HOURS_END = 20

# Ichimoku standard periods
TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52

# Warmup: need 52 bars for Span B + 26 bar cloud shift = 78 bars minimum
WARMUP_BARS = 80


# ---------------------------------------------------------------------------
# Resample 1H → 4H
# ---------------------------------------------------------------------------

def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV data to 4H candles.

    Uses standard OHLCV aggregation. Drops any incomplete 4H bars (dropna).

    Args:
        df_1h: DataFrame with DatetimeIndex and open/high/low/close/volume columns.

    Returns:
        4H DataFrame with same column schema.
    """
    df_4h = (
        df_1h.resample("4h").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )
    return df_4h


# ---------------------------------------------------------------------------
# Indicators (self-contained, no bot dependency for 4H sweep)
# ---------------------------------------------------------------------------

def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def add_4h_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add Ichimoku 9/26/52, ATR(14), hour, and dow to a 4H DataFrame.

    Cloud is computed bias-free: cloud_top/cloud_bottom at bar T equals
    span_a/span_b from T-26, matching the standard Ichimoku display convention.

    Args:
        df: 4H OHLCV DataFrame with DatetimeIndex.

    Returns:
        Copy of df with indicator columns added.
    """
    df = df.copy()

    # Tenkan-sen (9-period midpoint)
    df["tenkan"] = (
        df["high"].rolling(TENKAN_PERIOD).max() + df["low"].rolling(TENKAN_PERIOD).min()
    ) / 2

    # Kijun-sen (26-period midpoint)
    df["kijun"] = (
        df["high"].rolling(KIJUN_PERIOD).max() + df["low"].rolling(KIJUN_PERIOD).min()
    ) / 2

    # Span A raw (unshifted): average of Tenkan and Kijun
    span_a_raw = (df["tenkan"] + df["kijun"]) / 2

    # Span B raw (unshifted): 52-period midpoint
    span_b_raw = (
        df["high"].rolling(SENKOU_B_PERIOD).max() + df["low"].rolling(SENKOU_B_PERIOD).min()
    ) / 2

    # Cloud at bar T = span values from T-26 (standard Ichimoku: cloud is projected
    # forward 26 periods in display, so current cloud = values from 26 bars back)
    span_a_current = span_a_raw.shift(KIJUN_PERIOD)
    span_b_current = span_b_raw.shift(KIJUN_PERIOD)

    df["cloud_top"] = pd.concat([span_a_current, span_b_current], axis=1).max(axis=1)
    df["cloud_bottom"] = pd.concat([span_a_current, span_b_current], axis=1).min(axis=1)

    # Cross detection: Tenkan crosses Kijun
    df["cross_up"] = (df["tenkan"] > df["kijun"]) & (df["tenkan"].shift(1) <= df["kijun"].shift(1))
    df["cross_down"] = (df["tenkan"] < df["kijun"]) & (df["tenkan"].shift(1) >= df["kijun"].shift(1))

    # ATR(14) for position sizing
    df["atr"] = _compute_atr(df["high"], df["low"], df["close"], 14)

    # Time helpers for hours + weekend filter
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek  # 0=Mon, 5=Sat, 6=Sun

    return df


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def generate_signals(
    df: pd.DataFrame,
    long_only: bool = False,
    hours_start: int = HOURS_START,
    hours_end: int = HOURS_END,
) -> pd.DataFrame:
    """Generate Ichimoku Tenkan/Kijun cross signals on 4H data.

    Entry condition (long):
        - Tenkan crosses above Kijun
        - Close is above cloud_top (price above cloud)
        - Within trading hours
        - Not weekend

    Entry condition (short, standard only):
        - Tenkan crosses below Kijun
        - Close is below cloud_bottom (price below cloud)
        - Within trading hours
        - Not weekend

    Args:
        df: 4H DataFrame with indicators already computed.
        long_only: If True, only generate long signals.
        hours_start: UTC hour for session open (inclusive).
        hours_end: UTC hour for session close (exclusive).

    Returns:
        Copy of df with 'signal' column (1=long, -1=short, 0=none).
    """
    df = df.copy()
    signals = pd.Series(0, index=df.index)

    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5

    # Long signal: cross up + price above cloud
    above_cloud = df["close"] > df["cloud_top"]
    long_cond = df["cross_up"] & above_cloud & in_hours & not_we
    signals[long_cond] = 1

    if not long_only:
        # Short signal: cross down + price below cloud
        below_cloud = df["close"] < df["cloud_bottom"]
        short_cond = df["cross_down"] & below_cloud & in_hours & not_we
        signals[short_cond] = -1

    # Blank warmup bars — need 52 + 26 = 78 bars minimum
    signals.iloc[:WARMUP_BARS] = 0

    df["signal"] = signals
    return df


# ---------------------------------------------------------------------------
# Simulator (copied from mass_sweep.py with minor 4H adjustments)
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    sl_mult: float = 2.0,
    tp_mult: float = 5.0,
    risk_pct: float = 0.10,
    leverage: float = 10,
    commission: float = 0.00055,
    slippage: float = 0.0002,
    trail_mult: float = 0,
) -> dict:
    """Event-driven trade simulator.

    df must have a 'signal' column (1=long, -1=short, 0=none) and 'atr' column.
    Entries use the bar AFTER the signal bar (next open, approximated as next close).
    SL/TP checked against the entry bar's high/low.

    Returns:
        Dict with trades, tr_yr, wr, pf, dd, balance, ret.
    """
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades: List[Dict] = []
    position: Optional[Dict] = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]

        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]

            # Ratchet trailing stop (only moves in profit direction)
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

            hit_sl = (side == 1 and row["low"] <= sl) or (
                side == -1 and row["high"] >= sl
            )
            hit_tp = (
                (side == 1 and row["high"] >= tp) or (side == -1 and row["low"] <= tp)
            ) if tp > 0 else False

            if hit_sl or hit_tp:
                exit_p = sl if hit_sl else tp
                pnl_pct = (
                    side * (exit_p - entry) / entry - (commission + slippage) * 2
                )
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

        if position is None and sig_row.get("signal", 0) != 0:
            # Weekend guard (also set in signal gen, double-check here)
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
        "tr_yr": round(total / yr, 1) if yr > 0 else 0.0,
        "wr": round(wr, 1),
        "pf": round(pf, 3),
        "dd": round(max_dd * 100, 1),
        "balance": round(balance, 2),
        "ret": round((balance - 1000) / 10, 1),
    }


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(
    coins: List[str],
    min_pf: float = 1.2,
    min_trades: int = 12,
) -> List[Dict]:
    """Run 4H Ichimoku sweep across coins.

    For each coin:
    - Load 1H data, resample to 4H
    - Compute Ichimoku indicators
    - Test all (sl_mult, tp_mult) combos × (standard, long-only) = 12 variants per coin
    - Record winners (PF >= min_pf AND trades >= min_trades)

    Args:
        coins: List of coin prefixes (e.g. ['bnbusdt', 'linkusdt']).
        min_pf: Minimum profit factor threshold for winners.
        min_trades: Minimum trade count threshold for winners.

    Returns:
        List of winner dicts sorted by PF descending.
    """
    all_results: List[Dict] = []
    winners: List[Dict] = []

    print(f"\nLoading 1H data for {len(coins)} coin(s) and resampling to 4H...")

    loaded: Dict[str, pd.DataFrame] = {}
    for prefix in coins:
        name = prefix.replace("usdt", "").upper()
        for period in ["2y", "5y"]:
            fpath = os.path.join(DATA_DIR, f"{prefix}_1h_{period}.csv")
            if os.path.exists(fpath):
                try:
                    df_1h = load_ohlcv(fpath)
                    df_4h = resample_to_4h(df_1h)
                    df_4h = add_4h_indicators(df_4h)
                    loaded[name] = df_4h
                    break
                except Exception as exc:
                    print(f"  WARN: could not load {fpath}: {exc}")

    print(f"Loaded: {len(loaded)} coins with 4H data\n")

    if not loaded:
        print("No data loaded. Exiting.")
        return []

    # Build sweep combos: (label, long_only, sl_mult, tp_mult)
    combos: List[Tuple[str, bool, float, float]] = []
    for sl in SL_MULTS:
        for tp in TP_MULTS:
            combos.append((f"SL{sl:.1f}/TP{tp:.1f}", False, sl, tp))
            combos.append((f"SL{sl:.1f}/TP{tp:.1f}-LO", True, sl, tp))

    # Header
    header = (
        f"{'Coin':<8} {'Variant':<20} {'Trades':>6} {'Tr/yr':>6} "
        f"{'WR%':>6} {'PF':>6} {'DD%':>6}"
    )
    print(header)
    print("-" * len(header))

    for name in sorted(loaded.keys()):
        df = loaded[name]
        prefix = name.lower() + "usdt"

        for label, long_only, sl_mult, tp_mult in combos:
            variant_name = f"{'LongOnly' if long_only else 'Standard'}-{label}"
            try:
                df_sig = generate_signals(df, long_only=long_only)
                r = simulate(df_sig, sl_mult=sl_mult, tp_mult=tp_mult)
            except Exception as exc:
                print(f"  ERROR {name} {variant_name}: {exc}")
                continue

            row_data = {
                "coin": name,
                "prefix": prefix,
                "strategy": "Ichimoku-4H",
                "timeframe": "4h",
                "variant": variant_name,
                "long_only": long_only,
                "sl_mult": sl_mult,
                "tp_mult": tp_mult,
                "pf": r["pf"],
                "trades": r["trades"],
                "tr_yr": r["tr_yr"],
                "wr_pct": r["wr"],
                "dd_pct": r["dd"],
                "ret_pct": r["ret"],
            }
            all_results.append(row_data)

            is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades
            marker = " *" if is_winner else ""

            if r["trades"] >= 4:
                print(
                    f"{name:<8} {variant_name:<20} {r['trades']:>6} {r['tr_yr']:>5.0f}/yr"
                    f" {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%{marker}"
                )

            if is_winner:
                winners.append(row_data)

    # Summary table sorted by PF descending
    print("\n" + "=" * 75)
    print(f"SWEEP RESULTS — Winners (PF >= {min_pf}, trades >= {min_trades})")
    print("=" * 75)
    if winners:
        hdr = (
            f"{'Coin':<8} {'Variant':<22} {'PF':>6} {'Trades':>7} "
            f"{'Tr/yr':>6} {'WR%':>6} {'DD%':>6}"
        )
        print(hdr)
        print("-" * len(hdr))
        for w in sorted(winners, key=lambda x: -x["pf"]):
            print(
                f"{w['coin']:<8} {w['variant']:<22} {w['pf']:>6.2f} {w['trades']:>7}"
                f" {w['tr_yr']:>5.0f}/yr {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}%"
            )
    else:
        print("  No winners found.")

    tested = len(all_results)
    print(f"\nTotal winners: {len(winners)} / {tested} tested")

    # Best config per coin (highest PF that also meets min_trades)
    print("\n" + "=" * 75)
    print("BEST CONFIG PER COIN (highest PF, meets criteria)")
    print("=" * 75)
    if winners:
        best_per_coin: Dict[str, Dict] = {}
        for w in winners:
            coin = w["coin"]
            if coin not in best_per_coin or w["pf"] > best_per_coin[coin]["pf"]:
                best_per_coin[coin] = w
        bh = f"{'Coin':<8} {'Variant':<22} {'PF':>6} {'Trades':>7} {'WR%':>6} {'DD%':>6}"
        print(bh)
        print("-" * len(bh))
        for coin, w in sorted(best_per_coin.items(), key=lambda x: -x[1]["pf"]):
            print(
                f"{coin:<8} {w['variant']:<22} {w['pf']:>6.2f} {w['trades']:>7}"
                f" {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}%"
            )
    else:
        print("  No winners found.")

    # Save winners
    out_path = os.path.join(DATA_DIR, "sweep_4h_winners.json")
    with open(out_path, "w") as f:
        json.dump(winners, f, indent=2)
    print(f"\nWinners saved to {out_path}")

    return winners


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="4H Ichimoku sweep across large-cap coins (resampled from 1H data)"
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help="Comma-separated coin prefixes, e.g. bnbusdt,linkusdt (default: all target coins)",
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
        default=12,
        help="Minimum trade count for winners (default: 12)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    coins = (
        [c.strip().lower() for c in args.coins.split(",") if c.strip()]
        if args.coins
        else DEFAULT_COINS
    )

    print(
        f"4H Ichimoku sweep — {len(coins)} coins, "
        f"SL {SL_MULTS} × TP {TP_MULTS}, "
        f"hours {HOURS_START}-{HOURS_END} UTC"
    )
    print(f"Acceptance: PF >= {args.min_pf}, trades >= {args.min_trades}")

    run_sweep(coins, min_pf=args.min_pf, min_trades=args.min_trades)
