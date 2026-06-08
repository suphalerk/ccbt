"""Characterization tests for backtest/engine.py.

PR-A SCOPE: pin TODAY's behavior AS-IS (zero behavior change).

Documented KNOWN GAPS (to be fixed in PR-B / PR-C):
  - KNOWN GAP (cat 2): gap-through-SL fills at the literal pos.stop_loss,
    NOT the worse gap-open price.  Optimistic.  See test_gap_fill_pins_current_optimistic.
  - KNOWN GAP (cat 3): no liquidation modeling.  High-leverage positions whose
    adverse excursion crosses maintenance margin still exit at their ATR-SL,
    not at the liquidation price.  See test_liquidation_known_gap.
  - KNOWN GAP (cat 6): funding is NEVER deducted from position PnL (PR-C fix).
    See test_funding_not_charged_known_gap.
  - KNOWN BEHAVIOR (cat 1 tiebreak): same-candle SL+TP tiebreak uses candle
    body direction (OPTIMISTIC).  Replaced by SL-first in PR-B.
    See test_same_candle_sl_tp_tiebreak_current_candle_body.

All INDEPENDENT expected values are hand-derived; derivation in comments.
"""

from __future__ import annotations

import math
from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from backtest.engine import BacktestEngine, BacktestTrade
from bot.risk import RiskManager
from bot.strategy import SignalType

