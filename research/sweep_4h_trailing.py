"""
sweep_4h_trailing.py — 4H Ichimoku sweep with ATR trailing exit (no fixed TP).

Same entry logic as sweep_4h_ichimoku.py (Tenkan/Kijun cross above/below cloud on 4H
resampled data), but replaces fixed TP with a pure ATR trailing stop that ratchets
only in the profit direction.

Parameter grid:
    SL:   [1.5, 2.0, 2.5] × ATR  (fixed at entry)
    Trail: [2.0, 3.0, 4.0] × ATR  (distance from price high/low)

tp_mult is forced to 0 — trailing stop is the only exit besides SL.

Acceptance:
    PF >= 1.3, trades >= 10, DD <= 30%

Saves winners to data/sweep_4h_trail_winners.json.
Prints comparison table for coins present in original fixed-TP sweep.

Usage:
    python research/sweep_4h_trailing.py
    python research/sweep_4h_trailing.py --coins bnbusdt,renderusdt
    python research/sweep_4h_trailing.py --min-pf 1.2 --min-trades 8
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
# Deployed coins — skip these (already live)
# ---------------------------------------------------------------------------

DEPLOYED_COINS = {"BTC", "DOGE", "ARB", "WIF", "AVAX", "NEAR", "SOL"}

# ---------------------------------------------------------------------------
# Parameter grid
# ---------------------------------------------------------------------------

SL_MULTS = [1.5, 2.0, 2.5]
TRAIL_MULTS = [2.0, 3.0, 4.0]

# ---------------------------------------------------------------------------
# All coins with 1h_2y.csv data (excluding deployed)
# ---------------------------------------------------------------------------

ALL_AVAILABLE_COINS = [
    "1000bonkusdt", "1000pepeusdt", "1000shibusdt",
    "aaveusdt", "adausdt", "aiausdt", "aktusdt", "algousdt", "aliceusdt",
    "animeusdt", "ankrusdt", "aprusdt", "aptusdt", "arbusdt", "arcusdt",
    "ariausdt", "asterusdt", "athusdt", "atomusdt", "avaxusdt", "axsusdt",
    "banusdt", "bardusdt", "bchusdt", "beatusdt", "berausdt", "bnbusdt",
    "btcusdt", "btrusdt", "cfgusdt", "cfxusdt", "coinusdt", "collectusdt",
    "cosusdt", "crclusdt", "crvusdt", "dashusdt", "degousdt", "dogeusdt",
    "dotusdt", "edgeusdt", "enausdt", "enjusdt", "ensousdt", "etcusdt",
    "ethfiusdt", "fartcoinusdt", "fetusdt", "filusdt", "galausdt", "guausdt",
    "gunusdt", "hbarusdt", "hoodusdt", "humausdt", "hypeusdt", "icpusdt",
    "injusdt", "intcusdt", "ipusdt", "irysusdt", "jctusdt", "kasusdt",
    "katusdt", "kiteusdt", "lightusdt", "linkusdt", "litusdt", "ltcusdt",
    "lynusdt", "mstrusdt", "myxusdt", "naorisusdt", "nearusdt", "nightusdt",
    "ondousdt", "opnusdt", "opusdt", "paxgusdt", "penguusdt", "phausdt",
    "pippinusdt", "pixelusdt", "playusdt", "polusdt", "polyxusdt", "powerusdt",
    "pumpusdt", "qntusdt", "renderusdt", "riverusdt", "robousdt", "saharausdt",
    "sandusdt", "signusdt", "sirenusdt", "stousdt", "suiusdt", "taousdt",
    "tiausdt", "tonusdt", "tradoorusdt", "triausdt", "trumpusdt", "trxusdt",
    "tslausdt", "uniusdt", "virtualusdt", "vvvusdt", "waxpusdt", "wifusdt",
    "wldusdt", "wlfiusdt", "xagusdt", "xaiusdt", "xrpusdt", "xlmusdt",
    "xmrusdt", "xptusdt", "zecusdt", "zenusdt", "zetausdt", "zrousdt",
]

# Coins present in original 4H fixed-TP sweep (for comparison table)
ORIGINAL_SWEEP_COINS = {
    "ADA", "ALGO", "APT", "ATOM", "BCH", "BNB", "DOT", "ENA", "ETC",
    "FET", "FIL", "HBAR", "ICP", "LINK", "LTC", "OP", "PAXG", "QNT",
    "RENDER", "SAND", "SUI", "TAO", "TIA", "TON", "UNI", "WLD",
}

# Hours filter (inclusive start, exclusive end)
HOURS_START = 3
HOURS_END = 20

# Ichimoku standard periods
TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52

# Warmup: 52 bars for Span B + 26 bar cloud shift = 78 bars minimum
WARMUP_BARS = 80


# ---------------------------------------------------------------------------
# Resample 1H → 4H
# ---------------------------------------------------------------------------

def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV data to 4H candles.

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
# Indicators
# ---------------------------------------------------------------------------

def _compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
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

    # Span A raw: average of Tenkan and Kijun
    span_a_raw = (df["tenkan"] + df["kijun"]) / 2

    # Span B raw: 52-period midpoint
    span_b_raw = (
        df["high"].rolling(SENKOU_B_PERIOD).max()
        + df["low"].rolling(SENKOU_B_PERIOD).min()
    ) / 2

    # Cloud at bar T = span values from T-26 (projected forward 26 in Ichimoku convention)
    span_a_current = span_a_raw.shift(KIJUN_PERIOD)
    span_b_current = span_b_raw.shift(KIJUN_PERIOD)

    df["cloud_top"] = pd.concat([span_a_current, span_b_current], axis=1).max(axis=1)
    df["cloud_bottom"] = pd.concat([span_a_current, span_b_current], axis=1).min(axis=1)

    # Cross detection
    df["cross_up"] = (df["tenkan"] > df["kijun"]) & (
        df["tenkan"].shift(1) <= df["kijun"].shift(1)
    )
    df["cross_down"] = (df["tenkan"] < df["kijun"]) & (
        df["tenkan"].shift(1) >= df["kijun"].shift(1)
    )

    # ATR(14)
    df["atr"] = _compute_atr(df["high"], df["low"], df["close"], 14)

    # Time helpers
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

    Long entry: Tenkan crosses above Kijun + close above cloud_top + in session.
    Short entry (standard only): Tenkan crosses below Kijun + close below cloud_bottom.

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

    above_cloud = df["close"] > df["cloud_top"]
    long_cond = df["cross_up"] & above_cloud & in_hours & not_we
    signals[long_cond] = 1

    if not long_only:
        below_cloud = df["close"] < df["cloud_bottom"]
        short_cond = df["cross_down"] & below_cloud & in_hours & not_we
        signals[short_cond] = -1

    # Blank warmup bars
    signals.iloc[:WARMUP_BARS] = 0

    df["signal"] = signals
    return df


# ---------------------------------------------------------------------------
# Simulator with pure trailing exit
# ---------------------------------------------------------------------------

def simulate_trail(
    df: pd.DataFrame,
    sl_mult: float = 2.0,
    trail_mult: float = 3.0,
    risk_pct: float = 0.10,
    leverage: float = 10,
    commission: float = 0.00055,
    slippage: float = 0.0002,
) -> dict:
    """Event-driven trade simulator with ATR trailing stop (no fixed TP).

    Trail ratchets only in the profit direction:
    - Long:  trail_sl = max(bar_high - trail_dist, current_sl)  (never moves down)
    - Short: trail_sl = min(bar_low  + trail_dist, current_sl)  (never moves up)

    Trail is active immediately from entry (not after reaching a partial TP threshold).
    This keeps the simulator consistent with the mass_sweep.py interface where
    trail_mult > 0 and tp_mult = 0 produces pure trailing behaviour.

    Args:
        df: DataFrame with 'signal' and 'atr' columns.
        sl_mult: Fixed SL distance multiplier in ATR units.
        trail_mult: Trailing stop distance multiplier in ATR units.
        risk_pct: Fraction of balance risked per trade (for PnL scaling).
        leverage: Effective leverage used for PnL scaling.
        commission: Per-side commission rate.
        slippage: Per-side slippage rate.

    Returns:
        Dict: trades, tr_yr, wr, pf, dd, balance, ret.
    """
    balance = 1000.0
    peak = 1000.0
    max_dd = 0.0
    trades: List[Dict] = []
    position: Optional[Dict] = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        sig_row = df.iloc[i - 1]

        # --- Manage open position ---
        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]

            # Ratchet trailing stop (only moves in profit direction)
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

            if hit_sl:
                exit_p = sl
                pnl_pct = (
                    side * (exit_p - entry) / entry - (commission + slippage) * 2
                )
                pnl = balance * risk_pct * leverage * pnl_pct / sl_mult
                pnl = max(pnl, -balance * risk_pct * leverage)
                balance += pnl
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                trades.append({"pnl": pnl, "reason": "trail_sl"})
                position = None
                if balance <= 0:
                    break

        # --- Open new position ---
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

            if side == 1:
                sl_p = entry_p - sl_d
            else:
                sl_p = entry_p + sl_d

            position = {
                "side": side,
                "entry": entry_p,
                "sl": sl_p,
            }

    # --- Flush open position at last bar ---
    if position is not None:
        row = df.iloc[-1]
        sig_row = df.iloc[-2]
        side = position["side"]
        entry = position["entry"]
        exit_p = row["close"]
        pnl_pct = (
            side * (exit_p - entry) / entry - (commission + slippage) * 2
        )
        pnl = balance * risk_pct * leverage * pnl_pct / sl_mult
        pnl = max(pnl, -balance * risk_pct * leverage)
        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        trades.append({"pnl": pnl, "reason": "eod"})

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
    min_pf: float = 1.3,
    min_trades: int = 10,
    max_dd: float = 30.0,
) -> Tuple[List[Dict], Dict[str, float]]:
    """Run 4H Ichimoku trailing-exit sweep across coins.

    For each coin:
      - Load 1H data, resample to 4H
      - Compute Ichimoku indicators
      - Test all (sl_mult, trail_mult) combos × (standard, long-only)
      - Record winners: PF >= min_pf AND trades >= min_trades AND DD <= max_dd

    Args:
        coins: List of coin prefixes, e.g. ['bnbusdt', 'renderusdt'].
        min_pf: Minimum profit factor.
        min_trades: Minimum trade count.
        max_dd: Maximum drawdown percentage.

    Returns:
        Tuple of (winners_list, best_pf_per_coin_dict).
    """
    winners: List[Dict] = []
    best_trail_pf: Dict[str, float] = {}  # best trailing PF per coin (any variant)

    print(f"\nLoading 1H data for {len(coins)} coin(s) and resampling to 4H...")

    loaded: Dict[str, pd.DataFrame] = {}
    skipped_deployed: List[str] = []

    for prefix in coins:
        name = prefix.replace("usdt", "").upper().replace("USD", "")
        if name in DEPLOYED_COINS:
            skipped_deployed.append(name)
            continue
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

    if skipped_deployed:
        print(f"Skipped deployed: {', '.join(sorted(skipped_deployed))}")
    print(f"Loaded: {len(loaded)} coins with 4H data\n")

    if not loaded:
        print("No data loaded. Exiting.")
        return [], {}

    # Build sweep combos: (label, long_only, sl_mult, trail_mult)
    combos: List[Tuple[str, bool, float, float]] = []
    for sl in SL_MULTS:
        for tr in TRAIL_MULTS:
            combos.append((f"SL{sl:.1f}/TR{tr:.1f}", False, sl, tr))
            combos.append((f"SL{sl:.1f}/TR{tr:.1f}-LO", True, sl, tr))

    # Header
    header = (
        f"{'Coin':<8} {'Variant':<24} {'Trades':>6} {'Tr/yr':>6} "
        f"{'WR%':>6} {'PF':>6} {'DD%':>6}"
    )
    print(header)
    print("-" * len(header))

    for name in sorted(loaded.keys()):
        df = loaded[name]

        for label, long_only, sl_mult, trail_mult in combos:
            variant_name = f"{'LO' if long_only else 'Std'}-{label}"
            try:
                df_sig = generate_signals(df, long_only=long_only)
                r = simulate_trail(df_sig, sl_mult=sl_mult, trail_mult=trail_mult)
            except Exception as exc:
                print(f"  ERROR {name} {variant_name}: {exc}")
                continue

            is_winner = (
                r["pf"] >= min_pf
                and r["trades"] >= min_trades
                and r["dd"] <= max_dd
            )
            marker = " *" if is_winner else ""

            if r["trades"] >= 4:
                print(
                    f"{name:<8} {variant_name:<24} {r['trades']:>6} {r['tr_yr']:>5.0f}/yr"
                    f" {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%{marker}"
                )

            # Track best trailing PF per coin (used in comparison table)
            if r["pf"] > best_trail_pf.get(name, 0.0):
                best_trail_pf[name] = r["pf"]

            if is_winner:
                row_data = {
                    "coin": name,
                    "prefix": name.lower() + "usdt",
                    "strategy": "Ichimoku-4H-Trail",
                    "timeframe": "4h",
                    "variant": variant_name,
                    "long_only": long_only,
                    "sl_mult": sl_mult,
                    "trail_mult": trail_mult,
                    "tp_mult": 0,
                    "pf": r["pf"],
                    "trades": r["trades"],
                    "tr_yr": r["tr_yr"],
                    "wr_pct": r["wr"],
                    "dd_pct": r["dd"],
                    "ret_pct": r["ret"],
                }
                winners.append(row_data)

    # ---------------------------------------------------------------------------
    # Summary — winners
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print(
        f"TRAILING EXIT WINNERS  (PF >= {min_pf}, trades >= {min_trades}, DD <= {max_dd}%)"
    )
    print("=" * 80)
    if winners:
        hdr = (
            f"{'Coin':<8} {'Variant':<26} {'PF':>6} {'Trades':>7} "
            f"{'Tr/yr':>6} {'WR%':>6} {'DD%':>6}"
        )
        print(hdr)
        print("-" * len(hdr))
        for w in sorted(winners, key=lambda x: -x["pf"]):
            print(
                f"{w['coin']:<8} {w['variant']:<26} {w['pf']:>6.2f} {w['trades']:>7}"
                f" {w['tr_yr']:>5.0f}/yr {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}%"
            )
    else:
        print("  No winners found.")

    # Best config per coin
    print("\n" + "=" * 80)
    print("BEST CONFIG PER COIN (highest PF, meets all criteria)")
    print("=" * 80)
    if winners:
        best_per_coin: Dict[str, Dict] = {}
        for w in winners:
            coin = w["coin"]
            if coin not in best_per_coin or w["pf"] > best_per_coin[coin]["pf"]:
                best_per_coin[coin] = w
        bh = (
            f"{'Coin':<8} {'Variant':<26} {'PF':>6} {'Trades':>7} "
            f"{'WR%':>6} {'DD%':>6} {'Ret%':>7}"
        )
        print(bh)
        print("-" * len(bh))
        for coin, w in sorted(best_per_coin.items(), key=lambda x: -x[1]["pf"]):
            print(
                f"{coin:<8} {w['variant']:<26} {w['pf']:>6.2f} {w['trades']:>7}"
                f" {w['wr_pct']:>5.1f}% {w['dd_pct']:>5.1f}% {w['ret_pct']:>6.1f}%"
            )

    print(f"\nTotal winners: {len(winners)} / {len(combos) * len(loaded)} tested")

    # ---------------------------------------------------------------------------
    # Comparison: fixed TP vs trailing for original sweep coins
    # ---------------------------------------------------------------------------
    _print_comparison_table(best_trail_pf)

    # ---------------------------------------------------------------------------
    # Save winners
    # ---------------------------------------------------------------------------
    out_path = os.path.join(DATA_DIR, "sweep_4h_trail_winners.json")
    with open(out_path, "w") as f:
        json.dump(winners, f, indent=2)
    print(f"\nWinners saved to {out_path}")

    return winners, best_trail_pf


def _print_comparison_table(best_trail_pf: Dict[str, float]) -> None:
    """Print side-by-side comparison of fixed-TP vs trailing for original sweep coins.

    Loads sweep_4h_winners.json to get the best fixed-TP PF per coin, then compares
    against the trailing PF obtained in this sweep.

    Args:
        best_trail_pf: Map of coin name → best trailing PF from this sweep.
    """
    fixed_path = os.path.join(DATA_DIR, "sweep_4h_winners.json")
    if not os.path.exists(fixed_path):
        print("\n(sweep_4h_winners.json not found — skipping comparison table)")
        return

    try:
        with open(fixed_path) as f:
            fixed_winners = json.load(f)
    except Exception as exc:
        print(f"\n(Could not load sweep_4h_winners.json: {exc})")
        return

    # Best fixed PF per coin
    best_fixed_pf: Dict[str, float] = {}
    for w in fixed_winners:
        coin = w["coin"]
        if w["pf"] > best_fixed_pf.get(coin, 0.0):
            best_fixed_pf[coin] = w["pf"]

    # Union of coins present in either sweep
    all_coins = sorted(set(best_fixed_pf.keys()) | set(best_trail_pf.keys()))
    if not all_coins:
        return

    print("\n" + "=" * 60)
    print("COMPARISON: Fixed TP vs Trailing Exit (best PF per coin)")
    print("=" * 60)
    hdr = f"{'Coin':<10} {'Fixed_PF':>9} {'Trail_PF':>9} {'Improvement':>12}"
    print(hdr)
    print("-" * len(hdr))

    for coin in all_coins:
        fixed = best_fixed_pf.get(coin)
        trail = best_trail_pf.get(coin)

        if fixed is None:
            fixed_s = "    N/A"
            imp_s = "    N/A"
        else:
            fixed_s = f"  {fixed:6.2f}"

        if trail is None:
            trail_s = "    N/A"
            imp_s = "    N/A"
        else:
            trail_s = f"  {trail:6.2f}"

        if fixed is not None and trail is not None:
            imp = trail - fixed
            arrow = "+" if imp >= 0 else ""
            imp_s = f"     {arrow}{imp:+.2f}"
        else:
            imp_s = "    N/A"

        print(f"{coin:<10} {fixed_s}  {trail_s}  {imp_s}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "4H Ichimoku sweep with ATR trailing exit — "
            "resamples 1H data, tests SL/trail combos"
        )
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help=(
            "Comma-separated coin prefixes, e.g. bnbusdt,renderusdt "
            "(default: all available 1h_2y coins)"
        ),
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
        help="Minimum trade count for winners (default: 10)",
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
        coins = [c.strip().lower() for c in args.coins.split(",") if c.strip()]
    else:
        coins = ALL_AVAILABLE_COINS

    print(
        f"4H Ichimoku trailing sweep — {len(coins)} coins, "
        f"SL {SL_MULTS} × Trail {TRAIL_MULTS}, "
        f"hours {HOURS_START}-{HOURS_END} UTC"
    )
    print(
        f"Acceptance: PF >= {args.min_pf}, trades >= {args.min_trades}, "
        f"DD <= {args.max_dd}%"
    )
    print(f"Deployed coins skipped: {sorted(DEPLOYED_COINS)}")

    run_sweep(
        coins,
        min_pf=args.min_pf,
        min_trades=args.min_trades,
        max_dd=args.max_dd,
    )
