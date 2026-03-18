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
    original_size: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    tp1_price: float = 0.0
    tp1_hit: bool = False
    pnl: float = 0.0
    pnl_pct: float = 0.0
    close_reason: str = ""
    risk_amount: float = 0.0
    partial_pnl: float = 0.0  # PnL from TP1 partial close


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
        self.slippage_rate = config.get("slippage_rate", 0.0002)
        self.state = BacktestState(
            balance=initial_balance, initial_balance=initial_balance
        )

    @staticmethod
    def _get_slippage_multiplier(timestamp) -> float:
        """Get slippage multiplier based on time-of-day and day-of-week.

        Args:
            timestamp: A pandas Timestamp or datetime-like object.

        Returns:
            Multiplier for the base slippage rate.
        """
        try:
            ts = pd.Timestamp(timestamp)
        except Exception:
            return 1.0

        # Weekend: low liquidity
        if ts.dayofweek >= 5:  # Saturday=5, Sunday=6
            return 2.0

        hour = ts.hour
        if 0 <= hour < 8:
            return 1.0   # Asia hours
        elif 8 <= hour < 16:
            return 0.8   # Europe hours
        else:
            return 0.7   # US hours

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

            # Apply slippage to entry (with time-of-day adjustment)
            slippage_mult = self._get_slippage_multiplier(current_time)
            effective_slippage = self.slippage_rate * slippage_mult
            if signal_type == SignalType.LONG:
                entry_price *= 1 + effective_slippage
            else:
                entry_price *= 1 - effective_slippage

            # Calculate risk amount BEFORE commission deduction
            risk_amount = self.state.balance * self.config["risk_per_trade"]

            # Commission on entry
            commission = position_size * self.commission_rate
            self.state.balance -= commission

            # Compute TP1 price for partial take-profit
            partial_tp_atr_mult = self.config.get("partial_tp_atr_mult", 2.0)
            if signal_type == SignalType.LONG:
                tp1_price = entry_price + partial_tp_atr_mult * atr
            else:
                tp1_price = entry_price - partial_tp_atr_mult * atr

            self.state.position = BacktestTrade(
                entry_time=current_time,
                side="long" if signal_type == SignalType.LONG else "short",
                entry_price=entry_price,
                size=position_size,
                original_size=position_size,
                stop_loss=sl,
                take_profit=tp,
                tp1_price=tp1_price,
                tp1_hit=False,
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
        # Item 10: When both SL and TP hit on same candle, use candle direction as tiebreaker
        open_price = row["open"]
        if pos.side == "long":
            sl_hit = low <= pos.stop_loss
            tp_hit = high >= pos.take_profit
            if sl_hit and tp_hit:
                if close > open_price:  # Bullish candle → TP filled first
                    self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr)
                else:
                    self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr)
                return
            if sl_hit:
                self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr)
                return
        else:
            sl_hit = high >= pos.stop_loss
            tp_hit = low <= pos.take_profit
            if sl_hit and tp_hit:
                if close < open_price:  # Bearish candle → TP filled first
                    self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr)
                else:
                    self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr)
                return
            if sl_hit:
                self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr)
                return

        # --- Item 5: Partial TP1 check (before full TP) ---
        partial_tp_enabled = self.config.get("partial_tp_enabled", False)
        if partial_tp_enabled and not pos.tp1_hit and pos.tp1_price > 0:
            tp1_triggered = (
                (pos.side == "long" and high >= pos.tp1_price)
                or (pos.side == "short" and low <= pos.tp1_price)
            )
            if tp1_triggered:
                partial_pct = self.config.get("partial_tp_pct", 0.5)
                partial_size = pos.original_size * partial_pct
                partial_exit_price = pos.tp1_price

                # Apply slippage to partial exit
                slippage_mult = self._get_slippage_multiplier(current_time)
                effective_slippage = self.slippage_rate * slippage_mult
                if pos.side == "long":
                    partial_exit_price *= 1 - effective_slippage
                    partial_pnl_pct = (partial_exit_price - pos.entry_price) / pos.entry_price
                else:
                    partial_exit_price *= 1 + effective_slippage
                    partial_pnl_pct = (pos.entry_price - partial_exit_price) / pos.entry_price

                partial_pnl = partial_size * partial_pnl_pct
                # Commission on partial exit
                partial_pnl -= partial_size * self.commission_rate

                self.state.balance += partial_pnl
                self.state.daily_pnl += partial_pnl
                pos.partial_pnl += partial_pnl
                pos.tp1_hit = True
                # Reduce position size by partial amount
                pos.size -= partial_size
                pos.size = max(pos.size, 0.0)

                # Move SL to breakeven after TP1
                if self.config.get("move_sl_to_be_after_tp1", True):
                    if pos.side == "long":
                        pos.stop_loss = max(pos.stop_loss, pos.entry_price)
                    else:
                        pos.stop_loss = min(pos.stop_loss, pos.entry_price)

                logger.debug(
                    "backtest_partial_tp1",
                    extra={
                        "side": pos.side,
                        "tp1_price": pos.tp1_price,
                        "partial_pnl": round(partial_pnl, 4),
                        "remaining_size": pos.size,
                    },
                )

        # Full TP check (on remaining position)
        if pos.side == "long":
            if high >= pos.take_profit:
                self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr)
                return
        else:
            if low <= pos.take_profit:
                self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr)
                return

        # Trailing stop update with regime-adaptive multiplier
        if pd.notna(row.get("atr")):
            signal_type = SignalType.LONG if pos.side == "long" else SignalType.SHORT
            # Determine regime from row if available, else default to trending
            regime = row.get("regime", "trending") if hasattr(row, "get") else "trending"
            if not isinstance(regime, str):
                regime = "trending"
            regime_trail_config = self.config.copy()
            if regime == "ranging":
                regime_trail_config["atr_trail_mult"] = self.config.get(
                    "atr_trail_mult_ranging", self.config.get("atr_trail_mult", 1.8)
                )
            elif regime == "volatile":
                regime_trail_config["atr_trail_mult"] = self.config.get(
                    "atr_trail_mult_volatile", self.config.get("atr_trail_mult", 1.8)
                )
            else:
                regime_trail_config["atr_trail_mult"] = self.config.get(
                    "atr_trail_mult_trending", self.config.get("atr_trail_mult", 1.8)
                )
            new_sl = compute_trailing_stop(
                close, pos.stop_loss, row["atr"], signal_type, regime_trail_config
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

        # Apply slippage to exit (with time-of-day adjustment)
        slippage_mult = self._get_slippage_multiplier(exit_time)
        effective_slippage = self.slippage_rate * slippage_mult
        if pos.side == "long":
            exit_price *= 1 - effective_slippage
        else:
            exit_price *= 1 + effective_slippage

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
        # Total PnL includes partial close already booked (partial_pnl already added to balance)
        total_pnl = pnl + pos.partial_pnl
        risk_mgr.record_trade_result(total_pnl)

        pos.exit_time = exit_time
        pos.exit_price = exit_price
        pos.pnl = total_pnl  # Store combined PnL for metrics
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