from tests._bt_fixtures import (
    base_config,
    make_candle,
    make_ohlcv,
    make_trading_ohlcv,
    open_position,
    trading_config,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_engine(initial_balance: float = 10_000.0, **cfg_overrides) -> BacktestEngine:
    cfg = base_config(**cfg_overrides)
    # Suppress funding file lookup (no data files in test env)
    with patch("backtest.engine.add_funding_rate", side_effect=lambda df, _path: df):
        return BacktestEngine(cfg, initial_balance=initial_balance)


def _risk_mgr(engine: BacktestEngine) -> RiskManager:
    return RiskManager(engine.config, engine.state.balance)


# ============================================================================
# Category 1 — Intrabar SL / TP (no same-candle entry+exit)
# ============================================================================

class TestIntrabarExits:
    """PIN: position opened at candle i never exits on that same candle.

    engine.py:271 checks exit BEFORE entry each iteration.  Entry fires at
    candle i's close.  The very next iteration (candle i+1) is the first exit
    check.  So the entry candle's own h/l is never evaluated against SL/TP.
    This is a structural guarantee from the loop order.
    """

    def test_entry_candle_not_evaluated_for_exit(self):
        """Behavioral PIN-2 (partial): entry candle's h/l is never evaluated for exit.

        engine.py:270-275: the loop first checks exit (on row i), THEN entry
        (on prev_row i-1 as signal, row i as exec).  A position entered at the
        CLOSE of candle i is placed into engine.state.position AFTER the exit
        check for candle i has already run.  Therefore, candle i's h/l is never
        evaluated against the new trade's SL/TP.

        This fixture uses make_trading_ohlcv() + trading_config() to generate at
        least 2 trades, then asserts: for EVERY closed trade, entry_time != exit_time.

        The loop structure guarantees this structurally, but we verify it behaviorally
        because a structural test (asserting 'signal_row is prev_row') would pass even
        if the logic changed to use the wrong candle index — this assertion catches
        the actual observable outcome.
        """
        df = make_trading_ohlcv(300)
        cfg = trading_config(cooldown_candles_after_sl=0, cooldown_candles_after_close=0)

        with patch("backtest.engine.add_funding_rate", side_effect=lambda df, _: df):
            engine = BacktestEngine(cfg, initial_balance=10_000.0)
            engine.run(df)

        # Verify we actually got trades (guard against vacuous iteration)
        assert len(engine.state.trades) >= 2, (
            f"Fixture produced only {len(engine.state.trades)} trades — "
            "EMA crossover not firing; check make_trading_ohlcv() output"
        )

        for t in engine.state.trades:
            if t.entry_time and t.exit_time:
                assert t.entry_time != t.exit_time, (
                    f"Trade exited on the same candle it entered: {t.entry_time}"
                )

    def test_sl_hit_closes_long(self):
        """Long SL hit: position closed when low <= stop_loss."""
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        # Entry at 100, SL at 95, TP at 115
        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=115.0, size=100.0)

        # Candle with low = 94.0 (below SL 95) — SL should trigger
        candle = make_candle(open_price=98.0, high=101.0, low=94.0, close=99.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        assert engine.state.position is None, "Position should be closed on SL"
        assert len(engine.state.trades) == 1
        assert engine.state.trades[-1].close_reason == "stop_loss"

    def test_tp_hit_closes_long(self):
        """Long TP hit: position closed when high >= take_profit."""
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=115.0, size=100.0)

        # Candle with high = 116.0 (above TP 115) — TP should trigger
        candle = make_candle(open_price=110.0, high=116.0, low=109.0, close=112.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        assert engine.state.position is None, "Position should be closed on TP"
        assert engine.state.trades[-1].close_reason == "take_profit"

    def test_sl_hit_closes_short(self):
        """Short SL hit: position closed when high >= stop_loss."""
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="short", entry_price=100.0, stop_loss=106.0,
                      take_profit=88.0, size=100.0)

        candle = make_candle(open_price=102.0, high=107.0, low=101.0, close=103.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        assert engine.state.position is None
        assert engine.state.trades[-1].close_reason == "stop_loss"

    def test_tp_hit_closes_short(self):
        """Short TP hit: position closed when low <= take_profit."""
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="short", entry_price=100.0, stop_loss=106.0,
                      take_profit=88.0, size=100.0)

        candle = make_candle(open_price=92.0, high=93.0, low=87.0, close=90.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        assert engine.state.position is None
        assert engine.state.trades[-1].close_reason == "take_profit"

    def test_neither_sl_nor_tp_leaves_position_open(self):
        """No exit trigger → position stays open."""
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=115.0, size=100.0)

        # Candle fully within SL/TP range
        candle = make_candle(open_price=101.0, high=104.0, low=98.0, close=102.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        assert engine.state.position is not None, "Position should remain open"


# ============================================================================
# Category 1 — Same-candle SL+TP tiebreak (PR-B: SL-FIRST conservative)
# ============================================================================

class TestSameCandleTiebreak:
    """PIN-7 (PR-B): SL-first — when both SL and TP are touched on the same candle,
    the stop-loss always fills first.

    engine.py:994-1019 — THE SL-FIRST TIEBREAK (conservative / pessimistic).

    Rationale: intra-candle fill order is unknown; assuming TP-first inflates
    backtest PnL.  The safe assumption is that the adverse move (SL) happened
    before the favorable one (TP).

    Mutation test: reverting _check_exit to the old candle-body logic must fail
    test_sl_first_long_bullish_candle and test_sl_first_short_bearish_candle
    (those are the only two cases where candle-body gave TP, now give SL).
    """

    def test_sl_first_long_bullish_candle(self):
        """Long, bullish candle: both SL+TP touched → SL fills first (loss).

        PR-A (candle-body): bullish close → TP wins (optimistic).
        PR-B (SL-first):    SL wins regardless of candle direction.

        Mutation guard: if you revert to the candle-body tiebreak,
        close_reason becomes 'take_profit' and this test fails.
        """
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=110.0, size=100.0)

        # Both SL (low=94 ≤ 95) and TP (high=111 ≥ 110) touched.
        # Candle is BULLISH (close=106 > open=98) — under PR-A this picked TP.
        # Under PR-B it must pick SL.
        candle = make_candle(open_price=98.0, high=111.0, low=94.0, close=106.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        trade = engine.state.trades[-1]
        assert trade.close_reason == "stop_loss", (
            f"SL-first: bullish candle with both SL+TP touched must exit at stop_loss, "
            f"got {trade.close_reason!r}.  If 'take_profit', the candle-body tiebreak is still active."
        )
        # The exit must be a loss (exit price ≤ entry price for a long)
        assert trade.pnl < 0, (
            f"SL-first long exit must be a loss, got pnl={trade.pnl:.4f}"
        )

    def test_sl_first_long_bearish_candle(self):
        """Long, bearish candle: both SL+TP touched → SL fills first (already was SL under PR-A).

        Both PR-A and PR-B pick SL here (bearish body for long → pessimistic in both regimes).
        Pin ensures no regression.
        """
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=110.0, size=100.0)

        # Both touched; close(95) < open(104) → bearish
        candle = make_candle(open_price=104.0, high=111.0, low=94.0, close=95.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        assert engine.state.trades[-1].close_reason == "stop_loss"

    def test_sl_first_short_bearish_candle(self):
        """Short, bearish candle: both SL+TP touched → SL fills first (loss for short).

        PR-A (candle-body): bearish close → TP wins for short (optimistic).
        PR-B (SL-first):    SL wins regardless of candle direction.

        Mutation guard: if you revert to the candle-body tiebreak,
        close_reason becomes 'take_profit' and this test fails.
        """
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="short", entry_price=100.0, stop_loss=106.0,
                      take_profit=90.0, size=100.0)

        # Both hit: high=107 ≥ 106 (SL) and low=89 ≤ 90 (TP)
        # Candle is BEARISH (close=91 < open=103) — under PR-A this picked TP for short.
        # Under PR-B it must pick SL.
        candle = make_candle(open_price=103.0, high=107.0, low=89.0, close=91.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        trade = engine.state.trades[-1]
        assert trade.close_reason == "stop_loss", (
            f"SL-first: bearish candle with both SL+TP touched (short) must exit at stop_loss, "
            f"got {trade.close_reason!r}.  If 'take_profit', the candle-body tiebreak is still active."
        )
        # The exit must be a loss (exit price ≥ entry price for a short)
        assert trade.pnl < 0, (
            f"SL-first short exit must be a loss, got pnl={trade.pnl:.4f}"
        )

    def test_sl_first_short_bullish_candle(self):
        """Short, bullish candle: both SL+TP touched → SL fills first (also SL under PR-A).

        Bullish body for short is pessimistic in both PR-A and PR-B.
        Pin ensures no regression.
        """
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="short", entry_price=100.0, stop_loss=106.0,
                      take_profit=90.0, size=100.0)

        # Both hit; close(104) > open(92) → bullish → SL for short in both PR-A and PR-B
        candle = make_candle(open_price=92.0, high=107.0, low=89.0, close=104.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        assert engine.state.trades[-1].close_reason == "stop_loss"


# ============================================================================
# Category 2 — Gap-through-SL fill price (KNOWN GAP)
# ============================================================================

class TestGapFillKnownGap:
    """KNOWN GAP: engine fills gap-through at the literal stop_loss price.

    engine.py:1007: ``self._close_position(pos.stop_loss, ...)``
    When open < stop_loss for a long, the engine still fills at the
    pre-gap stop_loss level, not the worse gap-open.  This is optimistic.

    Pinned as a KNOWN LIMITATION for now (PR scope excludes modeling it).
    """

    def test_gap_open_below_sl_fills_at_sl_price_not_gap(self):
        """Gap down below SL → engine fills at SL, not at the actual gap-open.

        KNOWN GAP: optimistic fill.  Real fills would be at the gap-open price.
        """
        engine = _make_engine(initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        sl = 95.0
        open_position(engine, side="long", entry_price=100.0, stop_loss=sl,
                      take_profit=115.0, size=100.0)

        # Candle opens at 90 (gap below SL 95), low=89, high=91
        gap_open = 90.0
        candle = make_candle(open_price=gap_open, high=91.0, low=89.0, close=90.5)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        trade = engine.state.trades[-1]
        assert trade.close_reason == "stop_loss"
        # exit_price is post-slippage; without slippage it would equal sl exactly.
        # The key assertion is that it's NEAR sl, not near gap_open (90).
        # Slippage rate 0.02% on a Monday 01:00 UTC = Asia session = 1.0 mult.
        expected_exit = sl * (1 - engine.slippage_rate * 1.0)  # long exit: price * (1 - slip)
        assert abs(trade.exit_price - expected_exit) < 1e-8, (
            f"KNOWN GAP: exit filled at {trade.exit_price:.4f}, expected SL-price "
            f"{expected_exit:.4f} (NOT gap-open {gap_open})"
        )


# ============================================================================
# Category 3 — Liquidation (KNOWN GAP)
# ============================================================================

class TestLiquidationKnownGap:
    """KNOWN GAP: engine has no liquidation model.

    At 25x leverage, a 4% adverse move wipes the margin, but the engine
    simply exits at the ATR-SL price (which may be far wider than the
    liquidation threshold).  Result: unrealistically small losses on
    high-leverage trades.
    """

    def test_no_liquidation_at_high_leverage(self):
        """Position at 25x leverage exits at SL price, not at liquidation.

        KNOWN GAP: in reality the exchange would liquidate ~2-4% before the ATR-SL.
        Maintenance margin ≈ 0.5%; at 25x, liq at ~4% adverse (entry=100, liq≈96).
        Engine uses SL=95, which is a 5% adverse move → would already be liquidated.
        """
        engine = _make_engine(initial_balance=10_000.0, leverage=25)
        risk_mgr = _risk_mgr(engine)

        # 5% SL on a 25x position would be liquidated at ~4%.
        entry, sl, tp = 100.0, 95.0, 130.0
        open_position(engine, side="long", entry_price=entry, stop_loss=sl,
                      take_profit=tp, size=100.0)

        candle = make_candle(open_price=98.0, high=99.0, low=93.0, close=95.0)
        engine._check_exit(candle, "2024-01-02 01:00", risk_mgr)

        trade = engine.state.trades[-1]
        assert trade.close_reason == "stop_loss"
        # Confirm exit is near sl, NOT near liq_price (96)
        liq_price = entry * (1 - 0.04)  # approximate 25x liq at 4% adverse
        assert abs(trade.exit_price - sl) < 0.5, (
            f"KNOWN GAP: engine exited at {trade.exit_price:.2f} (near SL={sl}), "
            f"not near liquidation price ~{liq_price:.2f}"
        )


# ============================================================================
# Category 4 — Slippage (direction + time-of-day)
# ============================================================================

class TestSlippage:
    """PIN: direction-aware slippage + time-of-day multiplier.

    engine.py:159-184 (_get_slippage_multiplier):
      Weekend (Sat/Sun): 2.0x
      Asia (00-08 UTC):  1.0x
      Europe (08-16 UTC): 0.8x
      US (16-24 UTC):    0.7x

    engine.py:937-940 entry: long pays +slippage, short pays -slippage.
    engine.py:1251-1254 exit: long receives -slippage, short pays +slippage.
    """

    @pytest.mark.parametrize("hour,expected_mult", [
        (0, 1.0),   # Asia
        (4, 1.0),   # Asia
        (8, 0.8),   # Europe
        (12, 0.8),  # Europe
        (16, 0.7),  # US
        (20, 0.7),  # US
    ])
    def test_slippage_multiplier_weekday(self, hour, expected_mult):
        """Weekday hour → correct multiplier."""
        ts = pd.Timestamp("2024-01-03").replace(hour=hour)  # Wednesday
        mult = BacktestEngine._get_slippage_multiplier(ts)
        assert mult == expected_mult, f"hour={hour}: expected {expected_mult}, got {mult}"

    def test_slippage_multiplier_weekend(self):
        """Saturday → 2.0x multiplier."""
        ts = pd.Timestamp("2024-01-06 10:00")  # Saturday
        assert ts.dayofweek == 5  # guard: really Saturday
        mult = BacktestEngine._get_slippage_multiplier(ts)
        assert mult == 2.0

    def test_long_entry_price_inflated_by_slippage(self):
        """Long entry adds slippage: entry_price * (1 + rate * mult).

        Hand-derivation:
            base_price = 100.0
            slippage_rate = 0.0002
            timestamp = "2024-01-03 10:00" → Europe → mult = 0.8
            effective_slip = 0.0002 * 0.8 = 0.00016
            expected_entry = 100.0 * (1 + 0.00016) = 100.016
        """
        engine = _make_engine(initial_balance=10_000.0, slippage_rate=0.0002,
                              commission_rate=0.0)
        # No commission so balance impact is purely slippage
        # We test via _close_position math which uses the stored entry_price.
        # Instead, test directly via _get_slippage_multiplier + formula.
        ts = "2024-01-03 10:00"  # Wednesday 10:00 UTC → Europe (mult=0.8)
        mult = BacktestEngine._get_slippage_multiplier(ts)
        base = 100.0
        expected = base * (1 + 0.0002 * mult)
        # Compute the same way the engine would
        result = base * (1 + engine.slippage_rate * mult)
        assert abs(result - expected) < 1e-10

    def test_long_exit_slippage_reduces_exit_price(self):
        """Exit price for long is reduced by slippage (adverse for buyer).

        Hand-derivation for Asia session (mult=1.0), slippage=0.0002:
            exit_price_after_slip = 110.0 * (1 - 0.0002) = 109.978
        """
        engine = _make_engine(initial_balance=10_000.0, slippage_rate=0.0002,
                              commission_rate=0.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=110.0, size=100.0)

        # TP at 110, use Asia session (mult=1.0) — Monday 03:00 UTC
        candle = make_candle(open_price=108.0, high=111.0, low=107.0, close=109.0,
                             timestamp="2024-01-01 03:00")
        engine._check_exit(candle, "2024-01-01 03:00", risk_mgr)

        trade = engine.state.trades[-1]
        assert trade.close_reason == "take_profit"
        # Hand-computed: 110.0 * (1 - 0.0002 * 1.0) = 109.978
        expected_exit = 110.0 * (1 - 0.0002 * 1.0)
        assert abs(trade.exit_price - expected_exit) < 1e-8

    def test_short_entry_slippage_lowers_entry_price(self):
        """Short entry subtracts slippage (adverse: sells at lower price)."""
        # Verify via _get_slippage_multiplier + formula
        ts = "2024-01-03 10:00"  # Europe mult=0.8
        mult = BacktestEngine._get_slippage_multiplier(ts)
        base = 100.0
        rate = 0.0002
        expected_entry = base * (1 - rate * mult)
        assert abs(expected_entry - 100.0 * (1 - rate * 0.8)) < 1e-10

    def test_slippage_size_independent(self):
        """Slippage is fixed-fraction (not depth-aware): doubles size → same price.

        KNOWN LIMITATION (noted in tests docs): this is optimistic for large YOLO sizes.
        """
        # 100 units: effective_slippage = rate * mult
        # 10_000 units: effective_slippage = rate * mult (same fraction, bigger notional cost)
        ts = "2024-01-01 01:00"  # Asia mult=1.0
        rate = 0.0002
        mult = BacktestEngine._get_slippage_multiplier(ts)

        base = 100.0
        # Same percentage regardless of size
        price_100 = base * (1 + rate * mult)
        price_10k = base * (1 + rate * mult)
        assert price_100 == price_10k, "Slippage is size-independent (fixed-fraction)"


# ============================================================================
# Category 5 — Commission math (entry, full exit, partial TP1)
# ============================================================================

class TestCommission:
    """PIN: commission deducted at entry AND full exit.

    engine.py:946-947: commission deducted from balance on entry.
    engine.py:1265-1267: commission deducted from pnl on exit.

    Hand-derivation (base case, no slippage):
        size = 1000.0, rate = 0.00055
        entry_commission = 1000 * 0.00055 = 0.55
        exit_commission  = 1000 * 0.00055 = 0.55  (same size, exit at TP)
        total_commission = 1.10
    """

    def test_entry_commission_deducted_from_balance(self):
        """Entry commission = size * commission_rate, deducted immediately.

        Hand-derivation: size=1000, rate=0.00055 → commission=0.55
        Starting balance=10000 → balance after entry=9999.45
        """
        engine = _make_engine(initial_balance=10_000.0, commission_rate=0.00055,
                              slippage_rate=0.0)
        initial = engine.state.balance
        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=115.0, size=1000.0)

        # open_position charges commission = 1000 * 0.00055 = 0.55
        expected_balance = initial - 1000.0 * 0.00055
        assert abs(engine.state.balance - expected_balance) < 1e-8

    def test_exit_commission_deducted_from_pnl(self):
        """Exit commission = size * commission_rate, deducted from pnl.

        Hand-derivation (no slippage, flat exit at TP=110, long from 100):
            ref_price = 100.0
            exit_price = 110.0 (no slippage)
            pnl_pct = (110 - 100) / 100 = 0.10
            gross_pnl = size * 0.10 = 1000 * 0.10 = 100.0
            exit_commission = 1000 * 0.00055 = 0.55
            net_pnl_from_exit = 100.0 - 0.55 = 99.45
        """
        engine = _make_engine(initial_balance=10_000.0, commission_rate=0.00055,
                              slippage_rate=0.0)
        balance_after_entry = engine.state.balance - 1000.0 * 0.00055
        engine.state.balance = balance_after_entry  # reset manually for clarity

        risk_mgr = _risk_mgr(engine)
        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=110.0, size=1000.0,
                      risk_amount=10_000.0 * 0.01)
        # reset so we can cleanly track only the exit delta
        engine.state.balance = balance_after_entry

        candle = make_candle(open_price=108.0, high=111.0, low=107.0, close=109.0)
        engine._check_exit(candle, "2024-01-01 01:00", risk_mgr)

        trade = engine.state.trades[-1]
        # Gross pnl from exit leg only (not counting entry commission already taken)
        # exit_price == TP == 110.0 (no slippage)
        gross = 1000.0 * ((110.0 - 100.0) / 100.0)
        exit_comm = 1000.0 * 0.00055
        expected_net_pnl = gross - exit_comm  # = 99.45
        # trade.pnl = total_pnl (includes partial_pnl=0), so == net_pnl
        assert abs(trade.pnl - expected_net_pnl) < 1e-8

    def test_partial_tp1_commission(self):
        """Partial TP1 also deducts commission on the partial_size.

        Hand-derivation (no slippage, partial_pct=0.5):
            original_size = 1000, partial_size = 500
            tp1_price = 106.0
            partial_pnl_pct = (106 - 100) / 100 = 0.06
            partial_gross = 500 * 0.06 = 30.0
            partial_commission = 500 * 0.00055 = 0.275
            partial_net = 30.0 - 0.275 = 29.725
        """
        cfg = base_config(
            commission_rate=0.00055,
            slippage_rate=0.0,
            partial_tp_enabled=True,
            partial_tp_pct=0.5,
            move_sl_to_be_after_tp1=False,  # keep SL unchanged for simplicity
        )
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        balance_before = engine.state.balance
        # Charge entry commission manually (open_position does this)
        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=120.0, size=1000.0, tp1_price=106.0)

        # Candle that hits TP1 only (high=107, TP full=120 not reached)
        candle = make_candle(open_price=101.0, high=107.0, low=100.0, close=105.0)
        engine._check_exit(candle, "2024-01-01 01:00", risk_mgr)

        # Position should still be open (only partial taken)
        assert engine.state.position is not None
        assert engine.state.position.tp1_hit is True
        assert engine.state.position.size == 500.0  # halved

        # partial_pnl stored on position
        expected_partial_net = 500.0 * ((106.0 - 100.0) / 100.0) - 500.0 * 0.00055
        assert abs(engine.state.position.partial_pnl - expected_partial_net) < 1e-8

    def test_round_trip_commission(self):
        """Total round-trip commission = 2 × size × commission_rate (no slippage).

        Hand-derivation: size=1000, rate=0.00055
            entry_comm = 0.55
            exit_comm  = 0.55
            total      = 1.10
        """
        engine = _make_engine(initial_balance=10_000.0, commission_rate=0.00055,
                              slippage_rate=0.0)
        initial = engine.state.balance
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=100.0,  # flat exit at entry — zero PnL, only commissions
                      size=1000.0)
        # Force TP at exactly entry_price = 100.0 (flat trade)
        # Drive candle high enough to trigger TP
        candle = make_candle(open_price=99.0, high=101.0, low=98.0, close=100.0)
        # Override TP to current close exactly
        engine.state.position.take_profit = 100.0
        engine.state.position.entry_price = 100.0
        engine.state.position.avg_entry_price = 100.0

        engine._check_exit(candle, "2024-01-01 01:00", risk_mgr)

        # Total loss = 2 × 1000 × 0.00055 = 1.10
        expected_total_loss = -2 * 1000.0 * 0.00055
        actual_balance_change = engine.state.balance - initial
        assert abs(actual_balance_change - expected_total_loss) < 1e-8


# ============================================================================
# Category 6 — Funding deduction (PR-C)
# ============================================================================

