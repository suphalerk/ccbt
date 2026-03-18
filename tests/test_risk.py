"""Tests for risk management module."""

import time

import pytest

from bot.risk import RiskManager, RiskState


@pytest.fixture
def config():
    """Standard test configuration."""
    return {
        "symbol": "BTCUSDT",
        "leverage": 3,
        "risk_per_trade": 0.005,
        "max_daily_loss": 0.02,
        "max_positions": 2,
        "min_rr_ratio": 2.0,
        "max_consecutive_losses": 3,
        "cooldown_hours": 2,
        "max_api_errors": 3,
    }


@pytest.fixture
def risk_mgr(config):
    """Create a RiskManager with $1000 balance."""
    return RiskManager(config, balance=1000.0)


class TestPositionSizing:
    """Test position sizing calculations."""

    def test_basic_position_size(self, risk_mgr):
        """Position size = risk_amount / sl_pct = (1000*0.005) / 0.01 = 500."""
        size = risk_mgr.calculate_position_size(1000.0, 0.01)
        assert size == pytest.approx(500.0)

    def test_small_sl_large_position(self, risk_mgr):
        """Small SL should produce larger position (capped by leverage)."""
        size = risk_mgr.calculate_position_size(1000.0, 0.001)
        # Risk = 5, size = 5/0.001 = 5000, but max = 1000*3 = 3000
        assert size == pytest.approx(3000.0)

    def test_leverage_cap(self, risk_mgr):
        """Position size should be capped at max leverage."""
        size = risk_mgr.calculate_position_size(1000.0, 0.0001)
        # Risk = 5, size = 5/0.0001 = 50000, but max = 1000*3 = 3000
        assert size == pytest.approx(3000.0)

    def test_zero_sl_raises(self, risk_mgr):
        """Zero stop loss percentage should raise ValueError."""
        with pytest.raises(ValueError):
            risk_mgr.calculate_position_size(1000.0, 0.0)

    def test_negative_sl_raises(self, risk_mgr):
        """Negative stop loss percentage should raise ValueError."""
        with pytest.raises(ValueError):
            risk_mgr.calculate_position_size(1000.0, -0.01)

    def test_position_size_scales_with_balance(self, risk_mgr):
        """Position size should scale proportionally with balance."""
        size_1k = risk_mgr.calculate_position_size(1000.0, 0.01)
        size_2k = risk_mgr.calculate_position_size(2000.0, 0.01)
        assert size_2k == pytest.approx(size_1k * 2)


class TestDailyLossLimit:
    """Test daily loss limit circuit breaker."""

    def test_daily_loss_triggers_halt(self, risk_mgr):
        """Trading should halt when daily loss reaches 2%."""
        # Lose $20 on a $1000 account = 2%
        risk_mgr.record_trade_result(-20.0)
        can, reason = risk_mgr.can_trade(980.0, 0)
        assert not can
        assert "Daily loss limit" in reason

    def test_partial_losses_accumulate(self, risk_mgr):
        """Multiple small losses should accumulate to trigger limit."""
        risk_mgr.record_trade_result(-10.0)
        can, _ = risk_mgr.can_trade(990.0, 0)
        assert can  # 1% loss, not at limit yet

        risk_mgr.record_trade_result(-10.0)
        can, reason = risk_mgr.can_trade(980.0, 0)
        assert not can  # 2% loss, now at limit

    def test_wins_offset_losses(self, risk_mgr):
        """Winning trades should offset daily losses."""
        risk_mgr.record_trade_result(-15.0)
        risk_mgr.record_trade_result(10.0)
        # Net PnL = -5 = 0.5%, under limit
        can, _ = risk_mgr.can_trade(995.0, 0)
        assert can

    def test_daily_reset_clears_halt(self, risk_mgr):
        """Daily reset should clear halt state."""
        risk_mgr.record_trade_result(-25.0)
        can, _ = risk_mgr.can_trade(975.0, 0)
        assert not can

        risk_mgr.reset_daily(975.0)
        can, _ = risk_mgr.can_trade(975.0, 0)
        assert can


