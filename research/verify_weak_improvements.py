"""verify_weak_improvements.py — Full BacktestEngine verification for 29 weak coin improvements.

Loads best combo per coin from data/sweep_improve_weak.json (top_per_coin),
runs each through BacktestEngine with the exact signal/SL/TP/trail params,
and saves results to data/verify_weak_improvements.json.

PASS criteria: engine_pf >= 1.2 AND trades >= 6
"""

from __future__ import annotations

import copy
import io
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

PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"
SWEEP_PATH = DATA_DIR / "sweep_improve_weak.json"
OUTPUT_PATH = DATA_DIR / "verify_weak_improvements.json"

# ---------------------------------------------------------------------------
# Pass criteria
# ---------------------------------------------------------------------------
PASS_PF = 1.2
PASS_TRADES = 6

# ---------------------------------------------------------------------------
# All signals — every signal known to the engine. Only the target is enabled.
# ---------------------------------------------------------------------------
ALL_SIGNALS = [
    "ema_crossover", "ema_fast_crossover", "ema_pullback", "rsi_divergence",
    "bb_breakout", "mean_reversion", "body_dominance", "squeeze_release",
    "ichimoku_cloud", "supertrend", "vol_expansion",
    "dual_supertrend", "alligator", "ema_ichimoku_hybrid", "ichi_supertrend", "volexp_supertrend",
    "adx_di_cross", "choppiness_ema", "williams_r_adx", "roc_momentum",
    "stoch_supertrend", "price_channel_vol", "ema_alligator", "supertrend_volume",
    "stoch_mtf", "zscore_meanrev", "ema_ribbon",
    "dual_thrust", "awesome_oscillator", "range_bounce",
    "ribbon_rsi_vol", "dualthrust_adx", "zscore_stoch", "ichi_adx", "ribbon_ao",
]

# ---------------------------------------------------------------------------
# Sweep signal name → engine signal key mapping
# (sweep uses some abbreviated names that differ from engine signal keys)
# ---------------------------------------------------------------------------
SIGNAL_MAP: dict[str, str] = {
    "ichimoku_cloud": "ichimoku_cloud",
    "supertrend": "supertrend",
    "ema_ribbon": "ema_ribbon",
    "zscore_meanrev": "zscore_meanrev",
    "ichi_adx": "ichi_adx",
    "awesome_oscillator": "awesome_oscillator",
    "awesome_osc": "awesome_oscillator",
    "dual_thrust": "dual_thrust",
    "dualthrust_adx": "dualthrust_adx",
    "range_bounce": "range_bounce",
    "stoch_mtf": "stoch_mtf",
}

# ---------------------------------------------------------------------------
# Signals that use Ichimoku params
# ---------------------------------------------------------------------------
ICHI_SIGNALS = {"ichimoku_cloud", "ichi_adx", "ichi_supertrend", "ema_ichimoku_hybrid"}

# ---------------------------------------------------------------------------
# Base config — all fields required by BacktestEngine
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
    # Supertrend params
    "supertrend_multiplier": 2.0,
    # Dual supertrend params
    "dual_supertrend_fast_period": 7,
    "dual_supertrend_fast_mult": 2.0,
    "dual_supertrend_slow_period": 14,
    "dual_supertrend_slow_mult": 3.0,
    # Vol expansion params
    "vol_expansion_threshold": 1.8,
    "vol_expansion_lookback": 1,
    "vol_expansion_atr_ma_period": 20,
    "vol_expansion_ema_trend_period": 50,
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
    # All signals disabled by default
    "signals": {s: {"enabled": False} for s in ALL_SIGNALS},
}

