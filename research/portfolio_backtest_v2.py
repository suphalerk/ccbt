"""Portfolio backtest v2 — shared $200 wallet across all bots.

Runs each bot independently with a large neutral balance, extracts pnl_pct
per trade, merges all trades chronologically, then replays with a shared
$200 starting balance applying 1% risk compounding.
"""

import copy
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
logging.basicConfig(level=logging.WARNING)

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

# ---------------------------------------------------------------------------
# Bot definitions
# (coin, strategy_key, tf_signal, atr_sl_mult, atr_tp_mult, atr_trail_mult, label, data_prefix)
# atr_tp_mult = 0 means trailing exit only (no fixed TP)
# ---------------------------------------------------------------------------

BOTS = [
    # EMA 15m
    ("BTC",        "ema_crossover",      "15m", 1.0, 3.0, 2.0, "EMA 15m",       "btcusdt"),
    ("WIF",        "ema_crossover",      "15m", 1.0, 3.0, 2.0, "EMA 15m",       "wifusdt"),
    ("ARC",        "ema_crossover",      "15m", 1.0, 3.0, 2.0, "EMA 15m",       "arcusdt"),
    # Ichi 1H
    ("AVAX",       "ichimoku_cloud",     "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "avaxusdt"),
    ("POL",        "ichimoku_cloud",     "1h",  1.0, 0.0, 3.0, "Ichi 1H Trail", "polusdt"),
    ("GUN",        "ichimoku_cloud",     "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "gunusdt"),
    ("BERA",       "ichimoku_cloud",     "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "berausdt"),
    ("ATH",        "ichimoku_cloud",     "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "athusdt"),
    ("INJ",        "ichimoku_cloud",     "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "injusdt"),
    ("TRUMP",      "ichimoku_cloud",     "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "trumpusdt"),
    ("ANIME",      "ichimoku_cloud",     "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "animeusdt"),
    ("IP",         "ichimoku_cloud",     "1h",  2.0, 4.0, 3.0, "Ichi 1H",       "ipusdt"),
    # Ichi 4H
    ("1000SHIB",   "ichimoku_cloud",     "4h",  3.0, 5.0, 3.0, "Ichi 4H",       "1000shibusdt"),
    ("TAO",        "ichimoku_cloud",     "4h",  2.5, 4.0, 3.0, "Ichi 4H",       "taousdt"),
    ("RENDER",     "ichimoku_cloud",     "4h",  2.5, 4.0, 3.0, "Ichi 4H",       "renderusdt"),
    ("ARB",        "ichimoku_cloud",     "4h",  3.0, 4.0, 2.0, "Ichi 4H",       "arbusdt"),
    ("SIGN",       "ichimoku_cloud",     "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "signusdt"),
    ("TIA",        "ichimoku_cloud",     "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "tiausdt"),
    ("ONDO",       "ichimoku_cloud",     "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "ondousdt"),
    ("H",          "ichimoku_cloud",     "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "husdt"),
    ("AXS",        "ichimoku_cloud",     "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "axsusdt"),
    # 4H Trail
    ("ALGO",       "ichimoku_cloud",     "4h",  1.5, 0.0, 4.0, "4H Trail",      "algousdt"),
    ("TRX",        "ichimoku_cloud",     "4h",  1.5, 0.0, 4.0, "4H Trail",      "trxusdt"),
    ("POLYX",      "ichimoku_cloud",     "4h",  1.5, 0.0, 4.0, "4H Trail",      "polyxusdt"),
    ("FET",        "ichimoku_cloud",     "4h",  1.5, 0.0, 4.0, "4H Trail",      "fetusdt"),
    ("XLM",        "ichimoku_cloud",     "4h",  1.0, 0.0, 4.0, "4H Trail",      "xlmusdt"),
    ("SAHARA",     "ichimoku_cloud",     "4h",  3.0, 0.0, 4.0, "4H Trail",      "saharausdt"),
    # Supertrend
    ("MSTR",       "supertrend",         "1h",  2.0, 3.0, 2.5, "Supertrend",    "mstrusdt"),
    ("XAG",        "supertrend",         "1h",  2.0, 3.0, 2.5, "Supertrend",    "xagusdt"),
    # VolExp
    ("1000PEPE",   "vol_expansion",      "1h",  2.5, 3.0, 2.5, "VolExp",        "1000pepeusdt"),
    ("WLD",        "vol_expansion",      "1h",  2.5, 3.0, 2.5, "VolExp",        "wldusdt"),
    # EMA+Ichi 4H
    ("APT",        "ema_ichimoku_hybrid","4h",  1.5, 4.0, 2.0, "EMA+Ichi 4H",  "aptusdt"),
    ("NEAR",       "ema_ichimoku_hybrid","4h",  1.5, 4.0, 2.0, "EMA+Ichi 4H",  "nearusdt"),
    ("ZETA",       "ema_ichimoku_hybrid","4h",  1.5, 4.0, 2.0, "EMA+Ichi 4H",  "zetausdt"),
    # Ichi+ST 4H
    ("LTC",        "ichi_supertrend",    "4h",  2.0, 5.0, 3.0, "Ichi+ST 4H",   "ltcusdt"),
    # Alligator
    ("LIGHT",      "alligator",          "1h",  2.0, 4.0, 3.0, "Alligator 1H", "lightusdt"),
    ("HBAR",       "alligator",          "4h",  2.0, 4.0, 3.0, "Alligator 4H", "hbarusdt"),
    ("QNT",        "alligator",          "4h",  2.0, 4.0, 3.0, "Alligator 4H", "qntusdt"),
    ("ARC_A",      "alligator",          "4h",  2.0, 4.0, 3.0, "Alligator 4H", "arcusdt"),
    ("LINK",       "alligator",          "4h",  2.0, 4.0, 3.0, "Alligator 4H", "linkusdt"),
    ("SUI",        "alligator",          "4h",  2.0, 4.0, 3.0, "Alligator 4H", "suiusdt"),
    # Dual ST
    ("LYN",        "dual_supertrend",    "1h",  2.5, 4.0, 3.0, "Dual ST 1H",   "lynusdt"),
    ("HUMA",       "dual_supertrend",    "1h",  2.5, 4.0, 3.0, "Dual ST 1H",   "humausdt"),
    ("ENJ",        "dual_supertrend",    "4h",  2.5, 4.0, 3.0, "Dual ST 4H",   "enjusdt"),
    ("XPL",        "dual_supertrend",    "4h",  2.5, 4.0, 3.0, "Dual ST 4H",   "xplusdt"),
    ("XRP",        "dual_supertrend",    "4h",  2.5, 4.0, 3.0, "Dual ST 4H",   "xrpusdt"),
    ("AKT",        "dual_supertrend",    "4h",  2.5, 4.0, 3.0, "Dual ST 4H",   "aktusdt"),
]

# ---------------------------------------------------------------------------
# Config builder — builds a complete config dict for each bot
# ---------------------------------------------------------------------------

BASE_CONFIG = {
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

# All-signals-disabled template
ALL_SIGNALS_OFF = {
    "ema_crossover": {"enabled": False},
    "ema_fast_crossover": {"enabled": False},
    "ema_pullback": {"enabled": False},
    "rsi_divergence": {"enabled": False},
    "bb_breakout": {"enabled": False},
    "mean_reversion": {"enabled": False},
    "body_dominance": {"enabled": False},
    "squeeze_release": {"enabled": False},
    "ichimoku_cloud": {"enabled": False},
    "supertrend": {"enabled": False},
    "vol_expansion": {"enabled": False},
    "dual_supertrend": {"enabled": False},
    "alligator": {"enabled": False},
    "ema_ichimoku_hybrid": {"enabled": False},
    "ichi_supertrend": {"enabled": False},
    "volexp_supertrend": {"enabled": False},
}


def build_config(
    coin: str,
    strategy_key: str,
    tf_signal: str,
    atr_sl_mult: float,
    atr_tp_mult: float,
    atr_trail_mult: float,
    data_prefix: str,
) -> dict:
    """Build a complete engine config for one bot."""
    cfg = copy.deepcopy(BASE_CONFIG)
    cfg["symbol"] = f"{data_prefix.upper()}"
    cfg["timeframe_signal"] = tf_signal
    cfg["timeframe_trend"] = tf_signal  # same TF for 4h; 1h trend for 15m handled in loader
    cfg["atr_sl_mult"] = atr_sl_mult
    cfg["atr_tp_mult"] = atr_tp_mult
    cfg["atr_trail_mult"] = atr_trail_mult
    cfg["atr_trail_mult_trending"] = atr_trail_mult
    cfg["atr_trail_mult_ranging"] = max(2.0, atr_trail_mult - 1.0)
    cfg["atr_trail_mult_volatile"] = atr_trail_mult + 1.0

    signals = copy.deepcopy(ALL_SIGNALS_OFF)
    # EMA 15m uses both ema_crossover + ema_fast_crossover
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

DATA_DIR = Path("/Users/iceai/Work/ccbt/data")


def load_signal_data(prefix: str, tf: str) -> pd.DataFrame:
    """Load 1H or 4H signal data (4H is resampled from 1H)."""
    if tf == "15m":
        path = DATA_DIR / f"{prefix}_15m_2y.csv"
        if not path.exists():
            return pd.DataFrame()
        return load_ohlcv(str(path))
    elif tf == "1h":
        path = DATA_DIR / f"{prefix}_1h_2y.csv"
        if not path.exists():
            # Try 5y
            path = DATA_DIR / f"{prefix}_1h_5y.csv"
            if not path.exists():
                return pd.DataFrame()
        return load_ohlcv(str(path))
    elif tf == "4h":
        # Resample 1H to 4H
        path = DATA_DIR / f"{prefix}_1h_2y.csv"
        if not path.exists():
            path = DATA_DIR / f"{prefix}_1h_5y.csv"
            if not path.exists():
                return pd.DataFrame()
        df1h = load_ohlcv(str(path))
        return resample_to_4h(df1h)
    return pd.DataFrame()


def load_trend_data(prefix: str, tf: str) -> pd.DataFrame:
    """Load trend data — for 15m signal bots, trend is 1H."""
    if tf == "15m":
        path = DATA_DIR / f"{prefix}_1h_2y.csv"
        if not path.exists():
            return pd.DataFrame()
        return load_ohlcv(str(path))
    else:
        # For 1H and 4H, trend = signal
        return pd.DataFrame()


def resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV data to 4H. Returns tz-naive index."""
    df = df.copy()
    # Ensure tz-naive for consistent merging with funding CSV data
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


def filter_last_year(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the last 12 months of data. Preserves existing timezone (or lack thereof)."""
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
# Run individual bot and extract trades
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
    """Run one bot and return a list of trade dicts with pnl_pct and timestamps."""
    cfg = build_config(coin, strategy_key, tf_signal, atr_sl_mult, atr_tp_mult, atr_trail_mult, data_prefix)

    signal_df = load_signal_data(data_prefix, tf_signal)
    if signal_df.empty:
        print(f"  [SKIP] {coin} — no signal data for {data_prefix} {tf_signal}")
        return []

    trend_df = load_trend_data(data_prefix, tf_signal)

    # Filter to last 1 year
    signal_df = filter_last_year(signal_df)
    if not trend_df.empty:
        trend_df = filter_last_year(trend_df)

    if len(signal_df) < 50:
        print(f"  [SKIP] {coin} — insufficient data ({len(signal_df)} bars)")
        return []

    try:
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        engine.run(signal_df, trend_df if not trend_df.empty else None)
    except Exception as exc:
        print(f"  [ERROR] {coin} ({label}): {exc}")
        return []

    trades = []
    initial_balance = 10_000.0
    for t in engine.state.trades:
        # pnl_pct as fraction of initial balance (i.e. account-level percentage)
        pnl_frac = t.pnl / initial_balance
        # Parse exit_time for sorting
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
            "pnl_frac": pnl_frac,  # fraction of $10k initial — represents 1% risk trade scaled
        })
    return trades


# ---------------------------------------------------------------------------
# Shared wallet replay
# ---------------------------------------------------------------------------

def replay_shared_wallet(all_trades: list[dict], initial_balance: float = 200.0) -> list[dict]:
    """Replay all trades chronologically with shared compounding balance.

    pnl_frac from each trade represents the P&L as a fraction of the engine's
    $10k initial balance. Since each engine runs with risk_per_trade=0.01 (1%),
    pnl_frac is effectively the gain/loss per 1% risk unit.

    For the shared wallet: dollar_pnl = balance * pnl_frac
    This correctly models 1% risk compounding on the shared balance.
    """
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


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def print_per_bot_summary(
    replayed: list[dict],
    initial_balance: float,
    all_bots: list[tuple],
) -> None:
    """Report 1: per-bot stats sorted by total dollar PnL."""
    print("\n" + "=" * 78)
    print("PORTFOLIO BACKTEST — 1 Year, Shared $200 Wallet")
    print(f"Starting balance: ${initial_balance:.2f}")
    print("=" * 78)

    # Group by coin+label
    bot_stats: dict[str, dict] = {}
    for t in replayed:
        key = t["coin"]
        if key not in bot_stats:
            bot_stats[key] = {
                "coin": t["coin"],
                "label": t["label"],
                "trades": 0,
                "wins": 0,
                "total_pnl": 0.0,
            }
        bot_stats[key]["trades"] += 1
        if t["dollar_pnl"] > 0:
            bot_stats[key]["wins"] += 1
        bot_stats[key]["total_pnl"] += t["dollar_pnl"]

    # Add bots with zero trades
    bot_keys_in_trades = set(bot_stats.keys())
    for row in all_bots:
        coin = row[0]
        label = row[6]
        if coin not in bot_keys_in_trades:
            bot_stats[coin] = {"coin": coin, "label": label, "trades": 0, "wins": 0, "total_pnl": 0.0}

    sorted_bots = sorted(bot_stats.values(), key=lambda x: x["total_pnl"], reverse=True)

    hdr = f"{'Bot':<14} {'Strategy':<16} {'Trades':>7} {'Wins':>5} {'WR%':>6}  {'PnL$':>9}"
    print(hdr)
    print("-" * 68)

    total_trades = 0
    total_wins = 0
    total_pnl = 0.0

    for bs in sorted_bots:
        wr = (bs["wins"] / bs["trades"] * 100) if bs["trades"] > 0 else 0.0
        sign = "+" if bs["total_pnl"] >= 0 else ""
        line = (
            f"{bs['coin']:<14} {bs['label']:<16} "
            f"{bs['trades']:>7} {bs['wins']:>5} {wr:>5.1f}%  "
            f"{sign}{bs['total_pnl']:>8.2f}"
        )
        print(line)
        total_trades += bs["trades"]
        total_wins += bs["wins"]
        total_pnl += bs["total_pnl"]

    print("-" * 68)
    total_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
    final_balance = initial_balance + total_pnl
    sign = "+" if total_pnl >= 0 else ""
    print(
        f"{'TOTAL':<14} {'':<16} "
        f"{total_trades:>7} {total_wins:>5} {total_wr:>5.1f}%  "
        f"{sign}{total_pnl:>8.2f}"
    )
    print(f"\nFinal balance: ${final_balance:.2f}  ({sign}{(total_pnl / initial_balance * 100):.1f}%)")


def print_monthly_report(replayed: list[dict], all_coins: list[str], initial_balance: float) -> None:
    """Report 2: monthly breakdown showing every bot."""
    print("\n\n" + "=" * 78)
    print("MONTHLY REPORT")
    print("=" * 78)

    if not replayed:
        print("No trades.")
        return

    # Group trades by month and coin
    monthly_coin: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"trades": 0, "pnl": 0.0}))
    for t in replayed:
        month = t["exit_time"].strftime("%Y-%m")
        monthly_coin[month][t["coin"]]["trades"] += 1
        monthly_coin[month][t["coin"]]["pnl"] += t["dollar_pnl"]

    # Track running balance month by month
    balance = initial_balance
    for month in sorted(monthly_coin.keys()):
        coin_data = monthly_coin[month]
        month_total_pnl = sum(v["pnl"] for v in coin_data.values())
        prev_balance = balance
        balance += month_total_pnl

        print(f"\n--- {month} ---")
        print(f"  {'Bot':<14} {'Trades':>7}  {'PnL$':>9}  {'Balance$':>10}")
        # Print each coin active this month
        for coin in sorted(coin_data.keys()):
            cd = coin_data[coin]
            sign = "+" if cd["pnl"] >= 0 else ""
            print(f"  {coin:<14} {cd['trades']:>7}  {sign}{cd['pnl']:>8.2f}")
        sign = "+" if month_total_pnl >= 0 else ""
        print(f"  {'MONTH TOTAL':<14} {sum(v['trades'] for v in coin_data.values()):>7}  "
              f"{sign}{month_total_pnl:>8.2f}  ${balance:>9.2f}")


def print_daily_report(replayed: list[dict], initial_balance: float) -> None:
    """Report 3: daily trade-level log for last 2 months."""
    print("\n\n" + "=" * 78)
    print("DAILY REPORT (Last 2 months: Feb + Mar 2026)")
    print("=" * 78)

    if not replayed:
        print("No trades.")
        return

    # Filter last 2 months
    cutoff_month = "2026-02"
    recent = [t for t in replayed if t["exit_time"].strftime("%Y-%m") >= cutoff_month]

    if not recent:
        # Fall back to last 2 months of available data
        all_months = sorted({t["exit_time"].strftime("%Y-%m") for t in replayed})
        if len(all_months) >= 2:
            cutoff_month = all_months[-2]
            recent = [t for t in replayed if t["exit_time"].strftime("%Y-%m") >= cutoff_month]
        else:
            recent = replayed[-50:]  # last 50 trades

    print(f"\n{'Date':<12} {'Bot':<14} {'Side':<6} {'Entry$':>12} {'Exit$':>12} {'PnL$':>9}  {'Balance$':>10}")
    print("-" * 80)
    for t in recent:
        date = t["exit_time"].strftime("%Y-%m-%d")
        sign = "+" if t["dollar_pnl"] >= 0 else ""
        print(
            f"{date:<12} {t['coin']:<14} {t['side']:<6} "
            f"{t['entry_price']:>12.4f} {t['exit_price']:>12.4f} "
            f"{sign}{t['dollar_pnl']:>8.2f}  ${t['balance']:>9.2f}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Loading data and running bots...")
    print(f"Total bots: {len(BOTS)}\n")

    all_trades: list[dict] = []
    all_coins = []

    for bot_row in BOTS:
        coin, strategy_key, tf_signal, atr_sl, atr_tp, atr_trail, label, data_prefix = bot_row
        all_coins.append(coin)
        print(f"  Running {coin} ({label}, {tf_signal}) ...")
        trades = run_bot(coin, strategy_key, tf_signal, atr_sl, atr_tp, atr_trail, label, data_prefix)
        print(f"    -> {len(trades)} trades")
        all_trades.extend(trades)

    print(f"\nTotal raw trades: {len(all_trades)}")

    if not all_trades:
        print("No trades generated. Check data files.")
        return

    # Shared wallet replay
    INITIAL_BALANCE = 200.0
    replayed = replay_shared_wallet(all_trades, INITIAL_BALANCE)

    # Print reports
    print_per_bot_summary(replayed, INITIAL_BALANCE, BOTS)
    print_monthly_report(replayed, all_coins, INITIAL_BALANCE)
    print_daily_report(replayed, INITIAL_BALANCE)


if __name__ == "__main__":
    main()
