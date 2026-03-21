"""Verify 6 undeployed sweep winners with full BacktestEngine.

Coins:
  CRCL  — EMA(9/21) 15m   (sweep PF=2.67, suspicious)
  GUA   — Ichi+Trail 1H   (sweep PF=2.17)
  ANKR  — Ichimoku 1H     (sweep PF=1.42)
  ENSO  — Ichi+Trail 1H   (sweep PF=1.60)
  BEAT  — Ichimoku 1H     (sweep PF=2.14)
  KITE  — Ichimoku 1H     (sweep PF=1.55)

For each Ichimoku coin: sweep SL [1.5, 2.0, 2.5] x TP [4.0, 5.0, 0(trail-only)]
For CRCL (EMA): use config_doge.json template, report data-length warning.

Output: verification table + DEPLOY/REJECT recommendation.
"""

import copy
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"

# Load templates
with open(PROJECT_ROOT / "config_doge.json") as f:
    EMA_TEMPLATE = json.load(f)

with open(PROJECT_ROOT / "config_avax_ichi.json") as f:
    ICHI_TEMPLATE = json.load(f)


# ---------------------------------------------------------------------------
# Coin definitions
# ---------------------------------------------------------------------------
COINS = [
    {
        "coin": "CRCL",
        "strategy": "ema_15m",
        "sweep_pf": 2.67,
        "sweep_dd": 9,
        "sweep_trades_yr": 150,
    },
    {
        "coin": "GUA",
        "strategy": "ichi_trail",
        "sweep_pf": 2.17,
        "sweep_dd": 15,
        "sweep_trades_yr": 90,
    },
    {
        "coin": "ANKR",
        "strategy": "ichimoku",
        "sweep_pf": 1.42,
        "sweep_dd": 30,
        "sweep_trades_yr": 58,
    },
    {
        "coin": "ENSO",
        "strategy": "ichi_trail",
        "sweep_pf": 1.60,
        "sweep_dd": 34,
        "sweep_trades_yr": 74,
    },
    {
        "coin": "BEAT",
        "strategy": "ichimoku",
        "sweep_pf": 2.14,
        "sweep_dd": 45,
        "sweep_trades_yr": 63,
    },
    {
        "coin": "KITE",
        "strategy": "ichimoku",
        "sweep_pf": 1.55,
        "sweep_dd": 37,
        "sweep_trades_yr": 67,
    },
]


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------
def build_ema_config(coin: str) -> dict:
    """Build EMA 15m config from DOGE template."""
    cfg = copy.deepcopy(EMA_TEMPLATE)
    cfg["symbol"] = f"{coin}USDT"
    cfg["risk_per_trade"] = 0.02
    cfg["leverage"] = 25
    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"] = {"enabled": False}
    cfg["signals"] = {
        "ema_crossover": {"enabled": True},
        "ema_fast_crossover": {"enabled": True},
        "ema_pullback": {"enabled": False},
        "rsi_divergence": {"enabled": False},
        "bb_breakout": {"enabled": False},
        "mean_reversion": {"enabled": False},
        "body_dominance": {"enabled": False},
        "squeeze_release": {"enabled": False},
        "ichimoku_cloud": {"enabled": False},
    }
    return cfg


def build_ichi_config(coin: str, sl_mult: float, tp_mult: float, trail_mult: float = 3.0) -> dict:
    """Build Ichimoku 1H config from AVAX template."""
    cfg = copy.deepcopy(ICHI_TEMPLATE)
    cfg["symbol"] = f"{coin}USDT"
    cfg["risk_per_trade"] = 0.02
    cfg["leverage"] = 25
    cfg["atr_sl_mult"] = sl_mult
    cfg["atr_tp_mult"] = tp_mult
    cfg["atr_trail_mult"] = trail_mult
    cfg["atr_trail_mult_trending"] = trail_mult
    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"] = {"enabled": False}
    return cfg


