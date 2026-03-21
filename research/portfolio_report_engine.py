"""Portfolio report engine: runs all 19 bots through BacktestEngine and generates
monthly + daily PnL reports.

Usage:
    python research/portfolio_report_engine.py

Reports:
    Part 1 - Per-bot summary (trades, WR%, PF, Sharpe, DD%, total PnL)
    Part 2 - Monthly PnL table (all months that have trades)
    Part 3 - Daily PnL table (last 2 months only)
"""

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

# Suppress noisy logging from bot modules
logging.basicConfig(level=logging.ERROR)

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

REPO_ROOT = "/Users/iceai/Work/ccbt"

# Engine uses this as starting capital for all bots.
INITIAL_BALANCE = 200.0

# Column abbreviations used in the wide monthly/daily tables.
# Order matches PORTFOLIO list below.
BOT_ABBREVS = [
    "BTC", "DOGE", "ARB", "WIF", "AVAX", "NEAR", "SOL",
    "GUN", "BERA", "ATH", "ZETA", "ARC", "ANI", "TRMP",
    "INJ", "XLM", "SHIB", "TRX", "ARC2",
    "TAO4H", "RDR4H", "HBR4H",
]

PORTFOLIO = [
    # --- EMA Crossover 15m ---
    {
        "name": "BTC",
        "config": "config.json",
        "signal_data": "data/btcusdt_15m_2y.csv",
        "trend_data": "data/btcusdt_1h_2y.csv",
    },
    {
        "name": "DOGE",
        "config": "config_doge.json",
        "signal_data": "data/dogeusdt_15m_2y.csv",
        "trend_data": "data/dogeusdt_1h_2y.csv",
    },
    {
        "name": "ARB",
        "config": "config_arb.json",
        "signal_data": "data/arbusdt_15m_2y.csv",
        "trend_data": "data/arbusdt_1h_2y.csv",
    },
    {
        "name": "WIF",
        "config": "config_wif.json",
        "signal_data": "data/wifusdt_15m_2y.csv",
        "trend_data": "data/wifusdt_1h_2y.csv",
    },
    # --- Ichimoku Cloud 1H (trend_data=None: cloud uses same 1H data) ---
    {
        "name": "AVAX",
        "config": "config_avax_ichi.json",
        "signal_data": "data/avaxusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "NEAR",
        "config": "config_near_ichi.json",
        "signal_data": "data/nearusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "SOL",
        "config": "config_sol_ichi.json",
        "signal_data": "data/solusdt_1h_5y.csv",
        "trend_data": None,
    },
    # --- New Ichimoku bots ---
    {
        "name": "GUN",
        "config": "config_gunusdt_ichi.json",
        "signal_data": "data/gunusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "BERA",
        "config": "config_berausdt_ichi.json",
        "signal_data": "data/berausdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "ATH",
        "config": "config_athusdt_ichi.json",
        "signal_data": "data/athusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "ZETA",
        "config": "config_zetausdt_ichi.json",
        "signal_data": "data/zetausdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "ARC",
        "config": "config_arcusdt_ichi.json",
        "signal_data": "data/arcusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "ANIME",
        "config": "config_animeusdt_ichi.json",
        "signal_data": "data/animeusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "TRUMP",
        "config": "config_trumpusdt_ichi.json",
        "signal_data": "data/trumpusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "INJ",
        "config": "config_injusdt_ichi.json",
        "signal_data": "data/injusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "XLM",
        "config": "config_xlmusdt_ichi.json",
        "signal_data": "data/xlmusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "1000SHIB",
        "config": "config_1000shibusdt_ichi.json",
        "signal_data": "data/1000shibusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "TRX",
        "config": "config_trxusdt_ichi.json",
        "signal_data": "data/trxusdt_1h_2y.csv",
        "trend_data": None,
    },
    {
        "name": "ARC_EMA",
        "config": "config_arcusdt_ema.json",
        "signal_data": "data/arcusdt_15m_2y.csv",
        "trend_data": "data/arcusdt_1h_2y.csv",
    },
    # --- Ichimoku Cloud 4H (resample 1H data to 4H before engine) ---
    {
        "name": "TAO_4H",
        "config": "config_taousdt_ichi4h.json",
        "signal_data": "data/taousdt_1h_2y.csv",
        "trend_data": None,
        "resample_4h": True,
    },
    {
        "name": "RENDER_4H",
        "config": "config_renderusdt_ichi4h.json",
        "signal_data": "data/renderusdt_1h_2y.csv",
        "trend_data": None,
        "resample_4h": True,
    },
    {
        "name": "HBAR_4H",
        "config": "config_hbarusdt_ichi4h.json",
        "signal_data": "data/hbarusdt_1h_2y.csv",
        "trend_data": None,
        "resample_4h": True,
    },
]

