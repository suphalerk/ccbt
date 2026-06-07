"""Engine hygiene Round 1 — TDD tests (fail first, then fixed).

6 decision-invariant hygiene fixes:
  H1: _infer_close_reason — pnl<0 structurally first (before TP proximity)
  H2: double-count guard structural — closed.append + del BEFORE telemetry
  H3: TradeJournal.upsert_candles method reuses persistent conn; once-per-closed-candle gate
  H4: test_H1_cooldown drives real engine helper _candle_seconds (not inline parser)
  H5: _candle_seconds helper extracted; logger.warning on unknown-tf fallback
  H6: _infer_close_reason — drop unused trade_side param (or fix docstring)

All tests are pure-unit — no exchange, no network.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

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
# Helpers
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


# ---------------------------------------------------------------------------
# H1 — pnl<0 is STRUCTURAL top-priority (before TP proximity check)
# ---------------------------------------------------------------------------

@engine_required
class TestH1PnlSignStructuralPriority:
    """pnl < 0 must be the FIRST check in _infer_close_reason (before TP proximity)."""

    def test_negative_pnl_closer_to_tp_returns_stop_loss(self):
        """Exit price closer to TP than SL, but pnl < 0 → must still be stop_loss.

        This is the structural test: if pnl < 0 is evaluated AFTER the TP
        proximity check, a fee-driven TP miss would be mislabelled 'tp'.
        """
        from bot.engine import _infer_close_reason

        # Construct a scenario: entry=100, sl=97, tp=106, exit=105
        # dist_to_tp = |105-106| = 1, dist_to_sl = |105-97| = 8 → tp wins proximity
        # BUT pnl is negative (e.g. fee-driven loss on TP miss)
        info = _make_info(entry=100.0, sl=97.0, tp=106.0)
        reason = _infer_close_reason(
            exit_price=105.0,
            pnl=-0.5,   # small negative — fee-driven loss on near-TP exit
            info=info,
            trade_side="long",
        )
        assert reason == "stop_loss", (
            f"Negative pnl must trump TP proximity → 'stop_loss', got {reason!r}. "
            "Fix: hoist `if pnl < 0: return 'stop_loss'` above the dist_to_tp check."
        )

    def test_positive_pnl_closer_to_tp_returns_tp(self):
        """Positive pnl and exit closer to TP → should still label 'tp'."""
        from bot.engine import _infer_close_reason

        info = _make_info(entry=100.0, sl=97.0, tp=106.0)
        reason = _infer_close_reason(
            exit_price=105.9,
            pnl=+50.0,
            info=info,
            trade_side="long",
        )
        assert reason == "tp", (
            f"Positive pnl closer to TP must be 'tp', got {reason!r}"
        )

    def test_source_pnl_check_before_tp_proximity(self):
        """Source-level: pnl < 0 return statement must appear BEFORE dist_to_tp check."""
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        lines = source.splitlines()

        # Find _infer_close_reason body
        fn_start = None
        for i, line in enumerate(lines):
            if "def _infer_close_reason" in line:
                fn_start = i
                break
        assert fn_start is not None, "_infer_close_reason not found"

        # Collect function body until next top-level def/class
        fn_lines = []
        for line in lines[fn_start:]:
            if fn_lines and (line.startswith("def ") or line.startswith("class ")):
                break
            fn_lines.append(line)

        # Find positions of pnl<0 check and dist_to_tp check
        pnl_neg_pos = None
        dist_tp_pos = None
        for i, line in enumerate(fn_lines):
            if "pnl < 0" in line and pnl_neg_pos is None:
                pnl_neg_pos = i
            if "dist_to_tp" in line and "<" in line and dist_tp_pos is None:
                dist_tp_pos = i

        assert pnl_neg_pos is not None, "pnl < 0 check not found in _infer_close_reason"
        assert dist_tp_pos is not None, "dist_to_tp comparison not found"
        assert pnl_neg_pos < dist_tp_pos, (
            f"pnl < 0 check (line {pnl_neg_pos}) must appear BEFORE "
            f"dist_to_tp comparison (line {dist_tp_pos}) in _infer_close_reason. "
            "The pnl-sign gate is the structural top-priority discriminator."
        )


# ---------------------------------------------------------------------------
# H2 — double-count guard: closed.append BEFORE telemetry
# ---------------------------------------------------------------------------

@engine_required
class TestH2DoubleCountGuardStructural:
    """closed.append + del from open_trade_ids must happen BEFORE telemetry calls.

    The fix: immediately after record_trade_result succeeds, append to `closed`
    and remove from open_trade_ids — THEN run calibration / logger.info / send_alert.
    A future raising line in telemetry cannot re-drive record_trade_result.
    """

    def _run_check(self, trade_id: int, info: dict, journal: MagicMock,
                   risk_mgr: MagicMock, calibration_tracker=None) -> dict:
        from bot.engine import check_closed_positions
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": info["sl"], "amount": 1.0}
        ]
        client.get_ticker_price.return_value = info["sl"]
        client.cancel_all_orders.return_value = None

        return check_closed_positions(
            open_trade_ids={trade_id: info},
            current_positions=[],
            journal=journal,
            risk_mgr=risk_mgr,
            calibration_tracker=calibration_tracker,
            symbol="BTC/USDT:USDT",
            client=client,
        )

    def test_source_record_result_then_closed_append_then_telemetry(self):
        """Source-level: the exact ordering inside the for-loop body must be:
          1. record_trade_result  (risk accounting)
          2. closed.append(trade_id)  AND/OR del open_trade_ids[trade_id] inline
          3. calibration / logger.info('position_closed_detected') / send_alert  (telemetry)

        We verify this by finding the ACTUAL closed.append(trade_id) statement
        (not a comment) and confirming it appears BEFORE position_closed_detected
        and send_alert within the function body.
        """
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        lines = source.splitlines()

        # Find check_closed_positions body
        fn_start = None
        for i, line in enumerate(lines):
            if "def check_closed_positions" in line:
                fn_start = i
                break
        assert fn_start is not None, "check_closed_positions not found"

        fn_lines = []
        for line in lines[fn_start:]:
            if fn_lines and (line.startswith("def ") or line.startswith("class ")):
                break
            fn_lines.append(line)

        # Find the ACTUAL closed.append statement (not in a comment)
        closed_append_pos = None
        logger_info_pos = None
        send_alert_pos = None
        for i, line in enumerate(fn_lines):
            stripped = line.strip()
            # Must be a real statement, not a comment
            if "closed.append(" in stripped and not stripped.startswith("#") and closed_append_pos is None:
                closed_append_pos = i
            if "position_closed_detected" in line and logger_info_pos is None:
                logger_info_pos = i
            if "send_alert(" in stripped and not stripped.startswith("#") and "from bot" not in line and send_alert_pos is None:
                send_alert_pos = i

        assert closed_append_pos is not None, (
            "closed.append(trade_id) statement not found in check_closed_positions"
        )

        if logger_info_pos is not None:
            assert closed_append_pos < logger_info_pos, (
                f"closed.append (fn-line {closed_append_pos}) must appear BEFORE "
                f"logger.info('position_closed_detected') (fn-line {logger_info_pos}). "
                "Move closed.append immediately after record_trade_result so "
                "a future logging crash cannot re-drive record_trade_result."
            )
        if send_alert_pos is not None:
            assert closed_append_pos < send_alert_pos, (
                f"closed.append (fn-line {closed_append_pos}) must appear BEFORE "
                f"send_alert (fn-line {send_alert_pos})."
            )

    def test_raising_send_alert_no_double_count_on_next_loop(self):
        """Behavioral: if send_alert raises, the trade must be removed from
        open_trade_ids so that a SECOND call to check_closed_positions (simulating
        the next loop iteration) does NOT call record_trade_result again.

        Current (broken) flow: record_trade_result → telemetry → closed.append
          → send_alert raises before closed.append → trade stays in open_trade_ids
          → next loop: record_trade_result called again (double-count).

        Fixed flow: record_trade_result → closed.append → telemetry
          → send_alert raises AFTER closed.append → trade already removed
          → next loop: no double-count.
        """
        from bot.engine import check_closed_positions

        info = _make_info(entry=100.0, sl=95.0, tp=110.0)
        journal = MagicMock()
        journal.log_trade_close = MagicMock()
        risk_mgr = MagicMock()

        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": info["sl"], "amount": 1.0}
        ]
        client.get_ticker_price.return_value = info["sl"]
        client.cancel_all_orders.return_value = None

        trade_id = 88
        # Simulate loop 1: send_alert raises
        open_ids = {trade_id: info}
        with patch("bot.engine.send_alert", side_effect=RuntimeError("Telegram down")):
            try:
                open_ids = check_closed_positions(
                    open_trade_ids=open_ids,
                    current_positions=[],
                    journal=journal,
                    risk_mgr=risk_mgr,
                    calibration_tracker=None,
                    symbol="BTC/USDT:USDT",
                    client=client,
                )
            except RuntimeError:
                pass  # exception propagated — open_ids was mutated in-place or not

        # Simulate loop 2: no send_alert patch (now restored) — run with the
        # leftover open_ids. If closed.append ran before send_alert, open_ids
        # is empty and record_trade_result is NOT called again.
        with patch("bot.engine.send_alert"):  # succeeds this time
            open_ids_after_loop2 = check_closed_positions(
                open_trade_ids=dict(open_ids),  # copy for safety
                current_positions=[],
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=None,
                symbol="BTC/USDT:USDT",
                client=client,
            )

        # After the fix: record_trade_result must be called exactly once total
        # (loop 1 only, because the trade was removed before send_alert raised).
        # Before the fix: it would be called twice (loop 1 + loop 2).
        assert risk_mgr.record_trade_result.call_count == 1, (
            f"record_trade_result must be called exactly once across both loops. "
            f"Got {risk_mgr.record_trade_result.call_count} calls. "
            "This means the trade was NOT removed from open_trade_ids when "
            "send_alert raised — closed.append must move BEFORE send_alert."
        )


# ---------------------------------------------------------------------------
# H3 — TradeJournal.upsert_candles reuses persistent conn; per-closed-candle gate
# ---------------------------------------------------------------------------

class TestH3JournalUpsertCandlesMethod:
    """TradeJournal must have an upsert_candles method that reuses self._conn
    and deduplicates writes per closed candle boundary.
    """

    def test_trade_journal_has_upsert_candles_method(self, tmp_path):
        """TradeJournal must expose upsert_candles as an instance method."""
        from bot.logger import TradeJournal
        db_path = str(tmp_path / "trades.db")
        j = TradeJournal(db_path=db_path)
        try:
            assert hasattr(j, "upsert_candles"), (
                "TradeJournal must have a upsert_candles() instance method "
                "so engine can call self._journal.upsert_candles(...) "
                "rather than calling the module-level upsert_candles(db_path=...)."
            )
        finally:
            j.close()

    def test_journal_upsert_candles_reuses_persistent_connection(self, tmp_path):
        """upsert_candles on the journal must NOT open a new sqlite3.connect().

        We verify this by patching sqlite3.connect and confirming it is not
        called after TradeJournal.__init__ returns.
        """
        import pandas as pd
        from bot.logger import TradeJournal

        db_path = str(tmp_path / "trades.db")
        j = TradeJournal(db_path=db_path)
        try:
            df = pd.DataFrame({
                "timestamp": [1_704_067_200_000],
                "open": [40000.0], "high": [40100.0],
                "low": [39900.0], "close": [40050.0],
                "volume": [100.0],
            })
            # Count sqlite3.connect calls AFTER journal init
            with patch("sqlite3.connect") as mock_connect:
                j.upsert_candles(
                    symbol="BTCUSDT",
                    timeframe="15m",
                    df=df,
                )
                assert mock_connect.call_count == 0, (
                    f"TradeJournal.upsert_candles must reuse self._conn "
                    f"(NOT call sqlite3.connect). Got {mock_connect.call_count} new connections."
                )
        finally:
            j.close()

    def test_journal_upsert_candles_deduplicates_same_candle(self, tmp_path):
        """Calling upsert_candles twice with the same candle boundary must write once.

        The dedup gate: floor(now, tf) ensures only the closed candle is written
        per loop tick. Calling it twice with the same ts should not raise and
        the row count must remain 1 (INSERT OR REPLACE is idempotent).
        """
        import pandas as pd
        from bot.logger import TradeJournal

        db_path = str(tmp_path / "trades.db")
        j = TradeJournal(db_path=db_path)
        try:
            df = pd.DataFrame({
                "timestamp": [1_704_067_200_000],
                "open": [40000.0], "high": [40100.0],
                "low": [39900.0], "close": [40050.0],
                "volume": [100.0],
            })
            j.upsert_candles(symbol="BTCUSDT", timeframe="15m", df=df)
            j.upsert_candles(symbol="BTCUSDT", timeframe="15m", df=df)

            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM bot_ohlcv").fetchone()[0]
            conn.close()
            assert count == 1, (
                f"Same-candle double write must remain idempotent (1 row), got {count}"
            )
        finally:
            j.close()

    def test_engine_calls_journal_method_not_module_function(self):
        """engine.py must call self._journal.upsert_candles() not the module-level
        upsert_candles(db_path=...) function at the candle-persist call site.
        """
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()

        # Find the candle persist call site (around _evaluate_and_execute)
        lines = source.splitlines()
        upsert_lines = [
            (i, l) for i, l in enumerate(lines)
            if "upsert_candles(" in l and "def " not in l
        ]
        assert upsert_lines, "No upsert_candles() call found in engine.py"

        # At least one call must be via self._journal (the new method call)
        journal_calls = [
            (i, l) for i, l in upsert_lines
            if "self._journal.upsert_candles" in l or "_journal.upsert_candles" in l
        ]
        assert journal_calls, (
            "engine.py must call self._journal.upsert_candles(...) (instance method), "
            "not the module-level upsert_candles(db_path=...). "
            f"Found calls: {[l for _, l in upsert_lines]}"
        )


# ---------------------------------------------------------------------------
# H4 — test_H1_cooldown drives real engine helper _candle_seconds
# ---------------------------------------------------------------------------

@engine_required
class TestH4CooldownDrivesRealHelper:
    """The H1 cooldown test must exercise the real _candle_seconds engine helper."""

    def test_candle_seconds_helper_exists_in_engine(self):
        """engine.py must expose a _candle_seconds(tf) helper function."""
        from bot import engine as eng
        assert hasattr(eng, "_candle_seconds"), (
            "engine.py must define a module-level _candle_seconds(tf: str) -> int "
            "helper so that test_H1_cooldown can drive it directly rather than "
            "re-implementing the parser inline."
        )

    def test_candle_seconds_helper_h1_returns_3600(self):
        """_candle_seconds('H1') must return 3600 (handles uppercase Gold timeframe)."""
        from bot.engine import _candle_seconds
        result = _candle_seconds("H1")
        assert result == 3600, (
            f"_candle_seconds('H1') must return 3600, got {result}"
        )

    def test_candle_seconds_helper_1h_returns_3600(self):
        """_candle_seconds('1h') must return 3600."""
        from bot.engine import _candle_seconds
        assert _candle_seconds("1h") == 3600

    def test_candle_seconds_helper_15m_returns_900(self):
        """_candle_seconds('15m') must return 900."""
        from bot.engine import _candle_seconds
        assert _candle_seconds("15m") == 900

    def test_candle_seconds_helper_4h_returns_14400(self):
        """_candle_seconds('4h') must return 14400."""
        from bot.engine import _candle_seconds
        assert _candle_seconds("4h") == 14400

    def test_candle_seconds_helper_unknown_logs_warning(self, caplog):
        """_candle_seconds with an unrecognised timeframe must emit logger.warning."""
        from bot.engine import _candle_seconds
        with caplog.at_level(logging.WARNING, logger="bot.engine"):
            result = _candle_seconds("UNKNOWN_TF")
        # Must fall back to 15m
        assert result == 15 * 60, (
            f"Unknown tf fallback must be 900 (15m), got {result}"
        )
        # Must emit a warning — currently it silently defaults
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("UNKNOWN_TF" in m or "unknown" in m.lower() for m in warning_msgs), (
            f"_candle_seconds must log a warning for unrecognised tf 'UNKNOWN_TF'. "
            f"Got warnings: {warning_msgs}"
        )


# ---------------------------------------------------------------------------
# H5 — _candle_seconds extracted; both sites use it; warning on unknown-tf
# ---------------------------------------------------------------------------

@engine_required
class TestH5CandleSecondsExtracted:
    """Both cooldown gate and _sleep_until_next_candle must use _candle_seconds."""

    def test_cooldown_gate_uses_candle_seconds_helper(self):
        """The cooldown gate in _evaluate_and_execute must call _candle_seconds()
        rather than duplicating the inline parser.
        """
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        lines = source.splitlines()

        # Find _evaluate_and_execute method
        eval_start = None
        for i, line in enumerate(lines):
            if "def _evaluate_and_execute" in line:
                eval_start = i
                break
        assert eval_start is not None, "_evaluate_and_execute not found"

        eval_lines = []
        for line in lines[eval_start:]:
            if eval_lines and (line.startswith("    def ") or line.startswith("    async def ")):
                break
            eval_lines.append(line)

        eval_body = "\n".join(eval_lines)
        assert "_candle_seconds(" in eval_body, (
            "_evaluate_and_execute cooldown gate must call _candle_seconds(tf) "
            "instead of duplicating the inline parser. "
            "This ensures both sites stay in sync when the logic changes."
        )

    def test_sleep_until_next_candle_uses_candle_seconds_helper(self):
        """_sleep_until_next_candle must call _candle_seconds() instead of the
        inline parser block.
        """
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        lines = source.splitlines()

        sleep_start = None
        for i, line in enumerate(lines):
            if "def _sleep_until_next_candle" in line:
                sleep_start = i
                break
        assert sleep_start is not None, "_sleep_until_next_candle not found"

        sleep_lines = []
        for line in lines[sleep_start:]:
            if sleep_lines and (line.startswith("    def ") or line.startswith("    async def ")):
                break
            sleep_lines.append(line)

        sleep_body = "\n".join(sleep_lines)
        assert "_candle_seconds(" in sleep_body, (
            "_sleep_until_next_candle must call _candle_seconds(tf) "
            "rather than duplicating the inline parser. "
            "Currently the parser is duplicated and can drift."
        )

    def test_inline_parser_not_duplicated(self):
        """The inline parser `int(tf.replace('h','')) * 3600` must NOT appear
        more than once in engine.py (it is now centralised in _candle_seconds).
        """
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        # Count occurrences of the parser pattern
        occurrences = source.count("replace(\"h\", \"\") * 3600") + source.count("replace('h','') * 3600") + source.count("replace('h', '') * 3600")
        assert occurrences <= 1, (
            f"The candle-seconds parser `replace('h','') * 3600` must appear at most once "
            f"(inside _candle_seconds). Found {occurrences} occurrences — "
            "the duplicate inline parsers have not been replaced with _candle_seconds() calls."
        )


# ---------------------------------------------------------------------------
# H6 — _infer_close_reason: drop unused trade_side param
# ---------------------------------------------------------------------------

@engine_required
class TestH6TradesSideParamDropped:
    """_infer_close_reason must not have trade_side in its signature
    OR the docstring must accurately describe the parameter if kept.

    The task prefers deletion since classification is symmetric.
    """

    def test_trade_side_param_removed_from_signature(self):
        """_infer_close_reason must not accept trade_side as a parameter."""
        import inspect
        from bot.engine import _infer_close_reason
        sig = inspect.signature(_infer_close_reason)
        params = list(sig.parameters.keys())
        assert "trade_side" not in params, (
            f"_infer_close_reason still has 'trade_side' in its signature: {params}. "
            "The parameter is unused in classification (symmetric logic). Remove it."
        )

    def test_callers_do_not_pass_trade_side(self):
        """engine.py callers must not pass trade_side= to _infer_close_reason."""
        engine_path = REPO / "bot" / "engine.py"
        source = engine_path.read_text()
        # Find call sites
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "_infer_close_reason(" in line and "def " not in line:
                # Check the next few lines for trade_side=
                block = "\n".join(lines[i:i+6])
                assert "trade_side=" not in block, (
                    f"Caller at line {i+1} still passes trade_side= to "
                    f"_infer_close_reason. Remove this now-unused kwarg.\n"
                    f"Block: {block}"
                )
