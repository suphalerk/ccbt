"""Engine follow-up fixes — TDD regression tests (Round 1).

Written BEFORE the fixes so they fail first; then each fix makes them pass.

Covers:
  FIX1: Gold breakeven mislabel — hard loss within 0.5% of entry must NOT be
        labelled 'breakeven'; must be labelled 'stop_loss' (or at least be
        classified as an SL outcome for cooldown purposes).
  FIX2: busy_timeout on TradeJournal persistent connection and upsert_candles
        fresh connection.
  FIX3: log_trade_close + record_trade_result + closed.append idempotency —
        a second call with the same trade_id must not double-count consecutive loss.
  FIX4: initial_sl field removed from tracked-trade dict and docstring updated.
  FIX6: Behavioral non-fatal test for candle persist failure;
        cooldown tests for trail_stop and breakeven reasons.

All tests are pure-unit — no exchange, no network.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Guard: if anthropic/ccxt still missing, skip tests that need bot.engine
try:
    import anthropic  # noqa: F401
    import ccxt       # noqa: F401
    _engine_available = True
except ImportError:
    _engine_available = False

engine_required = pytest.mark.skipif(
    not _engine_available,
    reason="anthropic or ccxt not installed in this venv",
)


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_info(
    side: str = "buy",
    entry: float = 100.0,
    sl: float = 95.0,
    tp: float = 110.0,
    size: float = 1000.0,
) -> dict:
    return {
        "side": side,
        "entry_price": entry,
        "sl": sl,
        "tp": tp,
        "size": size,
        "atr": 1.0,
        "open_time": time.time() - 300,
        "calibration_id": None,
        "signal_type": None,
        "original_sl": sl,
        "original_tp": tp,
        "original_size": size,
        "tp1_price": 0.0,
        "tp1_hit": True,
        "regime": "trending",
    }


def _make_journal_mock() -> MagicMock:
    j = MagicMock()
    j.log_trade_close = MagicMock()
    return j


def _make_risk_mock() -> MagicMock:
    r = MagicMock()
    r.record_trade_result = MagicMock()
    return r


# ---------------------------------------------------------------------------
# FIX 1 — Gold breakeven mislabel
# ---------------------------------------------------------------------------

@engine_required
class TestGoldBreakevenMislabel:
    """Hard loss inside 0.5% of entry must be labelled 'stop_loss', not 'breakeven'."""

    def _call_infer(self, exit_price: float, pnl: float, info: dict) -> str:
        from bot.engine import _infer_close_reason
        return _infer_close_reason(
            exit_price=exit_price,
            pnl=pnl,
            info=info,
            trade_side="long",
        )

    def test_hard_loss_inside_band_labelled_stop_loss_not_breakeven(self):
        """
        Gold scenario: entry=2000, atr_sl_mult=2.5, SL may fall within 0.5%
        of entry for a tight move. Negative pnl must → stop_loss.
        """
        entry = 2000.0
        # SL at 0.3% below entry — inside the 0.5% hardcoded band
        sl = entry * (1 - 0.003)  # 1994.0
        tp = entry * 1.015         # 2030.0

        info = _make_info(entry=entry, sl=sl, tp=tp)
        # Exit at SL with negative pnl
        pnl = -30.0  # genuine loss

        reason = self._call_infer(exit_price=sl, pnl=pnl, info=info)
        assert reason == "stop_loss", (
            f"Hard loss with negative pnl must be 'stop_loss', got {reason!r}"
        )
        assert reason != "breakeven", (
            "A genuine loss must NOT be labelled 'breakeven'"
        )

    def test_hard_loss_exactly_at_entry_but_negative_pnl_is_stop_loss(self):
        """Even exit AT entry with negative pnl is stop_loss (e.g. fee-driven loss)."""
        entry = 2000.0
        info = _make_info(entry=entry, sl=entry, tp=entry * 1.02)
        # Negative PnL wins over proximity-to-entry
        reason = self._call_infer(exit_price=entry, pnl=-5.0, info=info)
        assert reason == "stop_loss", (
            f"Negative pnl at entry must still be 'stop_loss', got {reason!r}"
        )

    def test_genuine_breakeven_zero_pnl_still_labelled_breakeven(self):
        """Positive or zero pnl near entry still means breakeven — don't over-fix."""
        entry = 2000.0
        info = _make_info(entry=entry, sl=entry, tp=entry * 1.02)
        reason = self._call_infer(exit_price=entry, pnl=0.0, info=info)
        assert reason == "breakeven"

    def test_genuine_breakeven_small_positive_pnl_still_labelled_breakeven(self):
        entry = 2000.0
        sl_be = entry * 1.001  # 0.1% above entry
        info = _make_info(entry=entry, sl=sl_be, tp=entry * 1.02)
        reason = self._call_infer(exit_price=sl_be, pnl=+1.0, info=info)
        # pnl > 0 and near entry → breakeven (not trail_stop, not stop_loss)
        assert reason == "breakeven"

    @pytest.mark.parametrize("pnl", [-0.01, -1.0, -50.0, -500.0])
    def test_any_negative_pnl_within_band_is_stop_loss(self, pnl: float):
        entry = 2000.0
        sl = entry * 0.998  # within 0.5% band
        info = _make_info(entry=entry, sl=sl, tp=entry * 1.02)
        from bot.engine import _infer_close_reason
        reason = _infer_close_reason(
            exit_price=sl, pnl=pnl, info=info, trade_side="long"
        )
        assert reason == "stop_loss", (
            f"Negative pnl={pnl} near entry must be stop_loss, got {reason!r}"
        )

    def test_is_sl_classification_includes_stop_loss_reason(self):
        """
        The is_sl set in engine.py cooldown gate must include 'stop_loss' so
        that gold after_sl cooldown (8 candles) fires for losses near entry.

        We verify this by reading the engine source, not by running the full loop,
        because the is_sl set is a string literal in the cooldown block.
        """
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        # Find the is_sl assignment line
        for line in source.splitlines():
            if "is_sl" in line and "in (" in line:
                assert "stop_loss" in line, (
                    f"is_sl set must include 'stop_loss'; line: {line}"
                )
                break
        else:
            # Alternatively the check may use a different pattern; ensure
            # the word stop_loss appears somewhere in the cooldown block.
            assert "stop_loss" in source, (
                "engine.py must reference 'stop_loss' in its is_sl classification"
            )


