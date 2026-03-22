"""MEGA100 Engine Verification + Portfolio Backtest.

Part 1: Verify sweep_mega100.json winners (PF >= 1.5, trades >= 10, days_data >= 365)
        against the full BacktestEngine. Each winner is mapped to the closest
        engine signal that captures the core idea.

Part 2: Realistic portfolio backtest ($200 shared wallet, R-multiple, max 5 concurrent)
        combining ALL rounds (R9 through R11 + new mega100 verified bots).

Strategy mapping philosophy:
  The sweep used lightweight simulation; the engine is authoritative. Winners are mapped
  to the closest available engine signal — the primary indicator becomes the engine signal
  and secondary confirmations are implicit in the engine's own filters (RSI, volume, ATR).

Run: python3 research/mega100_verify_backtest.py
"""

from __future__ import annotations

import copy
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
logging.basicConfig(level=logging.WARNING)

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

DATA_DIR = Path("/Users/iceai/Work/ccbt/data")

# ---------------------------------------------------------------------------
# All signal keys off — enable only one at a time
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
    "dual_thrust_lookback": 20,
}

# ---------------------------------------------------------------------------
# Strategy mapping: sweep combo name -> engine signal key
# The primary indicator of the combo determines the engine signal.
# ---------------------------------------------------------------------------
COMBO_TO_ENGINE: dict[str, str] = {
    # Trend + Momentum (Category A)
    "EMA+RSI+Vol":        "ema_crossover",
    "EMA+ADX":            "ema_crossover",
    "EMA+MACD":           "ema_crossover",
    "EMA+AO":             "ema_crossover",
    "EMA+Stoch":          "ema_crossover",
    "Ichi+RSI":           "ichimoku_cloud",
    "Ichi+ADX":           "ichimoku_cloud",
    "Ichi+MACD":          "ichimoku_cloud",
    "Ichi+ATR":           "ichimoku_cloud",
    "ST+MACD":            "supertrend",
    "ST+ADX":             "supertrend",
    "ST+RSI":             "supertrend",
    "ST+AO":              "supertrend",
    # Trend + Volatility (Category B)
    "DualThrust+ADX":     "dual_thrust",
    "DualThrust+Vol":     "dual_thrust",
    "DualThrust+RSI":     "dual_thrust",
    "Donch+ATR":          "dual_thrust",
    "Donch+Vol":          "dual_thrust",
    "Donch+RSI":          "dual_thrust",
    "ST+BB_squeeze":      "supertrend",
    "ST+ATR_pctile":      "supertrend",
    "EMA+BB_expand":      "ema_crossover",
    "EMA+ATR_rise":       "ema_crossover",
    "EMA+KC":             "ema_crossover",
    "BB+MACD":            "supertrend",
    "ATRFlip+RSI":        "supertrend",
    "ATRFlip+Vol":        "supertrend_volume",
    # Mean Reversion (Category C)
    "ZScore+Stoch":       "zscore_meanrev",
    "ZScore+BB":          "zscore_meanrev",
    "ZScore+RSI+trend":   "zscore_meanrev",
    "ZScore+OBV_div":     "zscore_meanrev",
    "ConnorsRSI+trend":   "zscore_meanrev",
    "RSI2+trend+Vol":     "zscore_meanrev",
    "RSI+BB+Vol":         "range_bounce",
    "BB+RSI+Vol":         "range_bounce",
    "BB+CMF":             "range_bounce",
    "Support+RSI_div":    "range_bounce",
    "RangeBounce+RSI+Stoch": "range_bounce",
    "RangeBounce+ZScore": "range_bounce",
    "Eltrut+Chop":        "range_bounce",
    # Volume-Based (Category D)
    "OBV+EMA":            "ema_crossover",
    "OBV+RSI":            "ema_crossover",
    "CMF+EMA":            "ema_crossover",
    "CMF+ADX":            "adx_di_cross",
    "VolSpike+EMA":       "ema_crossover",
    "VolSpike+ST":        "supertrend_volume",
    "VolSpike+MACD":      "supertrend_volume",
    "MFI+trend":          "ema_crossover",
    "Regime+Vol":         "supertrend_volume",
    "BigCandle+Vol+EMA":  "vol_expansion",
    # Multi-Confluence (Category E)
    "Ribbon+RSI+Vol":     "ema_ribbon",
    "Ribbon+AO":          "ema_ribbon",
    "Quad_trend":         "ema_ribbon",
    "Quad_ST":            "supertrend",
    "Quad_confirm":       "ema_ribbon",
    "Penta_confirm":      "ema_ribbon",
    "Triple_trend":       "ema_crossover",
    "Triple_strong":      "ema_crossover",
    "Triple_ichi":        "ichimoku_cloud",
    "Triple_ST_ichi":     "ichi_supertrend",
    "All_osc":            "stoch_mtf",
    "Alligator+RSI+Vol":  "alligator",
    "DualST+Ichi":        "dual_supertrend",
    "Multi_system":       "ichimoku_cloud",
    "All_MA_above":       "ema_ribbon",
    # Pattern + Indicator (Category F)
    "HA_rev+RSI":         "ema_crossover",    # HA candle pattern -> EMA (closest)
    "Hammer+RSI+trend":   "range_bounce",
    "ShootStar+RSI+trend":"range_bounce",
    "Engulf+Vol+trend":   "ema_crossover",
    "InsideBar+ADX":      "adx_di_cross",
    "3Green+Vol+EMA":     "ema_crossover",
    "PinBar+Fib+RSI":     "range_bounce",
    "Swing+Vol+RSI":      "price_channel_vol",
    # Exotic / Adaptive (Category G)
    "Kalman+ADX":         "dual_supertrend",
    "Kalman+RSI":         "dual_supertrend",
    "Renko+EMA":          "supertrend",
    "Renko+Vol":          "supertrend_volume",
    "Pivot+Vol+ADX":      "price_channel_vol",
    "StochMTF+ZScore":    "stoch_mtf",
    "Fib+RSI+trend":      "zscore_meanrev",
    "Fib+Vol":            "price_channel_vol",
    "CCI+ADX":            "adx_di_cross",
    "CCI+Vol":            "adx_di_cross",
    "Aroon+RSI":          "ichimoku_cloud",
    "Aroon+Vol":          "ichimoku_cloud",
    "WR+ADX+RSI":         "williams_r_adx",
    "ROC+ADX":            "roc_momentum",
    "ROC+RSI_zone":       "roc_momentum",
    "Oscillator_triple":  "stoch_mtf",
    "Doji+squeeze+ADX":   "adx_di_cross",
    "MACD+ADX":           "supertrend",
    "MACD+Vol":           "supertrend",
    "MACD+BB":            "supertrend",
    "KC+ADX":             "adx_di_cross",
    "KC+RSI":             "ema_crossover",
    "PriceCh+ADX":        "price_channel_vol",
    "ForceIdx+EMA":       "ema_crossover",
    "Chop+ATR+EMA":       "choppiness_ema",
}