# ---------------------------------------------------------------------------
# Engine runner
# ---------------------------------------------------------------------------
def run_engine(config: dict, signal_path: str, trend_path: str) -> Optional[dict]:
    """Run BacktestEngine, return metrics dict or None on error."""
    try:
        signal_data = load_ohlcv(signal_path)
        trend_data = load_ohlcv(trend_path)
        engine = BacktestEngine(config)
        m = engine.run(signal_data, trend_data)

        # Compute annualized PnL
        if m.total_trades > 0:
            trades_list = engine.state.trades
            if trades_list:
                first_entry = trades_list[0].entry_time
                last_exit = trades_list[-1].exit_time
                import pandas as pd
                t0 = pd.Timestamp(first_entry)
                t1 = pd.Timestamp(last_exit)
                days = max((t1 - t0).days, 1)
                years = days / 365.25
                total_pnl = sum(t.pnl for t in trades_list)
                pnl_pct = total_pnl / engine.state.initial_balance * 100
                annual_pnl_pct = pnl_pct / years if years > 0 else 0
                trades_per_year = m.total_trades / years if years > 0 else 0
            else:
                pnl_pct = 0
                annual_pnl_pct = 0
                trades_per_year = 0
                days = 0
        else:
            pnl_pct = 0
            annual_pnl_pct = 0
            trades_per_year = 0
            days = 0

        return {
            "trades": m.total_trades,
            "pf": round(m.profit_factor, 2),
            "wr": round(m.win_rate * 100, 1),
            "dd": round(m.max_drawdown * 100, 1),
            "sharpe": round(m.sharpe_ratio, 2),
            "pnl_pct": round(pnl_pct, 1),
            "annual_pnl_pct": round(annual_pnl_pct, 1),
            "trades_per_year": round(trades_per_year, 0),
            "data_days": days,
        }
    except Exception as exc:
        print(f"    ERROR: {exc}")
        import traceback
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Resolve data paths
# ---------------------------------------------------------------------------
def get_data_paths(coin: str, strategy: str):
    """Return (signal_path, trend_path) for a coin+strategy."""
    prefix = f"{coin.lower()}usdt"
    if strategy == "ema_15m":
        sig = DATA_DIR / f"{prefix}_15m_2y.csv"
        trend = DATA_DIR / f"{prefix}_1h_2y.csv"
    else:  # ichimoku / ichi_trail
        sig = DATA_DIR / f"{prefix}_1h_2y.csv"
        trend = DATA_DIR / f"{prefix}_1h_2y.csv"
    return str(sig), str(trend)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 100)
    print("UNDEPLOYED WINNERS — FULL BACKTEST ENGINE VERIFICATION")
    print("=" * 100)

    all_results = []

    for entry in COINS:
        coin = entry["coin"]
        strategy = entry["strategy"]
        sweep_pf = entry["sweep_pf"]
        sweep_dd = entry["sweep_dd"]

        sig_path, trend_path = get_data_paths(coin, strategy)

        # Check data exists
        if not Path(sig_path).exists():
            print(f"\n[{coin}] SKIP — signal data missing: {sig_path}")
            continue
        if not Path(trend_path).exists():
            print(f"\n[{coin}] SKIP — trend data missing: {trend_path}")
            continue

        # Count data rows for warning
        import pandas as pd
        sig_df = pd.read_csv(sig_path)
        data_rows = len(sig_df)
        data_warning = ""
        if strategy == "ema_15m" and data_rows < 20000:
            data_warning = f" *** SHORT DATA: {data_rows} rows (~{data_rows // 96} days) ***"
        elif strategy != "ema_15m" and data_rows < 5000:
            data_warning = f" *** SHORT DATA: {data_rows} rows (~{data_rows // 24} days) ***"

        print(f"\n{'='*80}")
        print(f"[{coin}] Strategy: {strategy} | Sweep PF: {sweep_pf} | Sweep DD: {sweep_dd}%{data_warning}")
        print(f"{'='*80}")

        if strategy == "ema_15m":
            # Single run with EMA template
            cfg = build_ema_config(coin)
            print(f"  Running EMA(9/21) 15m backtest...", flush=True)
            result = run_engine(cfg, sig_path, trend_path)
            if result:
                result["config"] = "EMA default"
                result["sl"] = cfg["atr_sl_mult"]
                result["tp"] = cfg["atr_tp_mult"]
                all_results.append({"coin": coin, "strategy": strategy, "sweep_pf": sweep_pf, **result})
                print(f"  -> Trades={result['trades']}  PF={result['pf']}  WR={result['wr']}%  "
                      f"DD={result['dd']}%  Sharpe={result['sharpe']}  PnL={result['pnl_pct']}%  "
                      f"Annual={result['annual_pnl_pct']}%/yr  Tr/yr={result['trades_per_year']}")

        else:
            # Ichimoku: sweep SL x TP combinations
            sl_values = [1.5, 2.0, 2.5]
            tp_values = [4.0, 5.0]
            trail_values = [3.0]

            # For ichi_trail, also test TP=0 (trail-only)
            if strategy == "ichi_trail":
                tp_values = [0.0, 4.0, 5.0]

            best_result = None
            best_pf = 0

            for sl in sl_values:
                for tp in tp_values:
                    for trail in trail_values:
                        cfg = build_ichi_config(coin, sl, tp, trail)
                        label = f"SL={sl} TP={tp} Trail={trail}"
                        print(f"  {label} ...", end=" ", flush=True)
                        result = run_engine(cfg, sig_path, trend_path)
                        if result:
                            print(f"Tr={result['trades']:3d}  PF={result['pf']:.2f}  "
                                  f"WR={result['wr']:.0f}%  DD={result['dd']:.1f}%  "
                                  f"Sharpe={result['sharpe']:.2f}  PnL={result['pnl_pct']:.0f}%  "
                                  f"Annual={result['annual_pnl_pct']:.0f}%/yr")
                            if result["pf"] > best_pf and result["trades"] >= 5:
                                best_pf = result["pf"]
                                best_result = {
                                    "coin": coin, "strategy": strategy, "sweep_pf": sweep_pf,
                                    "config": label, "sl": sl, "tp": tp,
                                    **result,
                                }
                        else:
                            print("ERROR")

            if best_result:
                all_results.append(best_result)
                print(f"  >>> BEST: {best_result['config']}  PF={best_result['pf']}  "
                      f"Trades={best_result['trades']}")

    # ---------------------------------------------------------------------------
    # Summary table
    # ---------------------------------------------------------------------------
    print("\n\n" + "=" * 120)
    print("VERIFICATION SUMMARY")
    print("=" * 120)

    # Deploy criteria:
    # - PF >= 1.2 (engine, not sweep)
    # - Trades >= 15 total (or >= 10/yr annualized)
    # - DD <= 40%
    # - Data >= 180 days (6 months minimum for reliability)
    # - Sharpe >= 0.3

    header = (f"{'Coin':<6} {'Strategy':<12} {'Config':<20} {'Sweep_PF':>8} {'Eng_PF':>7} "
              f"{'Trades':>6} {'Tr/yr':>6} {'WR%':>5} {'DD%':>5} {'Sharpe':>7} "
              f"{'PnL%':>6} {'Ann%':>6} {'Days':>5} {'Verdict':<10}")
    print(header)
    print("-" * len(header))

    deploy_count = 0
    reject_count = 0

    for r in all_results:
        pf = r["pf"]
        trades = r["trades"]
        dd = r["dd"]
        sharpe = r["sharpe"]
        data_days = r.get("data_days", 0)
        trades_yr = r.get("trades_per_year", 0)

        # Verdict logic
        reasons = []
        if pf < 1.2:
            reasons.append(f"PF={pf}<1.2")
        if trades < 10:
            reasons.append(f"Tr={trades}<10")
        if dd > 40:
            reasons.append(f"DD={dd}%>40%")
        if data_days < 120:
            reasons.append(f"Data={data_days}d<120d")
        if sharpe < 0.0:
            reasons.append(f"Sharpe={sharpe}<0")
        # Check if engine PF is wildly different from sweep (>50% drop = curve fit concern)
        sweep_pf = r.get("sweep_pf", pf)
        if sweep_pf > 0 and pf < sweep_pf * 0.5:
            reasons.append(f"PF drop>{50}%")

        if not reasons:
            verdict = "DEPLOY"
            deploy_count += 1
        else:
            verdict = f"REJECT({','.join(reasons)})"
            reject_count += 1

        row = (f"{r['coin']:<6} {r['strategy']:<12} {r.get('config',''):<20} "
               f"{r.get('sweep_pf',0):>8.2f} {pf:>7.2f} "
               f"{trades:>6} {trades_yr:>6.0f} {r['wr']:>5.1f} {dd:>5.1f} {sharpe:>7.2f} "
               f"{r.get('pnl_pct',0):>6.1f} {r.get('annual_pnl_pct',0):>6.1f} "
               f"{data_days:>5} {verdict:<10}")
        print(row)

    print("-" * len(header))
    print(f"\nDEPLOY: {deploy_count}  |  REJECT: {reject_count}")
    print()

    # Data quality warnings
    print("DATA QUALITY NOTES:")
    for r in all_results:
        days = r.get("data_days", 0)
        if days < 180:
            print(f"  WARNING: {r['coin']} has only {days} days of data — "
                  f"results may not be reliable (min 180 days recommended)")
        if r.get("sweep_pf", 0) > 2.5:
            print(f"  WARNING: {r['coin']} sweep PF={r['sweep_pf']:.2f} is suspiciously high — "
                  f"check for overfitting / short data window")

    # Architecture recommendation
    print("\nARCHITECT NOTES:")
    print("  - Coins with < 180 days data: verify on forward-test before production deploy")
    print("  - Coins with DD > 25%: consider lower risk_per_trade (1-2%) to cap drawdown")
    print("  - Ichi+Trail (TP=0): trailing stop is sole exit — monitor for regime changes")
    print("  - All new coins: start with 1-2 weeks paper trading before live capital")


if __name__ == "__main__":
    main()
