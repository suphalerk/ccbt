"""Tests for external-close PnL sanity: implausible fill guard, cross-symbol
dedup, and multi-bot dedup.

All tests follow TDD — written BEFORE the fixes and are expected to FAIL until
the fixes land in bot/engine.py.

Decision-invariant contract:
- Whether/when a position closes (absence detection) is NOT changed.
- SL/TP placement, cancel_all_orders, _restore_positions are NOT changed.
- Only: exit_price sanity, PnL sanity, journal recording, and alert dedup.
"""
from __future__ import annotations

import time
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bot.engine import check_closed_positions

SYMBOL = "POL/USDT:USDT"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trade_info(
    entry: float = 0.09033,
    sl: float = 0.085,
    tp: float = 0.095,
    size: float = 1000.0,
    side: str = "buy",
    open_time: Optional[float] = None,
) -> dict:
    return {
        "side": side,
        "entry_price": entry,
        "sl": sl,
        "tp": tp,
        "size": size,
        "open_time": open_time or (time.time() - 3600),
        "signal_type": "ema_crossover",
        "atr": 0.005,
        "calibration_id": None,
    }


def _make_journal() -> MagicMock:
    j = MagicMock()
    j.log_trade_close = MagicMock()
    return j


def _make_risk_mgr() -> MagicMock:
    r = MagicMock()
    r.record_trade_result = MagicMock()
    return r


# ---------------------------------------------------------------------------
# Bug 1: Cross-symbol / implausible exit price guard
# ---------------------------------------------------------------------------

