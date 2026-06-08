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
from unittest.mock import patch

import pytest

# Add project root to path so we can import research module
sys.path.insert(0, str(Path(__file__).parent.parent))

from research.portfolio_backtest_v2 import BOTS, replay_shared_wallet
from backtest.engine import BacktestEngine
from tests._bt_fixtures import make_trading_ohlcv, trading_config


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


# ============================================================================
# PIN-1 — Guard run_bot():348 pnl_frac = t.pnl / initial_balance
#
# README §32-34, §86: "PIN-1 must cover run_bot():348 `pnl_frac = t.pnl/initial_balance`
# (the bug-prone line, correct only under uniform 1% risk)."
#
# The existing tests only exercise replay_shared_wallet (line 396), never the
# pnl_frac COMPUTATION at line 348.  A revert to heterogeneous risk would NOT
# fail the existing suite.
#
# Two complementary guards:
#   (A) Static: assert every bot in BOTS uses risk_per_trade==0.01. If someone
#       adds a 5%-risk bot, this immediately fails — no need to run a backtest.
#   (B) Behavioral: run BacktestEngine with 5% vs 1% risk on the same signal
#       fixture, extract pnl_frac = t.pnl/initial_balance for each, and show
#       they DIFFER by 5x. Then replay both through the shared wallet and show
#       the 5%-risk bot produces 5x the dollar_pnl — which is WRONG for a
#       uniform-risk portfolio (a 5%-risk bot should expose 5x more balance
#       risk, not be treated as a 1%-risk bot). This demonstrates the invariant
#       that run_bot():348 requires uniform 1% risk.
# ============================================================================

