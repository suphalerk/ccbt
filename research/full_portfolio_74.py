"""Full combined portfolio backtest — ALL verified bots from every round.

Sources:
  - Source 1: 51-bot portfolio from mega100_results.json (per_bot summary)
              + their underlying bot definitions (EXISTING_BOTS + R10 + R11 + M100)
  - Source 2: 23 combo bots from combo_results.json (R12)

Methodology (R-multiple shared wallet):
  1. Each bot runs BacktestEngine with $10K virtual balance to collect trades.
  2. R-multiple = trade_pnl / risk_amount_on_10K.
  3. All trades sorted by entry_time, replayed on shared $200 wallet.
  4. Dollar PnL = wallet * bot_risk_pct * R_multiple.
  5. Max 5 concurrent positions enforced.

Filters:
  - Data >= 12 months (skip if not)
  - Engine trades >= 8 (skip if not)

Output: stdout + data/full_portfolio_74.json
"""

from __future__ import annotations

import copy
import io
import json
import logging
import sys
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
ROOT = Path("/Users/iceai/Work/ccbt")
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING)
logging.getLogger("backtest").setLevel(logging.WARNING)
logging.getLogger("bot").setLevel(logging.WARNING)

from backtest.data_loader import load_ohlcv  # noqa: E402
from backtest.engine import BacktestEngine   # noqa: E402

DATA_DIR = ROOT / "data"
INITIAL_WALLET = 200.0
MAX_CONCURRENT = 5
MIN_DATA_MONTHS = 12
MIN_TRADES = 8

# ---------------------------------------------------------------------------
# Base config — used by all inline bots
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
    "ribbon_rsi_vol":      {"enabled": False},
    "dualthrust_adx":      {"enabled": False},
    "zscore_stoch":        {"enabled": False},
    "ichi_adx":            {"enabled": False},
    "ribbon_ao":           {"enabled": False},
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
    "dualthrust_adx_threshold": 25.0,
    "zscore_stoch_threshold": 2.0,
    "zscore_stoch_oversold": 20.0,
    "zscore_stoch_overbought": 80.0,
    "ichi_adx_threshold": 25.0,
    "ribbon_ao_period": 5,
}


# ---------------------------------------------------------------------------
# Bot definition
# ---------------------------------------------------------------------------

class BotDef:
    """Defines a single bot to backtest."""

    def __init__(
        self,
        label: str,
        coin: str,
        prefix: str,
        signal_key: str,
        tf: str,
        sl: float,
        tp: float,
        trail: float,
        risk_pct: float = 0.01,
        extra: Optional[dict] = None,
        config_file: Optional[str] = None,
    ) -> None:
        self.label = label
        self.coin = coin
        self.prefix = prefix
        self.signal_key = signal_key
        self.tf = tf
        self.sl = sl
        self.tp = tp
        self.trail = trail
        self.risk_pct = risk_pct
        self.extra = extra or {}
        self.config_file = config_file  # optional: load config from file


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def build_config(
    symbol: str,
    signal_key: str,
    tf: str,
    sl: float,
    tp: float,
    trail: float,
    risk_pct: float,
    extra: Optional[dict] = None,
) -> dict:
    """Build a complete engine config for one bot."""
    cfg = copy.deepcopy(BASE_CONFIG)
    cfg["symbol"] = symbol.upper()
    cfg["timeframe_signal"] = tf
    cfg["timeframe_trend"] = tf
    cfg["risk_per_trade"] = risk_pct
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_trail_mult_ranging"] = max(2.0, trail - 1.0)
    cfg["atr_trail_mult_volatile"] = trail + 1.0

    if extra:
        cfg.update(extra)

    # Enable only the target signal
    sigs = copy.deepcopy(ALL_SIGNALS_OFF)
    if signal_key == "ema_crossover":
        sigs["ema_crossover"] = {"enabled": True}
        sigs["ema_fast_crossover"] = {"enabled": True}
    else:
        sigs[signal_key] = {"enabled": True}

    # Ichimoku params
    if "ichi" in signal_key or signal_key == "ichimoku_cloud":
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52

    # Signal-specific extra config
    if signal_key in ("zscore_meanrev", "zscore_stoch"):
        cfg["zscore_atr_sl_mult"] = sl
        cfg["zscore_atr_tp_mult"] = tp
    elif signal_key == "dual_thrust":
        cfg["dual_thrust_k"] = 0.5
        cfg["dual_thrust_lookback"] = 20
    elif signal_key == "dualthrust_adx":
        cfg["dual_thrust_k"] = 0.5
        cfg["dual_thrust_lookback"] = 20
        cfg["dualthrust_adx_threshold"] = 25.0
    elif signal_key == "range_bounce":
        cfg["range_bounce_atr_sl_mult"] = sl
        cfg["range_bounce_atr_tp_mult"] = tp
        cfg["mr_atr_sl_mult"] = sl
        cfg["mr_atr_tp_mult"] = tp

    cfg["signals"] = sigs
    return cfg


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_signal_data(prefix: str, tf: str) -> pd.DataFrame:
    """Load signal data; 4h is resampled from 1h."""
    if tf == "15m":
        path = DATA_DIR / f"{prefix}_15m_2y.csv"
        return load_ohlcv(str(path)) if path.exists() else pd.DataFrame()
    elif tf == "1h":
        for suffix in ("_1h_2y.csv", "_1h_5y.csv", "_1h_1y.csv"):
            path = DATA_DIR / f"{prefix}{suffix}"
            if path.exists():
                return load_ohlcv(str(path))
        return pd.DataFrame()
    elif tf == "4h":
        for suffix in ("_1h_2y.csv", "_1h_5y.csv"):
            path = DATA_DIR / f"{prefix}{suffix}"
            if path.exists():
                return _resample_4h(load_ohlcv(str(path)))
        return pd.DataFrame()
    return pd.DataFrame()


