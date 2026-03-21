"""Regime-Adaptive Exit: baseline vs enabled comparison across all 29 portfolio bots.

Runs every bot in PORTFOLIO twice:
  1. regime_adaptive_exit=False  (baseline — must match normal run exactly)
  2. regime_adaptive_exit=True   (default regime params from task spec)

Reports per-bot PF delta, portfolio PF, DD, and regime distribution per bot.

Usage:
    python research/test_regime_exit.py
"""

import json
import logging
import os
import sys
from collections import Counter
from typing import Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.ERROR)
sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine
from backtest.metrics import BacktestMetrics
from research.portfolio_report_engine import INITIAL_BALANCE, PORTFOLIO

REPO_ROOT = "/Users/iceai/Work/ccbt"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _run_bot_with_flag(
    bot_def: dict,
    regime_adaptive_exit: bool,
) -> tuple[Optional[BacktestMetrics], list, str]:
    """Run one bot with regime_adaptive_exit toggled.

    Args:
        bot_def: Portfolio entry dict.
        regime_adaptive_exit: Whether to enable regime-adaptive exit.

    Returns:
        Tuple of (BacktestMetrics or None, list[BacktestTrade], error_str).
        On error, BacktestMetrics is None and error_str is non-empty.
    """
    config_path = os.path.join(REPO_ROOT, bot_def["config"])
    if not os.path.exists(config_path):
        return None, [], f"Config not found: {config_path}"

    signal_path = os.path.join(REPO_ROOT, bot_def["signal_data"])
    if not os.path.exists(signal_path):
        return None, [], f"Signal data not found: {signal_path}"

    with open(config_path) as f:
        config = json.load(f)

    # Inject the flag.  False is the neutral default so it never changes behaviour
    # relative to normal execution when testing the baseline.
    config["regime_adaptive_exit"] = regime_adaptive_exit

    engine = BacktestEngine(config, initial_balance=INITIAL_BALANCE)
    signal_data = load_ohlcv(signal_path)

    if bot_def.get("resample_4h"):
        signal_data = (
            signal_data.resample("4h")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
        )

    trend_data: Optional[pd.DataFrame] = None
    if bot_def.get("trend_data") is not None:
        trend_path = os.path.join(REPO_ROOT, bot_def["trend_data"])
        if not os.path.exists(trend_path):
            return None, [], f"Trend data not found: {trend_path}"
        trend_data = load_ohlcv(trend_path)

    metrics = engine.run(signal_data, trend_data)
    return metrics, engine.state.trades, ""


def _regime_distribution(trades: list) -> str:
    """Summarise entry-regime distribution as a compact string.

    Args:
        trades: List of BacktestTrade objects.

    Returns:
        Formatted string like '68% trending, 22% ranging, 10% volatile'.
    """
    if not trades:
        return "no trades"
    counts: Counter = Counter()
    for t in trades:
        regime = getattr(t, "entry_regime", "") or "unknown"
        counts[regime] += 1
    total = sum(counts.values())
    parts = []
    for label in ("trending", "ranging", "volatile", "unknown"):
        n = counts.get(label, 0)
        if n > 0:
            parts.append(f"{round(n / total * 100)}% {label}")
    return ", ".join(parts) if parts else "no trades"