# Map full bot names to short column abbreviations (same order as PORTFOLIO).
_NAME_TO_ABBREV: dict[str, str] = {
    bot["name"]: abbrev for bot, abbrev in zip(PORTFOLIO, BOT_ABBREVS)
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_pnl(val: float) -> str:
    """Format a dollar PnL with sign, no decimals."""
    if val >= 0:
        return f"+${int(round(val))}"
    return f"-${int(round(abs(val)))}"


def _fmt_pct(val: float) -> str:
    """Format a percentage (already in 0-100 scale) with one decimal."""
    return f"{val:.1f}%"


def _fmt_col(val: float, width: int = 6) -> str:
    """Format a dollar PnL value for a fixed-width table column."""
    s = _fmt_pnl(val)
    return s.rjust(width)


# ---------------------------------------------------------------------------
# Bot runner
# ---------------------------------------------------------------------------


def run_bot(bot_def: dict) -> tuple[list[dict], object]:
    """Run one bot through BacktestEngine.

    Args:
        bot_def: Portfolio entry dict with name/config/signal_data/trend_data.

    Returns:
        Tuple of (trade_dicts, BacktestMetrics).  trade_dicts have keys:
        bot, entry_time, exit_time, side, pnl, close_reason.

    Raises:
        FileNotFoundError: If config or data files are missing.
        Exception: Any engine error propagates to caller.
    """
    config_path = os.path.join(REPO_ROOT, bot_def["config"])
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")

    signal_path = os.path.join(REPO_ROOT, bot_def["signal_data"])
    if not os.path.exists(signal_path):
        raise FileNotFoundError(f"Signal data not found: {signal_path}")

    with open(config_path) as f:
        config = json.load(f)

    engine = BacktestEngine(config, initial_balance=INITIAL_BALANCE)
    signal_data = load_ohlcv(signal_path)

    # Resample 1H data to 4H for 4H Ichimoku bots
    if bot_def.get("resample_4h"):
        signal_data = (
            signal_data.resample("4h")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
        )

    trend_data: Optional[pd.DataFrame] = None
    if bot_def["trend_data"] is not None:
        trend_path = os.path.join(REPO_ROOT, bot_def["trend_data"])
        if not os.path.exists(trend_path):
            raise FileNotFoundError(f"Trend data not found: {trend_path}")
        trend_data = load_ohlcv(trend_path)

    metrics = engine.run(signal_data, trend_data)

    trades: list[dict] = []
    for t in engine.state.trades:
        trades.append(
            {
                "bot": bot_def["name"],
                "entry_time": pd.Timestamp(t.entry_time),
                "exit_time": pd.Timestamp(t.exit_time),
                "side": t.side,
                "pnl": t.pnl,
                "close_reason": t.close_reason,
            }
        )

    return trades, metrics


# ---------------------------------------------------------------------------
# Report printers
# ---------------------------------------------------------------------------


def print_part1_summary(
    bot_metrics: dict[str, dict],
    all_trades_df: pd.DataFrame,
) -> None:
    """Print Part 1: per-bot summary table."""
    print()
    print("=" * 100)
    print("PART 1: PER-BOT SUMMARY")
    print("=" * 100)

    header = (
        f"{'Bot':<12} {'Config':<32} {'Trades':>7} {'WR%':>6} "
        f"{'PF':>6} {'Sharpe':>7} {'DD%':>6} {'Total PnL':>12}"
    )
    print(header)
    print("-" * 100)

    total_trades = 0
    total_pnl = 0.0
    all_pnls: list[float] = []

    for bot in PORTFOLIO:
        name = bot["name"]
        entry = bot_metrics.get(name)
        if entry is None:
            # Bot was skipped due to error
            print(
                f"{name:<12} {'(skipped)':<32} {'N/A':>7} {'N/A':>6} "
                f"{'N/A':>6} {'N/A':>7} {'N/A':>6} {'N/A':>12}"
            )
            continue

        m = entry["metrics"]
        trades = entry["trades"]

        n = m.total_trades
        wr = m.win_rate * 100.0
        pf = m.profit_factor
        sharpe = m.sharpe_ratio
        dd = m.max_drawdown * 100.0
        bot_pnl = sum(t["pnl"] for t in trades)

        total_trades += n
        total_pnl += bot_pnl
        all_pnls.extend(t["pnl"] for t in trades)

        config_short = bot["config"]
        pnl_str = _fmt_pnl(bot_pnl)
        print(
            f"{name:<12} {config_short:<32} {n:>7} {wr:>5.1f}% "
            f"{pf:>6.2f} {sharpe:>7.2f} {dd:>5.1f}% {pnl_str:>12}"
        )

    # Portfolio totals row
    print("-" * 100)
    if all_pnls:
        wins = [p for p in all_pnls if p > 0]
        losses = [p for p in all_pnls if p < 0]
        total_wr = len(wins) / len(all_pnls) * 100.0 if all_pnls else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses)) if losses else 0.0
        total_pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        total_pnl_str = _fmt_pnl(total_pnl)
        # Portfolio-level Sharpe: treat each bot-trade as one return unit
        if len(all_pnls) > 1:
            arr = np.array(all_pnls)
            mean_r = np.mean(arr)
            std_r = np.std(arr, ddof=1)
            port_sharpe = (mean_r / std_r) * np.sqrt(len(arr)) if std_r > 0 else 0.0
        else:
            port_sharpe = 0.0
        # Portfolio drawdown from combined equity curve
        if not all_trades_df.empty:
            sorted_df = all_trades_df.sort_values("exit_time")
            cum = np.cumsum(sorted_df["pnl"].values)
            equity = INITIAL_BALANCE + cum
            peak = np.maximum.accumulate(equity)
            dd_arr = peak - equity
            max_dd = np.max(dd_arr)
            peak_at_max = peak[np.argmax(dd_arr)]
            port_dd = (max_dd / peak_at_max * 100.0) if peak_at_max > 0 else 0.0
        else:
            port_dd = 0.0

        print(
            f"{'TOTAL':<12} {'':<32} {total_trades:>7} {total_wr:>5.1f}% "
            f"{total_pf:>6.2f} {port_sharpe:>7.2f} {port_dd:>5.1f}% {total_pnl_str:>12}"
        )
    print()


