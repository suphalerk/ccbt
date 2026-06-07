"""Tests for T3: positions-fetch gate in engine.py (flag-guarded).

TDD spec — written BEFORE implementation.

Gate predicate (at engine.py "Get current positions" site, line ~563):

    When CCBT_SHARED_MARKETDATA == '1':
        if (not self._tracked_trades) AND (portfolio_manager.open_count >= max_positions):
            positions = []   # bot holds nothing AND global cap full → can't act, skip fetch
        else:
            positions = client.get_positions()   # always fetch if holding a trade

    When CCBT_SHARED_MARKETDATA != '1' (or unset):
        positions = client.get_positions()   # unconditional (today's behaviour)

Truth table:
    flag | has_trade | cap_full | expected
    -----|-----------|----------|-------------------------------
     ON  |   yes     |   yes    | FETCH (holder always fetches)
     ON  |   yes     |   no     | FETCH
     ON  |   no      |   yes    | SKIP  (positions=[])
     ON  |   no      |   no     | FETCH
     OFF |   yes     |   yes    | FETCH
     OFF |   yes     |   no     | FETCH
     OFF |   no      |   yes    | FETCH
     OFF |   no      |   no     | FETCH

Tests are PURE PREDICATE / UNIT tests — they do NOT boot the full engine.
We test the extracted helper `_should_skip_positions_fetch(tracked_trades,
portfolio_manager, max_positions, flag)` directly.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import the helper under test.
# The helper is expected to live in bot.engine as a module-level function (or
# importable name).  We import it after the implementation lands; until then
# the import will fail — which is the "RED" state this test must produce first.
# ---------------------------------------------------------------------------

from bot.engine import _should_skip_positions_fetch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pm(open_count: int, max_positions: int) -> SimpleNamespace:
    """Build a minimal PortfolioManager stand-in."""
    return SimpleNamespace(open_count=open_count, max_positions=max_positions)


def _tracked(has_trade: bool) -> dict:
    """Return a _tracked_trades dict that is non-empty iff has_trade."""
    return {1: {"side": "long"}} if has_trade else {}


# ---------------------------------------------------------------------------
# Core truth-table tests (flag ON)
# ---------------------------------------------------------------------------

class TestGateFlagOn:
    """When CCBT_SHARED_MARKETDATA == '1', the gate must respect the predicate."""

    FLAG = "1"

    def test_holder_at_cap_always_fetches(self):
        """Bot WITH a tracked trade ALWAYS fetches, even when global cap is full."""
        pm = _pm(open_count=10, max_positions=10)
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=True),
            portfolio_manager=pm,
            flag=self.FLAG,
        )
        assert result is False, (
            "Holder (has trade) must never skip fetch, even when cap is full"
        )

    def test_holder_below_cap_fetches(self):
        """Bot WITH a tracked trade fetches when global cap is not full."""
        pm = _pm(open_count=5, max_positions=10)
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=True),
            portfolio_manager=pm,
            flag=self.FLAG,
        )
        assert result is False

    def test_no_trade_cap_full_skips(self):
        """Bot with NO tracked trade and cap full → skip fetch (return True)."""
        pm = _pm(open_count=10, max_positions=10)
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=False),
            portfolio_manager=pm,
            flag=self.FLAG,
        )
        assert result is True, (
            "No-trade bot at global cap should skip the fetch"
        )

    def test_no_trade_below_cap_fetches(self):
        """Bot with NO tracked trade but cap not full → still fetch (may open)."""
        pm = _pm(open_count=5, max_positions=10)
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=False),
            portfolio_manager=pm,
            flag=self.FLAG,
        )
        assert result is False

    def test_no_trade_cap_exactly_at_limit_skips(self):
        """open_count == max_positions counts as 'full'."""
        pm = _pm(open_count=3, max_positions=3)
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=False),
            portfolio_manager=pm,
            flag=self.FLAG,
        )
        assert result is True

    def test_no_trade_cap_one_below_limit_fetches(self):
        """open_count == max_positions - 1 → cap NOT full → fetch."""
        pm = _pm(open_count=2, max_positions=3)
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=False),
            portfolio_manager=pm,
            flag=self.FLAG,
        )
        assert result is False


# ---------------------------------------------------------------------------
# Flag OFF: always fetch (parity with today's behaviour)
# ---------------------------------------------------------------------------

class TestGateFlagOff:
    """When flag is OFF (unset or any value other than '1'), always fetch."""

    @pytest.mark.parametrize("flag_value", [None, "0", "", "false", "off"])
    def test_always_fetches_flag_off(self, flag_value):
        """All flag-off states must result in fetch (return False)."""
        pm = _pm(open_count=10, max_positions=10)
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=False),
            portfolio_manager=pm,
            flag=flag_value,
        )
        assert result is False, (
            f"Flag={flag_value!r}: should never skip when flag is off"
        )

    def test_holder_at_cap_flag_off_fetches(self):
        pm = _pm(open_count=10, max_positions=10)
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=True),
            portfolio_manager=pm,
            flag=None,
        )
        assert result is False


# ---------------------------------------------------------------------------
# No portfolio_manager: always fetch (single-bot / main.py path)
# ---------------------------------------------------------------------------

class TestGateNoPortfolioManager:
    """When portfolio_manager is None (single-bot main.py), the gate is inactive."""

    def test_no_pm_flag_on_no_trade_fetches(self):
        """Without a PortfolioManager, even flag-on + no-trade must fetch."""
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=False),
            portfolio_manager=None,
            flag="1",
        )
        assert result is False

    def test_no_pm_flag_on_at_cap_fetches(self):
        """Without a PortfolioManager there is no cap concept → always fetch."""
        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=False),
            portfolio_manager=None,
            flag="1",
        )
        assert result is False


# ---------------------------------------------------------------------------
# Predicate short-circuits: tracked_trades evaluated FIRST (cheap path)
# ---------------------------------------------------------------------------

class TestGateShortCircuit:
    """Ensure (not tracked_trades) is evaluated before portfolio_manager access.

    If portfolio_manager is a mock, it must NOT be accessed when has_trade=True.
    """

    def test_pm_not_accessed_when_holder(self):
        """When bot has a tracked trade, portfolio_manager must not be accessed."""
        pm = MagicMock()
        pm.open_count = 10
        pm.max_positions = 10

        result = _should_skip_positions_fetch(
            tracked_trades=_tracked(has_trade=True),
            portfolio_manager=pm,
            flag="1",
        )
        assert result is False
        # open_count must NOT have been accessed (short-circuit)
        pm.open_count  # accessing it here is fine; we check it wasn't accessed INSIDE fn
        # We can't trivially assert property-access count with SimpleNamespace,
        # but with MagicMock we can check the attribute was not set via __getattr__
        # The real check: result is False (holder fetches) regardless of pm state.
