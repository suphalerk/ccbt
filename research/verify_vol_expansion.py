"""verify_vol_expansion.py — Full BacktestEngine verification for vol_expansion sweep winners.

Verifies 4 coins identified in the vol_expansion sweep:
  STO, 1000PEPE, WLD, SAND

Both modes per coin:
  1. Fixed TP:    atr_threshold=1.8, lookback=1, SL=2.5, TP=3.0
  2. Trail-only:  atr_threshold=1.8, lookback=1, SL=2.5, trail=3.0 (TP=0)

Accept criterion: engine PF >= 1.3, trades >= 6/yr.

Usage:
    python research/verify_vol_expansion.py
"""

from __future__ import annotations

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

# Suppress noisy backtest logs
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"

# ---------------------------------------------------------------------------
# Accept thresholds
# ---------------------------------------------------------------------------
MIN_PF = 1.3
MIN_TRADES_PER_YEAR = 6

# ---------------------------------------------------------------------------
# Base config template — vol_expansion 1H, based on Ichimoku template structure
# ---------------------------------------------------------------------------
BASE_CONFIG: dict = {
    "exchange": "binance",
    "symbol": "STOUSDT",
    "timeframe_signal": "1h",
    "timeframe_trend": "1h",
    "leverage": 25,
    "risk_per_trade": 0.01,
    "max_daily_loss": 0.3,
    "max_positions": 2,
    "max_consecutive_losses": 5,
    "cooldown_hours": 1,
    "max_api_errors": 3,
    "use_testnet": True,
    "ema_fast": 9,
    "ema_slow": 21,
    "ema_trend": 50,
    "rsi_period": 14,
    "rsi_min": 45,
    "rsi_max": 65,
    "atr_period": 14,
    "atr_min": 0.0,
    "partial_tp_enabled": False,
    "partial_tp_pct": 0.3,
    "partial_tp_atr_mult": 2.0,
    "move_sl_to_be_after_tp1": False,
    "breakeven_buffer_atr_mult": 0.5,
    "atr_trail_mult_post_tp1": 4.0,
    "volume_mult": 1.0,
    "volume_max_mult": None,
    "rsi_long_min": 45,
    "rsi_long_max": 65,
    "rsi_short_min": 35,
    "rsi_short_max": 55,
    "cooldown_candles_after_close": 0,
    "cooldown_candles_after_sl": 0,
    "min_rr_ratio": 0,
    "commission_rate": 0.0004,
    "slippage_rate": 0.00015,
    "crossover_lookback": 2,
    "weekend_trading_enabled": False,
    "weekend_size_reduction": 0.5,
    "ema_slope_period": 5,
    "ema_slope_min": 0.02,
    "trading_hours": {
        "enabled": True,
        "start_utc": 3,
        "end_utc": 20,
    },
    "regime_filter": {
        "enabled": True,
        "skip_ranging": True,
    },
    "flexible_cooldown": {
        "enabled": False,
        "min_quality_score": 0.7,
        "cooldown_reduction_factor": 0.5,
        "log_overrides": True,
    },
    "signals": {
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
        "vol_expansion": {"enabled": True},
    },
    # Vol expansion parameters
    "vol_expansion_threshold": 1.8,
    "vol_expansion_lookback": 1,
    "vol_expansion_atr_ma_period": 20,
    "vol_expansion_ema_trend_period": 50,
    # SL/TP/Trail (overridden per mode below)
    "atr_sl_mult": 2.5,
    "atr_tp_mult": 3.0,
    "atr_trail_mult": 3.0,
    "atr_trail_mult_trending": 3.0,
    "atr_trail_mult_ranging": 2.0,
    "atr_trail_mult_volatile": 4.0,
    # Disabled features
    "adaptive_sizing": {"enabled": False},
    "pyramiding": {"enabled": False},
    "mtd_accelerator": {"enabled": False},
    "signal_scorer": {"enabled": False},
    "ai_layer": {"enabled": False},
    # Misc required by engine
    "body_dominance_min_body": 0.65,
    "body_dominance_min_mom": 0.02,
    "body_dominance_min_vol": 1.5,
    "squeeze_release_low": 0.7,
    "squeeze_release_high": 0.8,
    "squeeze_release_min_mom4": 0.0,
    "mr_rsi_oversold": 30,
    "mr_rsi_overbought": 70,
    "mr_atr_sl_mult": 1.0,
    "mr_atr_tp_mult": 1.5,
    "ema_fast2": 5,
    "ema_slow2": 13,
    "bb_period": 20,
    "bb_std": 2.0,
    "bb_squeeze_percentile": 0.3,
    "bb_volume_mult": 1.2,
    "swing_lookback": 5,
    "divergence_lookback": 20,
}