def print_part2_monthly(
    all_trades_df: pd.DataFrame,
    active_bots: list[str],
) -> None:
    """Print Part 2: monthly PnL table across all months."""
    print("=" * 120)
    print("PART 2: MONTHLY PnL (full period, $200 starting balance per bot)")
    print("=" * 120)

    if all_trades_df.empty:
        print("  No trades found.")
        print()
        return

    # Map each bot to its abbreviation (limit to 4 chars for column header)
    abbrevs = [_NAME_TO_ABBREV.get(n, n[:4]) for n in active_bots]
    col_w = 7  # width per bot column

    # Build pivot: rows=month, cols=bot, values=sum(pnl)
    df = all_trades_df.copy()
    df["month"] = df["exit_time"].dt.to_period("M")
    pivot = (
        df.groupby(["month", "bot"])["pnl"]
        .sum()
        .unstack(fill_value=0.0)
        .reindex(columns=active_bots, fill_value=0.0)
    )
    pivot = pivot.sort_index()

    # Header row
    month_w = 10
    total_w = 9
    bal_w = 11
    dd_w = 6

    header_parts = [f"{'Month':<{month_w}}"]
    for abbr in abbrevs:
        header_parts.append(f"{abbr:>{col_w}}")
    header_parts.append(f"{'Total':>{total_w}}")
    header_parts.append(f"{'CumBal':>{bal_w}}")
    header_parts.append(f"{'DD%':>{dd_w}}")
    print("  ".join(header_parts))
    print("-" * (month_w + len(abbrevs) * (col_w + 2) + total_w + bal_w + dd_w + 10))

    balance = INITIAL_BALANCE
    peak_balance = INITIAL_BALANCE

    for month_period, row_series in pivot.iterrows():
        month_total = row_series.sum()
        balance += month_total
        peak_balance = max(peak_balance, balance)
        dd_pct = (peak_balance - balance) / peak_balance * 100.0 if peak_balance > 0 else 0.0

        row_parts = [f"{str(month_period):<{month_w}}"]
        for bot_name in active_bots:
            val = row_series.get(bot_name, 0.0)
            row_parts.append(_fmt_col(val, col_w))
        total_str = _fmt_pnl(month_total).rjust(total_w)
        bal_str = f"${int(round(balance))}".rjust(bal_w)
        dd_str = f"{dd_pct:.1f}%".rjust(dd_w)
        row_parts.append(total_str)
        row_parts.append(bal_str)
        row_parts.append(dd_str)
        print("  ".join(row_parts))

    print()
    print(f"  Final balance: ${int(round(balance))}  |  "
          f"Total PnL: {_fmt_pnl(balance - INITIAL_BALANCE)}  |  "
          f"Max DD: {_fmt_pct((peak_balance - balance) / peak_balance * 100.0 if peak_balance > 0 else 0.0)}")
    print()