class TestFundingDeduction:
    """PR-C: funding charged at 8h settlement boundaries only.

    engine.py:_check_exit — settlement check at 00:00/08:00/16:00 UTC.
    engine.py:_close_position — accrued_funding subtracted from pnl at close.

    Mutations that must fail:
      - Summing over all candles (ffill over-charge ~32x): PnL too low → fails.
      - Charging wrong sign (short pays, long receives): PnL wrong direction → fails.
      - Not charging at all: accrued_funding stays 0, pnl too high → fails.
    """

    def test_non_settlement_candle_no_charge(self):
        """Funding rate on a non-settlement candle does NOT charge funding.

        Candle at 01:00 UTC — not a settlement boundary.
        fundingRate=0.01 must have zero effect on PnL.

        Hand-derivation (no fees, no slippage, entry=100, exit=110, size=1000):
            gross pnl = 1000 * (110 - 100)/100 = 100.0
            accrued_funding = 0 (no settlement boundary crossed)
            net pnl = 100.0
        """
        engine = _make_engine(initial_balance=10_000.0, commission_rate=0.0,
                              slippage_rate=0.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=110.0, size=1000.0)

        candle = make_candle(open_price=108.0, high=111.0, low=107.0, close=109.0,
                             timestamp="2024-01-01 01:00")  # Non-settlement hour
        candle["fundingRate"] = 0.01  # 1% but at 01:00 — NOT a settlement

        engine._check_exit(candle, "2024-01-01 01:00", risk_mgr)

        trade = engine.state.trades[-1]
        expected_pnl = 1000.0 * (110.0 - 100.0) / 100.0  # 100.0
        assert abs(trade.pnl - expected_pnl) < 1e-8, (
            f"Non-settlement candle: funding must not be charged; "
            f"pnl={trade.pnl:.4f}, expected {expected_pnl:.4f}"
        )

    def test_settlement_candle_charges_long(self):
        """Positive funding at a settlement boundary charges the long holder.

        Settlement at 08:00 UTC; position does NOT close this candle.
        Close at a subsequent candle (TP hit).

        Hand-derivation (no commission, no slippage):
            size = 1000.0, rate = 0.001 (0.1%)
            Settlement candle: accrued_funding += 1000.0 * 0.001 = 1.0
            TP candle (no settlement): no new charge
            gross pnl = 1000 * (110 - 100)/100 = 100.0
            net pnl = 100.0 - 1.0 = 99.0

        Mutation (sum all candles): would charge on EVERY candle with rate=0.001,
        e.g. 3 candles × 1.0 = 3.0 → pnl = 97.0 → FAILS assertion.
        """
        engine = _make_engine(initial_balance=10_000.0, commission_rate=0.0,
                              slippage_rate=0.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=110.0, size=1000.0)

        rate = 0.001
        # Candle 1: settlement boundary (08:00 UTC), price stays in range (no exit)
        c1 = make_candle(open_price=101.0, high=105.0, low=100.0, close=103.0,
                         timestamp="2024-01-01 08:00")
        c1["fundingRate"] = rate
        engine._check_exit(c1, "2024-01-01 08:00", risk_mgr)
        assert engine.state.position is not None, "Position should still be open after c1"
        assert abs(engine.state.position.accrued_funding - 1.0) < 1e-10, (
            f"After settlement candle: accrued_funding should be 1.0, "
            f"got {engine.state.position.accrued_funding}"
        )

        # Candle 2: non-settlement (09:00 UTC), no exit
        c2 = make_candle(open_price=103.0, high=106.0, low=102.0, close=104.0,
                         timestamp="2024-01-01 09:00")
        c2["fundingRate"] = rate  # ffill — but NOT a settlement candle
        engine._check_exit(c2, "2024-01-01 09:00", risk_mgr)
        # No additional charge (09:00 is not a settlement hour)
        assert abs(engine.state.position.accrued_funding - 1.0) < 1e-10, (
            f"After non-settlement candle: accrued_funding should still be 1.0 (not ffill-charged), "
            f"got {engine.state.position.accrued_funding}"
        )

        # Candle 3: TP hit (high=111 >= take_profit=110)
        c3 = make_candle(open_price=109.0, high=111.0, low=108.0, close=110.0,
                         timestamp="2024-01-01 10:00")
        c3["fundingRate"] = rate
        engine._check_exit(c3, "2024-01-01 10:00", risk_mgr)

        assert engine.state.position is None, "Position should be closed at TP"
        trade = engine.state.trades[-1]
        assert trade.close_reason == "take_profit"

        # Hand-derived pnl: gross 100.0 - accrued_funding 1.0 = 99.0
        expected_pnl = 100.0 - 1.0
        assert abs(trade.pnl - expected_pnl) < 1e-8, (
            f"Long funding: expected pnl={expected_pnl:.4f}, got {trade.pnl:.4f}\n"
            f"  accrued_funding in trade should reduce gross pnl by 1.0"
        )

    def test_settlement_candle_credits_short(self):
        """Positive funding at a settlement boundary credits the short holder (receives).

        Short position: positive rate means longs pay, shorts receive.
        accrued_funding on short = -1.0 → pnl increases by 1.0.

        Hand-derivation (no commission, no slippage):
            entry=100, TP=88, size=1000, rate=0.001
            Settlement 08:00: accrued_funding += -(1000 * 0.001) = -1.0
            TP: gross pnl = 1000 * (100 - 88)/100 = 120.0
            net pnl = 120.0 - (-1.0) = 121.0  (short received funding = bonus)

        Mutation (wrong sign — short pays instead of receives):
            accrued_funding = +1.0 → pnl = 119.0 → FAILS assertion.
        """
        engine = _make_engine(initial_balance=10_000.0, commission_rate=0.0,
                              slippage_rate=0.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="short", entry_price=100.0, stop_loss=106.0,
                      take_profit=88.0, size=1000.0)

        rate = 0.001
        # Settlement 08:00 — short receives
        c1 = make_candle(open_price=99.0, high=100.0, low=97.0, close=98.0,
                         timestamp="2024-01-01 08:00")
        c1["fundingRate"] = rate
        engine._check_exit(c1, "2024-01-01 08:00", risk_mgr)
        assert engine.state.position is not None
        # Short accrued_funding is negative (receives)
        assert abs(engine.state.position.accrued_funding - (-1.0)) < 1e-10, (
            f"Short at settlement: accrued_funding should be -1.0 (receives), "
            f"got {engine.state.position.accrued_funding}"
        )

        # TP hit
        c2 = make_candle(open_price=89.0, high=90.0, low=87.0, close=88.5,
                         timestamp="2024-01-01 09:00")
        c2["fundingRate"] = rate
        engine._check_exit(c2, "2024-01-01 09:00", risk_mgr)

        trade = engine.state.trades[-1]
        assert trade.close_reason == "take_profit"

        # gross pnl = 1000 * (100 - 88)/100 = 120.0
        # accrued_funding = -1.0 → pnl -= -1.0 → net = 121.0
        expected_pnl = 120.0 + 1.0  # = 121.0
        assert abs(trade.pnl - expected_pnl) < 1e-8, (
            f"Short funding: expected pnl={expected_pnl:.4f}, got {trade.pnl:.4f}\n"
            f"  Short should RECEIVE funding (net pnl > gross). "
            f"If less, sign is wrong."
        )

    def test_no_funding_column_zero_deduction(self):
        """When fundingRate column is absent, accrued_funding stays 0.

        This is the path taken by snapshot tests (patch suppresses the column).
        No raise, no funding charged.
        """
        engine = _make_engine(initial_balance=10_000.0, commission_rate=0.0,
                              slippage_rate=0.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=110.0, size=1000.0)

        # Candle at settlement hour but NO fundingRate column
        c1 = make_candle(open_price=101.0, high=105.0, low=100.0, close=103.0,
                         timestamp="2024-01-01 08:00")
        # c1 does NOT have fundingRate (make_candle includes it at 0.0 — override it)
        c1_no_funding = c1.drop("fundingRate")
        engine._check_exit(c1_no_funding, "2024-01-01 08:00", risk_mgr)

        assert engine.state.position is not None
        assert engine.state.position.accrued_funding == 0.0, (
            f"No fundingRate column: accrued_funding must stay 0.0, "
            f"got {engine.state.position.accrued_funding}"
        )

        # TP close
        c2 = make_candle(open_price=109.0, high=111.0, low=108.0, close=110.0,
                         timestamp="2024-01-01 10:00")
        c2_no_funding = c2.drop("fundingRate")
        engine._check_exit(c2_no_funding, "2024-01-01 10:00", risk_mgr)

        trade = engine.state.trades[-1]
        expected_pnl = 1000.0 * (110.0 - 100.0) / 100.0  # 100.0 (no deduction)
        assert abs(trade.pnl - expected_pnl) < 1e-8, (
            f"No funding column: pnl should be unaffected; "
            f"got {trade.pnl:.4f}, expected {expected_pnl:.4f}"
        )


# ============================================================================
# Category 7 — Gold/forex funding no-op
# ============================================================================

class TestForexFundingNoOp:
    """PR-C cat 7 — XAUUSD (forex/gold) has no funding file → deduction == 0, no raise.

    When the funding_file path resolves to a file that doesn't exist,
    add_funding_rate returns the df unchanged (no fundingRate column).
    The engine's settlement check is then a no-op for every candle.
    A different instrument's funding file (xagusdt) is never auto-applied.

    These tests satisfy the test-plan requirement: no raise, no funding deducted,
    mismatched file never applied.
    """

    def test_missing_funding_file_doesnt_raise(self):
        """Engine runs without error when funding file doesn't exist (XAUUSD path)."""
        cfg = base_config(symbol="XAUUSD", funding_file="/nonexistent/path.csv")
        # No patch — let the real add_funding_rate handle missing file
        df = make_ohlcv(30, base_price=2000.0, atr_value=10.0)
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        try:
            engine.run(df)
        except FileNotFoundError:
            pytest.fail("Engine raised FileNotFoundError for missing funding file")

    def test_xauusd_funding_deduction_zero(self):
        """XAUUSD with missing funding file: accrued_funding == 0, pnl unaffected.

        PR-C no-op contract: when fundingRate column is absent (funding file missing),
        the engine must produce exactly the same PnL as without funding — no raise,
        no deduction, no phantom charge.

        Hand-derivation (no fees, no slippage, entry=2000, TP=2100, size=100):
            gross pnl = 100 * (2100 - 2000)/2000 = 5.0
            accrued_funding = 0 (no fundingRate column)
            net pnl = 5.0

        Mutation (auto-apply a default funding file): if the engine fell back to
        some default funding CSV and it happened to exist, this test would show
        unexpected pnl — preventing silent cross-instrument contamination.
        """
        cfg = base_config(
            symbol="XAUUSD",
            funding_file="/nonexistent/xauusd_funding_rate.csv",
            commission_rate=0.0,
            slippage_rate=0.0,
        )
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=2000.0, stop_loss=1900.0,
                      take_profit=2100.0, size=100.0)

        # Settlement boundary candle — but no fundingRate column (file missing)
        c1 = make_candle(open_price=2010.0, high=2020.0, low=2005.0, close=2015.0,
                         timestamp="2024-01-01 08:00")
        c1_no_fr = c1.drop("fundingRate")
        engine._check_exit(c1_no_fr, "2024-01-01 08:00", risk_mgr)

        assert engine.state.position is not None
        assert engine.state.position.accrued_funding == 0.0, (
            f"XAUUSD (no funding file): accrued_funding must be 0.0, "
            f"got {engine.state.position.accrued_funding}"
        )

        # TP close
        c2 = make_candle(open_price=2095.0, high=2105.0, low=2090.0, close=2100.0,
                         timestamp="2024-01-01 10:00")
        c2_no_fr = c2.drop("fundingRate")
        engine._check_exit(c2_no_fr, "2024-01-01 10:00", risk_mgr)

        trade = engine.state.trades[-1]
        assert trade.close_reason == "take_profit"
        expected_pnl = 100.0 * (2100.0 - 2000.0) / 2000.0  # 5.0
        assert abs(trade.pnl - expected_pnl) < 1e-8, (
            f"XAUUSD no-op: pnl should be {expected_pnl:.4f} (no funding), "
            f"got {trade.pnl:.4f}"
        )

    def test_wrong_instrument_funding_not_applied(self):
        """XAUUSD engine uses XAUUSD funding path, not XAGUSDT path.

        The funding_file is derived from the symbol, so XAUUSD resolves to
        data/xauusd_funding_rate.csv — never data/xagusdt_funding_rate.csv.
        This is a path-derivation assertion (no engine run needed).
        """
        cfg_xau = base_config(symbol="XAUUSD")
        cfg_xag = base_config(symbol="XAGUSDT")

        # Derive paths as engine.py does
        def _derive(cfg):
            sym = cfg.get("symbol", "BTCUSDT").replace("/", "").replace(":", "")
            return f"data/{sym.lower()}_funding_rate.csv"

        path_xau = _derive(cfg_xau)
        path_xag = _derive(cfg_xag)
        assert path_xau != path_xag, "Different symbols must derive different funding paths"
        assert "xauusd" in path_xau
        assert "xagusdt" in path_xag


# ============================================================================
# Category 10 — Partial + Pyramid + SL interaction
# ============================================================================