class TestImplausibleExitPriceGuard:
    """When get_closed_pnl returns a price that is implausible relative to
    entry (e.g. 28x the entry price for POLUSDT), the close must be recorded
    on the reconcile path — no garbage PnL, no garbage Telegram alert.
    """

    def _run(
        self,
        entry: float,
        garbage_exit: float,
        size: float = 1000.0,
        side: str = "buy",
    ):
        """Run check_closed_positions with a mocked client returning a
        garbage exit price; return (result_dict, journal, alert_calls).
        """
        info = _make_trade_info(entry=entry, sl=entry * 0.94, tp=entry * 1.06,
                                 size=size, side=side)
        journal = _make_journal()
        risk_mgr = _make_risk_mgr()

        close_side = "sell" if side == "buy" else "buy"
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": close_side, "price": garbage_exit, "amount": size}
        ]
        client.get_ticker_price.return_value = entry * 1.01  # sane current price
        client.cancel_all_orders.return_value = None

        alert_calls = []
        with patch("bot.engine.send_alert", side_effect=lambda msg, **kw: alert_calls.append(msg)):
            result = check_closed_positions(
                open_trade_ids={1: info},
                current_positions=[],  # position is gone
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=None,
                symbol=SYMBOL,
                client=client,
                last_trade_close={},
            )
        return result, journal, alert_calls

    def test_implausible_28x_price_does_not_journal_garbage_pnl(self):
        """POLUSDT entry=0.09033, exit=2.516 (28x): should NOT journal a
        -2685% PnL.  The close must be recorded with a bounded/null PnL."""
        entry = 0.09033
        garbage_exit = 2.516  # 27.8x entry — physically impossible in one candle

        result, journal, alert_calls = self._run(entry=entry, garbage_exit=garbage_exit)

        # Trade should still be detected as closed (absence detection unchanged)
        assert 1 not in result, "Close detection must still fire"

        # Journal must have been called, but NOT with a garbage PnL
        journal.log_trade_close.assert_called_once()
        kwargs = journal.log_trade_close.call_args[1]

        # pnl_pct must not be implausibly large
        pnl_pct = abs(kwargs.get("pnl_pct", 0))
        assert pnl_pct < 500, (
            f"Journaled pnl_pct={pnl_pct:.1f}% is implausible — should be capped/null"
        )

        # pnl absolute must be bounded relative to size
        pnl_abs = abs(kwargs.get("pnl", 0) or 0)
        size = 1000.0
        assert pnl_abs < size * 2, (
            f"Journaled |pnl|={pnl_abs:.2f} exceeds position size {size} — implausible"
        )

    def test_implausible_price_does_not_send_garbage_alert(self):
        """When the exit price is garbage, no alert with an implausible % figure
        should be sent."""
        entry = 0.09033
        garbage_exit = 2.516

        _, _, alert_calls = self._run(entry=entry, garbage_exit=garbage_exit)

        # Either no alert, or the alert does not contain a ridiculous % figure
        for msg in alert_calls:
            # A -2685% type string should not appear
            assert "2685" not in msg, f"Garbage PnL leaked into alert: {msg!r}"
            assert "-26" not in msg or "2685" not in msg  # catches -2685
            # Also guard against any >500% figure
            import re
            pct_matches = re.findall(r"([+-]?[\d,]+\.?\d*)\s*%", msg)
            for m in pct_matches:
                val = float(m.replace(",", ""))
                assert abs(val) < 600, (
                    f"Alert contains implausible PnL {val:.1f}%: {msg!r}"
                )

    def test_implausible_pnl_pct_never_journaled(self):
        """Even if the exit price is within a few percent, if computed pnl_pct
        exceeds a sane cap (e.g. > leverage * 100%), it must be capped/rejected."""
        # Construct: small entry, SL/TP tight, size large — impossible PnL%
        # Actually simulate via huge garbage exit to force a ridiculous pnl_pct
        entry = 1.0
        garbage_exit = 50.0  # 50x entry

        info = _make_trade_info(entry=entry, sl=0.9, tp=1.1, size=100.0)
        journal = _make_journal()
        risk_mgr = _make_risk_mgr()
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": garbage_exit, "amount": 100.0}
        ]
        client.get_ticker_price.return_value = entry * 1.01
        client.cancel_all_orders.return_value = None

        with patch("bot.engine.send_alert"):
            check_closed_positions(
                open_trade_ids={1: info},
                current_positions=[],
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=None,
                symbol=SYMBOL,
                client=client,
            )

        kwargs = journal.log_trade_close.call_args[1]
        pnl_pct = abs(kwargs.get("pnl_pct", 0))
        assert pnl_pct < 1000, (
            f"Journaled pnl_pct={pnl_pct:.1f}% is implausibly large"
        )

    def test_valid_fill_in_range_is_unchanged(self):
        """A valid exit price close to SL or TP must still journal the real PnL
        and fire exactly one alert — the sanity guard must not suppress valid closes."""
        entry = 0.09033
        valid_exit = 0.0855  # close to sl=0.085, within 5% of entry
        sl = entry * 0.94
        tp = entry * 1.06

        info = _make_trade_info(entry=entry, sl=sl, tp=tp, size=1000.0)
        journal = _make_journal()
        risk_mgr = _make_risk_mgr()
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": valid_exit, "amount": 1000.0}
        ]
        client.get_ticker_price.return_value = valid_exit
        client.cancel_all_orders.return_value = None

        alert_calls = []
        with patch("bot.engine.send_alert", side_effect=lambda msg, **kw: alert_calls.append(msg)):
            result = check_closed_positions(
                open_trade_ids={1: info},
                current_positions=[],
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=None,
                symbol=SYMBOL,
                client=client,
                last_trade_close={},
            )

        # Trade closed
        assert 1 not in result

        # Journal called with the real exit price
        kwargs = journal.log_trade_close.call_args[1]
        assert abs(kwargs["exit_price"] - valid_exit) < 0.0001, (
            f"Valid fill {valid_exit} was not journaled; got {kwargs['exit_price']}"
        )

        # Exactly one alert
        assert len(alert_calls) == 1, (
            f"Expected 1 alert for a valid close, got {len(alert_calls)}"
        )


# ---------------------------------------------------------------------------
# Bug 2: Multi-bot dedup — same netted position, several config-bots
# ---------------------------------------------------------------------------

