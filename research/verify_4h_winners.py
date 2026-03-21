"""Verify top 8 Ichimoku 4H sweep candidates with the full BacktestEngine.

Resamples 1H data to 4H, runs BacktestEngine with ichimoku_cloud signal,
and saves verified results to data/verified_4h_winners.json.
"""

import json
import logging
import sys

import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

# Suppress noisy backtest logs
logging.basicConfig(level=logging.WARNING)


# ---------------------------------------------------------------------------
# Candidates from sweep_4h_winners.json (top 8)
# ---------------------------------------------------------------------------
CANDIDATES = [
    {"coin": "PAXG",   "prefix": "paxgusdt",   "long_only": True,  "sl_mult": 2.5, "tp_mult": 5.0, "sweep_pf": 6.30},
    {"coin": "RENDER", "prefix": "renderusdt",  "long_only": False, "sl_mult": 2.5, "tp_mult": 4.0, "sweep_pf": 3.44},
    {"coin": "HBAR",   "prefix": "hbarusdt",    "long_only": False, "sl_mult": 2.0, "tp_mult": 5.0, "sweep_pf": 3.44},
    {"coin": "TAO",    "prefix": "taousdt",     "long_only": False, "sl_mult": 2.5, "tp_mult": 4.0, "sweep_pf": 2.31},
    {"coin": "FIL",    "prefix": "filusdt",     "long_only": True,  "sl_mult": 1.5, "tp_mult": 4.0, "sweep_pf": 2.85},
    {"coin": "ADA",    "prefix": "adausdt",     "long_only": True,  "sl_mult": 2.0, "tp_mult": 5.0, "sweep_pf": 2.50},
    {"coin": "ICP",    "prefix": "icpusdt",     "long_only": False, "sl_mult": 2.5, "tp_mult": 5.0, "sweep_pf": 2.20},
    {"coin": "DOT",    "prefix": "dotusdt",     "long_only": True,  "sl_mult": 2.0, "tp_mult": 4.0, "sweep_pf": 2.00},
]

PASS_PF = 1.1
PASS_TRADES = 10

