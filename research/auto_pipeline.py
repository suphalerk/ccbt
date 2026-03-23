#!/usr/bin/env python3
"""
Automated Research Pipeline — Full end-to-end strategy research.

Learned from 12 rounds of manual research:
1. IDEATE   → Generate strategy ideas (combos, param variations, external sources)
2. SWEEP    → Lightweight test across all coins (fast, thousands of tests)
3. VERIFY   → Full BacktestEngine on winners (accurate, filters overfitting)
4. AUDIT    → Walk-forward test, BTC sanity check, signal verification
5. BACKTEST → Shared wallet $200, R-multiple (FIXED), max 5 concurrent
6. REPORT   → Monthly + daily reports, per-bot summary
7. DEPLOY   → Generate configs, update docker-compose-multi.yml

Usage:
    # Full pipeline: sweep → verify → audit → backtest → report
    python3 research/auto_pipeline.py --full

    # Sweep only (fast exploration)
    python3 research/auto_pipeline.py --sweep-only

    # Verify + backtest only (from existing sweep results)
    python3 research/auto_pipeline.py --verify-only --input data/sweep_xxx.json

    # Improve weak bots (parameter optimization on losing bots)
    python3 research/auto_pipeline.py --improve-weak

    # Add new strategy ideas and test them
    python3 research/auto_pipeline.py --new-strategies strategies.json

    # Deploy verified winners
    python3 research/auto_pipeline.py --deploy

Key lessons embedded:
- Sweep pass rate to engine ~30% — always verify
- Z-Score sweep PFs are unreliable (overfitting on tiny samples)
- R-multiple MUST use actual engine risk_per_trade (BTC=5%, WIF=3%, others=1%)
- Walk-forward mandatory: OOS PF >= 60% of IS PF, else REJECT
- Min 15 trades for deployment, min 6 for verification
- 4H > 1H for most strategies
- ADX confirmation boosts any strategy
- Pattern strategies (candles) don't work on crypto
- High-frequency 15m scalping killed by fees (PF 0.3-0.5)
- Max 5 concurrent positions in shared wallet
- Data >= 12 months required
"""

import argparse
import copy
import json
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# ============================================================================
# Constants — learned from 12 rounds of research
# ============================================================================

SHARED_WALLET_RISK = 0.01  # Always 1% in shared wallet regardless of bot config
MAX_CONCURRENT = 5
MIN_DATA_MONTHS = 12
MIN_TRADES_VERIFY = 6
MIN_TRADES_DEPLOY = 15
MIN_WALKFORWARD_RATIO = 0.6  # OOS PF must be >= 60% of IS PF
INITIAL_BALANCE = 200.0
ENGINE_BALANCE = 10000.0

# All signals available in engine (as of R12)
ALL_SIGNALS = [
    'ema_crossover', 'ema_fast_crossover', 'ema_pullback', 'rsi_divergence',
    'bb_breakout', 'mean_reversion', 'body_dominance', 'squeeze_release',
    'ichimoku_cloud', 'supertrend', 'vol_expansion',
    'dual_supertrend', 'alligator', 'ema_ichimoku_hybrid', 'ichi_supertrend',
    'volexp_supertrend', 'adx_di_cross', 'choppiness_ema', 'williams_r_adx',
    'roc_momentum', 'stoch_supertrend', 'price_channel_vol', 'ema_alligator',
    'supertrend_volume', 'stoch_mtf', 'zscore_meanrev', 'ema_ribbon',
    'dual_thrust', 'awesome_oscillator', 'range_bounce',
    'ribbon_rsi_vol', 'dualthrust_adx', 'zscore_stoch', 'ichi_adx', 'ribbon_ao',
]

