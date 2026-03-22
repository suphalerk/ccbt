"""Round 11 — Stoch MTF / ZScore MeanRev / EMA Ribbon verification + portfolio backtest.

Part 1: Implement 3 new strategies (stoch_mtf, zscore_meanrev, ema_ribbon) in engine,
        then verify top sweep_round11.json winners through BacktestEngine.
Part 2: Realistic portfolio backtest ($200 shared wallet, R-multiple, max 5 concurrent)
        combining ALL rounds.

Run: python3 research/round11_verify_backtest.py
"""

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
# Strategy mapping: Round 11 sweep strategy_id -> engine signal key.
# Strategies not implemented in engine are mapped to None and skipped.
# ---------------------------------------------------------------------------
STRATEGY_MAP: dict[str, Optional[str]] = {
    "S4": "ema_ribbon",
    "S8": "stoch_mtf",
    "S9": "zscore_meanrev",
    # Not implemented in engine — skip
    "S1": None,   # Turtle 20/10
    "S3": None,   # Swing Breakout (not mapped)
    "S5": None,   # ATR Flip
    "S6": None,   # RSI2 Extreme (not mapped cleanly)
    "S7": None,   # Eltrut Reverse
    "S10": None,  # Fib Retracement
    "S11": None,  # Pivot Breakout
    "S12": None,  # HeikinAshi Rev
    "S13": None,  # ConsecRed DCA
    "S14": None,  # VolSqueeze TTM
    "S15": None,  # Triple Confluence
    "S16": None,  # S/R Break
    "S17": None,  # Candle Pattern
    "S18": None,  # Weighted Score
    "S19": None,  # Pairs Spread
    "S20": None,  # Renko Trend
}

# All signal keys off — enable only one at a time per bot
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
}

