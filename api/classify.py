"""Shared classify util for forward-test cohort decisions.

Lifted from research/forward_test_report.py (behaviour-preserving).
Both research/forward_test_report.py and the API import from here.
"""
from __future__ import annotations


def classify(trades: int, pf: float, min_trades: int, graduate_pf: float) -> str:
    """Decide a candidate's fate from its live sample (pure, testable).

    Args:
        trades:      Number of closed trades in the live sample.
        pf:          Profit factor (gross_win / gross_loss).
        min_trades:  Minimum trades required before graduating / dropping.
        graduate_pf: Profit factor threshold for READY_TO_AUDIT.

    Returns:
        One of: KEEP_TESTING, READY_TO_AUDIT, DROP, MARGINAL.
    """
    if trades < min_trades:
        return "KEEP_TESTING"
    if pf >= graduate_pf:
        return "READY_TO_AUDIT"
    if pf < 1.0:
        return "DROP"
    return "MARGINAL"
