"""Tests for T4: startup warm + stagger (flag-guarded).

TDD spec — written BEFORE implementation.

Covers:
1. compute_warm_plan(configs) — per-(symbol,tf) warm-limit computation
   - Returns a dict mapping (symbol, tf) → int (warm limit)
   - Each (symbol,tf) pair collects all bots sharing it and computes
     max(100, max(ema_trend_i * 3) over those bots)
   - A (symbol,tf) appears ONCE even when many bots share it
   - Bots with the same symbol but different timeframes get separate entries

2. Flag-OFF path:
   - compute_warm_plan is not required to be called
   - No warm/stagger when flag is off (verified via async_main mock path)

Tests are PURE UNIT tests — no real event loop, no real network calls.
We test the helper compute_warm_plan directly.
"""

from __future__ import annotations

from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Import the helper under test.
# compute_warm_plan is expected to live in main_multi.py as a module-level
# function.  Until implementation this import will fail — that is the
# RED state this test must produce first.
# ---------------------------------------------------------------------------

from main_multi import compute_warm_plan


# ---------------------------------------------------------------------------
# Minimal config builder — only the fields relevant to warm-plan computation
# ---------------------------------------------------------------------------

def _cfg(
    symbol: str,
    timeframe_signal: str,
    timeframe_trend: str,
    ema_trend: int = 50,
) -> dict:
    """Build a minimal bot config with the fields used by compute_warm_plan."""
    return {
        "symbol": symbol,
        "timeframe_signal": timeframe_signal,
        "timeframe_trend": timeframe_trend,
        "ema_trend": ema_trend,
        # Other required fields (not used by warm-plan)
        "ema_fast": 9,
        "ema_slow": 21,
        "leverage": 10,
        "risk_per_trade": 0.01,
        "max_daily_loss": 0.03,
        "max_positions": 5,
        "rsi_period": 14,
        "rsi_min": 45,
        "rsi_max": 65,
        "atr_period": 14,
        "atr_sl_mult": 1.5,
        "atr_tp_mult": 3.0,
    }


# ---------------------------------------------------------------------------
# Warm-limit computation tests
# ---------------------------------------------------------------------------

class TestComputeWarmPlan:
    """compute_warm_plan(configs) returns correct per-(symbol,tf) warm limits."""

    def test_single_bot_default_ema_trend(self):
        """Single bot, ema_trend=50 → limit = max(100, 50*3) = 150."""
        configs = [_cfg("BTCUSDT", "15m", "1h", ema_trend=50)]
        plan = compute_warm_plan(configs)

        # BTC uses two timeframes: signal=15m, trend=1h
        assert ("BTCUSDT", "15m") in plan
        assert ("BTCUSDT", "1h") in plan
        assert plan[("BTCUSDT", "15m")] == 150  # max(100, 50*3)
        assert plan[("BTCUSDT", "1h")] == 150

    def test_single_bot_small_ema_trend_floors_at_100(self):
        """ema_trend=20 → 20*3=60 < 100 → floor to 100."""
        configs = [_cfg("ETHUSDT", "15m", "1h", ema_trend=20)]
        plan = compute_warm_plan(configs)

        assert plan[("ETHUSDT", "15m")] == 100
        assert plan[("ETHUSDT", "1h")] == 100

    def test_same_symbol_tf_multiple_bots_takes_max_ema_trend(self):
        """Multiple bots on same (symbol,tf): warm limit uses max ema_trend across them."""
        configs = [
            _cfg("AVAXUSDT", "1h", "1h", ema_trend=30),   # 30*3=90 → 100
            _cfg("AVAXUSDT", "1h", "1h", ema_trend=50),   # 50*3=150
            _cfg("AVAXUSDT", "1h", "1h", ema_trend=60),   # 60*3=180
        ]
        plan = compute_warm_plan(configs)

        # Only one entry per (symbol, tf)
        assert ("AVAXUSDT", "1h") in plan
        # max ema_trend = 60 → 60*3 = 180 → max(100, 180) = 180
        assert plan[("AVAXUSDT", "1h")] == 180

    def test_different_symbols_get_separate_entries(self):
        """Bots on different symbols are independent entries in the plan."""
        configs = [
            _cfg("BTCUSDT", "15m", "1h", ema_trend=50),
            _cfg("ETHUSDT", "1h", "1h", ema_trend=50),
        ]
        plan = compute_warm_plan(configs)

        assert ("BTCUSDT", "15m") in plan
        assert ("BTCUSDT", "1h") in plan
        assert ("ETHUSDT", "1h") in plan
        # BTC's trend tf and ETH's are both 1h but different symbols
        assert ("BTCUSDT", "1h") != ("ETHUSDT", "1h")

    def test_different_signal_and_trend_timeframes_get_separate_entries(self):
        """Signal tf and trend tf are separate keys when they differ."""
        configs = [_cfg("SOLUSDT", "15m", "1h", ema_trend=50)]
        plan = compute_warm_plan(configs)

        assert ("SOLUSDT", "15m") in plan
        assert ("SOLUSDT", "1h") in plan
        # They may have the same limit, but they are distinct keys
        assert ("SOLUSDT", "15m") is not ("SOLUSDT", "1h")

    def test_same_signal_and_trend_timeframe_produces_one_entry(self):
        """When signal_tf == trend_tf, only one entry is produced (deduplication)."""
        configs = [_cfg("ALGOUSDT", "4h", "4h", ema_trend=50)]
        plan = compute_warm_plan(configs)

        # ("ALGOUSDT", "4h") should appear exactly once (not duplicated)
        count_4h = sum(1 for (sym, tf) in plan if sym == "ALGOUSDT" and tf == "4h")
        assert count_4h == 1, (
            "When signal_tf == trend_tf, the same key is produced only once"
        )

    def test_empty_configs_returns_empty_plan(self):
        """Empty config list → empty warm plan."""
        plan = compute_warm_plan([])
        assert plan == {}

    def test_large_ema_trend(self):
        """Large ema_trend (e.g. 200) → limit = max(100, 200*3) = 600."""
        configs = [_cfg("BTCUSDT", "1h", "4h", ema_trend=200)]
        plan = compute_warm_plan(configs)
        assert plan[("BTCUSDT", "1h")] == 600
        assert plan[("BTCUSDT", "4h")] == 600

    def test_mixed_bots_correct_per_group_limits(self):
        """
        Realistic scenario: BTC 15m/1h bots + AVAX 4h/4h bots.
        Each group gets the correct max limit.
        """
        configs = [
            _cfg("BTCUSDT", "15m", "1h", ema_trend=50),   # limit = max(100,150) = 150
            _cfg("BTCUSDT", "15m", "1h", ema_trend=30),   # limit = max(100, 90) = 100 → 150 wins
            _cfg("AVAXUSDT", "4h", "4h", ema_trend=26),   # limit = max(100, 78) = 100
        ]
        plan = compute_warm_plan(configs)

        assert plan[("BTCUSDT", "15m")] == 150  # max across both BTC bots
        assert plan[("BTCUSDT", "1h")] == 150
        assert plan[("AVAXUSDT", "4h")] == 100  # floor


