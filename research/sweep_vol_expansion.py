"""sweep_vol_expansion.py — Volatility Expansion Breakout strategy sweep on 1H data.

Enters trades when ATR is expanding above its rolling average — signals trend beginning.
Pure volatility-driven entry: ATR expansion + price breakout above/below recent high/low
+ EMA(50) trend filter.

Different from:
  - Keltner: price vs bands
  - Supertrend: trailing bands
  This is purely ATR expansion state + price structure breakout.

Variants:
  both  — Long + Short signals
  long  — Long-only (skip short entries)

Sweeps:
  atr_threshold:  [1.3, 1.5, 1.8, 2.0]   — ATR must be > atr_ma * threshold
  lookback:       [1, 3]                   — recent high/low breakout lookback candles
  SL:             [1.5, 2.0, 2.5] × ATR
  TP:             [3.0, 4.0, 5.0] × ATR  (fixed TP mode)
  Trail:          [2.5, 3.0] × ATR        (trailing stop mode, TP=0)
  Hours: 3-20 UTC, weekdays only

Skips all already-deployed coins.
Also tests 4H (resample 1H→4H) for any coin passing 1H filter.

Usage:
    python research/sweep_vol_expansion.py
    python research/sweep_vol_expansion.py --variant long
    python research/sweep_vol_expansion.py --coins ethusdt,linkusdt
    python research/sweep_vol_expansion.py --min-pf 1.3 --min-trades 10
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
OUTPUT_FILE = os.path.join(DATA_DIR, "sweep_vol_expansion_winners.json")

# Coins already deployed — skip in sweep
DEPLOYED_PREFIXES = {
    "btcusdt", "dogeusdt", "arbusdt", "wifusdt",
    "avaxusdt", "nearusdt", "solusdt",
    "gunusdt", "berausdt", "athusdt", "zetausdt",
    "arcusdt", "animeusdt", "trumpusdt", "injusdt",
    "xlmusdt", "1000shibusdt", "trxusdt", "taousdt",
    "renderusdt", "hbarusdt", "polusdt", "polyxusdt",
    "fetusdt", "algousdt", "mstrusdt", "xagusdt", "saharausdt",
}

ATR_PERIOD = 14
ATR_MA_PERIOD = 20
EMA50_PERIOD = 50


# ---------------------------------------------------------------------------
# Indicator builder
# ---------------------------------------------------------------------------

def _build_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add ATR, ATR-MA, EMA(50), vol_ma, hour, dow columns.

    Args:
        df: Raw OHLCV DataFrame.

    Returns:
        DataFrame with computed indicators.
    """
    df = df.copy()
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], ATR_PERIOD)
    df["atr_ma"] = df["atr"].rolling(ATR_MA_PERIOD).mean()
    df["ema50"] = compute_ema(df["close"], EMA50_PERIOD)
    df["vol_ma"] = compute_volume_ma(df["volume"], 20)
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek
    return df


# ---------------------------------------------------------------------------
# Strategy: Volatility Expansion Breakout signal generator
# ---------------------------------------------------------------------------

def strategy_vol_expansion(
    df: pd.DataFrame,
    atr_threshold: float = 1.5,
    lookback: int = 1,
    long_only: bool = False,
    hours_start: int = 3,
    hours_end: int = 20,
) -> pd.DataFrame:
    """Generate Volatility Expansion Breakout signals.

    Entry conditions:
      LONG:  ATR > ATR_MA * threshold  AND  close > high.shift(lookback)  AND  close > EMA(50)
      SHORT: ATR > ATR_MA * threshold  AND  close < low.shift(lookback)   AND  close < EMA(50)

    Uses closed candles only (signal computed on current candle, enter on next).
    Hours filter: 3-20 UTC. Weekend filter: off.

    Args:
        df: DataFrame with OHLCV + atr/atr_ma/ema50/hour/dow (from _build_base_indicators).
        atr_threshold: ATR must exceed ATR_MA by this factor (e.g. 1.5 = 50% above average).
        lookback: Lookback period for recent high/low breakout (1 or 3 candles).
        long_only: If True, only emit long signals.
        hours_start: Start hour UTC (inclusive).
        hours_end: End hour UTC (exclusive).

    Returns:
        DataFrame with 'signal' column (1=long, -1=short, 0=none).
    """
    df = df.copy()

    # Volatility state: is ATR expanding above its rolling mean?
    atr_expanding = df["atr"] > df["atr_ma"] * atr_threshold

    # Price structure breakout
    break_high = df["close"] > df["high"].shift(lookback)
    break_low = df["close"] < df["low"].shift(lookback)

    # Trend filter via EMA(50)
    above_ema50 = df["close"] > df["ema50"]
    below_ema50 = df["close"] < df["ema50"]

    # Combined signals
    long_signal = atr_expanding & break_high & above_ema50
    short_signal = atr_expanding & break_low & below_ema50

    # Time filters
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5

    signals = pd.Series(0, index=df.index)
    signals[long_signal & in_hours & not_we] = 1
    if not long_only:
        signals[short_signal & in_hours & not_we] = -1

    # Warmup: EMA50 + ATR_MA period + lookback + buffer
    warmup = EMA50_PERIOD + ATR_MA_PERIOD + lookback + 5
    signals.iloc[:warmup] = 0

    df["signal"] = signals
    return df


