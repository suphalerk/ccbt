"""Risk management: position sizing, daily loss limits, and circuit breakers."""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    """Tracks risk management state across the trading session."""

    daily_pnl: float = 0.0
    starting_balance: float = 0.0
    consecutive_losses: int = 0
    api_error_count: int = 0
    cooldown_until: float = 0.0
    is_halted: bool = False
    halt_reason: str = ""
    trades_today: list = field(default_factory=list)
    recent_results: list = field(default_factory=list)  # Last N trade W/L for dynamic sizing


class RiskManager:
    """Enforces risk limits and position sizing rules.

    All orders MUST pass through this manager before execution.
    """

    def __init__(self, config: dict, balance: float) -> None:
        """Initialize risk manager.

        Args:
            config: Bot configuration dictionary.
            balance: Current portfolio balance in USDT.
        """
        self.config = config
        self.state = RiskState(starting_balance=balance)
        self.max_daily_loss = config["max_daily_loss"]
        self.risk_per_trade = config["risk_per_trade"]
        self.max_leverage = config["leverage"]
        self.max_positions = config["max_positions"]
        self.min_rr_ratio = config.get("min_rr_ratio", 2.0)
        self.max_consecutive_losses = config.get("max_consecutive_losses", 3)
        self.cooldown_hours = config.get("cooldown_hours", 2)
        self.max_api_errors = config.get("max_api_errors", 3)

    def calculate_position_size(
        self, balance: float, stop_loss_pct: float
    ) -> float:
        """Calculate position size using the fixed-risk formula.

        Formula: position_size = (balance * risk_per_trade) / stop_loss_pct

        Args:
            balance: Current portfolio balance.
            stop_loss_pct: Stop loss distance as a decimal (e.g., 0.01 for 1%).

        Returns:
            Position size in USDT.

        Raises:
            ValueError: If stop_loss_pct is zero or negative.
        """
        if stop_loss_pct <= 0:
            raise ValueError(f"Stop loss percentage must be positive, got {stop_loss_pct}")

        risk_amount = balance * self.risk_per_trade
        position_size = risk_amount / stop_loss_pct

        # Enforce max leverage
        max_position = balance * self.max_leverage
        if position_size > max_position:
            position_size = max_position
            logger.warning(
                "position_size_capped",
                extra={
                    "reason": "max_leverage",
                    "max_leverage": self.max_leverage,
                    "capped_size": position_size,
                },
            )

        logger.info(
            "position_size_calculated",
            extra={
                "balance": balance,
                "risk_amount": risk_amount,
                "sl_pct": stop_loss_pct,
                "position_size": position_size,
                "effective_leverage": round(position_size / balance, 2),
            },
        )
        return position_size

    def get_dynamic_risk_factor(self) -> float:
        """Adjust risk based on recent performance using a continuous linear scale.

        Examines the last 20 trade results and returns a smooth multiplier
        for position sizing based on win rate. Replaces the previous stepped
        function (0.7/0.85/1.0) with a continuous linear interpolation:
        0.5 at 30% win rate, 1.0 at 50% win rate, clamped to [0.5, 1.0].

        Returns:
            Multiplier between 0.5 and 1.0 for position sizing.
        """
        results = self.state.recent_results
        if len(results) < 10:
            return 1.0  # Full size until enough data (no early-trade drag)

        # Use last 20 results (or all if fewer)
        recent = results[-20:]
        wins = sum(1 for r in recent if r > 0)
        win_rate = wins / len(recent)

        # Linear scale: 0.5 at 30% WR, 1.0 at 50% WR, capped at [0.5, 1.0]
        factor = 0.5 + (win_rate - 0.30) * (0.5 / 0.20)
        return max(0.5, min(1.0, factor))

    def can_trade(
        self,
        current_balance: float,
        num_open_positions: int,
        current_time: Optional[float] = None,
    ) -> tuple[bool, str]:
        """Check if trading is allowed based on all risk rules.

        Args:
            current_balance: Current portfolio balance.
            num_open_positions: Number of currently open positions.
            current_time: Unix timestamp to use for cooldown checks.
                If None, uses wall-clock time.time(). Pass simulated
                time for backtesting.

        Returns:
            Tuple of (can_trade, reason). reason is empty if can_trade is True.
        """
        now = current_time if current_time is not None else time.time()

        # Check if halted
        if self.state.is_halted:
            return False, f"Trading halted: {self.state.halt_reason}"

        # Check cooldown
        if now < self.state.cooldown_until:
            remaining = int(self.state.cooldown_until - now)
            return False, f"In cooldown period, {remaining}s remaining"
        elif self.state.cooldown_until > 0:
            # Cooldown just expired — reset consecutive losses so trading can resume
            self.state.consecutive_losses = 0
            self.state.cooldown_until = 0.0

        # Check daily loss limit
        daily_loss_pct = abs(self.state.daily_pnl) / self.state.starting_balance if self.state.starting_balance > 0 else 0
        if self.state.daily_pnl < 0 and daily_loss_pct >= self.max_daily_loss:
            self._halt("Daily loss limit reached: {:.2%}".format(daily_loss_pct))
            return False, self.state.halt_reason

        # Check max open positions
        if num_open_positions >= self.max_positions:
            return False, f"Max open positions reached ({self.max_positions})"

        # Check consecutive losses
        if self.state.consecutive_losses >= self.max_consecutive_losses:
            cooldown_seconds = self.cooldown_hours * 3600
            self.state.cooldown_until = now + cooldown_seconds
            # Don't reset consecutive_losses here — only reset on a win
            # (record_trade_result resets to 0 on positive PnL)
            return False, f"Consecutive loss limit ({self.max_consecutive_losses}), cooling down {self.cooldown_hours}h"

        return True, ""

    def validate_order(
        self,
        balance: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        num_open_positions: int,
        regime: str = "trending",
        current_time: Optional[float] = None,
    ) -> tuple[bool, str, float]:
        """Validate an order against all risk rules and compute position size.

        Args:
            balance: Current balance.
            entry_price: Planned entry price.
            stop_loss: Stop loss price.
            take_profit: Take profit price.
            num_open_positions: Current open position count.
            regime: Market regime for sizing adjustments.
            current_time: Unix timestamp for cooldown checks (None = wall-clock).

        Returns:
            Tuple of (approved, reason, position_size).
        """
        # Check if we can trade
        can, reason = self.can_trade(balance, num_open_positions, current_time=current_time)
        if not can:
            logger.warning("order_rejected", extra={"reason": reason})
            return False, reason, 0.0

        # Validate R:R ratio (gross check as safety net)
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        if risk == 0:
            return False, "Stop loss at entry price", 0.0

        rr_ratio = reward / risk
        # Use gross R:R with a lower threshold as safety net
        # (strategy.py already validates net R:R before reaching here)
        if rr_ratio < self.min_rr_ratio * 0.8:
            reason = f"R:R ratio {rr_ratio:.2f} below minimum {self.min_rr_ratio}"
            logger.warning("order_rejected", extra={"reason": reason})
            return False, reason, 0.0

        # Calculate position size
        sl_pct = risk / entry_price
        position_size = self.calculate_position_size(balance, sl_pct)

        # Apply dynamic risk factor based on recent win rate
        dynamic_factor = self.get_dynamic_risk_factor()
        position_size *= dynamic_factor

        # Reduce size in non-trending regimes
        regime_factor = 1.0
        if regime == "volatile":
            regime_factor = 0.5
        elif regime == "ranging":
            regime_factor = 0.7
        position_size *= regime_factor

        logger.info(
            "order_validated",
            extra={
                "entry": entry_price,
                "sl": stop_loss,
                "tp": take_profit,
                "rr": round(rr_ratio, 2),
                "size_usdt": round(position_size, 2),
                "dynamic_risk_factor": dynamic_factor,
                "regime_factor": regime_factor,
                "regime": regime,
            },
        )
        return True, "", position_size

    def record_trade_result(self, pnl: float) -> None:
        """Record a trade result and update risk state.

        Args:
            pnl: Profit/loss in USDT (negative for loss).
        """
        self.state.daily_pnl += pnl
        self.state.trades_today.append(pnl)
        self.state.recent_results.append(pnl)
        # Keep only last 20 results
        if len(self.state.recent_results) > 20:
            self.state.recent_results = self.state.recent_results[-20:]

        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        daily_pct = self.state.daily_pnl / self.state.starting_balance if self.state.starting_balance > 0 else 0

        logger.info(
            "trade_result_recorded",
            extra={
                "pnl": pnl,
                "daily_pnl": self.state.daily_pnl,
                "daily_pnl_pct": round(daily_pct, 4),
                "consecutive_losses": self.state.consecutive_losses,
            },
        )

        # Check if daily loss limit hit
        if self.state.daily_pnl < 0 and abs(daily_pct) >= self.max_daily_loss:
            self._halt("Daily loss limit reached: {:.2%}".format(abs(daily_pct)))

    def record_api_error(self) -> None:
        """Record an API error. Halts trading after max_api_errors consecutive errors."""
        self.state.api_error_count += 1
        if self.state.api_error_count >= self.max_api_errors:
            self._halt(f"API error limit reached ({self.max_api_errors} consecutive)")

    def clear_api_errors(self) -> None:
        """Reset the API error counter after a successful API call."""
        self.state.api_error_count = 0

    def check_leverage(self, position_value: float, balance: float) -> bool:
        """Check if actual leverage exceeds maximum.

        Args:
            position_value: Total position value.
            balance: Current balance.

        Returns:
            True if leverage is within limits.
        """
        if balance <= 0:
            return False
        actual_leverage = position_value / balance
        if actual_leverage > self.max_leverage:
            logger.warning(
                "leverage_exceeded",
                extra={
                    "actual": round(actual_leverage, 2),
                    "max": self.max_leverage,
                },
            )
            return False
        return True

    def reset_daily(self, new_balance: float) -> None:
        """Reset daily counters. Call at the start of each trading day.

        Args:
            new_balance: Current balance to use as the new starting balance.
        """
        self.state.daily_pnl = 0.0
        self.state.starting_balance = new_balance
        self.state.trades_today = []
        self.state.is_halted = False
        self.state.halt_reason = ""
        self.state.api_error_count = 0
        # consecutive_losses intentionally NOT reset here — only resets on a winning trade
        self.state.cooldown_until = 0.0
        logger.info("daily_reset", extra={"new_balance": new_balance})

    def get_tiered_risk(self, quality_score: float) -> tuple[float, float]:
        """Return (risk_per_trade, leverage_multiplier) based on adaptive sizing tiers.

        Uses the 'adaptive_sizing' config block to map a signal quality score to
        a risk multiplier and a leverage multiplier.  When adaptive sizing is
        disabled the method returns the base values (1.0 multipliers) so callers
        can always use its output unconditionally.

        Args:
            quality_score: Signal quality score in [0.0, 1.0] from
                ``compute_signal_quality_score()``.

        Returns:
            Tuple of (risk_per_trade_multiplier, leverage_multiplier).
            Both values are floats ≥ 0.0.
        """
        adaptive_cfg = self.config.get("adaptive_sizing", {})
        if not adaptive_cfg.get("enabled", False):
            return 1.0, 1.0

        tiers = adaptive_cfg.get("tiers", {})
        min_quality = adaptive_cfg.get("min_quality_for_trade", 0.3)

        # Below minimum — caller should skip the trade
        if quality_score < min_quality:
            return 0.0, 0.0

        # Evaluate tiers from highest to lowest
        tier_a = tiers.get("A", {})
        tier_b = tiers.get("B", {})
        tier_c = tiers.get("C", {})

        if quality_score >= tier_a.get("min_score", 0.75):
            return tier_a.get("risk_mult", 2.0), tier_a.get("leverage_mult", 1.5)
        if quality_score >= tier_b.get("min_score", 0.5):
            return tier_b.get("risk_mult", 1.0), tier_b.get("leverage_mult", 1.0)
        if quality_score >= tier_c.get("min_score", 0.3):
            return tier_c.get("risk_mult", 0.5), tier_c.get("leverage_mult", 0.8)

        # Between min_quality and lowest tier — treat as C
        return tier_c.get("risk_mult", 0.5), tier_c.get("leverage_mult", 0.8)

    def _halt(self, reason: str) -> None:
        """Halt all trading.

        Args:
            reason: Reason for halting.
        """
        self.state.is_halted = True
        self.state.halt_reason = reason
        logger.warning("trading_halted", extra={"reason": reason})