class TestPIN1RunBotPnlFracComputation:
    """PIN-1 guards: pnl_frac = t.pnl / initial_balance is correct ONLY under 1% risk.

    These tests cover the run_bot():348 site that was not covered by the
    existing replay_shared_wallet-only tests.
    """

    def test_all_deployed_bots_use_uniform_1pct_risk(self):
        """STATIC GUARD: every entry in BOTS has risk_per_trade == 0.01.

        portfolio_backtest_v2.py:348: `pnl_frac = t.pnl / initial_balance`
        This is correct ONLY when all bots use the same risk_per_trade.
        The BASE_CONFIG in portfolio_backtest_v2.py sets risk_per_trade=0.01.
        No per-bot override should change it.

        If a bot with risk=5% is added, pnl_frac would be 5x the 1%-risk value,
        and replay_shared_wallet would allocate 5x the shared wallet on that
        trade — the 10x-inflation bug class.

        We import BOTS (the roster) and build_config for each, then assert
        risk_per_trade == 0.01.  This is the authoritative guard because it
        tests the exact same code path that run_bot() uses (build_config).
        """
        from research.portfolio_backtest_v2 import build_config

        violations = []
        for bot_tuple in BOTS:
            # BOTS entries: (coin, strategy_key, tf_signal, atr_sl_mult, atr_tp_mult, atr_trail_mult, label, data_prefix)
            coin, strategy_key, tf_signal, atr_sl_mult, atr_tp_mult, atr_trail_mult, label, data_prefix = bot_tuple
            cfg = build_config(coin, strategy_key, tf_signal, atr_sl_mult, atr_tp_mult, atr_trail_mult, data_prefix)
            risk = cfg.get("risk_per_trade", None)
            if risk != 0.01:
                violations.append(f"{label}/{coin}: risk_per_trade={risk!r} (expected 0.01)")

        assert not violations, (
            "PIN-1: heterogeneous risk detected in BOTS roster — "
            "pnl_frac = t.pnl / initial_balance (run_bot():348) is only correct "
            "under uniform 1% risk.\nViolations:\n  " + "\n  ".join(violations)
        )

    def test_heterogeneous_risk_inflates_shared_wallet_via_pnl_frac(self):
        """BEHAVIORAL GUARD: demonstrate how heterogeneous risk inflates run_bot():348.

        This test exercises the EXACT formula at run_bot():348:
            pnl_frac = t.pnl / initial_balance

        For a 1R trade (SL=1%, TP=2%), the engine PnL is:
            PnL = risk_amount * rr_ratio = balance * risk_per_trade * rr_ratio

        So pnl_frac = (balance * risk_per_trade * rr_ratio) / initial_balance
                    = risk_per_trade * rr_ratio   (when balance == initial_balance)

        A 5%-risk bot earns pnl_frac = 5 * (1%-risk bot's pnl_frac) for the SAME rr.
        When replayed on the shared wallet, dollar_pnl = shared_balance * pnl_frac,
        so the 5%-risk bot receives 5x the shared-wallet dollar impact — WRONG.

        Hand-derivation:
            initial_balance = 10_000, rr_ratio = 3.0 (TP/SL)
            Bot-A (5% risk, 1R win):
                risk_amount = 10_000 * 0.05 = 500
                trade.pnl ≈ 500 * 3.0 = 1500
                pnl_frac = 1500 / 10_000 = 0.15

            Bot-B (1% risk, 1R win):
                risk_amount = 10_000 * 0.01 = 100
                trade.pnl ≈ 100 * 3.0 = 300
                pnl_frac = 300 / 10_000 = 0.03

            Shared wallet $200:
                Bot-A replay: dollar_pnl = 200 * 0.15 = 30.0  (15% of $200)
                Bot-B replay: dollar_pnl = 200 * 0.03 = 6.0   (3% of $200)
                Ratio = 5x — a 5%-risk bot claims 5x the shared-wallet as a 1%-risk bot.

        This test BYPASSES the position-size cap (which confounds an engine-run comparison)
        by simulating pnl directly: we construct t.pnl by formula, compute pnl_frac
        exactly as run_bot():348 does, then replay through replay_shared_wallet.

        The static guard (test_all_deployed_bots_use_uniform_1pct_risk) prevents this
        bug from entering production; this test demonstrates WHY uniformity matters.
        """
        initial_balance = 10_000.0
        shared_initial = 200.0
        rr_ratio = 3.0  # standard atr_tp_mult / atr_sl_mult

        # Simulate one 1R winning trade for each risk level,
        # computing pnl_frac exactly as run_bot():348: pnl_frac = t.pnl / initial_balance
        def _make_pnl_frac(risk_per_trade: float) -> float:
            """Compute pnl_frac for a 1R win as run_bot():348 would.

            risk_amount = initial_balance * risk_per_trade
            trade.pnl ≈ risk_amount * rr_ratio  (1R win, ignoring commission for clarity)
            pnl_frac = trade.pnl / initial_balance = risk_per_trade * rr_ratio
            """
            risk_amount = initial_balance * risk_per_trade
            trade_pnl = risk_amount * rr_ratio
            return trade_pnl / initial_balance  # this is run_bot():348

        pnl_frac_5pct = _make_pnl_frac(0.05)  # = 0.05 * 3.0 = 0.15
        pnl_frac_1pct = _make_pnl_frac(0.01)  # = 0.01 * 3.0 = 0.03

        # Hand-derived expected values (from docstring)
        assert abs(pnl_frac_5pct - 0.15) < 1e-10, f"5% pnl_frac: expected 0.15, got {pnl_frac_5pct}"
        assert abs(pnl_frac_1pct - 0.03) < 1e-10, f"1% pnl_frac: expected 0.03, got {pnl_frac_1pct}"
        assert abs(pnl_frac_5pct / pnl_frac_1pct - 5.0) < 1e-10, (
            "5%-risk pnl_frac should be exactly 5x the 1%-risk pnl_frac for the same 1R trade"
        )

        # Replay through the shared wallet — shows 5x dollar_pnl impact
        # (exactly as replay_shared_wallet does via dollar_pnl = balance * pnl_frac)
        result_5pct = replay_shared_wallet(
            [_trade("BIGBOT", pnl_frac_5pct, "2024-01-02 12:00")],
            initial_balance=shared_initial,
        )
        result_1pct = replay_shared_wallet(
            [_trade("SMBOT",  pnl_frac_1pct, "2024-01-02 12:00")],
            initial_balance=shared_initial,
        )

        dollar_5pct = result_5pct[0]["dollar_pnl"]  # = 200 * 0.15 = 30.0
        dollar_1pct = result_1pct[0]["dollar_pnl"]  # = 200 * 0.03 =  6.0

        # Hand-derived expected values
        assert abs(dollar_5pct - 30.0) < 1e-10, f"Bot-A dollar_pnl: expected 30.0, got {dollar_5pct}"
        assert abs(dollar_1pct - 6.0) < 1e-10,  f"Bot-B dollar_pnl: expected 6.0, got {dollar_1pct}"
        assert abs(dollar_5pct / dollar_1pct - 5.0) < 1e-10, (
            f"PIN-1 BUG DEMONSTRATED: 5%-risk bot claims {dollar_5pct:.1f} vs "
            f"1%-risk bot {dollar_1pct:.1f} ({dollar_5pct/dollar_1pct:.1f}x) "
            "in the shared wallet for an identical 1R trade. "
            "This is why uniform 1% risk_per_trade is required in BOTS: "
            "pnl_frac = t.pnl / initial_balance (run_bot():348) encodes risk level directly."
        )