def print_part3_daily(
    all_trades_df: pd.DataFrame,
    active_bots: list[str],
) -> None:
    """Print Part 3: daily PnL for the last 2 months."""
    print("=" * 100)
    print("PART 3: DAILY PnL (last 2 months)")
    print("=" * 100)

    if all_trades_df.empty:
        print("  No trades found.")
        print()
        return

    # Determine cutoff: last 2 calendar months relative to latest trade exit
    latest_exit = all_trades_df["exit_time"].max()
    # Go back 2 full months: e.g. if latest is 2026-03-15, cutoff is 2026-01-15
    cutoff = latest_exit - pd.DateOffset(months=2)

    recent_df = all_trades_df[all_trades_df["exit_time"] >= cutoff].copy()
    if recent_df.empty:
        print("  No trades in last 2 months.")
        print()
        return

    recent_df["date"] = recent_df["exit_time"].dt.date

    # Starting balance for this window: use cumulative up to cutoff
    sorted_all = all_trades_df.sort_values("exit_time")
    pre_cutoff = sorted_all[sorted_all["exit_time"] < cutoff]
    start_balance = INITIAL_BALANCE + pre_cutoff["pnl"].sum()

    # Header
    date_w = 12
    trd_w = 7
    win_w = 5
    los_w = 5
    pnl_w = 12
    bal_w = 12
    dd_w = 6
    coins_w = 40

    print(
        f"  {'Date':<{date_w}} {'Trades':>{trd_w}} {'Win':>{win_w}} {'Loss':>{los_w}} "
        f"{'Total PnL':>{pnl_w}} {'Cum.Bal':>{bal_w}} {'DD%':>{dd_w}}  Coins with trades"
    )
    print("-" * (date_w + trd_w + win_w + los_w + pnl_w + bal_w + dd_w + coins_w + 20))

    balance = start_balance
    peak_balance = start_balance

    for date_val, day_df in recent_df.groupby("date"):
        n_trades = len(day_df)
        n_wins = (day_df["pnl"] > 0).sum()
        n_losses = (day_df["pnl"] < 0).sum()
        day_pnl = day_df["pnl"].sum()

        balance += day_pnl
        peak_balance = max(peak_balance, balance)
        dd_pct = (peak_balance - balance) / peak_balance * 100.0 if peak_balance > 0 else 0.0

        # Coins detail: "BTC(+120) AVAX(-165)"
        coin_parts = []
        for _, trade_row in day_df.sort_values("bot").iterrows():
            pnl_int = int(round(trade_row["pnl"]))
            sign = "+" if pnl_int >= 0 else ""
            coin_parts.append(f"{trade_row['bot']}({sign}{pnl_int})")
        coins_str = " ".join(coin_parts)

        pnl_str = _fmt_pnl(day_pnl).rjust(pnl_w)
        bal_str = f"${int(round(balance))}".rjust(bal_w)
        dd_str = f"{dd_pct:.1f}%".rjust(dd_w)

        print(
            f"  {str(date_val):<{date_w}} {n_trades:>{trd_w}} {n_wins:>{win_w}} {n_losses:>{los_w}} "
            f"{pnl_str} {bal_str} {dd_str}  {coins_str}"
        )

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run all portfolio bots and print the three-part report."""
    print("=" * 100)
    print("CCBT PORTFOLIO REPORT ENGINE")
    print(f"Starting balance: ${INITIAL_BALANCE:,.0f}  |  Bots: {len(PORTFOLIO)}")
    print("=" * 100)
    print()
    print("Running backtests...")
    print()

    all_trades: list[dict] = []
    bot_metrics: dict[str, dict] = {}
    active_bots: list[str] = []  # bots that ran successfully, in PORTFOLIO order

    for bot in PORTFOLIO:
        name = bot["name"]
        print(f"  {name:<12}", end=" ", flush=True)
        try:
            trades, metrics = run_bot(bot)
            all_trades.extend(trades)
            bot_metrics[name] = {"metrics": metrics, "trades": trades}
            active_bots.append(name)
            total_bot_pnl = sum(t["pnl"] for t in trades)
            print(
                f"OK  trades={metrics.total_trades:>3}  "
                f"PF={metrics.profit_factor:.2f}  "
                f"WR={metrics.win_rate * 100:.0f}%  "
                f"PnL={_fmt_pnl(total_bot_pnl)}"
            )
        except FileNotFoundError as exc:
            print(f"SKIP  ({exc})")
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR  ({exc})")

    print()

    if not all_trades:
        print("No trades collected — nothing to report.")
        return

    # Build combined DataFrame, sorted by exit_time
    all_trades_df = pd.DataFrame(all_trades)
    all_trades_df = all_trades_df.sort_values("exit_time").reset_index(drop=True)

    # Part 1
    print_part1_summary(bot_metrics, all_trades_df)

    # Part 2
    print_part2_monthly(all_trades_df, active_bots)

    # Part 3
    print_part3_daily(all_trades_df, active_bots)


if __name__ == "__main__":
    main()
