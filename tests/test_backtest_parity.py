"""PIN-11: Dispatch parity between backtest engine and live generate_signal().

Decision 2 (approved 2026-06-08): BT dispatches 38 signal keys, live 31.
The 7 backtest-only keys are CONFIRMED orphans (backtest-dead: enabling one in
a deployed config produces silent zero-trades in live).

This test derives BOTH key sets by reading the source — it does NOT use a
hard-coded list as the ground truth.  The allowlist of 7 orphans IS the hard
assertion; it may only SHRINK (members removed by wiring them live), never grow.

Sources examined:
  bot/strategy.py generate_signal():
    Site 1 — row_only_checks block  (strategy.py:2349-2368)
    Site 2 — new_signals block      (strategy.py:2522-2558)
    Site 3 — standalone rsi_divergence (strategy.py:2615)
    Site 4 — standalone squeeze_release (strategy.py:2656)
    Site 5 — standalone supertrend     (strategy.py:2417)
    Site 6 — standalone ichimoku_cloud (strategy.py:2466)

  backtest/engine.py _check_entry():
    All signal branches from line 392 through 738.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Derive key sets from source (no hard-coded lists as single truth)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent

STRATEGY_FILE = PROJECT_ROOT / "bot" / "strategy.py"
ENGINE_FILE   = PROJECT_ROOT / "backtest" / "engine.py"


def _extract_live_keys() -> frozenset[str]:
    """Derive the complete live signal key set from ALL dispatch sites in
    bot/strategy.py generate_signal().

    Method: grep for all `signals_config.get("KEY"` patterns within the
    generate_signal() function body.  This is more robust than AST parsing
    because the function has multiple local scopes.

    We also include the standalone checks for rsi_divergence, squeeze_release,
    supertrend, and ichimoku_cloud that appear AFTER the new_signals loop.
    """
    source = STRATEGY_FILE.read_text()

    # Find generate_signal function body (it's the last function in the file
    # and runs from its def to end-of-file effectively, but we only need to
    # extract signals_config.get("...") occurrences within it).
    # Strategy: extract the function, then find all signals_config.get("KEY") calls.
    fn_match = re.search(r"def generate_signal\b", source)
    assert fn_match, "generate_signal function not found in strategy.py"

    fn_body = source[fn_match.start():]  # from function start to end of file

    # Pattern: signals_config.get("signal_key") or signals_config.get("signal_key", ...)
    pattern = re.compile(r'signals_config\.get\(\s*["\']([a-zA-Z_]+)["\']')
    keys = frozenset(pattern.findall(fn_body))

    assert len(keys) > 0, "No signal keys found in generate_signal — regex broken"
    return keys


def _extract_backtest_keys() -> frozenset[str]:
    """Derive the complete backtest signal key set from backtest/engine.py
    _check_entry() method.

    Same grep approach: signals_config.get("KEY") within _check_entry.
    """
    source = ENGINE_FILE.read_text()

    fn_match = re.search(r"def _check_entry\b", source)
    assert fn_match, "_check_entry method not found in engine.py"

    fn_body = source[fn_match.start():]  # from method start to end of file

    pattern = re.compile(r'signals_config\.get\(\s*["\']([a-zA-Z_]+)["\']')
    keys = frozenset(pattern.findall(fn_body))

    assert len(keys) > 0, "No signal keys found in _check_entry — regex broken"
    return keys


# ---------------------------------------------------------------------------
# The 7 orphan allowlist (BT-only, not wired to live)
# Approved 2026-06-08; may only SHRINK as live wiring is added.
# ---------------------------------------------------------------------------

BT_ONLY_ORPHAN_ALLOWLIST: frozenset[str] = frozenset({
    "adx_di_cross",
    "choppiness_ema",
    "williams_r_adx",
    "roc_momentum",
    "price_channel_vol",
    "ema_alligator",
    "ribbon_rsi_vol",
})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDispatchParity:
    """PIN-11: backtest signal keys minus live signal keys == exactly the 7 orphans."""

    def test_live_keys_found(self):
        """Smoke test: live key extraction finds at least 25 keys."""
        keys = _extract_live_keys()
        assert len(keys) >= 25, f"Live key extraction too short: {len(keys)} keys found"

    def test_backtest_keys_found(self):
        """Smoke test: backtest key extraction finds at least 30 keys."""
        keys = _extract_backtest_keys()
        assert len(keys) >= 30, f"Backtest key extraction too short: {len(keys)} keys found"

    def test_live_minus_backtest_is_empty(self):
        """Every live signal key is also in the backtest engine.

        live_keys - backtest_keys must be EMPTY.
        If non-empty, a live signal was added without a backtest counterpart
        → backtest silently underestimates the strategy's performance.
        """
        live = _extract_live_keys()
        bt   = _extract_backtest_keys()
        missing = live - bt
        assert missing == frozenset(), (
            f"Live signals NOT in backtest: {sorted(missing)}\n"
            "Add dispatch for these in backtest/engine.py _check_entry()."
        )

    def test_backtest_minus_live_equals_orphan_allowlist(self):
        """BT-only keys must equal EXACTLY the 7 orphan allowlist.

        backtest_keys - live_keys == BT_ONLY_ORPHAN_ALLOWLIST

        If this fails:
          - New orphan added: add it to BT_ONLY_ORPHAN_ALLOWLIST with justification,
            OR wire it to live dispatch.
          - Orphan removed from allowlist: a key was wired to live (good!).
            Remove it from BT_ONLY_ORPHAN_ALLOWLIST.
        """
        live = _extract_live_keys()
        bt   = _extract_backtest_keys()
        bt_only = bt - live

        assert bt_only == BT_ONLY_ORPHAN_ALLOWLIST, (
            f"BT-only keys mismatch.\n"
            f"  Expected (allowlist):  {sorted(BT_ONLY_ORPHAN_ALLOWLIST)}\n"
            f"  Actual BT-only:        {sorted(bt_only)}\n"
            f"  New orphans:           {sorted(bt_only - BT_ONLY_ORPHAN_ALLOWLIST)}\n"
            f"  Wired to live:         {sorted(BT_ONLY_ORPHAN_ALLOWLIST - bt_only)}"
        )

    def test_backtest_key_count(self):
        """Backtest has exactly 38 signal keys (as of 2026-06-08 review).

        If this count changes, the parity review must be re-run.
        """
        bt = _extract_backtest_keys()
        assert len(bt) == 38, (
            f"Expected 38 backtest keys, found {len(bt)}: {sorted(bt)}\n"
            "If a new signal was added, update this count AND verify parity."
        )

    def test_live_key_count(self):
        """Live dispatch has exactly 31 signal keys (as of 2026-06-08 review).

        38 BT keys - 7 orphans = 31 live keys.
        """
        live = _extract_live_keys()
        assert len(live) == 31, (
            f"Expected 31 live keys, found {len(live)}: {sorted(live)}\n"
            "If a new signal was added, update this count AND verify parity."
        )

    def test_orphan_allowlist_keys_in_backtest(self):
        """Each allowlisted orphan actually exists in the backtest engine.

        Guards against typos in the allowlist.
        """
        bt = _extract_backtest_keys()
        for key in BT_ONLY_ORPHAN_ALLOWLIST:
            assert key in bt, (
                f"Orphan '{key}' in allowlist but NOT found in backtest engine — "
                "possible typo in BT_ONLY_ORPHAN_ALLOWLIST."
            )

    def test_orphan_allowlist_keys_not_in_live(self):
        """Each allowlisted orphan is genuinely absent from live dispatch.

        Guards against stale allowlist (orphan was wired live but not removed).
        """
        live = _extract_live_keys()
        for key in BT_ONLY_ORPHAN_ALLOWLIST:
            assert key not in live, (
                f"Orphan '{key}' in allowlist but FOUND in live dispatch — "
                "remove it from BT_ONLY_ORPHAN_ALLOWLIST (it's been wired live)."
            )
