"""
mass_sweep.py — Lightweight EMA 15m + Ichimoku 1H sweep across many coins.

Loads coin list from data/liquid_coins.json if present, otherwise scans data/
for *_15m_2y.csv files. Runs 5 strategy variants per coin and saves winners
to data/sweep_winners.json.

Usage:
    python research/mass_sweep.py
    python research/mass_sweep.py --coins aaveusdt,ftmusdt
    python research/mass_sweep.py --min-pf 1.3 --min-trades 20
"""
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
from bot.data import compute_atr, compute_ema, compute_rsi, compute_volume_ma, detect_regime

DATA_DIR = "/Users/iceai/Work/ccbt/data"


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

    # EMA crossover signals
    df["cross_up"] = (df["ema_f"] > df["ema_s"]) & (
        df["ema_f"].shift(1) <= df["ema_s"].shift(1)
    )
    df["cross_down"] = (df["ema_f"] < df["ema_s"]) & (
        df["ema_f"].shift(1) >= df["ema_s"].shift(1)
    )

    # EMA slope (% change over last bar, relative to price)
    df["ema_slope"] = (df["ema_f"] - df["ema_f"].shift(1)).abs() / df["close"].shift(1)

    # Ichimoku
    df["tenkan"] = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    df["kijun"] = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
    span_b = (
        (df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2
    ).shift(26)
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

    # Hour + weekday helpers (if index is datetime)
    if hasattr(df.index, "hour"):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek

    return df


# ---------------------------------------------------------------------------
# Simulator
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
    trades = []
    position = None

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
                pnl_pct = side * (exit_p - entry) / entry - (commission + slippage) * 2
                pnl = balance * risk_pct * leverage * pnl_pct / sl_mult
                # Cap loss at full risk stake
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
# Strategy definitions
# ---------------------------------------------------------------------------

def strategy_ema_cross(
    df: pd.DataFrame,
    df_1h: Optional[pd.DataFrame] = None,
    hours_start: int = 3,
    hours_end: int = 20,
    vol_mult: float = 1.3,
    slope_min: float = 0.0,
) -> pd.DataFrame:
    """EMA(9/21) crossover with RSI + volume + trading-hours + 1h trend filter.

    The 1h trend filter (EMA50) is critical — without it, PF drops significantly
    because signals fire in both directions during ranging markets.
    """
    df_s = df.copy()
    signals = pd.Series(0, index=df_s.index)
    in_hours = (df_s["hour"] >= hours_start) & (df_s["hour"] < hours_end)
    not_we = df_s["dow"] < 5
    vol_ok = df_s["vol_ratio"] >= vol_mult
    rsi_bull = (df_s["rsi"] >= 45) & (df_s["rsi"] <= 65)
    rsi_bear = (df_s["rsi"] >= 35) & (df_s["rsi"] <= 55)
    slope_ok = df_s["ema_slope"] >= slope_min if slope_min > 0 else pd.Series(True, index=df_s.index)

    # 1h trend filter: only long above EMA50 on 1h, short below
    if df_1h is not None and not df_1h.empty:
        ema50_1h = compute_ema(df_1h["close"], 50)
        trend_1h = pd.DataFrame({"ema50_1h": ema50_1h, "close_1h": df_1h["close"]})
        # Forward-fill 1h data to 15m index (no look-ahead — uses last closed 1h candle)
        trend_1h = trend_1h.reindex(df_s.index, method="ffill")
        trend_bull = trend_1h["close_1h"] > trend_1h["ema50_1h"]
        trend_bear = trend_1h["close_1h"] < trend_1h["ema50_1h"]
    else:
        # Without trend filter, use 15m EMA50 as fallback
        ema50 = compute_ema(df_s["close"], 50)
        trend_bull = df_s["close"] > ema50
        trend_bear = df_s["close"] < ema50

    signals[df_s["cross_up"] & in_hours & not_we & vol_ok & rsi_bull & slope_ok & trend_bull] = 1
    signals[df_s["cross_down"] & in_hours & not_we & vol_ok & rsi_bear & slope_ok & trend_bear] = -1
    # Blank warmup bars
    signals.iloc[:60] = 0

    df_s["signal"] = signals
    return df_s


def strategy_ichimoku(
    df: pd.DataFrame,
    hours_start: int = 3,
    hours_end: int = 20,
) -> pd.DataFrame:
    """Ichimoku Tenkan/Kijun cross above/below cloud (fixed SL/TP)."""
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    signals[df["ichi_bull"] & in_hours & not_we] = 1
    signals[df["ichi_bear"] & in_hours & not_we] = -1
    # Blank warmup bars (need 52 + 26 = 78 bars for cloud)
    signals.iloc[:80] = 0

    df_s = df.copy()
    df_s["signal"] = signals
    return df_s


# ---------------------------------------------------------------------------
# Coin discovery
# ---------------------------------------------------------------------------

def discover_coins(coins_arg: Optional[List[str]]) -> List[str]:
    """Return list of lowercase prefixes (e.g. ['btcusdt', 'dogeusdt']).

    Priority:
    1. --coins CLI argument
    2. data/liquid_coins.json
    3. Scan data/ for *_15m_2y.csv files
    """
    if coins_arg:
        return [c.lower().strip() for c in coins_arg if c.strip()]

    liquid_path = os.path.join(DATA_DIR, "liquid_coins.json")
    if os.path.exists(liquid_path):
        with open(liquid_path) as f:
            raw = json.load(f)
        if raw and isinstance(raw[0], dict):
            # Prefer explicit 'prefix' field (e.g. "btcusdt"), else derive from 'symbol'
            result = []
            for entry in raw:
                if "prefix" in entry:
                    result.append(entry["prefix"].lower())
                else:
                    sym = entry.get("symbol", "")
                    result.append(sym.lower().replace("/", "").replace(":", "").replace("usdt", "usdt", 1))
            return result
        return [s.lower().replace("/", "").replace(":", "") for s in raw]

    # Fallback: scan data/ directory
    pattern = os.path.join(DATA_DIR, "*_15m_2y.csv")
    files = glob.glob(pattern)
    prefixes = []
    for f in sorted(files):
        base = os.path.basename(f)
        prefix = base.replace("_15m_2y.csv", "")
        prefixes.append(prefix)
    return prefixes


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(
    coins: List[str],
    min_pf: float = 1.2,
    min_trades: int = 15,
) -> List[Dict]:
    """Run sweep across coins. Returns list of winner dicts."""

    # Build name -> prefix mapping for output
    name_to_prefix = {}
    for prefix in coins:
        name = prefix.replace("usdt", "").upper()
        # Handle prefixes like 1000pepeusdt -> 1000PEPE
        name_to_prefix[name] = prefix

    # Strategy specs: (name, timeframe, strategy_fn, strategy_kwargs, sim_kwargs)
    strategy_specs = [
        (
            "EMA(9/21)-std",
            "15m",
            strategy_ema_cross,
            {"slope_min": 0.0002},   # 0.02% fractional — standard coins
            {"sl_mult": 1.0, "tp_mult": 3.0},
        ),
        (
            "EMA(9/21)-meme",
            "15m",
            strategy_ema_cross,
            {"slope_min": 0.0001},   # 0.01% fractional — volatile/meme coins
            {"sl_mult": 1.0, "tp_mult": 3.0},
        ),
        (
            "Ichimoku",
            "1h",
            strategy_ichimoku,
            {},
            {"sl_mult": 2.0, "tp_mult": 5.0},
        ),
        (
            "Ichi+Trail",
            "1h",
            strategy_ichimoku,
            {},
            {"sl_mult": 2.5, "tp_mult": 0, "trail_mult": 3.0},
        ),
    ]

    all_results: List[Dict] = []
    winners: List[Dict] = []
    loaded_15m: Dict[str, pd.DataFrame] = {}
    loaded_1h: Dict[str, pd.DataFrame] = {}

    print(f"\nLoading data for {len(coins)} coin(s)...")
    for prefix in coins:
        name = prefix.replace("usdt", "").upper()
        # Try both 2y and 5y variants
        for suffix, store in [("15m", loaded_15m), ("1h", loaded_1h)]:
            loaded = False
            for period in ["2y", "5y"]:
                fpath = os.path.join(DATA_DIR, f"{prefix}_{suffix}_{period}.csv")
                if os.path.exists(fpath):
                    try:
                        store[name] = add_indicators(load_ohlcv(fpath))
                        loaded = True
                        break
                    except Exception as e:
                        print(f"  WARN: could not load {fpath}: {e}")
            if not loaded:
                pass  # silently skip missing timeframes

    loaded_names_15m = set(loaded_15m.keys())
    loaded_names_1h = set(loaded_1h.keys())
    all_names = loaded_names_15m | loaded_names_1h
    print(f"Loaded: {len(loaded_names_15m)} coins with 15m data, {len(loaded_names_1h)} with 1h data")

    print("\nRunning sweep...\n")

    header = f"{'Coin':<7} {'Strategy':<16} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}"
    print(header)
    print("-" * len(header))

    for name in sorted(all_names):
        for spec in strategy_specs:
            strat_name, tf, strat_fn, strat_kw, sim_kw = spec
            store = loaded_15m if tf == "15m" else loaded_1h
            if name not in store:
                continue
            df = store[name]
            try:
                # Pass 1h data for trend filter when running EMA on 15m
                kw = dict(strat_kw)
                if tf == "15m" and name in loaded_1h:
                    kw["df_1h"] = loaded_1h[name]
                df_sig = strat_fn(df, **kw)

                # Apply regime filter — skip ranging periods
                if "atr" in df_sig.columns:
                    regimes = df_sig.apply(
                        lambda row: detect_regime(
                            row.get("atr", 0), row.get("vol_ratio", 1),
                            df_sig["atr"].rolling(50).mean().loc[row.name] if row.name in df_sig.index else 0,
                            df_sig["vol_ratio"].rolling(50).mean().loc[row.name] if row.name in df_sig.index else 1,
                        ),
                        axis=1,
                    ) if len(df_sig) < 500 else None  # skip for perf on large sets
                    # For large datasets, use vectorized regime detection
                    if regimes is None:
                        atr_ma = df_sig["atr"].rolling(50).mean()
                        vol_ma50 = df_sig["vol_ratio"].rolling(50).mean()
                        ranging = (df_sig["atr"] < atr_ma * 0.8) & (df_sig["vol_ratio"] < vol_ma50 * 0.8)
                        df_sig.loc[ranging, "signal"] = 0

                r = simulate(df_sig, **sim_kw)
            except Exception as e:
                print(f"  ERROR {name} {strat_name}: {e}")
                continue

            row_data = {
                "coin": name,
                "prefix": name_to_prefix.get(name, name.lower() + "usdt"),
                "strategy": strat_name,
                "timeframe": tf,
                "pf": round(r["pf"], 3),
                "trades": r["trades"],
                "tr_yr": round(r["tr_yr"], 1),
                "wr_pct": round(r["wr"], 1),
                "dd_pct": round(r["dd"], 1),
                "ret_pct": round(r["ret"], 1),
                "params": {**strat_kw, **sim_kw},
            }
            all_results.append(row_data)

            is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades
            marker = " *" if is_winner else ""
            if r["trades"] >= 5:
                print(
                    f"{name:<7} {strat_name:<16} {r['trades']:>6} {r['tr_yr']:>5.0f}/yr"
                    f" {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%{marker}"
                )

            if is_winner:
                winners.append(row_data)

    # Print summary table sorted by PF
    print("\n" + "=" * 70)
    print(f"SWEEP RESULTS — Winners (PF >= {min_pf}, trades >= {min_trades})")
    print("=" * 70)
    if winners:
        hdr = f"{'Coin':<7} {'Strategy':<16} {'PF':>6} {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'DD%':>6}"
        print(hdr)
        print("-" * len(hdr))
        for w in sorted(winners, key=lambda x: -x["pf"]):
            print(
                f"{w['coin']:<7} {w['strategy']:<16} {w['pf']:>6.2f} {w['trades']:>7}"
                f" {w['tr_yr']:>5.0f}/yr {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}%"
            )
    else:
        print("  No winners found.")

    tested = len(all_results)
    print(f"\nTotal winners: {len(winners)} / {tested} tested")

    # Save winners
    out_path = os.path.join(DATA_DIR, "sweep_winners.json")
    with open(out_path, "w") as f:
        json.dump(winners, f, indent=2)
    print(f"Winners saved to {out_path}")

    return winners


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mass sweep: EMA 15m + Ichimoku 1H across many coins"
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help="Comma-separated list of coin prefixes, e.g. btcusdt,dogeusdt",
    )
    parser.add_argument(
        "--min-pf",
        type=float,
        default=1.2,
        help="Minimum profit factor to include in winners (default: 1.2)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=15,
        help="Minimum number of trades over backtest period (default: 15)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    coins_arg = args.coins.split(",") if args.coins else None
    coins = discover_coins(coins_arg)

    if not coins:
        print("No coins found. Add CSV files to data/ or pass --coins.")
        sys.exit(1)

    print(f"Sweeping {len(coins)} coin(s): {', '.join(coins[:10])}{'...' if len(coins) > 10 else ''}")
    run_sweep(coins, min_pf=args.min_pf, min_trades=args.min_trades)
