"""backtest_5_profiles.py — Replay 96 audited bots across 5 deployment profiles.

Approach:
  1. Build the 96 audited bot list (same as backtest_96_audited.py).
  2. Run BacktestEngine ONCE per bot → collect R-multiple trade pool.
  3. Replay the shared wallet 5 times with different capital/risk/leverage/bot-set settings.

Profiles:
  testnet   — $5,000  | 1.0% risk | 25x lev | all 96 bots | max 10 concurrent
  real-test — $45     | 3.0% risk | 10x lev | top 5 bots  | max 2 concurrent
  real-safe — $200    | 1.0% risk | 10x lev | top 10 bots | max 5 concurrent
  real-grow — $500    | 0.9% risk | 15x lev | top 50 bots | max 10 concurrent
  real-full — $1,000  | 0.9% risk | 25x lev | all 96 bots | max 10 concurrent

R-multiple notes:
  - Engine runs with $10K virtual balance per bot.
  - r_mult = trade.pnl / (10_000 × actual_config_risk_pct)
  - BTC=5%, WIF=3%, AVAX=2%, others=1% in the engine.
  - Shared wallet always applies the profile's risk_pct:
      dollar_pnl = balance × profile.risk_pct × r_mult

Output: stdout + data/profiles_5_backtest.json
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
MIN_DATA_MONTHS = 12
MIN_TRADES = 6
ENGINE_BALANCE = 10_000.0

# ---------------------------------------------------------------------------
# 5 Deployment Profiles
# ---------------------------------------------------------------------------
PROFILES = [
    {
        "name": "testnet",
        "capital": 5000.0,
        "risk_pct": 0.01,
        "leverage": 25,
        "max_concurrent": 10,
        "bots": "all_96",
    },
    {
        "name": "real-test",
        "capital": 45.0,
        "risk_pct": 0.03,
        "leverage": 10,
        "max_concurrent": 2,
        "bots": "top_5",
    },
    {
        "name": "real-safe",
        "capital": 200.0,
        "risk_pct": 0.01,
        "leverage": 10,
        "max_concurrent": 5,
        "bots": "top_10",
    },
    {
        "name": "real-grow",
        "capital": 500.0,
        "risk_pct": 0.009,
        "leverage": 15,
        "max_concurrent": 10,
        "bots": "top_50",
    },
    {
        "name": "real-full",
        "capital": 1000.0,
        "risk_pct": 0.009,
        "leverage": 25,
        "max_concurrent": 10,
        "bots": "all_96",
    },
]

# Fixed top_5 bot labels for "real-test" profile (per spec)
TOP_5_LABELS: list[str] = [
    "HBAR ribbon_ao R13",
    "ARB dualthrust R13",
    "1000PEPE dualthrust R13",
    "IP ribbon_rsi R13",
    "WLD dualthrust R13",
]

# ---------------------------------------------------------------------------
# Bot definitions — mirrors backtest_96_audited.py exactly
# ---------------------------------------------------------------------------

REMOVE_BOTS: set[str] = {
    "ADA awesome_ R10",
    "ADA zscore_m R11",
    "ANIME Ichi 1H",
    "ANIME zscore_m R11",
    "CFX awesome_ R10",
    "CFX ema_ribb R11",
    "CFX range_bo R10",
    "CFX stoch_mt R11",
    "CRV range_bo R10",
    "DEGO dual_thr R10",
    "ENA stoch_mt R11",
    "ETHFI zscore_m R11",
    "GALA awesome_ R10",
    "GALA dual_thr R10",
    "INJ Ichi 1H",
    "INJ stoch_mt R11",
    "ONDO awesome_ R10",
    "ONDO zscore_m R11",
    "QNT dual_thr R10",
    "TIA Ichi 4H",
    "TIA dual_thr R10",
    "TIA range_bo R10",
    "VIRTUAL awesome_ R10",
    "VIRTUAL dual_thr R10",
    "VVV dual_thr R10",
    "VVV range_bo R10",
    "W dual_thr R10",
    "W range_bo R10",
    "ZEC dual_thr R10",
}

UPGRADE_COINS = {"PENGU", "POL", "AVAX", "ALICE", "DASH", "KAS"}

UPGRADES = [
    ("PENGU Ichi4H Trail",  "PENGU", "penguusdt",  "ichimoku_cloud",     "4h", 3.0, 0.0, 4.0, 0.01),
    ("POL Ichi4H Trail",    "POL",   "polusdt",    "ichimoku_cloud",     "4h", 3.0, 0.0, 4.0, 0.01),
    ("AVAX EMA Ribbon 4H",  "AVAX",  "avaxusdt",   "ema_ribbon",         "4h", 2.5, 5.0, 4.0, 0.02),
    ("ALICE AO 4H",         "ALICE", "aliceusdt",  "awesome_oscillator", "4h", 2.0, 3.0, 2.5, 0.01),
    ("DASH Ichi4H",         "DASH",  "dashusdt",   "ichimoku_cloud",     "4h", 1.0, 2.0, 2.0, 0.01),
    ("KAS EMA Ribbon 4H",   "KAS",   "kasusdt",    "ema_ribbon",         "4h", 1.5, 4.0, 3.0, 0.01),
]

R13_SPEC = [
    ("1000PEPE", "dualthrust_adx", "1h", 2.0, 4.0, 3.0),
    ("WLD",      "dualthrust_adx", "1h", 2.0, 4.0, 3.0),
    ("ARB",      "dualthrust_adx", "1h", 2.0, 4.0, 3.0),
    ("XAUUSD",   "ribbon_ao",      "4h", 2.0, 4.0, 3.0),
    ("ICP",      "ribbon_ao",      "4h", 2.0, 4.0, 3.0),
    ("XLM",      "ribbon_ao",      "4h", 2.0, 4.0, 3.0),
    ("AAVE",     "dualthrust_adx", "1h", 2.0, 4.0, 3.0),
    ("HBAR",     "ribbon_ao",      "1h", 2.0, 4.0, 3.0),
    ("BAN",      "ribbon_ao",      "1h", 2.0, 4.0, 3.0),
    ("XAI",      "ribbon_rsi_vol", "4h", 2.0, 4.0, 3.0),
    ("IP",       "ribbon_rsi_vol", "4h", 2.0, 4.0, 3.0),
]

ALL_SIGNAL_KEYS = [
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

ALL_SIGNALS_OFF: dict = {s: {"enabled": False} for s in ALL_SIGNAL_KEYS}

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
    "ema_fast": 9, "ema_slow": 21, "ema_trend": 50, "ema_fast2": 5, "ema_slow2": 13,
    "rsi_period": 14, "rsi_min": 45, "rsi_max": 65,
    "rsi_long_min": 45, "rsi_long_max": 65, "rsi_short_min": 35, "rsi_short_max": 55,
    "atr_period": 14, "atr_min": 0.0,
    "partial_tp_enabled": False, "partial_tp_pct": 0.3, "partial_tp_atr_mult": 2.0,
    "move_sl_to_be_after_tp1": True, "breakeven_buffer_atr_mult": 0.5,
    "atr_trail_mult_post_tp1": 4.0,
    "volume_mult": 1.0, "volume_max_mult": None,
    "cooldown_candles_after_close": 0, "cooldown_candles_after_sl": 0,
    "min_rr_ratio": 0,
    "commission_rate": 0.0004, "slippage_rate": 0.00015,
    "crossover_lookback": 2,
    "weekend_trading_enabled": False, "weekend_size_reduction": 0.5,
    "ema_slope_period": 5, "ema_slope_min": 0.0,
    "ichimoku_tenkan": 9, "ichimoku_kijun": 26, "ichimoku_senkou_b": 52,
    "vol_expansion_threshold": 1.8, "vol_expansion_lookback": 1,
    "vol_expansion_atr_ma_period": 20, "vol_expansion_ema_trend_period": 50,
    "supertrend_multiplier": 2.0, "supertrend_atr_period": 14,
    "trading_hours": {"enabled": True, "start_utc": 3, "end_utc": 20},
    "regime_filter": {"enabled": True, "skip_ranging": False},
    "flexible_cooldown": {"enabled": False, "min_quality_score": 0.7,
                          "cooldown_reduction_factor": 0.5, "log_overrides": True},
    "adaptive_sizing": {"enabled": False},
    "pyramiding": {"enabled": False},
    "mtd_accelerator": {"enabled": False},
    "signal_scorer": {"enabled": False},
    "ai_layer": {"enabled": False},
    "body_dominance_min_body": 0.65, "body_dominance_min_mom": 0.02, "body_dominance_min_vol": 1.5,
    "squeeze_release_low": 0.7, "squeeze_release_high": 0.8, "squeeze_release_min_mom4": 0.0,
    "mr_rsi_oversold": 30, "mr_rsi_overbought": 70, "mr_atr_sl_mult": 1.5, "mr_atr_tp_mult": 2.0,
    "range_bounce_atr_sl_mult": 1.5, "range_bounce_atr_tp_mult": 2.0, "range_bounce_proximity_pct": 0.015,
    "bb_period": 20, "bb_std": 2.0, "bb_squeeze_percentile": 0.3, "bb_volume_mult": 1.2,
    "swing_lookback": 5, "divergence_lookback": 20, "regime_lookback": 20,
    "stoch_k_period": 14, "stoch_d_period": 3, "stoch_oversold": 20.0, "stoch_overbought": 80.0,
    "zscore_period": 20, "zscore_threshold": 2.0, "zscore_atr_sl_mult": 1.5, "zscore_atr_tp_mult": 2.0,
    "dual_thrust_k": 0.5, "dual_thrust_lookback": 20,
    "dualthrust_adx_threshold": 25.0,
    "zscore_stoch_threshold": 2.0, "zscore_stoch_oversold": 20.0, "zscore_stoch_overbought": 80.0,
    "ichi_adx_threshold": 25.0,
    "ribbon_ao_period": 5,
}


# ---------------------------------------------------------------------------
# BotDef
# ---------------------------------------------------------------------------

class BotDef:
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
        is_r13: bool = False,
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
        self.config_file = config_file
        self.is_r13 = is_r13


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

    sigs = copy.deepcopy(ALL_SIGNALS_OFF)
    if signal_key == "ema_crossover":
        sigs["ema_crossover"] = {"enabled": True}
        sigs["ema_fast_crossover"] = {"enabled": True}
    else:
        sigs[signal_key] = {"enabled": True}

    if "ichi" in signal_key or signal_key == "ichimoku_cloud":
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52

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
# Data helpers
# ---------------------------------------------------------------------------

def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    return df.resample("4h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()


def load_signal_data(prefix: str, tf: str) -> pd.DataFrame:
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
    if tf != "15m":
        return pd.DataFrame()
    for suffix in ("_1h_2y.csv", "_1h_5y.csv"):
        path = DATA_DIR / f"{prefix}{suffix}"
        if path.exists():
            return load_ohlcv(str(path))
    return pd.DataFrame()


def data_span_months(df: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    idx = pd.to_datetime(df.index)
    return (idx[-1] - idx[0]).days / 30.44


def filter_last_year(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df = df.copy()
    df.index = idx
    cutoff = df.index[-1] - pd.DateOffset(months=12)
    return df[df.index >= cutoff].copy()


def _load_config_file(rel_path: str) -> Optional[dict]:
    p = ROOT / rel_path
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def _coin_to_prefix(coin: str) -> str:
    coin_lower = coin.lower()
    if coin_lower == "xauusd":
        return "xauusd"
    return coin_lower + "usdt"


# ---------------------------------------------------------------------------
# Bot registry — mirrors backtest_96_audited.py exactly
# ---------------------------------------------------------------------------

def _build_bot_list() -> list[BotDef]:
    bots: list[BotDef] = []

    existing_defs = [
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
        if coin in UPGRADE_COINS:
            continue
        cfg_loaded = _load_config_file(cfg_file) if cfg_file else None
        bots.append(BotDef(
            label=label, coin=coin, prefix=prefix, signal_key=signal,
            tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=risk,
            config_file=cfg_file if cfg_loaded else None,
        ))

    for label, coin, prefix, signal, tf, sl, tp, trail, risk in UPGRADES:
        bots.append(BotDef(
            label=label, coin=coin, prefix=prefix, signal_key=signal,
            tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=risk,
        ))

    # Round 10
    r10_path = DATA_DIR / "round10_results.json"
    r10_added: set[str] = set()

    if r10_path.exists():
        with open(r10_path) as f:
            r10_data = json.load(f)
        for vbot in r10_data.get("passed", []):
            if vbot.get("engine_pf", 0) < 1.2 or vbot.get("trades", 0) < 6:
                continue
            coin = vbot["coin"]
            if coin in UPGRADE_COINS:
                continue
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
            bots.append(BotDef(
                label=f"{coin} {signal_key[:8]} R10", coin=coin, prefix=prefix,
                signal_key=signal_key, tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))
    else:
        r10_hardcoded = [
            ("GALA dual_thr R10",    "GALA",     "galausdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("PHA dual_thr R10",     "PHA",      "phausdt",      "dual_thrust",         "4h", 2.0, 4.0, 3.0),
            ("ZEC dual_thr R10",     "ZEC",      "zecusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("FIL range_bo R10",     "FIL",      "filusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("PIXEL dual_thr R10",   "PIXEL",    "pixelusdt",    "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("VVV range_bo R10",     "VVV",      "vvvusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("W range_bo R10",       "W",        "wusdt",        "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("DEGO dual_thr R10",    "DEGO",     "degousdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("VIRTUAL dual_thr R10", "VIRTUAL",  "virtualusdt",  "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("FARTCOIN dual_thr R10","FARTCOIN", "fartcoinusdt", "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("DOT dual_thr R10",     "DOT",      "dotusdt",      "dual_thrust",         "4h", 2.0, 4.0, 3.0),
            ("PIPPIN dual_thr R10",  "PIPPIN",   "pippinusdt",   "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("1000BONK dual_thr R10","1000BONK", "1000bonkusdt", "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ICP dual_thr R10",     "ICP",      "icpusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("XAI awesome_ R10",     "XAI",      "xaiusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("XAI range_bo R10",     "XAI",      "xaiusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("DOT awesome_ R10",     "DOT",      "dotusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("BCH awesome_ R10",     "BCH",      "bchusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("UNI dual_thr R10",     "UNI",      "uniusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("CRV range_bo R10",     "CRV",      "crvusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("AAVE range_bo R10",    "AAVE",     "aaveusdt",     "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("SAND awesome_ R10",    "SAND",     "sandusdt",     "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("GALA awesome_ R10",    "GALA",     "galausdt",     "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("VIRTUAL awesome_ R10", "VIRTUAL",  "virtualusdt",  "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("VVV dual_thr R10",     "VVV",      "vvvusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("DOT range_bo R10",     "DOT",      "dotusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("FIL dual_thr R10",     "FIL",      "filusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("FIL awesome_ R10",     "FIL",      "filusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("W dual_thr R10",       "W",        "wusdt",        "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ZEN awesome_ R10",     "ZEN",      "zenusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("ATOM dual_thr R10",    "ATOM",     "atomusdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ENJ dual_thr R10",     "ENJ",      "enjusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ENJ range_bo R10",     "ENJ",      "enjusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("CFX awesome_ R10",     "CFX",      "cfxusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("CFX range_bo R10",     "CFX",      "cfxusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("APT range_bo R10",     "APT",      "aptusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("TON awesome_ R10",     "TON",      "tonusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("TON range_bo R10",     "TON",      "tonusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("ONDO range_bo R10",    "ONDO",     "ondousdt",     "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("ONDO awesome_ R10",    "ONDO",     "ondousdt",     "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("TIA dual_thr R10",     "TIA",      "tiausdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ANKR dual_thr R10",    "ANKR",     "ankrusdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("XRP dual_thr R10",     "XRP",      "xrpusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("PIXEL awesome_ R10",   "PIXEL",    "pixelusdt",    "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("ADA awesome_ R10",     "ADA",      "adausdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("AXS range_bo R10",     "AXS",      "axsusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("AXS awesome_ R10",     "AXS",      "axsusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("AXS dual_thr R10",     "AXS",      "axsusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ATOM awesome_ R10",    "ATOM",     "atomusdt",     "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("ICP range_bo R10",     "ICP",      "icpusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("QNT dual_thr R10",     "QNT",      "qntusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("IP dual_thr R10",      "IP",       "ipusdt",       "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("SUI dual_thr R10",     "SUI",      "suitusdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("SUI range_bo R10",     "SUI",      "suitusdt",     "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("XAI range_bo R10",     "XAI",      "xaiusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("SAND range_bo R10",    "SAND",     "sandusdt",     "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("AAVE dual_thr R10",    "AAVE",     "aaveusdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
        ]
        for label, coin, prefix, signal, tf, sl, tp, trail in r10_hardcoded:
            if coin in UPGRADE_COINS:
                continue
            key = f"{prefix}_{signal}_{tf}"
            if key in r10_added:
                continue
            r10_added.add(key)
            bots.append(BotDef(
                label=label, coin=coin, prefix=prefix, signal_key=signal,
                tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))

    # Round 11
    r11_path = DATA_DIR / "round11_results.json"
    r11_added: set[str] = set()

    if r11_path.exists():
        with open(r11_path) as f:
            r11_data = json.load(f)
        for vbot in r11_data.get("passed", []):
            if vbot.get("engine_pf", 0) < 1.2 or vbot.get("trades", 0) < 6:
                continue
            coin = vbot["coin"]
            if coin in UPGRADE_COINS:
                continue
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

    # Round 12
    r12_path = DATA_DIR / "round12_results.json"
    r12_added: set[str] = set()

    if r12_path.exists():
        with open(r12_path) as f:
            r12_data = json.load(f)
        for vbot in r12_data.get("passed", []):
            if vbot.get("engine_pf", 0) < 1.2 or vbot.get("trades", 0) < 6:
                continue
            coin = vbot["coin"]
            if coin in UPGRADE_COINS:
                continue
            signal_key = vbot["signal_key"]
            tf = vbot.get("tf", "1h")
            prefix = vbot.get("prefix", coin.lower() + "usdt")
            sl = vbot.get("atr_sl_mult", 2.0)
            tp = vbot.get("atr_tp_mult", 4.0)
            trail = vbot.get("atr_trail_mult", 3.0) or 3.0
            key = f"{prefix}_{signal_key}_{tf}"
            if key in r12_added:
                continue
            r12_added.add(key)
            bots.append(BotDef(
                label=f"{coin} {signal_key[:8]} R12", coin=coin, prefix=prefix,
                signal_key=signal_key, tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))

    # Deduplicate by (prefix, signal_key, tf) — keep first occurrence
    seen_keys: set[str] = set()
    deduped: list[BotDef] = []
    for b in bots:
        key = f"{b.prefix}_{b.signal_key}_{b.tf}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(b)

    # Round 13
    r13_added: set[str] = set()
    r13_spec_map = {
        (coin.upper(), signal, tf): (sl, tp, trail)
        for coin, signal, tf, sl, tp, trail in R13_SPEC
    }

    pipeline_path = DATA_DIR / "pipeline_20260323_104924.json"
    if pipeline_path.exists():
        with open(pipeline_path) as f:
            pipeline_data = json.load(f)
        for ab in pipeline_data.get("audited_bots", []):
            if ab.get("pf", 0) < 1.3 or ab.get("trades", 0) < 15:
                continue
            coin = ab["coin"].upper()
            signal = ab["signal"]
            tf = ab["tf"]
            spec_key = (coin, signal, tf)
            if spec_key not in r13_spec_map:
                continue
            sl, tp, trail = r13_spec_map[spec_key]
            prefix = ab.get("prefix", _coin_to_prefix(coin))
            key = f"{prefix}_{signal}_{tf}_r13"
            if key in r13_added:
                continue
            r13_added.add(key)
            label = f"{coin} {signal[:10]} R13"
            deduped.append(BotDef(
                label=label, coin=coin, prefix=prefix, signal_key=signal,
                tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
                is_r13=True,
            ))
    else:
        for coin, signal, tf, sl, tp, trail in R13_SPEC:
            prefix = _coin_to_prefix(coin)
            key = f"{prefix}_{signal}_{tf}_r13"
            if key in r13_added:
                continue
            r13_added.add(key)
            label = f"{coin} {signal[:10]} R13"
            deduped.append(BotDef(
                label=label, coin=coin.upper(), prefix=prefix, signal_key=signal,
                tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
                is_r13=True,
            ))

    return deduped


# ---------------------------------------------------------------------------
# Per-bot engine runner
# ---------------------------------------------------------------------------

def run_bot_engine(bot: BotDef) -> list[dict]:
    """Run BacktestEngine for one bot. Returns list of trade records with r_mult."""
    signal_df = load_signal_data(bot.prefix, bot.tf)
    if signal_df.empty:
        return []

    if data_span_months(signal_df) < MIN_DATA_MONTHS:
        return []

    trend_df = load_trend_data(bot.prefix, bot.tf)

    signal_1y = filter_last_year(signal_df)
    trend_1y = filter_last_year(trend_df) if not trend_df.empty else pd.DataFrame()

    if len(signal_1y) < 50:
        return []

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

    actual_risk_pct = cfg.get("risk_per_trade", bot.risk_pct)
    risk_amount = ENGINE_BALANCE * actual_risk_pct

    try:
        engine = BacktestEngine(cfg, initial_balance=ENGINE_BALANCE)
        with redirect_stdout(io.StringIO()):
            engine.run(signal_1y, trend_1y if not trend_1y.empty else None)
    except Exception as exc:
        print(f"  [ERROR] {bot.label}: {exc}")
        return []

    trades = engine.state.trades
    if len(trades) < MIN_TRADES:
        return []

    records = []
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
        records.append({
            "bot": bot.label,
            "coin": bot.coin,
            "side": t.side,
            "entry_time": entry_ts,
            "exit_time": exit_ts,
            "r_mult": r_mult,
            "risk_pct": actual_risk_pct,
        })

    return records


# ---------------------------------------------------------------------------
# Wallet replay with monthly + daily tracking
# ---------------------------------------------------------------------------

def replay_wallet(
    all_trades: list[dict],
    bot_set: set[str],
    initial: float,
    max_concurrent: int,
    risk_pct: float,
) -> dict:
    """Replay shared wallet for a given profile. Returns full detail dict."""
    # Filter to only this profile's bot set
    profile_trades = [t for t in all_trades if t["bot"] in bot_set]

    if not profile_trades:
        return {
            "final": initial, "return_pct": 0.0, "max_dd_pct": 0.0,
            "trades_used": 0, "trades_skipped": 0, "monthly": [], "daily": [], "per_bot": {},
        }

    sorted_trades = sorted(profile_trades, key=lambda t: t["entry_time"])

    # Enforce concurrent limit
    active: list[dict] = []
    accepted: list[dict] = []

    for t in sorted_trades:
        active = [a for a in active if a["exit_time"] > t["entry_time"]]
        if len(active) < max_concurrent:
            active.append(t)
            accepted.append(t)

    trades_skipped = len(sorted_trades) - len(accepted)

    # Replay in exit-time order
    accepted_sorted = sorted(accepted, key=lambda t: t["exit_time"])

    balance = initial
    peak = balance
    max_dd = 0.0

    monthly_pnl: dict[str, float] = defaultdict(float)
    monthly_bot_pnl: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    daily_pnl: dict[str, float] = defaultdict(float)
    per_bot_pnl: dict[str, float] = defaultdict(float)
    per_bot_trades: dict[str, int] = defaultdict(int)
    per_bot_wins: dict[str, int] = defaultdict(int)
    per_bot_r: dict[str, float] = defaultdict(float)

    for t in accepted_sorted:
        dollar_pnl = balance * risk_pct * t["r_mult"]
        balance += dollar_pnl
        balance = max(balance, 0.01)

        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

        exit_ts = t["exit_time"]
        month_key = exit_ts.strftime("%Y-%m")
        day_key = exit_ts.strftime("%Y-%m-%d")
        bot_label = t["bot"]

        monthly_pnl[month_key] += dollar_pnl
        monthly_bot_pnl[month_key][bot_label] += dollar_pnl
        daily_pnl[day_key] += dollar_pnl
        per_bot_pnl[bot_label] += dollar_pnl
        per_bot_trades[bot_label] += 1
        per_bot_r[bot_label] += t["r_mult"]
        if t["r_mult"] > 0:
            per_bot_wins[bot_label] += 1

    return_pct = (balance - initial) / initial * 100.0

    # Monthly list with running balance
    monthly_list = []
    running_bal = initial
    for month in sorted(monthly_pnl.keys()):
        pnl = monthly_pnl[month]
        pct = pnl / running_bal * 100.0 if running_bal > 0 else 0.0
        running_bal += pnl
        top_bots = sorted(monthly_bot_pnl[month].items(), key=lambda x: x[1], reverse=True)[:5]
        monthly_list.append({
            "month": month,
            "pnl": round(pnl, 2),
            "pct": round(pct, 1),
            "balance_end": round(running_bal, 2),
            "top_bots": [(b, round(p, 2)) for b, p in top_bots],
        })

    # Daily list (all days — full history, not just recent 2 months)
    daily_list = [
        {"day": d, "pnl": round(daily_pnl[d], 2)}
        for d in sorted(daily_pnl.keys())
    ]

    # Per-bot summary
    per_bot_list = {}
    for label in per_bot_pnl:
        n = per_bot_trades[label]
        wins = per_bot_wins[label]
        per_bot_list[label] = {
            "pnl": round(per_bot_pnl[label], 2),
            "trades": n,
            "wins": wins,
            "wr_pct": round(wins / n * 100, 1) if n > 0 else 0.0,
            "r_total": round(per_bot_r[label], 3),
        }

    return {
        "final": round(balance, 2),
        "return_pct": round(return_pct, 1),
        "max_dd_pct": round(max_dd * 100.0, 1),
        "trades_used": len(accepted),
        "trades_skipped": trades_skipped,
        "monthly": monthly_list,
        "daily": daily_list,
        "per_bot": per_bot_list,
    }


# ---------------------------------------------------------------------------
# Monthly avg helper
# ---------------------------------------------------------------------------

def monthly_avg(result: dict) -> float:
    """Average monthly PnL in dollars."""
    monthly = result.get("monthly", [])
    if not monthly:
        return 0.0
    total_pnl = sum(m["pnl"] for m in monthly)
    return total_pnl / len(monthly)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    sep = "=" * 90

    print(sep)
    print("5 DEPLOYMENT PROFILES COMPARISON  —  96 Walk-Forward Audited Bots")
    print(sep)
    print()

    # ------------------------------------------------------------------
    # Step 1: Build and filter bot list
    # ------------------------------------------------------------------
    print("Step 1: Building 96 audited bot list...")
    all_bots = _build_bot_list()
    bots = [b for b in all_bots if b.label not in REMOVE_BOTS]
    removed = [b.label for b in all_bots if b.label in REMOVE_BOTS]
    print(f"  Full list: {len(all_bots)} bots | Removed {len(removed)} FAIL bots | {len(bots)} audited bots")

    unmatched = REMOVE_BOTS - {b.label for b in all_bots}
    if unmatched:
        print(f"  WARNING: {len(unmatched)} REMOVE labels not found in bot list:")
        for u in sorted(unmatched):
            print(f"    - {u!r}")

    # ------------------------------------------------------------------
    # Step 2: Run BacktestEngine once per bot
    # ------------------------------------------------------------------
    print()
    print("Step 2: Running BacktestEngine for each bot (one-time)...")
    print()

    all_trade_records: list[dict] = []
    skipped_bots: list[str] = []
    bot_pnl_map: dict[str, float] = {}

    for i, bot in enumerate(bots, 1):
        if i % 10 == 0 or i == 1:
            print(f"  [{i:3d}/{len(bots)}] Processing... ({len(all_trade_records)} trades so far)")

        records = run_bot_engine(bot)
        if records:
            all_trade_records.extend(records)
            bot_pnl = sum(r["r_mult"] for r in records)
            bot_pnl_map[bot.label] = bot_pnl
        else:
            skipped_bots.append(bot.label)
            bot_pnl_map[bot.label] = 0.0

    active_bots = len(bots) - len(skipped_bots)
    print(f"\n  Done: {active_bots}/{len(bots)} bots produced trades. Total pool: {len(all_trade_records)} trades")
    if skipped_bots:
        shown = skipped_bots[:8]
        rest = f" +{len(skipped_bots) - 8} more" if len(skipped_bots) > 8 else ""
        print(f"  Skipped: {', '.join(shown)}{rest}")

    # ------------------------------------------------------------------
    # Step 3: Determine bot sets for each profile
    # ------------------------------------------------------------------
    print()
    print("Step 3: Determining bot sets...")

    # All active bots (produced trades)
    all_active_labels = {
        b.label for b in bots if b.label not in skipped_bots
    }

    # Sort active bots by total R-multiple (proxy for PnL contribution)
    sorted_by_r = sorted(
        all_active_labels,
        key=lambda label: bot_pnl_map.get(label, 0.0),
        reverse=True,
    )

    # top_5: hardcoded per spec (filter to only those that are active)
    top_5_set = {label for label in TOP_5_LABELS if label in all_active_labels}
    missing_top5 = [label for label in TOP_5_LABELS if label not in all_active_labels]
    if missing_top5:
        print(f"  WARNING: top_5 missing from active bots: {missing_top5}")
        # Supplement with next best bots
        supplement = [b for b in sorted_by_r if b not in top_5_set]
        top_5_set.update(supplement[:len(missing_top5)])

    top_10_set = set(sorted_by_r[:10])
    top_50_set = set(sorted_by_r[:50])

    print(f"  all_96:  {len(all_active_labels)} active bots")
    print(f"  top_50:  {len(top_50_set)} bots  (top by total R)")
    print(f"  top_10:  {len(top_10_set)} bots")
    print(f"  top_5:   {len(top_5_set)} bots  -> {sorted(top_5_set)}")

    # ------------------------------------------------------------------
    # Step 4: Replay wallet 5 times
    # ------------------------------------------------------------------
    print()
    print("Step 4: Replaying wallet for each profile...")
    print()

    bot_set_map = {
        "all_96": all_active_labels,
        "top_5": top_5_set,
        "top_10": top_10_set,
        "top_50": top_50_set,
    }

    profile_results: list[dict] = []

    for prof in PROFILES:
        bot_set = bot_set_map[prof["bots"]]
        result = replay_wallet(
            all_trade_records,
            bot_set=bot_set,
            initial=prof["capital"],
            max_concurrent=prof["max_concurrent"],
            risk_pct=prof["risk_pct"],
        )
        avg_monthly = monthly_avg(result)
        n_months = len(result["monthly"])
        profile_results.append({
            "profile": prof,
            "result": result,
            "avg_monthly": avg_monthly,
            "n_months": n_months,
        })

    # ------------------------------------------------------------------
    # Step 5: Print comparison table
    # ------------------------------------------------------------------
    print(sep)
    print("5 DEPLOYMENT PROFILES COMPARISON")
    print(sep)
    print()
    hdr = f"{'Profile':<12} {'Capital':>8} {'Risk':>6} {'Lev':>5} {'Bots':>5} {'Conc':>5}  "
    hdr += f"{'Final$':>10} {'Return%':>9} {'DD%':>6} {'Trades':>7} {'Monthly_avg':>12}"
    print(hdr)
    print("-" * len(hdr))

    for pr in profile_results:
        prof = pr["profile"]
        res = pr["result"]
        avg_m = pr["avg_monthly"]
        row = (
            f"{prof['name']:<12} "
            f"${prof['capital']:>7,.0f} "
            f"{prof['risk_pct']*100:>5.1f}% "
            f"{prof['leverage']:>4d}x "
            f"{len(bot_set_map[prof['bots']]):>5d} "
            f"{prof['max_concurrent']:>5d}  "
            f"${res['final']:>9,.2f} "
            f"{res['return_pct']:>+8.1f}% "
            f"{res['max_dd_pct']:>5.1f}% "
            f"{res['trades_used']:>7d} "
            f"${avg_m:>10.2f}/mo"
        )
        print(row)

    print()

    # ------------------------------------------------------------------
    # Step 6: Monthly breakdown per profile
    # ------------------------------------------------------------------
    for pr in profile_results:
        prof = pr["profile"]
        res = pr["result"]
        monthly = res["monthly"]
        if not monthly:
            print(f"[{prof['name']}] No monthly data.")
            continue

        print(f"[{prof['name']}] Monthly breakdown ({len(monthly)} months):")
        print(f"  {'Month':<9} {'PnL$':>10} {'Pct%':>7} {'Balance$':>12}")
        print(f"  {'-'*9} {'-'*10} {'-'*7} {'-'*12}")
        for m in monthly:
            sign = "+" if m["pnl"] >= 0 else ""
            print(
                f"  {m['month']:<9} "
                f"{sign}{m['pnl']:>9.2f} "
                f"{m['pct']:>+6.1f}% "
                f"${m['balance_end']:>11.2f}"
            )
        print()

    # ------------------------------------------------------------------
    # Step 7: Per-bot contributions for each profile (top 10)
    # ------------------------------------------------------------------
    print(sep)
    print("PER-BOT CONTRIBUTIONS (top 10 by PnL per profile)")
    print(sep)

    for pr in profile_results:
        prof = pr["profile"]
        res = pr["result"]
        per_bot = res["per_bot"]
        if not per_bot:
            continue
        top_bots = sorted(per_bot.items(), key=lambda x: x[1]["pnl"], reverse=True)[:10]
        print(f"\n[{prof['name']}]  capital=${prof['capital']:.0f}  risk={prof['risk_pct']*100:.1f}%  {len(per_bot)} bots active")
        print(f"  {'Bot':<35} {'PnL$':>9} {'Trades':>7} {'WR%':>6} {'R_total':>8}")
        print(f"  {'-'*35} {'-'*9} {'-'*7} {'-'*6} {'-'*8}")
        for name, info in top_bots:
            sign = "+" if info["pnl"] >= 0 else ""
            print(
                f"  {name:<35} "
                f"{sign}{info['pnl']:>8.2f} "
                f"{info['trades']:>7d} "
                f"{info['wr_pct']:>5.1f}% "
                f"{info['r_total']:>+7.3f}"
            )

    print()

    # ------------------------------------------------------------------
    # Step 8: Save JSON output
    # ------------------------------------------------------------------
    output = {
        "meta": {
            "description": "5 deployment profiles, 96 audited bots, same R-multiple trade pool",
            "engine_balance": ENGINE_BALANCE,
            "total_trade_pool": len(all_trade_records),
            "active_bots": active_bots,
        },
        "profiles": [],
    }

    for pr in profile_results:
        prof = pr["profile"]
        res = pr["result"]
        bot_set = bot_set_map[prof["bots"]]
        output["profiles"].append({
            "name": prof["name"],
            "settings": {
                "capital": prof["capital"],
                "risk_pct": prof["risk_pct"],
                "leverage": prof["leverage"],
                "max_concurrent": prof["max_concurrent"],
                "bots": prof["bots"],
                "active_bots_in_set": len(bot_set),
            },
            "summary": {
                "final": res["final"],
                "return_pct": res["return_pct"],
                "max_dd_pct": res["max_dd_pct"],
                "trades_used": res["trades_used"],
                "trades_skipped": res["trades_skipped"],
                "avg_monthly_pnl": round(pr["avg_monthly"], 2),
                "n_months": pr["n_months"],
            },
            "monthly": res["monthly"],
            "daily": res["daily"],
            "per_bot": res["per_bot"],
        })

    out_path = DATA_DIR / "profiles_5_backtest.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(sep)
    print(f"Saved to {out_path}")
    print(sep)


if __name__ == "__main__":
    main()
