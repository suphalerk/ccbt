"""Verify combo strategy sweep winners with full BacktestEngine and run
a realistic shared-wallet backtest combining all rounds.

Part 1: Verify combo winners from data/sweep_mega100.json
Part 2: Shared-wallet R-multiple replay across all verified bots
"""

import copy
import json
import logging
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import io
import os

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


class _SuppressStdout:
    """Context manager that swallows stdout (engine prints backtest summary each run)."""

    def __enter__(self) -> "_SuppressStdout":
        self._orig = sys.stdout
        sys.stdout = open(os.devnull, "w")  # noqa: WPS515
        return self

    def __exit__(self, *args: object) -> None:
        sys.stdout.close()
        sys.stdout = self._orig

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"
SWEEP_PATH = DATA_DIR / "sweep_mega100.json"
MEGA_RESULTS_PATH = DATA_DIR / "mega100_results.json"
OUTPUT_PATH = DATA_DIR / "combo_results.json"

# ---------------------------------------------------------------------------
# Combo strategy mapping: sweep name → engine signal key
# ---------------------------------------------------------------------------
COMBO_MAP: dict[str, str] = {
    "Ribbon+RSI+Vol": "ribbon_rsi_vol",
    "DualThrust+ADX": "dualthrust_adx",
    "ZScore+Stoch": "zscore_stoch",
    "ZScore+BB": "zscore_stoch",   # closest available combo signal
    "Ichi+ADX": "ichi_adx",
    "Ribbon+AO": "ribbon_ao",
}

# Ichimoku-based combos → use 1h data; others default to 1h as well
ICHI_SIGNALS = {"ichi_adx"}

# Pass/fail thresholds
MIN_ENGINE_PF = 1.2
MIN_ENGINE_TRADES = 8

# Qualifying criteria for candidates from sweep
MIN_SWEEP_PF = 1.3
MIN_SWEEP_TRADES = 10
MIN_DAYS_DATA = 365

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
with open(PROJECT_ROOT / "config_avax_ichi.json") as _f:
    TEMPLATE: dict = json.load(_f)

# ---------------------------------------------------------------------------
# All known signal keys (for disabling)
# ---------------------------------------------------------------------------
ALL_SIGNALS = [
    "ema_crossover", "ema_fast_crossover", "ema_pullback", "rsi_divergence",
    "bb_breakout", "mean_reversion", "body_dominance", "squeeze_release",
    "ichimoku_cloud", "supertrend", "vol_expansion",
    "dual_supertrend", "alligator", "ema_ichimoku_hybrid", "ichi_supertrend",
    "volexp_supertrend",
    "adx_di_cross", "choppiness_ema", "williams_r_adx", "roc_momentum",
    "stoch_supertrend", "price_channel_vol", "ema_alligator", "supertrend_volume",
    "stoch_mtf", "zscore_meanrev", "ema_ribbon",
    "dual_thrust", "awesome_oscillator", "range_bounce",
    "ribbon_rsi_vol", "dualthrust_adx", "zscore_stoch", "ichi_adx", "ribbon_ao",
]


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------
def build_combo_config(
    coin: str,
    signal_key: str,
    tf: str = "1h",
    sl: float = 2.0,
    tp: float = 4.0,
    trail: float = 3.0,
) -> dict:
    """Build engine config for a combo signal on a given coin.

    Args:
        coin: Coin symbol (e.g. 'AAVE').
        signal_key: Engine signal key (e.g. 'dualthrust_adx').
        tf: Timeframe for both signal and trend.
        sl: ATR SL multiplier.
        tp: ATR TP multiplier.
        trail: ATR trail multiplier.

    Returns:
        Full config dict ready for BacktestEngine.
    """
    cfg = copy.deepcopy(TEMPLATE)
    cfg["symbol"] = f"{coin}USDT"
    cfg["timeframe_signal"] = tf
    cfg["timeframe_trend"] = tf
    cfg["risk_per_trade"] = 0.01
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

    # Disable all signals, enable only the target
    cfg["signals"] = {s: {"enabled": False} for s in ALL_SIGNALS}
    cfg["signals"][signal_key] = {"enabled": True}

    # Ichimoku params for ichi-based combos
    if "ichi" in signal_key:
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52

    return cfg