class TestMultiBotDedup:
    """When several config-bots share one netted exchange position for a symbol,
    only ONE close should be journaled and ONE alert sent, not one per bot.

    Coordination mechanism: a shared recently-closed registry keyed by symbol
    (added to check_closed_positions via optional parameter) OR via
    PortfolioManager.  Implementations may differ; tests verify the outcome.
    """

    def _run_bot(
        self,
        trade_id: int,
        symbol: str,
        entry: float,
        sl: float,
        tp: float,
        exit_price: float,
        shared_close_registry: Optional[dict],
        journal: Optional[MagicMock] = None,
        risk_mgr: Optional[MagicMock] = None,
        alert_calls: Optional[list] = None,
    ):
        """Simulate one bot detecting a closed position.  Accepts a shared
        close_registry dict so callers can pass the same object to multiple bots."""
        if journal is None:
            journal = _make_journal()
        if risk_mgr is None:
            risk_mgr = _make_risk_mgr()

        info = _make_trade_info(entry=entry, sl=sl, tp=tp, size=500.0)
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": exit_price, "amount": 500.0}
        ]
        client.get_ticker_price.return_value = exit_price
        client.cancel_all_orders.return_value = None

        local_calls = [] if alert_calls is None else alert_calls
        with patch("bot.engine.send_alert", side_effect=lambda msg, **kw: local_calls.append(msg)):
            result = check_closed_positions(
                open_trade_ids={trade_id: info},
                current_positions=[],   # position gone from exchange
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=None,
                symbol=symbol,
                client=client,
                last_trade_close={},
                recently_closed=shared_close_registry,
            )
        return result, journal

    def test_two_bots_same_symbol_one_journal_entry(self):
        """Two config-bots sharing AXSUSDT both detect the close.  Only one
        journal row and one alert should be produced."""
        symbol = "AXS/USDT:USDT"
        entry = 5.0
        sl = 4.6
        tp = 5.5
        exit_price = 4.65  # SL hit

        # Shared recently-closed registry — passed to both bots
        recently_closed: dict = {}

        journal1 = _make_journal()
        journal2 = _make_journal()
        alert_calls: list = []

        # Bot 1 detects close first
        result1, _ = self._run_bot(
            trade_id=101,
            symbol=symbol,
            entry=entry, sl=sl, tp=tp, exit_price=exit_price,
            shared_close_registry=recently_closed,
            journal=journal1,
            alert_calls=alert_calls,
        )
        # Bot 2 detects the same netted close (different trade_id but same symbol)
        result2, _ = self._run_bot(
            trade_id=102,
            symbol=symbol,
            entry=entry, sl=sl, tp=tp, exit_price=exit_price,
            shared_close_registry=recently_closed,
            journal=journal2,
            alert_calls=alert_calls,
        )

        # Both bots should have removed their trade_id (close detection unchanged)
        assert 101 not in result1
        assert 102 not in result2

        # Total journal rows for this close event: exactly 1
        total_journal_calls = (
            journal1.log_trade_close.call_count
            + journal2.log_trade_close.call_count
        )
        assert total_journal_calls == 1, (
            f"Expected exactly 1 journal entry for one netted close, "
            f"got {total_journal_calls} "
            f"(bot1={journal1.log_trade_close.call_count}, "
            f"bot2={journal2.log_trade_close.call_count})"
        )

        # Total alerts: exactly 1
        assert len(alert_calls) == 1, (
            f"Expected exactly 1 alert, got {len(alert_calls)}: {alert_calls}"
        )

    def test_three_bots_same_symbol_still_one_alert(self):
        """AXS has 7 bots in prod — simulate 3 and assert only 1 alert fires."""
        symbol = "AXS/USDT:USDT"
        entry = 5.0
        sl = 4.6
        tp = 5.5
        exit_price = 5.55  # TP hit

        recently_closed: dict = {}
        alert_calls: list = []
        journals = []

        for tid in range(201, 204):  # 3 bots
            j = _make_journal()
            journals.append(j)
            self._run_bot(
                trade_id=tid,
                symbol=symbol,
                entry=entry, sl=sl, tp=tp, exit_price=exit_price,
                shared_close_registry=recently_closed,
                journal=j,
                alert_calls=alert_calls,
            )

        total_journal_calls = sum(j.log_trade_close.call_count for j in journals)
        assert total_journal_calls == 1, (
            f"Expected 1 journal entry across 3 bots, got {total_journal_calls}"
        )
        assert len(alert_calls) == 1, (
            f"Expected 1 alert across 3 bots, got {len(alert_calls)}"
        )

    def test_different_symbols_each_get_one_alert(self):
        """AXSUSDT and FILUSDT closing at the same time must each get exactly
        one alert — dedup is keyed by symbol, not global."""
        recently_closed: dict = {}
        alert_calls: list = []

        for symbol, tid, entry, sl, tp, ep in [
            ("AXS/USDT:USDT", 301, 5.0, 4.6, 5.5, 5.55),
            ("FIL/USDT:USDT", 302, 6.0, 5.5, 6.6, 5.52),
        ]:
            self._run_bot(
                trade_id=tid,
                symbol=symbol,
                entry=entry, sl=sl, tp=tp, exit_price=ep,
                shared_close_registry=recently_closed,
                alert_calls=alert_calls,
            )

        assert len(alert_calls) == 2, (
            f"Expected 2 alerts (one per symbol), got {len(alert_calls)}"
        )

    def test_registry_not_poisoned_on_journal_failure(self):
        """Regression: if log_trade_close raises (e.g. SQLite locked), the
        recently_closed registry must NOT be marked.  On the next-loop retry
        (or a sibling bot's call for the same symbol) there must be exactly
        ONE journal row and ONE record_trade_result — never zero.

        Bug path before fix (line 335 poisoning):
          1. Primary bot marks recently_closed[sym] BEFORE log_trade_close.
          2. log_trade_close raises → continue skips record_trade_result.
          3. On retry the key is already present → is_primary_closer=False
             → log_trade_close called 0 times, record_trade_result called 0 times
             → DB row stays status='open', circuit breakers never see the loss.
        """
        symbol = "AXS/USDT:USDT"
        trade_id = 999
        entry = 5.0
        sl = 4.6
        tp = 5.5
        exit_price = 4.65  # SL hit

        recently_closed: dict = {}

        # --- First attempt: log_trade_close raises (DB locked) ---------------
        journal_attempt1 = _make_journal()
        journal_attempt1.log_trade_close = MagicMock(
            side_effect=RuntimeError("database is locked")
        )
        risk_mgr_attempt1 = _make_risk_mgr()

        info = _make_trade_info(entry=entry, sl=sl, tp=tp, size=500.0)
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": exit_price, "amount": 500.0}
        ]
        client.get_ticker_price.return_value = exit_price
        client.cancel_all_orders.return_value = None

        with patch("bot.engine.send_alert"):
            result1 = check_closed_positions(
                open_trade_ids={trade_id: info},
                current_positions=[],  # position gone
                journal=journal_attempt1,
                risk_mgr=risk_mgr_attempt1,
                calibration_tracker=None,
                symbol=symbol,
                client=client,
                last_trade_close={},
                recently_closed=recently_closed,
            )

        # After a failed journal write: trade must still be in the result
        # (crash-safe: stays open for retry).
        assert trade_id in result1, (
            "After log_trade_close failure the trade_id must remain "
            "in open_trade_ids for retry — got it removed (closed.append ran)"
        )
        # Registry must NOT have been marked — the close was never committed.
        assert len(recently_closed) == 0, (
            f"recently_closed poisoned after journal failure: {recently_closed}"
        )
        # record_trade_result must NOT have been called (no commit → no accounting).
        risk_mgr_attempt1.record_trade_result.assert_not_called()

        # --- Second attempt: DB is healthy now (same registry, same trade_id) ---
        journal_attempt2 = _make_journal()  # succeeds
        risk_mgr_attempt2 = _make_risk_mgr()

        alert_calls: list = []
        with patch(
            "bot.engine.send_alert",
            side_effect=lambda msg, **kw: alert_calls.append(msg),
        ):
            result2 = check_closed_positions(
                open_trade_ids={trade_id: result1[trade_id]},  # carry over info
                current_positions=[],
                journal=journal_attempt2,
                risk_mgr=risk_mgr_attempt2,
                calibration_tracker=None,
                symbol=symbol,
                client=client,
                last_trade_close={},
                recently_closed=recently_closed,
            )

        # Retry must succeed: trade gone from open_trade_ids.
        assert trade_id not in result2, (
            "On retry the trade was not closed — log_trade_close succeeded but "
            "trade_id still in open_trade_ids"
        )
        # Exactly ONE journal call total across both attempts.
        total_journal = (
            journal_attempt1.log_trade_close.call_count  # raised, counts as 1
            + journal_attempt2.log_trade_close.call_count
        )
        # attempt1 raised (1 call, failed), attempt2 must have succeeded (1 call).
        assert journal_attempt2.log_trade_close.call_count == 1, (
            f"Expected retry to call log_trade_close exactly once; "
            f"got {journal_attempt2.log_trade_close.call_count}"
        )
        # record_trade_result called exactly once (on retry, not on failure).
        risk_mgr_attempt2.record_trade_result.assert_called_once()
        # Registry must now be marked (retry succeeded).
        assert len(recently_closed) == 1, (
            f"recently_closed should have 1 entry after successful retry; "
            f"got {recently_closed}"
        )

    def test_no_recently_closed_arg_behaves_as_before(self):
        """If recently_closed is not passed (None), behaviour is unchanged —
        normal closes still journal + alert once (single-bot path)."""
        symbol = "BTC/USDT:USDT"
        info = _make_trade_info(entry=30000, sl=29000, tp=32000, size=100.0)
        journal = _make_journal()
        risk_mgr = _make_risk_mgr()
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": 32000.0, "amount": 100.0}
        ]
        client.get_ticker_price.return_value = 32000.0
        client.cancel_all_orders.return_value = None

        alert_calls = []
        with patch("bot.engine.send_alert", side_effect=lambda msg, **kw: alert_calls.append(msg)):
            result = check_closed_positions(
                open_trade_ids={1: info},
                current_positions=[],
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=None,
                symbol=symbol,
                client=client,
                # recently_closed not passed → None by default
            )

        assert 1 not in result
        journal.log_trade_close.assert_called_once()
        assert len(alert_calls) == 1


