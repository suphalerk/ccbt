"""Round 9 — engine verification + combined portfolio backtest.

Part 1: Verify 78 new coins from sweep_round9.json with full BacktestEngine.
Part 2: Shared $200 wallet backtest combining 47 existing bots + new verified coins.
"""

import copy
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
logging.basicConfig(level=logging.WARNING)

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

DATA_DIR = Path("/Users/iceai/Work/ccbt/data")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEPLOYED = {
    "BTC", "WIF", "ARC", "AVAX", "NEAR", "POL", "GUN", "BERA", "ATH", "INJ",
    "TRUMP", "ANIME", "ZETA", "1000SHIB", "TAO", "RENDER", "HBAR", "ARB",
    "ALGO", "TRX", "POLYX", "FET", "XLM", "SAHARA", "MSTR", "XAG",
    "1000PEPE", "WLD", "APT", "LTC", "LIGHT", "QNT", "LYN", "SIGN", "ENJ",
    "TIA", "XPL", "IP", "ONDO", "LINK", "XRP", "SUI", "AKT", "H", "HUMA", "AXS",
}

# Strategy name (from sweep) → engine signal key (None = not implemented, skip)
STRATEGY_MAP: dict[str, Optional[str]] = {
    "ADX+DI Cross":       "adx_di_cross",
    "Choppiness+EMA":     "choppiness_ema",
    "WilliamsR+ADX":      "williams_r_adx",
    "ROC Momentum":       "roc_momentum",
    "Stoch+Supertrend":   "stoch_supertrend",
    "PriceChannel+Vol":   "price_channel_vol",
    "EMA+Alligator":      "ema_alligator",
    "Supertrend+Volume":  "supertrend_volume",
    # Not implemented in engine — skip
    "CMF Cross":          None,
    "TRIX Cross":         None,
    "LinReg Breakout":    None,
    "HeikinAshi Trend":   None,
    "Donchian+ADX":       None,
    "Vortex Cross":       None,
    "KAMA Cross":         None,
    "DEMA Cross":         None,
    "RSI+Ichimoku":       None,
    "OBV Breakout":       None,
    "HullMA Cross":       None,
    "Keltner+ADX":        None,
}

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
    "ema_slope_min": 0.02,
    "ichimoku_tenkan": 9,
    "ichimoku_kijun": 26,
    "ichimoku_senkou_b": 52,
    "vol_expansion_threshold": 1.8,
    "vol_expansion_lookback": 1,
    "vol_expansion_atr_ma_period": 20,
    "supertrend_multiplier": 2.0,
    "trading_hours": {"enabled": True, "start_utc": 3, "end_utc": 20},
    "regime_filter": {"enabled": True, "skip_ranging": True},
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
    "mr_atr_sl_mult": 1.0,
    "mr_atr_tp_mult": 1.5,
    "bb_period": 20,
    "bb_std": 2.0,
    "bb_squeeze_percentile": 0.3,
    "bb_volume_mult": 1.2,
    "swing_lookback": 5,
    "divergence_lookback": 20,
    "regime_lookback": 20,
}

# ---------------------------------------------------------------------------
# Existing 47 bots (coin, strategy_key, tf, sl, tp, trail, label, prefix)
# ---------------------------------------------------------------------------