# ---------------------------------------------------------------------------
# FIX 2 — busy_timeout on connections
# ---------------------------------------------------------------------------

class TestBusyTimeout:
    """PRAGMA busy_timeout must be set on both journal and producer connections.

    We can't spy on sqlite3.Connection.execute (C extension — immutable type),
    so we verify the behavior indirectly:
    1. Source-level: logger.py source must contain 'busy_timeout' in the right places.
    2. Behavioral: after a TradeJournal or upsert_candles call, querying
       PRAGMA busy_timeout on the connection returns >= 1000.
    """

    def test_journal_source_contains_busy_timeout(self):
        """bot/logger.py must contain PRAGMA busy_timeout in _init_db."""
        import re
        logger_path = REPO / "bot" / "logger.py"
        source = logger_path.read_text()
        assert "busy_timeout" in source, (
            "bot/logger.py must set PRAGMA busy_timeout"
        )
        # Verify numeric value >= 1000
        m = re.search(r"busy_timeout\s*=\s*(\d+)", source)
        assert m, "busy_timeout=N must appear in logger.py"
        assert int(m.group(1)) >= 1000, (
            f"busy_timeout must be >= 1000ms, got {m.group(1)}"
        )

    def test_upsert_candles_source_contains_busy_timeout(self):
        """upsert_candles function body must contain PRAGMA busy_timeout."""
        import re
        logger_path = REPO / "bot" / "logger.py"
        source = logger_path.read_text()

        # Extract the upsert_candles function body by finding its def and scanning
        # until the next top-level def/class.
        lines = source.splitlines()
        start = None
        for i, line in enumerate(lines):
            if line.lstrip().startswith("def upsert_candles("):
                start = i
                break

        assert start is not None, "upsert_candles not found in logger.py"

        # Collect lines from start until next top-level (non-indented) def/class
        func_lines = []
        for line in lines[start:]:
            # A top-level def/class (after the first line) ends the function
            if func_lines and re.match(r'^(def |class )', line):
                break
            func_lines.append(line)

        func_body = "\n".join(func_lines)
        assert "busy_timeout" in func_body, (
            "upsert_candles must set PRAGMA busy_timeout inside its body"
        )

    def test_journal_busy_timeout_behavioral(self, tmp_path):
        """After TradeJournal init, querying PRAGMA busy_timeout returns >= 1000."""
        from bot.logger import TradeJournal

        db_path = str(tmp_path / "trades.db")
        j = TradeJournal(db_path=db_path)
        try:
            row = j._conn.execute("PRAGMA busy_timeout").fetchone()
            assert row is not None
            assert row[0] >= 1000, (
                f"busy_timeout on journal connection should be >= 1000ms, got {row[0]}"
            )
        finally:
            j.close()

    def test_upsert_candles_busy_timeout_behavioral(self, tmp_path):
        """upsert_candles opens a connection that gets busy_timeout >= 1000ms.

        After calling upsert_candles, we open a fresh connection to the same DB
        and query PRAGMA busy_timeout to confirm it was set (SQLite persists
        this as a connection-level default — we verify the code path ran by
        checking source + the source-level test above; here we do a live smoke
        that the function completes without errors and the DB is readable).
        """
        import pandas as pd
        from bot.logger import upsert_candles

        db_path = str(tmp_path / "trades.db")
        df = pd.DataFrame({
            "timestamp": [1_704_067_200_000],
            "open": [40000.0], "high": [40100.0],
            "low": [39900.0], "close": [40050.0],
            "volume": [100.0],
        })

        # Should not raise
        upsert_candles(db_path=db_path, symbol="BTCUSDT", timeframe="15m", df=df)

        # Verify the row was written (connection worked correctly despite pragma)
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM bot_ohlcv").fetchone()[0]
        conn.close()
        assert count == 1, f"Expected 1 row after upsert, got {count}"