# All coins already deployed (prior rounds + R11)
ALL_EXISTING_COINS: set[str] = {
    "1000PEPE", "1000SHIB", "AAVE", "ADA", "AKT", "ALGO", "ANIME", "APT", "ARB", "ARC",
    "ATH", "ATOM", "AVAX", "AXS", "BAN", "BERA", "BNB", "BTC", "CFX", "DASH", "ENA", "ENJ",
    "ETHFI", "FET", "GUN", "H", "HBAR", "HUMA", "ICP", "INJ", "IP", "KAS", "LIGHT", "LINK",
    "LTC", "LYN", "MSTR", "NEAR", "ONDO", "PENGU", "PIPPIN", "PIXEL", "POL", "POLYX", "QNT",
    "RENDER", "SAHARA", "SIGN", "SUI", "TAO", "TIA", "TON", "TRUMP", "TRX", "WIF", "WLD",
    "XAG", "XLM", "XMR", "XPL", "XRP", "ZETA",
    # Also exclude forex
    "XAUUSD",
}

# Existing bots for portfolio backtest (Part 2) — same as R11's EXISTING_BOTS
EXISTING_BOTS: list[tuple] = [
    # (coin, strategy_key, tf, sl, tp, trail, label, prefix, risk_pct)
    ("BTC",      "ema_crossover",  "15m", 1.0, 3.0, 2.0, "EMA 15m",       "btcusdt",      0.05),
    ("WIF",      "ema_crossover",  "15m", 1.0, 3.0, 2.0, "EMA 15m",       "wifusdt",      0.03),
    ("ARC",      "ema_crossover",  "15m", 1.0, 3.0, 2.0, "EMA 15m",       "arcusdt",      0.03),
    ("AVAX",     "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "avaxusdt",     0.02),
    ("POL",      "ichimoku_cloud", "1h",  1.0, 0.0, 3.0, "Ichi 1H Trail", "polusdt",      0.01),
    ("GUN",      "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "gunusdt",      0.01),
    ("BERA",     "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "berausdt",     0.02),
    ("ATH",      "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "athusdt",      0.01),
    ("INJ",      "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "injusdt",      0.01),
    ("TRUMP",    "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "trumpusdt",    0.01),
    ("ANIME",    "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "animeusdt",    0.01),
    ("IP",       "ichimoku_cloud", "1h",  2.0, 4.0, 3.0, "Ichi 1H",       "ipusdt",       0.01),
    ("1000PEPE", "vol_expansion",  "1h",  2.5, 3.0, 2.5, "VolExp 1H",     "1000pepeusdt", 0.01),
    ("ONDO",     "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "ondousdt",     0.01),
    ("TIA",      "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "tiausdt",      0.01),
    ("TON",      "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "tonusdt",      0.01),
    ("XMR",      "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "xmrusdt",      0.01),
]

# R10/R11 min thresholds for inclusion in portfolio backtest
PRIOR_ROUND_MIN_PF = 1.2
PRIOR_ROUND_MIN_TRADES = 8


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------


def build_config(
    symbol: str,
    strategy_key: str,
    tf_signal: str,
    atr_sl_mult: float,
    atr_tp_mult: float,
    atr_trail_mult: float,
    data_prefix: str,
    extra_params: Optional[dict] = None,
) -> dict:
    """Build a complete engine config for one bot."""
    cfg = copy.deepcopy(BASE_CONFIG)
    cfg["symbol"] = data_prefix.upper()
    cfg["timeframe_signal"] = tf_signal
    cfg["timeframe_trend"] = tf_signal
    cfg["atr_sl_mult"] = atr_sl_mult
    cfg["atr_tp_mult"] = atr_tp_mult
    cfg["atr_trail_mult"] = atr_trail_mult
    cfg["atr_trail_mult_trending"] = atr_trail_mult
    cfg["atr_trail_mult_ranging"] = max(2.0, atr_trail_mult - 1.0)
    cfg["atr_trail_mult_volatile"] = atr_trail_mult + 1.0

    if extra_params:
        cfg.update(extra_params)

    signals = copy.deepcopy(ALL_SIGNALS_OFF)
    if strategy_key == "ema_crossover":
        signals["ema_crossover"] = {"enabled": True}
        signals["ema_fast_crossover"] = {"enabled": True}
    else:
        signals[strategy_key] = {"enabled": True}
    cfg["signals"] = signals
    return cfg


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV to 4H."""
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


def load_signal_data(prefix: str, tf: str) -> pd.DataFrame:
    """Load signal data; 4H is resampled from 1H."""
    if tf == "15m":
        path = DATA_DIR / f"{prefix}_15m_2y.csv"
        if not path.exists():
            return pd.DataFrame()
        return load_ohlcv(str(path))
    elif tf == "1h":
        for suffix in ("_1h_2y.csv", "_1h_5y.csv"):
            path = DATA_DIR / f"{prefix}{suffix}"
            if path.exists():
                return load_ohlcv(str(path))
        return pd.DataFrame()
    elif tf == "4h":
        for suffix in ("_1h_2y.csv", "_1h_5y.csv"):
            path = DATA_DIR / f"{prefix}{suffix}"
            if path.exists():
                return resample_to_4h(load_ohlcv(str(path)))
        return pd.DataFrame()
    return pd.DataFrame()


def load_trend_data(prefix: str, tf: str) -> pd.DataFrame:
    """For 15m bots the trend is 1H; for 1H/4H it equals signal data."""
    if tf == "15m":
        for suffix in ("_1h_2y.csv", "_1h_5y.csv"):
            path = DATA_DIR / f"{prefix}{suffix}"
            if path.exists():
                return load_ohlcv(str(path))
    return pd.DataFrame()


def filter_last_year(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the last 12 months."""
    if df.empty:
        return df
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df = df.copy()
    df.index = idx
    cutoff = df.index[-1] - pd.DateOffset(months=12)
    return df[df.index >= cutoff].copy()


def data_span_months(df: pd.DataFrame) -> float:
    """Return data span in months."""
    if df.empty or len(df) < 2:
        return 0.0
    idx = pd.to_datetime(df.index)
    return (idx[-1] - idx[0]).days / 30.44


# ---------------------------------------------------------------------------
# Engine runner
# ---------------------------------------------------------------------------


def run_engine_for_bot(
    coin: str,
    strategy_key: str,
    tf_signal: str,
    atr_sl_mult: float,
    atr_tp_mult: float,
    atr_trail_mult: float,
    label: str,
    data_prefix: str,
    extra_params: Optional[dict] = None,
    filter_year: bool = True,
) -> tuple[list, dict]:
    """Run BacktestEngine for one bot. Returns (raw_trade_objects, metrics_dict)."""
    cfg = build_config(
        coin, strategy_key, tf_signal, atr_sl_mult, atr_tp_mult, atr_trail_mult,
        data_prefix, extra_params=extra_params,
    )

    signal_df = load_signal_data(data_prefix, tf_signal)
    if signal_df.empty:
        return [], {}

    trend_df = load_trend_data(data_prefix, tf_signal)

    if filter_year:
        signal_df = filter_last_year(signal_df)
        if not trend_df.empty:
            trend_df = filter_last_year(trend_df)

    if len(signal_df) < 100:
        return [], {}

    try:
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        engine.run(signal_df, trend_df if not trend_df.empty else None)
    except Exception as exc:
        print(f"  [ENGINE ERROR] {coin} ({label}): {exc}")
        return [], {}

    trades = engine.state.trades
    if not trades:
        return [], {"pf": 0.0, "wr": 0.0, "trades": 0, "sharpe": 0.0, "dd": 0.0}

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    wr = len(wins) / len(trades) * 100.0

    equity = 10_000.0
    peak = equity
    max_dd = 0.0
    for t in trades:
        equity += t.pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0
        if dd > max_dd:
            max_dd = dd

    pnl_vals = [t.pnl / 10_000.0 for t in trades]
    if len(pnl_vals) > 1:
        mean_r = float(np.mean(pnl_vals))
        std_r = float(np.std(pnl_vals, ddof=1))
        trades_per_day = len(pnl_vals) / 365.0
        sharpe = (mean_r / std_r * (trades_per_day ** 0.5) * (365.0 ** 0.5)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    metrics = {
        "pf": round(pf, 2),
        "wr": round(wr, 1),
        "trades": len(trades),
        "sharpe": round(sharpe, 2),
        "dd": round(max_dd, 1),
    }
    return trades, metrics


# ---------------------------------------------------------------------------
# Part 1: Verify mega100 sweep winners
# ---------------------------------------------------------------------------


def verify_mega100_winners() -> tuple[list[dict], list[dict]]:
    """Verify mega100 sweep winners with BacktestEngine.

    Selection criteria:
    - PF >= 1.5 in sweep
    - trades >= 10 in sweep
    - days_data >= 365 (at least 1 year of data in sweep)
    - coin NOT already in deployed portfolio
    - Best combo per coin (highest sweep PF)
    - Sweep strategy must map to an engine signal

    Pass criteria:
    - Engine PF >= 1.2 AND trades >= 8
    """
    with open(DATA_DIR / "sweep_mega100.json") as f:
        sweep_data = json.load(f)

    all_results = sweep_data["winners"]

    # Apply selection filters
    candidates_raw = [
        x for x in all_results
        if x.get("pf", 0) >= 1.5
        and x.get("trades", 0) >= 10
        and x.get("days_data", 0) >= 365
        and x.get("coin") not in ALL_EXISTING_COINS
        and x.get("strategy") in COMBO_TO_ENGINE
    ]

    # Best combo per coin
    best_per_coin: dict[str, dict] = {}
    for x in candidates_raw:
        coin = x["coin"]
        if coin not in best_per_coin or x["pf"] > best_per_coin[coin]["pf"]:
            best_per_coin[coin] = x

    items = sorted(best_per_coin.values(), key=lambda x: -x["pf"])
    print(f"\nTotal mega100 candidates to verify: {len(items)} coins")
    print("  (new coins only, PF>=1.5 in sweep, days_data>=365)")

    # Print candidate table
    print(f"\n  {'Coin':<12} {'Sweep Strategy':<25} {'SID':<5} {'Sweep PF':>8} {'Trades':>7} {'Days':>6}  Engine Signal")
    print(f"  {'-'*12} {'-'*25} {'-'*5} {'-'*8} {'-'*7} {'-'*6}  {'-'*20}")
    for x in items:
        engine_sig = COMBO_TO_ENGINE.get(x["strategy"], "???")
        print(
            f"  {x['coin']:<12} {x['strategy']:<25} {x['strategy_id']:<5} "
            f"{x['pf']:>8.2f} {x['trades']:>7} {x['days_data']:>6}  {engine_sig}"
        )

    verification_results: list[dict] = []
    passed: list[dict] = []

    for idx, row in enumerate(items, 1):
        coin = row["coin"]
        strategy_name = row["strategy"]
        strategy_id = row["strategy_id"]
        signal_key = COMBO_TO_ENGINE[strategy_name]
        prefix = row.get("prefix", coin.lower() + "usdt")
        sweep_pf = row["pf"]
        sweep_trades = row["trades"]
        sweep_days = row["days_data"]

        # Use sweep's SL/TP from meta (all combos used same exit)
        meta = sweep_data.get("meta", {})
        atr_sl_mult = float(meta.get("sl_mult", 2.0))
        atr_tp_mult = float(meta.get("tp_mult", 4.0))
        atr_trail_mult = 3.0  # Standard trail

        if idx % 10 == 0 or idx == len(items):
            print(f"\n  Progress: {idx}/{len(items)}...")

        # Use 1H timeframe (all mega100 combos ran on 1H)
        tf = "1h"

        # Check data availability
        signal_df = load_signal_data(prefix, tf)
        if signal_df.empty or len(signal_df) < 100:
            verification_results.append({
                "coin": coin, "strategy": strategy_name, "strategy_id": strategy_id,
                "signal_key": signal_key, "tf": tf,
                "sweep_pf": sweep_pf, "sweep_trades": sweep_trades, "sweep_days": sweep_days,
                "engine_pf": None, "wr": None, "trades": None, "sharpe": None, "dd": None,
                "status": "SKIP_NO_DATA",
                "prefix": prefix, "atr_sl_mult": atr_sl_mult,
                "atr_tp_mult": atr_tp_mult, "atr_trail_mult": atr_trail_mult,
            })
            continue

        span = data_span_months(signal_df)
        if span < 12.0:
            verification_results.append({
                "coin": coin, "strategy": strategy_name, "strategy_id": strategy_id,
                "signal_key": signal_key, "tf": tf,
                "sweep_pf": sweep_pf, "sweep_trades": sweep_trades, "sweep_days": sweep_days,
                "engine_pf": None, "wr": None, "trades": None, "sharpe": None, "dd": None,
                "status": "SKIP_SHORT_DATA",
                "prefix": prefix, "atr_sl_mult": atr_sl_mult,
                "atr_tp_mult": atr_tp_mult, "atr_trail_mult": atr_trail_mult,
            })
            continue

        # Extra signal-specific config params
        extra_params: dict = {}
        if signal_key == "zscore_meanrev":
            extra_params["zscore_atr_sl_mult"] = atr_sl_mult
            extra_params["zscore_atr_tp_mult"] = atr_tp_mult
        elif signal_key == "dual_thrust":
            extra_params["dual_thrust_k"] = 0.5
            extra_params["dual_thrust_lookback"] = 20
        elif signal_key == "range_bounce":
            extra_params["range_bounce_atr_sl_mult"] = atr_sl_mult
            extra_params["range_bounce_atr_tp_mult"] = atr_tp_mult
            extra_params["mr_atr_sl_mult"] = atr_sl_mult
            extra_params["mr_atr_tp_mult"] = atr_tp_mult

        _, metrics = run_engine_for_bot(
            coin, signal_key, tf, atr_sl_mult, atr_tp_mult, atr_trail_mult,
            f"{strategy_name}", prefix,
            extra_params=extra_params, filter_year=True,
        )

        if not metrics:
            verification_results.append({
                "coin": coin, "strategy": strategy_name, "strategy_id": strategy_id,
                "signal_key": signal_key, "tf": tf,
                "sweep_pf": sweep_pf, "sweep_trades": sweep_trades, "sweep_days": sweep_days,
                "engine_pf": 0.0, "wr": 0.0, "trades": 0, "sharpe": 0.0, "dd": 0.0,
                "status": "FAIL_NO_TRADES",
                "prefix": prefix, "atr_sl_mult": atr_sl_mult,
                "atr_tp_mult": atr_tp_mult, "atr_trail_mult": atr_trail_mult,
            })
            continue

        engine_pf = metrics["pf"]
        engine_trades = metrics["trades"]
        status = "PASS" if engine_pf >= 1.2 and engine_trades >= 8 else "FAIL"

        result = {
            "coin": coin,
            "strategy": strategy_name,
            "strategy_id": strategy_id,
            "signal_key": signal_key,
            "tf": tf,
            "sweep_pf": round(sweep_pf, 3),
            "sweep_trades": sweep_trades,
            "sweep_days": sweep_days,
            "engine_pf": engine_pf,
            "wr": metrics["wr"],
            "trades": engine_trades,
            "sharpe": metrics["sharpe"],
            "dd": metrics["dd"],
            "status": status,
            "prefix": prefix,
            "atr_sl_mult": atr_sl_mult,
            "atr_tp_mult": atr_tp_mult,
            "atr_trail_mult": atr_trail_mult,
        }
        verification_results.append(result)

        if status == "PASS":
            passed.append(result)
            print(
                f"  PASS  {coin:<12} {strategy_name:<25} -> {signal_key:<20} "
                f"sweep={sweep_pf:.2f} engine={engine_pf:.2f} "
                f"trades={engine_trades} WR={metrics['wr']:.0f}%"
            )
        else:
            print(
                f"  FAIL  {coin:<12} {strategy_name:<25} -> {signal_key:<20} "
                f"sweep={sweep_pf:.2f} engine={engine_pf:.2f} trades={engine_trades}"
            )

    return passed, verification_results


def print_verification_report(
    verification_results: list[dict],
    passed: list[dict],
) -> None:
    """Print a formatted verification summary."""
    print(f"\n{'=' * 80}")
    print("MEGA100 ENGINE VERIFICATION RESULTS")
    print(f"{'=' * 80}")
    print(
        f"{'Coin':<12} {'Sweep Strategy':<25} {'Engine Signal':<22} "
        f"{'Sweep PF':>8} {'Eng PF':>7} {'Trades':>7} {'WR%':>5}  Status"
    )
    print("-" * 93)

    for r in sorted(verification_results, key=lambda x: -(x.get("engine_pf") or -99)):
        if r["status"] in ("SKIP_NO_DATA", "SKIP_SHORT_DATA"):
            status_str = r["status"]
        elif r["status"] == "FAIL_NO_TRADES":
            status_str = "FAIL (0 trades)"
        elif r["status"] == "PASS":
            status_str = "PASS"
        else:
            status_str = "FAIL"

        engine_pf_str = f"{r['engine_pf']:.2f}" if r.get("engine_pf") is not None else "  N/A"
        trades_str = str(r.get("trades") or "")
        wr_str = f"{r['wr']:.0f}" if r.get("wr") is not None else ""

        print(
            f"  {r['coin']:<12} {r['strategy']:<25} {r.get('signal_key', ''):<22} "
            f"{r['sweep_pf']:>8.2f} {engine_pf_str:>7} {trades_str:>7} {wr_str:>5}  {status_str}"
        )

    skipped = [r for r in verification_results if r["status"].startswith("SKIP")]
    failed = [r for r in verification_results if r["status"].startswith("FAIL")]
    print(f"\n  Passed:  {len(passed)}")
    print(f"  Failed:  {len(failed)}")
    print(f"  Skipped: {len(skipped)}")
    print(f"  Total:   {len(verification_results)}")

    if passed:
        print(f"\n  TOP PASSES (sorted by engine PF):")
        for r in sorted(passed, key=lambda x: -x.get("engine_pf", 0)):
            print(
                f"    {r['coin']:<12} {r['signal_key']:<22} "
                f"PF={r['engine_pf']:.2f}  WR={r['wr']:.0f}%  "
                f"Trades={r['trades']}  Sharpe={r['sharpe']:.2f}  DD={r['dd']:.1f}%"
            )


# ---------------------------------------------------------------------------
# Part 2: Realistic portfolio backtest
# ---------------------------------------------------------------------------


def run_portfolio_backtest(
    new_verified_bots: list[dict],
    initial_wallet: float = 200.0,
    max_concurrent: int = 5,
) -> dict:
    """Run realistic $200 portfolio backtest combining ALL rounds.

    Methodology (R-multiple replay):
    1. Each bot runs independently on 10K virtual balance to collect trades.
    2. R-multiple = trade_pnl / (10K * risk_pct).
    3. All trades across bots sorted by entry_time.
    4. Dollar PnL = shared_wallet * bot_risk_pct * R_multiple.
    5. Max 5 concurrent positions enforced.
    """
    print(f"\n{'=' * 80}")
    print("PART 2: REALISTIC PORTFOLIO BACKTEST")
    print(f"Wallet: ${initial_wallet:.0f}  |  Max concurrent: {max_concurrent}")
    print(f"{'=' * 80}")

    all_trade_records: list[dict] = []

    def collect_trades(
        coin: str,
        strategy_key: str,
        tf: str,
        sl: float,
        tp: float,
        trail: float,
        label: str,
        prefix: str,
        risk_pct: float,
        extra_params: Optional[dict] = None,
    ) -> int:
        """Run engine for one bot and append R-multiple trade records."""
        signal_df = load_signal_data(prefix, tf)
        if signal_df.empty:
            return 0

        span = data_span_months(signal_df)
        if span < 12.0:
            return 0

        trend_df = load_trend_data(prefix, tf)
        signal_df_yr = filter_last_year(signal_df)
        trend_df_yr = filter_last_year(trend_df) if not trend_df.empty else trend_df

        cfg = build_config(coin, strategy_key, tf, sl, tp, trail, prefix, extra_params)
        cfg["risk_per_trade"] = risk_pct

        try:
            engine = BacktestEngine(cfg, initial_balance=10_000.0)
            engine.run(signal_df_yr, trend_df_yr if not trend_df_yr.empty else None)
        except Exception as exc:
            print(f"  ERROR {coin} ({label}): {exc}")
            return 0

        risk_amount = 10_000.0 * risk_pct
        n = 0
        for t in engine.state.trades:
            try:
                entry_ts = pd.Timestamp(t.entry_time)
                if entry_ts.tzinfo is not None:
                    entry_ts = entry_ts.tz_localize(None)
                exit_ts = pd.Timestamp(t.exit_time)
                if exit_ts.tzinfo is not None:
                    exit_ts = exit_ts.tz_localize(None)
            except Exception:
                continue

            r_mult = t.pnl / risk_amount if risk_amount > 0 else 0.0
            all_trade_records.append({
                "bot": f"{coin} ({label})",
                "coin": coin,
                "side": t.side,
                "entry_time": entry_ts,
                "exit_time": exit_ts,
                "r_mult": r_mult,
                "risk_pct": risk_pct,
                "close_reason": t.close_reason,
            })
            n += 1
        return n

    # --- Existing bots (prior rounds, hardcoded) ---
    print("\nRunning existing bots (prior rounds)...")
    for coin, strategy_key, tf, sl, tp, trail, label, prefix, risk_pct in EXISTING_BOTS:
        n = collect_trades(coin, strategy_key, tf, sl, tp, trail, label, prefix, risk_pct)
        print(f"  {coin:<12} {label:<18} {n:>3} trades")

    # --- Round 10 verified bots ---
    round10_path = DATA_DIR / "round10_results.json"
    if round10_path.exists():
        with open(round10_path) as f:
            r10 = json.load(f)
        r10_passed = [
            x for x in r10.get("passed", [])
            if x.get("engine_pf", 0) >= PRIOR_ROUND_MIN_PF
            and x.get("trades", 0) >= PRIOR_ROUND_MIN_TRADES
            and x["coin"] not in ALL_EXISTING_COINS
        ]
        if r10_passed:
            print(f"\nRunning {len(r10_passed)} Round 10 bots...")
        for vbot in r10_passed:
            coin = vbot["coin"]
            signal_key = vbot["signal_key"]
            tf = vbot["tf"]
            prefix = vbot.get("prefix", coin.lower() + "usdt")
            params = vbot.get("params", {})
            atr_sl = vbot.get("atr_sl_mult", 1.5)
            atr_tp = vbot.get("atr_tp_mult", 2.0)
            extra: dict = {}
            if signal_key == "dual_thrust":
                extra["dual_thrust_k"] = params.get("k1", 0.5)
                extra["dual_thrust_lookback"] = params.get("lookback", 20)
            elif signal_key == "range_bounce":
                extra["range_bounce_lookback"] = params.get("range_lookback", 20)
                extra["range_bounce_rsi_lo"] = params.get("rsi_lo", 35)
                extra["range_bounce_rsi_hi"] = params.get("rsi_hi", 65)
                extra["range_bounce_atr_sl_mult"] = atr_sl
                extra["range_bounce_atr_tp_mult"] = atr_tp
                extra["mr_atr_sl_mult"] = atr_sl
                extra["mr_atr_tp_mult"] = atr_tp
            n = collect_trades(coin, signal_key, tf, atr_sl, atr_tp, 2.5,
                               f"{signal_key[:8]} R10", prefix, 0.01, extra)
            print(f"  {coin:<12} R10 {signal_key[:10]:<12} {n:>3} trades")

    # --- Round 11 verified bots ---
    round11_path = DATA_DIR / "round11_results.json"
    if round11_path.exists():
        with open(round11_path) as f:
            r11 = json.load(f)
        r11_passed = [
            x for x in r11.get("passed", [])
            if x.get("engine_pf", 0) >= PRIOR_ROUND_MIN_PF
            and x.get("trades", 0) >= PRIOR_ROUND_MIN_TRADES
            and x["coin"] not in ALL_EXISTING_COINS
        ]
        if r11_passed:
            print(f"\nRunning {len(r11_passed)} Round 11 bots...")
        for vbot in r11_passed:
            coin = vbot["coin"]
            signal_key = vbot["signal_key"]
            tf = vbot["tf"]
            prefix = vbot.get("prefix", coin.lower() + "usdt")
            atr_sl = vbot.get("atr_sl_mult", 2.0)
            atr_tp = vbot.get("atr_tp_mult", 3.0)
            atr_trail = vbot.get("atr_trail_mult") or 2.5
            extra = {}
            if signal_key == "zscore_meanrev":
                extra["zscore_atr_sl_mult"] = atr_sl
                extra["zscore_atr_tp_mult"] = atr_tp
            n = collect_trades(coin, signal_key, tf, atr_sl, atr_tp, atr_trail,
                               f"{signal_key[:8]} R11", prefix, 0.01, extra)
            print(f"  {coin:<12} R11 {signal_key[:10]:<12} {n:>3} trades")

    # --- New mega100 verified bots ---
    new_eligible = [v for v in new_verified_bots if v["coin"] not in ALL_EXISTING_COINS]
    if new_eligible:
        print(f"\nRunning {len(new_eligible)} new mega100 bots...")
    for vbot in new_eligible:
        coin = vbot["coin"]
        signal_key = vbot["signal_key"]
        tf = vbot["tf"]
        prefix = vbot.get("prefix", coin.lower() + "usdt")
        atr_sl = vbot.get("atr_sl_mult", 2.0)
        atr_tp = vbot.get("atr_tp_mult", 4.0)
        atr_trail = vbot.get("atr_trail_mult", 3.0) or 3.0
        extra = {}
        if signal_key == "zscore_meanrev":
            extra["zscore_atr_sl_mult"] = atr_sl
            extra["zscore_atr_tp_mult"] = atr_tp
        elif signal_key == "dual_thrust":
            extra["dual_thrust_k"] = 0.5
            extra["dual_thrust_lookback"] = 20
        elif signal_key == "range_bounce":
            extra["range_bounce_atr_sl_mult"] = atr_sl
            extra["range_bounce_atr_tp_mult"] = atr_tp
            extra["mr_atr_sl_mult"] = atr_sl
            extra["mr_atr_tp_mult"] = atr_tp
        n = collect_trades(coin, signal_key, tf, atr_sl, atr_tp, atr_trail,
                           f"{signal_key[:8]} M100", prefix, 0.01, extra)
        print(f"  {coin:<12} M100 {signal_key[:10]:<12} {n:>3} trades")

    if not all_trade_records:
        print("No trade records collected — aborting portfolio backtest.")
        return {}

    # ---------------------------------------------------------------------------
    # R-multiple replay
    # ---------------------------------------------------------------------------
    all_df = pd.DataFrame(all_trade_records).sort_values("entry_time").reset_index(drop=True)

    balance = initial_wallet
    open_trades: list[dict] = []
    per_bot_stats: dict[str, dict] = defaultdict(lambda: {
        "trades": 0, "pnl": 0.0, "wins": 0, "r_total": 0.0,
    })
    monthly_pnl: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    daily_log: list[dict] = []
    _eq_peak = initial_wallet
    _max_dd_usd = 0.0

    def close_expired(current_time: pd.Timestamp) -> None:
        nonlocal balance, open_trades, _eq_peak, _max_dd_usd
        still_open = []
        for ot in open_trades:
            if ot["exit_time"] <= current_time:
                dollar_pnl = balance * ot["risk_pct"] * ot["r_mult"]
                balance += dollar_pnl
                balance = max(balance, 0.01)

                if balance > _eq_peak:
                    _eq_peak = balance
                dd_now = _eq_peak - balance
                if dd_now > _max_dd_usd:
                    _max_dd_usd = dd_now

                bot_name = ot["bot"]
                per_bot_stats[bot_name]["trades"] += 1
                per_bot_stats[bot_name]["pnl"] += dollar_pnl
                per_bot_stats[bot_name]["r_total"] += ot["r_mult"]
                if dollar_pnl > 0:
                    per_bot_stats[bot_name]["wins"] += 1

                month_key = ot["exit_time"].strftime("%Y-%m")
                monthly_pnl[month_key]["pnl"] += dollar_pnl
                monthly_pnl[month_key]["trades"] += 1
                monthly_pnl[month_key][bot_name] += dollar_pnl

                daily_log.append({
                    "date": ot["exit_time"].strftime("%Y-%m-%d"),
                    "bot": bot_name,
                    "side": ot["side"],
                    "r_mult": round(ot["r_mult"], 3),
                    "pnl": round(dollar_pnl, 2),
                    "balance": round(balance, 2),
                })
            else:
                still_open.append(ot)
        open_trades = still_open

    for _, row in all_df.iterrows():
        close_expired(row["entry_time"])
        if len(open_trades) >= max_concurrent:
            continue
        open_trades.append(row.to_dict())

    if open_trades:
        last_exit = max(t["exit_time"] for t in open_trades)
        close_expired(last_exit + pd.Timedelta(seconds=1))

    # ---------------------------------------------------------------------------
    # Reports
    # ---------------------------------------------------------------------------
    total_trades = sum(s["trades"] for s in per_bot_stats.values())
    total_pnl = balance - initial_wallet
    total_return_pct = total_pnl / initial_wallet * 100.0
    max_dd_pct = (_max_dd_usd / _eq_peak * 100.0) if _eq_peak > 0 else 0.0

    print(f"\n{'=' * 80}")
    print("REPORT 1: PER-BOT SUMMARY")
    print(f"{'=' * 80}")
    print(f"{'Bot':<32} {'Trades':>6}  {'WR%':>5}  {'Avg R':>6}  {'PnL$':>9}")
    print("-" * 65)
    for bot_name, stats in sorted(per_bot_stats.items(), key=lambda x: -x[1]["pnl"]):
        n = stats["trades"]
        if n == 0:
            continue
        wr = stats["wins"] / n * 100.0
        avg_r = stats["r_total"] / n
        print(f"  {bot_name:<30} {n:>6}  {wr:>5.1f}  {avg_r:>+6.3f}  {stats['pnl']:>+9.2f}")

    print(f"\n{'=' * 80}")
    print("REPORT 2: MONTHLY BREAKDOWN (1 year)")
    print(f"{'=' * 80}")
    print(f"{'Month':<10}  {'Trades':>6}  {'PnL$':>9}  {'Balance$':>10}")
    print("-" * 44)

    running_bal = initial_wallet
    monthly_report_rows = []
    for month_key in sorted(monthly_pnl.keys()):
        m = monthly_pnl[month_key]
        month_pnl = m["pnl"]
        month_trades = int(m["trades"])
        running_bal += month_pnl
        print(f"{month_key:<10}  {month_trades:>6}  {month_pnl:>+9.2f}  ${running_bal:>9.2f}")

        bot_rows = [(k, v) for k, v in m.items() if k not in ("pnl", "trades") and v != 0]
        for bot_name, bot_pnl in sorted(bot_rows, key=lambda x: -abs(x[1]))[:5]:
            print(f"  {'':8}  {bot_name:<30}  {bot_pnl:>+9.2f}")

        monthly_report_rows.append({
            "month": month_key,
            "trades": month_trades,
            "pnl": round(month_pnl, 2),
            "balance": round(running_bal, 2),
        })

    print(f"\n{'=' * 80}")
    print("REPORT 3: DAILY LOG (last 2 months)")
    print(f"{'=' * 80}")
    print(f"{'Date':<12}  {'Bot':<32}  {'Side':<6}  {'R':>6}  {'PnL$':>8}  {'Balance$':>10}")
    print("-" * 82)

    if daily_log:
        log_df = pd.DataFrame(daily_log)
        cutoff_date = (
            pd.to_datetime(log_df["date"]).max() - pd.DateOffset(months=2)
        ).strftime("%Y-%m-%d")
        recent_log = log_df[log_df["date"] >= cutoff_date]
        for _, row in recent_log.iterrows():
            print(
                f"{row['date']:<12}  {row['bot']:<32}  {row['side']:<6}  "
                f"{row['r_mult']:>+6.3f}  {row['pnl']:>+8.2f}  ${row['balance']:>9.2f}"
            )

    print(f"\n{'=' * 80}")
    print("PORTFOLIO SUMMARY")
    print(f"{'=' * 80}")
    print(f"  Initial balance:  ${initial_wallet:.2f}")
    print(f"  Final balance:    ${balance:.2f}")
    print(f"  Total return:     {total_return_pct:+.1f}%  (${total_pnl:+.2f})")
    print(f"  Total trades:     {total_trades}")
    print(f"  Unique bots:      {len(per_bot_stats)}")
    print(f"  Max drawdown:     {max_dd_pct:.1f}%  (${_max_dd_usd:.2f} from peak ${_eq_peak:.2f})")

    return {
        "final_balance": round(balance, 2),
        "total_return_pct": round(total_return_pct, 2),
        "total_trades": total_trades,
        "max_dd_pct": round(max_dd_pct, 1),
        "per_bot": {k: dict(v) for k, v in per_bot_stats.items()},
        "monthly": monthly_report_rows,
        "daily": daily_log[-200:] if len(daily_log) > 200 else daily_log,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 80)
    print("MEGA100 — ENGINE VERIFICATION + PORTFOLIO BACKTEST")
    print("100 combination strategies, 1H timeframe, new coins only")
    print("=" * 80)

    # Part 1: Verify mega100 sweep winners
    passed, verification_results = verify_mega100_winners()
    print_verification_report(verification_results, passed)

    # Part 2: Portfolio backtest
    portfolio = run_portfolio_backtest(passed, initial_wallet=200.0, max_concurrent=5)

    # Save results
    out = {
        "verification": verification_results,
        "passed": passed,
        "portfolio_summary": portfolio,
    }
    out_path = DATA_DIR / "mega100_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
