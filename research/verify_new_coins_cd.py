"""verify_new_coins_cd.py — Full BacktestEngine verification for Tier C + Tier D candidates.

Tier C (PF 1.5-2.0, trades >= 10): 20 coins
Tier D* (PF >= 2.0, trades < 8 — risky): 14 coins

All strategies: ichimoku, ema, dual_supertrend, alligator, ema_ichimoku_hybrid,
                ichi_supertrend, volexp_supertrend

4H strategies resample 1H data to 4H before running the engine.

PASS criteria: engine_pf >= 1.2 AND trades >= 4.
Results saved to data/verified_new_cd.json.

Usage:
    python research/verify_new_coins_cd.py
"""

from __future__ import annotations

import copy
import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"

# ---------------------------------------------------------------------------
# Accept thresholds
# ---------------------------------------------------------------------------
PASS_PF = 1.2
PASS_TRADES = 4

# ---------------------------------------------------------------------------
# Base config — built from config_avax_ichi.json structure
# ---------------------------------------------------------------------------
BASE_CONFIG: dict = {
    "exchange": "binance",
    "symbol": "AVAXUSDT",
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
    "atr_sl_mult": 2.0,
    "atr_tp_mult": 4.0,
    "atr_trail_mult": 3.0,
    "atr_trail_mult_trending": 3.0,
    "atr_trail_mult_ranging": 2.0,
    "atr_trail_mult_volatile": 4.0,
    "atr_min": 0.0,
    "partial_tp_enabled": False,
    "partial_tp_pct": 0.3,
    "partial_tp_atr_mult": 2.0,
    "move_sl_to_be_after_tp1": True,
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
    "ema_slope_min": 0.0,
    "ichimoku_tenkan": 9,
    "ichimoku_kijun": 26,
    "ichimoku_senkou_b": 52,
    "trading_hours": {"enabled": False, "start_utc": 0, "end_utc": 24},
    "regime_filter": {"enabled": True, "skip_ranging": True},
    "flexible_cooldown": {"enabled": False},
    # Supertrend params (used by supertrend + ichi_supertrend + volexp_supertrend)
    "supertrend_multiplier": 2.0,
    # Dual supertrend params
    "dual_supertrend_fast_period": 7,
    "dual_supertrend_fast_mult": 2.0,
    "dual_supertrend_slow_period": 14,
    "dual_supertrend_slow_mult": 3.0,
    # Vol expansion params (used by volexp_supertrend)
    "vol_expansion_threshold": 1.8,
    "vol_expansion_lookback": 1,
    "vol_expansion_atr_ma_period": 20,
    "vol_expansion_ema_trend_period": 50,
    # All signals disabled by default — build_config enables only the target signal
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
        "vol_expansion": {"enabled": False},
        "dual_supertrend": {"enabled": False},
        "alligator": {"enabled": False},
        "ema_ichimoku_hybrid": {"enabled": False},
        "ichi_supertrend": {"enabled": False},
        "volexp_supertrend": {"enabled": False},
    },
    # Misc fields required by engine
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
    "adaptive_sizing": {"enabled": False},
    "pyramiding": {"enabled": False},
    "mtd_accelerator": {"enabled": False},
    "signal_scorer": {"enabled": False},
    "ai_layer": {"enabled": False},
}

# ---------------------------------------------------------------------------
# Candidate definitions
# ---------------------------------------------------------------------------

