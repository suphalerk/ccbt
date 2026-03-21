"""Verify lightweight sweep winners with the full BacktestEngine.

Loads sweep_winners.json, runs each winner through BacktestEngine,
optionally re-runs with funding scorer if data exists, and saves
verified results to data/verified_winners.json.
"""

import copy
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

# Suppress noisy backtest logs; show only WARNING+ from engine internals
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"
SWEEP_WINNERS_PATH = DATA_DIR / "sweep_winners.json"
VERIFIED_RESULTS_PATH = DATA_DIR / "verified_winners.json"

# ---------------------------------------------------------------------------
# Config templates (loaded once)
# ---------------------------------------------------------------------------
with open(PROJECT_ROOT / "config_doge.json") as _f:
    _EMA_TEMPLATE: dict = json.load(_f)

with open(PROJECT_ROOT / "config_avax_ichi.json") as _f:
    _ICHI_TEMPLATE: dict = json.load(_f)

# ---------------------------------------------------------------------------
# Funding scorer config (from config_wif.json pattern)
# ---------------------------------------------------------------------------
_FUNDING_SCORER_CONFIG: dict = {
    "enabled": True,
    "entry_threshold": 0.10,
    "high_conviction": 0.5,
    "weights": {
        "ema_alignment": 0.15,
        "momentum_mtf": 0.10,
        "volume_surge": 0.10,
        "rsi_zone": 0.10,
        "atr_regime": 0.05,
        "candle_strength": 0.15,
        "funding_rate": 0.35,
    },
}

# Pass/fail thresholds
MIN_ENGINE_PF = 1.1
MIN_ENGINE_TRADES = 10


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------

def build_ema_config(winner: dict) -> dict:
    """Build a full engine config for an EMA 15m winner."""
    cfg = copy.deepcopy(_EMA_TEMPLATE)
    prefix = winner["prefix"]
    coin_upper = winner.get("coin", prefix.replace("usdt", "").upper())
    symbol = f"{coin_upper}USDT"

    cfg["symbol"] = symbol
    cfg["risk_per_trade"] = 0.01
    cfg["leverage"] = 25

    # Propagate strategy params from the sweep result
    params = winner.get("params", {})
    if "ema_slope_min" in params:
        cfg["ema_slope_min"] = params["ema_slope_min"]
    if "atr_sl_mult" in params:
        cfg["atr_sl_mult"] = params["atr_sl_mult"]
    if "atr_tp_mult" in params:
        cfg["atr_tp_mult"] = params["atr_tp_mult"]
    if "atr_trail_mult" in params:
        cfg["atr_trail_mult"] = params["atr_trail_mult"]
        cfg["atr_trail_mult_trending"] = params["atr_trail_mult"]
    if "volume_mult" in params:
        cfg["volume_mult"] = params["volume_mult"]

    # Ensure correct signal set
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

    # Disable scorer for baseline run
    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"] = {"enabled": False}

    return cfg


def build_ichimoku_config(winner: dict) -> dict:
    """Build a full engine config for an Ichimoku 1H winner."""
    cfg = copy.deepcopy(_ICHI_TEMPLATE)
    prefix = winner["prefix"]
    coin_upper = winner.get("coin", prefix.replace("usdt", "").upper())
    symbol = f"{coin_upper}USDT"

    cfg["symbol"] = symbol
    cfg["risk_per_trade"] = 0.01
    cfg["leverage"] = 25

    # Standard Ichimoku params per requirements
    cfg["atr_sl_mult"] = 2.0
    cfg["atr_tp_mult"] = 5.0
    cfg["atr_trail_mult"] = 3.0
    cfg["atr_trail_mult_trending"] = 3.0
    cfg["ichimoku_tenkan"] = 9
    cfg["ichimoku_kijun"] = 26
    cfg["ichimoku_senkou_b"] = 52

    # Allow sweep-level overrides if present
    params = winner.get("params", {})
    for key in ("atr_sl_mult", "atr_tp_mult", "atr_trail_mult"):
        if key in params:
            cfg[key] = params[key]

    # Ensure correct signal set
    cfg["signals"] = {
        "ema_crossover": {"enabled": False},
        "ema_fast_crossover": {"enabled": False},
        "ema_pullback": {"enabled": False},
        "rsi_divergence": {"enabled": False},
        "bb_breakout": {"enabled": False},
        "mean_reversion": {"enabled": False},
        "body_dominance": {"enabled": False},
        "squeeze_release": {"enabled": False},
        "ichimoku_cloud": {"enabled": True},
    }

    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"] = {"enabled": False}

    return cfg