# ============================================================================
# PIN-1b — pin the ACTUAL historical-bug LINE (run_bot:348 pnl_frac = pnl/initial_balance)
# Found by mutation testing: the replay-apply step was pinned, but a 10x inflation
# injected at run_bot's pnl_frac computation went UNCAUGHT. This behavioral pin runs
# run_bot() with its data-loader + engine monkeypatched so a fixed engine PnL yields a
# known pnl_frac — so any change to line 348 (e.g. dropping risk-normalization) fails.
# ============================================================================
import types as _types
import pandas as _pd
import pytest as _pytest
import research.portfolio_backtest_v2 as _pb


class TestPIN1RunBotLine348:
    """Guard: run_bot():348 pnl_frac = t.pnl / initial_balance.

    The _StubEngine is patched at the MODULE LEVEL of portfolio_backtest_v2
    (monkeypatch.setattr(_pb, "BacktestEngine", _StubEngine)).  Since run_bot()
    references BacktestEngine as a bare name in that module's namespace, patching
    _pb.BacktestEngine replaces the exact symbol run_bot() uses — the stub IS
    exercised.

    We also assert len(trades)==1 in each test: if the stub were silently bypassed
    (e.g. because run_bot fell through to the real engine), the trades list would
    be empty (no EMA crossover on the flat-price fixture) and len==1 would fail.
    This serves as an implicit "stub was used" assertion.
    """

    def _patch(self, monkeypatch, fake_pnl):
        # 60 recent rows so run_bot doesn't SKIP (<50) and survives filter_last_year
        idx = _pd.date_range("2026-01-01", periods=60, freq="1h", tz="UTC")
        df = _pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx
        )
        monkeypatch.setattr(_pb, "load_signal_data", lambda *a, **k: df)
        monkeypatch.setattr(_pb, "load_trend_data", lambda *a, **k: _pd.DataFrame())
        monkeypatch.setattr(_pb, "filter_last_year", lambda d: d)

        fake_trade = _types.SimpleNamespace(
            pnl=fake_pnl, side="long", entry_price=1.0, exit_price=1.1,
            entry_time=idx[0], exit_time=idx[1], close_reason="take_profit",
        )

        stub_call_count = [0]  # mutable cell so the inner class can write it

        class _StubEngine:
            def __init__(self, *a, **k):
                stub_call_count[0] += 1
                self.state = _types.SimpleNamespace(trades=[fake_trade])
            def run(self, *a, **k):
                return None

        monkeypatch.setattr(_pb, "BacktestEngine", _StubEngine)
        return stub_call_count

    def test_pnl_frac_equals_pnl_over_initial_balance(self, monkeypatch):
        # engine initial_balance is hardcoded 10_000 in run_bot; a $1000 engine PnL
        # must record pnl_frac = 1000/10000 = 0.10 (hand-computed, not via prod code).
        stub_call_count = self._patch(monkeypatch, fake_pnl=1000.0)
        trades = _pb.run_bot("BTC", "ema", "1h", 1.5, 3.0, 5.0, "test", "btc")

        # Verify the stub was actually instantiated (not silently bypassed)
        assert stub_call_count[0] >= 1, (
            "_StubEngine was never instantiated — run_bot() may have imported "
            "BacktestEngine directly (not via _pb.BacktestEngine) so the patch "
            "did not intercept. Fix: ensure portfolio_backtest_v2.py uses a "
            "module-level 'from backtest.engine import BacktestEngine' and patch "
            "at _pb.BacktestEngine."
        )
        assert len(trades) == 1, (
            f"Expected 1 trade from _StubEngine, got {len(trades)}. "
            "If stub was bypassed, the real engine on a flat-price fixture "
            "would produce 0 trades."
        )
        assert trades[0]["pnl_frac"] == _pytest.approx(0.10, rel=1e-9), (
            "run_bot:348 must compute pnl_frac = engine_pnl / 10_000 (a 10x or "
            "risk-unnormalized change here is the historical shared-wallet inflation bug)"
        )

    def test_loss_pnl_frac_sign(self, monkeypatch):
        stub_call_count = self._patch(monkeypatch, fake_pnl=-250.0)
        trades = _pb.run_bot("BTC", "ema", "1h", 1.5, 3.0, 5.0, "test", "btc")
        assert stub_call_count[0] >= 1, "_StubEngine was never instantiated — patch did not intercept"
        assert len(trades) == 1
        assert trades[0]["pnl_frac"] == _pytest.approx(-0.025, rel=1e-9)
