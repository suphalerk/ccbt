"""Realistic portfolio backtest — corrected shared $200 wallet across 61 bots.

Bug fixes vs portfolio_backtest_v2.py:
  1. DOUBLE COMPOUNDING FIX: Track per-bot engine balance and compute R-multiple
     (pnl / engine_balance_at_trade * risk_per_trade). Apply that R to the shared
     wallet: dollar_pnl = shared_balance * risk_per_trade * r_multiple.
     The old code used pnl / INITIAL_10K which ignored that the engine compounds
     internally — early trades were scaled correctly but later trades were not.
  2. CONCURRENT POSITION LIMIT: At most MAX_CONCURRENT positions may be open
     simultaneously across all bots. Excess trades are skipped at entry time.
  3. STRICTER FILTERS:
     - Data < 12 months -> skip bot entirely
     - Trades < MIN_TRADES (10) -> skip bot (was 4 previously)
"""

import copy
import io
import json
import logging
import os
import sys
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
logging.basicConfig(level=logging.WARNING)

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INITIAL_BALANCE = 200.0
ENGINE_INITIAL_BALANCE = 10_000.0
RISK_PER_TRADE = 0.01          # 1% risk per trade
MAX_CONCURRENT = 5             # max simultaneous open positions in shared wallet
MIN_DATA_MONTHS = 12           # minimum data coverage to include a bot
MIN_TRADES = 10                # minimum engine trades to include a bot
DATA_DIR = Path("/Users/iceai/Work/ccbt/data")

# ---------------------------------------------------------------------------
# Bot definitions — 47 existing + 14 Round 9
# (coin, strategy_key, tf_signal, atr_sl_mult, atr_tp_mult, atr_trail_mult, label, data_prefix)
# atr_tp_mult = 0 means trailing exit only (no fixed TP)
# ---------------------------------------------------------------------------