EXISTING_BOTS: list[tuple] = [
    # EMA 15m
    ("BTC",      "ema_crossover",       "15m", 1.0, 3.0, 2.0, "EMA 15m",      "btcusdt"),
    ("WIF",      "ema_crossover",       "15m", 1.0, 3.0, 2.0, "EMA 15m",      "wifusdt"),
    ("ARC",      "ema_crossover",       "15m", 1.0, 3.0, 2.0, "EMA 15m",      "arcusdt"),
    # Ichi 1H
    ("AVAX",     "ichimoku_cloud",      "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "avaxusdt"),
    ("POL",      "ichimoku_cloud",      "1h",  1.0, 0.0, 3.0, "Ichi 1H Trail","polusdt"),
    ("GUN",      "ichimoku_cloud",      "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "gunusdt"),
    ("BERA",     "ichimoku_cloud",      "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "berausdt"),
    ("ATH",      "ichimoku_cloud",      "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "athusdt"),
    ("INJ",      "ichimoku_cloud",      "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "injusdt"),
    ("TRUMP",    "ichimoku_cloud",      "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "trumpusdt"),
    ("ANIME",    "ichimoku_cloud",      "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "animeusdt"),
    ("IP",       "ichimoku_cloud",      "1h",  2.0, 4.0, 3.0, "Ichi 1H",      "ipusdt"),
    # Ichi 4H
    ("1000SHIB", "ichimoku_cloud",      "4h",  3.0, 5.0, 3.0, "Ichi 4H",      "1000shibusdt"),
    ("TAO",      "ichimoku_cloud",      "4h",  2.5, 4.0, 3.0, "Ichi 4H",      "taousdt"),
    ("RENDER",   "ichimoku_cloud",      "4h",  2.5, 4.0, 3.0, "Ichi 4H",      "renderusdt"),
    ("ARB",      "ichimoku_cloud",      "4h",  3.0, 4.0, 2.0, "Ichi 4H",      "arbusdt"),
    ("SIGN",     "ichimoku_cloud",      "4h",  2.0, 4.0, 3.0, "Ichi 4H",      "signusdt"),
    ("TIA",      "ichimoku_cloud",      "4h",  2.0, 4.0, 3.0, "Ichi 4H",      "tiausdt"),
    ("ONDO",     "ichimoku_cloud",      "4h",  2.0, 4.0, 3.0, "Ichi 4H",      "ondousdt"),
    ("H",        "ichimoku_cloud",      "4h",  2.0, 4.0, 3.0, "Ichi 4H",      "husdt"),
    ("AXS",      "ichimoku_cloud",      "4h",  2.0, 4.0, 3.0, "Ichi 4H",      "axsusdt"),
    # 4H Trail
    ("ALGO",     "ichimoku_cloud",      "4h",  1.5, 0.0, 4.0, "4H Trail",     "algousdt"),
    ("TRX",      "ichimoku_cloud",      "4h",  1.5, 0.0, 4.0, "4H Trail",     "trxusdt"),
    ("POLYX",    "ichimoku_cloud",      "4h",  1.5, 0.0, 4.0, "4H Trail",     "polyxusdt"),
    ("FET",      "ichimoku_cloud",      "4h",  1.5, 0.0, 4.0, "4H Trail",     "fetusdt"),
    ("XLM",      "ichimoku_cloud",      "4h",  1.0, 0.0, 4.0, "4H Trail",     "xlmusdt"),
    ("SAHARA",   "ichimoku_cloud",      "4h",  3.0, 0.0, 4.0, "4H Trail",     "saharausdt"),
    # Supertrend
    ("MSTR",     "supertrend",          "1h",  2.0, 3.0, 2.5, "Supertrend",   "mstrusdt"),
    ("XAG",      "supertrend",          "1h",  2.0, 3.0, 2.5, "Supertrend",   "xagusdt"),
    # VolExp
    ("1000PEPE", "vol_expansion",       "1h",  2.5, 3.0, 2.5, "VolExp",       "1000pepeusdt"),
    ("WLD",      "vol_expansion",       "1h",  2.5, 3.0, 2.5, "VolExp",       "wldusdt"),
    # EMA+Ichi 4H
    ("APT",      "ema_ichimoku_hybrid", "4h",  1.5, 4.0, 2.0, "EMA+Ichi 4H", "aptusdt"),
    ("NEAR",     "ema_ichimoku_hybrid", "4h",  1.5, 4.0, 2.0, "EMA+Ichi 4H", "nearusdt"),
    ("ZETA",     "ema_ichimoku_hybrid", "4h",  1.5, 4.0, 2.0, "EMA+Ichi 4H", "zetausdt"),
    # Ichi+ST 4H
    ("LTC",      "ichi_supertrend",     "4h",  2.0, 5.0, 3.0, "Ichi+ST 4H",  "ltcusdt"),
    # Alligator
    ("LIGHT",    "alligator",           "1h",  2.0, 4.0, 3.0, "Alligator 1H","lightusdt"),
    ("HBAR",     "alligator",           "4h",  2.0, 4.0, 3.0, "Alligator 4H","hbarusdt"),
    ("QNT",      "alligator",           "4h",  2.0, 4.0, 3.0, "Alligator 4H","qntusdt"),
    ("ARC_A",    "alligator",           "4h",  2.0, 4.0, 3.0, "Alligator 4H","arcusdt"),
    ("LINK",     "alligator",           "4h",  2.0, 4.0, 3.0, "Alligator 4H","linkusdt"),
    ("SUI",      "alligator",           "4h",  2.0, 4.0, 3.0, "Alligator 4H","suiusdt"),
    # Dual ST
    ("LYN",      "dual_supertrend",     "1h",  2.5, 4.0, 3.0, "Dual ST 1H",  "lynusdt"),
    ("HUMA",     "dual_supertrend",     "1h",  2.5, 4.0, 3.0, "Dual ST 1H",  "humausdt"),
    ("ENJ",      "dual_supertrend",     "4h",  2.5, 4.0, 3.0, "Dual ST 4H",  "enjusdt"),
    ("XPL",      "dual_supertrend",     "4h",  2.5, 4.0, 3.0, "Dual ST 4H",  "xplusdt"),
    ("XRP",      "dual_supertrend",     "4h",  2.5, 4.0, 3.0, "Dual ST 4H",  "xrpusdt"),
    ("AKT",      "dual_supertrend",     "4h",  2.5, 4.0, 3.0, "Dual ST 4H",  "aktusdt"),
]

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

    signals = copy.deepcopy(ALL_SIGNALS_OFF)
    if strategy_key == "ema_crossover":
        signals["ema_crossover"] = {"enabled": True}
        signals["ema_fast_crossover"] = {"enabled": True}
    else:
        signals[strategy_key] = {"enabled": True}
    cfg["signals"] = signals
    return cfg


# ---------------------------------------------------------------------------
# Data loading
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


# ---------------------------------------------------------------------------
# Run one bot and collect trade records
# ---------------------------------------------------------------------------


def run_bot(
    coin: str,
    strategy_key: str,
    tf_signal: str,
    atr_sl_mult: float,
    atr_tp_mult: float,
    atr_trail_mult: float,
    label: str,
    data_prefix: str,
) -> list[dict]:
    """Run BacktestEngine for one bot and return trade list."""
    cfg = build_config(coin, strategy_key, tf_signal, atr_sl_mult, atr_tp_mult, atr_trail_mult, data_prefix)

    signal_df = load_signal_data(data_prefix, tf_signal)
    if signal_df.empty:
        return []

    trend_df = load_trend_data(data_prefix, tf_signal)

    signal_df = filter_last_year(signal_df)
    if not trend_df.empty:
        trend_df = filter_last_year(trend_df)

    if len(signal_df) < 50:
        return []

    try:
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        engine.run(signal_df, trend_df if not trend_df.empty else None)
    except Exception as exc:
        print(f"  [ENGINE ERROR] {coin} ({label}): {exc}")
        return []

    initial_balance = 10_000.0
    trades = []
    for t in engine.state.trades:
        pnl_frac = t.pnl / initial_balance
        try:
            exit_ts = pd.Timestamp(t.exit_time, tz="UTC") if t.exit_time else None
        except Exception:
            exit_ts = None
        try:
            entry_ts = pd.Timestamp(t.entry_time, tz="UTC") if t.entry_time else None
        except Exception:
            entry_ts = None
        trades.append({
            "coin": coin,
            "label": label,
            "strategy": strategy_key,
            "side": t.side,
            "entry_time": entry_ts,
            "exit_time": exit_ts,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "close_reason": t.close_reason,
            "pnl_frac": pnl_frac,
        })
    return trades


def compute_engine_metrics(trades_raw: list) -> dict:
    """Compute PF, WR, trades, Sharpe, max DD from engine trade objects."""
    if not trades_raw:
        return {"pf": 0.0, "wr": 0.0, "trades": 0, "sharpe": 0.0, "dd": 0.0}

    wins = [t.pnl for t in trades_raw if t.pnl > 0]
    losses = [t.pnl for t in trades_raw if t.pnl <= 0]
    total = len(trades_raw)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    wr = len(wins) / total * 100.0 if total > 0 else 0.0

    # Max drawdown
    equity = 10_000.0
    peak = equity
    max_dd = 0.0
    for t in trades_raw:
        equity += t.pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualised, daily returns)
    import numpy as np
    pnl_vals = [t.pnl / 10_000.0 for t in trades_raw]
    if len(pnl_vals) > 1:
        mean_r = float(np.mean(pnl_vals))
        std_r = float(np.std(pnl_vals, ddof=1))
        # Approximate: assume trades spread over 252 trading days
        trades_per_day = len(pnl_vals) / 252.0
        sharpe = (mean_r / std_r * (trades_per_day ** 0.5) * (252.0 ** 0.5)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    return {"pf": round(pf, 2), "wr": round(wr, 1), "trades": total,
            "sharpe": round(sharpe, 2), "dd": round(max_dd, 1)}


# ---------------------------------------------------------------------------
# Part 1: Verify new coins
# ---------------------------------------------------------------------------


def verify_new_coins() -> tuple[list[dict], list[dict]]:
    """Verify all 78 new coins. Returns (passed, all_results)."""
    with open(DATA_DIR / "sweep_round9.json") as f:
        sweep = json.load(f)

    # Best strategy per non-deployed coin (PF >= 1.3, trades >= 6)
    good = [
        r for r in sweep
        if r["pf"] >= 1.3 and r["trades"] >= 6 and r["coin"] not in DEPLOYED
        # Skip non-ASCII coin names (no data file possible)
        and all(ord(c) < 128 for c in r["coin"])
    ]
    best_per_coin: dict[str, dict] = {}
    for r in good:
        if r["coin"] not in best_per_coin or r["pf"] > best_per_coin[r["coin"]]["pf"]:
            best_per_coin[r["coin"]] = r

    new_coins = sorted(best_per_coin.values(), key=lambda x: -x["pf"])
    print(f"\nNew coins to verify: {len(new_coins)}")

    verification_results: list[dict] = []
    passed: list[dict] = []

    for idx, sweep_row in enumerate(new_coins, 1):
        coin = sweep_row["coin"]
        strategy = sweep_row["strategy"]
        timeframe = sweep_row["timeframe"].lower()
        prefix = sweep_row.get("prefix", coin.lower() + "usdt")
        sl = sweep_row["sl_mult"]
        tp = sweep_row["tp_mult"]
        trail = sweep_row["trail_mult"]
        sweep_pf = sweep_row["pf"]
        sweep_trades = sweep_row["trades"]

        if idx % 10 == 0:
            print(f"  Progress: {idx}/{len(new_coins)} coins verified...")

        signal_key = STRATEGY_MAP.get(strategy)

        if signal_key is None:
            result = {
                "coin": coin,
                "strategy": strategy,
                "signal_key": None,
                "timeframe": timeframe,
                "sweep_pf": sweep_pf,
                "sweep_trades": sweep_trades,
                "engine_pf": None,
                "wr": None,
                "trades": None,
                "sharpe": None,
                "dd": None,
                "prefix": prefix,
                "status": "SKIP_NOT_IMPL",
            }
            verification_results.append(result)
            continue

        # Check data file
        signal_df = load_signal_data(prefix, timeframe)
        if signal_df.empty:
            result = {
                "coin": coin,
                "strategy": strategy,
                "signal_key": signal_key,
                "timeframe": timeframe,
                "sweep_pf": sweep_pf,
                "sweep_trades": sweep_trades,
                "engine_pf": None,
                "wr": None,
                "trades": None,
                "sharpe": None,
                "dd": None,
                "prefix": prefix,
                "status": "SKIP_NO_DATA",
            }
            verification_results.append(result)
            continue

        # Use full 2-year window for verification (not last-year-only)
        trend_df = load_trend_data(prefix, timeframe)
        if len(signal_df) < 50:
            result = {
                "coin": coin,
                "strategy": strategy,
                "signal_key": signal_key,
                "timeframe": timeframe,
                "sweep_pf": sweep_pf,
                "sweep_trades": sweep_trades,
                "engine_pf": None,
                "wr": None,
                "trades": None,
                "sharpe": None,
                "dd": None,
                "prefix": prefix,
                "status": "SKIP_INSUFFICIENT_DATA",
            }
            verification_results.append(result)
            continue

        cfg = build_config(coin, signal_key, timeframe, sl, tp if tp > 0 else 0.0, max(trail, 2.0), prefix)

        try:
            engine = BacktestEngine(cfg, initial_balance=10_000.0)
            engine.run(signal_df, trend_df if not trend_df.empty else None)
            raw_trades = engine.state.trades
        except Exception as exc:
            result = {
                "coin": coin,
                "strategy": strategy,
                "signal_key": signal_key,
                "timeframe": timeframe,
                "sweep_pf": sweep_pf,
                "sweep_trades": sweep_trades,
                "engine_pf": None,
                "wr": None,
                "trades": None,
                "sharpe": None,
                "dd": None,
                "prefix": prefix,
                "status": f"ERROR: {exc}",
            }
            verification_results.append(result)
            continue

        metrics = compute_engine_metrics(raw_trades)

        passes = metrics["pf"] >= 1.2 and metrics["trades"] >= 4
        status = "PASS" if passes else "FAIL"

        result = {
            "coin": coin,
            "strategy": strategy,
            "signal_key": signal_key,
            "timeframe": timeframe,
            "sweep_pf": sweep_pf,
            "sweep_trades": sweep_trades,
            "engine_pf": metrics["pf"],
            "wr": metrics["wr"],
            "trades": metrics["trades"],
            "sharpe": metrics["sharpe"],
            "dd": metrics["dd"],
            "sl_mult": sl,
            "tp_mult": tp,
            "trail_mult": max(trail, 2.0),
            "prefix": prefix,
            "status": status,
        }
        verification_results.append(result)

        if passes:
            passed.append(result)

    return passed, verification_results


def print_verification_report(results: list[dict]) -> None:
    """Print Report 1: verification table."""
    print("\n\n" + "=" * 100)
    print("ROUND 9 ENGINE VERIFICATION")
    print("=" * 100)
    print(f"{'#':<4} {'Coin':<12} {'Strategy':<22} {'TF':<4} {'Sweep_PF':<10} "
          f"{'Eng_PF':<8} {'WR%':<6} {'Trades':<8} {'Sharpe':<8} {'DD%':<6} {'Status'}")
    print("-" * 100)

    runnable = [r for r in results if r.get("signal_key") is not None]
    skipped = [r for r in results if r.get("signal_key") is None]

    # Sort: PASS first by engine_pf desc, then FAIL, then error
    def sort_key(r: dict) -> tuple:
        if r["status"] == "PASS":
            return (0, -(r["engine_pf"] or 0))
        elif r["status"] == "FAIL":
            return (1, -(r["engine_pf"] or 0))
        else:
            return (2, 0)

    sorted_r = sorted(runnable, key=sort_key)
    all_sorted = sorted_r + skipped

    for i, r in enumerate(all_sorted, 1):
        eng_pf = f"{r['engine_pf']:.2f}" if r.get("engine_pf") is not None else "  -  "
        wr = f"{r['wr']:.1f}" if r.get("wr") is not None else "  - "
        trades = str(r["trades"]) if r.get("trades") is not None else " - "
        sharpe = f"{r['sharpe']:.2f}" if r.get("sharpe") is not None else "  - "
        dd = f"{r['dd']:.1f}" if r.get("dd") is not None else " - "
        print(
            f"{i:<4} {r['coin']:<12} {r['strategy']:<22} {r['timeframe'].upper():<4} "
            f"{r['sweep_pf']:<10.2f} {eng_pf:<8} {wr:<6} {trades:<8} {sharpe:<8} {dd:<6} {r['status']}"
        )

    passed = [r for r in results if r["status"] == "PASS"]
    failed = [r for r in results if r["status"] == "FAIL"]
    skipped_impl = [r for r in results if r["status"] == "SKIP_NOT_IMPL"]
    skipped_data = [r for r in results if r["status"] in ("SKIP_NO_DATA", "SKIP_INSUFFICIENT_DATA")]
    errors = [r for r in results if r["status"].startswith("ERROR")]

    print("-" * 100)
    print(f"PASS: {len(passed)}  |  FAIL: {len(failed)}  |  SKIP (not impl): {len(skipped_impl)}  "
          f"|  SKIP (no data): {len(skipped_data)}  |  ERROR: {len(errors)}")
    print(f"\nPassed coins: {', '.join(r['coin'] for r in passed)}")


# ---------------------------------------------------------------------------
# Part 2: Shared wallet backtest
# ---------------------------------------------------------------------------


def replay_shared_wallet(all_trades: list[dict], initial_balance: float = 200.0) -> list[dict]:
    """Replay all trades chronologically, compounding shared balance."""
    sorted_trades = sorted(
        [t for t in all_trades if t["exit_time"] is not None],
        key=lambda t: t["exit_time"],
    )
    balance = initial_balance
    result = []
    for t in sorted_trades:
        dollar_pnl = balance * t["pnl_frac"]
        balance += dollar_pnl
        result.append({**t, "dollar_pnl": dollar_pnl, "balance": balance})
    return result


def print_per_bot_summary(replayed: list[dict], initial_balance: float, all_bot_rows: list[tuple]) -> None:
    """Per-bot stats table."""
    print("\n" + "=" * 80)
    print("PORTFOLIO BACKTEST — 1 Year, Shared $200 Wallet (All Bots)")
    print(f"Starting balance: ${initial_balance:.2f}")
    print("=" * 80)

    bot_stats: dict[str, dict] = {}
    for t in replayed:
        key = t["coin"]
        if key not in bot_stats:
            bot_stats[key] = {"coin": t["coin"], "label": t["label"], "trades": 0, "wins": 0, "total_pnl": 0.0}
        bot_stats[key]["trades"] += 1
        if t["dollar_pnl"] > 0:
            bot_stats[key]["wins"] += 1
        bot_stats[key]["total_pnl"] += t["dollar_pnl"]

    # Add zero-trade bots
    for row in all_bot_rows:
        coin = row[0]
        label = row[6]
        if coin not in bot_stats:
            bot_stats[coin] = {"coin": coin, "label": label, "trades": 0, "wins": 0, "total_pnl": 0.0}

    sorted_bots = sorted(bot_stats.values(), key=lambda x: x["total_pnl"], reverse=True)

    print(f"{'Bot':<14} {'Strategy':<18} {'Trades':>7} {'Wins':>5} {'WR%':>6}  {'PnL$':>10}")
    print("-" * 72)

    total_trades = total_wins = 0
    total_pnl = 0.0
    for bs in sorted_bots:
        wr = bs["wins"] / bs["trades"] * 100.0 if bs["trades"] > 0 else 0.0
        sign = "+" if bs["total_pnl"] >= 0 else ""
        print(
            f"{bs['coin']:<14} {bs['label']:<18} "
            f"{bs['trades']:>7} {bs['wins']:>5} {wr:>5.1f}%  {sign}{bs['total_pnl']:>9.2f}"
        )
        total_trades += bs["trades"]
        total_wins += bs["wins"]
        total_pnl += bs["total_pnl"]

    print("-" * 72)
    total_wr = total_wins / total_trades * 100.0 if total_trades > 0 else 0.0
    final_balance = initial_balance + total_pnl
    sign = "+" if total_pnl >= 0 else ""
    print(
        f"{'TOTAL':<14} {'':<18} "
        f"{total_trades:>7} {total_wins:>5} {total_wr:>5.1f}%  {sign}{total_pnl:>9.2f}"
    )
    print(f"\nFinal balance: ${final_balance:.2f}  ({sign}{total_pnl / initial_balance * 100:.1f}%)")


def print_monthly_report(replayed: list[dict], initial_balance: float) -> None:
    """Monthly PnL breakdown with per-bot detail."""
    print("\n\n" + "=" * 80)
    print("MONTHLY PORTFOLIO RETURNS ($200 shared wallet)")
    print("=" * 80)

    if not replayed:
        print("No trades.")
        return

    monthly_coin: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0}))
    for t in replayed:
        month = t["exit_time"].strftime("%Y-%m")
        monthly_coin[month][t["coin"]]["trades"] += 1
        if t["dollar_pnl"] > 0:
            monthly_coin[month][t["coin"]]["wins"] += 1
        monthly_coin[month][t["coin"]]["pnl"] += t["dollar_pnl"]

    balance = initial_balance
    for month in sorted(monthly_coin.keys()):
        coin_data = monthly_coin[month]
        month_total_pnl = sum(v["pnl"] for v in coin_data.values())
        month_trades = sum(v["trades"] for v in coin_data.values())
        month_wins = sum(v["wins"] for v in coin_data.values())
        balance += month_total_pnl
        month_wr = month_wins / month_trades * 100.0 if month_trades > 0 else 0.0
        sign = "+" if month_total_pnl >= 0 else ""

        print(f"\n--- {month}  (Month PnL: {sign}${month_total_pnl:.2f}  Balance: ${balance:.2f}  "
              f"Trades: {month_trades}  WR: {month_wr:.0f}%) ---")
        print(f"  {'Bot':<14} {'Trades':>7}  {'Wins':>5}  {'WR%':>6}  {'PnL$':>9}")
        for coin in sorted(coin_data.keys()):
            cd = coin_data[coin]
            wr = cd["wins"] / cd["trades"] * 100.0 if cd["trades"] > 0 else 0.0
            sign2 = "+" if cd["pnl"] >= 0 else ""
            print(f"  {coin:<14} {cd['trades']:>7}  {cd['wins']:>5}  {wr:>5.1f}%  {sign2}{cd['pnl']:>8.2f}")


def print_daily_report(replayed: list[dict]) -> None:
    """Daily trade log for last 2 months."""
    print("\n\n" + "=" * 80)
    print("DAILY REPORT (Last 2 months)")
    print("=" * 80)

    if not replayed:
        print("No trades.")
        return

    all_months = sorted({t["exit_time"].strftime("%Y-%m") for t in replayed})
    if len(all_months) >= 2:
        cutoff_month = all_months[-2]
    elif all_months:
        cutoff_month = all_months[-1]
    else:
        return

    recent = [t for t in replayed if t["exit_time"].strftime("%Y-%m") >= cutoff_month]

    print(f"\n{'Date':<12} {'Bot':<14} {'Side':<6} {'Entry$':>12} {'Exit$':>12} {'PnL$':>10}  {'Balance$':>10}")
    print("-" * 88)
    for t in recent:
        date = t["exit_time"].strftime("%Y-%m-%d")
        sign = "+" if t["dollar_pnl"] >= 0 else ""
        print(
            f"{date:<12} {t['coin']:<14} {t['side']:<6} "
            f"{t['entry_price']:>12.4f} {t['exit_price']:>12.4f} "
            f"{sign}{t['dollar_pnl']:>9.2f}  ${t['balance']:>9.2f}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # -------------------------------------------------------------------
    # Part 1: Verify new coins
    # -------------------------------------------------------------------
    print("=" * 80)
    print("PART 1: ROUND 9 ENGINE VERIFICATION")
    print("=" * 80)

    passed, all_verify_results = verify_new_coins()

    print_verification_report(all_verify_results)

    # Save verification results
    save_verify = []
    for r in all_verify_results:
        save_verify.append({k: v for k, v in r.items()})
    with open(DATA_DIR / "round9_verified.json", "w") as f:
        json.dump(save_verify, f, indent=2)
    print(f"\nVerification results saved to data/round9_verified.json")

    # -------------------------------------------------------------------
    # Part 2: Combined portfolio backtest
    # -------------------------------------------------------------------
    print("\n\n" + "=" * 80)
    print("PART 2: COMBINED PORTFOLIO BACKTEST ($200 shared wallet)")
    print("=" * 80)

    # Build new-coin bot rows from passed verification
    new_bot_rows: list[tuple] = []
    for r in passed:
        coin = r["coin"]
        signal_key = r["signal_key"]
        tf = r["timeframe"]
        sl = r["sl_mult"]
        tp = r["tp_mult"]
        trail = r["trail_mult"]
        prefix = r["prefix"]
        strategy_label = r["strategy"][:14]
        new_bot_rows.append((coin, signal_key, tf, sl, tp, trail, strategy_label, prefix))

    all_bot_rows = EXISTING_BOTS + new_bot_rows
    print(f"Total bots: {len(EXISTING_BOTS)} existing + {len(new_bot_rows)} new = {len(all_bot_rows)}")
    print()

    all_trades: list[dict] = []

    # Existing bots
    print("Running existing 47 bots...")
    for i, bot_row in enumerate(EXISTING_BOTS, 1):
        coin, strategy_key, tf_signal, atr_sl, atr_tp, atr_trail, label, data_prefix = bot_row
        trades = run_bot(coin, strategy_key, tf_signal, atr_sl, atr_tp, atr_trail, label, data_prefix)
        if not trades:
            print(f"  [{i:02d}] {coin} ({label}) — no trades")
        else:
            print(f"  [{i:02d}] {coin} ({label}) — {len(trades)} trades")
        all_trades.extend(trades)

    # New bots
    if new_bot_rows:
        print(f"\nRunning {len(new_bot_rows)} new verified bots...")
        for i, bot_row in enumerate(new_bot_rows, 1):
            coin, strategy_key, tf_signal, atr_sl, atr_tp, atr_trail, label, data_prefix = bot_row
            trades = run_bot(coin, strategy_key, tf_signal, atr_sl, atr_tp, atr_trail, label, data_prefix)
            if not trades:
                print(f"  [{i:02d}] {coin} ({label}) — no trades")
            else:
                print(f"  [{i:02d}] {coin} ({label}) — {len(trades)} trades")
            all_trades.extend(trades)

    print(f"\nTotal raw trades: {len(all_trades)}")

    if not all_trades:
        print("No trades. Check data files.")
        return

    INITIAL_BALANCE = 200.0
    replayed = replay_shared_wallet(all_trades, INITIAL_BALANCE)

    # Print reports
    print_per_bot_summary(replayed, INITIAL_BALANCE, all_bot_rows)
    print_monthly_report(replayed, INITIAL_BALANCE)
    print_daily_report(replayed)

    # Save portfolio results
    portfolio_save = []
    for t in replayed:
        entry_ts = t["entry_time"].isoformat() if t["entry_time"] is not None else None
        exit_ts = t["exit_time"].isoformat() if t["exit_time"] is not None else None
        portfolio_save.append({
            "coin": t["coin"],
            "label": t["label"],
            "strategy": t["strategy"],
            "side": t["side"],
            "entry_time": entry_ts,
            "exit_time": exit_ts,
            "entry_price": t["entry_price"],
            "exit_price": t["exit_price"],
            "close_reason": t["close_reason"],
            "pnl_frac": t["pnl_frac"],
            "dollar_pnl": t["dollar_pnl"],
            "balance": t["balance"],
        })
    with open(DATA_DIR / "portfolio_full.json", "w") as f:
        json.dump(portfolio_save, f, indent=2)
    print(f"\nPortfolio results saved to data/portfolio_full.json")


if __name__ == "__main__":
    main()