# ---------------------------------------------------------------------------
# Data resolution
# ---------------------------------------------------------------------------
def resolve_data_paths(prefix: str, tf: str) -> tuple[Optional[str], Optional[str]]:
    """Return (signal_path, trend_path) for the given prefix and timeframe."""
    candidates = sorted(DATA_DIR.glob(f"{prefix}_{tf}*.csv"))
    path = str(candidates[0]) if candidates else None
    return path, path  # signal == trend for 1h-only strategies


# ---------------------------------------------------------------------------
# Engine runner
# ---------------------------------------------------------------------------
def run_engine(
    config: dict, signal_path: str, trend_path: str
) -> Optional[dict]:
    """Run BacktestEngine and return metrics dict, or None on error.

    Accesses engine.state directly for individual trade data and final balance,
    since BacktestMetrics only holds aggregate stats.
    """
    try:
        signal_data = load_ohlcv(signal_path)
        trend_data = load_ohlcv(trend_path)
        engine = BacktestEngine(config, initial_balance=10000.0)
        with _SuppressStdout():
            metrics = engine.run(signal_data, trend_data)

        # Access per-trade data from engine state (BacktestTrade dataclasses)
        trades_list = []
        for t in engine.state.trades:
            trades_list.append(
                {
                    "entry_time": str(t.entry_time),
                    "exit_time": str(t.exit_time),
                    "side": t.side,
                    "pnl": t.pnl,
                    "risk_amount": t.risk_amount,
                }
            )

        final_balance = engine.state.balance
        initial_balance = engine.state.initial_balance

        return {
            "trades": metrics.total_trades,
            "pf": round(metrics.profit_factor, 3),
            "wr": round(metrics.win_rate * 100, 1),
            "dd": round(metrics.max_drawdown * 100, 1),
            "sharpe": round(metrics.sharpe_ratio, 2),
            "final_balance": round(final_balance, 2),
            "initial_balance": round(initial_balance, 2),
            "trades_list": trades_list,
        }
    except Exception as exc:  # noqa: BLE001
        print(f"    ERROR: {exc}")
        return None