# tier: "C" or "D"
# strategy: one of ichimoku, ema, dual_supertrend, alligator,
#            ema_ichimoku_hybrid, ichi_supertrend, volexp_supertrend
# is_4h: True → resample 1H data to 4H
# sl, tp: ATR multiples
# symbol_override: for coins with special file/symbol names
CANDIDATES: list[dict] = [
    # ---- Tier C ----
    {"coin": "W",          "tier": "C", "strategy": "ichi_supertrend",    "is_4h": True,  "sl": 2.0, "tp": 5.0},
    {"coin": "ZEN",        "tier": "C", "strategy": "volexp_supertrend",  "is_4h": False, "sl": 2.5, "tp": 3.0},
    {"coin": "HUMA",       "tier": "C", "strategy": "dual_supertrend",    "is_4h": False, "sl": 2.5, "tp": 4.0},
    {"coin": "COLLECT",    "tier": "C", "strategy": "ichimoku",           "is_4h": False, "sl": 2.0, "tp": 4.0},
    {"coin": "ICP",        "tier": "C", "strategy": "alligator",          "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "SIGN",       "tier": "C", "strategy": "ichimoku",           "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "ENA",        "tier": "C", "strategy": "alligator",          "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "VIRTUAL",    "tier": "C", "strategy": "ichimoku",           "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "CFX",        "tier": "C", "strategy": "dual_supertrend",    "is_4h": True,  "sl": 2.5, "tp": 4.0},
    {"coin": "TON",        "tier": "C", "strategy": "ichimoku",           "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "LTC",        "tier": "C", "strategy": "ichi_supertrend",    "is_4h": True,  "sl": 2.0, "tp": 5.0},
    {"coin": "COS",        "tier": "C", "strategy": "ichi_supertrend",    "is_4h": True,  "sl": 2.0, "tp": 5.0},
    {"coin": "WLFI",       "tier": "C", "strategy": "ichimoku",           "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "GALA",       "tier": "C", "strategy": "alligator",          "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "NAORIS",     "tier": "C", "strategy": "ichimoku",           "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "FARTCOIN",   "tier": "C", "strategy": "dual_supertrend",    "is_4h": True,  "sl": 2.5, "tp": 4.0},
    {"coin": "BARD",       "tier": "C", "strategy": "ichimoku",           "is_4h": False, "sl": 2.0, "tp": 4.0},
    {"coin": "UNI",        "tier": "C", "strategy": "volexp_supertrend",  "is_4h": False, "sl": 2.5, "tp": 3.0},
    {"coin": "ONDO",       "tier": "C", "strategy": "ichimoku",           "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {
        "coin": "XAUUSD",
        "tier": "C",
        "strategy": "ichimoku",
        "is_4h": True,
        "sl": 2.5,
        "tp": 4.0,
        "file_prefix": "xauusd",        # no "usdt" suffix in filename
        "symbol_override": "XAUUSD",    # symbol for engine config
    },
    # ---- Tier D ----
    {
        "coin": "币安人生",
        "tier": "D",
        "strategy": "ichimoku",
        "is_4h": True,
        "sl": 2.0,
        "tp": 4.0,
        "file_prefix": "币安人生usdt",
        "symbol_override": "币安人生USDT",
    },
    {"coin": "PLAY",       "tier": "D", "strategy": "ichi_supertrend",    "is_4h": True,  "sl": 2.0, "tp": 5.0},
    {"coin": "DEGO",       "tier": "D", "strategy": "ema_ichimoku_hybrid","is_4h": True,  "sl": 1.5, "tp": 4.0},
    {"coin": "XPL",        "tier": "D", "strategy": "dual_supertrend",    "is_4h": True,  "sl": 2.5, "tp": 4.0},
    {"coin": "PUMP",       "tier": "D", "strategy": "ema",                "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "RIVER",      "tier": "D", "strategy": "dual_supertrend",    "is_4h": True,  "sl": 2.5, "tp": 4.0},
    {"coin": "SIREN",      "tier": "D", "strategy": "alligator",          "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "OP",         "tier": "D", "strategy": "volexp_supertrend",  "is_4h": False, "sl": 2.5, "tp": 4.0},
    {"coin": "AIA",        "tier": "D", "strategy": "ichimoku",           "is_4h": True,  "sl": 2.0, "tp": 4.0},
    {"coin": "1000BONK",   "tier": "D", "strategy": "volexp_supertrend",  "is_4h": False, "sl": 2.5, "tp": 3.0},
    {
        "coin": "龙虾",
        "tier": "D",
        "strategy": "ichimoku",
        "is_4h": False,
        "sl": 2.0,
        "tp": 4.0,
        "file_prefix": "龙虾usdt",
        "symbol_override": "龙虾USDT",
    },
    {"coin": "XPT",        "tier": "D", "strategy": "ema_ichimoku_hybrid","is_4h": False, "sl": 1.5, "tp": 4.0},
    {"coin": "HOOD",       "tier": "D", "strategy": "ichimoku",           "is_4h": False, "sl": 2.0, "tp": 4.0},
    {"coin": "KAT",        "tier": "D", "strategy": "ichimoku",           "is_4h": False, "sl": 2.0, "tp": 4.0},
]


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def build_config(cand: dict) -> dict:
    """Build a BacktestEngine config for the candidate.

    Args:
        cand: Candidate dict with coin, strategy, is_4h, sl, tp (and optional overrides).

    Returns:
        Config dict with only the target signal enabled.
    """
    cfg = copy.deepcopy(BASE_CONFIG)

    # Symbol
    symbol_override = cand.get("symbol_override")
    if symbol_override:
        cfg["symbol"] = symbol_override
    else:
        cfg["symbol"] = f"{cand['coin']}USDT"

    cfg["atr_sl_mult"] = cand["sl"]
    cfg["atr_tp_mult"] = cand["tp"]
    # Trail = sl mult (no fixed TP trail, use SL as fallback trail)
    cfg["atr_trail_mult"] = cand["sl"]
    cfg["atr_trail_mult_trending"] = cand["sl"]

    strategy = cand["strategy"]

    if strategy == "ichimoku":
        cfg["signals"]["ichimoku_cloud"] = {"enabled": True}
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52

    elif strategy == "ema":
        cfg["signals"]["ema_crossover"] = {"enabled": True}
        cfg["signals"]["ema_fast_crossover"] = {"enabled": True}

    elif strategy == "dual_supertrend":
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

    else:
        raise ValueError(f"Unknown strategy: {strategy!r}")

    return cfg


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

def load_coin_data(cand: dict) -> Optional[pd.DataFrame]:
    """Load 1H OHLCV data for the candidate.

    Args:
        cand: Candidate dict. Uses file_prefix override if present, otherwise
              derives prefix from coin name as {coin.lower()}usdt.

    Returns:
        Loaded DataFrame or None if no data file found.
    """
    file_prefix = cand.get("file_prefix")
    if not file_prefix:
        file_prefix = f"{cand['coin'].lower()}usdt"

    for period in ["2y", "5y"]:
        fpath = DATA_DIR / f"{file_prefix}_1h_{period}.csv"
        if fpath.exists():
            return load_ohlcv(str(fpath))
    return None


def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV DataFrame to 4H bars.

    Args:
        df_1h: 1H OHLCV data with DatetimeIndex.

    Returns:
        4H resampled DataFrame with NaN rows dropped.
    """
    return df_1h.resample("4h").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna()


# ---------------------------------------------------------------------------
# Engine runner
# ---------------------------------------------------------------------------

def run_engine(config: dict, signal_data: pd.DataFrame) -> Optional[dict]:
    """Run BacktestEngine and return key metrics.

    Args:
        config: Bot config dict with target signal enabled.
        signal_data: Signal-timeframe OHLCV DataFrame (already resampled if 4H).

    Returns:
        Dict with pf, trades, trades_per_yr, wr, dd, sharpe, years — or None on error.
    """
    try:
        engine = BacktestEngine(config, initial_balance=10000.0)

        # Suppress engine stdout (print_metrics)
        real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            metrics = engine.run(signal_data=signal_data, trend_data=None)
        finally:
            sys.stdout = real_stdout

        years = (signal_data.index[-1] - signal_data.index[0]).days / 365.25
        trades_per_yr = round(metrics.total_trades / years, 1) if years > 0 else 0.0

        return {
            "pf": round(metrics.profit_factor, 3),
            "trades": metrics.total_trades,
            "trades_per_yr": trades_per_yr,
            "wr": round(metrics.win_rate * 100, 1) if metrics.win_rate is not None else 0.0,
            "dd": round(metrics.max_drawdown * 100, 1) if metrics.max_drawdown is not None else 0.0,
            "sharpe": round(metrics.sharpe_ratio, 2) if metrics.sharpe_ratio is not None else 0.0,
            "years": round(years, 1),
            "bars": len(signal_data),
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Tier C + D Verification — Full BacktestEngine")
    print("=" * 75)
    print(f"PASS: engine_pf >= {PASS_PF}  AND  trades >= {PASS_TRADES}")
    print()

    col_fmt = (
        f"{'Coin':<12}  {'Tier':<4}  {'Strategy':<20}  {'TF':<4}  "
        f"{'PF':>6}  {'Tr':>4}  {'Tr/yr':>5}  "
        f"{'WR%':>5}  {'DD%':>5}  {'Sh':>5}  {'Status'}"
    )
    sep = "-" * len(col_fmt)
    print(col_fmt)
    print(sep)

    results: list[dict] = []

    for cand in CANDIDATES:
        coin = cand["coin"]
        tier = cand["tier"]
        strategy = cand["strategy"]
        is_4h = cand["is_4h"]
        tf_label = "4H" if is_4h else "1H"

        # Load data
        df_1h = load_coin_data(cand)
        if df_1h is None:
            row = {
                "coin": coin, "tier": tier, "strategy": strategy, "is_4h": is_4h,
                "sl": cand["sl"], "tp": cand["tp"],
                "status": "NO_DATA", "note": "no 1H data file",
            }
            results.append(row)
            print(
                f"{coin:<12}  {tier:<4}  {strategy:<20}  {tf_label:<4}  "
                f"{'N/A':>6}  {'N/A':>4}  {'N/A':>5}  "
                f"{'N/A':>5}  {'N/A':>5}  {'N/A':>5}  NO_DATA"
            )
            continue

        # Resample if 4H strategy
        signal_data = resample_to_4h(df_1h) if is_4h else df_1h

        # Build config and run engine
        config = build_config(cand)
        metrics = run_engine(config, signal_data)

        if metrics is None or "error" in metrics:
            err_msg = metrics.get("error", "unknown") if metrics else "None returned"
            row = {
                "coin": coin, "tier": tier, "strategy": strategy, "is_4h": is_4h,
                "sl": cand["sl"], "tp": cand["tp"],
                "status": "ERROR", "note": err_msg,
            }
            results.append(row)
            print(
                f"{coin:<12}  {tier:<4}  {strategy:<20}  {tf_label:<4}  "
                f"{'ERR':>6}  {'ERR':>4}  {'ERR':>5}  "
                f"{'ERR':>5}  {'ERR':>5}  {'ERR':>5}  ERROR: {err_msg[:40]}"
            )
            continue

        passed = metrics["pf"] >= PASS_PF and metrics["trades"] >= PASS_TRADES
        status = "PASS" if passed else "FAIL"

        row = {
            "coin": coin,
            "tier": tier,
            "strategy": strategy,
            "is_4h": is_4h,
            "sl": cand["sl"],
            "tp": cand["tp"],
            "engine_pf": metrics["pf"],
            "trades": metrics["trades"],
            "trades_per_yr": metrics["trades_per_yr"],
            "wr_pct": metrics["wr"],
            "dd_pct": metrics["dd"],
            "sharpe": metrics["sharpe"],
            "years": metrics["years"],
            "bars": metrics["bars"],
            "status": status,
            "note": f"{metrics['years']:.1f}yr  {metrics['bars']}bars",
        }
        results.append(row)

        print(
            f"{coin:<12}  {tier:<4}  {strategy:<20}  {tf_label:<4}  "
            f"{metrics['pf']:>6.3f}  {metrics['trades']:>4}  {metrics['trades_per_yr']:>5.1f}  "
            f"{metrics['wr']:>5.1f}  {metrics['dd']:>5.1f}  {metrics['sharpe']:>5.2f}  {status}"
        )

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    print(sep)
    passed_results = [r for r in results if r.get("status") == "PASS"]
    failed_results = [r for r in results if r.get("status") == "FAIL"]
    error_results  = [r for r in results if r.get("status") in ("ERROR", "NO_DATA")]

    print(f"\nPASS: {len(passed_results)}  FAIL: {len(failed_results)}  ERROR/NO_DATA: {len(error_results)}")
    print(f"Total: {len(results)} candidates")
    print()

    if passed_results:
        print("Verified winners (sorted by PF desc):")
        for r in sorted(passed_results, key=lambda x: -x.get("engine_pf", 0)):
            print(
                f"  [{r['tier']}] {r['coin']:<12}  {r['strategy']:<20}  "
                f"{'4H' if r['is_4h'] else '1H'}  "
                f"PF={r['engine_pf']:.3f}  trades={r['trades']} ({r['trades_per_yr']:.1f}/yr)  "
                f"WR={r['wr_pct']:.0f}%  DD={r['dd_pct']:.1f}%  Sharpe={r['sharpe']:.2f}"
            )
    else:
        print("No candidates passed full engine verification.")

    if error_results:
        print("\nErrors / missing data:")
        for r in error_results:
            print(f"  {r['coin']:<12}  {r['status']}  {r.get('note', '')}")

    # ---------------------------------------------------------------------------
    # Save results
    # ---------------------------------------------------------------------------
    out_path = DATA_DIR / "verified_new_cd.json"
    with open(str(out_path), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