# ---------------------------------------------------------------------------
# Coins to verify
# ---------------------------------------------------------------------------
COINS = [
    {"name": "STO",       "prefix": "stousdt"},
    {"name": "1000PEPE",  "prefix": "1000pepeusdt"},
    {"name": "WLD",       "prefix": "wldusdt"},
    {"name": "SAND",      "prefix": "sandusdt"},
]

# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------

def build_config_fixed_tp(coin: dict) -> dict:
    """Build config for fixed TP mode: SL=2.5, TP=3.0."""
    cfg = copy.deepcopy(BASE_CONFIG)
    symbol = coin["prefix"].upper().replace("USDT", "USDT")
    cfg["symbol"] = symbol
    cfg["atr_sl_mult"] = 2.5
    cfg["atr_tp_mult"] = 3.0
    cfg["atr_trail_mult"] = 3.0
    cfg["atr_trail_mult_trending"] = 3.0
    return cfg


def build_config_trail_only(coin: dict) -> dict:
    """Build config for trail-only mode: SL=2.5, TP=0, trail=3.0."""
    cfg = copy.deepcopy(BASE_CONFIG)
    symbol = coin["prefix"].upper().replace("USDT", "USDT")
    cfg["symbol"] = symbol
    cfg["atr_sl_mult"] = 2.5
    cfg["atr_tp_mult"] = 0      # 0 = trail only; engine maps this to TP=100x ATR
    cfg["atr_trail_mult"] = 3.0
    cfg["atr_trail_mult_trending"] = 3.0
    return cfg


# ---------------------------------------------------------------------------
# Data loader helper
# ---------------------------------------------------------------------------

def load_coin_data(prefix: str) -> Optional[object]:
    """Load 1H OHLCV data for a coin prefix.

    Args:
        prefix: Lowercase coin prefix, e.g. 'stousdt'.

    Returns:
        Loaded DataFrame or None if no data file is found.
    """
    for period in ["2y", "5y"]:
        fpath = DATA_DIR / f"{prefix}_1h_{period}.csv"
        if fpath.exists():
            return load_ohlcv(str(fpath))
    return None


# ---------------------------------------------------------------------------
# Engine runner
# ---------------------------------------------------------------------------