class TestConsecutiveLosses:
    """Test consecutive loss circuit breaker."""

    def test_consecutive_losses_trigger_cooldown(self, risk_mgr):
        """3 consecutive losses should trigger cooldown."""
        risk_mgr.record_trade_result(-3.0)
        risk_mgr.record_trade_result(-3.0)
        risk_mgr.record_trade_result(-3.0)
        can, reason = risk_mgr.can_trade(991.0, 0)
        assert not can
        assert "Consecutive loss" in reason

    def test_win_resets_consecutive_count(self, risk_mgr):
        """A winning trade should reset the consecutive loss counter."""
        risk_mgr.record_trade_result(-3.0)
        risk_mgr.record_trade_result(-3.0)
        risk_mgr.record_trade_result(5.0)  # Win resets counter
        risk_mgr.record_trade_result(-3.0)
        can, _ = risk_mgr.can_trade(996.0, 0)
        assert can  # Only 1 consecutive loss


class TestMaxPositions:
    """Test max open positions limit."""

    def test_max_positions_blocks(self, risk_mgr):
        """Should block trading when max positions reached."""
        can, reason = risk_mgr.can_trade(1000.0, 2)
        assert not can
        assert "Max open positions" in reason

    def test_under_max_allows(self, risk_mgr):
        """Should allow trading when under max positions."""
        can, _ = risk_mgr.can_trade(1000.0, 1)
        assert can


class TestOrderValidation:
    """Test full order validation."""

    def test_valid_order_approved(self, risk_mgr):
        """Valid order with good R:R should be approved."""
        approved, reason, size = risk_mgr.validate_order(
            balance=1000.0,
            entry_price=60000.0,
            stop_loss=59400.0,
            take_profit=61200.0,
            num_open_positions=0,
        )
        assert approved
        assert size > 0

    def test_bad_rr_rejected(self, risk_mgr):
        """Order with R:R below minimum should be rejected."""
        approved, reason, _ = risk_mgr.validate_order(
            balance=1000.0,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit=60500.0,  # R:R = 0.5, below 2.0
            num_open_positions=0,
        )
        assert not approved
        assert "R:R ratio" in reason

    def test_sl_at_entry_rejected(self, risk_mgr):
        """Order with SL at entry price should be rejected."""
        approved, reason, _ = risk_mgr.validate_order(
            balance=1000.0,
            entry_price=60000.0,
            stop_loss=60000.0,
            take_profit=61000.0,
            num_open_positions=0,
        )
        assert not approved


class TestApiErrorCircuitBreaker:
    """Test API error circuit breaker."""

    def test_api_errors_halt(self, risk_mgr):
        """3 consecutive API errors should halt trading."""
        risk_mgr.record_api_error()
        risk_mgr.record_api_error()
        risk_mgr.record_api_error()
        can, reason = risk_mgr.can_trade(1000.0, 0)
        assert not can
        assert "API error" in reason

    def test_clear_resets_errors(self, risk_mgr):
        """Clearing API errors should reset the counter."""
        risk_mgr.record_api_error()
        risk_mgr.record_api_error()
        risk_mgr.clear_api_errors()
        risk_mgr.record_api_error()
        can, _ = risk_mgr.can_trade(1000.0, 0)
        assert can  # Only 1 error after reset


class TestLeverageCheck:
    """Test leverage verification."""

    def test_within_limit(self, risk_mgr):
        """Should pass when leverage is within limit."""
        assert risk_mgr.check_leverage(2000.0, 1000.0)  # 2x

    def test_exceeds_limit(self, risk_mgr):
        """Should fail when leverage exceeds limit."""
        assert not risk_mgr.check_leverage(4000.0, 1000.0)  # 4x > 3x

    def test_zero_balance(self, risk_mgr):
        """Should fail with zero balance."""
        assert not risk_mgr.check_leverage(1000.0, 0.0)