# ---------------------------------------------------------------------------
# Part 1: Combo verification
# ---------------------------------------------------------------------------
def part1_verify_combos(sweep_data: dict) -> list[dict]:
    """Verify top combo-strategy winners against the full BacktestEngine.

    Args:
        sweep_data: Parsed sweep_mega100.json.

    Returns:
        List of verification result dicts.
    """
    print("\n" + "=" * 72)
    print("PART 1: COMBO STRATEGY VERIFICATION")
    print("=" * 72)

    all_winners: list[dict] = sweep_data.get("winners", [])

    # Filter qualifying combo candidates
    candidates = [
        w
        for w in all_winners
        if w["strategy"] in COMBO_MAP
        and w["pf"] >= MIN_SWEEP_PF
        and w["trades"] >= MIN_SWEEP_TRADES
        and w["days_data"] >= MIN_DAYS_DATA
    ]

    print(f"Qualifying combo candidates: {len(candidates)}")
    print(
        f"  (PF >= {MIN_SWEEP_PF}, trades >= {MIN_SWEEP_TRADES}, "
        f"days_data >= {MIN_DAYS_DATA})\n"
    )

    # Table header
    header = (
        f"{'Coin':<10} {'Combo Signal':<20} {'TF':<4} "
        f"{'Sweep_PF':<9} {'Engine_PF':<10} {'Trades':<7} {'WR%':<6} {'Status'}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)

    results: list[dict] = []
    passed = 0
    failed = 0
    skipped = 0

    for w in candidates:
        coin: str = w["coin"]
        prefix: str = w["prefix"]
        strategy: str = w["strategy"]
        signal_key: str = COMBO_MAP[strategy]
        sweep_pf: float = w["pf"]
        sweep_trades: int = w["trades"]

        tf = "1h"  # all combo signals currently run on 1h
        sl, tp, trail = 2.0, 4.0, 3.0

        signal_path, trend_path = resolve_data_paths(prefix, tf)
        if not signal_path:
            print(
                f"{'  ' + coin:<10} {strategy:<20} {tf:<4} {sweep_pf:<9.3f} "
                f"{'N/A':<10} {'N/A':<7} {'N/A':<6} SKIP (no data)"
            )
            skipped += 1
            continue

        cfg = build_combo_config(coin, signal_key, tf, sl, tp, trail)
        result = run_engine(cfg, signal_path, trend_path)

        if result is None:
            print(
                f"{'  ' + coin:<10} {strategy:<20} {tf:<4} {sweep_pf:<9.3f} "
                f"{'ERROR':<10} {'N/A':<7} {'N/A':<6} FAIL"
            )
            failed += 1
            status = "FAIL"
            engine_pf = 0.0
            engine_trades = 0
            engine_wr = 0.0
        else:
            engine_pf = result["pf"]
            engine_trades = result["trades"]
            engine_wr = result["wr"]
            pass_criteria = (
                engine_pf >= MIN_ENGINE_PF and engine_trades >= MIN_ENGINE_TRADES
            )
            status = "PASS" if pass_criteria else "FAIL"
            if pass_criteria:
                passed += 1
            else:
                failed += 1

            print(
                f"{'  ' + coin:<10} {strategy:<20} {tf:<4} {sweep_pf:<9.3f} "
                f"{engine_pf:<10.3f} {engine_trades:<7} {engine_wr:<6.1f} {status}"
            )

        entry: dict = {
            "coin": coin,
            "prefix": prefix,
            "strategy": strategy,
            "signal_key": signal_key,
            "tf": tf,
            "sweep_pf": sweep_pf,
            "sweep_trades": sweep_trades,
            "sweep_days": w["days_data"],
            "engine_pf": engine_pf,
            "wr": result["wr"] if result else 0.0,
            "trades": result["trades"] if result else 0,
            "sharpe": result["sharpe"] if result else 0.0,
            "dd": result["dd"] if result else 0.0,
            "atr_sl_mult": sl,
            "atr_tp_mult": tp,
            "atr_trail_mult": trail,
            "status": status,
            "trades_list": result["trades_list"] if result else [],
            "final_balance": result["final_balance"] if result else 10000.0,
            "initial_balance": result["initial_balance"] if result else 10000.0,
        }
        results.append(entry)

    print(sep)
    print(f"\nResults: {passed} PASS  |  {failed} FAIL  |  {skipped} SKIP")
    return results


# ---------------------------------------------------------------------------
# Part 2: Shared-wallet R-multiple replay
# ---------------------------------------------------------------------------
def _extract_existing_bot_trades(
    mega_results: dict, passed_combo: list[dict]
) -> list[dict]:
    """Build a unified trade list from existing portfolio bots + new combo bots.

    For existing bots in mega100_results['passed'], we re-run the engine to
    get individual trades with timestamps.  For combo bots we already have
    trades_list from Part 1.

    Returns list of dicts: {bot_name, exit_time, side, r_multiple}
    """
    all_trades: list[dict] = []

    # -- Existing verified bots: re-run engine to get trade-level data ------
    existing_passed: list[dict] = mega_results.get("passed", [])
    print(f"\nRe-running {len(existing_passed)} existing bots to extract trade timings...")

    for bot in existing_passed:
        coin = bot["coin"]
        prefix = bot["prefix"]
        signal_key = bot["signal_key"]
        tf = bot.get("tf", "1h")
        sl = bot.get("atr_sl_mult", 2.0)
        tp = bot.get("atr_tp_mult", 4.0)
        trail = bot.get("atr_trail_mult", 3.0)
        strategy = bot.get("strategy", signal_key)

        signal_path, trend_path = resolve_data_paths(prefix, tf)
        if not signal_path:
            print(f"  SKIP {coin} — no data")
            continue

        cfg = build_combo_config(coin, signal_key, tf, sl, tp, trail)

        # For ichimoku-based original bots, ensure ichimoku_cloud is enabled
        if signal_key == "ichimoku_cloud":
            cfg["signals"] = {s: {"enabled": False} for s in ALL_SIGNALS}
            cfg["signals"]["ichimoku_cloud"] = {"enabled": True}
            cfg["ichimoku_tenkan"] = 9
            cfg["ichimoku_kijun"] = 26
            cfg["ichimoku_senkou_b"] = 52

        result = run_engine(cfg, signal_path, trend_path)
        if not result or not result["trades_list"]:
            print(f"  SKIP {coin} ({strategy}) — engine returned no trades")
            continue

        bot_label = f"{coin} ({strategy})"
        print(
            f"  {bot_label:<35} trades={result['trades']:<4} PF={result['pf']:.2f}"
        )

        # Compute R-multiples
        engine_balance = 10000.0
        for t in result["trades_list"]:
            engine_risk = engine_balance * 0.01
            if engine_risk <= 0:
                continue
            r_mult = t["pnl"] / engine_risk if engine_risk > 0 else 0.0
            engine_balance += t["pnl"]
            all_trades.append(
                {
                    "bot": bot_label,
                    "exit_time": t["exit_time"],
                    "entry_time": t["entry_time"],
                    "side": t["side"],
                    "r_multiple": r_mult,
                }
            )

    # -- New combo bots -------------------------------------------------------
    combo_passed = [r for r in passed_combo if r["status"] == "PASS"]
    print(f"\nAdding {len(combo_passed)} new combo bots...")

    for bot in combo_passed:
        coin = bot["coin"]
        signal_key = bot["signal_key"]
        strategy = bot["strategy"]
        bot_label = f"{coin} ({strategy})"

        engine_balance = 10000.0
        for t in bot.get("trades_list", []):
            engine_risk = engine_balance * 0.01
            if engine_risk <= 0:
                continue
            r_mult = t["pnl"] / engine_risk if engine_risk > 0 else 0.0
            engine_balance += t["pnl"]
            all_trades.append(
                {
                    "bot": bot_label,
                    "exit_time": t["exit_time"],
                    "entry_time": t["entry_time"],
                    "side": t["side"],
                    "r_multiple": r_mult,
                }
            )

    return all_trades


def _shared_wallet_replay(
    all_trades: list[dict],
    initial_balance: float = 200.0,
    risk_per_trade: float = 0.01,
    max_concurrent: int = 5,
) -> tuple[float, list[dict], list[dict]]:
    """Replay all trades through a shared wallet with concurrency limit.

    Trades are processed in exit_time order.  When max_concurrent positions
    are already open, new trades that would overlap are skipped.

    Args:
        all_trades: Trade list with bot/entry_time/exit_time/side/r_multiple.
        initial_balance: Starting shared wallet balance.
        risk_per_trade: Fraction of balance risked per trade.
        max_concurrent: Maximum simultaneous open positions.

    Returns:
        (final_balance, monthly_data, daily_data)
    """
    # Parse times and sort by exit_time — strip timezone to get tz-naive UTC
    def _parse_ts(s: str) -> Optional[pd.Timestamp]:
        if not s:
            return None
        try:
            ts = pd.Timestamp(s)
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            return ts
        except Exception:
            return None

    valid_trades = []
    for t in all_trades:
        entry_dt = _parse_ts(t.get("entry_time", ""))
        exit_dt = _parse_ts(t.get("exit_time", ""))
        if entry_dt is None or exit_dt is None:
            continue
        valid_trades.append(
            {
                **t,
                "entry_dt": entry_dt,
                "exit_dt": exit_dt,
            }
        )

    valid_trades.sort(key=lambda x: x["exit_dt"])

    balance = initial_balance
    # Track open intervals: list of (entry_dt, exit_dt)
    open_intervals: list[tuple] = []

    daily_records: list[dict] = []
    # month -> {trades, pnl, wins}
    monthly_agg: dict[str, dict] = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})

    for t in valid_trades:
        entry_dt = t["entry_dt"]
        exit_dt = t["exit_dt"]

        # Prune closed intervals
        open_intervals = [
            (a, b) for (a, b) in open_intervals if b > entry_dt
        ]

        if len(open_intervals) >= max_concurrent:
            continue  # skip — too many concurrent positions

        # Execute trade
        shared_risk = balance * risk_per_trade
        dollar_pnl = shared_risk * t["r_multiple"]
        balance += dollar_pnl

        open_intervals.append((entry_dt, exit_dt))

        date_str = exit_dt.strftime("%Y-%m-%d")
        month_str = exit_dt.strftime("%Y-%m")
        is_win = dollar_pnl > 0

        daily_records.append(
            {
                "date": date_str,
                "bot": t["bot"],
                "side": t.get("side", "?"),
                "r_mult": round(t["r_multiple"], 3),
                "pnl": round(dollar_pnl, 2),
                "balance": round(balance, 2),
            }
        )

        monthly_agg[month_str]["trades"] += 1
        monthly_agg[month_str]["pnl"] += dollar_pnl
        monthly_agg[month_str]["wins"] += int(is_win)

    # Build monthly report (sorted)
    monthly_data: list[dict] = []
    running_balance = initial_balance
    for month in sorted(monthly_agg.keys()):
        agg = monthly_agg[month]
        running_balance += agg["pnl"]
        monthly_data.append(
            {
                "month": month,
                "trades": agg["trades"],
                "wins": agg["wins"],
                "pnl": round(agg["pnl"], 2),
                "balance": round(running_balance, 2),
            }
        )

    return balance, monthly_data, daily_records


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_combo_table(results: list[dict]) -> None:
    """Print the combo verification table (pass/fail only)."""
    passed = [r for r in results if r["status"] == "PASS"]
    print(f"\n{'=' * 72}")
    print("COMBO VERIFICATION SUMMARY — PASSED")
    print(f"{'=' * 72}")
    header = (
        f"{'Coin':<10} {'Combo Signal':<20} {'TF':<4} "
        f"{'Sweep_PF':<9} {'Engine_PF':<10} {'Trades':<7} {'WR%':<6} DD%"
    )
    print(header)
    print("-" * len(header))
    for r in sorted(passed, key=lambda x: -x["engine_pf"]):
        print(
            f"  {r['coin']:<8} {r['strategy']:<20} {r['tf']:<4} "
            f"{r['sweep_pf']:<9.2f} {r['engine_pf']:<10.2f} "
            f"{r['trades']:<7} {r['wr']:<6.1f} {r['dd']:.1f}"
        )
    print(f"\nTotal passed: {len(passed)}")