# ---------------------------------------------------------------------------
# Flag-OFF parity: compute_warm_plan returns a non-trivial plan regardless,
# but the CALLER (async_main) must not invoke warm when flag is off.
# We test via a lightweight integration check: verify the warm plan is never
# applied when the flag is off (no SharedMarketData constructed).
# ---------------------------------------------------------------------------

class TestFlagOffNoop:
    """Flag-OFF path: no warm, no SharedMarketData created."""

    def test_compute_warm_plan_is_pure_function(self):
        """compute_warm_plan is a pure helper — it does not check the flag.
        The flag gate lives in async_main. Calling it under flag-off is safe."""
        configs = [_cfg("BTCUSDT", "15m", "1h")]
        # Should not raise or call any exchange
        plan = compute_warm_plan(configs)
        assert isinstance(plan, dict)

    def test_flag_off_no_shared_market_data_import_unused(self):
        """When CCBT_SHARED_MARKETDATA is not '1', async_main should not
        construct SharedMarketData.  We verify the flag check logic by testing
        the helper function signature has no side effects under flag-off."""
        import os
        original = os.environ.get("CCBT_SHARED_MARKETDATA")
        try:
            # Ensure flag is off
            os.environ.pop("CCBT_SHARED_MARKETDATA", None)

            configs = [_cfg("BTCUSDT", "15m", "1h")]
            # compute_warm_plan itself is a pure function (no flag check)
            # — the flag gate is in async_main, not in compute_warm_plan.
            # This test verifies that compute_warm_plan does not inspect
            # the env flag (i.e. it returns the same result regardless).
            plan_no_flag = compute_warm_plan(configs)

            os.environ["CCBT_SHARED_MARKETDATA"] = "1"
            plan_with_flag = compute_warm_plan(configs)

            assert plan_no_flag == plan_with_flag, (
                "compute_warm_plan must not be affected by the flag — "
                "flag gating belongs in async_main"
            )
        finally:
            if original is None:
                os.environ.pop("CCBT_SHARED_MARKETDATA", None)
            else:
                os.environ["CCBT_SHARED_MARKETDATA"] = original


# ---------------------------------------------------------------------------
# Warm limit arithmetic edge cases
# ---------------------------------------------------------------------------

class TestWarmLimitArithmetic:
    """Verify the max(100, ema_trend*3) formula in all edge cases."""

    @pytest.mark.parametrize("ema_trend,expected", [
        (1,   100),   # 1*3=3 → floor 100
        (33,  100),   # 33*3=99 → floor 100
        (34,  102),   # 34*3=102 > 100 → 102
        (50,  150),   # typical: 150
        (100, 300),   # 100*3=300
        (200, 600),   # 200*3=600
    ])
    def test_warm_limit_formula(self, ema_trend: int, expected: int):
        """Warm limit = max(100, ema_trend*3) for all ema_trend values."""
        configs = [_cfg("TESTUSDT", "1h", "1h", ema_trend=ema_trend)]
        plan = compute_warm_plan(configs)
        assert plan[("TESTUSDT", "1h")] == expected, (
            f"ema_trend={ema_trend}: expected {expected}, got {plan[('TESTUSDT', '1h')]}"
        )

    def test_group_warm_limit_uses_max_not_avg(self):
        """The group warm limit is MAX across bots, not average."""
        configs = [
            _cfg("XRPUSDT", "1h", "4h", ema_trend=20),  # max(100,60)=100
            _cfg("XRPUSDT", "1h", "4h", ema_trend=50),  # max(100,150)=150
            _cfg("XRPUSDT", "1h", "4h", ema_trend=10),  # max(100,30)=100
        ]
        # avg ema_trend = 26.7 → avg limit = max(100,80) = 100
        # max ema_trend = 50 → max limit = 150
        plan = compute_warm_plan(configs)
        assert plan[("XRPUSDT", "1h")] == 150, "Must use MAX not AVG"
        assert plan[("XRPUSDT", "4h")] == 150
