"""Core 7 coins × 32 new strategies — full BacktestEngine verification.

Tests every strategy from R7-R12 against BTC, WIF, ARC, AVAX, GUN, ATH, 1000PEPE.
224 backtests total. Prints per-coin tables and an upgrade recommendation summary.
"""

import copy
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, "/Users/iceai/Work/ccbt")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"

# ---------------------------------------------------------------------------
# Stdout suppressor (engine prints a summary on each run)
# ---------------------------------------------------------------------------
class _Quiet:
    def __enter__(self) -> "_Quiet":
        self._orig = sys.stdout
        sys.stdout = open(os.devnull, "w")
        return self

    def __exit__(self, *_: object) -> None:
        sys.stdout.close()
        sys.stdout = self._orig


# ---------------------------------------------------------------------------
# Coin definitions
# ---------------------------------------------------------------------------
COINS = [
    # (display_name, file_prefix, current_strategy_label, current_pf)
    ("BTC",       "btcusdt",       "EMA 15m",        1.85),
    ("WIF",       "wifusdt",       "EMA 15m",        1.64),
    ("ARC",       "arcusdt",       "EMA 15m / Ichi", 1.89),
    ("AVAX",      "avaxusdt",      "Ichimoku 1H",    1.95),
    ("GUN",       "gunusdt",       "Ichimoku 1H",    4.00),
    ("ATH",       "athusdt",       "Ichimoku 1H",    2.51),
    ("1000PEPE",  "1000pepeusdt",  "Vol Exp 1H",    10.85),
]

