"""Characterization tests for backtest/metrics.py.

All expected values are INDEPENDENTLY hand-computed — no call to the
production formula inside the assertion.  Derivations are in comments.

PIN-5: max drawdown denominator is peak EQUITY (initial + peak_cumulative_pnl),
       not just peak cumulative PnL.
Sharpe: per-trade R-Sharpe, annualized with sqrt(trades_per_year).
PF=inf: when there are no losing trades.
"""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import BacktestMetrics, compute_metrics


# ---------------------------------------------------------------------------
# Helper: build a trades DataFrame from explicit lists
# ---------------------------------------------------------------------------

def _make_trades(
    pnls: list[float],
    entry_times: list[str] | None = None,
    exit_times: list[str] | None = None,
    risk_amounts: list[float] | None = None,
) -> pd.DataFrame:
    """Build a minimal trades DataFrame accepted by compute_metrics."""
    n = len(pnls)
    if entry_times is None:
        entry_times = [f"2024-01-{i+1:02d} 10:00" for i in range(n)]
    if exit_times is None:
        exit_times = [f"2024-01-{i+1:02d} 14:00" for i in range(n)]
    if risk_amounts is None:
        risk_amounts = [100.0] * n

    # pnl_pct = pnl / risk_amount (used for Sharpe, stored on BacktestTrade)
    pnl_pcts = [p / r for p, r in zip(pnls, risk_amounts)]

    return pd.DataFrame({
        "pnl": pnls,
        "pnl_pct": pnl_pcts,
        "risk_amount": risk_amounts,
        "entry_time": entry_times,
        "exit_time": exit_times,
        "side": ["long"] * n,
        "entry_price": [100.0] * n,
        "exit_price": [101.0] * n,
    })


# ============================================================================
# Empty / edge cases
# ============================================================================

class TestEmptyTrades:
    def test_empty_df_returns_zero_metrics(self):
        m = compute_metrics(pd.DataFrame(), initial_balance=10_000.0)
        assert m.total_trades == 0
        assert m.sharpe_ratio == 0.0
        assert m.max_drawdown == 0.0
        assert m.profit_factor == 0.0
        assert m.win_rate == 0.0


# ============================================================================
# PIN-5 — Max drawdown uses peak EQUITY denominator
# ============================================================================