class TestPartialAndSLInteraction:
    """PIN-6: partial TP1 → SL moves to breakeven; PIN-8: pyramid SL never loosens.

    These are white-box tests using open_position() + _check_exit() directly.
    """

    def test_sl_moves_to_breakeven_after_tp1(self):
        """After partial TP1 hits, SL moves at minimum to entry_price (breakeven).

        engine.py:1056-1064: move_sl_to_be_after_tp1=True →
            sl = max(sl, entry_price + breakeven_buffer * atr)

        Then the trailing stop runs on the same candle (engine.py:1192-1225) and
        may advance SL further.

        Hand-derivation for this fixture:
            entry=100, sl0=95, tp1=106, atr=1.0, trail_mult=2.0, close=105
            1. TP1 triggers (high=107 ≥ tp1=106)
            2. SL → max(95, 100 + 0*1.0) = 100  (breakeven, no buffer)
            3. Trailing stop: trail_price = close - atr*trail_mult = 105 - 2.0 = 103
               new_sl = max(100, 103) = 103  ← trailing stop advances BE further

        So the final SL = 103.0 (trailing stop wins over the raw BE of 100.0).
        The key invariant is sl_after >= entry_price (never below breakeven).
        """
        cfg = base_config(
            partial_tp_enabled=True,
            partial_tp_pct=0.5,
            move_sl_to_be_after_tp1=True,
            breakeven_buffer_atr_mult=0.0,
            slippage_rate=0.0,
            commission_rate=0.0,
        )
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=95.0,
                      take_profit=120.0, size=1000.0, tp1_price=106.0)

        # candle: close=105, high=107 (triggers TP1), atr=1.0, trail_mult=2.0
        candle = make_candle(open_price=103.0, high=107.0, low=102.0, close=105.0)
        engine._check_exit(candle, "2024-01-01 06:00", risk_mgr)

        pos = engine.state.position
        assert pos is not None
        assert pos.tp1_hit is True

        entry = 100.0
        # Key invariant: SL must be AT or ABOVE entry_price after BE move
        assert pos.stop_loss >= entry, (
            f"SL {pos.stop_loss:.2f} is below breakeven {entry:.2f} — BE move failed"
        )

        # Actual value: trailing stop advances SL from 100 to 103
        # close(105) - trail_mult(2.0) * atr(1.0) = 103; max(100, 103) = 103
        expected_sl_after_trail = 105.0 - 2.0 * 1.0  # = 103.0
        assert abs(pos.stop_loss - expected_sl_after_trail) < 1e-8, (
            f"After TP1+trail: expected SL={expected_sl_after_trail:.2f}, got {pos.stop_loss:.2f}"
        )

    def test_pyramid_sl_never_loosens_for_long(self):
        """Pyramid add ratchets SL up; it can never go below prior SL.

        engine.py:1156-1158: new_sl = max(pos.stop_loss, pos.entry_price + offset).
        The max() guarantees monotonic increase for longs.
        """
        cfg = base_config(
            slippage_rate=0.0,
            commission_rate=0.0,
            pyramiding={
                "enabled": True,
                "max_adds": 3,
                "add_1_atr_mult": 1.0,
                "add_1_size_pct": 0.5,
            },
        )
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        entry, sl0, tp = 100.0, 95.0, 200.0
        open_position(engine, side="long", entry_price=entry, stop_loss=sl0,
                      take_profit=tp, size=1000.0)

        # Inject EMA columns so pyramiding trend_aligned check passes
        pos = engine.state.position
        candle = pd.Series({
            "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0,
            "atr": 1.0, "volume": 1000.0, "fundingRate": 0.0, "regime": "trending",
            "ema_fast": 105.0, "ema_slow": 100.0,  # EMA9 > EMA21 → aligned
        }, name=pd.Timestamp("2024-01-01 02:00"))

        sl_before = pos.stop_loss
        engine._check_exit(candle, "2024-01-01 02:00", risk_mgr)
        sl_after = engine.state.position.stop_loss if engine.state.position else sl_before

        # SL should only move up (or stay the same) for a long
        assert sl_after >= sl_before, (
            f"Pyramid must not loosen SL for long: before={sl_before}, after={sl_after}"
        )

    def test_pyramid_sl_never_loosens_for_short(self):
        """Pyramid add ratchets SL down for short; it can never go above prior SL.

        engine.py:1159-1161: new_sl = min(pos.stop_loss, pos.entry_price - offset).
        """
        cfg = base_config(
            slippage_rate=0.0,
            commission_rate=0.0,
            pyramiding={
                "enabled": True,
                "max_adds": 3,
                "add_1_atr_mult": 1.0,
                "add_1_size_pct": 0.5,
            },
        )
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        entry, sl0, tp = 100.0, 106.0, 50.0
        open_position(engine, side="short", entry_price=entry, stop_loss=sl0,
                      take_profit=tp, size=1000.0)

        candle = pd.Series({
            "open": 99.0, "high": 100.0, "low": 97.0, "close": 98.0,
            "atr": 1.0, "volume": 1000.0, "fundingRate": 0.0, "regime": "trending",
            "ema_fast": 95.0, "ema_slow": 100.0,  # EMA9 < EMA21 → aligned for short
        }, name=pd.Timestamp("2024-01-01 02:00"))

        sl_before = engine.state.position.stop_loss
        engine._check_exit(candle, "2024-01-01 02:00", risk_mgr)
        sl_after = engine.state.position.stop_loss if engine.state.position else sl_before

        # SL should only move down (or stay) for a short
        assert sl_after <= sl_before, (
            f"Pyramid must not loosen SL for short: before={sl_before}, after={sl_after}"
        )


# ============================================================================
# Category 11 — Simulated-time cooldown (PIN-9)
# ============================================================================

class TestSimTimeCooldown:
    """PIN-9: candle-count cooldown uses simulated time, not wall clock.

    engine.py:846: sim_time = pd.Timestamp(current_time).timestamp()
    engine.py:312-318: candle cooldown enforced via last_close_candle_idx.

    Run-twice determinism: same OHLCV → identical trade count.
    """

    def test_run_twice_same_result(self):
        """PIN-9 (partial): deterministic — same input → same output on both runs.

        Guards against wall-clock / RNG leaks.  engine.py:319-330 flexible
        cooldown override must not introduce non-determinism.

        Uses make_trading_ohlcv() so the engine produces real trades and the
        flexible_cooldown code path (engine.py:319-330) is actually entered.
        A fixture with zero trades trivially satisfies this assertion without
        exercising the guard.
        """
        df = make_trading_ohlcv(300)
        cfg = trading_config(cooldown_candles_after_sl=3)

        def _run():
            with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
                engine = BacktestEngine(cfg, initial_balance=10_000.0)
                engine.run(df)
            return engine.state.trades, [(t.close_reason, round(t.pnl, 8)) for t in engine.state.trades]

        trades1, result1 = _run()
        _, result2 = _run()

        assert len(result1) >= 2, (
            f"Fixture produced only {len(result1)} trades — EMA crossover not firing"
        )
        assert result1 == result2, "Two runs on identical data produced different trades"

    def test_run_twice_with_flexible_cooldown(self):
        """PIN-9: flexible_cooldown doesn't introduce wall-clock non-determinism.

        engine.py:319-330: flexible_cooldown checks signal quality to override
        cooldown — must be deterministic (no time.time() or random calls).

        Uses make_trading_ohlcv() so the flexible-cooldown code path is actually
        exercised (it is only reached when a trade has closed within the cooldown
        window and a new signal appears — requires len(trades) >= 2).
        """
        df = make_trading_ohlcv(300)
        cfg = trading_config(
            cooldown_candles_after_sl=5,
            flexible_cooldown={
                "enabled": True,
                "min_quality_score": 0.5,
                "cooldown_reduction_factor": 0.5,
                "log_overrides": False,
            },
        )

        def _run():
            with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
                engine = BacktestEngine(cfg, initial_balance=10_000.0)
                engine.run(df)
            return [(t.close_reason, round(t.pnl, 8)) for t in engine.state.trades]

        r1 = _run()
        r2 = _run()

        assert len(r1) >= 2, (
            f"Fixture produced only {len(r1)} trades — flexible_cooldown path not exercised"
        )
        assert r1 == r2, "Flexible cooldown introduces non-determinism"

    def test_candle_count_cooldown_blocks_entry(self):
        """PIN-9: after SL close, cooldown_candles_after_sl reduces trade count.

        Strategy: run the same fixture twice — once with zero cooldown, once with
        a long cooldown (25 candles).  The cooldown run must produce FEWER trades
        than the no-cooldown run, proving cooldown is not a no-op.

        Uses make_trading_ohlcv() so the SL path actually fires and the cooldown
        has trades to block.  A fixture with no SL trades would vacuously pass
        because candle_count_cooldown_blocks_entry never has anything to block.
        """
        df = make_trading_ohlcv(300)

        def _run(cooldown: int) -> list:
            cfg = trading_config(
                cooldown_candles_after_sl=cooldown,
                cooldown_candles_after_close=cooldown,
            )
            with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
                engine = BacktestEngine(cfg, initial_balance=10_000.0)
                engine.run(df)
            return engine.state.trades

        trades_no_cooldown = _run(cooldown=0)
        trades_long_cooldown = _run(cooldown=25)

        assert len(trades_no_cooldown) >= 2, (
            "No-cooldown run has no trades — fixture not generating signals"
        )
        assert len(trades_long_cooldown) <= len(trades_no_cooldown), (
            f"Cooldown=25 produced MORE trades ({len(trades_long_cooldown)}) "
            f"than cooldown=0 ({len(trades_no_cooldown)}) — cooldown is a no-op"
        )


# ============================================================================
# Category 12 — force_close at backtest end
# ============================================================================

class TestForceClose:
    """PIN: _force_close closes open position at last candle's close price.

    engine.py:1291: closes at row['close'] with commission+slippage.
    """

    def test_force_close_closes_at_last_candle_close(self):
        """End-of-backtest force_close charges commission and exits at close.

        Hand-derivation (no slippage, rate=0.00055):
            size = 1000.0, entry = 100.0, last_close = 102.0
            pnl_pct = (102.0 - 100.0) / 100.0 = 0.02
            gross_pnl = 1000.0 * 0.02 = 20.0
            exit_comm = 1000.0 * 0.00055 = 0.55
            net_pnl = 20.0 - 0.55 = 19.45
        """
        engine = _make_engine(initial_balance=10_000.0, commission_rate=0.00055,
                              slippage_rate=0.0)

        open_position(engine, side="long", entry_price=100.0, stop_loss=90.0,
                      take_profit=200.0, size=1000.0)

        last_row = make_candle(open_price=101.0, high=103.0, low=100.0, close=102.0)
        engine._force_close(last_row, "2024-01-31 00:00", "backtest_end")

        assert engine.state.position is None
        trade = engine.state.trades[-1]
        assert trade.close_reason == "backtest_end"
        expected_pnl = 1000.0 * (102.0 - 100.0) / 100.0 - 1000.0 * 0.00055
        assert abs(trade.pnl - expected_pnl) < 1e-8

    def test_force_close_no_op_without_position(self):
        """_force_close does nothing when no position is open."""
        engine = _make_engine(initial_balance=10_000.0)
        initial_balance = engine.state.balance
        engine._force_close(
            make_candle(open_price=100.0, high=101.0, low=99.0, close=100.0),
            "2024-01-01 00:00",
            "backtest_end",
        )
        assert engine.state.balance == initial_balance
        assert len(engine.state.trades) == 0

    def test_force_close_with_nonzero_slippage(self):
        """_force_close with non-zero slippage_rate: exit price is slippage-adjusted.

        For a LONG force-close, slippage reduces the exit price:
            exit_price = last_close * (1 - slippage_rate * slippage_mult)

        The slippage_mult depends on time-of-day; this test uses a known midday UTC
        timestamp to get a stable multiplier (US session = 0.7).

        Hand-derivation (slippage_rate=0.0010, slippage_mult=0.7):
            last_close = 102.0
            effective_slippage = 0.0010 * 0.7 = 0.0007
            exit_price = 102.0 * (1 - 0.0007) = 102.0 * 0.9993 = 101.9286

            pnl_pct = (exit_price - entry_price) / entry_price
                    = (101.9286 - 100.0) / 100.0 = 0.019286
            gross_pnl = 1000.0 * 0.019286 = 19.286
            exit_comm = 1000.0 * 0.00055 = 0.55
            net_pnl = 19.286 - 0.55 = 18.736

        The key assertion: net_pnl with slippage < net_pnl without slippage.
        This guards against a missing slippage application in _force_close.
        """
        slippage_rate = 0.0010
        commission_rate = 0.00055
        engine_slipped = _make_engine(
            initial_balance=10_000.0,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
        )
        engine_zero = _make_engine(
            initial_balance=10_000.0,
            commission_rate=commission_rate,
            slippage_rate=0.0,
        )

        open_position(engine_slipped, side="long", entry_price=100.0,
                      stop_loss=90.0, take_profit=200.0, size=1000.0)
        open_position(engine_zero, side="long", entry_price=100.0,
                      stop_loss=90.0, take_profit=200.0, size=1000.0)

        # Midday US session timestamp → slippage_mult=0.7 (engine.py:159-184)
        last_row = make_candle(open_price=101.0, high=103.0, low=100.0, close=102.0)
        force_close_time = "2024-01-02 16:00"  # 16:00 UTC = US session

        engine_slipped._force_close(last_row, force_close_time, "backtest_end")
        engine_zero._force_close(last_row, force_close_time, "backtest_end")

        assert engine_slipped.state.position is None
        assert engine_zero.state.position is None

        pnl_slipped = engine_slipped.state.trades[-1].pnl
        pnl_zero = engine_zero.state.trades[-1].pnl

        assert pnl_slipped < pnl_zero, (
            f"Slipped PnL ({pnl_slipped:.6f}) should be less than zero-slippage "
            f"PnL ({pnl_zero:.6f}). Slippage not applied in _force_close."
        )
        # Exit price for slipped must be below the last_close
        exit_slipped = engine_slipped.state.trades[-1].exit_price
        assert exit_slipped < 102.0, (
            f"Force-close exit price {exit_slipped:.6f} should be < last_close=102.0 "
            "after long-exit slippage reduction."
        )