# ---------------------------------------------------------------------------
# Bug 3: get_closed_pnl cross-symbol filter
# ---------------------------------------------------------------------------

class TestGetClosedPnlSymbolFilter:
    """The get_closed_pnl call in check_closed_positions must only accept
    fills for the symbol being closed.  A fill whose price is plausible for
    a different symbol but implausible for the current one must be rejected."""

    def test_cross_symbol_fill_triggers_reconcile_path(self):
        """Simulate POL (entry ~0.09) where get_closed_pnl returns a fill
        with price 2.516 (plausible for AXS/USDT but not POL/USDT).
        The close should land on the reconcile path — no garbage PnL
        or pnl_pct > leverage-bounded cap."""
        symbol = "POL/USDT:USDT"
        entry = 0.09033
        sl = entry * 0.94
        tp = entry * 1.06
        garbage_price = 2.516  # cross-symbol contamination

        info = _make_trade_info(entry=entry, sl=sl, tp=tp, size=1000.0)
        journal = _make_journal()
        risk_mgr = _make_risk_mgr()
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": garbage_price, "amount": 1000.0}
        ]
        # Ticker also returns a sane price (near entry)
        client.get_ticker_price.return_value = entry * 0.96
        client.cancel_all_orders.return_value = None

        alert_calls = []
        with patch("bot.engine.send_alert", side_effect=lambda msg, **kw: alert_calls.append(msg)):
            result = check_closed_positions(
                open_trade_ids={1: info},
                current_positions=[],
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=None,
                symbol=symbol,
                client=client,
            )

        # Close still detected
        assert 1 not in result

        # pnl_pct must be bounded
        kwargs = journal.log_trade_close.call_args[1]
        pnl_pct = abs(kwargs.get("pnl_pct", 0))
        assert pnl_pct < 500, (
            f"pnl_pct={pnl_pct:.1f}% leaked from cross-symbol fill"
        )

        # No alert with garbage % figure
        import re
        for msg in alert_calls:
            pct_matches = re.findall(r"([+-]?[\d,]+\.?\d*)\s*%", msg)
            for m in pct_matches:
                val = float(m.replace(",", ""))
                assert abs(val) < 600, (
                    f"Alert contains cross-symbol-contaminated PnL {val:.1f}%: {msg!r}"
                )