# ---------------------------------------------------------------------------
# Strategy grid  (R7-R12)
# ---------------------------------------------------------------------------
STRATEGIES = [
    # R7-R8 new signals
    {"signal": "dual_supertrend",    "tf": "1h", "sl": 2.5, "tp": 4.0, "trail": 3.0, "label": "Dual ST 1H"},
    {"signal": "dual_supertrend",    "tf": "4h", "sl": 2.5, "tp": 4.0, "trail": 3.0, "label": "Dual ST 4H"},
    {"signal": "alligator",          "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Alligator 1H"},
    {"signal": "alligator",          "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Alligator 4H"},
    {"signal": "ema_ichimoku_hybrid","tf": "4h", "sl": 1.5, "tp": 4.0, "trail": 2.0, "label": "EMA+Ichi 4H"},
    {"signal": "ichi_supertrend",    "tf": "4h", "sl": 2.0, "tp": 5.0, "trail": 3.0, "label": "Ichi+ST 4H"},
    # R9 new signals
    {"signal": "adx_di_cross",       "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "ADX+DI 1H"},
    {"signal": "choppiness_ema",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Chop+EMA 1H"},
    {"signal": "williams_r_adx",     "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "WilliamsR+ADX 4H"},
    {"signal": "roc_momentum",       "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "ROC 1H"},
    {"signal": "roc_momentum",       "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "ROC 4H"},
    {"signal": "stoch_supertrend",   "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Stoch+ST 1H"},
    {"signal": "price_channel_vol",  "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "PriceCh+Vol 1H"},
    {"signal": "ema_alligator",      "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "EMA+Allig 1H"},
    {"signal": "supertrend_volume",  "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "ST+Vol 1H"},
    {"signal": "supertrend_volume",  "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "ST+Vol 4H"},
    # R10 new signals
    {"signal": "dual_thrust",        "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Dual Thrust 1H"},
    {"signal": "dual_thrust",        "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Dual Thrust 4H"},
    {"signal": "awesome_oscillator", "tf": "1h", "sl": 1.5, "tp": 4.0, "trail": 3.0, "label": "AO 1H"},
    {"signal": "awesome_oscillator", "tf": "4h", "sl": 1.5, "tp": 4.0, "trail": 3.0, "label": "AO 4H"},
    {"signal": "range_bounce",       "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0, "label": "Range Bounce 1H"},
    # R11 new signals
    {"signal": "stoch_mtf",          "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Stoch MTF 1H"},
    {"signal": "stoch_mtf",          "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Stoch MTF 4H"},
    {"signal": "zscore_meanrev",     "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0, "label": "Z-Score 1H"},
    {"signal": "ema_ribbon",         "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "EMA Ribbon 1H"},
    {"signal": "ema_ribbon",         "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "EMA Ribbon 4H"},
    # R12 combo signals
    {"signal": "ribbon_rsi_vol",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Ribbon+RSI+Vol 1H"},
    {"signal": "dualthrust_adx",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "DualThrust+ADX 1H"},
    {"signal": "zscore_stoch",       "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0, "label": "ZScore+Stoch 1H"},
    {"signal": "ichi_adx",           "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Ichi+ADX 1H"},
    {"signal": "ichi_adx",           "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Ichi+ADX 4H"},
    {"signal": "ribbon_ao",          "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0, "label": "Ribbon+AO 1H"},
]

# ---------------------------------------------------------------------------
# All known signal keys (for disabling everything except target)
# ---------------------------------------------------------------------------
ALL_SIGNALS = [
    "ema_crossover", "ema_fast_crossover", "ema_pullback", "rsi_divergence",
    "bb_breakout", "mean_reversion", "body_dominance", "squeeze_release",
    "ichimoku_cloud", "supertrend", "vol_expansion",
    "dual_supertrend", "alligator", "ema_ichimoku_hybrid", "ichi_supertrend",
    "volexp_supertrend",
    "adx_di_cross", "choppiness_ema", "williams_r_adx", "roc_momentum",
    "stoch_supertrend", "price_channel_vol", "ema_alligator", "supertrend_volume",
    "stoch_mtf", "zscore_meanrev", "ema_ribbon",
    "dual_thrust", "awesome_oscillator", "range_bounce",
    "ribbon_rsi_vol", "dualthrust_adx", "zscore_stoch", "ichi_adx", "ribbon_ao",
]

# ---------------------------------------------------------------------------
# Template config
# ---------------------------------------------------------------------------
with open(PROJECT_ROOT / "config_avax_ichi.json") as _f:
    TEMPLATE: dict = json.load(_f)

# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------
def build_config(coin_prefix: str, signal: str, tf: str, sl: float, tp: float, trail: float) -> dict:
    """Build a BacktestEngine config for one coin + strategy combination."""
    cfg = copy.deepcopy(TEMPLATE)
    cfg["symbol"] = f"{coin_prefix.upper()}USDT"
    # Normalise 1000PEPE symbol (already correct as prefix = 1000pepeusdt → 1000PEPEUSDT)
    cfg["timeframe_signal"] = tf
    cfg["timeframe_trend"] = tf
    cfg["risk_per_trade"] = 0.01
    cfg["leverage"] = 25
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_min"] = 0.0
    cfg["volume_mult"] = 1.0
    cfg["volume_max_mult"] = None
    cfg["min_rr_ratio"] = 0
    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"] = {"enabled": False}
    cfg["signals"] = {s: {"enabled": False} for s in ALL_SIGNALS}
    cfg["signals"][signal] = {"enabled": True}
    if "ichi" in signal:
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52
    return cfg


# ---------------------------------------------------------------------------
# Data loader helper — handles 4H via 1H CSV
# ---------------------------------------------------------------------------
def load_data(prefix: str, tf: str) -> Optional[object]:
    """Load OHLCV data for prefix/tf. For 4h, load the 1h file (engine resamples)."""
    candidates = sorted(DATA_DIR.glob(f"{prefix}_1h*.csv"))
    if not candidates:
        return None
    return load_ohlcv(str(candidates[0]))


# ---------------------------------------------------------------------------
# Engine runner
# ---------------------------------------------------------------------------
def run_one(coin_prefix: str, signal: str, tf: str, sl: float, tp: float, trail: float) -> Optional[dict]:
    """Run BacktestEngine for one config. Returns metrics dict or None on error."""
    try:
        df = load_data(coin_prefix, tf)
        if df is None or len(df) < 200:
            return {"error": "insufficient_data"}
        cfg = build_config(coin_prefix, signal, tf, sl, tp, trail)
        engine = BacktestEngine(cfg, initial_balance=10000.0)
        with _Quiet():
            metrics = engine.run(df, df)
        return {
            "pf": round(metrics.profit_factor, 2),
            "wr": round(metrics.win_rate * 100, 1),
            "trades": metrics.total_trades,
            "dd": round(metrics.max_drawdown * 100, 1),
            "sharpe": round(metrics.sharpe_ratio, 2),
            "final": round(engine.state.balance, 2),
        }
    except Exception as exc:
        return {"error": str(exc)[:60]}


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
MIN_PF = 1.3
MIN_TRADES = 6


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 72)
    print("CORE 7 COINS x 32 NEW STRATEGIES (224 backtests)")
    print("BacktestEngine | $10K initial | 25x leverage | 1% risk/trade")
    print("Pass threshold: PF >= 1.30 AND trades >= 6")
    print("=" * 72)

    # coin_name -> list of (label, result)
    all_results: dict[str, list] = {}
    # upgrades: list of (coin, current_pf, best_new_label, best_new_pf, trades)
    upgrades: list[tuple] = []
    # best overall per coin (label, pf, trades)
    best_overall: dict[str, tuple] = {}

    total = len(COINS) * len(STRATEGIES)
    done = 0

    for display_name, prefix, current_label, current_pf in COINS:
        coin_rows: list = []
        coin_best_pf = 0.0
        coin_best_label = current_label
        coin_best_trades = 0

        # Data availability note
        data_path = sorted(DATA_DIR.glob(f"{prefix}_1h*.csv"))
        if data_path:
            df_check = load_ohlcv(str(data_path[0]))
            n_rows = len(df_check)
            approx_months = round(n_rows / (24 * 30))
            data_note = f"~{approx_months}mo data ({n_rows} bars)"
        else:
            data_note = "NO DATA"
            for s in STRATEGIES:
                done += 1
            all_results[display_name] = []
            continue

        print(f"\n{'=' * 72}")
        print(f"{display_name}  [{data_note}]")
        print(f"  Current: {current_label} (PF ~{current_pf:.2f})")
        sep = "-" * 66
        header = f"  {'Strategy':<26} {'PF':>5}  {'WR%':>5}  {'Trades':>6}  {'DD%':>5}  {'Sharpe':>6}  Status"
        print(header)
        print("  " + sep)

        for strat in STRATEGIES:
            label = strat["label"]
            signal = strat["signal"]
            tf = strat["tf"]
            sl = strat["sl"]
            tp = strat["tp"]
            trail = strat["trail"]

            result = run_one(prefix, signal, tf, sl, tp, trail)
            done += 1

            if result is None or "error" in result:
                err = result["error"] if result else "None"
                status = f"ERR({err[:20]})"
                row = (label, None, status)
                print(f"  {label:<26} {'N/A':>5}  {'N/A':>5}  {'N/A':>6}  {'N/A':>5}  {'N/A':>6}  {status}")
            else:
                pf = result["pf"]
                wr = result["wr"]
                trades = result["trades"]
                dd = result["dd"]
                sharpe = result["sharpe"]
                passes = pf >= MIN_PF and trades >= MIN_TRADES
                better = passes and pf > current_pf
                status = ""
                if passes and better:
                    status = "BETTER"
                elif passes:
                    status = "pass"
                else:
                    status = "-"

                print(f"  {label:<26} {pf:>5.2f}  {wr:>5.1f}  {trades:>6}  {dd:>5.1f}  {sharpe:>6.2f}  {status}")

                if passes and pf > coin_best_pf:
                    coin_best_pf = pf
                    coin_best_label = label
                    coin_best_trades = trades

                row = (label, result, status)

            coin_rows.append(row)

        print("  " + sep)
        all_results[display_name] = coin_rows

        # Best overall (new strategy only, must beat current)
        if coin_best_pf > current_pf:
            upgrades.append((display_name, current_label, current_pf, coin_best_label, coin_best_pf, coin_best_trades))

        # Track best overall including current
        if coin_best_pf > 0:
            best_overall[display_name] = (coin_best_label, coin_best_pf, coin_best_trades)
        else:
            best_overall[display_name] = (current_label, current_pf, 0)

        # Progress
        pct = done / total * 100
        print(f"\n  [{done}/{total} = {pct:.0f}%]  Best new PF for {display_name}: "
              f"{coin_best_pf:.2f} ({coin_best_label})" if coin_best_pf > 0 else
              f"\n  [{done}/{total} = {pct:.0f}%]  No passing new strategy for {display_name}")

    # ---------------------------------------------------------------------------
    # Final recommendations
    # ---------------------------------------------------------------------------
    print("\n\n" + "=" * 72)
    print("RECOMMENDATIONS")
    print("=" * 72)

    if upgrades:
        print("\nUPGRADES FOUND:")
        print(f"  {'Coin':<10} {'Current':<20} {'CurPF':>6}  {'Best New Strategy':<26} {'NewPF':>6}  {'Trades':>7}  {'Delta':>7}")
        print("  " + "-" * 90)
        for coin, cur_lbl, cur_pf, new_lbl, new_pf, new_trades in sorted(upgrades, key=lambda x: -(x[4] - x[2])):
            delta = new_pf - cur_pf
            print(f"  {coin:<10} {cur_lbl:<20} {cur_pf:>6.2f}  {new_lbl:<26} {new_pf:>6.2f}  {new_trades:>7}  +{delta:>6.2f}")
    else:
        print("\n  No new strategy beats the current deployment for any of the 7 coins.")
        print("  Current deployments remain optimal.")

    print(f"\nBEST STRATEGY PER COIN (including current):")
    print(f"  {'Coin':<10} {'Best Strategy':<28} {'PF':>6}  {'Trades':>7}")
    print("  " + "-" * 58)
    for display_name, prefix, current_label, current_pf in COINS:
        if display_name in best_overall:
            lbl, pf, trades = best_overall[display_name]
            marker = " *NEW*" if lbl != current_label else ""
            print(f"  {display_name:<10} {lbl:<28} {pf:>6.2f}  {trades:>7}{marker}")

    # Summary counts
    total_pass = sum(
        1 for rows in all_results.values()
        for (lbl, res, status) in rows
        if res and "pf" in res and res["pf"] >= MIN_PF and res["trades"] >= MIN_TRADES
    )
    print(f"\nTotal passing combos (PF >= {MIN_PF}, trades >= {MIN_TRADES}): {total_pass} / {total}")
    print(f"Upgrade candidates: {len(upgrades)} coins")

    # Top 10 absolute performers across all coin × strategy combos
    all_pass_list = []
    for display_name, prefix, current_label, current_pf in COINS:
        rows = all_results.get(display_name, [])
        for (lbl, res, status) in rows:
            if res and "pf" in res and res["pf"] >= MIN_PF and res["trades"] >= MIN_TRADES:
                all_pass_list.append((display_name, lbl, res["pf"], res["wr"], res["trades"], res["dd"]))

    all_pass_list.sort(key=lambda x: -x[2])

    if all_pass_list:
        print(f"\nTOP 10 ABSOLUTE PERFORMERS (any coin × any new strategy):")
        print(f"  {'Coin':<10} {'Strategy':<26} {'PF':>6}  {'WR%':>5}  {'Trades':>7}  {'DD%':>5}")
        print("  " + "-" * 65)
        for coin, lbl, pf, wr, trades, dd in all_pass_list[:10]:
            print(f"  {coin:<10} {lbl:<26} {pf:>6.2f}  {wr:>5.1f}  {trades:>7}  {dd:>5.1f}")

    print("\n" + "=" * 72)
    print("Done.")


if __name__ == "__main__":
    main()