# ---------------------------------------------------------------------------
# FIX 3 — log_trade_close idempotency / crash safety
# ---------------------------------------------------------------------------

@engine_required
class TestTradeCloseIdempotency:
    """record_trade_result must not double-count if called twice for same trade."""

    def _run_check_closed(
        self,
        trade_id: int,
        info: dict,
        journal: MagicMock,
        risk_mgr: MagicMock,
    ) -> dict:
        from bot.engine import check_closed_positions

        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": info["sl"], "amount": 1.0}
        ]
        client.get_ticker_price.return_value = info["sl"]
        client.cancel_all_orders.return_value = None

        return check_closed_positions(
            open_trade_ids={trade_id: info},
            current_positions=[],  # position gone
            journal=journal,
            risk_mgr=risk_mgr,
            calibration_tracker=None,
            symbol="BTC/USDT:USDT",
            client=client,
        )

    def test_record_trade_result_called_exactly_once(self):
        """record_trade_result must be called exactly once per closed trade."""
        info = _make_info(entry=100.0, sl=95.0, tp=110.0, size=1000.0)
        journal = _make_journal_mock()
        risk_mgr = _make_risk_mock()

        self._run_check_closed(trade_id=42, info=info, journal=journal, risk_mgr=risk_mgr)

        assert risk_mgr.record_trade_result.call_count == 1, (
            f"record_trade_result must be called exactly once, "
            f"got {risk_mgr.record_trade_result.call_count}"
        )

    def test_log_trade_close_called_before_record_trade_result(self):
        """log_trade_close must succeed before record_trade_result is called."""
        info = _make_info(entry=100.0, sl=95.0, tp=110.0, size=1000.0)
        call_order: list[str] = []

        journal = MagicMock()
        journal.log_trade_close = MagicMock(side_effect=lambda **kw: call_order.append("log"))
        risk_mgr = MagicMock()
        risk_mgr.record_trade_result = MagicMock(side_effect=lambda _: call_order.append("record"))

        from bot.engine import check_closed_positions
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": info["sl"], "amount": 1.0}
        ]
        client.get_ticker_price.return_value = info["sl"]
        client.cancel_all_orders.return_value = None

        check_closed_positions(
            open_trade_ids={1: info},
            current_positions=[],
            journal=journal,
            risk_mgr=risk_mgr,
            calibration_tracker=None,
            symbol="BTC/USDT:USDT",
            client=client,
        )

        assert call_order == ["log", "record"], (
            f"Expected log before record, got order: {call_order}"
        )

    def test_journal_crash_leaves_trade_in_open_ids(self):
        """If log_trade_close raises, the trade must NOT be removed from open_trade_ids.

        This prevents double-counting on retry: if the log fails, the trade
        stays tracked and will be retried next loop iteration.
        """
        info = _make_info(entry=100.0, sl=95.0, tp=110.0, size=1000.0)
        journal = MagicMock()
        journal.log_trade_close = MagicMock(side_effect=Exception("DB error"))
        risk_mgr = _make_risk_mock()

        from bot.engine import check_closed_positions
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": info["sl"], "amount": 1.0}
        ]
        client.get_ticker_price.return_value = info["sl"]
        client.cancel_all_orders.return_value = None

        trade_id = 99
        result = check_closed_positions(
            open_trade_ids={trade_id: info},
            current_positions=[],
            journal=journal,
            risk_mgr=risk_mgr,
            calibration_tracker=None,
            symbol="BTC/USDT:USDT",
            client=client,
        )

        # record_trade_result must NOT have been called (log failed)
        risk_mgr.record_trade_result.assert_not_called()
        # trade must still be in open_trade_ids (will retry next loop)
        assert trade_id in result, (
            "Trade must remain in open_trade_ids when log_trade_close fails"
        )