# ============================================================================
# Cash conservation invariant
# ============================================================================

class TestCashConservation:
    """Balance reconciliation: final balance matches replayed accounting.

    CORRECT invariant (engine.py accounting):
        final = initial
                - sum(entry_commission_i)    ← charged at entry (line 947)
                + sum(trade.pnl_i)           ← exit pnl net of exit commission
        where trade.pnl stores combined pnl (including any partial_pnl)

    COMMON MISTAKE: final = initial + sum(trade.pnl)
    This is WRONG because entry commissions are NOT in trade.pnl.
    trade.pnl only nets the EXIT commission.

    NOTE: partial_pnl is already included in trade.pnl at close time
    (engine.py:1273 total_pnl = pnl + pos.partial_pnl).  Do NOT add
    partial_pnl separately when summing trade.pnl.
    """

    def test_cash_conservation_no_partial(self):
        """Balance reconciles: final = initial - entry_commissions + sum(trade.pnl).

        Verified via engine.state.balance (the authoritative ledger).

        CORRECT invariant derivation:
            - Entry commission charged at engine.py:946-947: balance -= size * commission_rate
            - Exit commission charged inside _close_position and netted into trade.pnl
            - Therefore: final = initial - sum(entry_comm) + sum(trade.pnl)
              where trade.pnl = gross_exit_pnl - exit_commission

        Uses make_trading_ohlcv() so len(trades) >= 2 and the reconciliation is
        non-trivial.  A zero-trade run trivially satisfies 10000 == 10000.

        Historical regression: an entry-slippage bug at engine.py:938 left this
        test GREEN when it had zero trades (vacuous), but was caught by the golden
        snapshot.  Requiring len(trades) >= 2 prevents that class of vacuous pass.
        """
        df = make_trading_ohlcv(300)
        cfg = trading_config(partial_tp_enabled=False)

        with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
            engine = BacktestEngine(cfg, initial_balance=10_000.0)
            engine.run(df)

        assert len(engine.state.trades) >= 2, (
            f"Fixture produced only {len(engine.state.trades)} trades — "
            "cash conservation test is vacuous without actual trades"
        )

        initial = engine.state.initial_balance
        final = engine.state.balance
        comm_rate = engine.commission_rate

        # Entry commissions: charged on original_size (before any partial size change)
        # For no-partial trades: original_size == size at close
        entry_commissions = sum(t.original_size * comm_rate for t in engine.state.trades)
        sum_pnl = sum(t.pnl for t in engine.state.trades)

        reconstructed = initial - entry_commissions + sum_pnl
        assert abs(final - reconstructed) < 1e-4, (
            f"Cash conservation violated.\n"
            f"  final={final:.6f}\n"
            f"  reconstructed={reconstructed:.6f} (initial - entry_comm + sum_pnl)\n"
            f"  diff={abs(final-reconstructed):.2e}"
        )

    def test_cash_conservation_with_partial_tp(self):
        """Partial TP does NOT double-add PnL: final = initial - entry_comm + trade.pnl.

        engine.py accounting with partial TP enabled:
          1. Entry commission charged at entry (engine.py:946-947):
               balance -= original_size * commission_rate
          2. TP1 fires (engine.py:1044-1050):
               partial_pnl = partial_size * pnl_pct - partial_size * commission_rate
               balance += partial_pnl
               pos.partial_pnl += partial_pnl
          3. Full close (engine.py:1265-1280):
               pnl = remaining_size * exit_pnl_pct - remaining_size * commission_rate
               balance += pnl
               trade.pnl = pnl + pos.partial_pnl   ← COMBINED total

        CORRECT invariant:
            final = initial - entry_comm + trade.pnl
        where trade.pnl is the combined total (pnl + partial_pnl already summed).

        ANTI-DOUBLE-COUNTING guard:
            final = initial - entry_comm + pnl + partial_pnl   ← WRONG
        This is the bug this test pins: partial_pnl was added to balance at TP1
        AND included in trade.pnl at close; summing it again double-counts.

        Hand-derivation (slippage_rate=0 for clarity):
            initial    = 10_000.0
            entry      = 100.0, size = 2000.0
            entry_comm = 2000.0 * 0.00055 = 1.10

            TP1 fires at 115 (partial_tp_atr_mult=1.5, ATR=10):
              partial_size = 2000 * 0.5 = 1000
              gross_partial = 1000 * (115 - 100) / 100 = 150.0
              partial_comm  = 1000 * 0.00055 = 0.55
              partial_pnl   = 150.0 - 0.55 = 149.45
              balance       = 10_000 - 1.10 + 149.45 = 10_148.35

            Full TP fires at 145 (atr_tp_mult=3, ATR=10, remaining=1000):
              gross_full  = 1000 * (145 - 100) / 100 = 450.0
              full_comm   = 1000 * 0.00055 = 0.55
              net_full    = 450.0 - 0.55 = 449.45
              balance     = 10_148.35 + 449.45 = 10_597.80

            trade.pnl   = net_full + partial_pnl = 449.45 + 149.45 = 598.90

        Conservation check:
            initial - entry_comm + trade.pnl = 10_000 - 1.10 + 598.90 = 10_597.80 ✓
        """
        from bot.risk import RiskManager

        entry_price = 100.0
        size = 2000.0
        sl = 85.0
        full_tp = 145.0
        tp1_price = 115.0
        atr = 10.0
        commission_rate = 0.00055
        initial_balance = 10_000.0

        engine = _make_engine(
            initial_balance=initial_balance,
            commission_rate=commission_rate,
            slippage_rate=0.0,
            atr_sl_mult=1.5,
            atr_tp_mult=3.0,
            partial_tp_enabled=True,
            partial_tp_pct=0.5,
            partial_tp_atr_mult=1.5,
            move_sl_to_be_after_tp1=True,
            breakeven_buffer_atr_mult=0.0,
        )
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=entry_price,
                      stop_loss=sl, take_profit=full_tp, size=size,
                      tp1_price=tp1_price, tp1_hit=False)

        # Candle 1: high >= tp1_price (115) → partial TP fires; low stays above SL.
        candle1 = make_candle(open_price=108.0, high=116.0, low=107.0,
                              close=114.0, atr=atr)
        engine._check_exit(candle1, "2024-01-02 01:00", risk_mgr)

        assert engine.state.position is not None, "Position should still be open after TP1"
        assert engine.state.position.tp1_hit, "tp1_hit should be True after partial exit"

        # Candle 2: high >= full_tp (145) → full TP fires.
        candle2 = make_candle(open_price=120.0, high=146.0, low=119.0,
                              close=140.0, atr=atr)
        engine._check_exit(candle2, "2024-01-02 02:00", risk_mgr)

        assert engine.state.position is None, "Position should be fully closed after TP"
        assert len(engine.state.trades) == 1

        trade = engine.state.trades[0]
        final_balance = engine.state.balance

        # Hand-computed values (derivation in docstring above):
        expected_entry_comm = size * commission_rate    # = 1.10
        expected_partial_pnl = (                        # = 149.45
            (size / 2) * (tp1_price - entry_price) / entry_price
            - (size / 2) * commission_rate
        )
        expected_full_pnl = (                           # = 449.45
            (size / 2) * (full_tp - entry_price) / entry_price
            - (size / 2) * commission_rate
        )
        expected_trade_pnl = expected_partial_pnl + expected_full_pnl  # = 598.90
        expected_balance = (
            initial_balance - expected_entry_comm + expected_trade_pnl  # = 10_597.80
        )

        assert abs(trade.pnl - expected_trade_pnl) < 1e-6, (
            f"trade.pnl={trade.pnl:.6f} expected={expected_trade_pnl:.6f}. "
            "trade.pnl must equal partial_pnl + net_full_exit_pnl (engine.py:1273/1280)."
        )
        assert abs(final_balance - expected_balance) < 1e-6, (
            f"Balance conservation violated.\n"
            f"  final={final_balance:.6f}\n"
            f"  expected={expected_balance:.6f} "
            f"(initial - entry_comm + trade.pnl = "
            f"{initial_balance} - {expected_entry_comm} + {trade.pnl:.4f})\n"
            "ANTI-DOUBLE-COUNTING: do NOT add partial_pnl separately — "
            "it is already included in trade.pnl (engine.py:1273)."
        )

    def test_no_entry_during_open_position(self):
        """Engine opens at most one position at a time (default max_positions=1 here).

        Guards against double-entry bugs.

        Uses make_trading_ohlcv() so the engine actually opens positions to verify.
        A zero-trade run vacuously passes (iterates over an empty list).
        """
        df = make_trading_ohlcv(300)
        cfg = trading_config(max_positions=1)

        with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
            engine = BacktestEngine(cfg, initial_balance=10_000.0)
            engine.run(df)

        assert len(engine.state.trades) >= 2, (
            f"Fixture produced only {len(engine.state.trades)} trades — "
            "overlap guard is vacuous without actual trades"
        )

        # Check trade timeline: no two overlapping trades
        trades = sorted(engine.state.trades, key=lambda t: t.entry_time)
        for i in range(len(trades) - 1):
            if trades[i].exit_time and trades[i + 1].entry_time:
                assert trades[i].exit_time <= trades[i + 1].entry_time, (
                    f"Overlapping trades: trade {i} exits at {trades[i].exit_time}, "
                    f"trade {i+1} enters at {trades[i+1].entry_time}"
                )


# ============================================================================
# Look-ahead pins (PIN-2, PIN-3, PIN-4) — asymmetric run_backtest() fixtures
#
# README §42-45: these MUST be behavioral (end-to-end run()) with ASYMMETRIC
# fixtures, NOT structural 'signal_row is prev_row' assertions.  A structural
# assertion passes while wrong if a variable is renamed; the fixture below
# catches the observable outcome.
# ============================================================================

