"""verify_deployed_new_strats.py — Test 29 deployed coins against 5 new strategy types.

145 backtests (29 coins × 5 strategies). For each coin, compare new strategy PF
against the current deployed PF to find upgrade candidates.

New strategies:
  1. Dual Supertrend 4H  — SL 2.5, TP 4.0
  2. VolExp+Supertrend 1H — SL 2.5, TP 4.0
  3. Alligator 4H        — SL 2.0, TP 4.0
  4. EMA+Ichimoku 4H     — SL 1.5, TP 4.0
  5. Ichi+Supertrend 4H  — SL 2.0, TP 5.0
"""

import copy
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_PATH = DATA_DIR / "verified_deployed_new_strats.json"

# ---------------------------------------------------------------------------
# Config template
# ---------------------------------------------------------------------------
with open(PROJECT_ROOT / "config_avax_ichi.json") as _f:
    TEMPLATE: dict = json.load(_f)

# ---------------------------------------------------------------------------
# Deployed coins and their current PFs
# ---------------------------------------------------------------------------
DEPLOYED_PFS: dict[str, float] = {
    "BTC":      1.41,
    "WIF":      1.71,
    "ARC":      1.59,   # ARC_EMA current
    "AVAX":     2.49,
    "NEAR":     1.82,
    "POL":      6.79,
    "GUN":      4.00,
    "BERA":     2.92,
    "ATH":      2.51,
    "INJ":      2.28,
    "TRUMP":    1.66,
    "ANIME":    1.51,
    "ZETA":     1.41,
    "1000SHIB": 18.43,
    "TAO":      6.13,
    "RENDER":   4.71,
    "HBAR":     3.26,
    "ARB":      4.38,
    "ALGO":     7.21,
    "TRX":      5.53,
    "POLYX":    5.13,
    "FET":      3.03,
    "XLM":      2.88,
    "SAHARA":   2.44,
    "MSTR":     2.66,
    "XAG":      2.09,
    "1000PEPE": 7.23,
    "WLD":      3.45,
}

# Coin → data file prefix mapping
COIN_PREFIXES: dict[str, str] = {
    "BTC":      "btcusdt",
    "WIF":      "wifusdt",
    "ARC":      "arcusdt",
    "AVAX":     "avaxusdt",
    "NEAR":     "nearusdt",
    "POL":      "polusdt",
    "GUN":      "gunusdt",
    "BERA":     "berausdt",
    "ATH":      "athusdt",
    "INJ":      "injusdt",
    "TRUMP":    "trumpusdt",
    "ANIME":    "animeusdt",
    "ZETA":     "zetausdt",
    "1000SHIB": "1000shibusdt",
    "TAO":      "taousdt",
    "RENDER":   "renderusdt",
    "HBAR":     "hbarusdt",
    "ARB":      "arbusdt",
    "ALGO":     "algousdt",
    "TRX":      "trxusdt",
    "POLYX":    "polyxusdt",
    "FET":      "fetusdt",
    "XLM":      "xlmusdt",
    "SAHARA":   "saharausdt",
    "MSTR":     "mstrusdt",
    "XAG":      "xagusdt",
    "1000PEPE": "1000pepeusdt",
    "WLD":      "wldusdt",
}