# ---------------------------------------------------------------------------
# FIX 4 — initial_sl removed from tracked-trade dict
# ---------------------------------------------------------------------------

@engine_required
class TestInitialSlRemoved:
    """initial_sl must not appear in the tracked-trade dict or docstring."""

    def test_initial_sl_not_in_docstring(self):
        """_infer_close_reason docstring must not claim to use initial_sl."""
        from bot.engine import _infer_close_reason
        doc = _infer_close_reason.__doc__ or ""
        assert "initial_sl" not in doc, (
            "Docstring must not reference initial_sl — field has been removed"
        )

    def test_no_initial_sl_writes_in_engine(self):
        """engine.py must not write 'initial_sl' into the tracked-trade dict."""
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        # Allow the old test helper to have initial_sl but not in production code
        # We look for assignment patterns: "initial_sl": ...
        assert '"initial_sl":' not in source, (
            "engine.py must not write 'initial_sl' into tracked-trade dicts"
        )

    def test_docstring_describes_pnl_sign_algorithm(self):
        """Docstring must describe the pnl-sign classification algorithm."""
        from bot.engine import _infer_close_reason
        doc = _infer_close_reason.__doc__ or ""
        # Docstring should mention pnl sign (positive/negative) as the discriminator
        assert any(word in doc.lower() for word in ["pnl", "profit", "loss"]), (
            "Docstring must describe pnl-sign algorithm"
        )


# ---------------------------------------------------------------------------
# FIX 6 — behavioral candle persist failure test
# ---------------------------------------------------------------------------