# ---------------------------------------------------------------------------
# Data file resolution
# ---------------------------------------------------------------------------

def resolve_data_paths(
    prefix: str, strategy: str
) -> tuple[Optional[str], Optional[str]]:
    """Return (signal_path, trend_path) for a winner's data files.

    EMA 15m  -> signal=*_15m_*.csv, trend=*_1h_*.csv
    Ichimoku -> signal=*_1h_*.csv,  trend=*_1h_*.csv  (same file)
    """
    if strategy.startswith("ema"):
        signal_tf, trend_tf = "15m", "1h"
    else:
        signal_tf, trend_tf = "1h", "1h"

    signal_candidates = sorted(DATA_DIR.glob(f"{prefix}_{signal_tf}*.csv"))
    trend_candidates = sorted(DATA_DIR.glob(f"{prefix}_{trend_tf}*.csv"))

    signal_path = str(signal_candidates[0]) if signal_candidates else None
    trend_path = str(trend_candidates[0]) if trend_candidates else None

    return signal_path, trend_path


def funding_file_path(prefix: str) -> Optional[str]:
    """Return funding rate CSV path if it exists, else None."""
    path = DATA_DIR / f"{prefix}_funding_rate.csv"
    return str(path) if path.exists() else None


# ---------------------------------------------------------------------------
# Single backtest runner
# ---------------------------------------------------------------------------