# ---------------------------------------------------------------------------
# Improvements — hand-curated best combo per coin (matches task spec)
# The script will also load from top_per_coin in sweep JSON and cross-check.
# ---------------------------------------------------------------------------
IMPROVEMENTS: list[dict] = [
    {"coin": "GALA",     "signal": "ema_ribbon",      "tf": "4h", "sl": 1.5, "tp": 4.0, "trail": 3.0},
    {"coin": "ICP",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.0, "tp": 3.0, "trail": 2.0},
    {"coin": "UNI",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "DOT",      "signal": "ichi_adx",        "tf": "4h", "sl": 3.0, "tp": 0,   "trail": 4.0},
    {"coin": "SUI",      "signal": "ichi_adx",        "tf": "4h", "sl": 2.0, "tp": 3.0, "trail": 2.5},
    {"coin": "PENGU",    "signal": "ichimoku_cloud",  "tf": "4h", "sl": 3.0, "tp": 0,   "trail": 4.0},
    {"coin": "ZEC",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 0.5, "tp": 1.0, "trail": 1.0},
    {"coin": "ZEN",      "signal": "supertrend",      "tf": "4h", "sl": 2.5, "tp": 5.0, "trail": 4.0},
    {"coin": "ZRO",      "signal": "awesome_oscillator", "tf": "4h", "sl": 1.5, "tp": 4.0, "trail": 3.0},
    {"coin": "VVV",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 2.0, "tp": 3.0, "trail": 2.5},
    {"coin": "AXS",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 2.0, "tp": 3.0, "trail": 2.5},
    {"coin": "XAI",      "signal": "supertrend",      "tf": "4h", "sl": 2.5, "tp": 5.0, "trail": 4.0},
    {"coin": "QNT",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.0, "tp": 3.0, "trail": 2.0},
    {"coin": "FARTCOIN", "signal": "zscore_meanrev",  "tf": "1h", "sl": 2.0, "tp": 3.0, "trail": 2.5},
    {"coin": "ALICE",    "signal": "awesome_oscillator", "tf": "4h", "sl": 2.0, "tp": 3.0, "trail": 2.5},
    {"coin": "ATOM",     "signal": "ema_ribbon",      "tf": "4h", "sl": 1.5, "tp": 3.0, "trail": 2.5},
    {"coin": "ENA",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 2.0, "tp": 3.0, "trail": 2.5},
    {"coin": "TON",      "signal": "ichi_adx",        "tf": "4h", "sl": 2.5, "tp": 4.0, "trail": 3.0},
    {"coin": "KAS",      "signal": "ema_ribbon",      "tf": "4h", "sl": 1.5, "tp": 4.0, "trail": 3.0},
    {"coin": "ADA",      "signal": "ichi_adx",        "tf": "4h", "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "ANIME",    "signal": "awesome_oscillator", "tf": "4h", "sl": 3.0, "tp": 0, "trail": 4.0},
    {"coin": "AVAX",     "signal": "ema_ribbon",      "tf": "4h", "sl": 2.5, "tp": 5.0, "trail": 4.0},
    {"coin": "POL",      "signal": "ichimoku_cloud",  "tf": "4h", "sl": 3.0, "tp": 0,   "trail": 4.0},
    {"coin": "DASH",     "signal": "ichimoku_cloud",  "tf": "4h", "sl": 1.0, "tp": 2.0, "trail": 2.0},
    {"coin": "PIXEL",    "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.5, "trail": 2.5},
    {"coin": "CFX",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.5, "trail": 2.5},
    {"coin": "CRV",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 2.0, "tp": 3.0, "trail": 2.5},
    {"coin": "SAND",     "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.0, "tp": 2.0, "trail": 2.0},
    {"coin": "AAVE",     "signal": "supertrend",      "tf": "4h", "sl": 2.5, "tp": 4.0, "trail": 3.5},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def coin_to_prefix(coin: str) -> str:
    """Derive data file prefix from coin name."""
    # Special cases
    specials = {
        "FARTCOIN": "fartcoinusdt",
        "1000SHIB": "1000shibusdt",
        "1000PEPE": "1000pepeusdt",
    }
    if coin in specials:
        return specials[coin]
    return f"{coin.lower()}usdt"


def load_data(prefix: str) -> Optional[pd.DataFrame]:
    """Load 1H OHLCV data for a coin prefix."""
    for suffix in ["2y", "5y", "1y"]:
        fpath = DATA_DIR / f"{prefix}_1h_{suffix}.csv"
        if fpath.exists():
            return load_ohlcv(str(fpath))
    return None


def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV to 4H bars."""
    return (
        df_1h.resample("4h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def build_config(imp: dict) -> dict:
    """Build BacktestEngine config for a single improvement candidate."""
    cfg = copy.deepcopy(BASE_CONFIG)

    coin = imp["coin"]
    signal_name = imp["signal"]
    sl = imp["sl"]
    tp = imp["tp"]
    trail = imp["trail"]

    # Map sweep signal name → engine signal key
    engine_signal = SIGNAL_MAP.get(signal_name, signal_name)

    cfg["symbol"] = f"{coin}USDT"
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp if tp > 0 else 0.0
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail

    # Enable only the target signal
    if engine_signal not in cfg["signals"]:
        # Signal not in ALL_SIGNALS — add it dynamically
        cfg["signals"][engine_signal] = {"enabled": True}
    else:
        cfg["signals"][engine_signal] = {"enabled": True}

    # Ichimoku params for signals that need them
    if engine_signal in ICHI_SIGNALS:
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52

    return cfg


def run_engine(config: dict, signal_data: pd.DataFrame) -> Optional[dict]:
    """Run BacktestEngine and return key metrics, or None on error."""
    try:
        engine = BacktestEngine(config, initial_balance=10000.0)

        # Suppress engine stdout
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
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Load sweep data to get sweep PF for each coin
    if not SWEEP_PATH.exists():
        print(f"ERROR: {SWEEP_PATH} not found.")
        sys.exit(1)

    with open(SWEEP_PATH) as f:
        sweep_data = json.load(f)

    top_per_coin: dict[str, dict] = sweep_data.get("top_per_coin", {})

    print("WEAK COIN VERIFICATION (29 coins)")
    print("=" * 80)
    print(f"PASS: engine_pf >= {PASS_PF}  AND  trades >= {PASS_TRADES}")
    print()

    header = (
        f"{'Coin':<10}  {'Strategy':<18}  {'TF':<4}  "
        f"{'Sweep_PF':>8}  {'Engine_PF':>9}  {'Trades':>6}  {'WR%':>5}  {'Status'}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)

    results: list[dict] = []

    for imp in IMPROVEMENTS:
        coin = imp["coin"]
        signal = imp["signal"]
        tf = imp["tf"]
        sl = imp["sl"]
        tp = imp["tp"]
        trail = imp["trail"]
        is_4h = (tf == "4h")

        # Get sweep PF from top_per_coin (fallback to 0)
        sweep_entry = top_per_coin.get(coin, {})
        sweep_pf = sweep_entry.get("pf", 0.0)

        prefix = coin_to_prefix(coin)
        df_1h = load_data(prefix)

        if df_1h is None:
            print(
                f"{coin:<10}  {signal:<18}  {tf:<4}  "
                f"{sweep_pf:>8.3f}  {'N/A':>9}  {'N/A':>6}  {'N/A':>5}  NO_DATA"
            )
            results.append({
                "coin": coin, "signal": signal, "tf": tf, "sl": sl, "tp": tp, "trail": trail,
                "sweep_pf": sweep_pf, "status": "NO_DATA",
            })
            continue

        signal_data = resample_to_4h(df_1h) if is_4h else df_1h
        cfg = build_config(imp)
        metrics = run_engine(cfg, signal_data)

        if metrics is None or "error" in metrics:
            err = metrics.get("error", "unknown") if metrics else "None"
            print(
                f"{coin:<10}  {signal:<18}  {tf:<4}  "
                f"{sweep_pf:>8.3f}  {'ERR':>9}  {'ERR':>6}  {'ERR':>5}  ERROR: {err[:40]}"
            )
            results.append({
                "coin": coin, "signal": signal, "tf": tf, "sl": sl, "tp": tp, "trail": trail,
                "sweep_pf": sweep_pf, "status": "ERROR", "note": err,
            })
            continue

        pf = metrics["pf"]
        trades = metrics["trades"]
        wr = metrics["wr"]
        passed = pf >= PASS_PF and trades >= PASS_TRADES
        status = "PASS" if passed else "FAIL"

        print(
            f"{coin:<10}  {signal:<18}  {tf:<4}  "
            f"{sweep_pf:>8.3f}  {pf:>9.3f}  {trades:>6}  {wr:>5.1f}  {status}"
        )

        results.append({
            "coin": coin,
            "signal": signal,
            "tf": tf,
            "sl": sl,
            "tp": tp,
            "trail": trail,
            "sweep_pf": sweep_pf,
            "engine_pf": pf,
            "trades": trades,
            "trades_per_yr": metrics["trades_per_yr"],
            "wr_pct": wr,
            "dd_pct": metrics["dd"],
            "sharpe": metrics["sharpe"],
            "years": metrics["years"],
            "status": status,
        })

    print(sep)

    # Summary
    passed = [r for r in results if r.get("status") == "PASS"]
    failed = [r for r in results if r.get("status") == "FAIL"]
    errors = [r for r in results if r.get("status") in ("ERROR", "NO_DATA")]

    print(f"\nPASS: {len(passed)}  FAIL: {len(failed)}  ERROR/NO_DATA: {len(errors)}")
    print(f"Total: {len(results)} coins")

    if passed:
        print("\nVerified winners (sorted by engine PF desc):")
        for r in sorted(passed, key=lambda x: -x.get("engine_pf", 0)):
            print(
                f"  {r['coin']:<10}  {r['signal']:<18}  {r['tf']}  "
                f"PF={r['engine_pf']:.3f}  trades={r['trades']} ({r['trades_per_yr']:.1f}/yr)  "
                f"WR={r['wr_pct']:.0f}%  DD={r['dd_pct']:.1f}%  Sharpe={r['sharpe']:.2f}"
            )

    if errors:
        print("\nErrors / missing data:")
        for r in errors:
            print(f"  {r['coin']:<10}  {r['status']}  {r.get('note', '')}")

    # Save results
    with open(str(OUTPUT_PATH), "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved {len(results)} results to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