# Strategy definitions: (key, label, is_4h, sl_mult, tp_mult)
NEW_STRATS: list[tuple[str, str, bool, float, float]] = [
    ("dual_supertrend",    "Dual ST 4H",    True,  2.5, 4.0),
    ("volexp_supertrend",  "VolExp+ST 1H",  False, 2.5, 4.0),
    ("alligator",          "Alligator 4H",  True,  2.0, 4.0),
    ("ema_ichimoku_hybrid","EMA+Ichi 4H",   True,  1.5, 4.0),
    ("ichi_supertrend",    "Ichi+ST 4H",    True,  2.0, 5.0),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV DataFrame to 4H candles."""
    df_4h = (
        df_1h.resample("4h").agg(
            {
                "open":   "first",
                "high":   "max",
                "low":    "min",
                "close":  "last",
                "volume": "sum",
            }
        )
        .dropna()
    )
    return df_4h


def find_data_file(prefix: str, timeframe: str) -> Optional[Path]:
    """Return the first matching data file for (prefix, timeframe), or None."""
    candidates = sorted(DATA_DIR.glob(f"{prefix}_{timeframe}*.csv"))
    return candidates[0] if candidates else None


def build_config(coin: str, strat_key: str, sl_mult: float, tp_mult: float) -> dict:
    """Build a backtest config for coin × strategy.

    Starts from the AVAX Ichimoku template and overrides signal, SL/TP, and
    any strategy-specific indicator parameters.
    """
    cfg = copy.deepcopy(TEMPLATE)
    prefix = COIN_PREFIXES[coin]
    symbol = f"{prefix.upper()}".replace("USDT", "") + "USDT"
    # Normalise: 1000SHIBUSDT stays as-is
    cfg["symbol"] = prefix.upper().rstrip("T") + "USDT" if not prefix.startswith("1000") else prefix.upper()
    # Simpler: just keep coin label + USDT
    cfg["symbol"] = coin + "USDT"

    cfg["risk_per_trade"] = 0.01
    cfg["leverage"] = 25
    cfg["atr_sl_mult"] = sl_mult
    cfg["atr_tp_mult"] = tp_mult
    cfg["atr_trail_mult"] = 3.0
    cfg["atr_trail_mult_trending"] = 3.0
    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"] = {"enabled": False}
    cfg["adaptive_sizing"] = {"enabled": False}
    cfg["pyramiding"] = {"enabled": False}
    cfg["mtd_accelerator"] = {"enabled": False}

    # All signals off by default
    all_signals_off = {
        "ema_crossover":       {"enabled": False},
        "ema_fast_crossover":  {"enabled": False},
        "ema_pullback":        {"enabled": False},
        "rsi_divergence":      {"enabled": False},
        "bb_breakout":         {"enabled": False},
        "mean_reversion":      {"enabled": False},
        "body_dominance":      {"enabled": False},
        "squeeze_release":     {"enabled": False},
        "ichimoku_cloud":      {"enabled": False},
        "supertrend":          {"enabled": False},
        "vol_expansion":       {"enabled": False},
        "dual_supertrend":     {"enabled": False},
        "alligator":           {"enabled": False},
        "ema_ichimoku_hybrid": {"enabled": False},
        "ichi_supertrend":     {"enabled": False},
        "volexp_supertrend":   {"enabled": False},
    }
    cfg["signals"] = all_signals_off

    # Enable the target signal
    cfg["signals"][strat_key] = {"enabled": True}

    # Strategy-specific indicator params
    if strat_key == "dual_supertrend":
        cfg.setdefault("dual_supertrend_fast_period", 7)
        cfg.setdefault("dual_supertrend_fast_mult", 1.5)
        cfg.setdefault("dual_supertrend_slow_period", 14)
        cfg.setdefault("dual_supertrend_slow_mult", 3.0)

    elif strat_key == "volexp_supertrend":
        cfg.setdefault("vol_expansion_threshold", 1.8)
        cfg.setdefault("vol_expansion_lookback", 1)
        cfg.setdefault("vol_expansion_atr_ma_period", 20)
        cfg.setdefault("supertrend_multiplier", 2.0)

    elif strat_key == "alligator":
        cfg.setdefault("alligator_jaw_period", 13)
        cfg.setdefault("alligator_jaw_offset", 8)
        cfg.setdefault("alligator_teeth_period", 8)
        cfg.setdefault("alligator_teeth_offset", 5)
        cfg.setdefault("alligator_lips_period", 5)
        cfg.setdefault("alligator_lips_offset", 3)

    elif strat_key == "ema_ichimoku_hybrid":
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52

    elif strat_key == "ichi_supertrend":
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52
        cfg.setdefault("supertrend_multiplier", 2.0)

    return cfg


def run_backtest(
    cfg: dict,
    df_signal: pd.DataFrame,
    df_trend: pd.DataFrame,
) -> Optional[dict]:
    """Run BacktestEngine and return metric dict, or None on error."""
    try:
        engine = BacktestEngine(cfg)
        metrics = engine.run(df_signal, df_trend)
        return {
            "pf":     round(float(metrics.profit_factor), 2),
            "wr":     round(float(metrics.win_rate) * 100, 1),
            "trades": int(metrics.total_trades),
            "sharpe": round(float(metrics.sharpe_ratio), 2),
            "dd":     round(float(metrics.max_drawdown) * 100, 1),
        }
    except Exception as exc:
        print(f"    ERROR: {exc}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    results: list[dict] = []

    # Pre-load all 1H data files once to avoid repeated IO
    print("Loading data files...", flush=True)
    data_1h: dict[str, Optional[pd.DataFrame]] = {}
    for coin, prefix in COIN_PREFIXES.items():
        path = find_data_file(prefix, "1h")
        if path:
            try:
                data_1h[coin] = load_ohlcv(str(path))
            except Exception as exc:
                print(f"  WARN: could not load {path}: {exc}")
                data_1h[coin] = None
        else:
            print(f"  WARN: no 1H file for {coin} ({prefix})")
            data_1h[coin] = None

    # Resample 4H data once per coin
    print("Resampling 4H data...", flush=True)
    data_4h: dict[str, Optional[pd.DataFrame]] = {}
    for coin, df in data_1h.items():
        if df is not None:
            try:
                data_4h[coin] = resample_to_4h(df)
            except Exception as exc:
                print(f"  WARN: resample failed for {coin}: {exc}")
                data_4h[coin] = None
        else:
            data_4h[coin] = None

    print(f"\nRunning 145 backtests ({len(DEPLOYED_PFS)} coins × {len(NEW_STRATS)} strategies)...\n")

    # ---------------------------------------------------------------------------
    # Header
    # ---------------------------------------------------------------------------
    header = (
        f"{'Coin':<12} {'Current_PF':>10}  {'New Strategy':<18} {'New_PF':>7} "
        f"{'WR%':>5} {'Trades':>7} {'Sharpe':>7} {'DD%':>5}  Better?"
    )
    separator = "-" * len(header)
    print("DEPLOYED COINS x NEW STRATEGIES (145 backtests)")
    print(separator)
    print(header)
    print(separator)

    for coin in DEPLOYED_PFS:
        current_pf = DEPLOYED_PFS[coin]
        df_1h = data_1h.get(coin)
        df_4h = data_4h.get(coin)

        for strat_key, strat_label, is_4h, sl_mult, tp_mult in NEW_STRATS:
            # Select signal and trend dataframes
            if is_4h:
                df_signal = df_4h
                df_trend = df_4h
            else:
                df_signal = df_1h
                df_trend = df_1h

            if df_signal is None or df_signal.empty:
                result_str = f"{'N/A':>7} {'N/A':>5} {'N/A':>7} {'N/A':>7} {'N/A':>5}  SKIP"
                print(
                    f"{coin:<12} {current_pf:>10.2f}  {strat_label:<18} {result_str}",
                    flush=True,
                )
                record = {
                    "coin": coin,
                    "current_pf": current_pf,
                    "strategy": strat_label,
                    "strat_key": strat_key,
                    "is_4h": is_4h,
                    "pf": None,
                    "wr": None,
                    "trades": None,
                    "sharpe": None,
                    "dd": None,
                    "better": False,
                    "error": "no_data",
                }
                results.append(record)
                continue

            cfg = build_config(coin, strat_key, sl_mult, tp_mult)
            m = run_backtest(cfg, df_signal, df_trend)

            if m is None:
                result_str = f"{'ERR':>7} {'ERR':>5} {'ERR':>7} {'ERR':>7} {'ERR':>5}  ERR"
                print(
                    f"{coin:<12} {current_pf:>10.2f}  {strat_label:<18} {result_str}",
                    flush=True,
                )
                record = {
                    "coin": coin,
                    "current_pf": current_pf,
                    "strategy": strat_label,
                    "strat_key": strat_key,
                    "is_4h": is_4h,
                    "pf": None,
                    "wr": None,
                    "trades": None,
                    "sharpe": None,
                    "dd": None,
                    "better": False,
                    "error": "engine_error",
                }
                results.append(record)
                continue

            better = m["pf"] > current_pf and m["trades"] >= 5
            better_str = "YES" if better else "no"

            print(
                f"{coin:<12} {current_pf:>10.2f}  {strat_label:<18} "
                f"{m['pf']:>7.2f} {m['wr']:>5.1f} {m['trades']:>7} "
                f"{m['sharpe']:>7.2f} {m['dd']:>5.1f}  {better_str}",
                flush=True,
            )

            record = {
                "coin": coin,
                "current_pf": current_pf,
                "strategy": strat_label,
                "strat_key": strat_key,
                "is_4h": is_4h,
                "pf": m["pf"],
                "wr": m["wr"],
                "trades": m["trades"],
                "sharpe": m["sharpe"],
                "dd": m["dd"],
                "better": better,
                "error": None,
            }
            results.append(record)

    print(separator)

    # ---------------------------------------------------------------------------
    # Upgrades summary
    # ---------------------------------------------------------------------------
    upgrades = [r for r in results if r.get("better")]
    print(f"\n{'=' * 70}")
    print("UPGRADES FOUND:")
    if upgrades:
        print(f"{'Coin':<12} {'Current(PF)':>11}  {'New Strategy':<18} {'New_PF':>7}  Improvement")
        print("-" * 70)
        for r in sorted(upgrades, key=lambda x: (x["pf"] or 0) - x["current_pf"], reverse=True):
            improvement = (r["pf"] or 0) - r["current_pf"]
            print(
                f"{r['coin']:<12} {r['current_pf']:>11.2f}  {r['strategy']:<18} "
                f"{r['pf']:>7.2f}  +{improvement:.2f}"
            )
    else:
        print("  None — no new strategy beats the current deployed PF for any coin.")

    print(f"{'=' * 70}\n")

    # ---------------------------------------------------------------------------
    # Best new strategy per coin
    # ---------------------------------------------------------------------------
    print("BEST NEW STRATEGY PER COIN:")
    print(f"{'Coin':<12} {'Best_Strategy':<18} {'Best_PF':>7} {'Current_PF':>11}  Status")
    print("-" * 65)
    for coin in DEPLOYED_PFS:
        coin_results = [r for r in results if r["coin"] == coin and r["pf"] is not None]
        if not coin_results:
            print(f"{coin:<12} {'N/A':<18} {'N/A':>7} {DEPLOYED_PFS[coin]:>11.2f}  no data")
            continue
        best = max(coin_results, key=lambda x: x["pf"] or 0)
        status = "UPGRADE" if best.get("better") else "keep current"
        print(
            f"{coin:<12} {best['strategy']:<18} {best['pf']:>7.2f} "
            f"{DEPLOYED_PFS[coin]:>11.2f}  {status}"
        )

    # ---------------------------------------------------------------------------
    # Save results
    # ---------------------------------------------------------------------------
    class NumpyEncoder(json.JSONEncoder):
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

    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2, cls=NumpyEncoder)

    print(f"\nSaved {len(results)} results to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