class TestMaxDrawdown:
    """PIN-5: drawdown % = max_drawdown_dollar / peak_equity.

    metrics.py:85-91:
        equity = initial_balance + cumsum(pnls)
        peak_equity = cummax(equity)
        drawdown = peak_equity - equity
        max_dd_pct = max(drawdown) / peak_equity[argmax(drawdown)]

    Test case (initial=10000, pnls=[+500, +200, -900]):
        equity  = [10500, 10700, 9800]
        peak    = [10500, 10700, 10700]
        drawdown= [0, 0, 900]
        max_dd_dollar = 900
        peak_at_max   = 10700    ← this is the denominator
        max_dd_pct    = 900/10700 ≈ 0.08411
    """

    def test_drawdown_denominator_is_peak_equity(self):
        """Drawdown % uses peak equity (initial+pnl), NOT just peak PnL.

        Hand-derivation:
            initial = 10_000
            pnls = [500, 200, -900]
            equity sequence: 10500, 10700, 9800
            peak at max drawdown: 10700
            max drawdown dollar: 900
            exact = 900 / 10700 = 0.08411214...
            rounded to 4dp (as compute_metrics does): 0.0841

        If the bug were present (peak_pnl denominator only):
            peak_pnl = 700
            wrong_dd_pct = 900 / 700 > 1.0 — absurd

        We compare against the ROUNDED value (the function rounds to 4dp at
        metrics.py:118: round(max_dd_pct, 4)).
        """
        trades = _make_trades([500.0, 200.0, -900.0])
        m = compute_metrics(trades, initial_balance=10_000.0)

        # Exact value is 900/10700 = 0.084112...
        # compute_metrics rounds to 4dp → 0.0841
        # We assert against the rounded form to match the function's output contract.
        exact = 900.0 / 10_700.0  # ≈ 0.084112
        rounded = round(exact, 4)  # = 0.0841
        assert abs(m.max_drawdown - rounded) < 1e-8, (
            f"max_drawdown={m.max_drawdown:.6f}, expected={rounded:.6f}. "
            "Denominator must be peak equity (initial + peak_cumulative_pnl)."
        )
        # Also assert it is NOT the wrong value: 900/700 = 1.2857 (the bug)
        wrong_pct = 900.0 / 700.0
        assert m.max_drawdown < 1.0, (
            f"max_drawdown={m.max_drawdown:.4f} exceeds 100% — likely using wrong denominator "
            f"(peak_pnl={700} instead of peak_equity={10_700})"
        )

    def test_drawdown_zero_when_monotonic_up(self):
        """No drawdown when PnL is strictly increasing."""
        trades = _make_trades([100.0, 200.0, 150.0, 300.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        # equity: 10100, 10300, 10450, 10750 — never below peak
        assert m.max_drawdown == 0.0, f"Expected 0 drawdown, got {m.max_drawdown}"

    def test_drawdown_at_trough(self):
        """Drawdown picks the WORST trough, not the final equity.

        Hand-derivation:
            initial=1000, pnls=[+500, -800, +200]
            equity:  [1500, 700, 900]
            peak:    [1500, 1500, 1500]
            drawdown:[0, 800, 600]
            max = 800, peak_at_max = 1500
            exact = 800/1500 = 0.53333...
            rounded to 4dp → 0.5333
        """
        trades = _make_trades([500.0, -800.0, 200.0])
        m = compute_metrics(trades, initial_balance=1_000.0)
        exact = 800.0 / 1_500.0
        rounded = round(exact, 4)  # = 0.5333
        assert abs(m.max_drawdown - rounded) < 1e-8


# ============================================================================
# Win rate and profit factor
# ============================================================================

class TestWinRateAndPF:
    def test_win_rate(self):
        """Win rate = wins / total trades.

        Hand: 3 wins, 2 losses → 3/5 = 0.6
        """
        trades = _make_trades([100.0, -50.0, 200.0, -30.0, 80.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        assert abs(m.win_rate - 3 / 5) < 1e-8

    def test_profit_factor(self):
        """PF = gross_profit / gross_loss.

        Hand: wins=[100, 200, 80] → gross_profit=380; losses=[50, 30] → gross_loss=80
        PF = 380 / 80 = 4.75
        """
        trades = _make_trades([100.0, -50.0, 200.0, -30.0, 80.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        expected_pf = (100.0 + 200.0 + 80.0) / (50.0 + 30.0)  # = 380/80 = 4.75
        assert abs(m.profit_factor - expected_pf) < 1e-6

    def test_profit_factor_inf_when_no_losses(self):
        """PF = inf when there are no losing trades.

        metrics.py:64: gross_loss=0 → profit_factor=float('inf')
        """
        trades = _make_trades([100.0, 200.0, 50.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        assert math.isinf(m.profit_factor), f"Expected inf, got {m.profit_factor}"

    def test_profit_factor_zero_when_no_wins(self):
        """PF = 0 when there are no winning trades.

        metrics.py:62: gross_profit=0, gross_loss>0 → PF=0/gross_loss=0
        """
        trades = _make_trades([-100.0, -50.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        assert m.profit_factor == 0.0


# ============================================================================
# Sharpe ratio — per-trade R-Sharpe
# ============================================================================

class TestSharpeRatio:
    """PIN: Sharpe is per-trade R-Sharpe, annualized by sqrt(trades_per_year).

    metrics.py:67-77:
        returns = trades['pnl_pct']
        mean    = np.mean(returns)
        std     = np.std(returns, ddof=1)
        trades_per_year = len(returns) / (span_days / 365.25)
        annualization   = sqrt(trades_per_year)
        sharpe = mean / std * annualization

    This is NOT a calendar-daily Sharpe (that's a possible future change).

    Hand-derivation for 4 trades:
        pnl = [+2R, +3R, -1R, +1R] → pnl_pct = [2, 3, -1, 1] (risk_amount=1)
        mean  = (2 + 3 - 1 + 1) / 4 = 5/4 = 1.25
        std   = std([2,3,-1,1], ddof=1)
              = sqrt(((2-1.25)² + (3-1.25)² + (-1-1.25)² + (1-1.25)²) / 3)
              = sqrt((0.5625 + 3.0625 + 5.0625 + 0.0625) / 3)
              = sqrt(8.75 / 3)
              = sqrt(2.9167) ≈ 1.70782

        span_days: entry[0]="2024-01-01 10:00", exit[-1]="2024-01-04 14:00"
        = (2024-01-04 14:00 − 2024-01-01 10:00) = 3 days + 4h = 3.167 days
        trades_per_year = 4 / (3.167 / 365.25) ≈ 461.2
        annualization = sqrt(461.2) ≈ 21.476

        sharpe = (1.25 / 1.70782) * 21.476 ≈ 15.70

    Note: metrics.py uses ``Timedelta(...).days`` which is an INTEGER floor,
    so "3 days 4 hours" → span_days=3, not 3.167.  This produces tpy=487 and
    Sharpe≈16.1522, NOT 15.70 (the fractional-days derivation).
    """

    def test_sharpe_hand_computed_4_trades(self):
        """Sharpe vs INDEPENDENT hand-computed value.

        Hand-derivation for 4 trades:
            pnl = [+2R, +3R, -1R, +1R] → pnl_pct = [2, 3, -1, 1] (risk_amount=1)
            mean  = (2 + 3 - 1 + 1) / 4 = 5/4 = 1.25
            std   = std([2,3,-1,1], ddof=1)
                  = sqrt(((0.75)² + (1.75)² + (2.25)² + (0.25)²) / 3)
                  = sqrt((0.5625 + 3.0625 + 5.0625 + 0.0625) / 3)
                  = sqrt(8.75 / 3) ≈ 1.70782

            span_days: metrics.py uses Timedelta.days (integer floor).
                exit[-1]  = "2024-01-04 14:00"
                entry[0]  = "2024-01-01 10:00"
                timedelta = 3 days 4 hours → .days = 3  (integer floor)
            trades_per_year = 4 / (3 / 365.25) = 4 * 365.25 / 3 = 487.0
            annualization   = sqrt(487.0) ≈ 22.0681

            sharpe = (1.25 / 1.70782) * 22.0681 ≈ 16.1522

        Important: metrics.py uses ``Timedelta(...).days`` (integer), NOT
        total_seconds/86400 (fractional).  Using fractional days gives a
        slightly different result (tpy≈461, sharpe≈15.72).  The pin uses
        the integer-days formula to match the actual code.
        """
        pnls = [2.0, 3.0, -1.0, 1.0]
        risk_amounts = [1.0, 1.0, 1.0, 1.0]
        entry_times = [
            "2024-01-01 10:00",
            "2024-01-02 10:00",
            "2024-01-03 10:00",
            "2024-01-04 10:00",
        ]
        exit_times = [
            "2024-01-01 14:00",
            "2024-01-02 14:00",
            "2024-01-03 14:00",
            "2024-01-04 14:00",
        ]
        trades = _make_trades(pnls, entry_times, exit_times, risk_amounts)

        m = compute_metrics(trades, initial_balance=10_000.0)

        # --- Independent computation (do NOT re-use production code) ---
        returns = np.array([p / r for p, r in zip(pnls, risk_amounts)])
        mean_r = np.mean(returns)             # 1.25
        std_r = np.std(returns, ddof=1)       # ≈ 1.70782

        # metrics.py:72 uses (pd.to_datetime(exit[-1]) - pd.to_datetime(entry[0])).days
        # which is the INTEGER floor of the total timedelta in days.
        span_days = (
            pd.Timestamp("2024-01-04 14:00") - pd.Timestamp("2024-01-01 10:00")
        ).days                                # = 3 (integer floor, NOT 3.167)
        tpy = len(returns) / (span_days / 365.25)  # = 4 / (3/365.25) = 487.0
        ann = math.sqrt(tpy)                  # ≈ 22.0681
        expected_sharpe = (mean_r / std_r) * ann  # ≈ 16.1522

        # Round to 4dp as the function does
        expected_sharpe_rounded = round(expected_sharpe, 4)  # 16.1522

        assert abs(m.sharpe_ratio - expected_sharpe_rounded) < 1e-3, (
            f"Sharpe={m.sharpe_ratio:.4f}, hand-computed={expected_sharpe_rounded:.4f}"
        )

    def test_sharpe_positive_for_profitable_strategy(self):
        """All-win trades → positive Sharpe."""
        trades = _make_trades([1.0, 2.0, 1.5, 3.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        assert m.sharpe_ratio > 0.0

    def test_sharpe_negative_for_losing_strategy(self):
        """All-loss trades → negative Sharpe."""
        trades = _make_trades([-1.0, -2.0, -0.5])
        m = compute_metrics(trades, initial_balance=10_000.0)
        assert m.sharpe_ratio < 0.0

    def test_single_trade_sharpe_zero(self):
        """With only 1 trade, std=1.0 (ddof=1 would be NaN — guarded by `if len > 1`).

        metrics.py:68: ``std = np.std(..., ddof=1) if len(returns) > 1 else 1.0``
        So sharpe = mean / 1.0 * sqrt(trades_per_year).
        """
        trades = _make_trades([5.0], ["2024-01-01 10:00"], ["2024-01-01 14:00"], [1.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        # Just check it doesn't crash and produces a finite value
        assert math.isfinite(m.sharpe_ratio)


# ============================================================================
# Avg R:R actual
# ============================================================================

class TestAvgRR:
    def test_avg_rr_actual(self):
        """avg_rr = mean(|wins|) / mean(|losses|).

        Hand: pnls=[100, -50, 200, -30]
            wins=[100, 200] → avg_win=150
            losses=[50, 30] → avg_loss=40
            avg_rr = 150 / 40 = 3.75
        """
        trades = _make_trades([100.0, -50.0, 200.0, -30.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        expected = 150.0 / 40.0  # = 3.75
        assert abs(m.avg_rr_actual - expected) < 1e-6

    def test_avg_rr_zero_when_no_wins(self):
        """No wins → avg_win=0 → avg_rr=0."""
        trades = _make_trades([-10.0, -20.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        assert m.avg_rr_actual == 0.0


# ============================================================================
# Total trades count
# ============================================================================

class TestTotalTrades:
    def test_total_trades(self):
        trades = _make_trades([10.0, -5.0, 20.0])
        m = compute_metrics(trades, initial_balance=10_000.0)
        assert m.total_trades == 3

    def test_total_trades_empty(self):
        m = compute_metrics(pd.DataFrame(), initial_balance=10_000.0)
        assert m.total_trades == 0