class TestDynamicRiskFactor:
    """Test dynamic risk factor based on recent win rate."""

    def test_insufficient_data_returns_conservative(self, config):
        """With fewer than 10 trades, should return 0.5."""
        rm = RiskManager(config, balance=100000.0)
        # No trades recorded
        assert rm.get_dynamic_risk_factor() == 0.5

        # Record 5 trades (still < 10)
        for _ in range(5):
            rm.record_trade_result(10.0)
        assert rm.get_dynamic_risk_factor() == 0.5

    def test_high_win_rate_returns_full(self, config):
        """Win rate > 50% should return 1.0."""
        rm = RiskManager(config, balance=100000.0)
        # Record 12 wins and 3 losses = 80% win rate
        for _ in range(12):
            rm.record_trade_result(10.0)
        for _ in range(3):
            rm.record_trade_result(-5.0)
        assert rm.get_dynamic_risk_factor() == 1.0

    def test_medium_win_rate_returns_reduced(self, config):
        """Win rate 40-50% should return 0.6."""
        rm = RiskManager(config, balance=100000.0)
        # Directly set recent_results: 5 wins and 7 losses = 41.6% win rate
        rm.state.recent_results = [
            10.0, -5.0, -5.0, 10.0, -5.0, -5.0, 10.0, -5.0, 10.0, -5.0, 10.0, -5.0
        ]
        factor = rm.get_dynamic_risk_factor()
        assert factor == 0.6

    def test_low_win_rate_returns_minimal(self, config):
        """Win rate < 40% should return 0.3."""
        rm = RiskManager(config, balance=100000.0)
        # Directly set recent_results to avoid triggering consecutive loss / daily loss limits
        # 3 wins out of 13 = 23% win rate
        rm.state.recent_results = [
            -5.0, -5.0, 10.0, -5.0, -5.0, 10.0, -5.0, -5.0, 10.0, -5.0, -5.0, -5.0, -5.0
        ]
        factor = rm.get_dynamic_risk_factor()
        assert factor == 0.3

    def test_dynamic_factor_applied_in_validate_order(self, config):
        """Dynamic risk factor should scale position size in validate_order."""
        # Create manager with no trade history (factor = 0.5)
        rm = RiskManager(config, balance=1000.0)
        _, _, size_conservative = rm.validate_order(
            balance=1000.0,
            entry_price=60000.0,
            stop_loss=59400.0,
            take_profit=61200.0,
            num_open_positions=0,
        )

        # Create another manager with high win rate (factor = 1.0)
        rm2 = RiskManager(config, balance=1000.0)
        for _ in range(15):
            rm2.record_trade_result(10.0)
        _, _, size_full = rm2.validate_order(
            balance=1000.0,
            entry_price=60000.0,
            stop_loss=59400.0,
            take_profit=61200.0,
            num_open_positions=0,
        )

        # Full risk should be ~2x conservative risk
        assert size_full == pytest.approx(size_conservative * 2.0, rel=0.01)

    def test_volatile_regime_reduces_position(self, config):
        """Volatile regime should further reduce position size by 50%."""
        rm = RiskManager(config, balance=1000.0)
        # With 0 trades, dynamic factor = 0.5
        _, _, size_trending = rm.validate_order(
            balance=1000.0,
            entry_price=60000.0,
            stop_loss=59400.0,
            take_profit=61200.0,
            num_open_positions=0,
            regime="trending",
        )

        rm2 = RiskManager(config, balance=1000.0)
        _, _, size_volatile = rm2.validate_order(
            balance=1000.0,
            entry_price=60000.0,
            stop_loss=59400.0,
            take_profit=61200.0,
            num_open_positions=0,
            regime="volatile",
        )

        # Volatile should be half of trending (additional 0.5x regime factor)
        assert size_volatile == pytest.approx(size_trending * 0.5, rel=0.01)