# ---------------------------------------------------------------------------
# Existing bots from all prior rounds — carried forward into portfolio
# ---------------------------------------------------------------------------
EXISTING_BOTS: list[tuple] = [
    # (coin, strategy_key, tf, sl, tp, trail, label, prefix, risk_pct)
    ("BTC",      "ema_crossover",  "15m", 1.0, 3.0, 2.0, "EMA 15m",      "btcusdt",      0.05),
    ("WIF",      "ema_crossover",  "15m", 1.0, 3.0, 2.0, "EMA 15m",      "wifusdt",      0.03),
    ("ARC",      "ema_crossover",  "15m", 1.0, 3.0, 2.0, "EMA 15m",      "arcusdt",      0.03),
    ("AVAX",     "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "avaxusdt",     0.02),
    ("POL",      "ichimoku_cloud", "1h",  1.0, 0.0, 3.0, "Ichi 1H Trail","polusdt",      0.01),
    ("GUN",      "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "gunusdt",      0.01),
    ("BERA",     "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "berausdt",     0.02),
    ("ATH",      "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "athusdt",      0.01),
    ("INJ",      "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "injusdt",      0.01),
    ("TRUMP",    "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "trumpusdt",    0.01),
    ("ANIME",    "ichimoku_cloud", "1h",  2.0, 5.0, 3.0, "Ichi 1H",      "animeusdt",    0.01),
    ("IP",       "ichimoku_cloud", "1h",  2.0, 4.0, 3.0, "Ichi 1H",      "ipusdt",       0.01),
    ("1000PEPE", "vol_expansion",  "1h",  2.5, 3.0, 2.5, "VolExp 1H",    "1000pepeusdt", 0.01),
    ("ONDO",     "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, "Ichi 4H",      "ondousdt",     0.01),
    ("TIA",      "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, "Ichi 4H",      "tiausdt",      0.01),
    ("TON",      "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, "Ichi 4H",      "tonusdt",      0.01),
    ("XMR",      "ichimoku_cloud", "4h",  2.0, 4.0, 3.0, "Ichi 4H",      "xmrusdt",      0.01),
]

# Round 10 verified bots — loaded from round10_results.json
# We include those that had >= 10 trades and PF >= 1.2 in engine.
ROUND10_MIN_PF = 1.2
ROUND10_MIN_TRADES = 8

# Coins already handled in EXISTING_BOTS or original deployed set
EXISTING_COINS: set[str] = {
    "BTC", "WIF", "ARC", "AVAX", "NEAR", "POL", "GUN", "BERA", "ATH", "INJ",
    "TRUMP", "ANIME", "ZETA", "1000SHIB", "TAO", "RENDER", "HBAR", "ARB",
    "ALGO", "TRX", "POLYX", "FET", "XLM", "SAHARA", "MSTR", "XAG",
    "1000PEPE", "WLD", "APT", "LTC", "LIGHT", "QNT", "LYN", "SIGN", "ENJ",
    "TIA", "XPL", "IP", "ONDO", "LINK", "XRP", "SUI", "AKT", "H", "HUMA",
    "TON", "XMR",
}


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
    """For 15m bots the trend is 1H; for 1H/4H it equals signal data (no separate trend)."""
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
# Part 1: Verify Round 11 sweep winners
# ---------------------------------------------------------------------------


def verify_round11_winners() -> tuple[list[dict], list[dict]]:
    """Verify top S4/S8/S9 sweep winners with BacktestEngine.

    Returns (passed_list, all_results_list).
    PASS = engine PF >= 1.2 AND trades >= 8.
    """
    with open(DATA_DIR / "sweep_round11.json") as f:
        sweep = json.load(f)

    # Filter to implemented strategies with minimum quality bar
    candidates = [
        x for x in sweep
        if STRATEGY_MAP.get(x.get("strategy_id")) is not None
        and x.get("pf", 0) >= 1.3
        and x.get("trades", 0) >= 10
        and all(ord(c) < 128 for c in x.get("coin", ""))
    ]

    # Best params per (coin, strategy_id, timeframe) — take highest sweep PF
    best_per_key: dict[tuple, dict] = {}
    for x in candidates:
        key = (x["coin"], x["strategy_id"], x["timeframe"])
        if key not in best_per_key or x["pf"] > best_per_key[key]["pf"]:
            best_per_key[key] = x

    items = sorted(best_per_key.values(), key=lambda x: -x["pf"])
    print(f"\nTotal R11 candidates to verify: {len(items)}")
    print("  (S4 EMA Ribbon, S8 Stoch MTF, S9 ZScore MeanRev)")

    verification_results: list[dict] = []
    passed: list[dict] = []

    for idx, row in enumerate(items, 1):
        coin = row["coin"]
        strat_id = row["strategy_id"]
        strategy_name = row.get("strategy", strat_id)
        signal_key = STRATEGY_MAP[strat_id]
        tf = row["timeframe"].lower()
        prefix = row.get("prefix", coin.lower() + "usdt")
        sweep_pf = row["pf"]
        sweep_trades = row.get("trades", 0)

        atr_sl_mult = row.get("sl_mult", 2.0)
        atr_tp_mult = row.get("tp_mult", 3.0)
        atr_trail_mult = row.get("trail_mult", 2.5) or 2.5

        if idx % 20 == 0:
            print(f"  Progress: {idx}/{len(items)}...")

        # Check data availability and span
        signal_df = load_signal_data(prefix, tf)
        if signal_df.empty or len(signal_df) < 100:
            verification_results.append({
                "coin": coin, "strategy": strategy_name, "signal_key": signal_key,
                "tf": tf, "sweep_pf": sweep_pf, "sweep_trades": sweep_trades,
                "engine_pf": None, "wr": None, "trades": None, "sharpe": None, "dd": None,
                "status": "SKIP_NO_DATA",
            })
            continue

        span = data_span_months(signal_df)
        if span < 12.0:
            verification_results.append({
                "coin": coin, "strategy": strategy_name, "signal_key": signal_key,
                "tf": tf, "sweep_pf": sweep_pf, "sweep_trades": sweep_trades,
                "engine_pf": None, "wr": None, "trades": None, "sharpe": None, "dd": None,
                "status": "SKIP_SHORT_DATA",
            })
            continue

        # Extra config params per strategy
        extra_params: dict = {}
        if signal_key == "zscore_meanrev":
            extra_params["zscore_atr_sl_mult"] = atr_sl_mult
            extra_params["zscore_atr_tp_mult"] = atr_tp_mult

        _, metrics = run_engine_for_bot(
            coin, signal_key, tf, atr_sl_mult, atr_tp_mult, atr_trail_mult,
            f"{strategy_name} {tf.upper()}", prefix,
            extra_params=extra_params, filter_year=True,
        )

        if not metrics:
            verification_results.append({
                "coin": coin, "strategy": strategy_name, "signal_key": signal_key,
                "tf": tf, "sweep_pf": sweep_pf, "sweep_trades": sweep_trades,
                "engine_pf": 0.0, "wr": 0.0, "trades": 0, "sharpe": 0.0, "dd": 0.0,
                "status": "FAIL_NO_TRADES",
            })
            continue

        engine_pf = metrics["pf"]
        engine_trades = metrics["trades"]
        status = "PASS" if engine_pf >= 1.2 and engine_trades >= 8 else "FAIL"

        result = {
            "coin": coin,
            "strategy": strategy_name,
            "signal_key": signal_key,
            "tf": tf,
            "sweep_pf": round(sweep_pf, 3),
            "sweep_trades": sweep_trades,
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

    return passed, verification_results


def print_verification_report(
    verification_results: list[dict],
    passed: list[dict],
    label: str = "Round 11",
) -> None:
    """Print a formatted verification summary."""
    print(f"\n{'=' * 70}")
    print(f"PART 1: {label} VERIFICATION RESULTS")
    print(f"{'=' * 70}")

    by_strategy: dict[str, list] = defaultdict(list)
    for r in verification_results:
        by_strategy[r.get("signal_key", "unknown")].append(r)

    for strat, results in sorted(by_strategy.items()):
        passed_strat = [r for r in results if r["status"] == "PASS"]
        total = len(results)
        print(f"\n  {strat}: {len(passed_strat)}/{total} passed")
        for r in sorted(passed_strat, key=lambda x: -(x.get("engine_pf") or 0)):
            print(
                f"    {r['coin']:<12} {r['tf'].upper():<4}  "
                f"sweep={r['sweep_pf']:.2f}  engine={r.get('engine_pf', 0):.2f}  "
                f"trades={r.get('trades', 0)}  WR={r.get('wr', 0):.0f}%  "
                f"Sharpe={r.get('sharpe', 0):.2f}  DD={r.get('dd', 0):.1f}%"
            )

    print(f"\nTotal PASS: {len(passed)} / {len(verification_results)}")


# ---------------------------------------------------------------------------
# Part 2: Realistic portfolio backtest
# ---------------------------------------------------------------------------


def run_portfolio_backtest(
    new_verified_bots: list[dict],
    initial_wallet: float = 200.0,
    max_concurrent: int = 5,
) -> dict:
    """Run realistic portfolio backtest combining all rounds.

    Methodology (R-multiple replay):
    1. Each bot runs independently on 10K virtual balance to get R-multiples.
       R-multiple = trade_pnl / (10K * bot_risk_pct).
    2. All trades across all bots are sorted by entry_time.
    3. At each trade, dollar_pnl = shared_wallet * bot_risk_pct * R_multiple.
    4. Max 5 concurrent positions enforced — later-opening trades are skipped.
    5. Balance updates after each trade exit.

    This avoids double compounding while keeping position sizing realistic.
    """
    print(f"\n{'=' * 70}")
    print("PART 2: REALISTIC PORTFOLIO BACKTEST")
    print(f"Wallet: ${initial_wallet:.0f}  |  Max concurrent: {max_concurrent}")
    print(f"{'=' * 70}")

    all_trade_records: list[dict] = []

    def collect_trades_from_engine(
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
        """Run engine for one bot and collect R-multiple trade records."""
        signal_df = load_signal_data(prefix, tf)
        if signal_df.empty:
            print(f"  SKIP {coin} ({label}): no data")
            return 0

        span = data_span_months(signal_df)
        if span < 12.0:
            print(f"  SKIP {coin} ({label}): only {span:.1f}mo data")
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
        print(f"  {coin:<12} {label:<18} {n:>3} trades")
        return n

    # --- Existing bots ---
    print("\nRunning existing bots...")
    for coin, strategy_key, tf, sl, tp, trail, label, prefix, risk_pct in EXISTING_BOTS:
        collect_trades_from_engine(coin, strategy_key, tf, sl, tp, trail, label, prefix, risk_pct)

    # --- Round 10 verified bots (from round10_results.json) ---
    round10_path = DATA_DIR / "round10_results.json"
    if round10_path.exists():
        with open(round10_path) as f:
            r10 = json.load(f)
        r10_passed = [
            x for x in r10.get("passed", [])
            if x.get("engine_pf", 0) >= ROUND10_MIN_PF
            and x.get("trades", 0) >= ROUND10_MIN_TRADES
            and x["coin"] not in EXISTING_COINS
        ]
        if r10_passed:
            print(f"\nRunning {len(r10_passed)} Round 10 verified bots...")
        for vbot in r10_passed:
            coin = vbot["coin"]
            signal_key = vbot["signal_key"]
            tf = vbot["tf"]
            prefix = vbot.get("prefix", coin.lower() + "usdt")
            params = vbot.get("params", {})
            atr_sl = vbot.get("atr_sl_mult", 1.5)
            atr_tp = vbot.get("atr_tp_mult", 2.0)
            atr_trail = 2.5

            extra_params: dict = {}
            if signal_key == "dual_thrust":
                extra_params["dual_thrust_k"] = params.get("k1", 0.5)
                extra_params["dual_thrust_lookback"] = params.get("lookback", 20)
            elif signal_key == "range_bounce":
                extra_params["range_bounce_lookback"] = params.get("range_lookback", 20)
                extra_params["range_bounce_rsi_lo"] = params.get("rsi_lo", 35)
                extra_params["range_bounce_rsi_hi"] = params.get("rsi_hi", 65)
                extra_params["range_bounce_atr_sl_mult"] = atr_sl
                extra_params["range_bounce_atr_tp_mult"] = atr_tp
                extra_params["mr_atr_sl_mult"] = atr_sl
                extra_params["mr_atr_tp_mult"] = atr_tp

            collect_trades_from_engine(
                coin, signal_key, tf, atr_sl, atr_tp, atr_trail,
                f"{signal_key[:8]} R10", prefix, 0.01, extra_params,
            )

    # --- Round 11 new verified bots ---
    new_eligible = [
        vbot for vbot in new_verified_bots
        if vbot["coin"] not in EXISTING_COINS
    ]
    if new_eligible:
        print(f"\nRunning {len(new_eligible)} Round 11 new bots...")
    for vbot in new_eligible:
        coin = vbot["coin"]
        signal_key = vbot["signal_key"]
        tf = vbot["tf"]
        prefix = vbot.get("prefix", coin.lower() + "usdt")
        atr_sl = vbot.get("atr_sl_mult", 2.0)
        atr_tp = vbot.get("atr_tp_mult", 3.0)
        atr_trail = vbot.get("atr_trail_mult", 2.5) or 2.5

        extra_params = {}
        if signal_key == "zscore_meanrev":
            extra_params["zscore_atr_sl_mult"] = atr_sl
            extra_params["zscore_atr_tp_mult"] = atr_tp

        collect_trades_from_engine(
            coin, signal_key, tf, atr_sl, atr_tp, atr_trail,
            f"{signal_key[:8]} R11", prefix, 0.01, extra_params,
        )

    if not all_trade_records:
        print("No trade records collected — aborting portfolio backtest.")
        return {}

    # ---------------------------------------------------------------------------
    # R-multiple replay
    # ---------------------------------------------------------------------------
    all_df = pd.DataFrame(all_trade_records)
    all_df = all_df.sort_values("entry_time").reset_index(drop=True)

    balance = initial_wallet
    open_trades: list[dict] = []   # Trades currently open (track by exit_time)
    per_bot_stats: dict[str, dict] = defaultdict(lambda: {
        "trades": 0, "pnl": 0.0, "wins": 0, "r_total": 0.0
    })
    monthly_pnl: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    daily_log: list[dict] = []
    # Track equity peak for drawdown computation inline (all trades, not just last 200)
    _eq_peak = initial_wallet
    _max_dd_usd = 0.0

    def close_expired_trades(current_time: pd.Timestamp) -> None:
        nonlocal balance, open_trades, _eq_peak, _max_dd_usd
        still_open = []
        for ot in open_trades:
            if ot["exit_time"] <= current_time:
                dollar_pnl = balance * ot["risk_pct"] * ot["r_mult"]
                balance += dollar_pnl
                balance = max(balance, 0.01)  # Never go below 1 cent

                # Inline drawdown tracking (all trades, avoids truncation issue)
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
        entry_time = row["entry_time"]
        close_expired_trades(entry_time)

        if len(open_trades) >= max_concurrent:
            continue  # Skip — at capacity

        open_trades.append(row.to_dict())

    # Close any remaining open trades at the last exit_time
    if open_trades:
        last_exit = max(t["exit_time"] for t in open_trades)
        close_expired_trades(last_exit + pd.Timedelta(seconds=1))

    # ---------------------------------------------------------------------------
    # Reports
    # ---------------------------------------------------------------------------
    print(f"\n{'=' * 70}")
    print("REPORT 1: PER-BOT SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Bot':<28} {'Strategy':<12} {'Trades':>6}  {'WR%':>5}  {'Avg_R':>6}  {'PnL$':>8}")
    print("-" * 72)

    all_bots_sorted = sorted(per_bot_stats.items(), key=lambda x: -x[1]["pnl"])
    for bot_name, stats in all_bots_sorted:
        n = stats["trades"]
        if n == 0:
            continue
        wr = stats["wins"] / n * 100.0
        avg_r = stats["r_total"] / n
        print(
            f"{bot_name:<28} {'':12} {n:>6}  {wr:>5.1f}  {avg_r:>+6.3f}  {stats['pnl']:>+8.2f}"
        )

    print(f"\n{'=' * 70}")
    print("REPORT 2: MONTHLY BREAKDOWN (1 year)")
    print(f"{'=' * 70}")
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

        # Per-bot breakdown within month
        bot_rows = [(k, v) for k, v in m.items() if k not in ("pnl", "trades") and v != 0]
        for bot_name, bot_pnl in sorted(bot_rows, key=lambda x: -abs(x[1]))[:5]:
            print(f"  {'':8}  {bot_name:<26}  {bot_pnl:>+9.2f}")

        monthly_report_rows.append({
            "month": month_key,
            "trades": month_trades,
            "pnl": round(month_pnl, 2),
            "balance": round(running_bal, 2),
        })

    print(f"\n{'=' * 70}")
    print("REPORT 3: DAILY LOG (last 2 months)")
    print(f"{'=' * 70}")
    print(f"{'Date':<12}  {'Bot':<28}  {'Side':<6}  {'R':>6}  {'PnL$':>8}  {'Balance$':>10}")
    print("-" * 78)

    if daily_log:
        log_df = pd.DataFrame(daily_log)
        cutoff_date = (
            pd.to_datetime(log_df["date"]).max() - pd.DateOffset(months=2)
        ).strftime("%Y-%m-%d")
        recent_log = log_df[log_df["date"] >= cutoff_date]
        for _, row in recent_log.iterrows():
            print(
                f"{row['date']:<12}  {row['bot']:<28}  {row['side']:<6}  "
                f"{row['r_mult']:>+6.3f}  {row['pnl']:>+8.2f}  ${row['balance']:>9.2f}"
            )

    # Summary
    total_trades = sum(s["trades"] for s in per_bot_stats.values())
    total_pnl = balance - initial_wallet
    total_return_pct = total_pnl / initial_wallet * 100.0

    print(f"\n{'=' * 70}")
    print("PORTFOLIO SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Initial balance:  ${initial_wallet:.2f}")
    print(f"  Final balance:    ${balance:.2f}")
    print(f"  Total return:     {total_return_pct:+.1f}%  (${total_pnl:+.2f})")
    print(f"  Total trades:     {total_trades}")
    print(f"  Unique bots:      {len(per_bot_stats)}")

    # Max drawdown computed inline during replay (all trades, not just last 200 in daily_log).
    # Express as % of the peak equity at the time of the trough (standard definition).
    max_dd_usd = _max_dd_usd
    max_dd_pct = (max_dd_usd / _eq_peak * 100.0) if _eq_peak > 0 else 0.0
    print(f"  Max drawdown:     {max_dd_pct:.1f}%  (${max_dd_usd:.2f} from peak ${_eq_peak:.2f})")

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
    print("=" * 70)
    print("ROUND 11 — NEW STRATEGIES + PORTFOLIO BACKTEST")
    print("Strategies: S4 EMA Ribbon, S8 Stoch MTF, S9 ZScore MeanRev")
    print("=" * 70)

    # Part 1: Verify Round 11 sweep winners
    passed, verification_results = verify_round11_winners()
    print_verification_report(verification_results, passed, "Round 11")

    # Part 2: Portfolio backtest
    portfolio = run_portfolio_backtest(passed, initial_wallet=200.0, max_concurrent=5)

    # Save results
    out = {
        "verification": verification_results,
        "passed": passed,
        "portfolio_summary": portfolio,
    }
    out_path = DATA_DIR / "round11_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
