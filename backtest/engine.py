"""Event-driven backtest engine."""

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd

from backtest.metrics import BacktestMetrics, compute_metrics, print_metrics
from bot.data import add_indicators, add_trend_filter, compute_atr, detect_regime
from bot.risk import RiskManager
from bot.strategy import (
    SignalType,
    check_bb_breakout_conditions,
    check_entry_conditions,
    check_fast_crossover_conditions,
    check_mean_reversion_conditions,
    check_pullback_conditions,
    check_rsi_divergence_conditions,
    compute_levels,
    compute_net_rr,
    compute_signal_quality_score,
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
    signal_source: str = "ema_crossover"  # Which signal type triggered this trade
    # Pyramiding tracking
    pyramid_count: int = 0
    pyramid_sizes: list = field(default_factory=list)


@dataclass
class BacktestState:
    """Current state during backtest simulation."""

    balance: float = 10000.0
    initial_balance: float = 10000.0
    position: Optional[BacktestTrade] = None
    trades: list = field(default_factory=list)
    daily_pnl: float = 0.0
    current_date: str = ""
    last_close_candle_idx: int = -999  # Candle index when last trade closed
    last_close_reason: str = ""  # "stop_loss", "take_profit", etc.
    # MTD accelerator state
    mtd_pnl: float = 0.0
    mtd_start_balance: float = 0.0
    current_month: str = ""


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
        # Store enriched df for multi-signal access in _check_entry
        self._df = df

        # Propagate rolling regime to each row for regime-adaptive exits and entry gate
        atr_period = self.config.get("atr_period", 14)
        regime_lookback = self.config.get("regime_lookback", 20)
        regimes = []
        for i in range(len(df)):
            if i < atr_period + regime_lookback:
                regimes.append("ranging")
            else:
                window = df.iloc[max(0, i - regime_lookback - atr_period) : i + 1]
                regimes.append(detect_regime(window, atr_period, lookback=regime_lookback))
        df["regime"] = regimes

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

        # Initialise MTD state for the first candle's month
        if len(df) > 0:
            first_month = str(pd.Timestamp(df.index[0]).to_period("M"))
            self.state.current_month = first_month
            self.state.mtd_start_balance = self.state.balance
            self.state.mtd_pnl = 0.0

        for i in range(1, len(df)):
            row = df.iloc[i]
            prev_row = df.iloc[i - 1]
            current_time = str(df.index[i])
            current_date = str(df.index[i].date())
            current_month = str(pd.Timestamp(df.index[i]).to_period("M"))

            # Daily reset
            if current_date != self.state.current_date:
                self.state.current_date = current_date
                self.state.daily_pnl = 0.0
                risk_mgr.reset_daily(self.state.balance)

            # Monthly reset for MTD accelerator
            if current_month != self.state.current_month:
                self.state.current_month = current_month
                self.state.mtd_pnl = 0.0
                self.state.mtd_start_balance = self.state.balance

            # Check existing position
            if self.state.position is not None:
                self._check_exit(row, current_time, risk_mgr, candle_idx=i)

            # Check for new entry (use prev_row to avoid look-ahead)
            if self.state.position is None:
                self._check_entry(prev_row, row, current_time, risk_mgr, candle_idx=i)

        # Close any remaining position at end
        if self.state.position is not None:
            self._force_close(df.iloc[-1], str(df.index[-1]), "backtest_end")

        # Compute metrics
        if self.state.trades:
            trades_df = pd.DataFrame([vars(t) for t in self.state.trades])
            metrics = compute_metrics(trades_df, initial_balance=self.state.initial_balance)
        else:
            metrics = compute_metrics(pd.DataFrame(), initial_balance=self.state.initial_balance)

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
        candle_idx: int = 0,
    ) -> None:
        """Check for entry signal and open position if valid.

        Args:
            signal_row: Previous candle (signal source, no look-ahead).
            current_row: Current candle (entry execution).
            current_time: Current timestamp.
            risk_mgr: Risk manager instance.
            candle_idx: Current candle index for cooldown tracking.
        """
        # Candle-based cooldown after trade close
        cooldown_normal = self.config.get("cooldown_candles_after_close", 0)
        cooldown_sl = self.config.get("cooldown_candles_after_sl", 0)
        in_cooldown = False
        if self.state.last_close_candle_idx >= 0:
            candles_since_close = candle_idx - self.state.last_close_candle_idx
            required_cooldown = cooldown_sl if self.state.last_close_reason == "stop_loss" else cooldown_normal
            if candles_since_close < required_cooldown:
                # Check if flexible cooldown can override
                flex_cfg = self.config.get("flexible_cooldown", {})
                if flex_cfg.get("enabled", False):
                    reduction = max(0.0, min(flex_cfg.get("cooldown_reduction_factor", 0.5), 1.0))
                    reduced_cooldown = required_cooldown * reduction
                    if candles_since_close >= reduced_cooldown:
                        # Peek at signal quality to decide override
                        in_cooldown = True  # Will be resolved below after signal check
                    else:
                        return
                else:
                    return

        # Weekend filter
        try:
            ts = pd.Timestamp(current_time)
            is_weekend = ts.dayofweek >= 5
        except Exception:
            is_weekend = False

        weekend_trading = self.config.get("weekend_trading_enabled", True)
        if is_weekend and not weekend_trading:
            return

        # Trading hours filter — skip low-liquidity dead hours
        trading_hours = self.config.get("trading_hours", None)
        if trading_hours and trading_hours.get("enabled", False):
            hour = ts.hour
            start_hour = trading_hours.get("start_utc", 0)
            end_hour = trading_hours.get("end_utc", 24)
            if start_hour < end_hour:
                if not (start_hour <= hour < end_hour):
                    return
            else:  # Wraps around midnight (e.g., 20-04)
                if end_hour <= hour < start_hour:
                    return

        # Regime gate — skip entries in ranging/volatile regime if configured.
        # Exception: mean_reversion REQUIRES ranging, so we cannot gate it out here.
        # Instead we gate per-signal inside the loop below.
        regime = signal_row.get("regime", "trending") if hasattr(signal_row, "get") else "trending"
        if not isinstance(regime, str):
            regime = "trending"
        regime_filter = self.config.get("regime_filter", {})
        mr_enabled = self.config.get("signals", {}).get("mean_reversion", {}).get("enabled", False)
        # Only apply the hard ranging gate when mean_reversion is disabled —
        # otherwise let each signal decide (MR requires ranging; others skip it).
        apply_ranging_gate = (
            regime_filter.get("enabled", False)
            and regime == "ranging"
            and regime_filter.get("skip_ranging", False)
            and not mr_enabled
        )
        if apply_ranging_gate:
            return
        if regime_filter.get("enabled", False) and regime == "volatile" and regime_filter.get("skip_volatile", False):
            return

        entry_price = current_row["close"]
        signals_config = self.config.get("signals", {})

        for signal_type in (SignalType.LONG, SignalType.SHORT):
            # Determine which signal source triggered, in priority order
            signal_source = None

            # Trend-following signals: skip when regime is ranging (they need trend)
            in_ranging = regime == "ranging"
            trend_signals_gated = (
                regime_filter.get("enabled", False)
                and in_ranging
                and regime_filter.get("skip_ranging", False)
            )

            if not trend_signals_gated:
                if signals_config.get("ema_crossover", {}).get("enabled", True):
                    if check_entry_conditions(signal_row, self.config, signal_type):
                        signal_source = "ema_crossover"

                if signal_source is None and signals_config.get("ema_fast_crossover", {}).get("enabled", True):
                    if check_fast_crossover_conditions(signal_row, self.config, signal_type):
                        signal_source = "ema_fast_crossover"

                if signal_source is None and signals_config.get("ema_pullback", {}).get("enabled", True):
                    if check_pullback_conditions(signal_row, self.config, signal_type):
                        signal_source = "ema_pullback"

                if signal_source is None and signals_config.get("bb_breakout", {}).get("enabled", True):
                    if check_bb_breakout_conditions(signal_row, self.config, signal_type):
                        signal_source = "bb_breakout"

                if signal_source is None and signals_config.get("rsi_divergence", {}).get("enabled", True):
                    if hasattr(self, "_df") and candle_idx > 0:
                        prev_slice = self._df.iloc[:candle_idx]
                        if check_rsi_divergence_conditions(signal_row, prev_slice, self.config, signal_type):
                            signal_source = "rsi_divergence"

            # Mean-reversion: only fires in ranging regime — check always (its own regime guard)
            if signal_source is None and signals_config.get("mean_reversion", {}).get("enabled", False):
                if check_mean_reversion_conditions(signal_row, self.config, signal_type):
                    signal_source = "mean_reversion"

            if signal_source is None:
                continue

            atr = signal_row["atr"]
            # Mean-reversion uses tighter SL/TP multipliers
            if signal_source == "mean_reversion":
                sl_mult = self.config.get("mr_atr_sl_mult", 1.0)
                tp_mult = self.config.get("mr_atr_tp_mult", 1.5)
                sl, tp = compute_levels(
                    entry_price, atr, signal_type,
                    {"atr_sl_mult": sl_mult, "atr_tp_mult": tp_mult},
                )
            else:
                sl, tp = compute_levels(entry_price, atr, signal_type, self.config)

            # Flexible cooldown: check signal quality to override
            if in_cooldown:
                net_rr = compute_net_rr(entry_price, sl, tp, self.config)
                vol_ma = signal_row.get("volume_ma", 0)
                volume_ratio = signal_row["volume"] / vol_ma if vol_ma and vol_ma > 0 else 1.0
                regime = signal_row.get("regime", "ranging") if hasattr(signal_row, "get") else "ranging"
                if not isinstance(regime, str):
                    regime = "ranging"
                flex_cfg = self.config.get("flexible_cooldown", {})
                min_quality = max(0.5, min(flex_cfg.get("min_quality_score", 0.7), 1.0))
                score = compute_signal_quality_score(
                    net_rr, signal_row.get("rsi", 50), volume_ratio, regime, signal_type, self.config
                )
                if score < min_quality:
                    continue
                if flex_cfg.get("log_overrides", True):
                    logger.info(
                        "flexible_cooldown_override",
                        extra={
                            "quality_score": round(score, 3),
                            "min_quality": min_quality,
                            "candles_since_close": candles_since_close,
                            "required_cooldown": required_cooldown,
                            "signal_type": signal_type.value,
                        },
                    )

            # Pass simulated time to risk manager for cooldown checks
            sim_time = pd.Timestamp(current_time).timestamp()

            # Validate through risk manager
            num_positions = 1 if self.state.position else 0
            approved, reason, position_size = risk_mgr.validate_order(
                balance=self.state.balance,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                num_open_positions=num_positions,
                current_time=sim_time,
            )

            if not approved:
                continue

            # --- Adaptive position sizing (Feature 1) ---
            # Compute signal quality score and apply tiered risk/leverage multipliers.
            # This block is entirely skipped (risk_mult=1.0, lev_mult=1.0) when
            # adaptive_sizing.enabled is false, so the baseline is unchanged.
            adaptive_cfg = self.config.get("adaptive_sizing", {})
            if adaptive_cfg.get("enabled", False):
                net_rr_for_score = compute_net_rr(entry_price, sl, tp, self.config)
                vol_ma_val = signal_row.get("volume_ma", 0)
                volume_ratio_val = (
                    signal_row["volume"] / vol_ma_val
                    if vol_ma_val and vol_ma_val > 0
                    else 1.0
                )
                quality_score = compute_signal_quality_score(
                    net_rr_for_score,
                    signal_row.get("rsi", 50),
                    volume_ratio_val,
                    regime,
                    signal_type,
                    self.config,
                )
                risk_mult, lev_mult = risk_mgr.get_tiered_risk(quality_score)
                if risk_mult == 0.0:
                    # D-grade: skip this signal
                    continue
                # Scale position size: risk_mult adjusts the risk budget,
                # lev_mult adjusts the effective leverage cap.
                position_size *= risk_mult
                effective_max = self.state.balance * (self.config.get("leverage", 3) * lev_mult)
                position_size = min(position_size, effective_max)
            # --- End adaptive sizing ---

            # --- MTD Accelerator (Change 2) ---
            # When enabled, scales position size based on month-to-date performance.
            # Disabled by default; when disabled, mtd_mult=1.0 so behaviour is identical.
            mtd_cfg = self.config.get("mtd_accelerator", {})
            if mtd_cfg.get("enabled", False):
                mtd_start = self.state.mtd_start_balance
                mtd_return = (
                    self.state.mtd_pnl / mtd_start if mtd_start > 0 else 0.0
                )
                tiers = mtd_cfg.get("tiers", [
                    {"min_return": 0.20, "mult": 1.5},
                    {"min_return": 0.10, "mult": 1.3},
                    {"min_return": 0.00, "mult": 1.0},
                    {"min_return": -0.05, "mult": 0.8},
                    {"min_return": -999, "mult": 0.6},
                ])
                mtd_mult = 0.6  # fallback
                for tier in sorted(tiers, key=lambda t: t["min_return"], reverse=True):
                    if mtd_return >= tier["min_return"]:
                        mtd_mult = tier["mult"]
                        break
                position_size *= mtd_mult
                logger.debug(
                    "mtd_accelerator_applied",
                    extra={
                        "mtd_return": round(mtd_return, 4),
                        "mtd_mult": mtd_mult,
                        "position_size": round(position_size, 4),
                    },
                )
            # --- End MTD Accelerator ---

            # Weekend size reduction
            if is_weekend and weekend_trading:
                weekend_reduction = self.config.get("weekend_size_reduction", 0.5)
                position_size *= weekend_reduction

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
                signal_source=signal_source,
            )
            break  # Only one entry per candle

    def _check_exit(
        self, row: pd.Series, current_time: str, risk_mgr: RiskManager,
        candle_idx: int = 0,
    ) -> None:
        """Check exit conditions for current position.

        Args:
            row: Current candle.
            current_time: Current timestamp.
            risk_mgr: Risk manager instance.
            candle_idx: Current candle index for cooldown tracking.
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
                    self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr, candle_idx)
                else:
                    self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr, candle_idx)
                return
            if sl_hit:
                self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr, candle_idx)
                return
        else:
            sl_hit = high >= pos.stop_loss
            tp_hit = low <= pos.take_profit
            if sl_hit and tp_hit:
                if close < open_price:  # Bearish candle → TP filled first
                    self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr, candle_idx)
                else:
                    self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr, candle_idx)
                return
            if sl_hit:
                self._close_position(pos.stop_loss, current_time, "stop_loss", risk_mgr, candle_idx)
                return

        # --- Partial TP1 check (before full TP) ---
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

                # Move SL to breakeven + buffer after TP1
                if self.config.get("move_sl_to_be_after_tp1", True):
                    be_buffer = self.config.get("breakeven_buffer_atr_mult", 0.0)
                    atr_val = row.get("atr", 0.0) if pd.notna(row.get("atr")) else 0.0
                    buffer = be_buffer * atr_val
                    if pos.side == "long":
                        pos.stop_loss = max(pos.stop_loss, pos.entry_price + buffer)
                    else:
                        pos.stop_loss = min(pos.stop_loss, pos.entry_price - buffer)

                logger.debug(
                    "backtest_partial_tp1",
                    extra={
                        "side": pos.side,
                        "tp1_price": pos.tp1_price,
                        "partial_pnl": round(partial_pnl, 4),
                        "remaining_size": pos.size,
                    },
                )

        # --- Pyramiding: add to winning position (Feature 2) ---
        # Evaluated before full-TP so the add can happen on the same candle that
        # reaches the pyramid threshold (but position is only closed at full TP).
        pyramid_cfg = self.config.get("pyramiding", {})
        if (
            pyramid_cfg.get("enabled", False)
            and pos.pyramid_count < pyramid_cfg.get("max_adds", 2)
            and pd.notna(row.get("atr"))
        ):
            atr_val = row["atr"]
            # Determine which pyramid level we're evaluating next
            level = pos.pyramid_count + 1
            if level == 1:
                trigger_atr_mult = pyramid_cfg.get("add_1_atr_mult", 1.0)
                add_size_pct = pyramid_cfg.get("add_1_size_pct", 0.5)
                new_sl_offset = 0.0  # Move SL to breakeven
            elif level == 2:
                trigger_atr_mult = pyramid_cfg.get("add_2_atr_mult", 2.0)
                add_size_pct = pyramid_cfg.get("add_2_size_pct", 0.25)
                new_sl_offset = 0.5 * atr_val  # Entry + 0.5×ATR buffer
            elif level == 3:
                trigger_atr_mult = pyramid_cfg.get("add_3_atr_mult", pyramid_cfg.get("add_2_atr_mult", 2.0) + 0.5)
                add_size_pct = pyramid_cfg.get("add_3_size_pct", 0.25)
                new_sl_offset = 1.0 * atr_val  # Entry + 1.0×ATR lock profit
            elif level == 4:
                trigger_atr_mult = pyramid_cfg.get("add_4_atr_mult", 2.8)
                add_size_pct = pyramid_cfg.get("add_4_size_pct", 0.25)
                new_sl_offset = 1.5 * atr_val  # Entry + 1.5×ATR lock more profit
            else:  # level == 5
                trigger_atr_mult = pyramid_cfg.get("add_5_atr_mult", 3.5)
                add_size_pct = pyramid_cfg.get("add_5_size_pct", 0.25)
                new_sl_offset = 2.0 * atr_val  # Entry + 2.0×ATR lock maximum profit

            # Check trigger: unrealized profit >= N×ATR
            if pos.side == "long":
                unrealized = close - pos.entry_price
                # Trend still aligned: EMA(9) > EMA(21)
                trend_aligned = (
                    pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow"))
                    and row["ema_fast"] > row["ema_slow"]
                )
            else:
                unrealized = pos.entry_price - close
                trend_aligned = (
                    pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow"))
                    and row["ema_fast"] < row["ema_slow"]
                )

            if trend_aligned and unrealized >= trigger_atr_mult * atr_val:
                # Change 1: Size adds from current balance (compounds with account growth),
                # not from original_size (which was fixed at entry time).
                sl_pct = abs(pos.entry_price - pos.stop_loss) / pos.entry_price
                if sl_pct > 0:
                    add_risk = (
                        self.state.balance
                        * self.config.get("risk_per_trade", 0.03)
                        * add_size_pct
                    )
                    add_size = add_risk / sl_pct
                    # Cap at conservative leverage limit to avoid over-exposure
                    max_add = self.state.balance * self.config.get("leverage", 10) * 0.5
                    add_size = min(add_size, max_add)
                else:
                    add_size = 0.0

                if add_size <= 0:
                    pass  # Skip this add; do not update pyramid_count
                else:
                    # Apply commission on the add
                    commission_add = add_size * self.commission_rate
                    self.state.balance -= commission_add

                    pos.size += add_size
                    pos.pyramid_count += 1
                    pos.pyramid_sizes.append(round(add_size, 4))

                    # Move stop loss for pyramiding
                    if pos.side == "long":
                        new_sl = pos.entry_price + new_sl_offset
                        pos.stop_loss = max(pos.stop_loss, new_sl)
                    else:
                        new_sl = pos.entry_price - new_sl_offset
                        pos.stop_loss = min(pos.stop_loss, new_sl)

                    # Change 3: Dynamic TP extension when enough pyramid adds have occurred
                    tp_ext_min = pyramid_cfg.get("tp_extension_min_pyramids", 2)
                    tp_ext_mult = pyramid_cfg.get("tp_extension_atr_mult", 0.0)
                    if pos.pyramid_count >= tp_ext_min and tp_ext_mult > 0:
                        if pos.side == "long":
                            new_tp = pos.take_profit + tp_ext_mult * atr_val
                            pos.take_profit = max(pos.take_profit, new_tp)
                        else:
                            new_tp = pos.take_profit - tp_ext_mult * atr_val
                            pos.take_profit = min(pos.take_profit, new_tp)

                    logger.debug(
                        "backtest_pyramid_add",
                        extra={
                            "pyramid_count": pos.pyramid_count,
                            "add_size": round(add_size, 4),
                            "new_total_size": round(pos.size, 4),
                            "new_sl": round(pos.stop_loss, 4),
                            "new_tp": round(pos.take_profit, 4),
                            "unrealized_atr": round(unrealized / atr_val, 2),
                        },
                    )
        # --- End pyramiding ---

        # Full TP check (on remaining position)
        if pos.side == "long":
            if high >= pos.take_profit:
                self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr, candle_idx)
                return
        else:
            if low <= pos.take_profit:
                self._close_position(pos.take_profit, current_time, "take_profit", risk_mgr, candle_idx)
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
            # Use wider trail after TP1 hit
            if pos.tp1_hit:
                post_tp1_mult = self.config.get("atr_trail_mult_post_tp1")
                if post_tp1_mult is not None:
                    regime_trail_config["atr_trail_mult"] = post_tp1_mult
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
        candle_idx: int = 0,
    ) -> None:
        """Close the current position.

        Args:
            exit_price: Exit price.
            exit_time: Exit timestamp.
            reason: Close reason.
            risk_mgr: Risk manager instance.
            candle_idx: Current candle index for cooldown tracking.
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
        # MTD accelerator: accumulate month-to-date PnL (including partial already booked)
        self.state.mtd_pnl += total_pnl

        pos.exit_time = exit_time
        pos.exit_price = exit_price
        pos.pnl = total_pnl  # Store combined PnL for metrics
        pos.pnl_pct = pnl_pct
        pos.close_reason = reason
        self.state.trades.append(pos)
        self.state.position = None

        # Track candle index for cooldown
        self.state.last_close_candle_idx = candle_idx
        self.state.last_close_reason = reason

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
