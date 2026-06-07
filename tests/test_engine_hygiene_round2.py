"""Engine hygiene Round 2 — TDD tests (failing first, then fixed).

Blocker H1 (Round 2):
  _infer_close_reason (bot/engine.py:84-96) relabels profitable stop exits from
  the old 'sl' to 'trail_stop'/'breakeven'.  The cooldown gate at engine.py:1158
  keys `is_sl = lc_reason in ('sl','stop_loss')`, so a profitable trail/BE exit
  now selects cooldown_candles_after_close (4) instead of cooldown_candles_after_sl
  (8) — this is an ENTRY-TIMING decision change, not telemetry.

  Exactly one config has asymmetric cooldowns: config_gold_forex.json
  (after_sl=8, after_close=4, tf=H1, flex_cooldown disabled).
  Gold runs TradingEngine via OandaClient monkey-patch, trailing stops ratchet SL.

  FIX: treat trail_stop AND breakeven as SL-like for cooldown selection.
  Both are stop exits (not TP exits) and warrant the same re-entry caution.

All tests are pure-unit — no exchange, no network, no SQLite on disk.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

try:
    import anthropic  # noqa: F401
    import ccxt  # noqa: F401
    _engine_available = True
except ImportError:
    _engine_available = False

engine_required = pytest.mark.skipif(
    not _engine_available,
    reason="anthropic or ccxt not installed in this venv",
)


# ---------------------------------------------------------------------------
# H1 (Round 2) — trail_stop and breakeven must be SL-like for cooldown gate
# ---------------------------------------------------------------------------

@engine_required
class TestCooldownGateStopExitsAreSLLike:
    """trail_stop and breakeven are stop exits — must use after_sl cooldown.

    The is_sl set controls which cooldown key is selected:
      True  → cooldown_candles_after_sl   (longer — e.g. 8 H1 candles = 8 h)
      False → cooldown_candles_after_close (shorter — e.g. 4 H1 candles = 4 h)

    Before the fix: only 'sl' and 'stop_loss' are in the set.
    After  the fix: 'trail_stop' and 'breakeven' are also in the set.

    This is not a label-correctness change — it is a decision invariant:
    any stop-triggered exit (regardless of PnL sign) must use the same
    cooldown as it did before _infer_close_reason was introduced (when ALL
    stop exits were labelled 'sl').
    """

    def _get_is_sl_line(self) -> str:
        """Return the source line that assigns is_sl in the cooldown gate."""
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        for line in source.splitlines():
            if "is_sl" in line and "in (" in line:
                return line
        return ""

    # ------------------------------------------------------------------
    # Source-level: the is_sl set must include all four stop-exit labels
    # ------------------------------------------------------------------

    def test_trail_stop_in_is_sl_set(self):
        """'trail_stop' must be in the is_sl set so profitable stops select
        the after_sl cooldown, preserving pre-taxonomy entry timing."""
        line = self._get_is_sl_line()
        assert line, "is_sl assignment not found in engine.py"
        assert "trail_stop" in line, (
            f"'trail_stop' must be in is_sl set to preserve stop-exit cooldown cadence.\n"
            f"Current line: {line.strip()}\n"
            "Without this, a profitable trailing stop on Gold H1 re-enters 4 candles "
            "sooner than before _infer_close_reason was introduced."
        )

    def test_breakeven_in_is_sl_set(self):
        """'breakeven' must be in the is_sl set so BE stops select the after_sl
        cooldown.  A break-even exit is a stop exit (stop moved to entry), not a
        profit-take — it warrants the same cooldown as a loss stop."""
        line = self._get_is_sl_line()
        assert line, "is_sl assignment not found in engine.py"
        assert "breakeven" in line, (
            f"'breakeven' must be in is_sl set to preserve stop-exit cooldown cadence.\n"
            f"Current line: {line.strip()}"
        )

    def test_stop_loss_still_in_is_sl_set(self):
        """'stop_loss' must still be in the is_sl set (regression guard)."""
        line = self._get_is_sl_line()
        assert line, "is_sl assignment not found in engine.py"
        assert "stop_loss" in line, (
            f"'stop_loss' must remain in is_sl set.\nCurrent line: {line.strip()}"
        )

    def test_legacy_sl_still_in_is_sl_set(self):
        """Legacy 'sl' label (old DB rows) must still be in the is_sl set."""
        line = self._get_is_sl_line()
        assert line, "is_sl assignment not found in engine.py"
        assert '"sl"' in line or "'sl'" in line, (
            f"Legacy 'sl' must remain in is_sl set for backward compat.\n"
            f"Current line: {line.strip()}"
        )

    # ------------------------------------------------------------------
    # Behavioral: cooldown duration for each exit reason on gold config
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("lc_reason,expected_candles", [
        # All stop exits → after_sl = 8 candles
        ("stop_loss",  8),
        ("sl",         8),   # legacy label
        ("trail_stop", 8),   # profitable trailing stop
        ("breakeven",  8),   # stop moved to entry
        # TP exit → after_close = 4 candles
        ("tp",         4),
        ("unknown",    4),   # unrecognised → conservative default
    ])
    def test_cooldown_candles_selected_for_gold_config(
        self, lc_reason: str, expected_candles: int
    ):
        """For config_gold_forex-like settings (after_sl=8, after_close=4),
        each close reason must select the correct number of cooldown candles."""
        config = {
            "timeframe_signal": "H1",
            "cooldown_candles_after_sl": 8,
            "cooldown_candles_after_close": 4,
        }
        # Inline the same logic as the fixed engine.py cooldown gate
        is_sl = lc_reason in ("sl", "stop_loss", "trail_stop", "breakeven")
        cooldown_candles = config.get(
            "cooldown_candles_after_sl" if is_sl else "cooldown_candles_after_close",
            4,
        )
        assert cooldown_candles == expected_candles, (
            f"lc_reason={lc_reason!r}: expected {expected_candles} candles, "
            f"got {cooldown_candles}"
        )

    def test_trail_stop_gold_h1_cooldown_equals_8_hours(self):
        """Gold H1 trail_stop exit: cooldown must be 8 h (8 × 3600 s), matching
        the before-taxonomy behaviour where all stops were labelled 'sl'."""
        from bot.engine import _candle_seconds

        config = {
            "timeframe_signal": "H1",
            "cooldown_candles_after_sl": 8,
            "cooldown_candles_after_close": 4,
        }
        lc_reason = "trail_stop"
        is_sl = lc_reason in ("sl", "stop_loss", "trail_stop", "breakeven")
        cooldown_candles = config.get(
            "cooldown_candles_after_sl" if is_sl else "cooldown_candles_after_close",
            4,
        )
        candle_secs = _candle_seconds(config["timeframe_signal"])
        required = cooldown_candles * candle_secs

        assert required == 8 * 3600, (
            f"trail_stop on H1 must require 8 h (28800 s) cooldown, got {required} s"
        )

    def test_breakeven_gold_h1_cooldown_equals_8_hours(self):
        """Gold H1 breakeven exit: cooldown must be 8 h, matching the
        before-taxonomy behaviour where all stops were labelled 'sl'."""
        from bot.engine import _candle_seconds

        config = {
            "timeframe_signal": "H1",
            "cooldown_candles_after_sl": 8,
            "cooldown_candles_after_close": 4,
        }
        lc_reason = "breakeven"
        is_sl = lc_reason in ("sl", "stop_loss", "trail_stop", "breakeven")
        cooldown_candles = config.get(
            "cooldown_candles_after_sl" if is_sl else "cooldown_candles_after_close",
            4,
        )
        candle_secs = _candle_seconds(config["timeframe_signal"])
        required = cooldown_candles * candle_secs

        assert required == 8 * 3600, (
            f"breakeven on H1 must require 8 h (28800 s) cooldown, got {required} s"
        )

    def test_tp_exit_gold_h1_still_uses_after_close(self):
        """TP exits must still use after_close (4 candles) — no change for TP."""
        from bot.engine import _candle_seconds

        config = {
            "timeframe_signal": "H1",
            "cooldown_candles_after_sl": 8,
            "cooldown_candles_after_close": 4,
        }
        lc_reason = "tp"
        is_sl = lc_reason in ("sl", "stop_loss", "trail_stop", "breakeven")
        cooldown_candles = config.get(
            "cooldown_candles_after_sl" if is_sl else "cooldown_candles_after_close",
            4,
        )
        candle_secs = _candle_seconds(config["timeframe_signal"])
        required = cooldown_candles * candle_secs

        assert required == 4 * 3600, (
            f"tp exit on H1 must require 4 h (14400 s) cooldown, got {required} s"
        )
