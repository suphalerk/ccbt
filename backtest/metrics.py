"""Backtest performance metrics: Sharpe, Sortino, Max Drawdown, Win Rate, etc."""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class BacktestMetrics:
    """Complete backtest performance metrics."""

    sharpe_ratio: float  # Target > 1.5
    sortino_ratio: float
    max_drawdown: float  # Accept < 15%
    win_rate: float  # Target > 45%
    profit_factor: float  # Target > 1.5
    avg_rr_actual: float
    total_trades: int
    avg_trade_duration: timedelta
    monthly_returns: list = field(default_factory=list)


def compute_metrics(
    trades: pd.DataFrame,
    risk_free_rate: float = 0.0,
    initial_balance: float = 10000.0,
) -> BacktestMetrics:
    """Compute full backtest metrics from trade results.

    Args:
        trades: DataFrame with columns: entry_time, exit_time, pnl, pnl_pct,
                entry_price, exit_price, side, risk_amount.
        risk_free_rate: Annual risk-free rate for Sharpe/Sortino.

    Returns:
        BacktestMetrics with all computed metrics.
    """
    if trades.empty:
        return BacktestMetrics(
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            avg_rr_actual=0.0,
            total_trades=0,
            avg_trade_duration=timedelta(0),
        )

    returns = trades["pnl_pct"].values
    pnls = trades["pnl"].values

    # Win rate
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    win_rate = len(wins) / len(pnls) if len(pnls) > 0 else 0.0

    # Profit factor
    gross_profit = wins.sum() if len(wins) > 0 else 0.0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe ratio (annualized, assuming ~96 trades per day on 15m candles)
    # Use daily returns approximation
    mean_return = np.mean(returns)
    std_return = np.std(returns, ddof=1) if len(returns) > 1 else 1.0
    daily_rf = risk_free_rate / 365
    sharpe = (mean_return - daily_rf) / std_return * np.sqrt(365) if std_return > 0 else 0.0

    # Sortino ratio (only downside deviation)
    downside_returns = returns[returns < 0]
    downside_std = np.std(downside_returns, ddof=1) if len(downside_returns) > 1 else 1.0
    sortino = (mean_return - daily_rf) / downside_std * np.sqrt(365) if downside_std > 0 else 0.0

    # Max drawdown (as percentage of equity at peak, not just cumulative PnL)
    cumulative = np.cumsum(pnls)
    equity = initial_balance + cumulative
    peak_equity = np.maximum.accumulate(equity)
    drawdown = peak_equity - equity
    max_dd = np.max(drawdown) if len(drawdown) > 0 else 0.0
    peak_at_max_dd = peak_equity[np.argmax(drawdown)] if len(drawdown) > 0 else initial_balance
    max_dd_pct = max_dd / peak_at_max_dd if peak_at_max_dd > 0 else 0.0

    # Average R:R actual
    avg_win = np.mean(np.abs(wins)) if len(wins) > 0 else 0.0
    avg_loss = np.mean(np.abs(losses)) if len(losses) > 0 else 1.0
    avg_rr = avg_win / avg_loss if avg_loss > 0 else 0.0

    # Average trade duration
    if "entry_time" in trades.columns and "exit_time" in trades.columns:
        durations = pd.to_datetime(trades["exit_time"]) - pd.to_datetime(trades["entry_time"])
        avg_duration = durations.mean()
    else:
        avg_duration = timedelta(0)

    # Monthly returns
    monthly = []
    if "exit_time" in trades.columns:
        trades_copy = trades.copy()
        trades_copy["month"] = pd.to_datetime(trades_copy["exit_time"]).dt.to_period("M")
        monthly_pnl = trades_copy.groupby("month")["pnl"].sum()
        monthly = monthly_pnl.to_dict()
        monthly = [{"month": str(k), "pnl": v} for k, v in monthly.items()]

    return BacktestMetrics(
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        max_drawdown=round(max_dd_pct, 4),
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 4),
        avg_rr_actual=round(avg_rr, 4),
        total_trades=len(pnls),
        avg_trade_duration=avg_duration,
        monthly_returns=monthly,
    )


def print_metrics(metrics: BacktestMetrics) -> str:
    """Format metrics as a readable string.

    Args:
        metrics: Computed backtest metrics.

    Returns:
        Formatted metrics string.
    """
    lines = [
        "=" * 50,
        "BACKTEST RESULTS",
        "=" * 50,
        f"Total Trades:        {metrics.total_trades}",
        f"Win Rate:            {metrics.win_rate:.2%}",
        f"Profit Factor:       {metrics.profit_factor:.2f}",
        f"Avg R:R Actual:      {metrics.avg_rr_actual:.2f}",
        f"Sharpe Ratio:        {metrics.sharpe_ratio:.2f}",
        f"Sortino Ratio:       {metrics.sortino_ratio:.2f}",
        f"Max Drawdown:        {metrics.max_drawdown:.2%}",
        f"Avg Trade Duration:  {metrics.avg_trade_duration}",
        "=" * 50,
    ]

    if metrics.monthly_returns:
        lines.append("MONTHLY RETURNS:")
        for m in metrics.monthly_returns:
            lines.append(f"  {m['month']}: ${m['pnl']:.2f}")
        lines.append("=" * 50)

    return "\n".join(lines)