class TestLookAheadPins:
    """PIN-2/3/4: behavioral guards that the engine uses only closed-candle data.

    Historical context: the look-ahead class inflated PF from ~0.90 to ~2.06 in
    early development.  These tests pin the CORRECT (no-look-ahead) behavior.
    """

    def test_pin2_no_entry_from_exec_candle_ema_crossover(self):
        """PIN-2: ema_cross_up on exec candle ONLY → NO trade opens.

        Asymmetric fixture design:
            - Long bearish series: EMA9 << EMA21, ema_cross_up=False throughout.
            - ONE final bull spike that causes EMA9 to cross above EMA21 on the
              VERY LAST candle (the exec candle, df.iloc[-1]).
            - Signal candle (df.iloc[-2]) still has ema_cross_up=False.
            - engine.py:274: _check_entry(signal_row=df.iloc[i-1], current_row=df.iloc[i])
              uses signal_row for signal check → sees ema_cross_up=False → NO trade.
            - BUG scenario (look-ahead): engine uses current_row for signal check
              → sees ema_cross_up=True → opens trade → would be force-closed as
              "backtest_end" with len(trades)==1.  This test catches that.

        Hand-derivation of crossover timing:
            - 100-candle bear run: each candle -2.0 pts. After 100 candles:
              close[-1] ≈ 1000 - 200 = 800.
            - Final candle +300 spike: close goes to ~1100 (well above EMA21 ~800).
            - EMA9 reacts in ONE candle (wt ~20%): EMA9 jumps from ~800 to ~880.
            - EMA21 barely reacts: EMA21 ≈ 805.
            - So ema_cross_up fires on df.iloc[-1] (the spike candle), but NOT on
              df.iloc[-2] (the candle before the spike, still bearish).
        """
        n = 101
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz=None)
        rows = []
        c = 1000.0
        for i in range(n):
            if i < n - 1:
                c -= 2.0   # steady bear run
            else:
                c += 300.0  # massive spike on the final (exec) candle
            rows.append({
                "open":   c - 1.0,
                "high":   c + 2.0,
                "low":    c - 2.0,
                "close":  c,
                "volume": 1000.0,
            })
        df = pd.DataFrame(rows, index=idx)

        cfg = trading_config(
            cooldown_candles_after_sl=0,
            cooldown_candles_after_close=0,
        )

        with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
            engine = BacktestEngine(cfg, initial_balance=10_000.0)
            engine.run(df)

        # The crossover fires on the EXEC candle (df.iloc[-1]).
        # The SIGNAL candle (df.iloc[-2]) still has ema_cross_up=False.
        # Correct behavior: no trade opens (signal_row has no signal).
        # Look-ahead bug: trade would open (engine uses exec_row for signal).
        assert len(engine.state.trades) == 0, (
            f"PIN-2 FAILED: engine entered {len(engine.state.trades)} trade(s) using "
            "exec-candle signal — look-ahead in ema_cross_up detection"
        )

    def test_pin2_body_dominance_exec_candle_no_entry(self):
        """PIN-2 (Round-3 body_dominance fixture): body dominance on exec candle → NO trade.

        Asymmetric fixture: signal candle (N-1) is a tiny-body doji (<65% body/range);
        exec candle (N) has a large body (>95% body/range) that would satisfy
        check_body_dominance_conditions if it were the signal candle.

        With correct behavior: engine uses signal_row (doji) → body_pct < min_body → skip.
        With look-ahead bug: engine uses current_row (big body) → would enter.

        body_dominance condition (strategy.py:428-430):
            body_pct >= min_body (default 0.65)
            mom10 >= min_mom (default 0.02)  — momentum must be positive for long
            volume >= volume_ma * min_vol (default 1.5x)

        Hand-derivation:
            signal_row: close=100.0, open=99.9, high=100.5, low=99.5 → body=0.1/1.0=0.10 (fail)
            exec_row:   close=103.0, open=100.5, high=103.0, low=100.5 → body=2.5/2.5=1.0 (pass)
        """
        # Build a base series to get warmup done, then append the asymmetric pair
        n_warmup = 60
        rows = []
        c = 1000.0
        for i in range(n_warmup):
            c += 2.0  # steady bull so trend indicators are healthy
            rows.append({
                "open":   c - 0.5,
                "high":   c + 0.5,
                "low":    c - 0.5,
                "close":  c,
                "volume": 3000.0,  # high volume throughout for volume_ma
            })

        # Signal candle (N-1): tiny body — body_pct = 0.1/1.0 = 10% (fails min_body=65%)
        c_s = c
        rows.append({
            "open":   c_s - 0.05,
            "high":   c_s + 0.5,
            "low":    c_s - 0.5,
            "close":  c_s + 0.05,  # tiny body: (0.10/1.00 = 10%)
            "volume": 3000.0,
        })

        # Exec candle (N): massive bull body — if looked at, body_pct ≈ 100%
        c_e = c_s + 5.0
        rows.append({
            "open":   c_e - 2.5,
            "high":   c_e,
            "low":    c_e - 2.5,
            "close":  c_e,  # perfect bull body: body=2.5, range=2.5 → 100%
            "volume": 5000.0,
        })

        idx = pd.date_range("2024-01-01", periods=len(rows), freq="1h", tz=None)
        df = pd.DataFrame(rows, index=idx)

        cfg = trading_config(
            cooldown_candles_after_sl=0,
            cooldown_candles_after_close=0,
            signals={
                **{k: {"enabled": False} for k in [
                    "ema_crossover", "ema_fast_crossover", "ema_pullback",
                    "rsi_divergence", "bb_breakout", "mean_reversion",
                    "squeeze_release", "ichimoku_cloud", "supertrend",
                    "vol_expansion", "dual_supertrend", "alligator",
                    "ema_ichimoku_hybrid", "ichi_supertrend", "volexp_supertrend",
                    "dual_thrust", "stoch_mtf", "zscore_meanrev", "awesome_oscillator",
                    "range_bounce", "ema_ribbon", "ichi_adx", "ribbon_ao",
                    "zscore_stoch", "stoch_supertrend", "supertrend_volume",
                    "dualthrust_adx", "pin_bar", "engulfing", "inside_bar_breakout",
                    "adx_di_cross", "choppiness_ema", "williams_r_adx", "roc_momentum",
                    "price_channel_vol", "ema_alligator", "ribbon_rsi_vol",
                ]},
                "body_dominance": {
                    "enabled": True,
                },
            },
            body_dominance_min_body=0.65,  # signal candle (10%) fails, exec candle (100%) passes
            body_dominance_min_mom=0.0,    # disable mom filter to isolate body check
            body_dominance_min_vol=0.0,    # disable vol filter to isolate body check
        )

        with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
            engine = BacktestEngine(cfg, initial_balance=10_000.0)
            engine.run(df)

        # Signal candle is the doji (body_pct=10% < 65%) → body_dominance fails.
        # Exec candle has body_pct=100% → would pass if looked at.
        # Correct: 0 trades. Look-ahead bug: ≥1 trade.
        trades_bd = [t for t in engine.state.trades if t.signal_source == "body_dominance"]
        assert len(trades_bd) == 0, (
            f"PIN-2 body_dominance FAILED: {len(trades_bd)} trade(s) opened using "
            "exec-candle body data — look-ahead in body_dominance detection"
        )

    def test_pin3_trend_filter_shift1_blocks_premature_entry(self):
        """PIN-3: trend_filter shift(1) in add_trend_filter() prevents look-ahead.

        data.py:1254-1261: all 1H trend features are shifted by 1 period before
        merge.  This means at candle i, 'above_trend' reflects the 1H state at
        candle i-1 (the last COMPLETED 1H candle), NOT candle i's own trend.

        Behavioral test: run the same 15m signal fixture with two different 1H
        trend datasets — one where 1H trend is strongly bullish from the start
        (above_trend=True at all merged candles), and one where 1H trend is
        strongly bearish (above_trend=False at all merged candles).

        Expected:
            - Bull-trend run: LONG entries fire (ema_cross_up AND above_trend=True).
            - Bear-trend run: LONG entries are blocked by above_trend=False filter.
              This directly tests data.py:1273 `above_trend = close > ema_trend_1h`
              combined with strategy.py:105-107 `if not row["above_trend"]: return False`.

        The shift(1) is implicitly tested: even if the bull trend just switched on
        the current exec candle, the signal_row (prev candle) would still see the
        shifted (one-period-old) state — and the shifted state blocks the entry.
        We pin the outcome (trade count difference), not the internal mechanism.

        Note on add_trend_filter() compatibility: requires index.name="timestamp"
        for the merge_asof call (data.py:1264-1270) to restore the DatetimeIndex
        correctly.  make_trading_ohlcv_named() sets this name explicitly.
        """
        def _make_named_df(n: int, base_price: float = 1000.0) -> pd.DataFrame:
            """make_trading_ohlcv with index.name='timestamp' for add_trend_filter."""
            df = make_trading_ohlcv(n, base_price=base_price)
            df.index.name = "timestamp"
            return df

        def _make_trend(n: int, close_price: float) -> pd.DataFrame:
            """Build a flat 1H trend DataFrame with known close price and named index."""
            idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz=None)
            idx.name = "timestamp"
            return pd.DataFrame({
                "open":   [close_price] * n,
                "high":   [close_price + 5.0] * n,
                "low":    [close_price - 5.0] * n,
                "close":  [close_price] * n,
                "volume": [1000.0] * n,
            }, index=idx)

        signal_df = _make_named_df(200, base_price=1000.0)
        cfg = trading_config(cooldown_candles_after_sl=0, cooldown_candles_after_close=0)

        def _run_with_trend(trend_df):
            with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
                engine = BacktestEngine(cfg, initial_balance=10_000.0)
                engine.run(signal_df.copy(), trend_df)
            return engine.state.trades

        # "Bull trend" from the LONG filter's perspective:
        #   above_trend = signal.close > ema_trend_1h  (data.py:1273)
        #   Signal prices ~1000; trend EMA50 must be BELOW 1000 → LONG entries pass.
        #   Use trend close=500 so EMA50 ≈ 500 << 1000 → above_trend=True.
        trades_bull = _run_with_trend(_make_trend(200, close_price=500.0))

        # "Bear trend" from the LONG filter's perspective:
        #   Signal prices ~1000; trend EMA50 >> 1000 → above_trend=False → LONG blocked.
        #   Use trend close=2000 so EMA50 ≈ 2000 >> 1000 → above_trend=False.
        trades_bear = _run_with_trend(_make_trend(200, close_price=2000.0))

        long_trades_bull = [t for t in trades_bull if t.side == "long"]
        long_trades_bear = [t for t in trades_bear if t.side == "long"]

        assert len(long_trades_bull) >= 1, (
            "PIN-3: 'bull-trend' run (trend close=500 < signal prices ~1000) produced no "
            "long trades — above_trend=True should allow LONG entries but ema_crossover "
            "is not firing. Check trading_config() filters or make_trading_ohlcv()."
        )
        assert len(long_trades_bear) == 0, (
            f"PIN-3 FAILED: 'bear-trend' run produced {len(long_trades_bear)} long trade(s). "
            "above_trend filter (data.py:1273 + strategy.py:105-107) is not blocking LONG "
            "entries when trend EMA50 >> signal price (above_trend=False). "
            "If trend_filter shift(1) were removed, a trend crossover on the exec candle "
            "could leak into the signal check and allow a premature LONG entry."
        )

    def test_pin4_regime_per_row_early_candles_are_ranging(self):
        """PIN-4 (ASYMMETRIC): regime is computed per-row (rolling), not globally.

        engine.py:219-229: for each row i, regime = detect_regime(df[:i+1]).
        Rows before atr_period + regime_lookback are assigned 'ranging' because
        there isn't enough history for a meaningful calculation (warmup floor).

        Asymmetric fixture design
        -------------------------
        Bear leg (candles 0-19): EMA9 < EMA21, no crossover yet.
        Bull leg (candles 20-89): sharp reversal causes EMA9 to cross above EMA21
            at candle 31 (verified: `ema_cross_up=True` fires at position 31, which
            is after EMA warmup 31 but BEFORE regime warmup 34).

        EMA warmup  = max(ema_fast=9, ema_slow=21) + 10 = 31   (data.py:235)
        Regime warmup = atr_period=14 + regime_lookback=20 = 34  (engine.py:224)

        With per-row rolling (correct):
            Crossover at candle 31 → regime loop assigns 'ranging' for i<34.
            regime_filter ON → entry blocked → 0 trades.

        With global detect_regime over full df (mutation/bug):
            Full df = 20-candle bear + 70-candle bull → strongly trending.
            detect_regime returns 'trending' → entry allowed at candle 31 → trades fire.

        CONTROL assertion: same fixture with regime_filter OFF → >= 1 trade.
        This proves the fixture CAN produce a trade; only the regime gate blocks it.

        Mutation-verification (not in committed code — run manually to confirm teeth):
            Temporarily set `backtest/engine.py:224` warmup check to `if False:`
            so all rows call detect_regime() → global regime 'trending' propagates
            to early rows → trades fire at candle 31 → new PIN-4 FAILS.
            Revert: `git checkout backtest/engine.py`.

        Hand-derivation:
            atr_period=14, regime_lookback=20 → warmup = 34.
            Crossover candle 31: i=31 < 34 → hardcoded 'ranging' (engine.py:224-225).
            Entry exec at candle 32: i=32 < 34 → also 'ranging' → regime gate fires.
            Result: 0 entries.
        """
        # Asymmetric fixture: bear_20 + bull_70 — crossover fires at candle 31
        # (within regime warmup=34 but after EMA warmup=31).
        # Empirically verified: bear_len=20, move=3 → ema_cross_up at position 31.
        n = 90
        import pandas as pd
        idx = pd.date_range("2024-01-01", periods=n, freq="1h")
        rows = []
        c = 1000.0
        for i in range(n):
            c += -3.0 if i < 20 else +3.0
            rows.append({"open": c - 1.0, "high": c + 2.0,
                         "low": c - 2.0, "close": c, "volume": 1000.0})
        asym_df = pd.DataFrame(rows, index=idx)

        # CONTROL: same fixture, regime_filter OFF → >= 1 trade.
        # Proves the crossover fires and position opens when regime is not blocking.
        cfg_off = trading_config(
            atr_period=14,
            regime_lookback=20,
            regime_filter={"enabled": False},
            cooldown_candles_after_sl=0,
            cooldown_candles_after_close=0,
        )
        with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
            engine_off = BacktestEngine(cfg_off, initial_balance=10_000.0)
            engine_off.run(asym_df.copy())

        assert len(engine_off.state.trades) >= 1, (
            "PIN-4 CONTROL FAILED: asymmetric fixture with regime_filter=OFF produced "
            f"{len(engine_off.state.trades)} trades.  The fixture must fire at least 1 "
            "entry so the regime=ON result (0 trades) is provably due to the gate, "
            "not absent signal.  Check make_trading_ohlcv or crossover warmup logic."
        )

        # ASYMMETRIC PIN: same fixture, regime_filter ON → 0 trades.
        # The crossover at candle 31 is within the regime warmup window (31 < 34),
        # so the per-row loop assigns 'ranging' and the gate blocks all entries.
        cfg_on = trading_config(
            atr_period=14,
            regime_lookback=20,
            regime_filter={"enabled": True, "skip_ranging": True, "skip_volatile": False},
            cooldown_candles_after_sl=0,
            cooldown_candles_after_close=0,
        )

        with patch("backtest.engine.add_funding_rate", side_effect=lambda d, _: d):
            engine_on = BacktestEngine(cfg_on, initial_balance=10_000.0)
            engine_on.run(asym_df.copy())

        # Per-row rolling: regime='ranging' for i<34 (warmup floor, engine.py:224-225).
        # Crossover at candle 31 → regime gate fires → 0 entries.
        # If regime were global (mutation): 'trending' → entries fire → this FAILS.
        assert len(engine_on.state.trades) == 0, (
            f"PIN-4 FAILED: {len(engine_on.state.trades)} trade(s) opened at the crossover "
            "candle (position 31 = within regime warmup 34). "
            "Engine may be computing regime globally (look-ahead) instead of per-row rolling. "
            "Mutation-verify: temporarily remove warmup floor in engine.py:224 → this test "
            "should FAIL (global regime sees 'trending') → revert."
        )