BOTS: list[tuple] = [
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
    # Supertrend 1H
    ("MSTR",     "supertrend",          "1h",  2.0, 3.0, 2.5, "Supertrend",   "mstrusdt"),
    ("XAG",      "supertrend",          "1h",  2.0, 3.0, 2.5, "Supertrend",   "xagusdt"),
    # VolExp 1H
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
    # Round 9 — 14 verified coins
    ("ZRO",      "adx_di_cross",        "1h",  2.0, 4.0, 3.0, "ADX+DI 1H",   "zrousdt"),
    ("ARIA",     "choppiness_ema",      "1h",  2.0, 4.0, 3.0, "Chop+EMA 1H", "ariausdt"),
    ("SAND",     "choppiness_ema",      "1h",  2.0, 4.0, 3.0, "Chop+EMA 1H", "sandusdt"),
    ("CRCL",     "price_channel_vol",   "1h",  2.0, 4.0, 3.0, "PChan+Vol 1H","crclusdt"),
    ("ZEC",      "adx_di_cross",        "1h",  2.0, 4.0, 3.0, "ADX+DI 1H",   "zecusdt"),
    ("DEGO",     "choppiness_ema",      "1h",  2.0, 4.0, 3.0, "Chop+EMA 1H", "degousdt"),
    ("TSLA",     "roc_momentum",        "1h",  2.0, 4.0, 3.0, "ROC Mom 1H",  "tslausdt"),
    ("KAS",      "williams_r_adx",      "4h",  2.0, 4.0, 3.0, "WR+ADX 4H",   "kasusdt"),
    ("RIVER",    "roc_momentum",        "1h",  2.0, 4.0, 3.0, "ROC Mom 1H",  "riverusdt"),
    ("TON",      "roc_momentum",        "4h",  2.0, 4.0, 3.0, "ROC Mom 4H",  "tonusdt"),
    ("DASH",     "supertrend_volume",   "4h",  2.0, 4.0, 3.0, "ST+Vol 4H",   "dashusdt"),
    ("DOT",      "supertrend_volume",   "4h",  2.0, 4.0, 3.0, "ST+Vol 4H",   "dotusdt"),
    ("APR",      "price_channel_vol",   "4h",  2.0, 4.0, 3.0, "PChan+Vol 4H","aprusdt"),
    ("XMR",      "roc_momentum",        "4h",  2.0, 4.0, 3.0, "ROC Mom 4H",  "xmrusdt"),
]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_CONFIG: dict = {
    "exchange": "binance",
    "leverage": 25,
    "risk_per_trade": RISK_PER_TRADE,
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


def build_config(
    symbol: str,
    strategy_key: str,
    tf_signal: str,
    atr_sl_mult: float,
    atr_tp_mult: float,
    atr_trail_mult: float,
) -> dict:
    """Build a complete engine config for one bot."""
    cfg = copy.deepcopy(BASE_CONFIG)
    cfg["symbol"] = symbol.upper()
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
    return df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()


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
    """Load 1H trend data for 15m bots; other TFs use signal data as trend."""
    if tf != "15m":
        return pd.DataFrame()
    for suffix in ("_1h_2y.csv", "_1h_5y.csv"):
        path = DATA_DIR / f"{prefix}{suffix}"
        if path.exists():
            return load_ohlcv(str(path))
    return pd.DataFrame()


def filter_last_year(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the last 12 months of data (tz-naive)."""
    if df.empty:
        return df
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df = df.copy()
    df.index = idx
    cutoff = df.index[-1] - pd.DateOffset(months=12)
    return df[df.index >= cutoff].copy()


def data_months_coverage(df: pd.DataFrame) -> float:
    """Return the number of months of data in a DataFrame."""
    if df.empty or len(df) < 2:
        return 0.0
    idx = pd.to_datetime(df.index)
    delta = idx[-1] - idx[0]
    return delta.days / 30.44


# ---------------------------------------------------------------------------
# Run individual bot and extract trades with R-multiples
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
) -> tuple[list[dict], str]:
    """Run one bot and return (trades, skip_reason).

    Trades contain pnl_raw (engine dollar PnL on $10K base) and r_multiple
    (pnl relative to engine balance at the time of the trade).

    The R-multiple approach eliminates double compounding:
    - Engine internally compounds. To get the true per-trade return we divide
      pnl by the engine balance *at the time of that trade*, not the fixed
      initial balance.
    - We track engine_balance rolling through each trade chronologically.
    - r_multiple = pnl_raw / (engine_balance_before_trade * RISK_PER_TRADE)
    - In shared wallet: dollar_pnl = shared_balance * RISK_PER_TRADE * r_multiple
    """
    signal_df = load_signal_data(data_prefix, tf_signal)
    if signal_df.empty:
        return [], f"no signal data ({data_prefix} {tf_signal})"

    # Check data coverage BEFORE filtering to last year
    months = data_months_coverage(signal_df)
    if months < MIN_DATA_MONTHS:
        return [], f"insufficient data coverage ({months:.1f} months < {MIN_DATA_MONTHS})"

    trend_df = load_trend_data(data_prefix, tf_signal)

    # Filter to last 1 year
    signal_df = filter_last_year(signal_df)
    if not trend_df.empty:
        trend_df = filter_last_year(trend_df)

    if len(signal_df) < 50:
        return [], f"too few bars after filter ({len(signal_df)})"

    cfg = build_config(data_prefix, strategy_key, tf_signal, atr_sl_mult, atr_tp_mult, atr_trail_mult)

    try:
        engine = BacktestEngine(cfg, initial_balance=ENGINE_INITIAL_BALANCE)
        # Suppress engine's own stdout report (it prints to stdout)
        with redirect_stdout(io.StringIO()):
            engine.run(signal_df, trend_df if not trend_df.empty else None)
    except Exception as exc:
        return [], f"engine error: {exc}"

    raw_trades = list(engine.state.trades)

    if len(raw_trades) < MIN_TRADES:
        return [], f"too few trades ({len(raw_trades)} < {MIN_TRADES})"

    # Compute R-multiples, tracking engine balance through time
    engine_balance = ENGINE_INITIAL_BALANCE
    trades: list[dict] = []
    for t in raw_trades:
        engine_risk = engine_balance * RISK_PER_TRADE
        r_multiple = (t.pnl / engine_risk) if engine_risk > 0 else 0.0
        engine_balance = max(engine_balance + t.pnl, 100.0)  # floor to avoid divide-by-zero

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
            "pnl_raw": t.pnl,          # raw engine PnL ($10K base, for audit only)
            "r_multiple": r_multiple,   # units of 1%-risk won/lost
        })

    return trades, ""


# ---------------------------------------------------------------------------
# Shared wallet replay — R-multiple method with concurrent position limit
# ---------------------------------------------------------------------------


def replay_shared_wallet(
    all_bot_trades: dict[str, list[dict]],
    initial_balance: float = INITIAL_BALANCE,
    max_concurrent: int = MAX_CONCURRENT,
) -> list[dict]:
    """Replay shared wallet using R-multiples.

    Steps:
    1. Merge all trades; each trade already has r_multiple from its bot's
       engine balance at the time of the trade (no double compounding).
    2. Sort by entry_time and apply concurrent position limit: skip entries
       when max_concurrent positions are already open.
    3. Sort active trades by exit_time and replay with compounding balance:
       dollar_pnl = shared_balance * RISK_PER_TRADE * r_multiple

    Why R-multiple is correct:
    - r_multiple = pnl / (engine_balance_at_trade * 0.01)
    - e.g. if engine_balance=$12K and trade pnl=$120: r = $120/($12K*0.01) = +1.0R
    - Shared wallet risked 1% of $200 = $2: dollar_pnl = $2 * 1.0R = $2
    - This is exactly what would happen in a real wallet risking 1% per trade.
    """
    # Flatten all bot trades into one list
    all_trades: list[dict] = []
    for trades in all_bot_trades.values():
        all_trades.extend(trades)

    # Filter out trades with missing timestamps
    all_trades = [t for t in all_trades if t["entry_time"] is not None and t["exit_time"] is not None]

    if not all_trades:
        return []

    # Sort by entry_time to apply concurrent limit at the moment of entry
    all_trades.sort(key=lambda t: t["entry_time"])

    # Apply concurrent position limit
    # Track open positions as list of exit_times
    open_exit_times: list[pd.Timestamp] = []
    for t in all_trades:
        # Expire positions that have closed before this entry
        open_exit_times = [et for et in open_exit_times if et > t["entry_time"]]

        if len(open_exit_times) >= max_concurrent:
            t["active"] = False
        else:
            t["active"] = True
            open_exit_times.append(t["exit_time"])

    # Replay active trades in exit_time order for PnL sequencing
    active = [t for t in all_trades if t["active"]]
    active.sort(key=lambda t: t["exit_time"])

    balance = initial_balance
    result: list[dict] = []
    for t in active:
        shared_risk = balance * RISK_PER_TRADE
        dollar_pnl = shared_risk * t["r_multiple"]
        balance += dollar_pnl
        balance = max(balance, 1.0)  # absolute floor
        result.append({**t, "dollar_pnl": dollar_pnl, "balance": balance})

    return result


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def print_audit_summary(
    bots_attempted: int,
    skipped_data: list[tuple[str, str]],
    skipped_trades: list[tuple[str, str]],
    active_bots: int,
    total_raw_trades: int,
    concurrent_skipped: int,
    active_trades: int,
) -> None:
    """Report 0: audit header."""
    print("\n" + "=" * 78)
    print("REALISTIC PORTFOLIO BACKTEST — AUDIT SUMMARY")
    print("=" * 78)
    print(f"Bots attempted         : {bots_attempted}")
    print(f"Skipped (short data)   : {len(skipped_data)}")
    for coin, reason in sorted(skipped_data):
        print(f"  {coin:<16} {reason}")
    print(f"Skipped (few trades)   : {len(skipped_trades)}")
    for coin, reason in sorted(skipped_trades):
        print(f"  {coin:<16} {reason}")
    print(f"Active bots            : {active_bots}")
    print(f"Total raw trades       : {total_raw_trades}")
    print(f"Skipped (concurrent)   : {concurrent_skipped}")
    print(f"Replayed trades        : {active_trades}")
    print(f"Max concurrent limit   : {MAX_CONCURRENT}")
    print(f"Initial balance        : ${INITIAL_BALANCE:.2f}")
    print(f"Min data coverage      : {MIN_DATA_MONTHS} months")
    print(f"Min trades required    : {MIN_TRADES}")


def print_per_bot_summary(
    replayed: list[dict],
    all_bots_run: list[tuple],
    initial_balance: float,
) -> None:
    """Report 1: per-bot stats sorted by total dollar PnL."""
    print("\n" + "=" * 78)
    print("PER-BOT SUMMARY (sorted by PnL)")
    print("=" * 78)

    bot_stats: dict[str, dict] = {}
    for t in replayed:
        key = t["coin"]
        if key not in bot_stats:
            bot_stats[key] = {
                "coin": t["coin"],
                "label": t["label"],
                "trades": 0,
                "wins": 0,
                "r_sum": 0.0,
                "total_pnl": 0.0,
            }
        bot_stats[key]["trades"] += 1
        if t["dollar_pnl"] > 0:
            bot_stats[key]["wins"] += 1
        bot_stats[key]["r_sum"] += t["r_multiple"]
        bot_stats[key]["total_pnl"] += t["dollar_pnl"]

    # Add bots that ran but had all trades skipped due to concurrent limit
    for row in all_bots_run:
        coin = row[0]
        label = row[6]
        if coin not in bot_stats:
            bot_stats[coin] = {
                "coin": coin,
                "label": label,
                "trades": 0,
                "wins": 0,
                "r_sum": 0.0,
                "total_pnl": 0.0,
            }

    sorted_bots = sorted(bot_stats.values(), key=lambda x: x["total_pnl"], reverse=True)

    hdr = f"{'Bot':<14} {'Strategy':<16} {'Trades':>7} {'WR%':>6} {'Avg_R':>6}  {'PnL$':>9}"
    print(hdr)
    print("-" * 68)

    total_trades = total_wins = 0
    total_pnl = 0.0

    for bs in sorted_bots:
        wr = (bs["wins"] / bs["trades"] * 100) if bs["trades"] > 0 else 0.0
        avg_r = (bs["r_sum"] / bs["trades"]) if bs["trades"] > 0 else 0.0
        sign = "+" if bs["total_pnl"] >= 0 else ""
        avg_r_str = f"{avg_r:+.2f}R"
        print(
            f"{bs['coin']:<14} {bs['label']:<16} "
            f"{bs['trades']:>7} {wr:>5.1f}% {avg_r_str:>6}  "
            f"{sign}{bs['total_pnl']:>8.2f}"
        )
        total_trades += bs["trades"]
        total_wins += bs["wins"]
        total_pnl += bs["total_pnl"]

    print("-" * 68)
    total_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
    final_balance = initial_balance + total_pnl
    sign = "+" if total_pnl >= 0 else ""
    print(
        f"{'TOTAL':<14} {'':<16} "
        f"{total_trades:>7} {total_wr:>5.1f}%        "
        f"{sign}{total_pnl:>8.2f}"
    )
    pct = (total_pnl / initial_balance * 100) if initial_balance > 0 else 0.0
    print(f"\nFinal balance: ${final_balance:.2f}  ({sign}{pct:.1f}%)")

    # Max drawdown from equity curve
    if replayed:
        balances = [initial_balance] + [t["balance"] for t in replayed]
        peak = balances[0]
        max_dd = 0.0
        for b in balances:
            if b > peak:
                peak = b
            dd = (peak - b) / peak * 100
            if dd > max_dd:
                max_dd = dd
        print(f"Max drawdown   : {max_dd:.1f}%")


def print_monthly_report(replayed: list[dict], initial_balance: float) -> None:
    """Report 2: monthly breakdown with per-bot detail."""
    print("\n\n" + "=" * 78)
    print("MONTHLY REPORT")
    print("=" * 78)

    if not replayed:
        print("No trades.")
        return

    monthly_coin: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: {"trades": 0, "pnl": 0.0, "r_sum": 0.0})
    )
    for t in replayed:
        month = t["exit_time"].strftime("%Y-%m")
        monthly_coin[month][t["coin"]]["trades"] += 1
        monthly_coin[month][t["coin"]]["pnl"] += t["dollar_pnl"]
        monthly_coin[month][t["coin"]]["r_sum"] += t["r_multiple"]

    balance = initial_balance
    for month in sorted(monthly_coin.keys()):
        coin_data = monthly_coin[month]
        month_pnl = sum(v["pnl"] for v in coin_data.values())
        month_trades = sum(v["trades"] for v in coin_data.values())
        prev_balance = balance
        balance += month_pnl

        sign = "+" if month_pnl >= 0 else ""
        print(f"\n--- {month}  trades={month_trades:>3}  {sign}${month_pnl:>8.2f}  -> ${balance:>9.2f} ---")
        print(f"  {'Bot':<14} {'Tr':>4}  {'Avg_R':>6}  {'PnL$':>9}")
        for coin in sorted(coin_data.keys()):
            cd = coin_data[coin]
            avg_r = cd["r_sum"] / cd["trades"] if cd["trades"] > 0 else 0.0
            sign2 = "+" if cd["pnl"] >= 0 else ""
            print(
                f"  {coin:<14} {cd['trades']:>4}  {avg_r:>+.2f}R  {sign2}{cd['pnl']:>8.2f}"
            )


def print_daily_report(replayed: list[dict], initial_balance: float) -> None:
    """Report 3: daily trade-level log for last 2 months."""
    print("\n\n" + "=" * 78)
    print("DAILY REPORT (Last 2 months)")
    print("=" * 78)

    if not replayed:
        print("No trades.")
        return

    # Determine cutoff: last 2 months of available data
    all_months = sorted({t["exit_time"].strftime("%Y-%m") for t in replayed})
    cutoff_month = all_months[-2] if len(all_months) >= 2 else all_months[0]
    recent = [t for t in replayed if t["exit_time"].strftime("%Y-%m") >= cutoff_month]

    print(f"\n{'Date':<12} {'Bot':<14} {'Side':<6} {'R_mult':>7}  {'PnL$':>9}  {'Balance$':>10}")
    print("-" * 72)
    for t in recent:
        date = t["exit_time"].strftime("%Y-%m-%d")
        r_str = f"{t['r_multiple']:+.2f}R"
        sign = "+" if t["dollar_pnl"] >= 0 else ""
        reason = t.get("close_reason", "")
        print(
            f"{date:<12} {t['coin']:<14} {t['side']:<6} {r_str:>7}  "
            f"{sign}{t['dollar_pnl']:>8.2f}  ${t['balance']:>9.2f}"
            + (f"  [{reason}]" if reason else "")
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("Realistic Portfolio Backtest — Running 61 bots...")
    print(f"Filters: data>={MIN_DATA_MONTHS}mo, trades>={MIN_TRADES}, max_concurrent={MAX_CONCURRENT}\n")

    skipped_data: list[tuple[str, str]] = []
    skipped_trades: list[tuple[str, str]] = []
    all_bots_run: list[tuple] = []
    all_bot_trades: dict[str, list[dict]] = {}
    total_raw = 0

    for bot_row in BOTS:
        coin, strategy_key, tf_signal, atr_sl, atr_tp, atr_trail, label, data_prefix = bot_row
        print(f"  Running {coin:<14} ({label}, {tf_signal}) ... ", end="", flush=True)

        trades, skip_reason = run_bot(
            coin, strategy_key, tf_signal, atr_sl, atr_tp, atr_trail, label, data_prefix
        )

        if skip_reason:
            # Distinguish filter type
            if "data" in skip_reason or "coverage" in skip_reason or "bars" in skip_reason:
                skipped_data.append((coin, skip_reason))
                print(f"SKIP (data): {skip_reason}")
            else:
                skipped_trades.append((coin, skip_reason))
                print(f"SKIP (trades): {skip_reason}")
            continue

        print(f"{len(trades)} trades")
        all_bots_run.append(bot_row)
        all_bot_trades[coin] = trades
        total_raw += len(trades)

    print(f"\nTotal raw trades from {len(all_bots_run)} active bots: {total_raw}")

    if not all_bot_trades:
        print("No trades generated. Check data files.")
        return

    # Shared wallet replay
    replayed = replay_shared_wallet(all_bot_trades, INITIAL_BALANCE, MAX_CONCURRENT)

    # Count concurrent skips
    all_flat: list[dict] = []
    for trades in all_bot_trades.values():
        all_flat.extend(trades)
    valid_ts = [t for t in all_flat if t["entry_time"] is not None and t["exit_time"] is not None]
    concurrent_skipped = len(valid_ts) - len(replayed)

    # Reports
    print_audit_summary(
        bots_attempted=len(BOTS),
        skipped_data=skipped_data,
        skipped_trades=skipped_trades,
        active_bots=len(all_bots_run),
        total_raw_trades=total_raw,
        concurrent_skipped=concurrent_skipped,
        active_trades=len(replayed),
    )
    print_per_bot_summary(replayed, all_bots_run, INITIAL_BALANCE)
    print_monthly_report(replayed, INITIAL_BALANCE)
    print_daily_report(replayed, INITIAL_BALANCE)


if __name__ == "__main__":
    main()