def print_monthly_report(
    monthly_data: list[dict], limit_months: int = 12
) -> None:
    """Print last N months of the portfolio monthly report."""
    print(f"\n{'=' * 60}")
    print("MONTHLY REPORT (last 12 months)")
    print(f"{'=' * 60}")
    header = f"{'Month':<10} {'Trades':<8} {'Wins':<6} {'PnL$':>8}  {'Balance$':>10}"
    print(header)
    print("-" * len(header))
    for row in monthly_data[-limit_months:]:
        print(
            f"  {row['month']:<8} {row['trades']:<8} "
            f"{row['wins']:<6} {row['pnl']:>8.2f}  {row['balance']:>10.2f}"
        )


def print_daily_report(daily_data: list[dict], limit_days: int = 60) -> None:
    """Print last 60 days of individual trades."""
    print(f"\n{'=' * 72}")
    print("DAILY TRADE REPORT (last 2 months)")
    print(f"{'=' * 72}")
    header = (
        f"{'Date':<12} {'Bot':<35} {'Side':<6} "
        f"{'R_mult':>7}  {'PnL$':>7}  {'Balance$':>10}"
    )
    print(header)
    print("-" * len(header))
    # Get last 60 calendar days by date field
    if daily_data:
        cutoff = pd.Timestamp(daily_data[-1]["date"]) - pd.Timedelta(days=limit_days)
        recent = [d for d in daily_data if pd.Timestamp(d["date"]) >= cutoff]
        for row in recent[-200:]:  # cap at 200 rows for readability
            print(
                f"  {row['date']:<10} {row['bot']:<35} {row['side']:<6} "
                f"{row['r_mult']:>7.3f}  {row['pnl']:>7.2f}  {row['balance']:>10.2f}"
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # Load inputs
    if not SWEEP_PATH.exists():
        print(f"ERROR: {SWEEP_PATH} not found.")
        sys.exit(1)
    if not MEGA_RESULTS_PATH.exists():
        print(f"ERROR: {MEGA_RESULTS_PATH} not found.")
        sys.exit(1)

    with open(SWEEP_PATH) as f:
        sweep_data: dict = json.load(f)
    with open(MEGA_RESULTS_PATH) as f:
        mega_results: dict = json.load(f)

    # -------------------------------------------------------------------------
    # Part 1: Verify combo winners
    # -------------------------------------------------------------------------
    combo_results = part1_verify_combos(sweep_data)
    print_combo_table(combo_results)

    # -------------------------------------------------------------------------
    # Part 2: Shared wallet replay
    # -------------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("PART 2: REALISTIC SHARED-WALLET BACKTEST ($200 starting balance)")
    print("=" * 72)

    all_trades = _extract_existing_bot_trades(mega_results, combo_results)

    if not all_trades:
        print("No trades extracted — skipping shared wallet replay.")
        final_balance = 200.0
        monthly_data: list[dict] = []
        daily_data: list[dict] = []
    else:
        print(f"\nTotal trades for replay: {len(all_trades)}")
        final_balance, monthly_data, daily_data = _shared_wallet_replay(
            all_trades,
            initial_balance=200.0,
            risk_per_trade=0.01,
            max_concurrent=5,
        )
        print(f"\nFinal balance:  ${final_balance:,.2f}")
        print(f"Total return:   {(final_balance / 200 - 1) * 100:+.1f}%")

        print_monthly_report(monthly_data)
        print_daily_report(daily_data)

    # -------------------------------------------------------------------------
    # Save all data
    # -------------------------------------------------------------------------
    # Prepare serializable combo results (strip trades_list for storage efficiency
    # — keep only summary fields)
    combo_summary = []
    for r in combo_results:
        entry = {k: v for k, v in r.items() if k != "trades_list"}
        combo_summary.append(entry)

    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "combo_pass_threshold": {"min_engine_pf": MIN_ENGINE_PF, "min_trades": MIN_ENGINE_TRADES},
            "shared_wallet": {
                "initial_balance": 200.0,
                "final_balance": round(final_balance, 2),
                "total_return_pct": round((final_balance / 200 - 1) * 100, 2),
                "max_concurrent": 5,
            },
        },
        "verification": combo_summary,
        "passed": [r for r in combo_summary if r["status"] == "PASS"],
        "portfolio_summary": {
            "final_balance": round(final_balance, 2),
            "total_return_pct": round((final_balance / 200 - 1) * 100, 2),
            "total_trades": len(daily_data),
            "monthly": monthly_data,
            "daily": daily_data[-200:],  # store last 200 daily trades
        },
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nAll data saved to {OUTPUT_PATH}")
    print(f"\nPASSED combo bots: {len(output['passed'])}")
    print(f"Final portfolio balance: ${final_balance:,.2f} (+{(final_balance/200-1)*100:.1f}%)")


if __name__ == "__main__":
    main()
