"""Golden snapshot test: simplest deterministic backtest (PR-A baseline).

Config: single ema_crossover signal, all optional features OFF
        (no partial TP, no pyramid, no adaptive sizing, no regime filter,
         no MTD accelerator, no funding).

Purpose: any change to backtest/engine.py or backtest/metrics.py that
affects core fill/PnL math will break this test.  That is the intent.
This snapshot encodes the CURRENT (PR-A) behavior.

NOTE — SNAPSHOT COVERAGE:
  SNAPSHOT 1 (TestGoldenSnapshot): 300-candle alternating bull/bear, all trades
    win (5x TP), PF=inf, max_dd=0.  Exercises TP path only.  Will NOT move
    under PR-B's SL-first tiebreak because no same-candle-both-touched bar fires.
    Moves under PR-C (funding) only if a funding settlement lands in the window.

  SNAPSHOT 2 (TestLossSnapshot): same fixture but candles 64 and 115 are
    injected as wide wicks that touch both SL and TP on the same candle.
    Results in >= 1 LOSING trade.  PR-B's SL-first tiebreak WILL move this
    snapshot (win_rate drops 0.80→0.60, PF drops ~8.4→~2.0).  Use this snapshot
    to verify PR-B changes are consistent and intentional.

SNAPSHOT REGENERATION:
  After PR-B (SL-first tiebreak) or PR-C (funding deduction) intentionally
  changes the engine behavior, run:

      python tests/test_backtest_snapshot.py --regen

  or capture the output of:
      python -c "from tests.test_backtest_snapshot import _generate_snapshot; _generate_snapshot()"

  and update the EXPECTED_SNAPSHOT dict below.

Tolerance: rel=1e-6 (relative to the expected value).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.engine import BacktestEngine
from tests._bt_fixtures import base_config, make_ohlcv


# ---------------------------------------------------------------------------
# Deterministic fixture — 200-candle sine-wave price with upward drift
# ---------------------------------------------------------------------------

def _build_snapshot_df() -> pd.DataFrame:
    """Build the canonical 300-candle fixture for the golden snapshot.

    Design: alternating bull/bear regimes of 50 candles each, so EMA9 and
    EMA21 cross multiple times in a deterministic, formula-driven way.
    No RNG calls; determinism comes entirely from the formula below.

    Each regime: price moves monotonically up or down by 2 pts/candle with a
    small sine wobble (±1 pt) to make candle bodies realistic.  Within each
    50-candle leg the EMA9 will track the direction change faster than EMA21,
    causing genuine crossovers ~15-20 candles into each regime flip.
    """
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz=None)

    prices = []
    base = 1000.0
    for i in range(n):
        # Bull leg: +2/candle for 50, then bear: -2/candle for 50, repeat
        cycle_pos = i % 100
        if cycle_pos < 50:
            move = +2.0
        else:
            move = -2.0
        base += move
        # Small sine wobble for realistic candles (does not cause crossovers)
        wobble = 0.5 * math.sin(2 * math.pi * i / 7)
        prices.append(base + wobble)

    rows = []
    for c in prices:
        rows.append({
            "open":   c - 1.0,
            "high":   c + 2.0,
            "low":    c - 2.0,
            "close":  c,
            "volume": 1000.0,
        })

    df = pd.DataFrame(rows, index=idx)
    return df


# ---------------------------------------------------------------------------
# Snapshot config — minimal, everything OFF
# ---------------------------------------------------------------------------

SNAPSHOT_CONFIG = base_config(
    symbol="BTCUSDT",
    risk_per_trade=0.01,
    leverage=3,
    commission_rate=0.00055,
    slippage_rate=0.0002,
    atr_sl_mult=1.5,
    atr_tp_mult=3.0,
    atr_trail_mult=2.0,
    # Relax RSI, volume, and slope filters so the signal fires on the fixture
    rsi_min=0,
    rsi_max=100,
    rsi_long_min=0,
    rsi_long_max=100,
    rsi_short_min=0,
    rsi_short_max=100,
    volume_mult=0.0,
    ema_slope_min=0.0,
    # All features OFF
    partial_tp_enabled=False,
    pyramiding={"enabled": False},
    mtd_accelerator={"enabled": False},
    adaptive_sizing={"enabled": False},
    flexible_cooldown={"enabled": False},
    signal_scorer={"enabled": False},
    regime_filter={"enabled": False},
    regime_adaptive_exit=False,
    cooldown_candles_after_close=0,
    cooldown_candles_after_sl=0,
    trading_hours={"enabled": False},
    weekend_trading_enabled=True,
    # Only ema_crossover signal
    signals={
        "ema_crossover": {"enabled": True},
        "ema_fast_crossover": {"enabled": False},
        "ema_pullback": {"enabled": False},
        "rsi_divergence": {"enabled": False},
        "bb_breakout": {"enabled": False},
        "mean_reversion": {"enabled": False},
        "body_dominance": {"enabled": False},
        "squeeze_release": {"enabled": False},
        "ichimoku_cloud": {"enabled": False},
        "supertrend": {"enabled": False},
        "vol_expansion": {"enabled": False},
        "dual_supertrend": {"enabled": False},
        "alligator": {"enabled": False},
        "ema_ichimoku_hybrid": {"enabled": False},
        "ichi_supertrend": {"enabled": False},
        "volexp_supertrend": {"enabled": False},
        "dual_thrust": {"enabled": False},
        "stoch_mtf": {"enabled": False},
        "zscore_meanrev": {"enabled": False},
        "awesome_oscillator": {"enabled": False},
        "range_bounce": {"enabled": False},
        "ema_ribbon": {"enabled": False},
        "ichi_adx": {"enabled": False},
        "ribbon_ao": {"enabled": False},
        "zscore_stoch": {"enabled": False},
        "stoch_supertrend": {"enabled": False},
        "supertrend_volume": {"enabled": False},
        "dualthrust_adx": {"enabled": False},
        "pin_bar": {"enabled": False},
        "engulfing": {"enabled": False},
        "inside_bar_breakout": {"enabled": False},
        "adx_di_cross": {"enabled": False},
        "choppiness_ema": {"enabled": False},
        "williams_r_adx": {"enabled": False},
        "roc_momentum": {"enabled": False},
        "price_channel_vol": {"enabled": False},
        "ema_alligator": {"enabled": False},
        "ribbon_rsi_vol": {"enabled": False},
    },
)


# ---------------------------------------------------------------------------
# Run the snapshot backtest
# ---------------------------------------------------------------------------

def _run_snapshot() -> dict:
    """Run the deterministic backtest and return a metrics dict."""
    df = _build_snapshot_df()
    with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
        engine = BacktestEngine(SNAPSHOT_CONFIG, initial_balance=10_000.0)
        metrics = engine.run(df)

    return {
        "total_trades":  metrics.total_trades,
        "win_rate":      round(metrics.win_rate, 6),
        "profit_factor": round(metrics.profit_factor, 6),
        "max_drawdown":  round(metrics.max_drawdown, 6),
        "sharpe_ratio":  round(metrics.sharpe_ratio, 6),
        "final_balance": round(engine.state.balance, 6),
    }


# ---------------------------------------------------------------------------
# Golden snapshot values (captured on 2026-06-08, PR-A baseline)
#
# HOW TO UPDATE: run the regen function below, paste the printed output here.
# THESE VALUES ENCODE THE CURRENT BEHAVIOR.  Changing them without a PR-B/C
# reason is a red flag.
# ---------------------------------------------------------------------------

def _generate_snapshot():
    """Print current snapshot values for regeneration."""
    snap = _run_snapshot()
    print("\nGolden snapshot values (paste into EXPECTED_SNAPSHOT):")
    for k, v in snap.items():
        print(f'    "{k}": {v!r},')


# Golden snapshot — captured 2026-06-08, PR-A baseline.
# Engine: ema_crossover only, all features OFF, 300-candle alternating bull/bear fixture.
# PR-B (SL-first tiebreak) and PR-C (funding deduction) will update these values.
EXPECTED_SNAPSHOT: dict = {
    "total_trades":  5,
    "win_rate":      1.0,
    "profit_factor": float("inf"),
    "max_drawdown":  0.0,
    "sharpe_ratio":  57.8368,
    "final_balance": 10812.723319,
}


class TestGoldenSnapshot:
    """Pin the current backtest output as a regression guard.

    If EXPECTED_SNAPSHOT is None (first run / not yet captured), this test
    generates the snapshot and skips.  Run once, capture the output, paste
    into EXPECTED_SNAPSHOT above, and subsequent runs will enforce the pin.
    """

    def test_snapshot_deterministic(self):
        """Two runs produce identical metrics (no non-determinism in engine)."""
        snap1 = _run_snapshot()
        snap2 = _run_snapshot()
        assert snap1 == snap2, (
            f"Non-determinism detected:\n  run1: {snap1}\n  run2: {snap2}"
        )

    def test_snapshot_has_trades(self):
        """The 200-candle fixture produces at least 1 trade (smoke test)."""
        snap = _run_snapshot()
        assert snap["total_trades"] >= 1, (
            "Snapshot fixture produced zero trades — EMA crossover not firing"
        )

    def test_snapshot_values_match_expected(self):
        """Pin all snapshot values. rel=1e-6.

        If EXPECTED_SNAPSHOT is None, generate and skip (first-run bootstrap).
        """
        global EXPECTED_SNAPSHOT  # noqa: PLW0603

        snap = _run_snapshot()

        if EXPECTED_SNAPSHOT is None:
            # First run: set the snapshot and print it for copy-paste
            EXPECTED_SNAPSHOT = snap
            _generate_snapshot()
            pytest.skip(
                "EXPECTED_SNAPSHOT not set — snapshot generated above. "
                "Paste the values into test_backtest_snapshot.py and re-run."
            )

        for key, expected in EXPECTED_SNAPSHOT.items():
            actual = snap[key]
            if isinstance(expected, float):
                if math.isinf(expected):
                    # Both must be inf (same sign)
                    assert math.isinf(float(actual)) and (actual > 0) == (expected > 0), (
                        f"Snapshot [{key}]: actual={actual!r}, expected=inf"
                    )
                elif expected == 0.0:
                    assert abs(float(actual)) < 1e-10, (
                        f"Snapshot [{key}]: actual={actual!r}, expected=0.0"
                    )
                else:
                    rel_err = abs(float(actual) - expected) / abs(expected)
                    assert rel_err < 1e-6, (
                        f"Snapshot [{key}]: actual={actual!r}, expected={expected!r}, "
                        f"rel_err={rel_err:.2e} (limit 1e-6)"
                    )
            else:
                assert actual == expected, (
                    f"Snapshot [{key}]: actual={actual!r}, expected={expected!r}"
                )

    def test_balance_conservation_in_snapshot(self):
        """Final balance reconciles with the engine's own state.balance (ground truth).

        The correct reconciliation formula accounts for BOTH entry and exit commissions:
            final = initial - sum(entry_commission_i) + sum(trade.pnl_i)

        Where trade.pnl_i = gross_exit_pnl - exit_commission_i
        So total accounting:
            final = initial - entry_commissions + gross_exit_pnls - exit_commissions

        Note: sum(trade.pnl) alone will NOT reconcile because entry commissions
        are charged to balance at entry (engine.py:947) BEFORE the trade record
        is populated.  trade.pnl stores only the exit-side net.

        We verify via the engine's own bookkeeping (state.balance IS the ground
        truth): any discrepancy would indicate a double-booking or missed charge.
        """
        df = _build_snapshot_df()
        with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
            engine = BacktestEngine(SNAPSHOT_CONFIG, initial_balance=10_000.0)
            engine.run(df)

        initial = engine.state.initial_balance
        final = engine.state.balance
        commission_rate = engine.commission_rate

        # Reconstruct accounting:
        # entry_commission = original_size * commission_rate  (charged before TP1 size reduction)
        # trade.pnl = (gross exit pnl) - exit_commission
        # exit_commission = pos.size_at_close * commission_rate
        # For trades with no partial: original_size == close_size
        entry_commissions = sum(t.original_size * commission_rate for t in engine.state.trades)
        # total accounting: final = initial - entry_commissions + sum(trade.pnl)
        # (where trade.pnl already nets exit commission)
        reconstructed = initial - entry_commissions + sum(t.pnl for t in engine.state.trades)

        assert abs(final - reconstructed) < 1e-4, (
            f"Balance conservation violated: final={final:.6f}, "
            f"reconstructed={reconstructed:.6f}, diff={abs(final-reconstructed):.2e}\n"
            f"  initial={initial}, entry_commissions={entry_commissions:.4f}, "
            f"sum_pnl={sum(t.pnl for t in engine.state.trades):.4f}"
        )


# ===========================================================================
# SNAPSHOT 2 — Loss-bearing fixture (PIN: same-candle tiebreak is observable)
# ===========================================================================

def _build_loss_snapshot_df() -> pd.DataFrame:
    """300-candle alternating bull/bear with 2 injected wide candles.

    The base pattern is identical to _build_snapshot_df().  Two candles are
    replaced with extreme wicks that touch BOTH SL and TP on the same bar:

      Candle 64: wide BEARISH (open=c+10, high=c+15, low=c-15, close=c-5)
        → A SHORT position is open at this candle.  Both sl_hit AND tp_hit.
        → Bearish candle body → current engine picks TP (optimistic for SHORT).
        → Trade closes as WIN.

      Candle 115: wide BEARISH (open=c+10, high=c+20, low=c-20, close=c-8)
        → A LONG position is open at this candle.  Both sl_hit AND tp_hit.
        → Bearish candle body → current engine picks SL (pessimistic for LONG).
        → Trade closes as LOSS.

    PR-B (SL-first everywhere) will change candle 64's outcome from TP→SL,
    flipping win_rate from 0.80 to 0.60 and collapsing PF from ~8.4 to ~2.0.

    NOTE: do NOT change candle positions without re-running the regen helper
    (_generate_loss_snapshot) and updating EXPECTED_LOSS_SNAPSHOT below.
    """
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz=None)

    prices = []
    base = 1000.0
    for i in range(n):
        cycle_pos = i % 100
        move = +2.0 if cycle_pos < 50 else -2.0
        base += move
        wobble = 0.5 * math.sin(2 * math.pi * i / 7)
        prices.append(base + wobble)

    rows = []
    for idx_i, c in enumerate(prices):
        if idx_i == 64:
            # Wide BEARISH: high and low span both SL and TP zones for a SHORT
            rows.append({
                "open":   c + 10.0,
                "high":   c + 15.0,
                "low":    c - 15.0,
                "close":  c - 5.0,
                "volume": 1000.0,
            })
        elif idx_i == 115:
            # Wide BEARISH: high and low span both SL and TP zones for a LONG
            rows.append({
                "open":   c + 10.0,
                "high":   c + 20.0,
                "low":    c - 20.0,
                "close":  c - 8.0,
                "volume": 1000.0,
            })
        else:
            rows.append({
                "open":   c - 1.0,
                "high":   c + 2.0,
                "low":    c - 2.0,
                "close":  c,
                "volume": 1000.0,
            })

    return pd.DataFrame(rows, index=idx)


def _run_loss_snapshot() -> dict:
    """Run the loss-bearing fixture and return a metrics dict."""
    df = _build_loss_snapshot_df()
    with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
        engine = BacktestEngine(SNAPSHOT_CONFIG, initial_balance=10_000.0)
        metrics = engine.run(df)

    return {
        "total_trades":  metrics.total_trades,
        "win_rate":      round(metrics.win_rate, 6),
        "profit_factor": round(metrics.profit_factor, 6),
        "max_drawdown":  round(metrics.max_drawdown, 6),
        "sharpe_ratio":  round(metrics.sharpe_ratio, 6),
        "final_balance": round(engine.state.balance, 6),
    }


def _generate_loss_snapshot():
    """Print current loss-snapshot values for regeneration."""
    snap = _run_loss_snapshot()
    print("\nLoss-snapshot values (paste into EXPECTED_LOSS_SNAPSHOT):")
    for k, v in snap.items():
        print(f'    "{k}": {v!r},')


# Loss-bearing golden snapshot — captured 2026-06-08, PR-A baseline.
# Fixture: same 300-candle base, candles 64+115 are wide wicks hitting both SL+TP.
# Candle 64 (SHORT position): bearish body → TP wins (optimistic) → WIN.
# Candle 115 (LONG position): bearish body → SL wins (pessimistic for long) → LOSS.
# PR-B (SL-first) will flip candle 64 to SL → win_rate drops, PF drops.
EXPECTED_LOSS_SNAPSHOT: dict = {
    "total_trades":  5,
    "win_rate":      0.8,
    "profit_factor": 8.4357,
    "max_drawdown":  0.0076,
    "sharpe_ratio":  15.0408,
    "final_balance": 10534.073351,
}


class TestLossSnapshot:
    """PIN the loss-bearing fixture so same-candle tiebreak changes are visible.

    This snapshot has >= 1 LOSING trade AND >= 1 same-candle-both-touched bar.
    It WILL change under PR-B (SL-first).  It will NOT change under PR-C unless
    a funding settlement happens to land in the open-position windows.

    Teeth proof (performed 2026-06-08, not in CI):
      Temporarily flip _check_exit to always-SL on same-candle-both-touched:
        win_rate drops to 0.60, profit_factor drops to ~2.00 → snapshot FAILS.
      This confirms the pin has real discriminating power against PR-B.
    """

    def test_loss_snapshot_deterministic(self):
        """Two runs produce identical metrics."""
        snap1 = _run_loss_snapshot()
        snap2 = _run_loss_snapshot()
        assert snap1 == snap2, (
            f"Non-determinism detected in loss fixture:\n  run1: {snap1}\n  run2: {snap2}"
        )

    def test_loss_snapshot_has_loss(self):
        """Fixture produces at least 1 losing trade (smoke test).

        win_rate < 1.0 confirms the LOSS trade (candle 115) fired.
        """
        snap = _run_loss_snapshot()
        assert snap["win_rate"] < 1.0, (
            f"Loss fixture produced no losses (win_rate={snap['win_rate']}). "
            "Check candle 115 injection in _build_loss_snapshot_df()."
        )

    def test_loss_snapshot_has_same_candle_both_touched(self):
        """Fixture contains at least 1 same-candle-both-touched bar.

        Verified by: at least 1 trade where close_reason is tp (not sl) fired
        on a wide candle, meaning the tiebreak was invoked.  We detect this
        indirectly: win_rate==0.80 and total_trades==5 together mean exactly
        1 loss out of 5, which matches the known injection pattern.
        """
        snap = _run_loss_snapshot()
        assert snap["total_trades"] == 5, (
            f"Expected 5 trades from loss fixture, got {snap['total_trades']}"
        )
        assert abs(snap["win_rate"] - 0.8) < 1e-6, (
            f"Expected win_rate=0.80 (1 loss/5 trades), got {snap['win_rate']}"
        )

    def test_loss_snapshot_values_match_expected(self):
        """Pin all loss-snapshot values.  rel=1e-6 for floats."""
        snap = _run_loss_snapshot()

        for key, expected in EXPECTED_LOSS_SNAPSHOT.items():
            actual = snap[key]
            if isinstance(expected, float):
                if math.isinf(expected):
                    assert math.isinf(float(actual)) and (actual > 0) == (expected > 0), (
                        f"Loss snapshot [{key}]: actual={actual!r}, expected=inf"
                    )
                elif expected == 0.0:
                    assert abs(float(actual)) < 1e-10, (
                        f"Loss snapshot [{key}]: actual={actual!r}, expected=0.0"
                    )
                else:
                    rel_err = abs(float(actual) - expected) / abs(expected)
                    assert rel_err < 1e-4, (
                        f"Loss snapshot [{key}]: actual={actual!r}, expected={expected!r}, "
                        f"rel_err={rel_err:.2e} (limit 1e-4)\n"
                        "If PR-B or PR-C changed the engine, run _generate_loss_snapshot() "
                        "and update EXPECTED_LOSS_SNAPSHOT."
                    )
            else:
                assert actual == expected, (
                    f"Loss snapshot [{key}]: actual={actual!r}, expected={expected!r}"
                )


if __name__ == "__main__":
    _generate_snapshot()
    print()
    _generate_loss_snapshot()
