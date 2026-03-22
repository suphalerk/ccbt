"""final_backtest.py — Definitive combined portfolio backtest with weak-coin upgrades.

Sources:
  - Source 1: All 136 bots from full_portfolio_74.json (rerun via full_portfolio_74.py registry)
  - Source 2: 6 new weak-coin upgrades (replaces losing bot versions for those coins)

Upgrade coins (REMOVE losing versions, ADD upgrade):
  PENGU → ichimoku_cloud 4h  sl=3.0 trail=4.0
  POL   → ichimoku_cloud 4h  sl=3.0 trail=4.0
  AVAX  → ema_ribbon     4h  sl=2.5 tp=5.0 trail=4.0
  ALICE → awesome_oscillator 4h sl=2.0 tp=3.0 trail=2.5
  DASH  → ichimoku_cloud 4h  sl=1.0 tp=2.0 trail=2.0
  KAS   → ema_ribbon     4h  sl=1.5 tp=4.0 trail=3.0 (new — all existing KAS bots removed)

Methodology (R-multiple shared wallet):
  1. Each bot runs BacktestEngine with $10K virtual balance to collect trades.
  2. R-multiple = trade_pnl / risk_amount_on_10K.
  3. All trades sorted by entry_time, replayed on shared $200 wallet.
  4. Dollar PnL = wallet * bot_risk_pct * R_multiple.
  5. Max 5 concurrent positions enforced.

Reports:
  1. Per-bot summary (sorted by shared wallet PnL$)
  2. Monthly returns with per-bot breakdown
  3. Daily log (last 2 months)

Output: stdout + data/final_backtest_fixed.json
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
MIN_TRADES = 6          # task spec: skip bots with fewer than 6 trades

# ---------------------------------------------------------------------------
# Coins whose ALL existing bots get replaced by the upgrade versions
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
# Signal list — every signal known to the engine
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
# Base config
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
    "vol_expansion_ema_trend_period": 50,
    "supertrend_multiplier": 2.0,
    "supertrend_atr_period": 14,
    "trading_hours": {"enabled": True, "start_utc": 3, "end_utc": 20},
    "regime_filter": {"enabled": True, "skip_ranging": False},
    "flexible_cooldown": {"enabled": False, "min_quality_score": 0.7,
                          "cooldown_reduction_factor": 0.5, "log_overrides": True},
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
    """Single bot to backtest."""

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
        self.config_file = config_file


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

    # Signal-specific config overrides
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
    """For 15m bots, trend is 1h; for 1h/4h bots trend == signal data."""
    if tf != "15m":
        return pd.DataFrame()
    for suffix in ("_1h_2y.csv", "_1h_5y.csv"):
        path = DATA_DIR / f"{prefix}{suffix}"
        if path.exists():
            return load_ohlcv(str(path))
    return pd.DataFrame()


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


# ---------------------------------------------------------------------------
# Bot registry — mirrors full_portfolio_74.py + applies upgrades
# ---------------------------------------------------------------------------

def _load_config_file(rel_path: str) -> Optional[dict]:
    p = ROOT / rel_path
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def _build_bot_list() -> list[BotDef]:
    """Build unified bot list: all rounds from full_portfolio_74 sources + 6 upgrades."""
    bots: list[BotDef] = []

    # -------------------------------------------------------------------
    # EXISTING BOTS (prior to R10 — matches full_portfolio_74.py)
    # -------------------------------------------------------------------
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
        if coin in UPGRADE_COINS:
            continue  # will be replaced by upgrade
        cfg = _load_config_file(cfg_file) if cfg_file else None
        bots.append(BotDef(
            label=label, coin=coin, prefix=prefix, signal_key=signal,
            tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=risk,
            config_file=cfg_file if cfg else None,
        ))

    # -------------------------------------------------------------------
    # ROUND 10 bots
    # -------------------------------------------------------------------
    r10_path = DATA_DIR / "round10_results.json"
    r10_added: set[str] = set()

    if r10_path.exists():
        with open(r10_path) as f:
            r10_data = json.load(f)
        r10_passed = [
            x for x in r10_data.get("passed", [])
            if x.get("engine_pf", 0) >= 1.2 and x.get("trades", 0) >= 6
        ]
        for vbot in r10_passed:
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

    # -------------------------------------------------------------------
    # ROUND 11 bots
    # -------------------------------------------------------------------
    r11_path = DATA_DIR / "round11_results.json"
    r11_added: set[str] = set()

    if r11_path.exists():
        with open(r11_path) as f:
            r11_data = json.load(f)
        r11_passed = [
            x for x in r11_data.get("passed", [])
            if x.get("engine_pf", 0) >= 1.2 and x.get("trades", 0) >= 6
        ]
        for vbot in r11_passed:
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
            ("BAN zscore_m",     "BAN",   "banusdt",      "zscore_meanrev", "1h", 1.5, 2.0, 2.0),
            ("ADA stoch_mtf",    "ADA",   "adausdt",      "stoch_mtf",      "4h", 2.0, 4.0, 3.0),
            ("CFX ema_rib",      "CFX",   "cfxusdt",      "ema_ribbon",     "4h", 2.0, 4.0, 3.0),
            ("ICP ema_rib",      "ICP",   "icpusdt",      "ema_ribbon",     "4h", 2.0, 4.0, 3.0),
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

    # -------------------------------------------------------------------
    # MEGA100 verified bots
    # -------------------------------------------------------------------
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
            if coin in UPGRADE_COINS:
                continue
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

    # -------------------------------------------------------------------
    # ROUND 12: Combo bots
    # -------------------------------------------------------------------
    combo_path = DATA_DIR / "combo_results.json"
    combo_added: set[str] = set()

    if combo_path.exists():
        with open(combo_path) as f:
            combo_data = json.load(f)
        combo_passed = [
            x for x in combo_data.get("passed", [])
            if x.get("engine_pf", 0) >= 1.2 and x.get("trades", 0) >= 6
        ]
        for vbot in combo_passed:
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
            if key in combo_added:
                continue
            combo_added.add(key)
            bots.append(BotDef(
                label=f"{coin} {signal_key[:8]} R12", coin=coin, prefix=prefix,
                signal_key=signal_key, tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=0.01,
            ))

    # -------------------------------------------------------------------
    # Dedup: remove exact duplicates (same prefix + signal_key + tf)
    # Keep higher risk_pct version (earlier = more validated)
    # -------------------------------------------------------------------
    seen: dict[str, BotDef] = {}
    deduped: list[BotDef] = []
    for bot in bots:
        key = f"{bot.prefix}_{bot.signal_key}_{bot.tf}"
        if key not in seen:
            seen[key] = bot
            deduped.append(bot)
        else:
            existing = seen[key]
            if bot.risk_pct > existing.risk_pct:
                idx = deduped.index(existing)
                deduped[idx] = bot
                seen[key] = bot

    # -------------------------------------------------------------------
    # Add 6 upgrade bots (appended after dedup to avoid removal)
    # -------------------------------------------------------------------
    upgrade_keys: set[str] = set()
    for label, coin, prefix, signal, tf, sl, tp, trail, risk in UPGRADES:
        key = f"{prefix}_{signal}_{tf}"
        if key in upgrade_keys:
            continue
        upgrade_keys.add(key)
        deduped.append(BotDef(
            label=label, coin=coin, prefix=prefix, signal_key=signal,
            tf=tf, sl=sl, tp=tp, trail=trail, risk_pct=risk,
        ))

    return deduped


# ---------------------------------------------------------------------------
# Per-bot engine runner
# ---------------------------------------------------------------------------

def run_bot_engine(bot: BotDef) -> tuple[list, dict]:
    """Run BacktestEngine for one bot. Returns (raw_trade_list, metrics)."""
    signal_df = load_signal_data(bot.prefix, bot.tf)
    if signal_df.empty:
        return [], {}

    if data_span_months(signal_df) < MIN_DATA_MONTHS:
        return [], {}

    trend_df = load_trend_data(bot.prefix, bot.tf)

    signal_1y = filter_last_year(signal_df)
    trend_1y = filter_last_year(trend_df) if not trend_df.empty else pd.DataFrame()

    if len(signal_1y) < 50:
        return [], {}

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

    # Use the ACTUAL risk from the config the engine will use (not bot.risk_pct which
    # may differ if config_file overrides it). This ensures R-multiple is correctly
    # normalised regardless of what the config file says.
    actual_risk_pct = cfg.get("risk_per_trade", bot.risk_pct)

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

    pnl_vals = [t.pnl for t in trades]
    wins = [p for p in pnl_vals if p > 0]
    losses = [p for p in pnl_vals if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    wr = len(wins) / len(trades) * 100.0

    risk_amount = 10_000.0 * actual_risk_pct
    r_vals = [t.pnl / risk_amount for t in trades if risk_amount > 0]
    avg_r = float(np.mean(r_vals)) if r_vals else 0.0

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
        "actual_risk_pct": actual_risk_pct,
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
    """Replay all trades on shared wallet. Returns (summary+per_bot, monthly_rows, daily_log)."""

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
    # Per-bot per-month: {month_key: {bot_label: {"pnl": 0.0, "trades": 0}}}
    monthly_bot_pnl: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: {"pnl": 0.0, "trades": 0}))
    monthly_pnl: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    daily_log: list[dict] = []
    _eq_peak = initial_wallet
    _max_dd_usd = 0.0

    def close_expired(current_time: pd.Timestamp) -> None:
        nonlocal balance, open_trades, _eq_peak, _max_dd_usd
        still_open = []
        for ot in open_trades:
            if ot["exit_time"] <= current_time:
                # Shared wallet ALWAYS uses 1% risk per trade (normalized R-multiple).
                # r_mult was computed using the engine's actual risk (bot.risk_pct),
                # so it correctly represents alpha. We apply a flat 1% here so bots
                # with higher configured risk (BTC=5%, WIF=3%) don't get disproportionate
                # share of the shared wallet.
                dollar_pnl = balance * 0.01 * ot["r_mult"]
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
                monthly_bot_pnl[month_key][bot_name]["pnl"] += dollar_pnl
                monthly_bot_pnl[month_key][bot_name]["trades"] += 1

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

    # Build monthly rows with per-bot breakdown
    running_bal = initial_wallet
    monthly_rows = []
    for month_key in sorted(monthly_pnl.keys()):
        m = monthly_pnl[month_key]
        month_pnl = m["pnl"]
        month_trades = int(m["trades"])
        running_bal += month_pnl

        # per-bot breakdown for this month, sorted by PnL desc
        bot_breakdown = []
        for bot_name, bm in sorted(
            monthly_bot_pnl[month_key].items(),
            key=lambda x: -x[1]["pnl"],
        ):
            bot_breakdown.append({
                "bot": bot_name,
                "trades": bm["trades"],
                "pnl": round(bm["pnl"], 2),
            })

        monthly_rows.append({
            "month": month_key,
            "trades": month_trades,
            "pnl": round(month_pnl, 2),
            "balance": round(running_bal, 2),
            "bots": bot_breakdown,
        })

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
    """Report 1: per-bot summary table sorted by shared wallet PnL$."""
    lines = [
        "",
        "=" * 95,
        "REPORT 1: PER-BOT SUMMARY (sorted by shared wallet PnL$)",
        "=" * 95,
        f"{'Bot':<32} {'Strategy':<20} {'Trades':>6}  {'WR%':>5}  {'Avg R':>6}  {'PnL$':>9}",
        "-" * 82,
    ]

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
        tf = eng_m.get("tf", "?")
        strat = f"{signal[:14]} {tf}"
        rows.append((bot_name, strat, n, wr, avg_r, pnl))

    rows.sort(key=lambda x: -x[5])
    total_pnl = 0.0
    total_trades = 0
    for bot_name, strat, n, wr, avg_r, pnl in rows:
        lines.append(
            f"  {bot_name:<30} {strat:<20} {n:>6}  {wr:>4.0f}%  {avg_r:>+6.3f}  {pnl:>+9.2f}"
        )
        total_pnl += pnl
        total_trades += n

    lines += [
        "-" * 82,
        f"  {'TOTAL':<30} {'':<20} {total_trades:>6}  {'':>5}  {'':>6}  {total_pnl:>+9.2f}",
        "=" * 95,
    ]
    return "\n".join(lines)


def report_monthly(monthly_rows: list[dict]) -> str:
    """Report 2: monthly returns with per-bot breakdown (1 year)."""
    lines = [
        "",
        "=" * 65,
        "REPORT 2: MONTHLY RETURNS WITH PER-BOT BREAKDOWN",
        "=" * 65,
    ]
    for row in monthly_rows:
        lines.append(
            f"\n{row['month']}  trades={row['trades']:>4}  PnL={row['pnl']:>+9.2f}  "
            f"Balance=${row['balance']:>9.2f}"
        )
        for b in row["bots"]:
            lines.append(
                f"    {b['bot']:<32}  {b['trades']:>3} trades  {b['pnl']:>+9.2f}"
            )
        lines.append(f"  {'MONTH TOTAL':<32}  {row['trades']:>3} trades  {row['pnl']:>+9.2f}")
    lines.append("\n" + "=" * 65)
    return "\n".join(lines)


def report_daily(daily_log: list[dict]) -> str:
    """Report 3: daily log for last 2 months."""
    if not daily_log:
        return "\nNo daily trades."

    log_df = pd.DataFrame(daily_log)
    cutoff = (
        pd.to_datetime(log_df["date"]).max() - pd.DateOffset(months=2)
    ).strftime("%Y-%m-%d")
    recent = log_df[log_df["date"] >= cutoff].copy()

    lines = [
        "",
        "=" * 100,
        "REPORT 3: DAILY LOG (last 2 months)",
        "=" * 100,
        f"{'Date':<12}  {'Bot':<32}  {'Side':<6}  {'R':>7}  {'PnL$':>9}  {'Balance$':>10}",
        "-" * 85,
    ]
    for _, row in recent.iterrows():
        lines.append(
            f"{row['date']:<12}  {row['bot']:<32}  {row['side']:<6}  "
            f"{row['r_mult']:>+7.3f}  {row['pnl']:>+9.2f}  ${row['balance']:>9.2f}"
        )
    lines.append("=" * 100)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 85)
    print("FINAL BACKTEST — ALL VERIFIED BOTS + 6 WEAK-COIN UPGRADES")
    print(f"Shared wallet: ${INITIAL_WALLET:.0f}  |  Max concurrent: {MAX_CONCURRENT}  |  Min trades: {MIN_TRADES}")
    print("=" * 85)

    # Build unified bot list
    bots = _build_bot_list()
    upgrade_labels = {u[0] for u in UPGRADES}
    n_upgrades = sum(1 for b in bots if b.label in upgrade_labels)
    print(f"\nBuilt {len(bots)} unique bot definitions ({n_upgrades} upgrades, {len(bots) - n_upgrades} existing).")
    print(f"Upgrade coins removed from base portfolio: {sorted(UPGRADE_COINS)}")
    print(f"Upgrade bots added: {[b.label for b in bots if b.label in upgrade_labels]}")

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
        # Use ACTUAL engine risk (from config) not bot.risk_pct, so R-multiple is
        # correctly normalised. Shared wallet replay always uses 0.01 (1%) flat.
        actual_risk_pct = metrics["actual_risk_pct"]
        risk_amount = 10_000.0 * actual_risk_pct

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
                "risk_pct": actual_risk_pct,   # actual engine risk for audit trail
                "close_reason": t.close_reason,
            })

    print(f"\nCompleted {len(bot_metrics)} bots. Skipped {len(skipped)}.")
    if skipped:
        print("  Skipped:")
        for s in skipped:
            print(f"    - {s}")

    # Engine metrics summary
    print("\nEngine metrics (per bot, independent $10K runs):")
    print(f"  {'Bot':<32} {'Signal':<20} {'Tr':>4}  {'WR%':>5}  {'PF':>5}  {'AvgR':>6}  {'PnL$10K':>9}")
    print("  " + "-" * 82)
    for m in sorted(bot_metrics, key=lambda x: -x["pnl_10k"]):
        pf_str = f"{m['pf']:.2f}" if m["pf"] < 99 else "inf"
        upgrade_mark = " (*)" if m["label"] in upgrade_labels else ""
        print(
            f"  {m['label'] + upgrade_mark:<32} {m['signal'][:14]:<14} {m['tf']:<6} "
            f"{m['trades']:>4}  {m['wr_pct']:>4.0f}%  {pf_str:>5}  "
            f"{m['avg_r']:>+6.3f}  {m['pnl_10k']:>+9.2f}"
        )

    if not all_trade_records:
        print("\nERROR: No trades collected — check data files.")
        return

    # Shared wallet replay
    portfolio, monthly_rows, daily_log = run_portfolio_replay(
        all_trade_records, INITIAL_WALLET, MAX_CONCURRENT,
    )

    per_bot_stats = portfolio.pop("per_bot", {})

    # Portfolio summary
    print(f"\n{'=' * 85}")
    print("PORTFOLIO SUMMARY")
    print(f"{'=' * 85}")
    print(f"  Initial balance:  ${portfolio['initial_wallet']:.2f}")
    print(f"  Final balance:    ${portfolio['final_balance']:.2f}")
    print(f"  Total return:     {portfolio['total_return_pct']:+.1f}%  (${portfolio['total_pnl']:+.2f})")
    print(f"  Total trades:     {portfolio['total_trades']}")
    print(f"  Unique bots:      {portfolio['unique_bots']}")
    print(f"  Max drawdown:     {portfolio['max_dd_pct']:.1f}%  (${portfolio['max_dd_usd']:.2f})")

    # Print all 3 reports
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
            "upgrade_coins": sorted(UPGRADE_COINS),
            "upgrade_bots": [b.label for b in bots if b.label in upgrade_labels],
            "initial_wallet": INITIAL_WALLET,
            "max_concurrent": MAX_CONCURRENT,
        },
        "portfolio_summary": {
            **portfolio,
            "per_bot": {k: dict(v) for k, v in per_bot_stats.items()},
        },
        "monthly": monthly_rows,
        "daily": daily_log[-300:],
        "engine_metrics": bot_metrics,
    }

    out_path = DATA_DIR / "final_backtest_fixed.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=_clean)

    print(f"\nResults saved to {out_path}")

    # Bug-fix comparison
    print("\n" + "=" * 60)
    print("COMPARISON (R-multiple bug fix)")
    print("=" * 60)
    print(f"  Buggy:  $200 -> $3,422 (+1,611%) DD 12.4%")
    print(f"  Fixed:  $200 -> ${portfolio['final_balance']:.0f} ({portfolio['total_return_pct']:+.1f}%) DD {portfolio['max_dd_pct']:.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
