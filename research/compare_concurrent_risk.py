"""compare_concurrent_risk.py — Compare 9 configurations of max_concurrent × risk_pct.

Approach:
  1. Run all 136 bots once through BacktestEngine to collect R-multiple trade records.
  2. Replay the shared wallet 9 times with different (max_concurrent, risk_pct) params.
  3. Print comparison table sorted by risk-adjusted return (Return% / MaxDD%).

R-multiple notes:
  - Engine runs with $10K virtual balance per bot.
  - R-mult = trade.pnl / (10_000 * actual_config_risk_pct)
  - BTC (5%), WIF (3%), AVAX (2%) → larger R-multiples reflecting real alpha.
  - Shared wallet applies the test risk_pct uniformly: dollar_pnl = balance * risk_pct * r_mult.

Bot list: same 136 bots as r13_combined_backtest.py.
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
MIN_DATA_MONTHS = 12
MIN_TRADES = 6

# ---------------------------------------------------------------------------
# Test matrix: 9 (max_concurrent, risk_pct) combinations
# ---------------------------------------------------------------------------
TEST_CONFIGS = [
    # (label,              max_concurrent, risk_pct)
    ("baseline (current)",  5,  0.010),
    ("conservative",        5,  0.005),
    ("ultra-safe",          5,  0.003),
    ("more positions",     10,  0.010),
    ("balanced",           10,  0.005),
    ("safe + more",        10,  0.003),
    ("aggressive",         15,  0.010),
    ("moderate + many",    15,  0.005),
    ("diversified",        15,  0.003),
]

# ---------------------------------------------------------------------------
# Upgrade coins — same as r13_combined_backtest.py
# ---------------------------------------------------------------------------
UPGRADE_COINS = {"PENGU", "POL", "AVAX", "ALICE", "DASH", "KAS"}

UPGRADES = [
    # (label, coin, prefix, signal, tf, sl, tp, trail, risk_pct)
    ("PENGU Ichi4H Trail",  "PENGU", "penguusdt",  "ichimoku_cloud",     "4h", 3.0, 0.0, 4.0, 0.01),
    ("POL Ichi4H Trail",    "POL",   "polusdt",    "ichimoku_cloud",     "4h", 3.0, 0.0, 4.0, 0.01),
    ("AVAX EMA Ribbon 4H",  "AVAX",  "avaxusdt",   "ema_ribbon",         "4h", 2.5, 5.0, 4.0, 0.02),
    ("ALICE AO 4H",         "ALICE", "aliceusdt",  "awesome_oscillator", "4h", 2.0, 3.0, 2.5, 0.01),
    ("DASH Ichi4H",         "DASH",  "dashusdt",   "ichimoku_cloud",     "4h", 1.0, 2.0, 2.0, 0.01),
    ("KAS EMA Ribbon 4H",   "KAS",   "kasusdt",    "ema_ribbon",         "4h", 1.5, 4.0, 3.0, 0.01),
]

# ---------------------------------------------------------------------------
# R13 bots
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Signal keys
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Base engine config
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
# Bot definition
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
# Bot registry — mirrors r13_combined_backtest.py exactly
# ---------------------------------------------------------------------------

def _build_bot_list() -> list[BotDef]:
    bots: list[BotDef] = []

    # Existing bots (pre-R10)
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

    # Upgrade bots
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
            ("GALA dual_thr",    "GALA",     "galausdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("PHA dual_thr",     "PHA",      "phausdt",      "dual_thrust",         "4h", 2.0, 4.0, 3.0),
            ("ZEC dual_thr",     "ZEC",      "zecusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("FIL range_bo",     "FIL",      "filusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("PIXEL dual_thr",   "PIXEL",    "pixelusdt",    "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("VVV range_bo",     "VVV",      "vvvusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("W range_bo",       "W",        "wusdt",        "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("DEGO dual_thr",    "DEGO",     "degousdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("VIRTUAL dual_thr", "VIRTUAL",  "virtualusdt",  "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("FARTCOIN dual_thr","FARTCOIN", "fartcoinusdt", "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("DOT dual_thr",     "DOT",      "dotusdt",      "dual_thrust",         "4h", 2.0, 4.0, 3.0),
            ("PIPPIN dual_thr",  "PIPPIN",   "pippinusdt",   "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("1000BONK dual_thr","1000BONK", "1000bonkusdt", "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ICP dual_thr",     "ICP",      "icpusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("XAI awesome_",     "XAI",      "xaiusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("XAI range_bo",     "XAI",      "xaiusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("DOT awesome_",     "DOT",      "dotusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("BCH awesome_",     "BCH",      "bchusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("UNI dual_thr",     "UNI",      "uniusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("CRV range_bo",     "CRV",      "crvusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("AAVE range_bo",    "AAVE",     "aaveusdt",     "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("SAND awesome_",    "SAND",     "sandusdt",     "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("GALA awesome_",    "GALA",     "galausdt",     "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("VIRTUAL awesome_", "VIRTUAL",  "virtualusdt",  "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("SAND AO 4H",       "SAND",     "sandusdt",     "awesome_oscillator",  "4h", 1.5, 4.0, 3.0),
            ("VVV dual_thr",     "VVV",      "vvvusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ZEC range_bo",     "ZEC",      "zecusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("DOT range_bo",     "DOT",      "dotusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("FIL dual_thr",     "FIL",      "filusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("FIL awesome_",     "FIL",      "filusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("W dual_thr",       "W",        "wusdt",        "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ZEN awesome_",     "ZEN",      "zenusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("ATOM dual_thr",    "ATOM",     "atomusdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ENJ dual_thr",     "ENJ",      "enjusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ENJ range_bo",     "ENJ",      "enjusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("CFX awesome_",     "CFX",      "cfxusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("CFX range_bo",     "CFX",      "cfxusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("APT range_bo",     "APT",      "aptusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("TON awesome_",     "TON",      "tonusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("TON range_bo",     "TON",      "tonusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("ONDO range_bo",    "ONDO",     "ondousdt",     "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("ONDO awesome_",    "ONDO",     "ondousdt",     "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("XAI awesome_2",    "XAI",      "xaiusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("DOT awesome_2",    "DOT",      "dotusdt",      "awesome_oscillator",  "4h", 1.5, 4.0, 3.0),
            ("ZEN awesome_2",    "ZEN",      "zenusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("TIA dual_thr",     "TIA",      "tiausdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ANKR dual_thr",    "ANKR",     "ankrusdt",     "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("XRP dual_thr",     "XRP",      "xrpusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("PIXEL awesome_",   "PIXEL",    "pixelusdt",    "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("ADA awesome_",     "ADA",      "adausdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("AXS range_bo",     "AXS",      "axsusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("AXS awesome_",     "AXS",      "axsusdt",      "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("AXS dual_thr",     "AXS",      "axsusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("ATOM awesome_",    "ATOM",     "atomusdt",     "awesome_oscillator",  "1h", 1.5, 4.0, 3.0),
            ("ICP range_bo",     "ICP",      "icpusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("FIL range_bo2",    "FIL",      "filusdt",      "range_bounce",        "1h", 1.5, 2.0, 2.0),
            ("QNT dual_thr",     "QNT",      "qntusdt",      "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("VIRTUAL dual_thr", "VIRTUAL",  "virtualusdt",  "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("IP dual_thr",      "IP",       "ipusdt",       "dual_thrust",         "1h", 2.0, 4.0, 3.0),
            ("SAND AO2 4H",      "SAND",     "sandusdt",     "awesome_oscillator",  "4h", 1.5, 4.0, 3.0),
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
    else:
        r11_hardcoded = [
            ("BAN zscore_m",  "BAN",  "banusdt",  "zscore_meanrev", "1h", 1.5, 2.0, 2.0),
            ("ADA stoch_mtf", "ADA",  "adausdt",  "stoch_mtf",      "4h", 2.0, 4.0, 3.0),
            ("CFX ema_rib",   "CFX",  "cfxusdt",  "ema_ribbon",     "4h", 2.0, 4.0, 3.0),
            ("ICP ema_rib",   "ICP",  "icpusdt",  "ema_ribbon",     "4h", 2.0, 4.0, 3.0),
        ]
        for label, coin, prefix, signal, tf, sl, tp, trail in r11_hardcoded:
            if coin in UPGRADE_COINS:
                continue
            key = f"{prefix}_{signal}_{tf}"
            if key in r11_added:
                continue
            r11_added.add(key)
            bots.append(BotDef(
                label=label, coin=coin, prefix=prefix, signal_key=signal,
                tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
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

    # R13 bots
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
        print("  WARNING: pipeline file not found, using hardcoded R13 spec.")
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
# Per-bot engine runner — collect R-multiple trade records
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
    risk_amount = 10_000.0 * actual_risk_pct

    try:
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
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
# Wallet replay
# ---------------------------------------------------------------------------

def replay_wallet(
    all_trades: list[dict],
    initial: float = INITIAL_WALLET,
    max_concurrent: int = 5,
    risk_pct: float = 0.01,
) -> dict:
    """Replay shared wallet with given parameters.

    R-multiple was computed using ACTUAL engine risk (BTC=5%, WIF=3%, etc.)
    so those bots' R-multiples already reflect their real alpha.
    We apply a uniform risk_pct here so the wallet scales consistently.
    """
    if not all_trades:
        return {
            "final": initial, "return_pct": 0.0,
            "max_dd_pct": 0.0, "trades_used": 0, "trades_skipped": 0,
        }

    sorted_trades = sorted(all_trades, key=lambda t: t["entry_time"])

    # Enforce concurrent limit: assign which trades are accepted
    active: list[dict] = []
    accepted: list[dict] = []

    for t in sorted_trades:
        # Expire trades that have closed before this entry
        active = [a for a in active if a["exit_time"] > t["entry_time"]]
        if len(active) < max_concurrent:
            active.append(t)
            accepted.append(t)

    trades_used = len(accepted)
    trades_skipped = len(sorted_trades) - trades_used

    # Replay in exit-time order
    accepted_sorted = sorted(accepted, key=lambda t: t["exit_time"])

    balance = initial
    peak = balance
    max_dd = 0.0

    for t in accepted_sorted:
        dollar_pnl = balance * risk_pct * t["r_mult"]
        balance += dollar_pnl
        balance = max(balance, 0.01)
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return_pct = (balance - initial) / initial * 100.0

    return {
        "final": round(balance, 2),
        "return_pct": round(return_pct, 1),
        "max_dd_pct": round(max_dd * 100.0, 1),
        "trades_used": trades_used,
        "trades_skipped": trades_skipped,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 90)
    print("CONCURRENT x RISK COMPARISON  |  $200 wallet  |  136-bot portfolio (R13)")
    print("=" * 90)
    print("Step 1: Running all bots through BacktestEngine (once)...")
    print()

    bots = _build_bot_list()
    print(f"Built {len(bots)} bot definitions.")

    all_trade_records: list[dict] = []
    skipped: list[str] = []

    for i, bot in enumerate(bots, 1):
        if i % 20 == 0:
            print(f"  Progress: {i}/{len(bots)} bots, {len(all_trade_records)} trades collected...")

        records = run_bot_engine(bot)
        if records:
            all_trade_records.extend(records)
        else:
            skipped.append(bot.label)

    total_bots_active = len(bots) - len(skipped)
    print(f"\nCompleted: {total_bots_active} bots active, {len(skipped)} skipped.")
    print(f"Total raw trade pool: {len(all_trade_records)} trades")
    print()

    # Step 2: Replay 9 times
    print("Step 2: Replaying wallet with 9 configurations...\n")

    results = []
    for label, max_conc, risk in TEST_CONFIGS:
        res = replay_wallet(
            all_trade_records,
            initial=INITIAL_WALLET,
            max_concurrent=max_conc,
            risk_pct=risk,
        )
        risk_adj = (
            round(res["return_pct"] / res["max_dd_pct"], 1)
            if res["max_dd_pct"] > 0.0
            else float("inf")
        )
        results.append({
            "label": label,
            "max_conc": max_conc,
            "risk_pct": risk,
            "final": res["final"],
            "return_pct": res["return_pct"],
            "max_dd_pct": res["max_dd_pct"],
            "trades_used": res["trades_used"],
            "trades_skipped": res["trades_skipped"],
            "risk_adj": risk_adj,
        })

    # ---------------------------------------------------------------------------
    # Print comparison table
    # ---------------------------------------------------------------------------
    print(f"CONCURRENT x RISK COMPARISON  ($200 wallet, {total_bots_active} active bots, {len(all_trade_records)} trade pool)")
    print()
    header = (
        f"{'#':<3} {'Label':<22} {'MaxConc':>8} {'Risk%':>6} "
        f"{'Final$':>9} {'Return%':>8} {'MaxDD%':>7} "
        f"{'Trades':>7} {'Skipped':>8} {'RiskAdj':>8}"
    )
    separator = "-" * len(header)
    print(header)
    print(separator)

    best_risk_adj = max(r["risk_adj"] for r in results)
    best_return = max(r["return_pct"] for r in results)
    lowest_dd = min(r["max_dd_pct"] for r in results)

    for i, r in enumerate(results, 1):
        flags = []
        if r["risk_adj"] == best_risk_adj:
            flags.append("BEST-ADJ")
        if r["return_pct"] == best_return:
            flags.append("BEST-RET")
        if r["max_dd_pct"] == lowest_dd:
            flags.append("LOWEST-DD")
        flag_str = "  <-- " + " | ".join(flags) if flags else ""

        print(
            f"{i:<3} {r['label']:<22} {r['max_conc']:>8} {r['risk_pct']*100:>5.1f}% "
            f"{'$' + str(r['final']):>9} {r['return_pct']:>+7.1f}% {r['max_dd_pct']:>6.1f}% "
            f"{r['trades_used']:>7} {r['trades_skipped']:>8} {r['risk_adj']:>8.1f}"
            f"{flag_str}"
        )

    print(separator)
    print()
    print("RiskAdj = Return% / MaxDD%  (higher = better risk-adjusted return)")
    print()

    # ---------------------------------------------------------------------------
    # Best config summary
    # ---------------------------------------------------------------------------
    best = max(results, key=lambda r: r["risk_adj"])
    print("=" * 90)
    print(f"BEST RISK-ADJUSTED CONFIG: [{best['label']}]")
    print(
        f"  Max Concurrent: {best['max_conc']}  |  Risk/Trade: {best['risk_pct']*100:.1f}%  "
        f"|  Final: ${best['final']}  |  Return: {best['return_pct']:+.1f}%  "
        f"|  MaxDD: {best['max_dd_pct']:.1f}%  |  RiskAdj: {best['risk_adj']:.1f}"
    )
    print("=" * 90)
    print()

    # ---------------------------------------------------------------------------
    # Group analysis: effect of max_concurrent (fixed risk 1%)
    # ---------------------------------------------------------------------------
    print("EFFECT OF MAX_CONCURRENT  (risk = 1.0%):")
    print(f"  {'MaxConc':>8} {'Return%':>8} {'MaxDD%':>7} {'Trades':>7} {'Skipped':>8} {'RiskAdj':>8}")
    for r in results:
        if abs(r["risk_pct"] - 0.01) < 1e-9:
            print(
                f"  {r['max_conc']:>8} {r['return_pct']:>+7.1f}% {r['max_dd_pct']:>6.1f}% "
                f"{r['trades_used']:>7} {r['trades_skipped']:>8} {r['risk_adj']:>8.1f}"
            )
    print()

    # ---------------------------------------------------------------------------
    # Group analysis: effect of risk_pct (fixed concurrent 10)
    # ---------------------------------------------------------------------------
    print("EFFECT OF RISK_PCT  (max_concurrent = 10):")
    print(f"  {'Risk%':>6} {'Return%':>8} {'MaxDD%':>7} {'Trades':>7} {'RiskAdj':>8}")
    for r in results:
        if r["max_conc"] == 10:
            print(
                f"  {r['risk_pct']*100:>5.1f}% {r['return_pct']:>+7.1f}% {r['max_dd_pct']:>6.1f}% "
                f"{r['trades_used']:>7} {r['risk_adj']:>8.1f}"
            )
    print()

    # ---------------------------------------------------------------------------
    # Summary note on R-multiple methodology
    # ---------------------------------------------------------------------------
    print("NOTE: R-multiple methodology")
    print("  Each bot runs with $10K engine balance using its ACTUAL config risk_pct.")
    print("  R-mult = trade_pnl / (10_000 * actual_risk_pct)")
    print("  BTC (5%), WIF/ARC (3%), AVAX (2%) → R-multiples reflect full alpha.")
    print("  Wallet replay: dollar_pnl = balance * test_risk_pct * r_mult")
    print("  All 9 tests use the same trade pool — only replay parameters change.")

    # ---------------------------------------------------------------------------
    # Fine-tune grid: target realistic 1000%/yr (backtest ~2,500%)
    # ---------------------------------------------------------------------------
    # risk_pct values are fractions (same scale as TEST_CONFIGS: 0.01 = 1.0%)
    FINE_CONFIGS = [
        # (max_concurrent, risk_pct, initial_balance, label)
        (8,  0.010, 200.0,  "8pos 1.0%"),
        (8,  0.008, 200.0,  "8pos 0.8%"),
        (8,  0.007, 200.0,  "8pos 0.7%"),
        (9,  0.010, 200.0,  "9pos 1.0%"),
        (9,  0.008, 200.0,  "9pos 0.8%"),
        (10, 0.008, 200.0,  "10pos 0.8%"),
        (10, 0.007, 200.0,  "10pos 0.7%"),
        (10, 0.009, 200.0,  "10pos 0.9%"),
        (11, 0.010, 200.0,  "11pos 1.0%"),
        (11, 0.008, 200.0,  "11pos 0.8%"),
        (12, 0.010, 200.0,  "12pos 1.0%"),
        (12, 0.008, 200.0,  "12pos 0.8%"),
        (12, 0.007, 200.0,  "12pos 0.7%"),
        (13, 0.010, 200.0,  "13pos 1.0%"),
        (13, 0.008, 200.0,  "13pos 0.8%"),
        # Same combos with $500 starting capital
        (10, 0.010, 500.0,  "10pos 1.0% $500"),
        (12, 0.008, 500.0,  "12pos 0.8% $500"),
    ]

    TARGET_LOW  = 2000.0   # backtest return% lower bound
    TARGET_HIGH = 3333.0   # backtest return% upper bound
    TARGET_MID  = 2500.0   # ideal mid-point (= realistic ~1000% at 40% discount)
    MAX_DD_LIMIT = 35.0    # realistic DD at 45-50% max; filter at 35% backtest

    fine_results = []
    for max_conc, risk, init_bal, lbl in FINE_CONFIGS:
        res = replay_wallet(
            all_trade_records,
            initial=init_bal,
            max_concurrent=max_conc,
            risk_pct=risk,
        )
        skip_pct = (
            round(res["trades_skipped"] / (res["trades_used"] + res["trades_skipped"]) * 100.0, 1)
            if (res["trades_used"] + res["trades_skipped"]) > 0
            else 0.0
        )
        real_est = round(res["return_pct"] * 0.40, 1)
        target_gap = abs(res["return_pct"] - TARGET_MID)
        fine_results.append({
            "label": lbl,
            "max_conc": max_conc,
            "risk_pct": risk,
            "initial": init_bal,
            "final": res["final"],
            "return_pct": res["return_pct"],
            "max_dd_pct": res["max_dd_pct"],
            "trades_used": res["trades_used"],
            "skip_pct": skip_pct,
            "real_est": real_est,
            "target_gap": target_gap,
            "in_target_band": TARGET_LOW <= res["return_pct"] <= TARGET_HIGH,
            "dd_ok": res["max_dd_pct"] <= MAX_DD_LIMIT,
        })

    # Sort: in-band + DD-ok first, then by closeness to TARGET_MID
    fine_results.sort(key=lambda r: (
        0 if (r["in_target_band"] and r["dd_ok"]) else 1,
        r["target_gap"],
    ))

    print()
    print("=" * 105)
    print("FINE-TUNE: Target realistic 1000%/yr  (backtest ~2,500%)")
    print(f"  Band: {TARGET_LOW:.0f}%-{TARGET_HIGH:.0f}% backtest return  |  MaxDD < {MAX_DD_LIMIT:.0f}%  |  Real estimate = backtest × 40%")
    print("=" * 105)
    print()

    col_h = (
        f"{'Conc':>5} {'Risk%':>5} {'Start$':>7} {'Return%':>9} {'DD%':>6} "
        f"{'Trades':>7} {'Skip%':>6} {'Real_est(40%)':>14} {'Target_gap':>11}  {'Label'}"
    )
    print(col_h)
    print("-" * 105)

    best_fine: dict | None = None
    for r in fine_results:
        # Mark which criteria are met
        band_marker = "IN-BAND" if r["in_target_band"] else "       "
        dd_marker   = "DD-OK"   if r["dd_ok"]           else "DD-HIGH"
        match_marker = " <- MATCH" if (r["in_target_band"] and r["dd_ok"]) else ""

        if best_fine is None and r["in_target_band"] and r["dd_ok"]:
            best_fine = r

        print(
            f"{r['max_conc']:>5} {r['risk_pct']*100:>4.1f}% "
            f"{'$'+str(int(r['initial'])):>7} "
            f"{r['return_pct']:>+8.1f}% {r['max_dd_pct']:>5.1f}% "
            f"{r['trades_used']:>7} {r['skip_pct']:>5.1f}% "
            f"{r['real_est']:>+13.1f}% "
            f"{r['target_gap']:>11.1f}  "
            f"{r['label']}"
            f"{match_marker}"
        )

    print("-" * 105)
    print()

    # RECOMMENDED SETUP
    if best_fine:
        print("=" * 105)
        print("RECOMMENDED SETUP")
        print(f"  Max Concurrent : {best_fine['max_conc']}")
        print(f"  Risk per Trade : {best_fine['risk_pct']*100:.1f}%")
        print(f"  Starting Cap   : ${int(best_fine['initial'])}")
        print(f"  Backtest Return: {best_fine['return_pct']:+.1f}%  (${best_fine['final']:,.2f} final)")
        print(f"  Max Drawdown   : {best_fine['max_dd_pct']:.1f}%")
        print(f"  Trades Used    : {best_fine['trades_used']}  ({best_fine['skip_pct']:.1f}% skipped)")
        print(f"  Realistic Est  : {best_fine['real_est']:+.1f}%/yr  (40% discount for live friction)")
        print("=" * 105)
    else:
        # No config hit the ideal band — show closest within DD limit
        dd_ok_results = [r for r in fine_results if r["dd_ok"]]
        if dd_ok_results:
            closest = min(dd_ok_results, key=lambda r: r["target_gap"])
            print("=" * 105)
            print("RECOMMENDED SETUP  (closest to target within DD limit)")
            print(f"  Max Concurrent : {closest['max_conc']}")
            print(f"  Risk per Trade : {closest['risk_pct']*100:.1f}%")
            print(f"  Starting Cap   : ${int(closest['initial'])}")
            print(f"  Backtest Return: {closest['return_pct']:+.1f}%  (${closest['final']:,.2f} final)")
            print(f"  Max Drawdown   : {closest['max_dd_pct']:.1f}%")
            print(f"  Trades Used    : {closest['trades_used']}  ({closest['skip_pct']:.1f}% skipped)")
            print(f"  Realistic Est  : {closest['real_est']:+.1f}%/yr  (40% discount for live friction)")
            print("=" * 105)
        else:
            print("  No configuration passed the DD < 35% filter. Lower risk_pct or max_concurrent.")


if __name__ == "__main__":
    main()