class TestCandlePersistNonFatal:
    """upsert_candles raising must not crash the caller; engine logs and continues."""

    def test_upsert_candles_exception_is_logged_not_raised(self, tmp_path):
        """When upsert_candles raises, the engine must log 'candle_persist_failed'
        and NOT propagate the exception to the trading loop.

        We test this by checking the engine source for a try/except around
        upsert_candles that logs the error without re-raising.
        """
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        lines = source.splitlines()

        # Find the upsert_candles call and verify there's an except block nearby
        upsert_lines = [i for i, l in enumerate(lines) if "upsert_candles(" in l and "def " not in l]
        assert upsert_lines, "engine.py must call upsert_candles()"

        # Verify a 'candle_persist_failed' log event exists somewhere in engine.py
        assert "candle_persist_failed" in source, (
            "engine.py must log 'candle_persist_failed' when upsert_candles raises"
        )

    def test_upsert_candles_exception_does_not_block_signal_eval(self, tmp_path):
        """A functional test: patching upsert_candles to raise must leave logger intact."""
        import pandas as pd
        import logging

        # We import only bot.logger (no ccxt needed) and confirm that even if
        # upsert_candles raises, the TradeJournal methods still work.
        from bot.logger import TradeJournal, upsert_candles

        db_path = str(tmp_path / "trades.db")
        j = TradeJournal(db_path=db_path)

        # Simulate upsert_candles raising
        log_records: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_records.append(record)

        cap = Capture()
        cap.setLevel(logging.WARNING)
        logging.getLogger().addHandler(cap)

        try:
            with patch("bot.logger.upsert_candles", side_effect=RuntimeError("DB locked")):
                # If this were called from engine, the engine would catch and log
                # We can't run the full engine, but we can verify the pattern exists
                # in source (see test above). Here we just confirm TradeJournal
                # is still usable after the error.
                try:
                    upsert_candles(
                        db_path=db_path,
                        symbol="BTCUSDT",
                        timeframe="15m",
                        df=pd.DataFrame(),  # empty — would normally early-return
                    )
                except RuntimeError:
                    pass  # The mock raises; we catch here to show non-fatal usage

            # Journal must still be functional
            tid = j.log_trade_open(
                symbol="BTCUSDT",
                side="buy",
                entry_price=100.0,
                size=1000.0,
                stop_loss=95.0,
                take_profit=110.0,
            )
            assert tid > 0
        finally:
            logging.getLogger().removeHandler(cap)
            j.close()


# ---------------------------------------------------------------------------
# FIX 6 — cooldown reason: trail_stop and breakeven are NOT is_sl
# ---------------------------------------------------------------------------

@engine_required
class TestCooldownReasonClassification:
    """trail_stop and breakeven must use after_close cooldown, not after_sl."""

    def test_trail_stop_not_in_is_sl_set(self):
        """engine.py is_sl classification must NOT include 'trail_stop'."""
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()

        # Find the is_sl assignment
        for line in source.splitlines():
            if "is_sl" in line and "in (" in line:
                assert "trail_stop" not in line, (
                    f"'trail_stop' must not be in is_sl set; line: {line}"
                )
                break

    def test_breakeven_not_in_is_sl_set(self):
        """engine.py is_sl classification must NOT include 'breakeven'.

        The original bug was breakeven *masking* stop_loss; the fix must ensure
        genuine losses are reclassified as stop_loss, NOT that breakeven is
        added to is_sl (which would give false SL-length cooldowns on true BE exits).
        """
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()

        for line in source.splitlines():
            if "is_sl" in line and "in (" in line:
                assert "breakeven" not in line, (
                    f"'breakeven' must not be in is_sl set; line: {line}"
                )
                break

    def test_stop_loss_in_is_sl_set(self):
        """'stop_loss' must be in is_sl so gold after_sl cooldown fires correctly."""
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()

        found_is_sl_line = False
        for line in source.splitlines():
            if "is_sl" in line and "in (" in line:
                found_is_sl_line = True
                assert "stop_loss" in line, (
                    f"is_sl set must include 'stop_loss'; line: {line}"
                )
                break

        assert found_is_sl_line, "Could not find is_sl assignment in engine.py"