def run_engine(config: dict, signal_path: str, trend_path: str) -> Optional[dict]:
    """Run BacktestEngine and return a dict of key metrics, or None on error."""
    try:
        signal_data = load_ohlcv(signal_path)
        trend_data = load_ohlcv(trend_path)
        engine = BacktestEngine(config)
        metrics = engine.run(signal_data, trend_data)

        trades = metrics.total_trades
        pf = metrics.profit_factor
        wr = round(metrics.win_rate * 100, 1)
        dd = round(metrics.max_drawdown * 100, 1)
        sharpe = round(metrics.sharpe_ratio, 2)

        return {
            "trades": trades,
            "pf": round(pf, 2),
            "wr": wr,
            "dd": dd,
            "sharpe": sharpe,
        }
    except Exception as exc:  # noqa: BLE001
        print(f"    ERROR: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not SWEEP_WINNERS_PATH.exists():
        print(f"ERROR: {SWEEP_WINNERS_PATH} not found.")
        print("Run the lightweight sweep first to generate sweep_winners.json.")
        sys.exit(1)

    with open(SWEEP_WINNERS_PATH) as f:
        winners: list[dict] = json.load(f)

    if not winners:
        print("sweep_winners.json is empty — nothing to verify.")
        sys.exit(0)

    print(f"Loaded {len(winners)} winners from {SWEEP_WINNERS_PATH}")
    print("Running full BacktestEngine verification...\n")

    verified: list[dict] = []
    passed_count = 0
    failed_count = 0

    # Header
    col_w = [8, 14, 10, 10, 7, 6, 6, 12, 8]
    headers = ["Coin", "Strategy", "Sweep_PF", "Engine_PF", "Trades", "WR%", "DD%",
               "Funding_PF", "Status"]
    header_line = "  ".join(h.ljust(col_w[i]) for i, h in enumerate(headers))
    separator = "-" * len(header_line)

    print("VERIFICATION RESULTS")
    print(separator)
    print(header_line)
    print(separator)

    for winner in winners:
        coin: str = winner.get("coin", winner.get("prefix", "?").replace("usdt", "").upper())
        prefix: str = winner.get("prefix", coin.lower() + "usdt")
        strategy: str = winner.get("strategy", "ema_15m")
        sweep_pf: float = winner.get("pf", winner.get("sweep_pf", 0.0))

        print(f"  [{coin}] Building config for {strategy}...", end=" ", flush=True)

        # Resolve data files
        signal_path, trend_path = resolve_data_paths(prefix, strategy)
        if not signal_path or not trend_path:
            print(f"SKIP — data files missing ({prefix}_{strategy})")
            continue

        # Build config
        if strategy.startswith("ema"):
            cfg = build_ema_config(winner)
        else:
            cfg = build_ichimoku_config(winner)

        # Baseline engine run
        print("running engine...", end=" ", flush=True)
        result = run_engine(cfg, signal_path, trend_path)
        if result is None:
            print("SKIP — engine error")
            continue

        engine_pf = result["pf"]
        engine_trades = result["trades"]
        engine_wr = result["wr"]
        engine_dd = result["dd"]
        engine_sharpe = result["sharpe"]

        print(f"PF={engine_pf}", end=" ", flush=True)

        # Funding scorer run
        funding_pf: Optional[float] = None
        funding_helps: bool = False
        fpath = funding_file_path(prefix)
        if fpath and strategy.startswith("ema"):
            print("+ funding...", end=" ", flush=True)
            cfg_funding = copy.deepcopy(cfg)
            cfg_funding["signal_scorer"] = copy.deepcopy(_FUNDING_SCORER_CONFIG)
            result_f = run_engine(cfg_funding, signal_path, trend_path)
            if result_f is not None:
                funding_pf = result_f["pf"]
                funding_helps = funding_pf > engine_pf
                print(f"funding_PF={funding_pf}", end=" ", flush=True)

        # Pass/fail
        passed = engine_pf >= MIN_ENGINE_PF and engine_trades >= MIN_ENGINE_TRADES
        status = "PASS" if passed else "FAIL"
        if passed:
            passed_count += 1
        else:
            failed_count += 1

        print()  # newline after inline progress

        # Formatted table row
        funding_str = f"{funding_pf:.2f}" if funding_pf is not None else "-"
        row_values = [
            coin, strategy, f"{sweep_pf:.2f}", f"{engine_pf:.2f}",
            str(engine_trades), f"{engine_wr:.0f}%", f"{engine_dd:.1f}%",
            funding_str, status,
        ]
        row_line = "  ".join(v.ljust(col_w[i]) for i, v in enumerate(row_values))
        print(f"  {row_line}")

        # Collect result record
        params_out = dict(winner.get("params", {}))
        params_out.setdefault("atr_sl_mult", cfg.get("atr_sl_mult"))
        params_out.setdefault("atr_tp_mult", cfg.get("atr_tp_mult"))
        params_out.setdefault("atr_trail_mult", cfg.get("atr_trail_mult"))

        record: dict = {
            "coin": coin,
            "prefix": prefix,
            "strategy": strategy,
            "sweep_pf": round(float(sweep_pf), 3),
            "engine_pf": engine_pf,
            "engine_trades": engine_trades,
            "engine_wr": engine_wr,
            "engine_dd": engine_dd,
            "engine_sharpe": engine_sharpe,
            "funding_pf": round(float(funding_pf), 3) if funding_pf is not None else None,
            "funding_helps": funding_helps,
            "passed": passed,
            "params": params_out,
        }
        verified.append(record)

    print(separator)
    print(f"\nPASSED: {passed_count} coins")
    print(f"FAILED: {failed_count} coins (PF < {MIN_ENGINE_PF} or trades < {MIN_ENGINE_TRADES})")

    # Save results — convert numpy types to Python native for JSON
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

    with open(VERIFIED_RESULTS_PATH, "w") as f:
        json.dump(verified, f, indent=2, cls=NumpyEncoder)

    print(f"\nSaved {len(verified)} results to {VERIFIED_RESULTS_PATH}")

    # Summary of passing coins
    passing = [r for r in verified if r["passed"]]
    if passing:
        print("\nPassing coins:")
        for r in sorted(passing, key=lambda x: x["engine_pf"], reverse=True):
            funding_note = ""
            if r["funding_pf"] is not None:
                arrow = "+" if r["funding_helps"] else "-"
                funding_note = f"  [funding {arrow}{abs(r['funding_pf'] - r['engine_pf']):.2f}]"
            print(
                f"  {r['coin']:10s} {r['strategy']:14s}  "
                f"PF={r['engine_pf']:.2f}  WR={r['engine_wr']:.0f}%  "
                f"DD={r['engine_dd']:.1f}%  Sharpe={r['engine_sharpe']:.2f}"
                f"{funding_note}"
            )


if __name__ == "__main__":
    main()