def _portfolio_pf(all_pnls: list[float]) -> float:
    """Compute overall portfolio profit factor from a flat list of trade PnLs.

    Args:
        all_pnls: All trade PnLs across the portfolio.

    Returns:
        Portfolio-level profit factor.
    """
    wins = [p for p in all_pnls if p > 0]
    losses = [abs(p) for p in all_pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    return gross_profit / gross_loss if gross_loss > 0 else float("inf")


def _portfolio_dd(trade_pnls_sorted_by_exit: list[float]) -> float:
    """Compute max drawdown fraction from a combined equity curve.

    Args:
        trade_pnls_sorted_by_exit: PnLs in exit-time order across all bots.

    Returns:
        Max drawdown as a fraction (e.g. 0.12 = 12%).
    """
    if not trade_pnls_sorted_by_exit:
        return 0.0
    pnls = np.array(trade_pnls_sorted_by_exit)
    equity = INITIAL_BALANCE + np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    # Avoid division by zero
    safe_peak = np.where(peak > 0, peak, 1.0)
    dd_arr = (peak - equity) / safe_peak
    return float(np.max(dd_arr))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run baseline and regime-adaptive exit tests across all portfolio bots."""
    print("=" * 110)
    print("REGIME-ADAPTIVE EXIT TEST — Baseline vs Regime-Adaptive")
    print(f"Bots: {len(PORTFOLIO)}  |  Starting balance: ${INITIAL_BALANCE:,.0f} per bot")
    print("=" * 110)
    print()

    # Collect per-bot results: {name: {base_metrics, base_trades, reg_metrics, reg_trades}}
    bot_results: dict[str, dict] = {}
    skipped: list[str] = []

    for bot in PORTFOLIO:
        name = bot["name"]
        print(f"  {name:<14}", end=" ", flush=True)

        # Baseline (regime_adaptive_exit=False)
        base_metrics, base_trades, base_err = _run_bot_with_flag(bot, regime_adaptive_exit=False)
        if base_err:
            print(f"SKIP  ({base_err})")
            skipped.append(name)
            continue

        # Regime-adaptive (regime_adaptive_exit=True)
        reg_metrics, reg_trades, reg_err = _run_bot_with_flag(bot, regime_adaptive_exit=True)
        if reg_err:
            print(f"SKIP  ({reg_err})")
            skipped.append(name)
            continue

        delta_pf = reg_metrics.profit_factor - base_metrics.profit_factor
        print(
            f"OK  base_PF={base_metrics.profit_factor:.2f}  "
            f"reg_PF={reg_metrics.profit_factor:.2f}  "
            f"delta={delta_pf:+.2f}"
        )

        bot_results[name] = {
            "bot_def": bot,
            "base_metrics": base_metrics,
            "base_trades": base_trades,
            "reg_metrics": reg_metrics,
            "reg_trades": reg_trades,
        }

    print()

    if not bot_results:
        print("No bots ran successfully. Nothing to compare.")
        return

    # -----------------------------------------------------------------------
    # Per-bot summary table
    # -----------------------------------------------------------------------

    print("=" * 110)
    print(
        f"{'Bot':<14} {'Base_PF':>8} {'Reg_PF':>8} {'Delta':>8} "
        f"{'Base_DD%':>9} {'Reg_DD%':>8}  Regime Distribution (regime run)"
    )
    print("-" * 110)

    # Collect trades across portfolio for portfolio-level metrics.
    # Sort by exit_time to build a combined equity curve.
    base_all_trades: list[tuple] = []  # (exit_time, pnl)
    reg_all_trades: list[tuple] = []

    for bot in PORTFOLIO:
        name = bot["name"]
        if name not in bot_results:
            continue
        r = bot_results[name]

        base_pf = r["base_metrics"].profit_factor
        reg_pf = r["reg_metrics"].profit_factor
        base_dd = r["base_metrics"].max_drawdown
        reg_dd = r["reg_metrics"].max_drawdown
        delta = reg_pf - base_pf
        regime_dist = _regime_distribution(r["reg_trades"])

        print(
            f"{name:<14} {base_pf:>8.2f} {reg_pf:>8.2f} {delta:>+8.2f} "
            f"{base_dd * 100:>8.1f}% {reg_dd * 100:>7.1f}%  {regime_dist}"
        )

        for t in r["base_trades"]:
            base_all_trades.append((t.exit_time, t.pnl))
        for t in r["reg_trades"]:
            reg_all_trades.append((t.exit_time, t.pnl))

    print()

    # -----------------------------------------------------------------------
    # Portfolio-level metrics
    # -----------------------------------------------------------------------

    base_all_trades.sort(key=lambda x: x[0])
    reg_all_trades.sort(key=lambda x: x[0])

    base_pnls = [p for _, p in base_all_trades]
    reg_pnls = [p for _, p in reg_all_trades]

    port_base_pf = _portfolio_pf(base_pnls)
    port_reg_pf = _portfolio_pf(reg_pnls)
    port_delta = port_reg_pf - port_base_pf

    port_base_dd = _portfolio_dd(base_pnls)
    port_reg_dd = _portfolio_dd(reg_pnls)

    print("-" * 110)
    print(
        f"{'PORTFOLIO':<14} {port_base_pf:>8.2f} {port_reg_pf:>8.2f} {port_delta:>+8.2f} "
        f"{port_base_dd * 100:>8.1f}% {port_reg_dd * 100:>7.1f}%"
    )
    print()

    # -----------------------------------------------------------------------
    # Verdict
    # -----------------------------------------------------------------------

    improved = port_reg_pf > port_base_pf
    dd_ok = port_reg_dd <= port_base_dd * 1.10  # Allow up to +10% relative DD increase

    print("VERDICT")
    print("-" * 60)
    print(f"  Bots tested:   {len(bot_results)} / {len(PORTFOLIO)}")
    print(f"  Portfolio PF:  {port_base_pf:.2f} -> {port_reg_pf:.2f}  ({port_delta:+.2f})")
    print(f"  Portfolio DD:  {port_base_dd * 100:.1f}% -> {port_reg_dd * 100:.1f}%")
    print()
    if improved and dd_ok:
        print("  ACCEPT: regime-adaptive exit improves portfolio PF without material DD increase.")
    elif improved and not dd_ok:
        print("  MIXED: PF improves but DD worsens >10% — review carefully.")
    else:
        print("  REJECT: portfolio PF did not improve with regime-adaptive exit.")

    if skipped:
        print()
        print(f"  Skipped (missing data): {', '.join(skipped)}")

    print()


if __name__ == "__main__":
    main()
