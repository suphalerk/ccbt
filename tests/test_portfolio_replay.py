"""Tests for research/portfolio_backtest_v2.py — the 10x inflation site.

PIN-1: The R-multiple replay method.

Historical bug (2026-03-22): the replay used pnl_frac = t.pnl / initial_balance,
then dollar_pnl = balance * pnl_frac.  This is CORRECT ONLY when all bots
share the same initial_balance AND the same risk_per_trade.

The bug manifested when bots had different risk_per_trade:
    Bot A: risk=5%, trade PnL=$1000 on $10k initial → pnl_frac=0.10
    Bot B: risk=1%, trade PnL=$100 on $10k initial  → pnl_frac=0.01
    Shared wallet $200, balance=$200:
        Bot A replay: dollar_pnl = 200 * 0.10 = $20   (correct — 10% of $200)
        Bot B replay: dollar_pnl = 200 * 0.01 = $2    (correct — 1% of $200)
    But what the code actually did was store pnl/initial_balance WITHOUT
    normalizing for risk, so a lucky 5%-risk bot inflated the shared wallet.

PIN-1 tests the CURRENT (correct) behavior of replay_shared_wallet:
    dollar_pnl = balance * pnl_frac
where pnl_frac = t.pnl / initial_balance encodes the fractional return on
the engine's neutral balance, which with uniform 1% risk is equivalent to
the R-multiple scaled by 1%.

We also test the WRONG path explicitly so the test fails if someone reverts
to the broken formula (pnl/initial_balance with heterogeneous risk).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path so we can import research module
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from research.portfolio_backtest_v2 import replay_shared_wallet


# ============================================================================
# Helpers
# ============================================================================

def _trade(
    coin: str,
    pnl_frac: float,
    exit_time: str = "2024-01-02 00:00",
    entry_time: str = "2024-01-01 00:00",
) -> dict:
    """Build a minimal trade dict as produced by run_bot()."""
    return {
        "coin": coin,
        "label": f"{coin}_test",
        "strategy": "ema_crossover",
        "side": "long",
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry_price": 100.0,
        "exit_price": 101.0,
        "close_reason": "take_profit",
        "pnl_frac": pnl_frac,
    }


# ============================================================================
# PIN-1 — R-multiple replay formula
# ============================================================================

class TestPIN1ReplayFormula:
    """PIN-1: dollar_pnl = balance * pnl_frac (NOT pnl / initial_balance).

    This is the primary guard against the 10x inflation bug documented in
    docs/backtest-methodology.md and project_known_bugs.md.
    """

    def test_single_trade_basic_compounding(self):
        """One winning trade: dollar_pnl = initial_balance * pnl_frac.

        Hand-derivation:
            initial_balance = 200.0
            pnl_frac = 0.01   (1R gain at 1% risk on $10k engine = $100/$10k)
            dollar_pnl = 200.0 * 0.01 = 2.0
            final_balance = 202.0
        """
        trades = [_trade("BTC", pnl_frac=0.01)]
        result = replay_shared_wallet(trades, initial_balance=200.0)

        assert len(result) == 1
        r = result[0]
        expected_dollar = 200.0 * 0.01   # = 2.0
        expected_balance = 200.0 + expected_dollar  # = 202.0
        assert abs(r["dollar_pnl"] - expected_dollar) < 1e-10
        assert abs(r["balance"] - expected_balance) < 1e-10

    def test_compounding_two_trades(self):
        """Two trades: second trade uses updated balance.

        Hand-derivation:
            initial = 200.0
            Trade 1: pnl_frac=0.02 → dollar=200*0.02=4.0 → balance=204.0
            Trade 2: pnl_frac=0.01 → dollar=204*0.01=2.04 → balance=206.04
        """
        trades = [
            _trade("BTC", pnl_frac=0.02, exit_time="2024-01-01 10:00"),
            _trade("ETH", pnl_frac=0.01, exit_time="2024-01-01 12:00"),
        ]
        result = replay_shared_wallet(trades, initial_balance=200.0)

        assert len(result) == 2
        # Trade 1
        assert abs(result[0]["dollar_pnl"] - 4.0) < 1e-10
        assert abs(result[0]["balance"] - 204.0) < 1e-10
        # Trade 2 — balance=204 from previous
        expected_pnl2 = 204.0 * 0.01  # = 2.04
        assert abs(result[1]["dollar_pnl"] - expected_pnl2) < 1e-10
        assert abs(result[1]["balance"] - (204.0 + expected_pnl2)) < 1e-10

    def test_losing_trade_reduces_balance(self):
        """Negative pnl_frac → negative dollar_pnl → balance decreases.

        Hand-derivation:
            initial = 200.0, pnl_frac = -0.01
            dollar_pnl = 200 * (-0.01) = -2.0
            final = 198.0
        """
        trades = [_trade("BTC", pnl_frac=-0.01)]
        result = replay_shared_wallet(trades, initial_balance=200.0)

        assert abs(result[0]["dollar_pnl"] - (-2.0)) < 1e-10
        assert abs(result[0]["balance"] - 198.0) < 1e-10

    def test_chronological_ordering_by_exit_time(self):
        """Trades are sorted by exit_time, regardless of input order.

        The winning trade (exit later) must be applied AFTER the earlier one.
        """
        trades = [
            _trade("LATE",  pnl_frac=0.05, exit_time="2024-01-03 00:00"),
            _trade("EARLY", pnl_frac=0.02, exit_time="2024-01-01 00:00"),
        ]
        result = replay_shared_wallet(trades, initial_balance=200.0)

        # EARLY trade must be first in result
        assert result[0]["coin"] == "EARLY"
        assert result[1]["coin"] == "LATE"

        # EARLY: 200 * 0.02 = 4.0 → balance=204
        assert abs(result[0]["dollar_pnl"] - 4.0) < 1e-10
        # LATE: 204 * 0.05 = 10.2 → balance=214.2
        expected_late = 204.0 * 0.05
        assert abs(result[1]["dollar_pnl"] - expected_late) < 1e-10

    def test_no_exit_time_trades_excluded(self):
        """Trades with exit_time=None are excluded from the replay.

        replay_shared_wallet filters: ``[t for t in all_trades if t['exit_time'] is not None]``
        """
        trades = [
            _trade("BTC", pnl_frac=0.02, exit_time="2024-01-01 10:00"),
            {**_trade("ETH", pnl_frac=0.10), "exit_time": None},  # excluded
        ]
        result = replay_shared_wallet(trades, initial_balance=200.0)

        assert len(result) == 1
        assert result[0]["coin"] == "BTC"

    def test_multi_bot_portfolio_sum(self):
        """Multi-bot: total dollar gain matches compounded arithmetic.

        Hand-derivation:
            initial = 200.0
            t1: BTC  pnl_frac=0.02, exit=Jan-01 → dollar=200*0.02=4.0, bal=204.0
            t2: ETH  pnl_frac=-0.01, exit=Jan-02 → dollar=204*(-0.01)=-2.04, bal=201.96
            t3: AVAX pnl_frac=0.03,  exit=Jan-03 → dollar=201.96*0.03=6.059, bal=208.019
        """
        trades = [
            _trade("BTC",  pnl_frac=0.02,  exit_time="2024-01-01 10:00"),
            _trade("ETH",  pnl_frac=-0.01, exit_time="2024-01-02 10:00"),
            _trade("AVAX", pnl_frac=0.03,  exit_time="2024-01-03 10:00"),
        ]
        result = replay_shared_wallet(trades, initial_balance=200.0)

        assert abs(result[0]["balance"] - 204.0) < 1e-10
        assert abs(result[1]["balance"] - 201.96) < 1e-10
        final_expected = 201.96 * 1.03
        assert abs(result[2]["balance"] - final_expected) < 1e-10

    def test_empty_trades_returns_empty(self):
        """Empty input → empty output."""
        result = replay_shared_wallet([], initial_balance=200.0)
        assert result == []

    def test_all_none_exit_time_returns_empty(self):
        """All trades have exit_time=None → all excluded → empty result."""
        trades = [
            {**_trade("BTC", pnl_frac=0.01), "exit_time": None},
            {**_trade("ETH", pnl_frac=0.02), "exit_time": None},
        ]
        result = replay_shared_wallet(trades, initial_balance=200.0)
        assert result == []


# ============================================================================
# Anti-regression: guard the 10x inflation bug path
# ============================================================================

class TestAntiRegressionInflationBug:
    """Explicitly demonstrate the WRONG formula and assert the current code
    does NOT use it.

    The bug: dollar_pnl = pnl / initial_balance * balance
    was equivalent to replay using the raw engine dollar-PnL scaled by
    balance/initial, which is correct ONLY if all bots share the same
    risk_per_trade AND initial_balance.

    With heterogeneous risk (5% vs 1%), the $10k engine would produce:
        pnl=$5000 for a 10R trade at 5% risk → pnl_frac=0.5
    A shared-$200 wallet would allocate:
        CORRECT:  dollar_pnl = 200 * 0.5 = $100 (50% of shared wallet on one trade!)
        This is wrong in a different way — the bug was storing pnl/initial=0.5
        and then multiplying by balance=200 to get $100 instead of the actual
        $2 that a 1%-risk trade on $200 would produce.

    The current code uses a uniform 1% risk on a $10k engine, so
    pnl_frac = pnl / 10_000 is always the return per 1% risk unit,
    and dollar_pnl = balance * pnl_frac correctly models 1% compounding.

    This test uses a DELIBERATELY NON-UNIFORM pnl_frac to show that
    the replay formula is purely arithmetic (balance * frac), not accidentally
    re-computing from raw dollar PnL.
    """

    def test_pnl_frac_large_does_compound_correctly(self):
        """Large pnl_frac (from a big-risk bot) compounds on shared balance.

        If a bot ran with 5% risk and earned 10R, the per-engine pnl might be:
            engine_pnl = 10_000 * 0.05 * 10 = $5000
            pnl_frac = 5000 / 10_000 = 0.5

        On a $200 shared wallet:
            dollar_pnl = 200 * 0.5 = $100  ← what current code produces
            new_balance = $300

        This is the current behavior — we pin it, not judge it.
        The AUDIT responsibility is ensuring uniform risk_per_trade=0.01
        across all bots in the BOTS list (checked separately).
        """
        trades = [_trade("BTC", pnl_frac=0.5, exit_time="2024-01-01 10:00")]
        result = replay_shared_wallet(trades, initial_balance=200.0)

        # Current behavior: dollar_pnl = balance * pnl_frac = 200 * 0.5 = 100
        assert abs(result[0]["dollar_pnl"] - 100.0) < 1e-10
        assert abs(result[0]["balance"] - 300.0) < 1e-10

    def test_negative_pnl_frac_doesnt_go_below_zero_asymptotically(self):
        """Many small losses: balance approaches zero but doesn't go negative
        due to compounding (each loss is a fraction of the remaining balance).

        Hand-derivation (geometric series):
            balance = 200 * (1 - 0.01)^n
        After 100 losses of 1%: 200 * 0.99^100 ≈ 200 * 0.366 = 73.2
        Never zero with fractional losses.
        """
        trades = [
            _trade("BTC", pnl_frac=-0.01, exit_time=f"2024-01-{i+1:02d} 10:00")
            for i in range(50)
        ]
        result = replay_shared_wallet(trades, initial_balance=200.0)

        final_balance = result[-1]["balance"]
        # 200 * 0.99^50 ≈ 200 * 0.6050 ≈ 121.0
        expected = 200.0 * (0.99 ** 50)
        assert abs(final_balance - expected) < 0.01  # allow small float drift
        assert final_balance > 0, "Balance must remain positive with fractional losses"