def run_engine(config: dict, signal_data: object, trend_data: object) -> Optional[dict]:
    """Run BacktestEngine and return key metrics.

    Args:
        config: Bot configuration dict.
        signal_data: Signal timeframe OHLCV DataFrame.
        trend_data: Trend timeframe OHLCV DataFrame (same as signal for 1H).

    Returns:
        Dict with pf, trades, wr, dd, sharpe, years — or None on error.
    """
    try:
        import pandas as pd
        engine = BacktestEngine(config)
        metrics = engine.run(signal_data, trend_data)

        # Calculate years covered
        years = (signal_data.index[-1] - signal_data.index[0]).days / 365.25

        return {
            "pf": round(metrics.profit_factor, 2),
            "trades": metrics.total_trades,
            "trades_per_yr": round(metrics.total_trades / years, 1) if years > 0 else 0.0,
            "wr": round(metrics.win_rate * 100, 1),
            "dd": round(metrics.max_drawdown * 100, 1),
            "sharpe": round(metrics.sharpe_ratio, 2),
            "years": round(years, 1),
        }
    except Exception as exc:
        print(f"      ERROR: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Vol Expansion Breakout — Full BacktestEngine Verification")
    print("=" * 65)
    print(f"Signal: 1H  |  atr_threshold=1.8  |  lookback=1")
    print(f"Accept: PF >= {MIN_PF}, trades >= {MIN_TRADES_PER_YEAR}/yr")
    print()

    results: list[dict] = []

    for coin in COINS:
        name = coin["name"]
        prefix = coin["prefix"]

        print(f"[{name}] Loading 1H data...", end=" ", flush=True)
        df = load_coin_data(prefix)
        if df is None:
            print(f"SKIP — no data file found for {prefix}")
            continue
        print(f"{len(df)} candles ({(df.index[-1] - df.index[0]).days / 365.25:.1f} yr)")

        # --- Fixed TP mode ---
        print(f"  Fixed TP (SL=2.5, TP=3.0)...    ", end=" ", flush=True)
        cfg_fixed = build_config_fixed_tp(coin)
        r_fixed = run_engine(cfg_fixed, df, df)
        if r_fixed:
            passed = r_fixed["pf"] >= MIN_PF and r_fixed["trades_per_yr"] >= MIN_TRADES_PER_YEAR
            status = "PASS" if passed else "FAIL"
            print(
                f"PF={r_fixed['pf']:.2f}  trades={r_fixed['trades']} "
                f"({r_fixed['trades_per_yr']:.0f}/yr)  WR={r_fixed['wr']:.0f}%  "
                f"DD={r_fixed['dd']:.1f}%  Sharpe={r_fixed['sharpe']:.2f}  [{status}]"
            )
            results.append({
                "coin": name, "mode": "fixed_tp",
                "sl": 2.5, "tp": 3.0, "trail": 0,
                **r_fixed, "passed": passed,
            })
        else:
            print("ENGINE ERROR")
            results.append({"coin": name, "mode": "fixed_tp", "passed": False, "error": True})

        # --- Trail-only mode ---
        print(f"  Trail-only (SL=2.5, trail=3.0)...", end=" ", flush=True)
        cfg_trail = build_config_trail_only(coin)
        r_trail = run_engine(cfg_trail, df, df)
        if r_trail:
            passed = r_trail["pf"] >= MIN_PF and r_trail["trades_per_yr"] >= MIN_TRADES_PER_YEAR
            status = "PASS" if passed else "FAIL"
            print(
                f"PF={r_trail['pf']:.2f}  trades={r_trail['trades']} "
                f"({r_trail['trades_per_yr']:.0f}/yr)  WR={r_trail['wr']:.0f}%  "
                f"DD={r_trail['dd']:.1f}%  Sharpe={r_trail['sharpe']:.2f}  [{status}]"
            )
            results.append({
                "coin": name, "mode": "trail_only",
                "sl": 2.5, "tp": 0, "trail": 3.0,
                **r_trail, "passed": passed,
            })
        else:
            print("ENGINE ERROR")
            results.append({"coin": name, "mode": "trail_only", "passed": False, "error": True})

        print()

    # ---------------------------------------------------------------------------
    # Summary table
    # ---------------------------------------------------------------------------
    print()
    print("=" * 80)
    print("RESULTS SUMMARY")
    print("=" * 80)

    col_widths = [10, 10, 6, 6, 7, 7, 6, 7, 7, 8]
    headers = ["Coin", "Mode", "PF", "Trades", "Tr/yr", "WR%", "DD%", "Sharpe", "Years", "Status"]
    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep = "-" * len(header_line)
    print(header_line)
    print(sep)

    passed_count = 0
    failed_count = 0

    for r in results:
        if r.get("error"):
            row_vals = [r["coin"], r["mode"], "ERR", "-", "-", "-", "-", "-", "-", "ERROR"]
        else:
            row_vals = [
                r["coin"],
                r["mode"],
                f"{r.get('pf', 0):.2f}",
                str(r.get("trades", 0)),
                f"{r.get('trades_per_yr', 0):.0f}",
                f"{r.get('wr', 0):.0f}%",
                f"{r.get('dd', 0):.1f}%",
                f"{r.get('sharpe', 0):.2f}",
                f"{r.get('years', 0):.1f}",
                "PASS" if r.get("passed") else "FAIL",
            ]
        print("  ".join(v.ljust(col_widths[i]) for i, v in enumerate(row_vals)))
        if r.get("passed"):
            passed_count += 1
        else:
            failed_count += 1

    print(sep)
    print(f"\nPASSED: {passed_count}  FAILED: {failed_count}")
    print(f"Accept criterion: PF >= {MIN_PF}  AND  trades/yr >= {MIN_TRADES_PER_YEAR}")

    # Highlight passing coins with best mode
    passing = [r for r in results if r.get("passed") and not r.get("error")]
    if passing:
        print("\nVerified winners (PF desc):")
        for r in sorted(passing, key=lambda x: -x["pf"]):
            print(
                f"  {r['coin']:<12} {r['mode']:<12} "
                f"PF={r['pf']:.2f}  trades={r['trades']} ({r['trades_per_yr']:.0f}/yr)  "
                f"WR={r['wr']:.0f}%  DD={r['dd']:.1f}%  Sharpe={r['sharpe']:.2f}"
            )
    else:
        print("\nNo coins passed full engine verification.")


if __name__ == "__main__":
    main()