def load_trend_data(prefix: str, tf: str) -> pd.DataFrame:
    """For 15m bots, trend is 1h; for 1h/4h, trend == signal data."""
    if tf != "15m":
        return pd.DataFrame()
    for suffix in ("_1h_2y.csv", "_1h_5y.csv"):
        path = DATA_DIR / f"{prefix}{suffix}"
        if path.exists():
            return load_ohlcv(str(path))
    return pd.DataFrame()


def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h OHLCV to 4h bars."""
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    return df.resample("4h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna()


def data_span_months(df: pd.DataFrame) -> float:
    """Return data span in months."""
    if df.empty or len(df) < 2:
        return 0.0
    idx = pd.to_datetime(df.index)
    return (idx[-1] - idx[0]).days / 30.44


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
# Bot registry — ALL verified bots from every round
# ---------------------------------------------------------------------------

def _load_config_file(rel_path: str) -> Optional[dict]:
    """Load config JSON, return None if not found."""
    p = ROOT / rel_path
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def _build_bot_list() -> list[BotDef]:
    """Build the full unified bot list from all rounds."""
    bots: list[BotDef] = []

    # -----------------------------------------------------------------------
    # EXISTING BOTS (prior rounds — from EXISTING_BOTS in mega100_verify_backtest.py)
    # -----------------------------------------------------------------------
    existing_defs = [
        # (label, coin, prefix, signal, tf, sl, tp, trail, risk_pct, config_file)
        ("BTC EMA 15m",      "BTC",      "btcusdt",      "ema_crossover",  "15m", 1.0, 3.0, 2.0, 0.05, "config.json"),
        ("WIF EMA 15m",      "WIF",      "wifusdt",      "ema_crossover",  "15m", 1.0, 3.0, 2.0, 0.03, "config_wif.json"),
        ("ARC EMA 15m",      "ARC",      "arcusdt",      "ema_crossover",  "15m", 1.0, 3.0, 2.0, 0.03, "config_arcusdt_ema.json"),
        ("AVAX Ichi 1H",     "AVAX",     "avaxusdt",     "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, 0.02, "config_avax_ichi.json"),
        ("POL Ichi Trail",   "POL",      "polusdt",      "ichimoku_cloud", "1h",  1.0, 0.0, 3.0, 0.01, "config_polusdt_ichi4htrail.json"),
        ("GUN Ichi 1H",      "GUN",      "gunusdt",      "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, 0.01, "config_gunusdt_ichi.json"),
        ("BERA Ichi 1H",     "BERA",     "berausdt",     "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, 0.02, "config_berausdt_ichi.json"),
        ("ATH Ichi 1H",      "ATH",      "athusdt",      "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, 0.01, "config_athusdt_ichi.json"),
        ("INJ Ichi 1H",      "INJ",      "injusdt",      "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, 0.01, "config_injusdt_ichi.json"),
        ("TRUMP Ichi 1H",    "TRUMP",    "trumpusdt",    "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, 0.01, "config_trumpusdt_ichi.json"),
        ("ANIME Ichi 1H",    "ANIME",    "animeusdt",    "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, 0.01, "config_animeusdt_ichi.json"),
        ("IP Ichi 1H",       "IP",       "ipusdt",       "ichimoku_cloud", "1h",  2.0, 4.0, 3.0, 0.01, "config_ipusdt_ichi.json"),
        ("1000PEPE VolExp",  "1000PEPE", "1000pepeusdt", "vol_expansion",  "1h",  2.5, 3.0, 2.5, 0.01, "config_1000pepeusdt_volexp.json"),
        ("ONDO Ichi 4H",     "ONDO",     "ondousdt",     "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, 0.01, "config_ondousdt_ichi4h.json"),
        ("TIA Ichi 4H",      "TIA",      "tiausdt",      "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, 0.01, "config_tiausdt_ichi4h.json"),
        ("TON Ichi 4H",      "TON",      "tonusdt",      "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, 0.01, "config_tonusdt_ichi4h.json"),
        ("XMR Ichi 4H",      "XMR",      "xmrusdt",      "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, 0.01, "config_xmrusdt_ichi4h.json"),
    ]
    for label, coin, prefix, signal, tf, sl, tp, trail, risk, cfg_file in existing_defs:
        cfg = _load_config_file(cfg_file) if cfg_file else None
        bots.append(BotDef(
            label=label, coin=coin, prefix=prefix, signal_key=signal,
            tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=risk,
            config_file=cfg_file if cfg else None,
        ))

    # -----------------------------------------------------------------------
    # ROUND 10 verified bots (from round10_results.json, plus hardcoded known passes)
    # -----------------------------------------------------------------------
    r10_path = DATA_DIR / "round10_results.json"
    r10_added: set[str] = set()

    if r10_path.exists():
        with open(r10_path) as f:
            r10_data = json.load(f)
        r10_passed = [
            x for x in r10_data.get("passed", [])
            if x.get("engine_pf", 0) >= 1.2 and x.get("trades", 0) >= 8
        ]
        for vbot in r10_passed:
            coin = vbot["coin"]
            signal_key = vbot["signal_key"]
            tf = vbot.get("tf", "1h")
            prefix = vbot.get("prefix", coin.lower() + "usdt")
            sl = vbot.get("atr_sl_mult", 2.0)
            tp = vbot.get("atr_tp_mult", 4.0)
            trail = vbot.get("atr_trail_mult", 3.0) or 3.0
            key = f"{prefix}_{signal_key}_{tf}"
            if key in r10_added:
                continue
            r10_added.add(key)
            label = f"{coin} {signal_key[:8]} R10"
            bots.append(BotDef(
                label=label, coin=coin, prefix=prefix, signal_key=signal_key,
                tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))
    else:
        # Hardcoded R10 passes (from mega100 per_bot labels and CLAUDE.md)
        r10_hardcoded = [
            # (label, coin, prefix, signal, tf, sl, tp, trail)
            ("GALA dual_thr",    "GALA",     "galausdt",     "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("PHA dual_thr",     "PHA",      "phausdt",      "dual_thrust",      "4h", 2.0, 4.0, 3.0),
            ("ZEC dual_thr",     "ZEC",      "zecusdt",      "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("FIL range_bo",     "FIL",      "filusdt",      "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("PIXEL dual_thr",   "PIXEL",    "pixelusdt",    "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("VVV range_bo",     "VVV",      "vvvusdt",      "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("W range_bo",       "W",        "wusdt",        "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("KAS range_bo",     "KAS",      "kasusdt",      "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("DEGO dual_thr",    "DEGO",     "degousdt",     "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("VIRTUAL dual_thr", "VIRTUAL",  "virtualusdt",  "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("FARTCOIN dual_thr","FARTCOIN",  "fartcoinusdt", "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("DOT dual_thr",     "DOT",      "dotusdt",      "dual_thrust",      "4h", 2.0, 4.0, 3.0),
            ("PIPPIN dual_thr",  "PIPPIN",   "pippinusdt",   "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("1000BONK dual_thr","1000BONK", "1000bonkusdt", "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("ALICE dual_thr",   "ALICE",    "aliceusdt",    "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("DASH dual_thr",    "DASH",     "dashusdt",     "dual_thrust",      "4h", 2.0, 4.0, 3.0),
            ("ICP dual_thr",     "ICP",      "icpusdt",      "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("XAI awesome_",     "XAI",      "xaiusdt",      "awesome_oscillator","1h", 1.5, 4.0, 3.0),
            ("XAI range_bo",     "XAI",      "xaiusdt",      "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("DOT awesome_",     "DOT",      "dotusdt",      "awesome_oscillator","1h", 1.5, 4.0, 3.0),
            ("BCH awesome_",     "BCH",      "bchusdt",      "awesome_oscillator","1h", 1.5, 4.0, 3.0),
            ("UNI dual_thr",     "UNI",      "uniusdt",      "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("CRV range_bo",     "CRV",      "crvusdt",      "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("AAVE range_bo",    "AAVE",     "aaveusdt",     "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("ALICE range_bo",   "ALICE",    "aliceusdt",    "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("SAND awesome_",    "SAND",     "sandusdt",     "awesome_oscillator","1h", 1.5, 4.0, 3.0),
            ("GALA awesome_",    "GALA",     "galausdt",     "awesome_oscillator","1h", 1.5, 4.0, 3.0),
            ("VIRTUAL awesome_", "VIRTUAL",  "virtualusdt",  "awesome_oscillator","1h", 1.5, 4.0, 3.0),
            ("SAND AO 4H",       "SAND",     "sandusdt",     "awesome_oscillator","4h", 1.5, 4.0, 3.0),
            ("VVV dual_thr",     "VVV",      "vvvusdt",      "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("ZEC range_bo",     "ZEC",      "zecusdt",      "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("DOT range_bo",     "DOT",      "dotusdt",      "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("FIL dual_thr",     "FIL",      "filusdt",      "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("FIL awesome_",     "FIL",      "filusdt",      "awesome_oscillator","1h", 1.5, 4.0, 3.0),
            ("FIL range_bo2",    "FIL",      "filusdt",      "range_bounce",     "1h", 1.5, 2.0, 2.0),
            ("W dual_thr",       "W",        "wusdt",        "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("GALA dual_thr2",   "GALA",     "galausdt",     "dual_thrust",      "1h", 2.0, 4.0, 3.0),
            ("XAI awesome_2",    "XAI",      "xaiusdt",      "awesome_oscillator","1h", 1.5, 4.0, 3.0),
            ("DOT awesome_2",    "DOT",      "dotusdt",      "awesome_oscillator","4h", 1.5, 4.0, 3.0),
            ("ALICE awesome_",   "ALICE",    "aliceusdt",    "awesome_oscillator","1h", 1.5, 4.0, 3.0),
            ("ZEN awesome_",     "ZEN",      "zenusdt",      "awesome_oscillator","1h", 1.5, 4.0, 3.0),
        ]
        for label, coin, prefix, signal, tf, sl, tp, trail in r10_hardcoded:
            key = f"{prefix}_{signal}_{tf}"
            if key in r10_added:
                continue
            r10_added.add(key)
            bots.append(BotDef(
                label=label, coin=coin, prefix=prefix, signal_key=signal,
                tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))

    # -----------------------------------------------------------------------
    # ROUND 11 verified bots
    # -----------------------------------------------------------------------
    r11_path = DATA_DIR / "round11_results.json"
    r11_added: set[str] = set()

    if r11_path.exists():
        with open(r11_path) as f:
            r11_data = json.load(f)
        r11_passed = [
            x for x in r11_data.get("passed", [])
            if x.get("engine_pf", 0) >= 1.2 and x.get("trades", 0) >= 8
        ]
        for vbot in r11_passed:
            coin = vbot["coin"]
            signal_key = vbot["signal_key"]
            tf = vbot.get("tf", "1h")
            prefix = vbot.get("prefix", coin.lower() + "usdt")
            sl = vbot.get("atr_sl_mult", 2.0)
            tp = vbot.get("atr_tp_mult", 4.0)
            trail = vbot.get("atr_trail_mult", 3.0) or 3.0
            key = f"{prefix}_{signal_key}_{tf}"
            if key in r11_added:
                continue
            r11_added.add(key)
            bots.append(BotDef(
                label=f"{coin} {signal_key[:8]} R11", coin=coin, prefix=prefix,
                signal_key=signal_key, tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))
    else:
        # Hardcoded R11 passes (from CLAUDE.md and mega100 per_bot)
        r11_hardcoded = [
            ("BAN zscore_m",  "BAN",   "banusdt",   "zscore_meanrev", "1h", 1.5, 2.0, 2.0),
            ("ADA stoch_mtf", "ADA",   "adausdt",   "stoch_mtf",      "4h", 2.0, 4.0, 3.0),
            ("PENGU ema_rib", "PENGU", "penguusdt", "ema_ribbon",     "4h", 2.0, 4.0, 3.0),
            ("CFX ema_rib",   "CFX",   "cfxusdt",   "ema_ribbon",     "4h", 2.0, 4.0, 3.0),
            ("ICP ema_rib",   "ICP",   "icpusdt",   "ema_ribbon",     "4h", 2.0, 4.0, 3.0),
        ]
        for label, coin, prefix, signal, tf, sl, tp, trail in r11_hardcoded:
            key = f"{prefix}_{signal}_{tf}"
            if key in r11_added:
                continue
            r11_added.add(key)
            bots.append(BotDef(
                label=label, coin=coin, prefix=prefix, signal_key=signal,
                tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))

    # -----------------------------------------------------------------------
    # MEGA100 verified bots (from mega100_results.json verification PASS)
    # -----------------------------------------------------------------------
    mega_path = DATA_DIR / "mega100_results.json"
    mega_added: set[str] = set()

    if mega_path.exists():
        with open(mega_path) as f:
            mega_data = json.load(f)
        mega_passed = [
            x for x in mega_data.get("verification", [])
            if x.get("status") == "PASS"
        ]
        for vbot in mega_passed:
            coin = vbot["coin"]
            signal_key = vbot["signal_key"]
            tf = vbot.get("tf", "1h")
            prefix = vbot.get("prefix", coin.lower() + "usdt")
            sl = vbot.get("atr_sl_mult", 2.0)
            tp = vbot.get("atr_tp_mult", 4.0)
            trail = vbot.get("atr_trail_mult", 3.0) or 3.0
            key = f"{prefix}_{signal_key}_{tf}"
            if key in mega_added:
                continue
            mega_added.add(key)
            bots.append(BotDef(
                label=f"{coin} {signal_key[:8]} M100", coin=coin, prefix=prefix,
                signal_key=signal_key, tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))

    # -----------------------------------------------------------------------
    # ROUND 12: Combo bots from combo_results.json
    # -----------------------------------------------------------------------
    combo_path = DATA_DIR / "combo_results.json"
    combo_added: set[str] = set()

    if combo_path.exists():
        with open(combo_path) as f:
            combo_data = json.load(f)
        combo_passed = [
            x for x in combo_data.get("passed", [])
            if x.get("engine_pf", 0) >= 1.2 and x.get("trades", 0) >= 8
        ]
        for vbot in combo_passed:
            coin = vbot["coin"]
            signal_key = vbot["signal_key"]
            tf = vbot.get("tf", "1h")
            prefix = vbot.get("prefix", coin.lower() + "usdt")
            sl = vbot.get("atr_sl_mult", 2.0)
            tp = vbot.get("atr_tp_mult", 4.0)
            trail = vbot.get("atr_trail_mult", 3.0) or 3.0
            key = f"{prefix}_{signal_key}_{tf}"
            if key in combo_added:
                continue
            combo_added.add(key)
            bots.append(BotDef(
                label=f"{coin} {signal_key[:8]} R12", coin=coin, prefix=prefix,
                signal_key=signal_key, tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))

    # -----------------------------------------------------------------------
    # Dedup: remove exact duplicates (same prefix + signal_key + tf)
    # If duplicate exists, keep higher-risk one (usually the earlier round)
    # -----------------------------------------------------------------------
    seen: dict[str, BotDef] = {}
    deduped: list[BotDef] = []
    for bot in bots:
        key = f"{bot.prefix}_{bot.signal_key}_{bot.tf}"
        if key not in seen:
            seen[key] = bot
            deduped.append(bot)
        else:
            # Keep the one with higher risk_pct (earlier round = more validated)
            existing = seen[key]
            if bot.risk_pct > existing.risk_pct:
                idx = deduped.index(existing)
                deduped[idx] = bot
                seen[key] = bot

    return deduped


# ---------------------------------------------------------------------------
# Per-bot engine runner
# ---------------------------------------------------------------------------

def run_bot_engine(bot: BotDef) -> tuple[list, dict]:
    """Run BacktestEngine for one bot. Returns (raw_trade_list, per_bot_metrics)."""

    # Load signal data
    signal_df = load_signal_data(bot.prefix, bot.tf)
    if signal_df.empty:
        return [], {}

    # Check data span
    if data_span_months(signal_df) < MIN_DATA_MONTHS:
        return [], {}

    # Load trend data (only for 15m bots)
    trend_df = load_trend_data(bot.prefix, bot.tf)

    # Filter to last year
    signal_1y = filter_last_year(signal_df)
    trend_1y = filter_last_year(trend_df) if not trend_df.empty else pd.DataFrame()

    if len(signal_1y) < 50:
        return [], {}

    # Build config — prefer config file, fall back to inline
    if bot.config_file:
        cfg = _load_config_file(bot.config_file)
        if cfg is None:
            cfg = build_config(
                bot.prefix, bot.signal_key, bot.tf,
                bot.sl, bot.tp, bot.trail, bot.risk_pct, bot.extra,
            )
    else:
        cfg = build_config(
            bot.prefix, bot.signal_key, bot.tf,
            bot.sl, bot.tp, bot.trail, bot.risk_pct, bot.extra,
        )

    try:
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        with redirect_stdout(io.StringIO()):
            engine.run(signal_1y, trend_1y if not trend_1y.empty else None)
    except Exception as exc:
        print(f"  [ERROR] {bot.label}: {exc}")
        return [], {}

    trades = engine.state.trades
    if not trades:
        return [], {}

    # Compute per-bot metrics
    pnl_vals = [t.pnl for t in trades]
    wins = [p for p in pnl_vals if p > 0]
    losses = [p for p in pnl_vals if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    wr = len(wins) / len(trades) * 100.0

    # Avg R-multiple
    risk_amount = 10_000.0 * bot.risk_pct
    r_vals = [t.pnl / risk_amount for t in trades if risk_amount > 0]
    avg_r = float(np.mean(r_vals)) if r_vals else 0.0

    # Max drawdown
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

    # Sharpe
    if len(pnl_vals) > 1 and risk_amount > 0:
        r_series = [p / risk_amount for p in pnl_vals]
        mean_r = float(np.mean(r_series))
        std_r = float(np.std(r_series, ddof=1))
        tpd = len(r_series) / 365.0
        sharpe = (mean_r / std_r * (tpd ** 0.5) * (365.0 ** 0.5)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    metrics = {
        "label": bot.label,
        "coin": bot.coin,
        "signal": bot.signal_key,
        "tf": bot.tf,
        "trades": len(trades),
        "wr_pct": round(wr, 1),
        "avg_r": round(avg_r, 3),
        "pf": round(pf, 2),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd, 1),
        "pnl_10k": round(engine.state.balance - 10_000.0, 2),
    }
    return trades, metrics


# ---------------------------------------------------------------------------
# R-multiple shared wallet replay
# ---------------------------------------------------------------------------

def run_portfolio_replay(
    all_trade_records: list[dict],
    initial_wallet: float = INITIAL_WALLET,
    max_concurrent: int = MAX_CONCURRENT,
) -> tuple[dict, list, list]:
    """Replay all trades on shared wallet. Returns (per_bot_stats, monthly_rows, daily_log)."""

    if not all_trade_records:
        return {}, [], []

    all_df = (
        pd.DataFrame(all_trade_records)
        .sort_values("entry_time")
        .reset_index(drop=True)
    )

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

    # Build monthly rows
    running_bal = initial_wallet
    monthly_rows = []
    for month_key in sorted(monthly_pnl.keys()):
        m = monthly_pnl[month_key]
        month_pnl = m["pnl"]
        month_trades = int(m["trades"])
        running_bal += month_pnl
        monthly_rows.append({
            "month": month_key,
            "trades": month_trades,
            "pnl": round(month_pnl, 2),
            "balance": round(running_bal, 2),
        })

    # Summary stats
    max_dd_pct = (_max_dd_usd / _eq_peak * 100.0) if _eq_peak > 0 else 0.0
    total_pnl = balance - initial_wallet
    total_return_pct = total_pnl / initial_wallet * 100.0

    summary = {
        "initial_wallet": initial_wallet,
        "final_balance": round(balance, 2),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_return_pct, 1),
        "max_dd_pct": round(max_dd_pct, 1),
        "max_dd_usd": round(_max_dd_usd, 2),
        "total_trades": sum(s["trades"] for s in per_bot_stats.values()),
        "unique_bots": len(per_bot_stats),
    }

    return {**summary, "per_bot": {k: dict(v) for k, v in per_bot_stats.items()}}, monthly_rows, daily_log


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def report_per_bot(per_bot_stats: dict, bot_metrics: list[dict]) -> str:
    """Report 1: per-bot summary table."""
    lines = [
        "",
        "=" * 90,
        "REPORT 1: PER-BOT SUMMARY (sorted by shared wallet PnL$)",
        "=" * 90,
        f"{'Bot':<30} {'Signal':<16} {'Trades':>6}  {'WR%':>5}  {'Avg R':>6}  {'PnL$':>9}",
        "-" * 80,
    ]

    # Merge engine metrics with wallet PnL
    metrics_map = {m["label"]: m for m in bot_metrics}

    rows = []
    for bot_name, stats in per_bot_stats.items():
        if stats["trades"] == 0:
            continue
        n = stats["trades"]
        wr = stats["wins"] / n * 100.0
        avg_r = stats["r_total"] / n
        pnl = stats["pnl"]
        eng_m = metrics_map.get(bot_name, {})
        signal = eng_m.get("signal", "?")
        rows.append((bot_name, signal, n, wr, avg_r, pnl))

    rows.sort(key=lambda x: -x[5])
    total_pnl = 0.0
    total_trades = 0
    for bot_name, signal, n, wr, avg_r, pnl in rows:
        lines.append(
            f"  {bot_name:<28} {signal:<16} {n:>6}  {wr:>4.0f}%  {avg_r:>+6.3f}  {pnl:>+9.2f}"
        )
        total_pnl += pnl
        total_trades += n

    lines += [
        "-" * 80,
        f"  {'TOTAL':<28} {'':<16} {total_trades:>6}  {'':>5}  {'':>6}  {total_pnl:>+9.2f}",
        "=" * 90,
    ]
    return "\n".join(lines)


def report_monthly(monthly_rows: list[dict]) -> str:
    """Report 2: monthly portfolio returns (last 1 year)."""
    lines = [
        "",
        "=" * 60,
        "REPORT 2: MONTHLY RETURNS",
        "=" * 60,
        f"{'Month':<10}  {'Trades':>6}  {'PnL$':>9}  {'Balance$':>10}",
        "-" * 44,
    ]
    for row in monthly_rows:
        lines.append(
            f"{row['month']:<10}  {row['trades']:>6}  {row['pnl']:>+9.2f}  ${row['balance']:>9.2f}"
        )
    lines.append("=" * 60)
    return "\n".join(lines)


def report_daily(daily_log: list[dict]) -> str:
    """Report 3: daily log for last 2 months."""
    if not daily_log:
        return "\nNo daily trades."

    log_df = pd.DataFrame(daily_log)
    cutoff = (
        pd.to_datetime(log_df["date"]).max() - pd.DateOffset(months=2)
    ).strftime("%Y-%m-%d")
    recent = log_df[log_df["date"] >= cutoff]

    lines = [
        "",
        "=" * 95,
        "REPORT 3: DAILY LOG (last 2 months)",
        "=" * 95,
        f"{'Date':<12}  {'Bot':<30}  {'Side':<6}  {'R':>6}  {'PnL$':>8}  {'Balance$':>10}",
        "-" * 80,
    ]
    for _, row in recent.iterrows():
        lines.append(
            f"{row['date']:<12}  {row['bot']:<30}  {row['side']:<6}  "
            f"{row['r_mult']:>+6.3f}  {row['pnl']:>+8.2f}  ${row['balance']:>9.2f}"
        )
    lines.append("=" * 95)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 80)
    print("FULL PORTFOLIO BACKTEST — ALL VERIFIED BOTS (Every Round)")
    print(f"Shared wallet: ${INITIAL_WALLET:.0f}  |  Max concurrent: {MAX_CONCURRENT}")
    print("=" * 80)

    # Build unified bot list
    bots = _build_bot_list()
    print(f"\nBuilt {len(bots)} unique bot definitions.")

    # Run each bot through the engine
    all_trade_records: list[dict] = []
    bot_metrics: list[dict] = []
    skipped: list[str] = []

    print(f"\nRunning backtest engines...")
    for i, bot in enumerate(bots):
        if i > 0 and i % 10 == 0:
            print(f"  Progress: {i}/{len(bots)} bots...")

        trades, metrics = run_bot_engine(bot)

        if not trades or len(trades) < MIN_TRADES:
            reason = "no data" if not trades else f"only {len(trades)} trades"
            skipped.append(f"{bot.label} ({reason})")
            continue

        bot_metrics.append(metrics)
        risk_amount = 10_000.0 * bot.risk_pct

        for t in trades:
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
                "bot": bot.label,
                "coin": bot.coin,
                "side": t.side,
                "entry_time": entry_ts,
                "exit_time": exit_ts,
                "r_mult": r_mult,
                "risk_pct": bot.risk_pct,
                "close_reason": t.close_reason,
            })

    print(f"\nCompleted {len(bot_metrics)} bots. Skipped {len(skipped)}.")
    if skipped:
        print("  Skipped:")
        for s in skipped:
            print(f"    - {s}")

    # Engine metrics summary
    print("\nEngine metrics (per bot, independent $10K runs):")
    print(f"  {'Bot':<30} {'Signal':<16} {'Tr':>4}  {'WR%':>5}  {'PF':>5}  {'AvgR':>6}  {'PnL$10K':>9}")
    print("  " + "-" * 75)
    for m in sorted(bot_metrics, key=lambda x: -x["pnl_10k"]):
        pf_str = f"{m['pf']:.2f}" if m["pf"] < 99 else "inf"
        print(
            f"  {m['label']:<30} {m['signal']:<16} {m['trades']:>4}  "
            f"{m['wr_pct']:>4.0f}%  {pf_str:>5}  {m['avg_r']:>+6.3f}  "
            f"{m['pnl_10k']:>+9.2f}"
        )

    if not all_trade_records:
        print("\nERROR: No trades collected — check data files.")
        return

    # Shared wallet replay
    portfolio, monthly_rows, daily_log = run_portfolio_replay(
        all_trade_records, INITIAL_WALLET, MAX_CONCURRENT,
    )

    # Print reports
    per_bot_stats = portfolio.pop("per_bot", {})
    print(f"\n{'=' * 80}")
    print("PORTFOLIO SUMMARY")
    print(f"{'=' * 80}")
    print(f"  Initial balance:  ${portfolio['initial_wallet']:.2f}")
    print(f"  Final balance:    ${portfolio['final_balance']:.2f}")
    print(f"  Total return:     {portfolio['total_return_pct']:+.1f}%  (${portfolio['total_pnl']:+.2f})")
    print(f"  Total trades:     {portfolio['total_trades']}")
    print(f"  Unique bots:      {portfolio['unique_bots']}")
    print(f"  Max drawdown:     {portfolio['max_dd_pct']:.1f}%  (${portfolio['max_dd_usd']:.2f})")

    print(report_per_bot(per_bot_stats, bot_metrics))
    print(report_monthly(monthly_rows))
    print(report_daily(daily_log))

    # Save JSON
    def _clean(obj: object) -> object:
        if isinstance(obj, (pd.Timestamp, pd.Period)):
            return str(obj)
        if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
            return None
        return obj

    output = {
        "meta": {
            "bots_run": len(bot_metrics),
            "bots_skipped": len(skipped),
            "skipped_labels": skipped,
            "initial_wallet": INITIAL_WALLET,
            "max_concurrent": MAX_CONCURRENT,
        },
        "portfolio_summary": {
            **portfolio,
            "per_bot": {k: dict(v) for k, v in per_bot_stats.items()},
            "monthly": monthly_rows,
            "daily": daily_log[-300:],
        },
        "engine_metrics": bot_metrics,
    }

    out_path = DATA_DIR / "full_portfolio_74.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=_clean)

    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