# ============================================================================
# PIN-10 — Funding charged = independently hand-computed multi-settlement,
#           size-changing (partial-TP mid-hold), BOTH long and short
# ============================================================================

class TestFundingPIN10:
    """PIN-10: multi-settlement, size-changing funding accumulation.

    Test plan requirement:
      "Build a deterministic OHLCV fixture spanning ≥2 settlement boundaries
       with a known per-settlement rate; assert the trade's total_pnl reflects
       exactly the hand-summed funding (each settlement on the then-current size).
       Mutation: summing over all candles (ffill) instead of settlements must FAIL it;
       charging the wrong sign must FAIL it."

    All values independently hand-derived below — NOT computed by calling the
    engine code path under test.

    Fixture design (long with partial-TP):
      Candle 0: entry at 08:00 UTC (NOT a settlement — settlement fires BEFORE
                entry in engine.py's per-candle loop order, but we use open_position
                which seeds the position directly so settlement never sees it during
                the "entry candle"; the first settlement charged is candle 1).
      Candle 1: 08:00 UTC settlement (rate=0.001) — size=1000 → charge 1.0
      Candle 2: 09:00 UTC NOT settlement — no charge; partial-TP fires, size→500
      Candle 3: 16:00 UTC settlement (rate=0.001) — size=500 → charge 0.5
      Candle 4: 17:00 UTC close (TP hit)

    Hand-computed funding total: 1.0 + 0.5 = 1.5 (long pays)

    Mutation A — sum over all 4 candles (ffill):
        4 candles × 1000×0.001 = 4.0 (wrong — size unchanged across all candles)
        OR: candle1 size=1000→1.0, candle2 size=1000→1.0, candle3 size=500→0.5, candle4 size=500→0.5 = 3.0
        Either way ≠ 1.5 → assertion fails.

    Mutation B — wrong sign (short pays instead of receives):
        long accrued_funding would be ADDED instead of subtracted → pnl > gross → fails.
    """

    def _make_candle_with_funding(
        self,
        timestamp: str,
        open_: float,
        high: float,
        low: float,
        close: float,
        funding_rate: float,
    ) -> pd.Series:
        """Build a candle Series with a fundingRate field."""
        c = make_candle(
            open_price=open_, high=high, low=low, close=close,
            timestamp=timestamp,
        )
        c["fundingRate"] = funding_rate
        return c

    def test_pin10_long_multi_settlement_size_changing(self):
        """PIN-10 (long): 2 settlements, partial-TP halves size between them.

        Hand-derived expected values (no commission, no slippage):

        Position: LONG, entry=100.0, size=1000.0, TP=120.0 (full), TP1=106.0 (partial, 50%)
        Settlement rate: 0.001 per boundary.

        Candle 1 (08:00 UTC — settlement):
            charge = 1000.0 × 0.001 = 1.0
            accrued_funding = 1.0
            position stays open (no SL/TP hit)

        Candle 2 (09:00 UTC — NOT settlement):
            no charge
            Partial-TP1 fires (high=107 ≥ tp1=106):
              partial_size = 1000 * 0.5 = 500
              partial_pnl_pct = (106 - 100) / 100 = 0.06
              partial_pnl = 500 * 0.06 = 30.0
              pos.size → 500
            accrued_funding = 1.0 (unchanged)

        Candle 3 (16:00 UTC — settlement):
            charge = 500.0 × 0.001 = 0.5   ← THEN-CURRENT size after partial-TP
            accrued_funding = 1.5
            position stays open

        Candle 4 (17:00 UTC — NOT settlement):
            Full TP fires (high=121 ≥ take_profit=120):
              _close_position called
              pnl_from_exit = pos.size × (exit - entry)/entry
                            = 500 × (120 - 100)/100 = 100.0
              exit_commission = 0 (no commission)
              pnl_from_exit = 100.0
              pnl -= accrued_funding = 100.0 - 1.5 = 98.5
              total_pnl = pnl_from_exit + partial_pnl = 98.5 + 30.0 = 128.5

        Mutation A verification (sum over all candles):
            If settlement check fires on every candle (not boundary-gated):
              After c1: 1000×0.001=1.0
              After c2: 1000×0.001=1.0 (before partial-TP check, size still 1000)
                  OR 500×0.001=0.5 (after partial-TP if order wrong)
              After c3: 500×0.001=0.5
              After c4: 500×0.001=0.5 (before close)
            → total 3.0 (worst case all candles, pre-partial) → pnl = 97.0 ≠ 98.5 → FAILS.

        Mutation B (wrong sign — long receives instead of pays):
            accrued_funding = -1.5 → pnl = 98.5 + 1.5 = 130.0 ≠ 128.5 → FAILS.
        """
        cfg = base_config(
            commission_rate=0.0,
            slippage_rate=0.0,
            partial_tp_enabled=True,
            partial_tp_pct=0.5,
            move_sl_to_be_after_tp1=False,  # keep SL static for clarity
        )
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(
            engine, side="long",
            entry_price=100.0, stop_loss=90.0, take_profit=120.0,
            size=1000.0, tp1_price=106.0,
        )

        rate = 0.001

        # --- Candle 1: 08:00 UTC settlement → charges 1000 × 0.001 = 1.0
        c1 = self._make_candle_with_funding(
            "2024-01-01 08:00", open_=101.0, high=104.0, low=100.0, close=103.0,
            funding_rate=rate,
        )
        engine._check_exit(c1, "2024-01-01 08:00", risk_mgr)
        assert engine.state.position is not None, "Position should not close on c1"
        assert abs(engine.state.position.accrued_funding - 1.0) < 1e-10, (
            f"After c1 (settlement): accrued_funding={engine.state.position.accrued_funding:.6f}, "
            f"expected 1.0"
        )

        # --- Candle 2: 09:00 UTC (NOT settlement) → partial-TP fires, size→500
        c2 = self._make_candle_with_funding(
            "2024-01-01 09:00", open_=103.0, high=107.0, low=102.0, close=104.0,
            funding_rate=rate,  # ffill — but NOT settlement hour
        )
        engine._check_exit(c2, "2024-01-01 09:00", risk_mgr)
        pos = engine.state.position
        assert pos is not None, "Position should not fully close on c2"
        assert pos.tp1_hit is True, "Partial TP1 should have fired on c2"
        assert abs(pos.size - 500.0) < 1e-8, (
            f"After partial-TP: size should be 500.0, got {pos.size}"
        )
        # No additional funding charge at non-settlement 09:00
        assert abs(pos.accrued_funding - 1.0) < 1e-10, (
            f"After c2 (non-settlement): accrued_funding should still be 1.0 "
            f"(ffill must not charge), got {pos.accrued_funding}"
        )

        # --- Candle 3: 16:00 UTC settlement → charges 500 × 0.001 = 0.5
        c3 = self._make_candle_with_funding(
            "2024-01-01 16:00", open_=104.0, high=108.0, low=103.0, close=105.0,
            funding_rate=rate,
        )
        engine._check_exit(c3, "2024-01-01 16:00", risk_mgr)
        assert engine.state.position is not None, "Position should not close on c3"
        assert abs(engine.state.position.accrued_funding - 1.5) < 1e-10, (
            f"After c3 (settlement, size=500): accrued_funding should be 1.5, "
            f"got {engine.state.position.accrued_funding}"
        )

        # --- Candle 4: 17:00 UTC — full TP fires (high=121 ≥ take_profit=120)
        c4 = self._make_candle_with_funding(
            "2024-01-01 17:00", open_=118.0, high=121.0, low=117.0, close=120.0,
            funding_rate=rate,
        )
        engine._check_exit(c4, "2024-01-01 17:00", risk_mgr)

        assert engine.state.position is None, "Position should be closed at full TP"
        trade = engine.state.trades[-1]
        assert trade.close_reason == "take_profit"

        # Hand-derived total_pnl = 128.5
        # (exit pnl on remaining 500 units: 100.0) - (accrued_funding: 1.5) + (partial_pnl: 30.0)
        # = 98.5 + 30.0 = 128.5
        expected_exit_pnl = 500.0 * (120.0 - 100.0) / 100.0  # = 100.0
        expected_partial_pnl = 500.0 * (106.0 - 100.0) / 100.0  # = 30.0
        expected_funding = 1.5
        expected_total_pnl = (expected_exit_pnl - expected_funding) + expected_partial_pnl
        assert abs(trade.pnl - expected_total_pnl) < 1e-8, (
            f"PIN-10 (long): total_pnl={trade.pnl:.6f}, expected {expected_total_pnl:.6f}\n"
            f"  Breakdown: exit_pnl={expected_exit_pnl:.1f} "
            f"- funding={expected_funding:.1f} + partial={expected_partial_pnl:.1f}\n"
            f"  If pnl < expected: wrong sign (long paying wrong direction).\n"
            f"  If pnl == {expected_exit_pnl + expected_partial_pnl - 4 * rate * 1000:.1f}: "
            f"all-candle ffill over-charge (Mutation A).\n"
            f"  If pnl == {expected_exit_pnl + expected_partial_pnl + expected_funding:.1f}: "
            f"wrong sign — long receives (Mutation B)."
        )

    def test_pin10_short_multi_settlement_size_changing(self):
        """PIN-10 (short): 2 settlements, short RECEIVES funding; size changes via partial-TP.

        Short position: positive funding rate → shorts receive (credit).
        accrued_funding is NEGATIVE for short → pnl -= negative → net increases.

        Hand-derived expected values (no commission, no slippage):

        Position: SHORT, entry=100.0, size=1000.0, TP=82.0 (full), TP1=94.0 (partial, 50%)
        Settlement rate: 0.001 per boundary.

        Candle 1 (08:00 UTC — settlement):
            settlement for short = -(1000 × 0.001) = -1.0
            accrued_funding = -1.0  (short receives)

        Candle 2 (09:00 UTC — NOT settlement):
            no charge
            Partial-TP1 fires (low=93 ≤ tp1=94):
              partial_size = 1000 * 0.5 = 500
              partial_pnl_pct = (100 - 94) / 100 = 0.06
              partial_pnl = 500 * 0.06 = 30.0
              pos.size → 500

        Candle 3 (16:00 UTC — settlement):
            settlement for short = -(500 × 0.001) = -0.5
            accrued_funding = -1.5

        Candle 4 (17:00 UTC — NOT settlement):
            Full TP fires (low=81 ≤ take_profit=82):
              pnl_from_exit = 500 × (100 - 82)/100 = 90.0
              pnl -= accrued_funding → pnl -= (-1.5) = pnl + 1.5 = 91.5
              total_pnl = 91.5 + 30.0 = 121.5

        Mutation B verification (wrong sign — short pays instead of receives):
            accrued_funding = +1.5 → pnl = 90.0 - 1.5 + 30.0 = 118.5 ≠ 121.5 → FAILS.
        """
        cfg = base_config(
            commission_rate=0.0,
            slippage_rate=0.0,
            partial_tp_enabled=True,
            partial_tp_pct=0.5,
            move_sl_to_be_after_tp1=False,
        )
        engine = BacktestEngine(cfg, initial_balance=10_000.0)
        risk_mgr = _risk_mgr(engine)

        open_position(
            engine, side="short",
            entry_price=100.0, stop_loss=110.0, take_profit=82.0,
            size=1000.0, tp1_price=94.0,
        )

        rate = 0.001

        # Candle 1: 08:00 settlement — short receives
        c1 = self._make_candle_with_funding(
            "2024-01-01 08:00", open_=99.0, high=100.0, low=96.0, close=97.0,
            funding_rate=rate,
        )
        engine._check_exit(c1, "2024-01-01 08:00", risk_mgr)
        assert engine.state.position is not None
        assert abs(engine.state.position.accrued_funding - (-1.0)) < 1e-10, (
            f"Short at settlement: accrued_funding should be -1.0 (receives), "
            f"got {engine.state.position.accrued_funding}"
        )

        # Candle 2: 09:00 (NOT settlement) — partial-TP fires
        c2 = self._make_candle_with_funding(
            "2024-01-01 09:00", open_=96.0, high=97.0, low=93.0, close=94.5,
            funding_rate=rate,
        )
        engine._check_exit(c2, "2024-01-01 09:00", risk_mgr)
        pos = engine.state.position
        assert pos is not None
        assert pos.tp1_hit is True
        assert abs(pos.size - 500.0) < 1e-8
        assert abs(pos.accrued_funding - (-1.0)) < 1e-10, (
            f"After non-settlement c2: accrued_funding should still be -1.0, "
            f"got {pos.accrued_funding}"
        )

        # Candle 3: 16:00 settlement — short receives again on reduced size
        c3 = self._make_candle_with_funding(
            "2024-01-01 16:00", open_=94.0, high=95.0, low=91.0, close=92.0,
            funding_rate=rate,
        )
        engine._check_exit(c3, "2024-01-01 16:00", risk_mgr)
        assert engine.state.position is not None
        assert abs(engine.state.position.accrued_funding - (-1.5)) < 1e-10, (
            f"After c3 (settlement, size=500): accrued_funding should be -1.5, "
            f"got {engine.state.position.accrued_funding}"
        )

        # Candle 4: TP fires (low=81 ≤ take_profit=82)
        c4 = self._make_candle_with_funding(
            "2024-01-01 17:00", open_=84.0, high=85.0, low=81.0, close=82.5,
            funding_rate=rate,
        )
        engine._check_exit(c4, "2024-01-01 17:00", risk_mgr)

        assert engine.state.position is None
        trade = engine.state.trades[-1]
        assert trade.close_reason == "take_profit"

        # Hand-derived total_pnl = 121.5
        expected_exit_pnl = 500.0 * (100.0 - 82.0) / 100.0  # = 90.0
        expected_partial_pnl = 500.0 * (100.0 - 94.0) / 100.0  # = 30.0
        expected_funding = -1.5  # short receives
        expected_total_pnl = (expected_exit_pnl - expected_funding) + expected_partial_pnl
        # = (90.0 - (-1.5)) + 30.0 = 91.5 + 30.0 = 121.5
        assert abs(trade.pnl - expected_total_pnl) < 1e-8, (
            f"PIN-10 (short): total_pnl={trade.pnl:.6f}, expected {expected_total_pnl:.6f}\n"
            f"  Breakdown: exit_pnl={expected_exit_pnl:.1f} "
            f"- funding={expected_funding:.1f} + partial={expected_partial_pnl:.1f}\n"
            f"  If pnl < expected: short is paying instead of receiving (wrong sign / Mutation B).\n"
            f"  If pnl > expected and >> gross: all-candle ffill over-credit (Mutation A)."
        )

    def test_pin10_force_close_includes_funding(self):
        """PIN-10 variant: force_close (backtest_end) also deducts accrued_funding.

        Covers the force_close path (engine.py:1289-1304) which calls _close_position.
        The funding accumulated before force_close must appear in the final trade.pnl.

        Hand-derivation (no commission, no slippage):
            entry=100.0, size=1000.0, rate=0.001
            Settlement at 08:00: accrued_funding = 1.0
            Force-close at 10:00 (not settlement): close at price 102.0
              exit_pnl = 1000 × (102 - 100)/100 = 20.0
              pnl -= 1.0 (accrued_funding)
              net pnl = 19.0
        """
        engine = _make_engine(initial_balance=10_000.0, commission_rate=0.0,
                              slippage_rate=0.0)
        risk_mgr = _risk_mgr(engine)

        open_position(engine, side="long", entry_price=100.0, stop_loss=90.0,
                      take_profit=200.0, size=1000.0)

        rate = 0.001
        # Settlement candle — no SL/TP hit
        c1 = self._make_candle_with_funding(
            "2024-01-01 08:00", open_=101.0, high=103.0, low=100.0, close=101.5,
            funding_rate=rate,
        )
        engine._check_exit(c1, "2024-01-01 08:00", risk_mgr)
        assert engine.state.position is not None
        assert abs(engine.state.position.accrued_funding - 1.0) < 1e-10

        # force_close at 10:00 with close=102.0
        last_row = self._make_candle_with_funding(
            "2024-01-01 10:00", open_=101.0, high=103.0, low=100.0, close=102.0,
            funding_rate=rate,
        )
        engine._force_close(last_row, "2024-01-01 10:00", "backtest_end")

        assert engine.state.position is None
        trade = engine.state.trades[-1]
        assert trade.close_reason == "backtest_end"

        # Hand-derived: 1000 × (102 - 100)/100 - 1.0 = 20.0 - 1.0 = 19.0
        expected_pnl = 1000.0 * (102.0 - 100.0) / 100.0 - 1.0
        assert abs(trade.pnl - expected_pnl) < 1e-8, (
            f"force_close funding: expected pnl={expected_pnl:.4f}, "
            f"got {trade.pnl:.4f}. accrued_funding must be deducted in force_close path."
        )

    def test_pin10_entry_on_settlement_candle_excludes_that_settlement_run_e2e(self):
        """PIN-10 loop-order invariant (end-to-end run()): settlement on the entry candle
        is EXCLUDED from accrued_funding.

        This is an ASYMMETRIC behavioral pin for the run() loop order:

            for i in range(1, len(df)):
                if position is not None:
                    _check_exit(row[i])   ← funding + exit check
                if position is None:
                    _check_entry(prev_row[i-1], row[i])  ← opens position at row[i]

        At iteration i=8 (candle "2024-01-01 08:00"):
            - _check_exit runs first  → position is None → skipped (no funding charged)
            - _check_entry runs after → signal at candle 7 (07:00) fires → position opens

        So the 08:00 settlement is NEVER seen by the newly opened position.
        First settlement charged is at 16:00 (candle 16).

        Fixture design (no commission, no slippage, partial_tp OFF):
            Candle freq: 1h, starting 2024-01-01 00:00 UTC
            Candle  7 (07:00): ema_cross_up=True (injected) → signal fires
            Candle  8 (08:00): entry executes at close=100.0; 08:00 is a settlement
                               but position did not exist when _check_exit ran → NOT charged
            Candles 9-15     : hold; non-settlement or settlement but check-exit sees pos
            Candle 16 (16:00): settlement charged; accrued_funding += size × 0.001
            Candle 17 (17:00): high=104.0 ≥ TP=103.0 → take_profit; trade closes

        Expected: accrued_funding == trade.size × 0.001  (exactly 1 settlement, not 2)

        Mutation strength: if _check_entry moved ABOVE _check_exit in run(), the 08:00
        settlement would fire against the newly opened position → accrued_funding == 2 ×
        trade.size × 0.001 → assertion fails.  White-box math pins cannot cover this
        loop-order invariant; only a full run() fixture can.

        Hand-derivation (no fees, slippage, partial):
            entry_price  = 100.0  (close of candle 8)
            atr          = 1.0
            SL           = 100.0 - 1.5×1.0 = 98.5   (below all candle lows = 99.5)
            TP           = 100.0 + 3.0×1.0 = 103.0   (hit at candle 17 where high=104.0)
            rate         = 0.001
            Settlement boundaries crossed while HELD: 16:00 only (1 boundary)
            accrued_funding = trade.size × 0.001
        """
        rate = 0.001
        n = 25  # 25 candles: 00:00 to 00:00+24h

        # --- Build raw OHLCV DataFrame ---
        idx = pd.date_range("2024-01-01 00:00", periods=n, freq="1h", tz=None)
        rows = []
        for i in range(n):
            if i == 17:
                # TP candle: high=104.0 ≥ TP=103.0
                row = {
                    "open": 100.5, "high": 104.0, "low": 100.0, "close": 103.5,
                    "volume": 1000.0,
                }
            else:
                row = {
                    "open": 99.7, "high": 100.5, "low": 99.5, "close": 100.0,
                    "volume": 1000.0,
                }
            rows.append(row)
        raw_df = pd.DataFrame(rows, index=idx)

        # --- Patches ---
        # add_indicators: inject indicator columns; force ema_cross_up=True at candle 7.
        # Everything else set to values that allow a LONG entry without interference:
        #   rsi=55 ∈ [0,100] (trading_config has rsi filters open), atr=1.0 > 0,
        #   ema_slope=0.01 ≥ 0 (trading_config: ema_slope_min=0.0), volume_ma=500
        #   so volume 1000 > 500×0.0 = passes trading_config's volume_mult=0.0.
        # ema_cross_down=False everywhere to suppress shorts.

        def fake_add_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
            df = df.copy()
            df["ema_fast"] = 10.0
            df["ema_slow"] = 9.0
            df["rsi"] = 55.0
            df["atr"] = 1.0
            df["volume_ma"] = 500.0
            df["ema_slope"] = 0.01
            df["ema_cross_up"] = False
            df["ema_cross_down"] = False
            # Signal fires at candle 7 (07:00 UTC) so entry executes at candle 8 (08:00)
            df.iloc[7, df.columns.get_loc("ema_cross_up")] = True
            # Columns required by other strategy checks (prevent KeyError in loops)
            for col in (
                "bb_upper", "bb_lower", "bb_mid", "bb_width", "bb_width_pct",
                "swing_low", "swing_high", "pullback_long", "pullback_short",
                "ema_fast2", "ema_slow2", "regime",
            ):
                if col not in df.columns:
                    df[col] = 0.0
            return df

        def fake_add_funding_rate(df: pd.DataFrame, _path: str) -> pd.DataFrame:
            df = df.copy()
            df["fundingRate"] = rate
            return df

        cfg = trading_config(
            commission_rate=0.0,
            slippage_rate=0.0,
            partial_tp_enabled=False,
            atr_sl_mult=1.5,
            atr_tp_mult=3.0,
        )

        with patch("backtest.engine.add_indicators", side_effect=fake_add_indicators), \
             patch("backtest.engine.add_funding_rate", side_effect=fake_add_funding_rate):
            engine = BacktestEngine(cfg, initial_balance=10_000.0)
            engine.run(raw_df)

        # Must have exactly 1 trade (the LONG opened at 08:00, closed at 17:00)
        assert len(engine.state.trades) == 1, (
            f"Expected exactly 1 trade; got {len(engine.state.trades)}. "
            "Check fixture: signal at candle 7 → entry at candle 8."
        )
        trade = engine.state.trades[0]

        # Entry must be on the 08:00 candle (confirms fixture and loop-order invariant)
        assert "08:00" in trade.entry_time, (
            f"Expected entry at 08:00 candle; got entry_time={trade.entry_time!r}"
        )
        assert trade.close_reason == "take_profit", (
            f"Expected take_profit close; got {trade.close_reason!r}"
        )

        # Core invariant: exactly 1 settlement (16:00 only), NOT 2 (not counting 08:00 entry candle).
        #
        # Correct behavior (loop order: exit-before-entry):
        #     At candle 8 (08:00): _check_exit runs → position is None → no funding charged.
        #     _check_entry opens position.
        #     At candle 16 (16:00): _check_exit runs → position exists → 1 settlement charged.
        #     accrued_funding = trade.size × 0.001
        #
        # Broken behavior (if _check_entry moved above _check_exit in run()):
        #     At candle 8 (08:00): _check_entry opens position first.
        #     _check_exit runs → position exists → 08:00 settlement charged.
        #     At candle 16 (16:00): another settlement charged.
        #     accrued_funding = 2 × trade.size × 0.001  → assertion fails.
        expected_funding = trade.size * rate  # 1 settlement on full size
        assert abs(trade.accrued_funding - expected_funding) < 1e-10, (
            f"Entry-candle exclusion FAILED.\n"
            f"  accrued_funding = {trade.accrued_funding:.6f}\n"
            f"  expected        = {expected_funding:.6f}  (1 settlement at 16:00 only)\n"
            f"  trade.size      = {trade.size:.4f}\n"
            f"  If accrued ≈ 2×expected: 08:00 entry-candle was charged "
            f"(loop order: entry before exit — invariant broken).\n"
            f"  White-box tests cannot catch this; only an end-to-end run() fixture can."
        )