# Template config — mirrors config_avax_ichi.json but adapted for 4H
BASE_CONFIG = {
    "exchange": "binance",
    "timeframe_signal": "4h",
    "timeframe_trend": "4h",
    "leverage": 25,
    "risk_per_trade": 0.01,          # 1% for conservative expansion estimate
    "max_daily_loss": 0.3,
    "max_positions": 1,
    "max_consecutive_losses": 5,
    "cooldown_hours": 4,
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
    "move_sl_to_be_after_tp1": True,
    "breakeven_buffer_atr_mult": 0.5,
    "atr_trail_mult": 3.0,
    "atr_trail_mult_trending": 3.0,
    "atr_trail_mult_ranging": 2.0,
    "atr_trail_mult_volatile": 4.0,
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
    # 4H candles span the full trading day — disable hour filter
    "weekend_trading_enabled": False,
    "weekend_size_reduction": 0.5,
    "ema_slope_period": 5,
    "ema_slope_min": 0.0,           # disable slope filter for 4H (wider candles)
    "ichimoku_tenkan": 9,
    "ichimoku_kijun": 26,
    "ichimoku_senkou_b": 52,
    "trading_hours": {"enabled": False, "start_utc": 0, "end_utc": 24},
    "regime_filter": {"enabled": True, "skip_ranging": True},
    "flexible_cooldown": {"enabled": False},
    "signals": {
        "ema_crossover": {"enabled": False},
        "ema_fast_crossover": {"enabled": False},
        "ema_pullback": {"enabled": False},
        "rsi_divergence": {"enabled": False},
        "bb_breakout": {"enabled": False},
        "mean_reversion": {"enabled": False},
        "body_dominance": {"enabled": False},
        "squeeze_release": {"enabled": False},
        "ichimoku_cloud": {"enabled": True},
    },
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


def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV DataFrame to 4H bars.

    Args:
        df_1h: 1H OHLCV data with DatetimeIndex.

    Returns:
        4H resampled DataFrame with NaN rows dropped.
    """
    df_4h = df_1h.resample("4h").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna()
    return df_4h


def run_candidate(cand: dict) -> dict:
    """Run BacktestEngine for a single candidate.

    Args:
        cand: Candidate dict with coin, prefix, long_only, sl_mult, tp_mult, sweep_pf.

    Returns:
        Result dict with all metrics and PASS/FAIL status.
    """
    coin = cand["coin"]
    prefix = cand["prefix"]

    # Locate data file — prefer 5y, fall back to 2y
    data_path_5y = f"/Users/iceai/Work/ccbt/data/{prefix}_1h_5y.csv"
    data_path_2y = f"/Users/iceai/Work/ccbt/data/{prefix}_1h_2y.csv"

    import os
    if os.path.exists(data_path_5y):
        data_path = data_path_5y
        data_label = "5y"
    elif os.path.exists(data_path_2y):
        data_path = data_path_2y
        data_label = "2y"
    else:
        return {
            "coin": coin,
            "sweep_pf": cand["sweep_pf"],
            "engine_pf": None,
            "trades": 0,
            "wr_pct": None,
            "dd_pct": None,
            "sharpe": None,
            "status": "NO_DATA",
            "note": "No 1H data file found",
        }

    # Load and resample
    df_1h = load_ohlcv(data_path)
    df_4h = resample_to_4h(df_1h)

    # Build config
    config = dict(BASE_CONFIG)
    config["symbol"] = f"{coin}USDT"
    config["atr_sl_mult"] = cand["sl_mult"]
    config["atr_tp_mult"] = cand["tp_mult"]

    # For long-only coins: BacktestEngine has no long_only flag —
    # run standard (both directions) as a conservative lower bound.
    # Note this in the result.
    long_only_note = ""
    if cand["long_only"]:
        long_only_note = "long_only_in_sweep_ran_both"

    # Run engine — suppress engine's own print_metrics output
    import io
    engine = BacktestEngine(config, initial_balance=10000.0)
    try:
        _stdout_capture = io.StringIO()
        _real_stdout = sys.stdout
        sys.stdout = _stdout_capture
        try:
            metrics = engine.run(signal_data=df_4h, trend_data=None)
        finally:
            sys.stdout = _real_stdout
    except Exception as exc:
        return {
            "coin": coin,
            "sweep_pf": cand["sweep_pf"],
            "engine_pf": None,
            "trades": 0,
            "wr_pct": None,
            "dd_pct": None,
            "sharpe": None,
            "status": "ERROR",
            "note": str(exc),
        }

    pf = round(metrics.profit_factor, 3)
    trades = metrics.total_trades
    wr = round(metrics.win_rate * 100, 1) if metrics.win_rate is not None else 0.0
    # max_drawdown is already a fraction (e.g. 0.067 = 6.7%)
    dd = round(metrics.max_drawdown * 100, 1) if metrics.max_drawdown is not None else 0.0
    sharpe = round(metrics.sharpe_ratio, 2) if metrics.sharpe_ratio is not None else 0.0

    status = "PASS" if (pf >= PASS_PF and trades >= PASS_TRADES) else "FAIL"

    note_parts = []
    if long_only_note:
        note_parts.append(long_only_note)
    note_parts.append(f"data={data_label}")
    note_parts.append(f"4H_bars={len(df_4h)}")

    return {
        "coin": coin,
        "sweep_pf": cand["sweep_pf"],
        "engine_pf": pf,
        "trades": trades,
        "wr_pct": wr,
        "dd_pct": dd,
        "sharpe": sharpe,
        "status": status,
        "note": ", ".join(note_parts),
    }


def main() -> None:
    results = []

    # Header
    print(
        f"\n{'Coin':<8}  {'Sweep_PF':>8}  {'Engine_PF':>9}  "
        f"{'Trades':>6}  {'WR%':>5}  {'DD%':>5}  {'Sharpe':>6}  {'Status':<6}  Note"
    )
    print("-" * 100)

    for cand in CANDIDATES:
        print(f"  Running {cand['coin']}...", end="", flush=True)
        result = run_candidate(cand)
        results.append(result)

        pf_str = f"{result['engine_pf']:.3f}" if result["engine_pf"] is not None else "N/A"
        wr_str = f"{result['wr_pct']:.1f}" if result["wr_pct"] is not None else "N/A"
        dd_str = f"{result['dd_pct']:.1f}" if result["dd_pct"] is not None else "N/A"
        sh_str = f"{result['sharpe']:.2f}" if result["sharpe"] is not None else "N/A"

        print(
            f"\r{result['coin']:<8}  {result['sweep_pf']:>8.2f}  {pf_str:>9}  "
            f"{result['trades']:>6}  {wr_str:>5}  {dd_str:>5}  {sh_str:>6}  "
            f"{result['status']:<6}  {result['note']}"
        )

    print("-" * 100)
    passed = [r for r in results if r["status"] == "PASS"]
    print(f"\nPASS: {len(passed)}/{len(results)} candidates verified by full engine")
    print(f"PASS criteria: engine_pf >= {PASS_PF} AND trades >= {PASS_TRADES}\n")

    if passed:
        print("Verified winners:")
        for r in passed:
            print(
                f"  {r['coin']:<8}  engine_pf={r['engine_pf']:.3f}  "
                f"trades={r['trades']}  WR={r['wr_pct']}%  DD={r['dd_pct']}%  Sharpe={r['sharpe']}"
            )

    # Save results
    out_path = "/Users/iceai/Work/ccbt/data/verified_4h_winners.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