# ---------------------------------------------------------------------------
# Simulator (matches sweep_supertrend.py pattern)
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

    df must have a 'signal' column (1=long, -1=short, 0=none) and 'atr'.
    Returns metrics dict with trades, tr_yr, wr, pf, dd, balance, ret.

    Args:
        df: Signal DataFrame with OHLCV + signal + atr columns.
        sl_mult: Stop loss ATR multiplier.
        tp_mult: Take profit ATR multiplier (0 = trailing stop only).
        risk_pct: Risk per trade as fraction of balance.
        leverage: Leverage multiplier.
        commission: Round-trip commission rate.
        slippage: Round-trip slippage rate.
        trail_mult: Trailing stop ATR multiplier (0 = disabled, use fixed TP).

    Returns:
        Dict with performance metrics.
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

            # Update trailing stop (ratchet in profit direction only)
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
# 4H resampling
# ---------------------------------------------------------------------------

def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV DataFrame to 4H bars.

    Args:
        df: 1H OHLCV DataFrame with DatetimeIndex.

    Returns:
        4H OHLCV DataFrame.
    """
    df_4h = df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["close"])
    return df_4h


# ---------------------------------------------------------------------------
# Coin discovery
# ---------------------------------------------------------------------------

def discover_coins(coins_arg: Optional[List[str]]) -> List[str]:
    """Return list of coin prefixes (lowercase), filtering deployed coins.

    Priority: CLI --coins arg > glob scan of *_1h_2y.csv files.

    Args:
        coins_arg: Optional explicit list of coin prefixes.

    Returns:
        List of coin prefixes to sweep.
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
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_coin_1h(prefix: str) -> Optional[pd.DataFrame]:
    """Load 1H OHLCV CSV for a coin prefix, return None on failure.

    Args:
        prefix: Coin prefix (e.g. 'ethusdt').

    Returns:
        DataFrame with indicators, or None if loading fails.
    """
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
    """Run full Volatility Expansion Breakout sweep across all parameter combos.

    Sweeps 1H data for all coins, then re-tests 4H for any coin that has at
    least one 1H winner.

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
    atr_thresholds = [1.3, 1.5, 1.8, 2.0]
    lookbacks = [1, 3]
    sl_values = [1.5, 2.0, 2.5]
    tp_values = [3.0, 4.0, 5.0]        # Fixed TP combos
    trail_values = [2.5, 3.0]           # Trailing stop combos (no fixed TP)

    # Total combos per coin per timeframe
    fixed_tp_count = len(atr_thresholds) * len(lookbacks) * len(sl_values) * len(tp_values)
    trail_count = len(atr_thresholds) * len(lookbacks) * len(sl_values) * len(trail_values)
    total_combos = fixed_tp_count + trail_count

    print(f"\nLoading 1H data for {len(coins)} coin(s)...")
    coin_data: Dict[str, pd.DataFrame] = {}
    raw_data_1h: Dict[str, pd.DataFrame] = {}
    for prefix in coins:
        df = _load_coin_1h(prefix)
        if df is not None:
            name = prefix.replace("usdt", "").upper()
            coin_data[name] = df
            raw_data_1h[name] = df
    print(f"Loaded {len(coin_data)} coins with 1H data.")

    prefix_map = {p.replace("usdt", "").upper(): p for p in coins}
    winners: List[dict] = []
    coins_with_1h_winners: set = set()

    print(f"\nSweeping {total_combos} combos per coin × {len(coin_data)} coins = "
          f"{total_combos * len(coin_data)} total 1H simulations")
    print(f"Variant: {variant.upper()}  |  "
          f"Filter: PF >= {min_pf}, trades >= {min_trades}, DD <= {min_dd}%\n")

    header = (
        f"{'Coin':<8} {'TF':<4} {'ATRt':>5} {'Lb':>3} {'SL':>5} {'TP/Tr':>6} {'Mode':<8} "
        f"{'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}"
    )
    print(header)
    print("-" * len(header))

    def _run_coin_sweep(
        name: str,
        df: pd.DataFrame,
        timeframe: str,
        prefix_str: str,
    ) -> List[dict]:
        """Inner helper: sweep all combos for a single coin+timeframe.

        Args:
            name: Display name (e.g. 'ETH').
            df: Indicator-enriched OHLCV DataFrame.
            timeframe: '1h' or '4h'.
            prefix_str: Raw prefix like 'ethusdt'.

        Returns:
            List of winner dicts found for this coin/timeframe.
        """
        coin_winners: List[dict] = []

        for atr_thresh, lb in product(atr_thresholds, lookbacks):
            # Build signals once per (threshold, lookback) combo
            try:
                df_sig = strategy_vol_expansion(
                    df,
                    atr_threshold=atr_thresh,
                    lookback=lb,
                    long_only=long_only,
                )
            except Exception as e:
                print(f"  ERROR {name}/{timeframe} thresh={atr_thresh} lb={lb}: {e}")
                continue

            # Fixed TP combos
            for sl_m, tp_m in product(sl_values, tp_values):
                try:
                    r = simulate(df_sig, sl_mult=sl_m, tp_mult=tp_m, trail_mult=0)
                except Exception as e:
                    print(f"  ERROR sim {name}/{timeframe} sl={sl_m} tp={tp_m}: {e}")
                    continue

                is_winner = (
                    r["pf"] >= min_pf
                    and r["trades"] >= min_trades
                    and r["dd"] <= min_dd
                )

                if r["trades"] >= 5:
                    marker = " *" if is_winner else ""
                    print(
                        f"{name:<8} {timeframe:<4} {atr_thresh:>5.1f} {lb:>3}"
                        f" {sl_m:>5.1f} {tp_m:>6.1f} {'fixed':<8}"
                        f" {r['trades']:>6} {r['tr_yr']:>5.0f}/yr"
                        f" {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%{marker}"
                    )

                if is_winner:
                    coin_winners.append({
                        "coin": name,
                        "prefix": prefix_str,
                        "strategy": f"vol_expansion_{variant}",
                        "timeframe": timeframe,
                        "pf": round(r["pf"], 3),
                        "trades": r["trades"],
                        "tr_yr": round(r["tr_yr"], 1),
                        "wr_pct": round(r["wr"], 1),
                        "dd_pct": round(r["dd"], 1),
                        "ret_pct": round(r["ret"], 1),
                        "params": {
                            "atr_threshold": atr_thresh,
                            "lookback": lb,
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
                    print(f"  ERROR sim {name}/{timeframe} sl={sl_m} trail={trail_m}: {e}")
                    continue

                is_winner = (
                    r["pf"] >= min_pf
                    and r["trades"] >= min_trades
                    and r["dd"] <= min_dd
                )

                if r["trades"] >= 5:
                    marker = " *" if is_winner else ""
                    print(
                        f"{name:<8} {timeframe:<4} {atr_thresh:>5.1f} {lb:>3}"
                        f" {sl_m:>5.1f} {trail_m:>6.1f} {'trail':<8}"
                        f" {r['trades']:>6} {r['tr_yr']:>5.0f}/yr"
                        f" {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%{marker}"
                    )

                if is_winner:
                    coin_winners.append({
                        "coin": name,
                        "prefix": prefix_str,
                        "strategy": f"vol_expansion_{variant}",
                        "timeframe": timeframe,
                        "pf": round(r["pf"], 3),
                        "trades": r["trades"],
                        "tr_yr": round(r["tr_yr"], 1),
                        "wr_pct": round(r["wr"], 1),
                        "dd_pct": round(r["dd"], 1),
                        "ret_pct": round(r["ret"], 1),
                        "params": {
                            "atr_threshold": atr_thresh,
                            "lookback": lb,
                            "sl_mult": sl_m,
                            "tp_mult": 0,
                            "trail_mult": trail_m,
                            "mode": "trailing",
                            "long_only": long_only,
                        },
                    })

        return coin_winners

    # --- 1H sweep ---
    for name in sorted(coin_data):
        df = coin_data[name]
        prefix_str = prefix_map.get(name, name.lower() + "usdt")
        coin_winners = _run_coin_sweep(name, df, "1h", prefix_str)
        if coin_winners:
            coins_with_1h_winners.add(name)
        winners.extend(coin_winners)

    # --- 4H sweep for coins that had 1H winners ---
    if coins_with_1h_winners:
        print(f"\n{'=' * 60}")
        print(f"4H SWEEP — {len(coins_with_1h_winners)} coin(s) with 1H winners: "
              f"{', '.join(sorted(coins_with_1h_winners))}")
        print("=" * 60)

        for name in sorted(coins_with_1h_winners):
            raw_1h = raw_data_1h.get(name)
            if raw_1h is None:
                continue
            try:
                # Resample the raw 1H data (without computed indicators) to 4H
                raw_cols = [c for c in ["open", "high", "low", "close", "volume"]
                            if c in raw_1h.columns]
                df_4h_raw = _resample_4h(raw_1h[raw_cols])
                df_4h = _build_base_indicators(df_4h_raw)
            except Exception as e:
                print(f"  WARN: could not build 4H data for {name}: {e}")
                continue

            prefix_str = prefix_map.get(name, name.lower() + "usdt")
            coin_winners_4h = _run_coin_sweep(name, df_4h, "4h", prefix_str)
            winners.extend(coin_winners_4h)

    return winners


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _save_winners(winners: List[dict]) -> None:
    """Write winners to OUTPUT_FILE (overwrites if exists).

    Args:
        winners: List of winner dicts.
    """
    with open(OUTPUT_FILE, "w") as f:
        json.dump(winners, f, indent=2)
    print(f"\nSaved {len(winners)} winner(s) to {OUTPUT_FILE}")


def _print_summary(winners: List[dict], min_pf: float, min_trades: int) -> None:
    """Print sorted summary of all winners.

    Args:
        winners: List of winner dicts.
        min_pf: Minimum profit factor threshold used.
        min_trades: Minimum trades threshold used.
    """
    print(f"\n{'=' * 90}")
    print(f"SWEEP COMPLETE — Volatility Expansion Breakout Winners "
          f"(PF >= {min_pf}, trades >= {min_trades})")
    print("=" * 90)

    if not winners:
        print("No winners found.")
        return

    hdr = (
        f"{'Coin':<8} {'TF':<4} {'Strategy':<22} {'ATRt':>5} {'Lb':>3} {'SL':>5} "
        f"{'TP/Tr':>6} {'Mode':<8} {'PF':>6} {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'DD%':>6}"
    )
    print(hdr)
    print("-" * len(hdr))

    for w in sorted(winners, key=lambda x: -x["pf"]):
        p = w["params"]
        exit_val = p["tp_mult"] if p["mode"] == "fixed_tp" else p["trail_mult"]
        print(
            f"{w['coin']:<8} {w['timeframe']:<4} {w['strategy']:<22} "
            f"{p['atr_threshold']:>5.1f} {p['lookback']:>3}"
            f" {p['sl_mult']:>5.1f} {exit_val:>6.1f} {p['mode']:<8}"
            f" {w['pf']:>6.2f} {w['trades']:>7}"
            f" {w['tr_yr']:>5.0f}/yr {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}%"
        )

    print(f"\nTotal winners: {len(winners)}")

    # Coin frequency breakdown
    coin_counts: Dict[str, int] = {}
    for w in winners:
        key = f"{w['coin']} ({w['timeframe']})"
        coin_counts[key] = coin_counts.get(key, 0) + 1
    print("\nWinners per coin/timeframe:")
    for key, cnt in sorted(coin_counts.items(), key=lambda x: -x[1]):
        print(f"  {key:<16} {cnt} combo(s)")

    # Top 10 by PF
    print("\nTop 10 by Profit Factor:")
    top10_hdr = (
        f"  {'Coin':<8} {'TF':<4} {'ATRt':>5} {'Lb':>3} {'SL':>5} {'TP/Tr':>6} {'Mode':<8}"
        f" {'PF':>6} {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'DD%':>6}"
    )
    print(top10_hdr)
    print("  " + "-" * (len(top10_hdr) - 2))
    for w in sorted(winners, key=lambda x: -x["pf"])[:10]:
        p = w["params"]
        exit_val = p["tp_mult"] if p["mode"] == "fixed_tp" else p["trail_mult"]
        print(
            f"  {w['coin']:<8} {w['timeframe']:<4} "
            f"{p['atr_threshold']:>5.1f} {p['lookback']:>3}"
            f" {p['sl_mult']:>5.1f} {exit_val:>6.1f} {p['mode']:<8}"
            f" {w['pf']:>6.2f} {w['trades']:>7}"
            f" {w['tr_yr']:>5.0f}/yr {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}%"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="sweep_vol_expansion: Volatility Expansion Breakout 1H/4H strategy sweep",
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
        default=25.0,
        help="Maximum drawdown %% for winners (default: 25.0)",
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
