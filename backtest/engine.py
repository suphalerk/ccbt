"""Event-driven backtest engine."""

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd

from backtest.metrics import BacktestMetrics, compute_metrics, print_metrics
from bot.data import add_indicators, add_trend_filter, compute_atr
from bot.risk import RiskManager
from bot.strategy import (
    SignalType,
    check_entry_conditions,
    compute_levels,
    compute_trailing_stop,
)

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """A single backtest trade record."""

    entry_time: str
    exit_time: str = ""
    side: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    size: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    close_reason: str = ""
    risk_amount: float = 0.0


@dataclass
class BacktestState:
    """Current state during backtest simulation."""

    balance: float = 10000.0
    initial_balance: float = 10000.0
    position: Optional[BacktestTrade] = None
    trades: list = field(default_factory=list)
    daily_pnl: float = 0.0
    current_date: str = ""


class BacktestEngine:
    """Event-driven backtesting engine.

    Simulates the trading strategy on historical data with realistic
    commission, slippage, and risk management.
    """

    def __init__(self, config: dict, initial_balance: float = 10000.0) -> None:
        """Initialize the backtest engine.

        Args:
            config: Bot configuration.
            initial_balance: Starting balance in USDT.
        """
        self.config = config
        self.commission_rate = config.get("commission_rate", 0.00055)
        self.slippage_rate = config.get("slippage_rate", 0.0005)
        self.state = BacktestState(
            balance=initial_balance, initial_balance=initial_balance
        )

    def run(
        self,
        signal_data: pd.DataFrame,
        trend_data: Optional[pd.DataFrame] = None,
    ) -> BacktestMetrics:
        """Run the backtest on historical data.

        Uses candle close prices only (no look-ahead bias).

        Args:
            signal_data: Signal timeframe OHLCV data (e.g., 15m).
            trend_data: Trend timeframe OHLCV data (e.g., 1h). Optional.

        Returns:
            BacktestMetrics with full performance analysis.
        """
        # Add indicators
        df = add_indicators(signal_data, self.config)
        if trend_data is not None and not trend_data.empty:
            df = add_trend_filter(df, trend_data, self.config)

        risk_mgr = RiskManager(self.config, self.state.balance)

        logger.info(
            "backtest_started",
            extra={
                "candles": len(df),
                "balance": self.state.balance,
                "start": str(df.index[0]) if len(df) > 0 else "",
                "end": str(df.index[-1]) if len(df) > 0 else "",
            },
        )

        for i in range(1, len(df)):
            row = df.iloc[i]
            prev_row = df.iloc[i - 1]
            current_time = str(df.index[i])
            current_date = str(df.index[i].date())

            # Daily reset
            if current_date != self.state.current_date:
                self.state.current_date = current_date
                self.state.daily_pnl = 0.0
                risk_mgr.reset_daily(self.state.balance)

            # Check existing position
            if self.state.position is not None:
                self._check_exit(row, current_time, risk_mgr)

            # Check for new entry (use prev_row to avoid look-ahead)
            if self.state.position is None:
                self._check_entry(prev_row, row, current_time, risk_mgr)

        # Close any remaining position at end
        if self.state.position is not None:
            self._force_close(df.iloc[-1], str(df.index[-1]), "backtest_end")

        # Compute metrics
        if self.state.trades:
            trades_df = pd.DataFrame([vars(t) for t in self.state.trades])
            metrics = compute_metrics(trades_df)
        else:
            metrics = compute_metrics(pd.DataFrame())

        result_str = print_metrics(metrics)
        logger.info("backtest_complete", extra={"summary": result_str})
        print(result_str)

        return metrics

    def _check_entry(
        self,
        signal_row: pd.Series,
        current_row: pd.Series,
        current_time: str,
        risk_mgr: RiskManager,
    ) -> None:
        """Check for entry signal and open position if valid.

        Args:
            signal_row: Previous candle (signal source, no look-ahead).
            current_row: Current candle (entry execution).
            current_time: Current timestamp.
            risk_mgr: Risk manager instance.
        """
        entry_price = current_row["close"]

        for signal_type in (SignalType.LONG, SignalType.SHORT):
            if not check_entry_conditions(signal_row, self.config, signal_type):
                continue

            atr = signal_row["atr"]
            sl, tp = compute_levels(entry_price, atr, signal_type, self.config)

            # Validate through risk manager
            num_positions = 1 if self.state.position else 0
            approved, reason, position_size = risk_mgr.validate_order(
                balance=self.state.balance,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                num_open_positions=num_positions,
            )

            if not approved:
                continue

            # Apply slippage to entry
            if signal_type == SignalType.LONG:
                entry_price *= 1 + self.slippage_rate
            else:
                entry_price *= 1 - self.slippage_rate

            # Commission on entry
            commission = position_size * self.commission_rate
            self.state.balance -= commission

            risk_amount = self.state.balance * self.config["risk_per_trade"]

            self.state.position = BacktestTrade(
                entry_time=current_time,
                side="long" if signal_type == SignalType.LONG else "short",
                entry_price=entry_price,
                size=position_size,
                stop_loss=sl,
                take_profit=tp,
                risk_amount=risk_amount,
            )
            break  # Only one entry per candle

    def _check_exit(
        self, row: pd.Series, current_time: str, risk_mgr: RiskManager
    ) -> None:
        """Check exit conditions for current position.

        Args:
            row: Current candle.
            current_time: Current timestamp.
            risk_mgr: Risk manager instance.
        """
        pos = self.state.position
        if pos is None:
            return

        close = row["close"]
        high = row["high"]
        low = row["low"]

        # Check stop loss hit (using high/low for intracandle)
        if pos.side == "long":
            if low <= pos.stop_loss:
                self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr)
                return
            if high >= pos.take_profit:
                self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr)
                return
        else:
            if high >= pos.stop_loss:
                self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr)
                return
            if low <= pos.take_profit:
                self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr)
                return

        # Trailing stop update
        if pd.notna(row.get("atr")):
            signal_type = SignalType.LONG if pos.side == "long" else SignalType.SHORT
            new_sl = compute_trailing_stop(
                close, pos.stop_loss, row["atr"], signal_type, self.config
            )
            pos.stop_loss = new_sl

    def _close_position(
        self,
        exit_price: float,
        exit_time: str,
        reason: str,
        risk_mgr: RiskManager,
    ) -> None:
        """Close the current position.

        Args:
            exit_price: Exit price.
            exit_time: Exit timestamp.
            reason: Close reason.
            risk_mgr: Risk manager instance.
        """
        pos = self.state.position
        if pos is None:
            return

        # Apply slippage to exit
        if pos.side == "long":
            exit_price *= 1 - self.slippage_rate
        else:
            exit_price *= 1 + self.slippage_rate

        # Calculate PnL
        if pos.side == "long":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price

        pnl = pos.size * pnl_pct

        # Commission on exit
        commission = pos.size * self.commission_rate
        pnl -= commission

        # Update state
        self.state.balance += pnl
        self.state.daily_pnl += pnl
        risk_mgr.record_trade_result(pnl)

        pos.exit_time = exit_time
        pos.exit_price = exit_price
        pos.pnl = pnl
        pos.pnl_pct = pnl_pct
        pos.close_reason = reason
        self.state.trades.append(pos)
        self.state.position = None

    def _force_close(
        self, row: pd.Series, current_time: str, reason: str
    ) -> None:
        """Force close position at market price (end of backtest).

        Args:
            row: Current candle.
            current_time: Current timestamp.
            reason: Close reason.
        """
        if self.state.position is None:
            return

        # Create a temporary risk manager for closing
        risk_mgr = RiskManager(self.config, self.state.balance)
        self._close_position(row["close"], current_time, reason, risk_mgr)
