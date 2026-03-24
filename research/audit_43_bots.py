"""Walk-forward audit for 43 bots that have not been audited yet.

For each bot:
  1. Load 1H data (resample to 4H if needed)
  2. Split at midpoint (IS = first half, OOS = second half)
  3. Run BacktestEngine on each half independently
  4. PASS if: OOS PF >= 60% of IS PF AND OOS trades >= 3 AND full PF >= 1.2

Saves results to data/audit_43.json.
"""

import contextlib
import copy
import io
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

DATA_DIR = Path("/Users/iceai/Work/ccbt/data")
OUT_PATH = DATA_DIR / "audit_43.json"

# ---------------------------------------------------------------------------
# Bots to audit
# ---------------------------------------------------------------------------
BOTS = [
    {"coin": "1000SHIB", "signal": "ema_ribbon",      "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "1000SHIB", "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "ADA",      "signal": "awesome_oscillator","tf": "1h","sl": 1.5, "tp": 4.0, "trail": 3.0},
    {"coin": "ADA",      "signal": "stoch_mtf",        "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "ADA",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "ANIME",    "signal": "ichimoku_cloud",  "tf": "1h", "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "ANIME",    "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "BERA",     "signal": "ichimoku_cloud",  "tf": "1h", "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "BERA",     "signal": "ema_ribbon",      "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "BNB",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "CFX",      "signal": "awesome_oscillator","tf": "1h","sl": 1.5, "tp": 4.0, "trail": 3.0},
    {"coin": "CFX",      "signal": "ema_ribbon",      "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "CFX",      "signal": "range_bounce",    "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "CFX",      "signal": "stoch_mtf",       "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "CRV",      "signal": "range_bounce",    "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "DEGO",     "signal": "dual_thrust",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "ENA",      "signal": "stoch_mtf",       "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "ETHFI",    "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "GALA",     "signal": "awesome_oscillator","tf": "1h","sl": 1.5, "tp": 4.0, "trail": 3.0},
    {"coin": "GALA",     "signal": "dual_thrust",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "INJ",      "signal": "ichimoku_cloud",  "tf": "1h", "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "INJ",      "signal": "ema_ribbon",      "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "INJ",      "signal": "stoch_mtf",       "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "INJ",      "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "ONDO",     "signal": "ichimoku_cloud",  "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "ONDO",     "signal": "awesome_oscillator","tf": "1h","sl": 1.5, "tp": 4.0, "trail": 3.0},
    {"coin": "ONDO",     "signal": "range_bounce",    "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "ONDO",     "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "QNT",      "signal": "dual_thrust",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "TIA",      "signal": "ichimoku_cloud",  "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "TIA",      "signal": "dual_thrust",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "TIA",      "signal": "range_bounce",    "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "TRUMP",    "signal": "ichimoku_cloud",  "tf": "1h", "sl": 2.0, "tp": 5.0, "trail": 3.0},
    {"coin": "TRUMP",    "signal": "ema_ribbon",      "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "TRUMP",    "signal": "stoch_mtf",       "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "TRUMP",    "signal": "zscore_meanrev",  "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "VIRTUAL",  "signal": "awesome_oscillator","tf": "1h","sl": 1.5, "tp": 4.0, "trail": 3.0},
    {"coin": "VIRTUAL",  "signal": "dual_thrust",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "VVV",      "signal": "dual_thrust",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "VVV",      "signal": "range_bounce",    "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "W",        "signal": "dual_thrust",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"coin": "W",        "signal": "range_bounce",    "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"coin": "ZEC",      "signal": "dual_thrust",     "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
]

# ---------------------------------------------------------------------------
# All signal keys — all off by default; only one enabled per run
# ---------------------------------------------------------------------------
ALL_SIGNALS_OFF: dict = {
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
    "adx_di_cross":        {"enabled": False},
    "choppiness_ema":      {"enabled": False},
    "williams_r_adx":      {"enabled": False},
    "roc_momentum":        {"enabled": False},
    "stoch_supertrend":    {"enabled": False},
    "price_channel_vol":   {"enabled": False},
    "ema_alligator":       {"enabled": False},
    "supertrend_volume":   {"enabled": False},
    "dual_thrust":         {"enabled": False},
    "awesome_oscillator":  {"enabled": False},
    "range_bounce":        {"enabled": False},
    "stoch_mtf":           {"enabled": False},
    "zscore_meanrev":      {"enabled": False},
    "ema_ribbon":          {"enabled": False},
}

# ---------------------------------------------------------------------------
# Base engine config — mirrors round10/11 scripts
# ---------------------------------------------------------------------------
BASE_CONFIG: dict = {
    "exchange": "binance",
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
    "ema_fast2": 5,
    "ema_slow2": 13,
    "rsi_period": 14,
    "rsi_min": 45,
    "rsi_max": 65,
    "rsi_long_min": 45,
    "rsi_long_max": 65,
    "rsi_short_min": 35,
    "rsi_short_max": 55,
    "atr_period": 14,
    "atr_min": 0.0,
    "partial_tp_enabled": False,
    "partial_tp_pct": 0.3,
    "partial_tp_atr_mult": 2.0,
    "move_sl_to_be_after_tp1": True,
    "breakeven_buffer_atr_mult": 0.5,
    "atr_trail_mult_post_tp1": 4.0,
    "volume_mult": 1.0,
    "volume_max_mult": None,
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
    "vol_expansion_threshold": 1.8,
    "vol_expansion_lookback": 1,
    "vol_expansion_atr_ma_period": 20,
    "supertrend_multiplier": 2.0,
    "supertrend_atr_period": 14,
    "trading_hours": {"enabled": True, "start_utc": 3, "end_utc": 20},
    "regime_filter": {"enabled": True, "skip_ranging": False},
    "flexible_cooldown": {
        "enabled": False,
        "min_quality_score": 0.7,
        "cooldown_reduction_factor": 0.5,
        "log_overrides": True,
    },
    "adaptive_sizing": {"enabled": False},
    "pyramiding": {"enabled": False},
    "mtd_accelerator": {"enabled": False},
    "signal_scorer": {"enabled": False},
    "ai_layer": {"enabled": False},
    "body_dominance_min_body": 0.65,
    "body_dominance_min_mom": 0.02,
    "body_dominance_min_vol": 1.5,
    "squeeze_release_low": 0.7,
    "squeeze_release_high": 0.8,
    "squeeze_release_min_mom4": 0.0,
    "mr_rsi_oversold": 30,
    "mr_rsi_overbought": 70,
    "mr_atr_sl_mult": 1.5,
    "mr_atr_tp_mult": 2.0,
    "range_bounce_atr_sl_mult": 1.5,
    "range_bounce_atr_tp_mult": 2.0,
    "range_bounce_proximity_pct": 0.015,
    "bb_period": 20,
    "bb_std": 2.0,
    "bb_squeeze_percentile": 0.3,
    "bb_volume_mult": 1.2,
    "swing_lookback": 5,
    "divergence_lookback": 20,
    "regime_lookback": 20,
    "stoch_k_period": 14,
    "stoch_d_period": 3,
    "stoch_oversold": 20.0,
    "stoch_overbought": 80.0,
    "zscore_period": 20,
    "zscore_threshold": 2.0,
    "zscore_atr_sl_mult": 1.5,
    "zscore_atr_tp_mult": 2.0,
    "dual_thrust_k": 0.5,
    "dual_thrust_lookback": 4,
    "ao_fast": 5,
    "ao_slow": 34,
    "ema_ribbon_periods": [9, 13, 21, 34, 55],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def coin_to_prefix(coin: str) -> str:
    """Convert coin name to data file prefix (e.g. 1000SHIB -> 1000shibusdt)."""
    return coin.lower() + "usdt"


def resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV DataFrame to 4H."""
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    resampled = df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return resampled


def load_signal_df(prefix: str, tf: str) -> pd.DataFrame:
    """Load data for signal timeframe; resample 1H->4H when needed."""
    for suffix in ("_1h_2y.csv", "_1h_5y.csv"):
        path = DATA_DIR / f"{prefix}{suffix}"
        if path.exists():
            df_1h = load_ohlcv(str(path))
            if tf == "4h":
                return resample_to_4h(df_1h)
            return df_1h
    return pd.DataFrame()


def split_half(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split DataFrame at midpoint index (row-based)."""
    mid = len(df) // 2
    return df.iloc[:mid].copy(), df.iloc[mid:].copy()


def build_config(bot: dict) -> dict:
    """Build a full engine config for one bot."""
    coin = bot["coin"]
    signal = bot["signal"]
    tf = bot["tf"]
    sl = bot["sl"]
    tp = bot["tp"]
    trail = bot["trail"]

    cfg = copy.deepcopy(BASE_CONFIG)
    prefix = coin_to_prefix(coin)
    cfg["symbol"] = prefix.upper()
    cfg["timeframe_signal"] = tf
    cfg["timeframe_trend"] = tf

    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_trail_mult_ranging"] = max(2.0, trail - 1.0)
    cfg["atr_trail_mult_volatile"] = trail + 1.0

    # Ichimoku params
    cfg["ichimoku_tenkan"] = 9
    cfg["ichimoku_kijun"] = 26
    cfg["ichimoku_senkou_b"] = 52

    signals = copy.deepcopy(ALL_SIGNALS_OFF)
    signals[signal] = {"enabled": True}
    cfg["signals"] = signals

    return cfg


def compute_pf_from_trades(trades: list) -> tuple[float, int]:
    """Return (profit_factor, trade_count) from a list of BacktestTrade objects."""
    if not trades:
        return 0.0, 0
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl <= 0))
    if gross_loss == 0:
        pf = float("inf") if gross_profit > 0 else 0.0
    else:
        pf = gross_profit / gross_loss
    return round(pf, 2), len(trades)


def run_engine(cfg: dict, signal_df: pd.DataFrame) -> list:
    """Run BacktestEngine on signal_df (trend_df=None for 1H/4H strategies).

    Returns list of BacktestTrade objects, or empty list on error.
    Suppresses engine stdout (print_metrics) so the audit table stays readable.
    """
    try:
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        with contextlib.redirect_stdout(io.StringIO()):
            engine.run(signal_df, None)
        return engine.state.trades
    except Exception as exc:
        print(f"    [ENGINE ERROR] {exc}")
        return []


# ---------------------------------------------------------------------------
# Walk-forward pass/fail criteria
# ---------------------------------------------------------------------------
MIN_FULL_PF = 1.2
MIN_OOS_RATIO = 0.60   # OOS PF >= 60% of IS PF
MIN_OOS_TRADES = 3


def verdict(is_pf: float, oos_pf: float, oos_trades: int, full_pf: float) -> tuple[str, float]:
    """Return (verdict_str, ratio) for a bot."""
    ratio = (oos_pf / is_pf) if is_pf > 0 else 0.0
    if full_pf < MIN_FULL_PF:
        return "FAIL (full PF < 1.2)", ratio
    if oos_trades < MIN_OOS_TRADES:
        return f"FAIL (OOS trades={oos_trades} < 3)", ratio
    if ratio < MIN_OOS_RATIO:
        return f"FAIL (ratio={ratio:.0%} < 60%)", ratio
    return "PASS", ratio


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    results = []

    header = f"{'Bot':<40}  {'IS_PF':>6}  {'IS_Tr':>5}  {'OOS_PF':>7}  {'OOS_Tr':>6}  {'Ratio':>6}  Verdict"
    sep = "-" * len(header)
    print()
    print("WALK-FORWARD AUDIT — 43 Bots")
    print(sep)
    print(header)
    print(sep)

    n_pass = 0
    n_fail = 0

    for bot in BOTS:
        coin = bot["coin"]
        signal = bot["signal"]
        tf = bot["tf"]
        label = f"{coin} {signal} {tf}"

        prefix = coin_to_prefix(coin)
        signal_df = load_signal_df(prefix, tf)

        if signal_df.empty or len(signal_df) < 100:
            print(f"  {label:<40}  [NO DATA]")
            results.append({
                "bot": label, "coin": coin, "signal": signal, "tf": tf,
                "verdict": "ERROR (no data)",
                "is_pf": None, "is_trades": None,
                "oos_pf": None, "oos_trades": None,
                "ratio": None, "full_pf": None,
            })
            n_fail += 1
            continue

        cfg = build_config(bot)

        # Full period run (for full_pf check)
        full_trades = run_engine(cfg, signal_df)
        full_pf, full_n = compute_pf_from_trades(full_trades)

        # IS / OOS split
        is_df, oos_df = split_half(signal_df)

        is_trades_list = run_engine(cfg, is_df)
        oos_trades_list = run_engine(cfg, oos_df)

        is_pf, is_n = compute_pf_from_trades(is_trades_list)
        oos_pf, oos_n = compute_pf_from_trades(oos_trades_list)

        v, ratio = verdict(is_pf, oos_pf, oos_n, full_pf)

        pass_flag = v == "PASS"
        if pass_flag:
            n_pass += 1
        else:
            n_fail += 1

        ratio_str = f"{ratio:.0%}" if is_pf > 0 else "N/A"

        print(
            f"  {label:<40}  {is_pf:>6.2f}  {is_n:>5d}  {oos_pf:>7.2f}  {oos_n:>6d}  "
            f"{ratio_str:>6}  {v}"
        )

        results.append({
            "bot": label,
            "coin": coin,
            "signal": signal,
            "tf": tf,
            "verdict": v,
            "is_pf": is_pf,
            "is_trades": is_n,
            "oos_pf": oos_pf,
            "oos_trades": oos_n,
            "ratio": round(ratio, 4),
            "full_pf": full_pf,
            "full_trades": full_n,
        })

    print(sep)
    print(f"\nSummary: {n_pass} PASS | {n_fail} FAIL\n")

    # Save JSON
    with open(OUT_PATH, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {OUT_PATH}")


if __name__ == "__main__":
    main()
