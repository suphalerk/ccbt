"""Verify Tier A + Tier B new coin candidates with the full BacktestEngine.

46 coins that passed the lightweight sweep (PF >= 2.0, trades >= 8) are now
run through the production BacktestEngine with the exact strategy configs
specified in the research brief.

Results are saved to data/verified_new_ab.json.

PASS = engine PF >= 1.2 AND trades >= 4
FAIL = engine PF < 1.2 OR trades < 4
"""

import copy
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_PATH = DATA_DIR / "verified_new_ab.json"

# ---------------------------------------------------------------------------
# Config template (loaded once)
# ---------------------------------------------------------------------------
with open(PROJECT_ROOT / "config_avax_ichi.json") as _f:
    _TEMPLATE: dict = json.load(_f)

# ---------------------------------------------------------------------------
# Pass/fail thresholds
# ---------------------------------------------------------------------------
MIN_PF = 1.2
MIN_TRADES = 4


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def build_config(
    coin: str,
    strategy: str,
    sl: float,
    tp: float,
    trail: Optional[float] = None,
    timeframe: str = "1h",
    is_4h: bool = False,
) -> dict:
    """Build a BacktestEngine config dict for a given coin + strategy.

    Args:
        coin: Uppercase coin ticker, e.g. "PIPPIN".
        strategy: Signal key — one of: "dual_supertrend", "alligator",
            "ema_ichimoku_hybrid", "ichi_supertrend", "volexp_supertrend",
            "ichimoku", "ema".
        sl: ATR stop-loss multiplier.
        tp: ATR take-profit multiplier.
        trail: ATR trailing-stop multiplier (defaults to sl).
        timeframe: Base timeframe string (informational only).
        is_4h: Whether to use 4H resampled data (handled at call site).

    Returns:
        Config dict ready to pass to BacktestEngine.
    """
    cfg = copy.deepcopy(_TEMPLATE)

    symbol = f"{coin}USDT"
    cfg["symbol"] = symbol
    cfg["risk_per_trade"] = 0.01
    cfg["leverage"] = 25
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail if trail is not None else sl
    cfg["atr_trail_mult_trending"] = trail if trail is not None else sl
    cfg["atr_min"] = 0.0
    cfg["volume_mult"] = 1.0
    cfg["volume_max_mult"] = None
    cfg["min_rr_ratio"] = 0
    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"] = {"enabled": False}

    # Disable all signals first
    cfg["signals"] = {
        "ema_crossover": {"enabled": False},
        "ema_fast_crossover": {"enabled": False},
        "ema_pullback": {"enabled": False},
        "rsi_divergence": {"enabled": False},
        "bb_breakout": {"enabled": False},
        "mean_reversion": {"enabled": False},
        "body_dominance": {"enabled": False},
        "squeeze_release": {"enabled": False},
        "ichimoku_cloud": {"enabled": False},
        "supertrend": {"enabled": False},
        "vol_expansion": {"enabled": False},
        "dual_supertrend": {"enabled": False},
        "alligator": {"enabled": False},
        "ema_ichimoku_hybrid": {"enabled": False},
        "ichi_supertrend": {"enabled": False},
        "volexp_supertrend": {"enabled": False},
    }

    # Enable the requested signal
    if strategy == "dual_supertrend":
        cfg["signals"]["dual_supertrend"] = {"enabled": True}

    elif strategy == "alligator":
        cfg["signals"]["alligator"] = {"enabled": True}

    elif strategy == "ema_ichimoku_hybrid":
        cfg["signals"]["ema_ichimoku_hybrid"] = {"enabled": True}
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52

    elif strategy == "ichi_supertrend":
        cfg["signals"]["ichi_supertrend"] = {"enabled": True}
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52

    elif strategy == "volexp_supertrend":
        cfg["signals"]["volexp_supertrend"] = {"enabled": True}

    elif strategy == "ichimoku":
        cfg["signals"]["ichimoku_cloud"] = {"enabled": True}
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52

    elif strategy == "ema":
        cfg["signals"]["ema_crossover"] = {"enabled": True}
        cfg["signals"]["ema_fast_crossover"] = {"enabled": True}

    else:
        raise ValueError(f"Unknown strategy: {strategy!r}")

    return cfg


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load_1h(prefix: str) -> Optional[pd.DataFrame]:
    """Load 1H OHLCV data for a coin prefix, or None if missing."""
    candidates = sorted(DATA_DIR.glob(f"{prefix}_1h*.csv"))
    if not candidates:
        return None
    return load_ohlcv(str(candidates[0]))


def resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H DataFrame to 4H OHLCV."""
    return (
        df.resample("4h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


# ---------------------------------------------------------------------------
# Single run helper
# ---------------------------------------------------------------------------

def run_engine(cfg: dict, signal_df: pd.DataFrame, trend_df: pd.DataFrame) -> Optional[dict]:
    """Run BacktestEngine; return metrics dict or None on error."""
    try:
        engine = BacktestEngine(cfg, initial_balance=10000.0)
        metrics = engine.run(signal_df, trend_df)
        return {
            "trades": metrics.total_trades,
            "pf": round(metrics.profit_factor, 2),
            "wr": round(metrics.win_rate * 100, 1),
            "dd": round(metrics.max_drawdown * 100, 1),
            "sharpe": round(metrics.sharpe_ratio, 2),
        }
    except Exception as exc:
        print(f"ERROR: {exc}")
        return None


# ---------------------------------------------------------------------------
# Coin list
# Columns: coin, strategy, sl, tp, sweep_pf (approx), tier, is_4h
# ---------------------------------------------------------------------------

CANDIDATES = [
    # ── Tier A ──
    ("PIPPIN",  "ichi_supertrend",   2.0, 5.0, 7.09, "A", True),
    ("LYN",     "dual_supertrend",   2.5, 4.0, 6.50, "A", False),
    ("ATOM",    "volexp_supertrend", 2.5, 4.0, 5.38, "A", False),
    ("VVV",     "ema_ichimoku_hybrid", 1.5, 4.0, 5.12, "A", True),
    ("LIGHT",   "alligator",         2.0, 4.0, 4.80, "A", False),
    ("XAN",     "ema",               2.0, 4.0, 4.60, "A", True),   # DoW+EMA → EMA fallback
    ("PENGU",   "volexp_supertrend", 2.5, 4.0, 4.30, "A", False),
    ("MYX",     "volexp_supertrend", 2.5, 4.0, 4.10, "A", False),
    ("ROBO",    "ichimoku",          2.0, 4.0, 3.90, "A", False),
    ("XRP",     "dual_supertrend",   2.5, 4.0, 3.80, "A", True),
    ("POWER",   "ichimoku",          2.0, 4.0, 3.20, "A", False),
    # ── Tier B ──
    ("IP",      "ichimoku",          2.0, 4.0, 2.78, "B", False),
    ("ANKR",    "dual_supertrend",   2.5, 4.0, 2.75, "B", True),
    ("PAXG",    "dual_supertrend",   2.5, 4.0, 2.70, "B", True),
    ("LINK",    "alligator",         2.0, 4.0, 2.65, "B", True),
    ("SUI",     "alligator",         2.0, 4.0, 2.62, "B", True),
    ("AKT",     "dual_supertrend",   2.5, 4.0, 2.60, "B", True),
    ("ETHFI",   "volexp_supertrend", 2.5, 4.0, 2.55, "B", False),
    ("TSLA",    "ema",               2.0, 4.0, 2.50, "B", False),
    ("AAVE",    "volexp_supertrend", 2.5, 4.0, 2.48, "B", False),
    ("FIL",     "dual_supertrend",   2.5, 4.0, 2.45, "B", True),
    ("ARIA",    "ema",               2.0, 4.0, 2.42, "B", False),
    ("AXS",     "ichimoku",          2.0, 4.0, 2.40, "B", True),
    ("ZEC",     "ema_ichimoku_hybrid", 1.5, 4.0, 2.38, "B", True),
    ("HYPE",    "ichimoku",          2.0, 4.0, 2.35, "B", True),
    ("TRIA",    "ema_ichimoku_hybrid", 1.5, 4.0, 2.33, "B", False),
    ("XNY",     "ichimoku",          2.0, 4.0, 2.30, "B", True),
    ("BTR",     "ichimoku",          2.0, 4.0, 2.28, "B", True),
    ("DOT",     "ichimoku",          2.0, 4.0, 2.25, "B", True),
    ("APR",     "ichimoku",          2.0, 4.0, 2.22, "B", True),
    ("PIXEL",   "alligator",         2.0, 4.0, 2.20, "B", True),
    ("ADA",     "ema_ichimoku_hybrid", 1.5, 4.0, 2.18, "B", True),
    ("ASTER",   "ichimoku",          2.0, 4.0, 2.15, "B", True),
    ("APT",     "ema_ichimoku_hybrid", 1.5, 4.0, 2.12, "B", True),
    ("XAI",     "ichi_supertrend",   2.0, 5.0, 2.10, "B", True),
    ("DASH",    "dual_supertrend",   2.5, 4.0, 2.08, "B", True),
    ("ALICE",   "ema",               2.0, 4.0, 2.06, "B", True),
    ("ZRO",     "dual_supertrend",   2.5, 4.0, 2.05, "B", True),
    ("SAND",    "volexp_supertrend", 2.5, 4.0, 2.04, "B", False),
    ("DOGE",    "dual_supertrend",   2.5, 4.0, 2.03, "B", True),
    ("QNT",     "alligator",         2.0, 4.0, 2.02, "B", True),
    ("NIGHT",   "ichimoku",          2.0, 4.0, 2.01, "B", False),
    ("IRYS",    "ichimoku",          2.0, 4.0, 2.00, "B", False),
    ("TIA",     "ichimoku",          2.0, 4.0, 2.00, "B", True),
    ("ENJ",     "dual_supertrend",   2.5, 4.0, 2.00, "B", True),
    ("H",       "ichimoku",          2.0, 4.0, 2.00, "B", True),
]

# Strategy label for display (maps internal key → human-readable)
STRATEGY_LABELS = {
    "dual_supertrend":    "Dual ST",
    "alligator":          "Alligator",
    "ema_ichimoku_hybrid":"EMA+Ichi",
    "ichi_supertrend":    "Ichi+ST",
    "volexp_supertrend":  "VolExp+ST",
    "ichimoku":           "Ichimoku",
    "ema":                "EMA",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("TIER A+B ENGINE VERIFICATION (46 coins)")
    header = (
        f"{'Coin':<12} {'Strategy':<20} {'TF':<4} {'Sweep_PF':>8} "
        f"{'Engine_PF':>9} {'WR%':>5} {'Trades':>6} {'Sharpe':>6} "
        f"{'DD%':>5}  {'Status'}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)

    results: list[dict] = []
    passed: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []

    for coin, strategy, sl, tp, sweep_pf, tier, is_4h in CANDIDATES:
        prefix = (coin + "usdt").lower()
        tf_label = "4H" if is_4h else "1H"
        strat_label = STRATEGY_LABELS.get(strategy, strategy)
        if strategy == "ema":
            tf_label = "1H"  # EMA fallback always on 1H

        # ── Load data ──
        raw_1h = load_1h(prefix)
        if raw_1h is None:
            tag = "SKIP-NO-DATA"
            print(
                f"{coin:<12} {strat_label:<20} {tf_label:<4} {sweep_pf:>8.2f} "
                f"{'—':>9} {'—':>5} {'—':>6} {'—':>6} {'—':>5}  {tag}"
            )
            skipped.append(coin)
            continue

        if is_4h and strategy != "ema":
            signal_df = resample_4h(raw_1h)
            trend_df = signal_df
        else:
            signal_df = raw_1h
            trend_df = raw_1h

        # ── Build config ──
        try:
            cfg = build_config(coin, strategy, sl, tp, is_4h=is_4h)
        except ValueError as exc:
            print(f"{coin:<12} SKIP — config error: {exc}")
            skipped.append(coin)
            continue

        # ── Run engine ──
        m = run_engine(cfg, signal_df, trend_df)

        if m is None:
            tag = "SKIP-ERROR"
            print(
                f"{coin:<12} {strat_label:<20} {tf_label:<4} {sweep_pf:>8.2f} "
                f"{'ERR':>9} {'—':>5} {'—':>6} {'—':>6} {'—':>5}  {tag}"
            )
            skipped.append(coin)
            continue

        eng_pf = m["pf"]
        eng_tr = m["trades"]
        eng_wr = m["wr"]
        eng_sh = m["sharpe"]
        eng_dd = m["dd"]

        passed_check = eng_pf >= MIN_PF and eng_tr >= MIN_TRADES
        status = "PASS" if passed_check else "FAIL"

        print(
            f"{coin:<12} {strat_label:<20} {tf_label:<4} {sweep_pf:>8.2f} "
            f"{eng_pf:>9.2f} {eng_wr:>5.1f} {eng_tr:>6} {eng_sh:>6.2f} "
            f"{eng_dd:>5.1f}%  {status}"
        )

        entry = {
            "coin": coin,
            "tier": tier,
            "prefix": prefix,
            "symbol": f"{coin}USDT",
            "strategy": strategy,
            "strategy_label": strat_label,
            "timeframe": tf_label,
            "sl": sl,
            "tp": tp,
            "sweep_pf": sweep_pf,
            "engine_pf": eng_pf,
            "win_rate": eng_wr,
            "trades": eng_tr,
            "sharpe": eng_sh,
            "max_drawdown": eng_dd,
            "status": status,
        }
        results.append(entry)

        if passed_check:
            passed.append(coin)
        else:
            failed.append(coin)

    # ── Summary ──
    print(sep)
    print(
        f"\nSummary: {len(passed)} PASS | {len(failed)} FAIL | {len(skipped)} SKIP"
        f" (of {len(CANDIDATES)} total)"
    )
    if passed:
        print(f"Passed:  {', '.join(passed)}")
    if failed:
        print(f"Failed:  {', '.join(failed)}")
    if skipped:
        print(f"Skipped: {', '.join(skipped)}")

    # ── Save results ──
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