# Strategies that historically work well (from 12 rounds)
TOP_STRATEGIES = [
    {"signal": "dual_thrust", "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"signal": "ichimoku_cloud", "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"signal": "ichimoku_cloud", "tf": "4h", "sl": 3.0, "tp": 0, "trail": 4.0},  # trail-only
    {"signal": "awesome_oscillator", "tf": "4h", "sl": 1.5, "tp": 4.0, "trail": 3.0},
    {"signal": "ema_ribbon", "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"signal": "dualthrust_adx", "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"signal": "ichi_adx", "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"signal": "supertrend", "tf": "4h", "sl": 2.5, "tp": 5.0, "trail": 4.0},
    {"signal": "range_bounce", "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"signal": "zscore_meanrev", "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0},
    {"signal": "stoch_mtf", "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
]

# Parameter grid for optimization
PARAM_GRID = [
    (1.0, 2.0, 2.0), (1.0, 3.0, 2.0), (1.5, 3.0, 2.5),
    (1.5, 4.0, 3.0), (2.0, 3.0, 2.5), (2.0, 4.0, 3.0),
    (2.0, 5.0, 3.0), (2.5, 4.0, 3.5), (2.5, 5.0, 4.0),
    (3.0, 0.0, 4.0),  # trail-only
]

# ============================================================================
# IDEATION SOURCES — ranked by historical success rate
# ============================================================================
#
# Source 1: GitHub open-source bots (BEST — Round 10 ALL 5 passed)
#   - Search: github.com "trading strategy" python crypto stars:>100
#   - Top repos: je-suis-tm/quant-trading, FMZQuant, OctoBot, NostalgiaForInfinity
#   - Extract: indicator logic, entry/exit rules, parameter defaults
#   - Why works: battle-tested by real traders, community-validated
#
# Source 2: TradingView/FMZQuant collections (GOOD — Round 11)
#   - Search: TradingView community scripts, FMZQuant strategy names
#   - Focus: strategies with >1000 likes or >100 stars
#   - Extract: Pine Script logic → translate to Python
#
# Source 3: Indicator combinations (GOOD — Round 12)
#   - Take 2-3 indicators that work individually
#   - Combine with AND logic (both must agree)
#   - Best combo pattern: [trend indicator] + [ADX confirmation]
#   - DualThrust+ADX was dominant (13/23 passes)
#
# Source 4: Parameter optimization (MODERATE — Weak bot sweep)
#   - Use strategy that works on OTHER coins
#   - Test 10 SL/TP/Trail parameter sets
#   - Test both 1H and 4H timeframes
#   - Best for fixing losing bots, not finding new edge
#
# Source 5: Academic papers (LOW — tested but marginal)
#   - Machine learning predictions add complexity but not edge
#   - Kalman filter ≈ EMA performance
#   - Statistical arbitrage (pairs) doesn't work reliably on crypto
#
# Source 6: Custom indicator ideas (LOWEST — Round 7-9)
#   - Novel indicators without community validation
#   - High failure rate (~70% fail engine verification)
#   - Only worth trying after exhausting sources 1-4
#
# WHAT NEVER WORKS (don't retry):
#   - 15m/5m scalping (fees > edge, PF 0.3-0.5)
#   - Candlestick patterns (pin bar, engulfing, inside bar — PF < 1.0)
#   - Mean reversion without range detection (BB bounce, RSI extreme)
#   - Multi-confluence 4-5 indicators (too selective, fewer trades)
#   - BTC 5m anything (oracle max PF 1.10)
#   - MACD standalone (PF 0.71)
#   - EMA pullback (structurally unprofitable)

# All combo patterns that historically produce winners
COMBO_PATTERNS = [
    # [base_signal] + [confirmation] — from Round 12 mega100
    # Format: (signal, extra_condition_description)
    ("dual_thrust", "ADX > 25"),        # 13 passes — dominant
    ("ichimoku_cloud", "ADX > 25"),     # 5 passes
    ("ema_ribbon", "RSI > 50 + Vol"),   # best avg PF 1.44
    ("ema_ribbon", "AO > 0"),           # 18 winners
    ("awesome_oscillator", "EMA trend"), # 28 winners in sweep
    ("supertrend", "Volume > 2x"),      # 5 winners
    ("range_bounce", "RSI + Stoch"),    # works with proper range detection
]

# ============================================================================
# Config Builder
# ============================================================================

def load_template():
    """Load base config template."""
    template_path = PROJECT_ROOT / "config_avax_ichi.json"
    if not template_path.exists():
        # Fallback: build minimal template
        return {
            "exchange": "binance", "use_testnet": True, "leverage": 25,
            "max_daily_loss": 0.3, "max_positions": 2, "max_consecutive_losses": 5,
            "cooldown_hours": 1, "max_api_errors": 3,
            "ema_fast": 9, "ema_slow": 21, "ema_trend": 50, "rsi_period": 14,
            "atr_period": 14, "volume_mult": 1.0, "commission_rate": 0.0004,
            "slippage_rate": 0.00015, "crossover_lookback": 2,
            "weekend_trading_enabled": False, "ema_slope_period": 5, "ema_slope_min": 0.02,
            "trading_hours": {"enabled": True, "start_utc": 3, "end_utc": 20},
            "regime_filter": {"enabled": True, "skip_ranging": True},
            "flexible_cooldown": {"enabled": False},
            "adaptive_sizing": {"enabled": False},
            "pyramiding": {"enabled": False},
            "mtd_accelerator": {"enabled": False},
            "signal_scorer": {"enabled": False},
            "ai_layer": {"enabled": False},
        }
    with open(template_path) as f:
        return json.load(f)


_TEMPLATE = None

def get_template():
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = load_template()
    return _TEMPLATE


def build_config(coin: str, signal: str, tf: str, sl: float, tp: float,
                 trail: float, risk: float = 0.01) -> dict:
    """Build a complete engine config."""
    cfg = copy.deepcopy(get_template())
    cfg["symbol"] = f"{coin}USDT"
    cfg["timeframe_signal"] = tf
    cfg["timeframe_trend"] = tf
    cfg["risk_per_trade"] = risk
    cfg["leverage"] = 25
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_min"] = 0.0
    cfg["volume_mult"] = 1.0
    cfg["volume_max_mult"] = None
    cfg["min_rr_ratio"] = 0
    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"] = {"enabled": False}
    cfg["signals"] = {s: {"enabled": False} for s in ALL_SIGNALS}
    cfg["signals"][signal] = {"enabled": True}
    if signal in ("ema_crossover",):
        cfg["signals"]["ema_fast_crossover"] = {"enabled": True}
    if "ichi" in signal:
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52
    return cfg


# ============================================================================
# Data Loading
# ============================================================================

def discover_coins(min_months: int = MIN_DATA_MONTHS) -> List[str]:
    """Find all coins with sufficient 1H data."""
    coins = []
    for f in sorted(DATA_DIR.glob("*_1h_2y.csv")):
        prefix = f.stem.replace("_1h_2y", "")
        try:
            df = pd.read_csv(f, index_col="timestamp", parse_dates=True, nrows=5)
            df_full = pd.read_csv(f, index_col="timestamp", parse_dates=True)
            months = (df_full.index[-1] - df_full.index[0]).days / 30
            if months >= min_months:
                coins.append(prefix)
        except Exception:
            continue
    return coins


def load_coin_data(prefix: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Load 1H data and create 4H resample."""
    path = DATA_DIR / f"{prefix}_1h_2y.csv"
    df_1h = load_ohlcv(str(path))
    df_4h = df_1h.resample("4h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    return df_1h, df_4h


# ============================================================================
# Step 1: SWEEP — Test strategies across coins
# ============================================================================

def run_engine_test(cfg: dict, signal_data: pd.DataFrame,
                    trend_data: pd.DataFrame) -> Optional[dict]:
    """Run BacktestEngine and return metrics."""
    try:
        engine = BacktestEngine(cfg, initial_balance=ENGINE_BALANCE)
        engine.run(signal_data, trend_data)
        trades = engine.state.trades
        if not trades:
            return None

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0)
        wr = len(wins) / len(trades) * 100 if trades else 0

        # Max drawdown
        equity = ENGINE_BALANCE
        peak = equity
        max_dd = 0
        for t in trades:
            equity += t.pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return {
            "trades": len(trades),
            "pf": round(pf, 2),
            "wr": round(wr, 1),
            "dd": round(max_dd * 100, 1),
            "raw_trades": trades,  # for R-multiple calc
            "actual_risk": cfg.get("risk_per_trade", 0.01),
        }
    except Exception as e:
        logger.warning(f"Engine error: {e}")
        return None


def sweep_strategies(coins: List[str], strategies: List[dict],
                     param_grid: Optional[List[tuple]] = None) -> List[dict]:
    """Sweep strategies across coins. Returns list of results."""
    results = []
    total = len(coins)

    for i, prefix in enumerate(coins):
        if (i + 1) % 10 == 0:
            print(f"  Sweep progress: {i+1}/{total} coins")

        try:
            df_1h, df_4h = load_coin_data(prefix)
        except Exception:
            continue

        coin = prefix.replace("usdt", "").upper()
        if coin.startswith("1000"):
            coin = coin  # keep as-is

        for strat in strategies:
            signal = strat["signal"]
            default_tf = strat.get("tf", "1h")
            default_sl = strat.get("sl", 2.0)
            default_tp = strat.get("tp", 4.0)
            default_trail = strat.get("trail", 3.0)

            params_to_test = param_grid if param_grid else [(default_sl, default_tp, default_trail)]

            for sl, tp, trail in params_to_test:
                for tf, df in [("1h", df_1h), ("4h", df_4h)]:
                    cfg = build_config(coin, signal, tf, sl, tp, trail)
                    metrics = run_engine_test(cfg, df, df)
                    if metrics and metrics["trades"] >= MIN_TRADES_VERIFY:
                        results.append({
                            "coin": coin, "prefix": prefix, "signal": signal,
                            "tf": tf, "sl": sl, "tp": tp, "trail": trail,
                            "pf": metrics["pf"], "wr": metrics["wr"],
                            "trades": metrics["trades"], "dd": metrics["dd"],
                            "actual_risk": metrics["actual_risk"],
                        })

    results.sort(key=lambda x: -x["pf"])
    return results


# ============================================================================
# Step 0: IDEATE — Generate strategy ideas automatically
# ============================================================================

def ideate_strategies(mode: str = "all") -> List[dict]:
    """Generate strategy ideas based on what historically works.

    Modes:
        "all"       — all implemented strategies × both timeframes
        "combos"    — combo signals (2 indicators AND)
        "params"    — top strategies × parameter grid
        "new_coins" — existing strategies on coins not yet tested

    Returns list of strategy dicts ready for sweep.
    """
    strategies = []

    if mode in ("all", "combos"):
        # All single signals × 1H + 4H
        trend_signals = [
            "dual_thrust", "ichimoku_cloud", "awesome_oscillator",
            "ema_ribbon", "supertrend", "dual_supertrend", "alligator",
            "ema_ichimoku_hybrid", "ichi_supertrend",
            "adx_di_cross", "choppiness_ema", "roc_momentum",
            "stoch_mtf", "ema_alligator", "supertrend_volume",
            "price_channel_vol", "williams_r_adx",
        ]
        mr_signals = ["zscore_meanrev", "range_bounce", "zscore_stoch"]
        combo_signals = ["dualthrust_adx", "ichi_adx", "ribbon_rsi_vol", "ribbon_ao"]

        for signal in trend_signals:
            strategies.append({"signal": signal, "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0})
            strategies.append({"signal": signal, "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0})

        for signal in mr_signals:
            strategies.append({"signal": signal, "tf": "1h", "sl": 1.5, "tp": 2.0, "trail": 2.0})

        for signal in combo_signals:
            strategies.append({"signal": signal, "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0})
            strategies.append({"signal": signal, "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0})

    elif mode == "params":
        # Top strategies × parameter grid (for optimization)
        strategies = TOP_STRATEGIES  # will be combined with PARAM_GRID in sweep

    elif mode == "new_coins":
        strategies = TOP_STRATEGIES

    else:
        strategies = TOP_STRATEGIES

    print(f"  Ideation mode '{mode}': {len(strategies)} strategy variants generated")
    return strategies


def ideate_from_file(filepath: str) -> List[dict]:
    """Load custom strategy ideas from JSON file.

    Expected format:
    [
        {"signal": "dual_thrust", "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
        {"signal": "ichimoku_cloud", "tf": "4h", "sl": 3.0, "tp": 0, "trail": 4.0},
        ...
    ]
    """
    with open(filepath) as f:
        strategies = json.load(f)
    print(f"  Loaded {len(strategies)} strategies from {filepath}")
    return strategies


# ============================================================================
# Step 2: VERIFY — Full engine verification with stricter filters
# ============================================================================

def verify_winners(sweep_results: List[dict], min_pf: float = 1.2,
                   min_trades: int = MIN_TRADES_VERIFY) -> List[dict]:
    """Filter sweep results by thresholds."""
    return [r for r in sweep_results if r["pf"] >= min_pf and r["trades"] >= min_trades]


# ============================================================================
# Step 3: AUDIT — Walk-forward test
# ============================================================================

def audit_walkforward(coin: str, prefix: str, signal: str, tf: str,
                      sl: float, tp: float, trail: float) -> dict:
    """Run walk-forward test: split data in half, compare IS vs OOS."""
    try:
        df_1h, df_4h = load_coin_data(prefix)
        df = df_4h if tf == "4h" else df_1h

        midpoint = len(df) // 2
        first_half = df.iloc[:midpoint]
        second_half = df.iloc[midpoint:]

        cfg = build_config(coin, signal, tf, sl, tp, trail)

        m1 = run_engine_test(cfg, first_half, first_half)
        m2 = run_engine_test(cfg, second_half, second_half)

        pf1 = m1["pf"] if m1 else 0
        pf2 = m2["pf"] if m2 else 0
        tr1 = m1["trades"] if m1 else 0
        tr2 = m2["trades"] if m2 else 0

        ratio = pf2 / pf1 if pf1 > 0 else 0
        stable = ratio >= MIN_WALKFORWARD_RATIO and tr2 >= 3

        return {
            "coin": coin, "signal": signal, "tf": tf,
            "is_pf": pf1, "is_trades": tr1,
            "oos_pf": pf2, "oos_trades": tr2,
            "ratio": round(ratio, 2),
            "stable": stable,
            "verdict": "PASS" if stable else "FAIL",
        }
    except Exception as e:
        return {"coin": coin, "signal": signal, "verdict": "ERROR", "error": str(e)}


# ============================================================================
# Step 4: BACKTEST — Shared wallet with R-multiple (FIXED)
# ============================================================================

def run_shared_wallet(verified: List[dict], initial_balance: float = INITIAL_BALANCE,
                      max_concurrent: int = MAX_CONCURRENT) -> dict:
    """Run shared wallet backtest with CORRECT R-multiple method."""
    all_trades = []

    for v in verified:
        try:
            df_1h, df_4h = load_coin_data(v["prefix"])
            df = df_4h if v["tf"] == "4h" else df_1h

            cfg = build_config(v["coin"], v["signal"], v["tf"], v["sl"], v["tp"], v["trail"])
            actual_risk = cfg["risk_per_trade"]

            engine = BacktestEngine(cfg, initial_balance=ENGINE_BALANCE)
            engine.run(df, df)

            # Compute R-multiples with CORRECT actual risk
            engine_bal = ENGINE_BALANCE
            for t in engine.state.trades:
                engine_risk = engine_bal * actual_risk
                r_mult = t.pnl / engine_risk if engine_risk > 0 else 0
                engine_bal += t.pnl
                engine_bal = max(engine_bal, 100)

                entry_time = pd.Timestamp(t.entry_time) if t.entry_time else None
                exit_time = pd.Timestamp(t.exit_time) if t.exit_time else None

                if entry_time and exit_time:
                    all_trades.append({
                        "coin": v["coin"], "signal": v["signal"],
                        "side": t.side,
                        "entry_time": entry_time, "exit_time": exit_time,
                        "r_multiple": r_mult,
                        "bot": f"{v['coin']} {v['signal']} {v['tf']}",
                    })
        except Exception as e:
            logger.warning(f"Backtest error {v['coin']}: {e}")

    if not all_trades:
        return {"final_balance": initial_balance, "total_return_pct": 0}

    # Sort by entry time, enforce concurrent limit
    all_trades.sort(key=lambda t: t["entry_time"])
    active = []
    for t in all_trades:
        active = [a for a in active if a["exit_time"] > t["entry_time"]]
        t["active"] = len(active) < max_concurrent
        if t["active"]:
            active.append(t)

    # Replay by exit time
    active_trades = [t for t in all_trades if t.get("active")]
    active_trades.sort(key=lambda t: t["exit_time"])

    balance = initial_balance
    peak = balance
    max_dd = 0
    monthly = defaultdict(lambda: {"pnl": 0, "trades": 0, "balance": 0})
    daily_trades = []

    for t in active_trades:
        # FIXED: Always use 1% shared wallet risk × R-multiple
        shared_risk = balance * SHARED_WALLET_RISK
        dollar_pnl = shared_risk * t["r_multiple"]
        balance += dollar_pnl
        balance = max(balance, 0.01)

        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

        month_key = t["exit_time"].strftime("%Y-%m")
        monthly[month_key]["pnl"] += dollar_pnl
        monthly[month_key]["trades"] += 1
        monthly[month_key]["balance"] = balance

        daily_trades.append({
            "date": t["exit_time"].strftime("%Y-%m-%d"),
            "bot": t["bot"], "side": t["side"],
            "r_multiple": round(t["r_multiple"], 2),
            "pnl": round(dollar_pnl, 2),
            "balance": round(balance, 2),
        })

    monthly_list = [{"month": k, **v} for k, v in sorted(monthly.items())]
    for m in monthly_list:
        m["balance"] = round(m["balance"], 2)
        m["pnl"] = round(m["pnl"], 2)

    return {
        "initial_balance": initial_balance,
        "final_balance": round(balance, 2),
        "total_return_pct": round((balance - initial_balance) / initial_balance * 100, 1),
        "max_dd_pct": round(max_dd * 100, 1),
        "total_trades": len(active_trades),
        "monthly": monthly_list,
        "daily": daily_trades,
    }


# ============================================================================
# Step 5: REPORT
# ============================================================================

def print_report(backtest_result: dict):
    """Print monthly + daily reports."""
    br = backtest_result
    print("=" * 70)
    print(f"PORTFOLIO: ${br['initial_balance']} → ${br['final_balance']}"
          f" (+{br['total_return_pct']}%)")
    print(f"Trades: {br['total_trades']} | Max DD: {br['max_dd_pct']}%")
    print("=" * 70)

    print(f"\n{'Month':<10} {'Trades':>7} {'PnL$':>10} {'Balance$':>10}")
    print("-" * 42)
    for m in br["monthly"]:
        print(f"{m['month']:<10} {m['trades']:>7} {m['pnl']:>+10.2f} ${m['balance']:>9.2f}")

    # Daily last 2 months
    daily = br.get("daily", [])
    if daily:
        all_months = sorted(set(d["date"][:7] for d in daily))
        cutoff = all_months[-2] if len(all_months) >= 2 else all_months[0]
        recent = [d for d in daily if d["date"] >= cutoff]

        print(f"\nDAILY (Last 2 months, {len(recent)} trades)")
        print(f"{'Date':<12} {'Bot':<24} {'Side':<6} {'R':>7} {'PnL$':>9} {'Bal$':>10}")
        print("-" * 72)
        for d in recent[-80:]:
            print(f"{d['date']:<12} {d['bot'][:22]:<24} {d['side']:<6}"
                  f" {d['r_multiple']:>+6.2f}R {d['pnl']:>+9.2f} ${d['balance']:>9.2f}")


# ============================================================================
# Step 6: DEPLOY
# ============================================================================

def generate_configs(verified: List[dict]):
    """Generate config files for verified winners."""
    created = 0
    for v in verified:
        signal_suffix = v["signal"].replace("_", "")
        if v["tf"] == "4h" and "4h" not in signal_suffix:
            signal_suffix += "4h"
        filename = f"config_{v['prefix']}_{signal_suffix}.json"
        filepath = PROJECT_ROOT / filename

        if filepath.exists():
            continue

        cfg = build_config(v["coin"], v["signal"], v["tf"], v["sl"], v["tp"], v["trail"])
        cfg["_comment"] = (f"{v['coin']} {v['signal']} {v['tf'].upper()} — "
                          f"PF {v['pf']}, {v['trades']} trades, WR {v['wr']}%")

        with open(filepath, "w") as f:
            json.dump(cfg, f, indent=2)
        created += 1

    print(f"  Created {created} new config files")
    return created


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def run_full_pipeline(strategies=None, param_grid=None, coins=None,
                      ideate_mode: str = "all"):
    """Run the complete research pipeline end-to-end.

    Flow: IDEATE → SWEEP → VERIFY → AUDIT → BACKTEST → REPORT
    """
    start_time = time.time()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 70)
    print(f"AUTO PIPELINE — {timestamp}")
    print("=" * 70)

    # Step 0a: Discover coins
    if coins is None:
        print("\n[Step 0a] Discovering coins with >= 12 months data...")
        coin_prefixes = discover_coins()
        print(f"  Found {len(coin_prefixes)} coins")
    else:
        coin_prefixes = coins

    # Step 0b: Ideate strategies
    if strategies is None:
        print(f"\n[Step 0b] IDEATE — generating strategy ideas (mode: {ideate_mode})")
        strategies = ideate_strategies(ideate_mode)

    print(f"\n[Step 1] SWEEP — {len(strategies)} strategies × {len(coin_prefixes)} coins")
    sweep_results = sweep_strategies(coin_prefixes, strategies, param_grid)
    print(f"  Total results: {len(sweep_results)}")
    passing = [r for r in sweep_results if r["pf"] >= 1.2]
    print(f"  Passing (PF >= 1.2): {len(passing)}")

    # Step 2: Verify (already engine-tested since we use BacktestEngine in sweep)
    print(f"\n[Step 2] VERIFY — filter PF >= 1.2, trades >= {MIN_TRADES_VERIFY}")
    verified = verify_winners(sweep_results)
    # Deduplicate: best per coin
    best_per_coin = {}
    for v in verified:
        key = v["coin"]
        if key not in best_per_coin or v["pf"] > best_per_coin[key]["pf"]:
            best_per_coin[key] = v
    verified_best = sorted(best_per_coin.values(), key=lambda x: -x["pf"])
    print(f"  Verified winners: {len(verified_best)} unique coins")

    # Step 3: Audit (walk-forward)
    print(f"\n[Step 3] AUDIT — walk-forward test on {len(verified_best)} coins")
    audited = []
    for v in verified_best:
        result = audit_walkforward(v["coin"], v["prefix"], v["signal"],
                                   v["tf"], v["sl"], v["tp"], v["trail"])
        if result.get("verdict") == "PASS":
            audited.append(v)
            print(f"  ✅ {v['coin']} {v['signal']} {v['tf']} — IS PF={result['is_pf']:.2f},"
                  f" OOS PF={result['oos_pf']:.2f} (ratio {result['ratio']:.0%})")
        else:
            print(f"  ❌ {v['coin']} {v['signal']} — {result.get('verdict','FAIL')}")

    print(f"  Audit passed: {len(audited)}/{len(verified_best)}")

    if not audited:
        print("\n❌ No strategies passed audit. Pipeline complete.")
        return

    # Step 4: Backtest
    print(f"\n[Step 4] BACKTEST — $200 shared wallet, {len(audited)} bots")
    backtest_result = run_shared_wallet(audited)

    # Step 5: Report
    print(f"\n[Step 5] REPORT")
    print_report(backtest_result)

    # Save results
    output_path = DATA_DIR / f"pipeline_{timestamp}.json"
    save_data = {
        "timestamp": timestamp,
        "sweep_total": len(sweep_results),
        "verified": len(verified_best),
        "audited": len(audited),
        "audited_bots": [
            {k: v for k, v in a.items() if k != "raw_trades"}
            for a in audited
        ],
        "backtest": backtest_result,
    }
    with open(output_path, "w") as f:
        json.dump(save_data, f, indent=2, default=str)
    print(f"\n  Results saved to {output_path}")

    elapsed = time.time() - start_time
    print(f"\n  Pipeline completed in {elapsed/60:.1f} minutes")

    return save_data


def run_improve_weak():
    """Find better strategies for weak/losing bots."""
    print("=" * 70)
    print("IMPROVE WEAK BOTS — Parameter optimization on losing bots")
    print("=" * 70)

    # Load current portfolio results
    results_path = DATA_DIR / "final_backtest_fixed.json"
    if not results_path.exists():
        print("❌ No final_backtest_fixed.json found. Run full pipeline first.")
        return

    with open(results_path) as f:
        data = json.load(f)

    per_bot = data["portfolio_summary"]["per_bot"]

    # Find losing bots
    weak_coins = set()
    for label, stats in per_bot.items():
        if stats.get("pnl", 0) < 0:
            coin = label.split()[0]
            weak_coins.add(coin)

    print(f"  Found {len(weak_coins)} losing coins: {', '.join(sorted(weak_coins))}")

    # Map to prefixes
    weak_prefixes = []
    for coin in weak_coins:
        prefix = coin.lower() + "usdt"
        if (DATA_DIR / f"{prefix}_1h_2y.csv").exists():
            weak_prefixes.append(prefix)

    print(f"  {len(weak_prefixes)} have data")

    # Run sweep with param grid on weak coins only
    results = run_full_pipeline(
        strategies=TOP_STRATEGIES,
        param_grid=PARAM_GRID,
        coins=weak_prefixes,
    )

    return results


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Automated Research Pipeline — IDEATE → SWEEP → VERIFY → AUDIT → BACKTEST",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline with all strategies (most thorough)
  python3 research/auto_pipeline.py --full

  # Full pipeline with parameter optimization
  python3 research/auto_pipeline.py --full --ideate params

  # Full pipeline with combo strategies only
  python3 research/auto_pipeline.py --full --ideate combos

  # Custom strategies from JSON file
  python3 research/auto_pipeline.py --full --strategies my_ideas.json

  # Optimize weak/losing bots
  python3 research/auto_pipeline.py --improve-weak

  # Test specific coins only
  python3 research/auto_pipeline.py --full --coins btcusdt,ethusdt,avaxusdt

  # Deploy verified winners
  python3 research/auto_pipeline.py --deploy --input data/pipeline_xxx.json

Ideation modes:
  all     — all 30+ signals × 1H + 4H (comprehensive, slow)
  combos  — combo signals only (dualthrust_adx, ichi_adx, etc.)
  params  — top 11 strategies × 10 param sets (optimization)
        """,
    )
    parser.add_argument("--full", action="store_true", help="Run full pipeline")
    parser.add_argument("--ideate", type=str, default="all",
                        choices=["all", "combos", "params", "new_coins"],
                        help="Ideation mode (default: all)")
    parser.add_argument("--sweep-only", action="store_true", help="Sweep only")
    parser.add_argument("--improve-weak", action="store_true", help="Optimize weak bots")
    parser.add_argument("--deploy", action="store_true", help="Generate configs for verified")
    parser.add_argument("--input", type=str, help="Input JSON for verify-only or deploy")
    parser.add_argument("--strategies", type=str, help="Custom strategies JSON file")
    parser.add_argument("--coins", type=str, help="Comma-separated coin prefixes")
    parser.add_argument("--min-pf", type=float, default=1.2, help="Min PF threshold")

    args = parser.parse_args()

    if args.improve_weak:
        run_improve_weak()
    elif args.full or not any([args.sweep_only, args.deploy, args.input]):
        coins = args.coins.split(",") if args.coins else None
        if args.strategies:
            strategies = ideate_from_file(args.strategies)
            run_full_pipeline(strategies=strategies, coins=coins, ideate_mode=args.ideate)
        else:
            param_grid = PARAM_GRID if args.ideate == "params" else None
            run_full_pipeline(coins=coins, ideate_mode=args.ideate, param_grid=param_grid)
    elif args.deploy and args.input:
        with open(args.input) as f:
            data = json.load(f)
        verified = data.get("audited_bots", [])
        generate_configs(verified)
        print(f"Configs generated. Run: docker compose -f docker-compose-multi.yml up -d --build")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
