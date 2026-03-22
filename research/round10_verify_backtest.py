"""Round 10 — GitHub strategy verification + portfolio backtest.

Part 1: Implement + verify G1 Dual Thrust, G3 Awesome Oscillator, G4 Range Bounce
        across all sweep_github.json winners using full BacktestEngine.
Part 2: Realistic portfolio backtest ($200 shared wallet, R-multiple, max 5 concurrent).

Run: python3 research/round10_verify_backtest.py
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
# Strategy mapping: sweep strategy name → engine signal key
# G2_KalmanTrend: not implemented (Kalman filter requires stateful estimation)
# G5_Confluence: composite multi-indicator score — not mapped to single engine key
# ---------------------------------------------------------------------------
STRATEGY_MAP: dict[str, Optional[str]] = {
    "G1_DualThrust":        "dual_thrust",
    "G3_AwesomeOscillator": "awesome_oscillator",
    "G4_RangeBounce":       "range_bounce",
    "G2_KalmanTrend":       None,  # Not implemented
    "G5_Confluence":        None,  # Composite, not directly mappable
}

# All signal keys — all off by default; we enable only one at a time
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
    "trading_hours": {"enabled": True, "start_utc": 3, "end_utc": 20},
    "regime_filter": {"enabled": True, "skip_ranging": False},  # Range Bounce needs ranging
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
}

# ---------------------------------------------------------------------------
# 14 verified existing bots from realistic portfolio backtest
# (coin, strategy_key, tf, sl, tp, trail, label, prefix)
# ---------------------------------------------------------------------------
EXISTING_BOTS: list[tuple] = [
    ("POL",      "ichimoku_cloud",  "1h",  1.0, 0.0, 3.0, "Ichi 1H Trail", "polusdt"),
    ("BERA",     "ichimoku_cloud",  "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "berausdt"),
    ("BTC",      "ema_crossover",   "15m", 1.0, 3.0, 2.0, "EMA 15m",       "btcusdt"),
    ("IP",       "ichimoku_cloud",  "1h",  2.0, 4.0, 3.0, "Ichi 1H",       "ipusdt"),
    ("1000PEPE", "vol_expansion",   "1h",  2.5, 3.0, 2.5, "VolExp",        "1000pepeusdt"),
    ("TRUMP",    "ichimoku_cloud",  "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "trumpusdt"),
    ("WIF",      "ema_crossover",   "15m", 1.0, 3.0, 2.0, "EMA 15m",       "wifusdt"),
    ("TON",      "ichimoku_cloud",  "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "tonusdt"),
    ("ONDO",     "ichimoku_cloud",  "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "ondousdt"),
    ("AVAX",     "ichimoku_cloud",  "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "avaxusdt"),
    ("TIA",      "ichimoku_cloud",  "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "tiausdt"),
    ("XMR",      "ichimoku_cloud",  "4h",  2.0, 4.0, 3.0, "Ichi 4H",       "xmrusdt"),
    ("ANIME",    "ichimoku_cloud",  "1h",  2.0, 5.0, 3.0, "Ichi 1H",       "animeusdt"),
    ("ARC",      "ema_crossover",   "15m", 1.0, 3.0, 2.0, "EMA 15m",       "arcusdt"),
]

# Already-deployed coins (to avoid duplicate bots)
DEPLOYED_COINS: set[str] = {
    "BTC", "WIF", "ARC", "AVAX", "NEAR", "POL", "GUN", "BERA", "ATH", "INJ",
    "TRUMP", "ANIME", "ZETA", "1000SHIB", "TAO", "RENDER", "HBAR", "ARB",
    "ALGO", "TRX", "POLYX", "FET", "XLM", "SAHARA", "MSTR", "XAG",
    "1000PEPE", "WLD", "APT", "LTC", "LIGHT", "QNT", "LYN", "SIGN", "ENJ",
    "TIA", "XPL", "IP", "ONDO", "LINK", "XRP", "SUI", "AKT", "H", "HUMA", "AXS",
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
        data_prefix, extra_params=extra_params
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

    # Sharpe (trade-level)
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
# Part 1: Verify sweep_github.json winners with full engine
# ---------------------------------------------------------------------------


def verify_github_winners() -> tuple[list[dict], list[dict]]:
    """Verify top G1/G3/G4 sweep winners with BacktestEngine.

    Returns (passed_list, all_results_list).
    PASS = engine PF >= 1.2 AND trades >= 8.
    """
    with open(DATA_DIR / "sweep_github.json") as f:
        sweep = json.load(f)

    # Filter to implemented strategies, PF>=1.3, trades>=10
    candidates = [
        x for x in sweep
        if STRATEGY_MAP.get(x["strategy"]) is not None
        and x.get("pf", 0) >= 1.3
        and x.get("trades", 0) >= 10
        and all(ord(c) < 128 for c in x["coin"])  # ASCII-only coins
    ]

    # Best params per (coin, strategy, timeframe)
    best_per_key: dict[tuple, dict] = {}
    for x in candidates:
        key = (x["coin"], x["strategy"], x["timeframe"])
        if key not in best_per_key or x["pf"] > best_per_key[key]["pf"]:
            best_per_key[key] = x

    items = sorted(best_per_key.values(), key=lambda x: -x["pf"])
    print(f"\nTotal candidates to verify: {len(items)}")
    print("  (G1_DualThrust, G3_AwesomeOscillator, G4_RangeBounce)")

    verification_results: list[dict] = []
    passed: list[dict] = []

    for idx, row in enumerate(items, 1):
        coin = row["coin"]
        strategy = row["strategy"]
        signal_key = STRATEGY_MAP[strategy]
        tf = row["timeframe"].lower()
        prefix = row.get("prefix", coin.lower() + "usdt")
        sweep_pf = row["pf"]
        sweep_trades = row["trades"]
        params = row.get("params", {})

        if idx % 20 == 0:
            print(f"  Progress: {idx}/{len(items)}...")

        # Build extra_params from sweep params
        extra_params: dict = {}
        if signal_key == "dual_thrust":
            extra_params["dual_thrust_k"] = params.get("k1", 0.5)
            extra_params["dual_thrust_lookback"] = params.get("lookback", 20)
        elif signal_key == "range_bounce":
            extra_params["range_bounce_lookback"] = params.get("range_lookback", 20)
            extra_params["range_bounce_rsi_lo"] = params.get("rsi_lo", 35)
            extra_params["range_bounce_rsi_hi"] = params.get("rsi_hi", 65)
            extra_params["range_bounce_atr_sl_mult"] = params.get("sl_mult", 1.5)
            extra_params["range_bounce_atr_tp_mult"] = params.get("tp_mult", 2.0)
            extra_params["mr_atr_sl_mult"] = params.get("sl_mult", 1.5)
            extra_params["mr_atr_tp_mult"] = params.get("tp_mult", 2.0)
        # For awesome_oscillator, sl/tp from params
        atr_sl_mult = params.get("sl_mult", 2.0)
        atr_tp_mult = params.get("tp_mult", 3.0)
        atr_trail_mult = 2.5

        # Check data availability
        signal_df = load_signal_data(prefix, tf)
        if signal_df.empty or len(signal_df) < 100:
            verification_results.append({
                "coin": coin, "strategy": strategy, "signal_key": signal_key,
                "tf": tf, "sweep_pf": sweep_pf, "sweep_trades": sweep_trades,
                "engine_pf": None, "wr": None, "trades": None, "sharpe": None, "dd": None,
                "status": "SKIP_NO_DATA",
            })
            continue

        # Check data span >= 12 months
        span = data_span_months(signal_df)
        if span < 12.0:
            verification_results.append({
                "coin": coin, "strategy": strategy, "signal_key": signal_key,
                "tf": tf, "sweep_pf": sweep_pf, "sweep_trades": sweep_trades,
                "engine_pf": None, "wr": None, "trades": None, "sharpe": None, "dd": None,
                "status": "SKIP_SHORT_DATA",
            })
            continue

        _, metrics = run_engine_for_bot(
            coin, signal_key, tf, atr_sl_mult, atr_tp_mult, atr_trail_mult,
            f"{strategy} {tf.upper()}", prefix,
            extra_params=extra_params, filter_year=True,
        )

        if not metrics:
            verification_results.append({
                "coin": coin, "strategy": strategy, "signal_key": signal_key,
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
            "strategy": strategy,
            "signal_key": signal_key,
            "tf": tf,
            "sweep_pf": sweep_pf,
            "sweep_trades": sweep_trades,
            "engine_pf": engine_pf,
            "wr": metrics["wr"],
            "trades": engine_trades,
            "sharpe": metrics["sharpe"],
            "dd": metrics["dd"],
            "status": status,
            "params": params,
            "prefix": prefix,
            "atr_sl_mult": atr_sl_mult,
            "atr_tp_mult": atr_tp_mult,
        }
        verification_results.append(result)

        if status == "PASS":
            passed.append(result)

    return passed, verification_results


# ---------------------------------------------------------------------------
# Part 2: Realistic portfolio backtest ($200 shared wallet)
# ---------------------------------------------------------------------------


def run_portfolio_backtest(
    new_verified_bots: list[dict],
    initial_wallet: float = 200.0,
    max_concurrent: int = 5,
) -> dict:
    """Run realistic portfolio backtest combining existing + new verified bots.

    Methodology:
    - Each bot tracks R-multiples (profit as % of its individual risk amount)
    - Per-trade risk = wallet_balance * bot_risk_pct (1% for new bots, varies for existing)
    - Max 5 concurrent positions across all bots
    - Trades are sorted by entry time and replayed chronologically
    - Balance updates after each trade exit

    Args:
        new_verified_bots: Verified new bots from verify_github_winners().
        initial_wallet: Starting shared wallet balance in USDT.
        max_concurrent: Maximum simultaneous open positions.

    Returns:
        Dict with monthly/daily reports and summary stats.
    """
    print("\n" + "=" * 70)
    print("PART 2: REALISTIC PORTFOLIO BACKTEST")
    print(f"Wallet: ${initial_wallet:.0f}  |  Max concurrent: {max_concurrent}")
    print("=" * 70)

    # Collect all trade records from all bots
    all_trade_records: list[dict] = []

    # --- Existing bots ---
    print("\nRunning existing bots...")
    for coin, strategy_key, tf, sl, tp, trail, label, prefix in EXISTING_BOTS:
        signal_df = load_signal_data(prefix, tf)
        if signal_df.empty:
            print(f"  SKIP {coin} ({label}): no data")
            continue

        span = data_span_months(signal_df)
        if span < 12.0:
            print(f"  SKIP {coin} ({label}): only {span:.1f}mo data")
            continue

        trend_df = load_trend_data(prefix, tf)
        signal_df_yr = filter_last_year(signal_df)
        trend_df_yr = filter_last_year(trend_df) if not trend_df.empty else trend_df

        cfg = build_config(coin, strategy_key, tf, sl, tp, trail, prefix)
        # Original bots use higher risk
        if coin == "BTC":
            cfg["risk_per_trade"] = 0.05
        elif coin in ("WIF", "ARC"):
            cfg["risk_per_trade"] = 0.03
        elif coin in ("AVAX", "BERA", "IP", "POL"):
            cfg["risk_per_trade"] = 0.02
        else:
            cfg["risk_per_trade"] = 0.01

        try:
            engine = BacktestEngine(cfg, initial_balance=10_000.0)
            engine.run(signal_df_yr, trend_df_yr if not trend_df_yr.empty else None)
        except Exception as exc:
            print(f"  ERROR {coin}: {exc}")
            continue

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

            # R-multiple: PnL as fraction of risk amount
            risk_amount = 10_000.0 * cfg["risk_per_trade"]
            r_mult = t.pnl / risk_amount if risk_amount > 0 else 0.0

            all_trade_records.append({
                "bot": f"{coin} ({label})",
                "coin": coin,
                "side": t.side,
                "entry_time": entry_ts,
                "exit_time": exit_ts,
                "r_mult": r_mult,
                "risk_pct": cfg["risk_per_trade"],
                "close_reason": t.close_reason,
            })

        n = len(engine.state.trades)
        print(f"  {coin:<12} {label:<16} {n:>3} trades")

    # --- New verified bots ---
    if new_verified_bots:
        print(f"\nRunning {len(new_verified_bots)} new verified bots...")
        for vbot in new_verified_bots:
            coin = vbot["coin"]
            # Skip if already deployed
            if coin in DEPLOYED_COINS:
                continue
            signal_key = vbot["signal_key"]
            tf = vbot["tf"]
            prefix = vbot.get("prefix", coin.lower() + "usdt")
            params = vbot.get("params", {})
            atr_sl = vbot.get("atr_sl_mult", 1.5)
            atr_tp = vbot.get("atr_tp_mult", 2.0)
            atr_trail = 2.5

            # Build extra params
            extra_params: dict = {}
            if signal_key == "dual_thrust":
                extra_params["dual_thrust_k"] = params.get("k1", 0.5)
                extra_params["dual_thrust_lookback"] = params.get("lookback", 20)
            elif signal_key == "range_bounce":
                extra_params["range_bounce_lookback"] = params.get("range_lookback", 20)
                extra_params["range_bounce_rsi_lo"] = params.get("rsi_lo", 35)
                extra_params["range_bounce_rsi_hi"] = params.get("rsi_hi", 65)
                extra_params["range_bounce_atr_sl_mult"] = params.get("sl_mult", 1.5)
                extra_params["range_bounce_atr_tp_mult"] = params.get("tp_mult", 2.0)
                extra_params["mr_atr_sl_mult"] = params.get("sl_mult", 1.5)
                extra_params["mr_atr_tp_mult"] = params.get("tp_mult", 2.0)

            signal_df = load_signal_data(prefix, tf)
            if signal_df.empty:
                continue
            signal_df_yr = filter_last_year(signal_df)
            trend_df_yr = load_trend_data(prefix, tf)
            if not trend_df_yr.empty:
                trend_df_yr = filter_last_year(trend_df_yr)

            cfg = build_config(coin, signal_key, tf, atr_sl, atr_tp, atr_trail, prefix, extra_params=extra_params)
            cfg["risk_per_trade"] = 0.01

            try:
                engine = BacktestEngine(cfg, initial_balance=10_000.0)
                engine.run(signal_df_yr, trend_df_yr if not trend_df_yr.empty else None)
            except Exception as exc:
                print(f"  ERROR {coin}: {exc}")
                continue

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

                risk_amount = 10_000.0 * 0.01
                r_mult = t.pnl / risk_amount if risk_amount > 0 else 0.0

                all_trade_records.append({
                    "bot": f"{coin} ({signal_key.replace('_', ' ').title()})",
                    "coin": coin,
                    "side": t.side,
                    "entry_time": entry_ts,
                    "exit_time": exit_ts,
                    "r_mult": r_mult,
                    "risk_pct": 0.01,
                    "close_reason": t.close_reason,
                })

            n = len(engine.state.trades)
            print(f"  {coin:<12} {signal_key:<24} {n:>3} trades  PF={vbot['engine_pf']:.2f}")

    if not all_trade_records:
        print("No trade records collected.")
        return {}

    # Sort by entry time
    all_trade_records.sort(key=lambda x: x["entry_time"])
    print(f"\nTotal raw trade records: {len(all_trade_records)}")

    # -----------------------------------------------------------------------
    # Replay with shared wallet, max_concurrent limit, R-multiple sizing
    # -----------------------------------------------------------------------
    wallet = initial_wallet
    open_positions: list[dict] = []  # Currently open trades
    closed_trades: list[dict] = []

    # We simulate using entry/exit times:
    # 1. At each "event" (entry or exit), update open positions
    # 2. Process exits first (frees up slots), then entries

    # Build event queue
    events: list[dict] = []
    for tr in all_trade_records:
        events.append({"type": "entry", "time": tr["entry_time"], "trade": tr})
        events.append({"type": "exit", "time": tr["exit_time"], "trade": tr})

    events.sort(key=lambda e: (e["time"], 0 if e["type"] == "exit" else 1))

    entered_trades: set[int] = set()  # Track which trades were actually entered

    for event in events:
        ev_type = event["type"]
        tr = event["trade"]
        tr_id = id(tr)

        if ev_type == "exit":
            # Only close if we actually entered this trade
            if tr_id not in entered_trades:
                continue

            # Find and close the position
            for pos in open_positions:
                if pos["trade_id"] == tr_id:
                    risk_amt = wallet * pos["risk_pct"]
                    pnl_usd = pos["r_mult"] * risk_amt
                    wallet += pnl_usd
                    wallet = max(wallet, 0.01)  # Can't go negative

                    closed_trades.append({
                        "bot": tr["bot"],
                        "coin": tr["coin"],
                        "side": tr["side"],
                        "entry_time": tr["entry_time"],
                        "exit_time": tr["exit_time"],
                        "r_mult": pos["r_mult"],
                        "pnl_usd": pnl_usd,
                        "risk_pct": pos["risk_pct"],
                        "wallet_after": wallet,
                        "close_reason": tr["close_reason"],
                    })
                    open_positions.remove(pos)
                    break

        elif ev_type == "entry":
            # Check concurrent limit
            if len(open_positions) >= max_concurrent:
                continue

            entered_trades.add(tr_id)
            open_positions.append({
                "trade_id": tr_id,
                "bot": tr["bot"],
                "r_mult": tr["r_mult"],
                "risk_pct": tr["risk_pct"],
                "entry_time": tr["entry_time"],
            })

    print(f"Closed trades after concurrent filter: {len(closed_trades)}")

    # -----------------------------------------------------------------------
    # Monthly report
    # -----------------------------------------------------------------------
    if not closed_trades:
        print("No closed trades in portfolio simulation.")
        return {}

    ct_df = pd.DataFrame(closed_trades)
    ct_df["month"] = ct_df["exit_time"].dt.to_period("M")

    print("\n" + "=" * 70)
    print("REALISTIC PORTFOLIO MONTHLY REPORT ($200 shared, R-multiple, max 5 concurrent)")
    print(f"{'Month':<10} {'Trades':>7} {'PnL$':>10} {'Balance$':>12}")
    print("-" * 42)

    balance = initial_wallet
    monthly_rows: list[dict] = []
    for month, grp in ct_df.groupby("month"):
        month_trades = len(grp)
        month_pnl = grp["pnl_usd"].sum()
        balance = grp["wallet_after"].iloc[-1]
        monthly_rows.append({"month": str(month), "trades": month_trades, "pnl": month_pnl, "balance": balance})
        print(f"{str(month):<10} {month_trades:>7} {month_pnl:>+10.2f} {balance:>12.2f}")

    # -----------------------------------------------------------------------
    # Daily report — last 2 months
    # -----------------------------------------------------------------------
    ct_df["date"] = ct_df["exit_time"].dt.date
    last_2m_cutoff = ct_df["exit_time"].max() - pd.DateOffset(months=2)
    recent = ct_df[ct_df["exit_time"] >= last_2m_cutoff].copy()

    print("\n" + "=" * 70)
    print("DAILY DETAIL — LAST 2 MONTHS")
    print(f"{'Date':<12} {'Bot':<24} {'Side':<6} {'R':<7} {'PnL$':>9} {'Balance$':>12}")
    print("-" * 75)

    for _, row in recent.iterrows():
        print(
            f"{str(row['date']):<12} {str(row['bot']):<24} {str(row['side']):<6} "
            f"{row['r_mult']:>+6.2f}R {row['pnl_usd']:>+9.2f} {row['wallet_after']:>12.2f}"
        )

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    total_pnl = ct_df["pnl_usd"].sum()
    final_balance = ct_df["wallet_after"].iloc[-1]
    total_return_pct = (final_balance - initial_wallet) / initial_wallet * 100.0
    wins = ct_df[ct_df["pnl_usd"] > 0]
    losses = ct_df[ct_df["pnl_usd"] <= 0]
    wr = len(wins) / len(ct_df) * 100.0
    gross_profit = wins["pnl_usd"].sum()
    gross_loss = abs(losses["pnl_usd"].sum())
    port_pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown on wallet
    peak = initial_wallet
    max_dd = 0.0
    for _, row in ct_df.sort_values("exit_time").iterrows():
        bal = row["wallet_after"]
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100.0
        if dd > max_dd:
            max_dd = dd

    print("\n" + "=" * 70)
    print("PORTFOLIO SUMMARY")
    print(f"  Initial balance:  ${initial_wallet:.2f}")
    print(f"  Final balance:    ${final_balance:.2f}")
    print(f"  Total return:     {total_return_pct:+.1f}%")
    print(f"  Total PnL:        ${total_pnl:+.2f}")
    print(f"  Portfolio PF:     {port_pf:.2f}")
    print(f"  Win rate:         {wr:.1f}%")
    print(f"  Max drawdown:     {max_dd:.1f}%")
    print(f"  Total trades:     {len(ct_df)}")
    print(f"  Bots:             {ct_df['bot'].nunique()}")

    return {
        "final_balance": final_balance,
        "total_return_pct": total_return_pct,
        "portfolio_pf": port_pf,
        "win_rate": wr,
        "max_dd": max_dd,
        "total_trades": len(ct_df),
        "monthly": monthly_rows,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 70)
    print("ROUND 10: GitHub Strategy Implementation + Verification + Portfolio")
    print("=" * 70)
    print("\nStrategies implemented in engine:")
    print("  G1 Dual Thrust Breakout  → signal key: dual_thrust")
    print("  G3 Awesome Oscillator    → signal key: awesome_oscillator")
    print("  G4 Range Bounce          → signal key: range_bounce")
    print("  G2 Kalman Trend          → NOT IMPLEMENTED (complex Kalman filter)")
    print("  G5 Confluence            → NOT MAPPED (composite multi-signal)")

    # -------------------------------------------------------------------
    # PART 1: VERIFY
    # -------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PART 1: ENGINE VERIFICATION")
    print("=" * 70)

    passed, all_results = verify_github_winners()

    # Print verification table
    print("\nROUND 10 ENGINE VERIFICATION")
    print(f"{'Coin':<12} {'Strategy':<22} {'TF':<5} {'Sweep_PF':>9} {'Eng_PF':>8} {'Trades':>7} {'WR%':>6} {'Status'}")
    print("-" * 80)

    for r in sorted(all_results, key=lambda x: -(x.get("engine_pf") or 0)):
        if r["status"] in ("SKIP_NO_DATA", "SKIP_SHORT_DATA", "SKIP_NOT_IMPL"):
            continue
        ep = f"{r['engine_pf']:.2f}" if r["engine_pf"] is not None else "-"
        wr = f"{r['wr']:.0f}" if r["wr"] is not None else "-"
        tr = str(r["trades"]) if r["trades"] is not None else "-"
        print(
            f"{r['coin']:<12} {r['strategy']:<22} {r['tf']:<5} "
            f"{r['sweep_pf']:>9.2f} {ep:>8} {tr:>7} {wr:>6} {r['status']}"
        )

    print(f"\nPassed: {len(passed)}/{len([r for r in all_results if r['status'] not in ('SKIP_NO_DATA','SKIP_SHORT_DATA','SKIP_NOT_IMPL')])} verified")

    print("\nPASSED (engine PF >= 1.2, trades >= 8):")
    for r in sorted(passed, key=lambda x: -x["engine_pf"]):
        print(
            f"  {r['coin']:<10} {r['strategy']:<22} {r['tf']:<5} "
            f"Sweep PF={r['sweep_pf']:.2f}  Engine PF={r['engine_pf']:.2f}  "
            f"Trades={r['trades']}  WR={r['wr']:.0f}%  DD={r['dd']:.1f}%"
        )

    # -------------------------------------------------------------------
    # PART 2: PORTFOLIO BACKTEST
    # -------------------------------------------------------------------
    summary = run_portfolio_backtest(
        new_verified_bots=passed,
        initial_wallet=200.0,
        max_concurrent=5,
    )

    # Save results to JSON
    output_path = DATA_DIR / "round10_results.json"
    with open(output_path, "w") as f:
        json.dump({
            "verification": all_results,
            "passed": passed,
            "portfolio_summary": summary,
        }, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
